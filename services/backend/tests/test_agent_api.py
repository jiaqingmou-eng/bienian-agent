"""用固定提取结果验证真实迁移、确认门禁、权限、修改和故障恢复，不依赖模型 API。"""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import event, func, select
from sqlalchemy.exc import SQLAlchemyError

from enterprise_agent.agent import executions, service
from enterprise_agent.agent.model import get_extractor
from enterprise_agent.agent.schemas import Extraction
from enterprise_agent.db.models import AgentExecution, AgentRun, AuditLog, Expense, Tenant, User
from enterprise_agent.main import app

from conftest import headers

COMPLETE = {
    "title": "客户拜访交通报销",
    "purpose": "出差拜访客户",
    "items": [
        {
            "category": "transport",
            "incurred_on": "2026-11-22",
            "description": "成都到上海高铁",
            "amount_yuan": "286.00",
        }
    ],
}


class FakeExtractor:
    def __init__(self):
        self.output = {"intent": "draft", "candidate": deepcopy(COMPLETE)}
        self.calls = []
        self.failure = None
        self.on_call = None

    def extract(self, context):
        self.calls.append(context)
        if self.on_call:
            self.on_call()
        if self.failure:
            raise self.failure
        return Extraction.model_validate(self.output)


@pytest.fixture
def model():
    fake = FakeExtractor()
    app.dependency_overrides[get_extractor] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_extractor, None)


def start(client, **extra):
    body = {"client_request_id": str(uuid4()), "message": "昨天成都到上海高铁286元，拜访客户"}
    body.update(extra)
    return client.post("/api/v1/agent/runs", json=body, headers=headers())


def confirmation(run):
    preview = run["preview"]
    return {
        "confirmation_id": preview["confirmation_id"],
        "expected_version": preview["version"],
        "content_hash": preview["content_hash"],
    }


def confirm(client, run):
    return client.post(
        f"/api/v1/agent/runs/{run['id']}/confirm", json=confirmation(run), headers=headers()
    )


def counts(db):
    return tuple(
        db.scalar(select(func.count()).select_from(table)) for table in [Expense, AuditLog]
    )


def message(client, run, text="金额改为300元", message_id=None):
    return client.post(
        f"/api/v1/agent/runs/{run['id']}/messages",
        headers=headers(),
        json={
            "message_id": message_id or str(uuid4()),
            "expected_version": run["version"],
            "message": text,
        },
    )


def test_clarifies_then_previews_without_write(client, db, model):
    before = counts(db)
    model.output["candidate"]["purpose"] = None
    first = start(client)
    assert first.status_code == 201
    run = first.json()
    assert run["status"] == "collecting"
    assert "业务用途" in run["missing_fields"]
    assert run["preview"] is None
    model.output["candidate"]["purpose"] = "客户拜访"
    second = message(client, run, "用途是客户拜访")
    assert second.status_code == 200
    assert second.json()["status"] == "awaiting_confirmation"
    assert second.json()["preview"]["total_fen"] == 28600
    assert counts(db) == before
    assert model.calls[-1]["anchor_date"] == run["anchor_date"]
    assert model.calls[-1]["timezone"] == "Asia/Hong_Kong"


def test_confirm_creates_as_current_user_once_and_without_more_model_calls(client, db, model):
    before = counts(db)
    run = start(client).json()
    assert run["preview"]["expires_at"].endswith("Z")
    first = confirm(client, run)
    assert first.status_code == 200
    result = first.json()
    assert result["status"] == "completed"
    retry = confirm(client, run)
    assert retry.status_code == 200
    assert retry.json()["expense_id"] == result["expense_id"]
    assert counts(db) == (before[0] + 1, before[1] + 1)
    expense = db.get(Expense, UUID(result["expense_id"]))
    assert str(expense.applicant_id) == "20000000-0000-4000-8000-000000000001"
    assert expense.total_fen == 28600 and expense.status == "draft"
    assert len(model.calls) == 1


