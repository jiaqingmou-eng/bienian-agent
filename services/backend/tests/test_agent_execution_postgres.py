"""用真实 PostgreSQL 验证执行记录升级、隔离约束、接收原子性与同消息并发去重。"""

import os
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event, Lock
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from fastapi import HTTPException
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from enterprise_agent.agent import executions, service
from enterprise_agent.agent.schemas import Extraction, RunMessageRequest, StartRunRequest
from enterprise_agent.db.models import AgentExecution, AgentRun, AuditLog, Base, Expense, User
from enterprise_agent.db.seed import DEMO_ACCOUNTS, seed

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"), reason="TEST_POSTGRES_URL not configured"
)


class FixedExtractor:
    """返回固定完整预览；可暂停一次模型调用，观察另一并发请求的真实数据库行为。"""

    def __init__(self, started=None, release=None):
        self.calls = 0
        self.lock = Lock()
        self.started = started
        self.release = release

    def extract(self, _context):
        """只记录调用次数，不调用外部模型或财务工具。"""
        with self.lock:
            self.calls += 1
        if self.started:
            self.started.set()
            if not self.release.wait(timeout=15):
                raise RuntimeError("Test model release deadline exceeded")
        return Extraction.model_validate(
            {
                "intent": "draft",
                "candidate": {
                    "title": "虚构报销",
                    "purpose": "虚构客户拜访",
                    "items": [
                        {
                            "category": "transport",
                            "incurred_on": "2026-11-23",
                            "description": "虚构高铁票",
                            "amount_yuan": "286.00",
                        }
                    ],
                },
            }
        )


@pytest.fixture
def execution_engine():
    """每次创建随机 schema，finally 只清理本测试命名空间，不升级或清空演示库。"""
    schema = f"test_{uuid4().hex}"
    engine = create_engine(
        os.environ["TEST_POSTGRES_URL"],
        hide_parameters=True,
        connect_args={"options": f"-c search_path={schema}"},
    )
    with engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    try:
        yield engine
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()


def migrate(engine, revision):
    """在测试 schema 执行正式迁移，支持带旧数据升级和同 revision 重复运行。"""
    with engine.begin() as connection:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def test_execution_upgrade_preserves_legacy_runs_and_database_scope(execution_engine):
    """从真实 0002 旧数据升级，检查元数据、历史兼容、复合外键与去重约束。"""
    migrate(execution_engine, "0002_agent_runs")
    created_at = datetime.now(UTC) - timedelta(days=1)
    pending_id, completed_id = uuid4(), uuid4()
    with Session(execution_engine) as db:
        seed(db)
        actor = db.get(User, DEMO_ACCOUNTS["employee_a"])
        for mid, completed in [(pending_id, False), (completed_id, True)]:
            messages = [{"id": str(mid), "role": "user", "content": "历史消息"}]
            if completed:
                messages.append({"id": str(uuid4()), "role": "assistant", "content": "请补用途"})
            db.add(
                AgentRun(
                    tenant_id=actor.tenant_id,
                    owner_id=actor.id,
                    client_request_id=mid,
                    input_hash=executions.message_hash("历史消息"),
                    anchor_date="2026-11-23",
                    timezone="Asia/Hong_Kong",
                    messages=messages,
                    version=1 if completed else 0,
                    created_at=created_at,
                )
            )
        db.commit()
    migrate(execution_engine, "head")
    migrate(execution_engine, "head")
    with execution_engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    with Session(execution_engine) as db:
        actor = db.get(User, DEMO_ACCOUNTS["employee_a"])
        model = FixedExtractor()
        result = service.start_run(
            db,
            actor,
            StartRunRequest(client_request_id=completed_id, message="历史消息"),
            model,
            str(uuid4()),
        )
        assert result.version == 1 and model.calls == 0
        assert db.scalar(select(func.count()).select_from(AgentExecution)) == 0
        result = service.start_run(
            db,
            actor,
            StartRunRequest(client_request_id=pending_id, message="历史消息"),
            model,
            str(uuid4()),
        )
        record = db.scalar(select(AgentExecution))
        assert record.status == "succeeded" and record.attempt_count == 1
        assert record.created_at > created_at
        assert db.get(AgentRun, result.id).created_at == created_at
        # 直接构造不合法记录，证明即使绕过 HTTP，数据库也不能把别人的执行关联到本任务。
        for invalid in ["owner", "duplicate", "status"]:
            run = db.get(AgentRun, result.id)
            value = AgentExecution(
                tenant_id=run.tenant_id,
                owner_id=DEMO_ACCOUNTS["employee_b"] if invalid == "owner" else run.owner_id,
                run_id=run.id,
                message_id=pending_id if invalid == "duplicate" else uuid4(),
                input_hash=executions.message_hash("约束测试"),
                accepted_version=run.version,
                request_id=str(uuid4()),
                status="invalid" if invalid == "status" else "queued",
            )
            db.add(value)
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()
        assert db.scalar(select(func.count()).select_from(AgentExecution)) == 1
    # downgrade 删除新执行表和归属约束，但原两条 run 仍然存在；再次升级不补造旧记录。
    with execution_engine.begin() as connection:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["connection"] = connection
        command.downgrade(config, "0002_agent_runs")
    migrate(execution_engine, "head")
    with Session(execution_engine) as db:
        assert db.scalar(select(func.count()).select_from(AgentRun)) == 2
        assert db.scalar(select(func.count()).select_from(AgentExecution)) == 0


