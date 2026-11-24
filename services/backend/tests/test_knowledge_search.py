"""验证条款切分、原文引用、完整索引提交、检索权限及适用版本选择。"""

from hashlib import sha256
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import event, func, select

from enterprise_agent.db.models import AuditLog, KnowledgeDocument, KnowledgeIndex, Tenant, User
from enterprise_agent.db.seed import DEMO_ACCOUNTS, seed_knowledge
from enterprise_agent.knowledge.chunking import RULE_VERSION, build_units
from enterprise_agent.knowledge.normalization import clean_original, normalize_document
from enterprise_agent.knowledge.retrieval import build_index
from enterprise_agent.knowledge.schemas import RegisterDocument
from enterprise_agent.knowledge.service import change_publication, register_document

from conftest import headers, token


def make_policy(db, *, version=1, visibility="tenant", start="2026-01-01", end=None, indexed=True):
    """每个测试经正式服务发布指定版本，不直接制造可检索状态。"""
    manager = db.get(User, DEMO_ACCOUNTS["finance"])
    command = RegisterDocument(
        source_key="search-policy",
        title="虚构差旅制度",
        version=version,
        format="markdown",
        visibility=visibility,
        effective_from=start,
        effective_until=end,
    )
    raw = (
        f"# 虚构差旅制度\n\n## 住宿\n\n第一条 成都住宿每晚不超过 {400 + version}.00 元。\n\n"
        "超过限额的部分不得自动报销。\n\n| 条件 | 要求 |\n| --- | --- |\n| 成都 | 有效凭证 |\n\n"
        "## 交通\n\n第二条 高铁二等座按有效凭证列明的实际金额申请。"
    ).encode()
    row = register_document(db, manager, command, raw, str(uuid4()))
    change_publication(db, manager, row.id, publish=True, request_id=str(uuid4()))
    if indexed:
        build_index(db, manager, row.id, str(uuid4()))
    return manager, row


def search(client, query="成都住宿标准", **kwargs):
    """固定日期避免测试结果依赖宿主机时钟；HTTP 返回原文证据而非答案。"""
    return client.post(
        "/api/v1/knowledge/search",
        headers=headers(),
        json={
            "query": query,
            "as_of": "2026-11-24",
            **kwargs,
        },
    )


def test_chunking_is_repeatable_and_preserves_clause_table_and_line_mapping():
    """长条款可分为多个子片段，但完整证据保留后续否定条件及表格，引用可重复生成。"""
    raw = (
        "# 虚构制度\r\n\r\n第一条 成都住宿每晚不超过 400.00 元。\r\n\r\n"
        + "业务凭证必须完整。" * 150
        + "\r\n\r\n超过限额不得自动报销。\r\n\r\n"
        + "| 费用 | 条件 |\r\n| --- | --- |\r\n| 400.00 元 | 成都 |\r\n\r\n第二条 交通另行核对。"
    ).encode()
    document_id = uuid4()
    normalized = normalize_document(raw, "markdown")
    units = build_units(document_id, sha256(raw).hexdigest(), raw, normalized)
    assert len(units) == 2 and len(units[0].chunks) > 1
    unit = units[0]
    assert "400.00 元" in unit.text and "超过限额不得自动报销" in unit.text
    assert "| 400.00 元 | 成都 |" in unit.text
    assert unit.source_start_line == 3 and unit.source_end_line == 11
    original_lines = clean_original(raw).split("\n")
    assert unit.text == "\n".join(original_lines[2:11])
    assert "".join(unit.text[c.start_char : c.end_char] for c in unit.chunks) == unit.text
    assert all(
        c.source_start_line <= c.source_end_line <= unit.source_end_line for c in unit.chunks
    )
    assert [u.model_dump() for u in units] == [
        u.model_dump()
        for u in build_units(
            document_id,
            sha256(raw).hexdigest(),
            raw,
            normalized,
        )
    ]


def test_search_returns_full_evidence_reference_and_no_financial_writes(client, db):
    """命中可追溯到同版条款，检索只读且不追加审计、消息或财务记录。"""
    _, row = make_policy(db)
    before = db.scalar(select(func.count()).select_from(AuditLog))
    response = search(client)
    assert response.status_code == 200
    body = response.json()
    assert body["as_of"] == "2026-11-24" and body["query"] == "成都住宿标准"
    hit = body["items"][0]
    assert hit["document"]["id"] == str(row.id) and hit["document"]["version"] == 1
    assert "401.00 元" in hit["text"] and "超过限额的部分不得" in hit["text"]
    assert "| 成都 | 有效凭证 |" in hit["text"] and len(hit["citation_id"]) == 64
    assert hit["rule_version"] == RULE_VERSION and "answer" not in body
    assert search(client).json() == body
    assert db.scalar(select(func.count()).select_from(AuditLog)) == before
    assert search(client, "高铁二等座").json()["items"][0]["text"].startswith("第二条")
    assert search(client, "火星天气预报").json()["items"] == []
    assert search(client, "请问报销制度规定").json()["items"] == []


