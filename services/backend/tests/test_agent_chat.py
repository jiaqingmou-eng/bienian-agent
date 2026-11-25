"""统一对话入口的业务隔离、证据校验与失败边界；固定模型输出不等于真实模型质量。"""

from copy import deepcopy
from datetime import date
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from enterprise_agent.agent.chat_model import get_chat_model
from enterprise_agent.agent.chat_schemas import IntentDecision, PolicyGeneration
from enterprise_agent.agent.model import get_extractor
from enterprise_agent.agent.policy_dates import policy_date
from enterprise_agent.agent.schemas import Extraction
from enterprise_agent.db.models import AgentExecution, AgentRun, AuditLog, Expense
from enterprise_agent.db.seed import DEMO_ACCOUNTS, seed_knowledge
from enterprise_agent.knowledge.service import change_publication
from enterprise_agent.main import app

from conftest import headers


class StubChat:
    """分别记录分类和作答上下文，可注入外部调用期间的撤回等并发变化。"""

    def __init__(self):
        self.decision = IntentDecision(intent="policy", query="成都住宿标准")
        self.classifications = []
        self.answers = []
        self.output = None
        self.failure = None
        self.on_answer = None

    def classify(self, context):
        self.classifications.append(context)
        if self.failure:
            raise self.failure
        return self.decision

    def answer(self, context):
        self.answers.append(context)
        if self.on_answer:
            self.on_answer()
        if self.output is not None:
            return PolicyGeneration.model_validate(self.output)
        evidence = context["evidence"][0]
        return PolicyGeneration(
            status="answered",
            answer="成都出差每人每晚不超过400.00元，超标部分不能自动认定。",
            citations=[
                {
                    "citation_id": evidence["citation_id"],
                    "quote": evidence["text"],
                }
            ],
        )


class StubExpense:
    """报销分支复用旧提取契约，测试不改变财务事实。"""

    def __init__(self):
        self.calls = 0

    def extract(self, _context):
        self.calls += 1
        return Extraction(
            intent="draft",
            candidate={
                "title": "测试差旅",
                "purpose": "客户拜访",
                "items": [
                    {
                        "category": "transport",
                        "incurred_on": "2026-11-24",
                        "description": "高铁",
                        "amount_yuan": "286.00",
                    }
                ],
            },
        )


@pytest.fixture
def models(client):
    """依赖覆盖限定在本测试，不访问外部模型。"""
    chat, expense = StubChat(), StubExpense()
    app.dependency_overrides[get_chat_model] = lambda: chat
    app.dependency_overrides[get_extractor] = lambda: expense
    return chat, expense


def send(client, text="成都出差住宿标准是多少？", **extra):
    """通过真实路由、鉴权和 schema 发送单轮请求。"""
    return client.post(
        "/api/v1/agent/chat",
        headers=headers(),
        json={
            "message_id": str(uuid4()),
            "message": text,
            **extra,
        },
    )


def counts(db):
    return tuple(
        db.scalar(select(func.count()).select_from(table))
        for table in (
            AgentRun,
            AgentExecution,
            Expense,
            AuditLog,
        )
    )


def test_policy_answer_formats_evidence_and_preserves_pending_confirmation(client, db, models):
    """最关键门禁：咨询不能追加报销消息、改变版本、清空预览或新增财务 / 执行记录。"""
    chat, expense = models
    chat.decision = IntentDecision(intent="expense")
    run = send(client, "高铁286元，帮我报销").json()["run"]
    seed_knowledge(db)
    before = counts(db)
    original = deepcopy(client.get(f"/api/v1/agent/runs/{run['id']}", headers=headers()).json())
    chat.decision = IntentDecision(intent="policy", query="成都住宿标准")
    response = send(client, run_id=run["id"], expected_version=run["version"])
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "policy" and body["status"] == "answered" and body["run"] is None
    assert "400.00" in body["message"] and body["sources"][0]["document"]["version"] == 1
    assert "第四条" in body["sources"][0]["text"]
    assert client.get(f"/api/v1/agent/runs/{run['id']}", headers=headers()).json() == original
    assert counts(db) == before and expense.calls == 1
    context = chat.answers[0]
    assert set(context) == {"question", "as_of", "evidence"}
    assert set(context["evidence"][0]) == {
        "citation_id",
        "document_title",
        "document_version",
        "effective_from",
        "effective_until",
        "heading_path",
        "text",
    }
    assert "tenant_id" not in str(context) and "confirmation_id" not in str(chat.classifications)