@pytest.mark.parametrize("followup", [False, True])
def test_execution_same_message_concurrent_acceptance_creates_one_record(
    execution_engine, followup
):
    """两个独立 Session 同时接收同一消息，仅一条记录和一次模型计算，不实现 run 租约。"""
    migrate(execution_engine, "head")
    with Session(execution_engine) as db:
        seed(db)
        actor = db.get(User, DEMO_ACCOUNTS["employee_a"])
        first = (
            service.start_run(
                db,
                actor,
                StartRunRequest(client_request_id=uuid4(), message="第一条消息"),
                FixedExtractor(),
                str(uuid4()),
            )
            if followup
            else None
        )
        before = tuple(db.scalar(select(func.count()).select_from(t)) for t in [Expense, AuditLog])
    mid, barrier = uuid4(), Barrier(2)
    started, release = Event(), Event()
    model = FixedExtractor(started, release)

    def submit():
        """让两个事务真实竞争接收操作；执行中的重复请求应返回 409。"""
        with Session(execution_engine) as db:
            actor = db.get(User, DEMO_ACCOUNTS["employee_a"])
            barrier.wait(timeout=10)
            try:
                if followup:
                    return service.send_message(
                        db,
                        actor,
                        first.id,
                        RunMessageRequest(
                            message_id=mid, message="相同的后续消息", expected_version=first.version
                        ),
                        model,
                        str(uuid4()),
                    )
                return service.start_run(
                    db,
                    actor,
                    StartRunRequest(client_request_id=mid, message="相同的初始消息"),
                    model,
                    str(uuid4()),
                )
            except HTTPException as exc:
                assert exc.status_code == 409
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit) for _ in range(2)]
        try:
            assert started.wait(timeout=10)
            done, _ = wait(futures, timeout=10, return_when=FIRST_COMPLETED)
            assert len(done) == 1 and next(iter(done)).result() is None
        finally:
            release.set()
        results = [future.result(timeout=10) for future in futures]
    assert sum(result is not None for result in results) == 1 and model.calls == 1
    with Session(execution_engine) as db:
        record = db.scalar(select(AgentExecution).where(AgentExecution.message_id == mid))
        assert record.status == "succeeded" and record.attempt_count == 1
        assert db.scalar(select(func.count()).select_from(AgentExecution)) == (2 if followup else 1)
        run = db.get(AgentRun, record.run_id)
        assert len(run.messages) == (4 if followup else 2)
        assert before == tuple(
            db.scalar(select(func.count()).select_from(t)) for t in [Expense, AuditLog]
        )


@pytest.mark.parametrize("followup", [False, True])
def test_execution_acceptance_rollback_in_postgres(execution_engine, followup):
    """真实事务中执行记录插入失败，关闭请求 Session 后再读，消息与旧确认均保持原样。"""
    migrate(execution_engine, "head")
    with Session(execution_engine) as db:
        seed(db)
        actor = db.get(User, DEMO_ACCOUNTS["employee_a"])
        first = (
            service.start_run(
                db,
                actor,
                StartRunRequest(client_request_id=uuid4(), message="第一条消息"),
                FixedExtractor(),
                str(uuid4()),
            )
            if followup
            else None
        )

    def reject_insert(_mapper, _connection, _target):
        """在真实 flush 阶段注入故障，不修改正式事务逻辑。"""
        raise SQLAlchemyError("injected execution acceptance failure")

    event.listen(AgentExecution, "before_insert", reject_insert)
    try:
        with Session(execution_engine) as db:
            actor = db.get(User, DEMO_ACCOUNTS["employee_a"])
            with pytest.raises(SQLAlchemyError):
                if followup:
                    service.send_message(
                        db,
                        actor,
                        first.id,
                        RunMessageRequest(
                            message_id=uuid4(), expected_version=first.version, message="修改消息"
                        ),
                        FixedExtractor(),
                        str(uuid4()),
                    )
                else:
                    service.start_run(
                        db,
                        actor,
                        StartRunRequest(client_request_id=uuid4(), message="新建消息"),
                        FixedExtractor(),
                        str(uuid4()),
                    )
    finally:
        event.remove(AgentExecution, "before_insert", reject_insert)
    with Session(execution_engine) as db:
        assert db.scalar(select(func.count()).select_from(AgentExecution)) == (1 if followup else 0)
        if followup:
            actor = db.get(User, DEMO_ACCOUNTS["employee_a"])
            assert service.response(service.load_run(db, actor, first.id)) == first
        else:
            assert db.scalar(select(func.count()).select_from(AgentRun)) == 0