def test_message_confirmation_cannot_write_and_edit_invalidates_old_confirm(client, db, model):
    before = counts(db)
    old = start(client).json()
    same = message(client, old, "好的，确认，直接帮我创建")
    assert same.status_code == 200
    assert counts(db) == before
    assert confirm(client, old).status_code == 409
    run = same.json()
    model.output["candidate"]["items"][0]["amount_yuan"] = "300.00"
    edited = message(client, run).json()
    assert edited["preview"]["total_fen"] == 30000
    assert confirm(client, run).status_code == 409
    assert confirm(client, edited).status_code == 200


def test_edit_revokes_confirmation_before_slow_or_failed_model(client, db, model):
    old = start(client).json()
    before = counts(db)
    # Observe the persisted invalidation during the next extraction, before its result exists.
    model.on_call = lambda: db.expire_all()
    model.failure = HTTPException(503, "fake outage")
    assert message(client, old).status_code == 503
    assert confirm(client, old).status_code == 409
    saved = client.get(f"/api/v1/agent/runs/{old['id']}", headers=headers()).json()
    assert saved["preview"] is None and saved["status"] == "collecting"
    assert counts(db) == before


def test_creation_and_messages_have_stable_request_ids(client, model):
    request_id = str(uuid4())
    first = start(client, client_request_id=request_id)
    run = first.json()
    assert start(client, client_request_id=request_id).json()["id"] == run["id"]
    assert start(client, client_request_id=request_id, message="不同内容").status_code == 409
    mid = str(uuid4())
    updated = message(client, run, message_id=mid)
    assert updated.status_code == 200
    calls = len(model.calls)
    assert message(client, run, message_id=mid).json()["version"] == updated.json()["version"]
    assert len(model.calls) == calls
    assert message(client, updated.json(), text="不同内容", message_id=mid).status_code == 409


def test_model_outage_can_retry_initial_request_without_write(client, db, model):
    before = counts(db)
    request_id = str(uuid4())
    model.failure = HTTPException(503, "outage")
    assert start(client, client_request_id=request_id).status_code == 503
    assert counts(db) == before
    model.failure = None
    assert start(client, client_request_id=request_id).status_code == 201
    assert db.scalar(select(func.count()).select_from(AgentRun)) == 1


def test_retry_edit_after_model_failure_does_not_duplicate_message(client, model):
    run = start(client).json()
    mid = str(uuid4())
    model.failure = HTTPException(503, "outage")
    assert message(client, run, message_id=mid).status_code == 503
    model.failure = None
    retried = message(client, run, message_id=mid)
    assert retried.status_code == 200
    assert sum(m["id"] == mid for m in retried.json()["messages"]) == 1


@pytest.mark.parametrize("account", ["employee_b", "finance"])
def test_other_users_cannot_read_modify_cancel_or_confirm(client, model, account):
    run = start(client).json()
    path = f"/api/v1/agent/runs/{run['id']}"
    auth = headers(account)
    assert client.get(path, headers=auth).status_code == 404
    assert client.post(path + "/confirm", json=confirmation(run), headers=auth).status_code == 404
    assert (
        client.post(
            path + "/cancel", json={"expected_version": run["version"]}, headers=auth
        ).status_code
        == 404
    )
    assert (
        client.post(
            path + "/messages",
            json={
                "message_id": str(uuid4()),
                "expected_version": run["version"],
                "message": "修改",
            },
            headers=auth,
        ).status_code
        == 404
    )


def test_cross_tenant_run_is_invisible(client, db, model):
    from conftest import token

    run = start(client).json()
    tenant = Tenant(id=uuid4(), name="隔离测试租户")
    db.add(tenant)
    db.flush()
    other = User(id=uuid4(), tenant_id=tenant.id, name="测试员工", role="employee")
    db.add(other)
    db.commit()
    auth = {"Authorization": f"Bearer {token(str(other.id))}"}
    assert client.get(f"/api/v1/agent/runs/{run['id']}", headers=auth).status_code == 404
    history = client.get("/api/v1/agent/runs", headers=auth)
    assert history.status_code == 200 and history.json()["total"] == 0