def test_expense_retry_replays_before_classification_and_content_conflicts(client, db, models):
    """新建和追加消息均沿用稳定标识；重试不再次分类，不追加答复或重新签发预览。"""
    chat, expense = models
    chat.decision = IntentDecision(intent="expense")
    message_id = str(uuid4())
    first = send(client, "高铁286元", message_id=message_id).json()["run"]
    chat.failure = HTTPException(503, "分类器不可用")
    again = send(client, "高铁286元", message_id=message_id)
    assert again.status_code == 200 and again.json()["run"] == first
    assert expense.calls == 1 and len(chat.classifications) == 1
    assert send(client, "换内容", message_id=message_id).status_code == 409
    chat.failure = None
    second_id = str(uuid4())
    second = send(
        client,
        "金额改为300元",
        message_id=second_id,
        run_id=first["id"],
        expected_version=first["version"],
    )
    assert second.status_code == 200
    chat.failure = HTTPException(503, "分类器不可用")
    assert (
        send(
            client,
            "金额改为300元",
            message_id=second_id,
            run_id=first["id"],
            expected_version=first["version"],
        ).json()["run"]
        == second.json()["run"]
    )
    assert db.scalar(select(func.count()).select_from(AgentRun)) == 1


@pytest.mark.parametrize("intent", ["mixed", "clarify", "unsupported"])
def test_non_expense_intents_do_not_create_tasks(client, db, models, intent):
    chat, expense = models
    chat.decision = IntentDecision(intent=intent)
    before = counts(db)
    response = send(client, "告诉我标准再创建报销")
    assert response.status_code == 200 and response.json()["kind"] == "clarification"
    assert counts(db) == before and expense.calls == 0 and not chat.answers


def test_no_results_historical_date_and_budget_do_not_call_answer_model(
    client, db, models, monkeypatch
):
    """无依据 / 预算不足不硬答；明确历史日期进入原有日期过滤。"""
    chat, _ = models
    seed_knowledge(db)
    chat.decision = IntentDecision(intent="policy", query="成都住宿标准", as_of=date(2025, 12, 31))
    response = send(client, "2025年12月31日成都住宿标准？")
    assert response.json()["status"] == "insufficient" and response.json()["as_of"] == "2025-12-31"
    assert not chat.answers
    chat.decision = IntentDecision(intent="policy", query="火星天气预报")
    assert send(client).json()["status"] == "insufficient" and not chat.answers
    chat.decision = IntentDecision(intent="policy", query="成都住宿标准")
    monkeypatch.setattr("enterprise_agent.knowledge.answering.MAX_EVIDENCE_CHARS", 1)
    assert send(client).json()["status"] == "insufficient" and not chat.answers


@pytest.mark.parametrize("mode", ["missing", "forged", "quote", "duplicate"])
def test_invalid_model_citations_are_rejected(client, db, models, mode):
    """校验引用归属、逐字摘录和去重；不能因为返回了合法格式ID就相信模型。"""
    chat, _ = models
    seed_knowledge(db)

    def invalid(context):
        evidence = context["evidence"][0]
        citation = {"citation_id": evidence["citation_id"], "quote": evidence["text"]}
        citations = [citation]
        if mode == "missing":
            citations = []
        elif mode == "forged":
            citation["citation_id"] = "f" * 64
        elif mode == "quote":
            citation["quote"] = "模型伪造的制度摘录"
        else:
            citations.append(citation.copy())
        return PolicyGeneration(status="answered", answer="无效答案", citations=citations)

    chat.answer = invalid
    assert send(client).status_code == 502


@pytest.mark.parametrize("status", ["insufficient", "conflict", "needs_clarification"])
def test_uncertain_generation_uses_fixed_message_without_policy_claims(client, db, models, status):
    chat, _ = models
    seed_knowledge(db)
    chat.output = {"status": status, "answer": "这里夹带了无依据的金额999元", "citations": []}
    response = send(client)
    assert response.status_code == 200 and response.json()["status"] == status
    assert "999" not in response.json()["message"] and response.json()["sources"] == []


def test_model_failure_and_retrieval_failure_are_errors_not_no_policy(
    client, db, models, monkeypatch
):
    chat, _ = models
    before = counts(db)
    chat.failure = HTTPException(503, "分类失败")
    assert send(client).status_code == 503
    assert counts(db) == before
    chat.failure = None

    def fail(*_args):
        raise HTTPException(503, "检索不可用")

    monkeypatch.setattr("enterprise_agent.knowledge.retrieval.search_documents", fail)
    assert send(client).status_code == 503 and not chat.answers


