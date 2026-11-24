"""用固定提取结果验证真实迁移、确认门禁、权限、修改和故障恢复，不依赖模型 API。"""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from enterprise_agent.agent import service
from enterprise_agent.agent.model import get_extractor
from enterprise_agent.agent.schemas import Extraction
from enterprise_agent.db.models import AgentRun, AuditLog, Expense, Tenant, User
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