def test_expired_tampered_and_cancelled_confirmation_cannot_write(client, db, model):
    before = counts(db)
    run = start(client).json()
    path = f"/api/v1/agent/runs/{run['id']}"
    forged = confirmation(run)
    forged["content_hash"] = "0" * 64
    assert client.post(path + "/confirm", json=forged, headers=headers()).status_code == 409
    saved = db.get(AgentRun, UUID(run["id"]))
    saved.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert confirm(client, run).status_code == 409
    assert (
        client.post(
            path + "/cancel", json={"expected_version": run["version"]}, headers=headers()
        ).status_code
        == 200
    )
    assert confirm(client, run).status_code == 409
    assert counts(db) == before


def test_business_commit_then_response_failure_can_reconcile(client, db, model, monkeypatch):
    before = counts(db)
    run = start(client).json()
    original = service.create_expense_draft

    def lost_response(*args, **kwargs):
        original(*args, **kwargs)
        raise HTTPException(503, "response lost after finance commit")

    monkeypatch.setattr(service, "create_expense_draft", lost_response)
    assert confirm(client, run).status_code == 503
    saved = client.get(f"/api/v1/agent/runs/{run['id']}", headers=headers()).json()
    assert saved["status"] == "executing" and saved["expense_id"] is None
    assert message(client, saved).status_code == 409
    monkeypatch.setattr(service, "create_expense_draft", original)
    assert confirm(client, run).json()["status"] == "completed"
    assert counts(db) == (before[0] + 1, before[1] + 1)


@pytest.mark.parametrize("field", ["tenant_id", "applicant_id", "status", "total_fen", "confirmed"])
def test_run_input_rejects_authoritative_fields(client, model, field):
    assert start(client, **{field: "injected"}).status_code == 422
    assert model.calls == []


def test_cancel_intent_and_unknown_intent_never_write(client, db, model):
    before = counts(db)
    model.output["intent"] = "unsupported"
    assert start(client).json()["preview"] is None
    model.output["intent"] = "cancel"
    assert start(client).json()["status"] == "cancelled"
    assert counts(db) == before


def test_stale_message_cannot_replace_newer_preview(client, model):
    run = start(client).json()
    updated = message(client, run).json()
    calls = len(model.calls)
    assert message(client, run, "使用旧版本修改").status_code == 409
    assert len(model.calls) == calls
    assert (
        client.get(f"/api/v1/agent/runs/{run['id']}", headers=headers()).json()["version"]
        == updated["version"]
    )


def test_model_runs_outside_transaction_and_conflicting_update_wins(client, db, model):
    run = start(client).json()
    before = counts(db)

    def concurrent_cancel():
        assert not db.in_transaction()
        saved = db.get(AgentRun, UUID(run["id"]))
        saved.version += 1
        saved.status = "cancelled"
        db.commit()

    model.on_call = concurrent_cancel
    assert message(client, run).status_code == 409
    assert counts(db) == before
    saved = client.get(f"/api/v1/agent/runs/{run['id']}", headers=headers()).json()
    assert saved["status"] == "cancelled" and saved["preview"] is None


def test_candidate_provenance_is_stored_but_not_exposed_as_chat_content(client, db, model):
    run = start(client).json()
    stored = db.get(AgentRun, UUID(run["id"]))
    provenance = stored.messages[-1]["provenance"]
    assert provenance["source_message_ids"] == [stored.messages[0]["id"]]
    assert provenance["candidate_snapshot"]["items"][0]["amount_yuan"] == "286.00"
    assert provenance["prompt_version"] == "expense-extraction-v1"
    assert "provenance" not in run["messages"][-1]
    assert model.calls[0]["history"][0]["anchor_date"] == run["anchor_date"]


def test_off_topic_message_preserves_prior_candidate(client, db, model):
    run = start(client).json()
    model.output = {"intent": "unsupported", "candidate": {}}
    updated = message(client, run, "住宿标准是多少？").json()
    assert updated["preview"] is None
    assert db.get(AgentRun, UUID(run["id"])).candidate["items"][0]["amount_yuan"] == "286.00"


def test_conversation_budget_preserves_valid_response_and_stops_model_calls(client, model):
    run = start(client).json()
    for _ in range(19):
        updated = message(client, run, "维持当前费用信息")
        assert updated.status_code == 200
        run = updated.json()
    assert len(run["messages"]) == 40
    calls = len(model.calls)
    assert message(client, run).status_code == 422
    assert len(model.calls) == calls