def test_withdrawal_during_generation_blocks_answer(client, db, models):
    """模拟模型慢调用期间撤回来源，最终不能展示已经取出的条款。"""
    chat, _ = models
    seed_knowledge(db)

    def withdraw():
        from enterprise_agent.db.models import KnowledgeDocument, User

        manager = db.get(User, DEMO_ACCOUNTS["finance"])
        document = db.scalar(select(KnowledgeDocument))
        change_publication(db, manager, document.id, publish=False, request_id=str(uuid4()))

    chat.on_answer = withdraw
    assert send(client).status_code == 409


def test_auth_ownership_and_request_whitelist(client, db, models):
    chat, _ = models
    body = {"message_id": str(uuid4()), "message": "成都住宿标准"}
    assert client.post("/api/v1/agent/chat", json=body).status_code == 401
    for extra in [
        {"tenant_id": str(uuid4())},
        {"expected_version": 0},
        {"run_id": str(uuid4())},
        {"run_id": str(uuid4()), "expected_version": True},
    ]:
        assert send(client, **extra).status_code == 422
    chat.decision = IntentDecision(intent="expense")
    run = send(client).json()["run"]
    before = len(chat.classifications)
    response = client.post(
        "/api/v1/agent/chat",
        headers=headers("employee_b"),
        json={
            **body,
            "run_id": run["id"],
            "expected_version": run["version"],
        },
    )
    assert response.status_code == 404 and len(chat.classifications) == before
    assert db.get(AgentRun, UUID(run["id"])).owner_id == DEMO_ACCOUNTS["employee_a"]


@pytest.mark.parametrize(
    "message,proposed,clear,resolved",
    [
        ("去年成都住宿标准？", None, False, None),
        ("去年成都住宿标准？", date(2025, 1, 1), False, None),
        ("2026年成都住宿标准？", None, False, None),
        ("2026-02-30成都住宿标准？", None, False, None),
        ("2026-01-01到2026-02-01的标准？", None, False, None),
        ("2025年12月31日的标准？", None, True, date(2025, 12, 31)),
        ("2025/12/31的标准？", date(2026, 1, 1), True, date(2025, 12, 31)),
        ("昨天成都住宿标准？", None, True, date(2026, 11, 24)),
        ("昨天和今天的标准？", None, False, None),
        ("成都住宿标准？", date(2025, 1, 1), False, None),
        ("成都住宿标准？", None, True, None),
    ],
)
def test_date_gate_does_not_trust_model_guess(message, proposed, clear, resolved):
    """真实模型发现的时间歧义不只靠 Prompt；固定日历规则作最后门禁。"""
    assert policy_date(message, proposed, date(2026, 11, 25)) == (clear, resolved)


def test_policy_date_gate_clarifies_even_if_classifier_misses_ambiguity(client, db, models):
    chat, _ = models
    seed_knowledge(db)
    chat.decision = IntentDecision(intent="policy", query="成都住宿标准")
    response = send(client, "去年成都住宿标准？")
    assert response.json()["status"] == "needs_clarification" and not chat.answers


def test_uncertain_model_citations_are_discarded(client, db, models):
    chat, _ = models
    seed_knowledge(db)
    chat.output = {
        "status": "needs_clarification",
        "answer": "夹带错误标准999元",
        "citations": [
            {"citation_id": "f" * 64, "quote": "不可信摘录"},
        ],
    }
    response = send(client)
    assert response.status_code == 200 and response.json()["status"] == "needs_clarification"
    assert response.json()["sources"] == [] and "999" not in response.json()["message"]


def test_empty_policy_query_retrieves_using_original_question(client, db, models, monkeypatch):
    """真实模型曾返回 policy + 空 query；允许用原问题检索，不把辅助改写缺失当作故障。"""
    from enterprise_agent.knowledge import retrieval

    chat, expense = models
    seed_knowledge(db)
    chat.decision = IntentDecision(intent="policy", query="  ")
    original = retrieval.search_documents
    queries = []

    def capture(db, actor, query):
        queries.append(query.query)
        return original(db, actor, query)

    monkeypatch.setattr(retrieval, "search_documents", capture)
    question = "成都住宿标准是多少？"
    response = send(client, question)
    assert response.status_code == 200 and response.json()["status"] == "answered"
    assert queries == [question, question]  # 首次检索和作答后的权限复查均采用原问题。
    assert expense.calls == 0


def test_empty_policy_query_does_not_truncate_long_question(client, db, models):
    """回退原问题时也遵守检索长度上限，要求用户缩小范围，不丢掉超长问题尾部的条件。"""
    chat, expense = models
    chat.decision = IntentDecision(intent="policy", query="")
    before = counts(db)
    response = send(client, "成都住宿标准" + "请解释" * 100)
    assert response.status_code == 200 and response.json()["status"] == "needs_clarification"
    assert not chat.answers and expense.calls == 0
    assert counts(db) == before