def test_index_command_is_idempotent_and_failure_leaves_no_partial_index(db):
    """索引先完整生成再与审计提交；重复运行不增加快照，失败回滚没有可见半成品。"""
    manager, row = make_policy(db, indexed=False)

    def fail(_mapper, _connection, audit):
        if audit.action == "knowledge.index":
            raise RuntimeError("simulated index audit failure")

    event.listen(AuditLog, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError):
            build_index(db, manager, row.id, str(uuid4()))
        db.rollback()
    finally:
        event.remove(AuditLog, "before_insert", fail)
    assert db.get(KnowledgeIndex, row.id) is None
    first = build_index(db, manager, row.id, str(uuid4()))
    payload = first.units
    second = build_index(db, manager, row.id, str(uuid4()))
    assert (
        second.units == payload and db.scalar(select(func.count()).select_from(KnowledgeIndex)) == 1
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.action == "knowledge.index",
            )
        )
        == 1
    )


def test_missing_or_incomplete_index_is_not_searchable(client, db):
    """发布与索引是独立动作；没有快照不命中，结构损坏明确报错而非返回残缺结果。"""
    manager, row = make_policy(db, indexed=False)
    assert search(client).json()["items"] == []
    index = build_index(db, manager, row.id, str(uuid4()))
    index.chunk_count += 1
    db.commit()
    assert search(client).status_code == 503


def test_date_and_latest_version_do_not_fall_back_to_unindexed_old_policy(client, db):
    """适用时间先筛选，再选最高可读版本；新版无索引时不拿旧标准冒充当前依据。"""
    make_policy(db, end="2026-06-30")
    manager, newer = make_policy(db, version=2, start="2026-07-01", indexed=False)
    assert search(client).json()["items"] == []
    old = search(client, as_of="2026-06-30").json()["items"]
    assert old[0]["document"]["version"] == 1
    build_index(db, manager, newer.id, str(uuid4()))
    assert search(client).json()["items"][0]["document"]["version"] == 2
    assert search(client, as_of="2025-12-31").json()["items"] == []
    # 适用区间重叠仍确定性选择最高版本，避免同一问题同时给出两个冲突标准。
    make_policy(db, version=3)
    assert {hit["document"]["version"] for hit in search(client).json()["items"]} == {3}


def test_private_cross_tenant_withdrawn_and_unpublished_are_filtered(client, db):
    """权限 / 发布状态每次从原文读取，索引存在不代表用户可以访问。"""
    manager, row = make_policy(db, visibility="owner")
    assert search(client).json()["items"] == []
    payload = {"query": "成都住宿", "as_of": "2026-11-24"}
    assert client.post("/api/v1/knowledge/search", headers=headers("finance"), json=payload).json()[
        "items"
    ]
    tenant = Tenant(name="其他企业")
    db.add(tenant)
    db.flush()
    other = User(tenant_id=tenant.id, name="外部财务", role="finance")
    db.add(other)
    db.commit()
    auth = {"Authorization": f"Bearer {token(str(other.id))}"}
    assert client.post("/api/v1/knowledge/search", headers=auth, json=payload).json()["items"] == []
    change_publication(db, manager, row.id, publish=False, request_id=str(uuid4()))
    assert (
        client.post("/api/v1/knowledge/search", headers=headers("finance"), json=payload).json()[
            "items"
        ]
        == []
    )
    assert db.get(KnowledgeIndex, row.id) is not None


@pytest.mark.parametrize(
    "invalid",
    [
        {"query": " "},
        {"query": "x" * 301},
        {"query": "成都", "tenant_id": str(uuid4())},
        {"query": "成都", "as_of": "2026-02-30"},
        {"query": "成都", "limit": True},
        {"query": "成都", "limit": 11},
    ],
)
def test_search_rejects_invalid_input(client, invalid):
    """原始查询输入由 schema 严格约束，不能接受调用者指定的身份或布尔条数。"""
    assert (
        client.post("/api/v1/knowledge/search", headers=headers(), json=invalid).status_code == 422
    )


def test_search_requires_identity_and_index_management_requires_owner(client, db):
    """检索必须认证，员工及其他财务不可越过责任人的索引管理边界。"""
    manager, row = make_policy(db, indexed=False)
    assert client.post("/api/v1/knowledge/search", json={"query": "成都"}).status_code == 401
    with pytest.raises(HTTPException) as rejected:
        build_index(db, db.get(User, DEMO_ACCOUNTS["employee_a"]), row.id, str(uuid4()))
    assert rejected.value.status_code == 403
    row.status = "draft"
    db.commit()
    with pytest.raises(HTTPException) as rejected:
        build_index(db, manager, row.id, str(uuid4()))
    assert rejected.value.status_code == 404


def test_demo_seed_indexes_existing_policy_without_duplicate_snapshots(db):
    """升级后重新运行 seed 为现有已发布制度补建索引，引用保持稳定。"""
    seed_knowledge(db)
    row = db.scalar(select(KnowledgeDocument))
    first = db.get(KnowledgeIndex, row.id).units
    seed_knowledge(db)
    assert db.get(KnowledgeIndex, row.id).units == first
    assert db.scalar(select(func.count()).select_from(KnowledgeIndex)) == 1