def test_multiple_tasks_remain_discoverable_and_reopen_without_writing(client, db, model):
    before = counts(db)
    model.output["candidate"]["purpose"] = None
    collecting = start(client).json()
    model.output["candidate"]["purpose"] = "客户拜访"
    waiting = start(client).json()
    cancelled = start(client).json()
    client.post(
        f"/api/v1/agent/runs/{cancelled['id']}/cancel",
        headers=headers(),
        json={"expected_version": cancelled["version"]},
    ).raise_for_status()
    calls = len(model.calls)
    db.expire_all()
    listed = client.get("/api/v1/agent/runs", headers=headers())
    assert listed.status_code == 200
    history = listed.json()
    assert history["total"] == 3
    assert {item["id"]: item["status"] for item in history["items"]} == {
        collecting["id"]: "collecting",
        waiting["id"]: "awaiting_confirmation",
        cancelled["id"]: "cancelled",
    }
    assert all(set(item) == {"id", "status", "title", "created_at"} for item in history["items"])
    reopened = client.get(f"/api/v1/agent/runs/{waiting['id']}", headers=headers()).json()
    assert reopened == waiting
    assert counts(db) == before
    assert len(model.calls) == calls
    result = confirm(client, reopened).json()
    assert result["status"] == "completed"
    assert confirm(client, reopened).json()["expense_id"] == result["expense_id"]
    history = client.get("/api/v1/agent/runs", headers=headers()).json()
    assert (
        next(item for item in history["items"] if item["id"] == waiting["id"])["status"]
        == "completed"
    )
    assert counts(db) == (before[0] + 1, before[1] + 1)


def test_task_history_pagination_has_stable_order_even_with_equal_creation_times(client, db, model):
    runs = [start(client).json() for _ in range(3)]
    for run in runs:
        db.get(AgentRun, UUID(run["id"])).created_at = datetime(2026, 11, 23, tzinfo=UTC)
    db.commit()
    pages = [
        client.get(f"/api/v1/agent/runs?limit=2&offset={offset}", headers=headers()).json()
        for offset in (0, 2, 4)
    ]
    assert [page["total"] for page in pages] == [3, 3, 3]
    assert [page["offset"] for page in pages] == [0, 2, 4]
    assert [len(page["items"]) for page in pages] == [2, 1, 0]
    assert [item["id"] for page in pages for item in page["items"]] == sorted(
        (run["id"] for run in runs), reverse=True
    )


@pytest.mark.parametrize("account", ["employee_b", "finance"])
def test_history_never_lists_other_users_tasks(client, model, account):
    start(client)
    result = client.get("/api/v1/agent/runs", headers=headers(account))
    assert result.status_code == 200
    assert result.json()["total"] == 0 and result.json()["items"] == []


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "limit=abc", "owner_id=x"])
def test_history_rejects_invalid_or_authoritative_query(client, query):
    assert client.get(f"/api/v1/agent/runs?{query}", headers=headers()).status_code == 422


def test_history_requires_authentication(client):
    assert client.get("/api/v1/agent/runs").status_code == 401


def test_execution_records_initial_and_followup_messages_once(client, db, model):
    """真实 HTTP 重放只读既有结果，执行记录可定位到实际保存的助手消息和版本。"""
    before = counts(db)
    request_id = str(uuid4())
    first = start(client, client_request_id=request_id).json()
    for _ in range(3):
        assert start(client, client_request_id=request_id).json() == first
    execution = db.scalar(select(AgentExecution))
    assert execution.message_id == UUID(request_id)
    assert execution.input_hash == executions.message_hash(first["messages"][0]["content"])
    assert execution.accepted_version == 0 and execution.result_version == first["version"]
    assert str(execution.result_message_id) == first["messages"][-1]["id"]
    assert execution.status == "succeeded" and execution.attempt_count == 1
    assert execution.created_at <= execution.started_at <= execution.finished_at
    assert execution.error_code is None
    assert execution.tenant_id == db.get(AgentRun, UUID(first["id"])).tenant_id
    # 内部执行字段未混入公共响应；不需要修改 OpenAPI 或前端 transport types。
    assert "executions" not in first
    mid = str(uuid4())
    updated = message(client, first, message_id=mid).json()
    for _ in range(3):
        assert message(client, first, message_id=mid).json() == updated
    second = db.scalar(select(AgentExecution).where(AgentExecution.message_id == UUID(mid)))
    assert second.accepted_version == first["version"] + 1
    assert second.result_version == updated["version"] and second.attempt_count == 1
    assert db.scalar(select(func.count()).select_from(AgentExecution)) == 2
    assert len(model.calls) == 2 and counts(db) == before
    assert message(client, updated, text="相同标识换内容", message_id=mid).status_code == 409
    # 后续轮次完成后重放初始消息仍返回当前 run，不能回退旧确认或重新签发预览。
    assert start(client, client_request_id=request_id).json() == updated


