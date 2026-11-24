"""真实 BFF → Python → PostgreSQL 冒烟；模型端点为本地替身，不调用外部模型。"""

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from enterprise_agent.config import get_settings

ROOT = Path(__file__).resolve().parents[3]


class ModelStub(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        context = json.loads(body["messages"][-1]["content"])
        has_purpose = any("客户拜访" in item["content"] for item in context["history"])
        content = {
            "intent": "draft",
            "candidate": {
                "title": "高铁报销",
                "purpose": "客户拜访" if has_purpose else None,
                "items": [
                    {
                        "category": "transport",
                        "incurred_on": "2026-11-22",
                        "description": "成都到上海高铁",
                        "amount_yuan": "286.00",
                    }
                ],
            },
        }
        payload = json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(content, ensure_ascii=False)},
                    }
                ]
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


def free_port():
    with socket.socket() as handle:
        handle.bind(("127.0.0.1", 0))
        return handle.getsockname()[1]


def wait_ready(client, url, process):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Test service exited; inspect work/p1-*-http.log")
        try:
            if client.get(url, timeout=0.5).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError("Test service readiness deadline exceeded")


def main():
    url = make_url(get_settings().database_url)
    if url.host not in {"localhost", "127.0.0.1"} or url.get_backend_name() != "postgresql":
        raise SystemExit("Local PostgreSQL required")
    node = shutil.which("node")
    if node is None or not (ROOT / "apps/bff/dist/main.js").exists():
        raise SystemExit("Run pnpm build first; node and built BFF are required")
    schema = f"test_{uuid4().hex}"
    scoped = url.set(database="enterprise_agent_p0_test").update_query_dict(
        {"options": f"-c search_path={schema}"}
    )
    engine = create_engine(scoped, hide_parameters=True)
    processes, logs = [], []
    stub = ThreadingHTTPServer(("127.0.0.1", 0), ModelStub)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    created = False
    try:
        with engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        created = True
        backend_port, bff_port = free_port(), free_port()
        env = {
            **os.environ,
            "DATABASE_URL": scoped.render_as_string(hide_password=False),
            "INTERNAL_JWT_SECRET": "p1-smoke-internal-fake-secret-0123456789012345",
            "SESSION_SECRET": "p1-smoke-session-fake-secret-9876543210987654",
            "ENABLE_DEMO_LOGIN": "true",
            "NODE_ENV": "test",
            "BACKEND_HOST": "127.0.0.1",
            "BACKEND_PORT": str(backend_port),
            "BACKEND_URL": f"http://127.0.0.1:{backend_port}",
            "BFF_HOST": "127.0.0.1",
            "BFF_PORT": str(bff_port),
            "APP_ORIGIN": "http://localhost:5173",
            "LLM_API_KEY": "fake-http-smoke-key",
            "LLM_MODEL": "local-stub",
            "LLM_BASE_URL": f"http://127.0.0.1:{stub.server_port}/v1",
        }
        for command in (
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            [sys.executable, "-m", "enterprise_agent.db.seed"],
        ):
            subprocess.run(
                command,
                cwd=ROOT / "services/backend",
                env=env,
                check=True,
                stdout=subprocess.DEVNULL,
            )
        (ROOT / "work").mkdir(exist_ok=True)
        for name, command, directory in [
            ("backend", [sys.executable, "-m", "enterprise_agent"], ROOT / "services/backend"),
            ("bff", [node, "dist/main.js"], ROOT / "apps/bff"),
        ]:
            handle = (ROOT / f"work/p1-{name}-http.log").open("w", encoding="utf-8")
            logs.append(handle)
            processes.append(
                subprocess.Popen(
                    command,
                    cwd=directory,
                    env=env,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            )
        with httpx.Client(trust_env=False) as probe:
            wait_ready(probe, f"http://127.0.0.1:{backend_port}/ready", processes[0])
            wait_ready(probe, f"http://127.0.0.1:{bff_port}/api/ready", processes[1])
        origin = {"Origin": "http://localhost:5173"}
        with httpx.Client(
            base_url=f"http://127.0.0.1:{bff_port}/api", headers=origin, timeout=15, trust_env=False
        ) as client:
            assert (
                client.post("/auth/demo-login", json={"account_key": "employee_a"}).status_code
                == 200
            )
            first = client.post(
                "/agent/runs",
                json={"client_request_id": str(uuid4()), "message": "2026-11-22高铁票286元"},
            )
            assert first.status_code == 201, first.status_code
            run = first.json()
            assert run["status"] == "collecting" and "业务用途" in run["missing_fields"]
            with engine.connect() as connection:
                assert connection.execute(text("SELECT count(*) FROM expenses")).scalar() == 2
            path = f"/agent/runs/{run['id']}"
            second = client.post(
                path + "/messages",
                json={
                    "message_id": str(uuid4()),
                    "expected_version": run["version"],
                    "message": "用途是客户拜访",
                },
            )
            assert second.status_code == 200, second.status_code
            run = second.json()
            assert run["preview"]["total_fen"] == 28600
            # 查询业务页面后通过列表重新找回原预览；新任务不覆盖已有任务。
            assert client.get("/expenses").status_code == 200
            another = client.post(
                "/agent/runs",
                json={"client_request_id": str(uuid4()), "message": "另一笔高铁票286元"},
            )
            assert another.status_code == 201
            assert another.json()["id"] != run["id"]
            history = client.get("/agent/runs?limit=5&offset=0")
            assert history.status_code == 200 and history.json()["total"] == 2
            assert {item["id"] for item in history.json()["items"]} == {
                run["id"],
                another.json()["id"],
            }
            assert client.get(path).json() == run
            with engine.connect() as connection:
                assert connection.execute(text("SELECT count(*) FROM expenses")).scalar() == 2
            confirmation = {
                "confirmation_id": run["preview"]["confirmation_id"],
                "expected_version": run["preview"]["version"],
                "content_hash": run["preview"]["content_hash"],
            }
            result = client.post(path + "/confirm", json=confirmation)
            assert result.status_code == 200, result.status_code
            expense_id = result.json()["expense_id"]
            assert result.json()["status"] == "completed"
            assert (
                client.post(path + "/confirm", json=confirmation).json()["expense_id"] == expense_id
            )
            assert client.get(f"/expenses/{expense_id}").json()["total_fen"] == 28600
            assert (
                client.post("/auth/demo-login", json={"account_key": "employee_b"}).status_code
                == 200
            )
            assert client.get(path).status_code == 404
            history = client.get("/agent/runs")
            assert history.status_code == 200 and history.json()["items"] == []
            assert client.get(f"/expenses/{expense_id}").status_code == 404
            with engine.connect() as connection:
                assert connection.execute(text("SELECT count(*) FROM expenses")).scalar() == 3
                assert connection.execute(text("SELECT count(*) FROM audit_logs")).scalar() == 3
        report = {
            "mode": "real-bff-backend-postgres-with-local-model-stub",
            "passed": True,
            "checks": [
                "clarification-no-write",
                "preview",
                "confirmed-create",
                "retry-single-write",
                "owner-read",
                "cross-user-denied",
                "multiple-tasks-discoverable",
                "preview-reopened-without-write",
                "private-task-history",
            ],
        }
        (ROOT / "work/p1-http-flow.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(report, indent=2))
    finally:
        for process in reversed(processes):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for handle in logs:
            handle.close()
        stub.shutdown()
        stub.server_close()
        if created:
            with engine.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()


if __name__ == "__main__":
    main()