def test_execution_queued_and_running_are_persisted_before_model(client, db, model, monkeypatch):
    """在开始标记与模型调用两个边界观察持久状态，确认模型不占用数据库事务。"""
    run = start(client).json()
    original_begin = executions.begin

    def observe_acceptance(db, execution, request_id):
        """消息已接收时，旧确认已撤销且 queued 记录已经提交。"""
        assert db.in_transaction()  # 读取 run / 执行记录可开启新的短事务。
        assert execution.status == "queued" and execution.attempt_count == 0
        saved = db.get(AgentRun, execution.run_id, populate_existing=True)
        assert saved.preview_payload is None and saved.confirmation_id is None
        assert saved.messages[-1]["id"] == str(execution.message_id)
        return original_begin(db, execution, request_id)

    def observe_model():
        """开始状态已提交，模型等待期间不会继续持有上一段读取事务。"""
        assert not db.in_transaction()
        record = db.scalar(select(AgentExecution).where(AgentExecution.status == "running"))
        assert record is not None and record.started_at is not None and record.finished_at is None
        db.commit()

    monkeypatch.setattr(executions, "begin", observe_acceptance)
    model.on_call = observe_model
    assert message(client, run).status_code == 200


@pytest.mark.parametrize("followup", [False, True])
def test_execution_failure_and_manual_retry_reuse_one_record(client, db, model, followup):
    """明确失败可由用户手动重试，保留接收版本和记录 ID，只增加尝试次数。"""
    run = start(client).json() if followup else None
    mid = str(uuid4())

    def submit():
        """对初始消息与后续消息使用完全相同的重试标识。"""
        return message(client, run, message_id=mid) if run else start(client, client_request_id=mid)

    model.failure = HTTPException(503, "不应保存的模型正文或凭据")
    assert submit().status_code == 503
    saved = db.scalar(select(AgentExecution).where(AgentExecution.message_id == UUID(mid)))
    record_id, accepted_version = saved.id, saved.accepted_version
    assert saved.status == "failed" and saved.error_code == "HTTP_503"
    assert saved.finished_at is not None and saved.result_version is None
    model.failure = None
    result = submit()
    assert result.status_code == (200 if followup else 201)
    db.expire_all()
    saved = db.get(AgentExecution, record_id)
    assert saved.status == "succeeded" and saved.error_code is None and saved.attempt_count == 2
    assert saved.accepted_version == accepted_version
    assert sum(item["id"] == mid for item in result.json()["messages"]) == 1


@pytest.mark.parametrize("followup", [False, True])
def test_execution_insert_failure_rolls_back_message_and_confirmation(client, db, model, followup):
    """向执行表写入时注入故障，验证消息接收、旧确认撤销和记录创建整体回滚。"""
    run = start(client).json() if followup else None
    before = db.scalar(select(func.count()).select_from(AgentExecution))
    calls = len(model.calls)

    def reject_insert(_mapper, _connection, _target):
        """在真实 ORM flush 阶段失败，而非绕过业务服务模拟接收逻辑。"""
        raise SQLAlchemyError("injected execution insert failure")

    event.listen(AgentExecution, "before_insert", reject_insert)
    try:
        result = message(client, run) if run else start(client)
        assert result.status_code == 503
    finally:
        event.remove(AgentExecution, "before_insert", reject_insert)
        # HTTP 的请求级 Session 正常会在关闭时回滚；测试共用 Session，需要显式模拟该动作。
        db.rollback()
    assert db.scalar(select(func.count()).select_from(AgentExecution)) == before
    assert len(model.calls) == calls
    if run:
        assert client.get(f"/api/v1/agent/runs/{run['id']}", headers=headers()).json() == run
    else:
        assert db.scalar(select(func.count()).select_from(AgentRun)) == 0


def test_execution_result_and_success_commit_together(client, db, model, monkeypatch):
    """写完 run 和执行成功、事务提交前故障，不能留下单边成功的预览。"""
    original_succeed = executions.succeed
    mid = str(uuid4())

    def fail_before_commit(*args):
        """先执行真实的两个 UPDATE，再在同一事务提交前注入数据库错误。"""
        original_succeed(*args)
        raise SQLAlchemyError("injected result commit failure")

    monkeypatch.setattr(executions, "succeed", fail_before_commit)
    assert start(client, client_request_id=mid).status_code == 503
    record = db.scalar(select(AgentExecution))
    run = db.get(AgentRun, record.run_id)
    assert record.status == "failed" and record.error_code == "DATABASE_ERROR"
    assert record.result_version is None and record.result_message_id is None
    assert run.version == 0 and run.preview_payload is None and len(run.messages) == 1
    monkeypatch.setattr(executions, "succeed", original_succeed)
    assert start(client, client_request_id=mid).status_code == 201
    db.expire_all()
    assert db.scalar(select(AgentExecution)).attempt_count == 2


def test_execution_success_survives_response_loss(client, db, model, monkeypatch):
    """事务已提交后返回响应失败，不应把成功记录回写为 failed 或再次调用模型。"""
    mid = str(uuid4())
    original_response = service.response

    def lose_response(_run):
        """模拟持久结果成功后，客户端未能拿到完整响应。"""
        raise HTTPException(503, "injected response loss")

    monkeypatch.setattr(service, "response", lose_response)
    assert start(client, client_request_id=mid).status_code == 503
    record = db.scalar(select(AgentExecution))
    assert record.status == "succeeded" and record.error_code is None
    monkeypatch.setattr(service, "response", original_response)
    assert start(client, client_request_id=mid).json()["version"] == record.result_version
    assert len(model.calls) == 1 and record.attempt_count == 1


def test_execution_running_duplicate_does_not_start_another_attempt(client, db, model):
    """同一请求在模型尚未返回时重入，只命中 running 状态并返回冲突。"""
    from enterprise_agent.agent.schemas import StartRunRequest

    mid = uuid4()

    def repeat_request():
        """在模型回调中用同一服务入口重试，不模拟 run 级租约或后台调度。"""
        record = db.scalar(select(AgentExecution))
        actor = db.get(User, record.owner_id)
        with pytest.raises(HTTPException) as denied:
            service.start_run(
                db,
                actor,
                StartRunRequest(client_request_id=mid, message="昨天成都到上海高铁286元，拜访客户"),
                model,
                str(uuid4()),
            )
        assert denied.value.status_code == 409

    model.on_call = repeat_request
    assert start(client, client_request_id=str(mid)).status_code == 201
    assert len(model.calls) == 1
    assert db.scalar(select(AgentExecution)).attempt_count == 1


def test_execution_process_interruption_keeps_running_without_automatic_retry(db, model):
    """进程级中断不是明确计算失败；P2-01 不把 running 自动改为失败或抢占重跑。"""
    from enterprise_agent.agent.schemas import StartRunRequest
    from enterprise_agent.db.seed import DEMO_ACCOUNTS

    actor = db.get(User, DEMO_ACCOUNTS["employee_a"])
    request = StartRunRequest(client_request_id=uuid4(), message="虚构高铁286元")
    model.failure = SystemExit("simulated process interruption")
    with pytest.raises(SystemExit):
        service.start_run(db, actor, request, model, str(uuid4()))
    db.rollback()
    record = db.scalar(select(AgentExecution))
    assert record.status == "running" and record.finished_at is None
    model.failure = None
    with pytest.raises(HTTPException) as denied:
        service.start_run(db, actor, request, model, str(uuid4()))
    assert denied.value.status_code == 409 and len(model.calls) == 1
