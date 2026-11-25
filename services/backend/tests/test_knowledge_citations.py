"""固定版本引用读取：验证权限、版本、只读边界及损坏快照的明确失败。"""

from copy import deepcopy
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from enterprise_agent.db.models import (
    AgentExecution,
    AgentRun,
    AuditLog,
    Expense,
    KnowledgeDocument,
    KnowledgeIndex,
    Tenant,
    User,
)
from enterprise_agent.db.seed import DEMO_ACCOUNTS
from enterprise_agent.knowledge.retrieval import build_index
from enterprise_agent.knowledge.schemas import RegisterDocument
from enterprise_agent.knowledge.service import change_publication, register_document

from conftest import headers, token


def policy(db, *, source="citation-demo", version=1, visibility="tenant", end=None, indexed=True):
    """通过管理服务构造虚构来源，BOM / CRLF / 空行和表格均保留在原件中。"""
    manager = db.get(User, DEMO_ACCOUNTS["finance"])
    raw = (
        "\ufeff# 虚构制度\r\n\r\n第一条 成都住宿不超过400.00元。\r\n\r\n"
        "超标部分不能自动报销。\r\n\r\n| 条件 | 说明 |\r\n| --- | --- |\r\n"
        "| 成都 | 有效凭证 |\r\n\r\n第二条 高铁二等座按实际金额核对。"
    ).encode()
    document = register_document(
        db,
        manager,
        RegisterDocument(
            source_key=source,
            title="虚构引用制度",
            version=version,
            format="markdown",
            effective_from="2026-01-01",
            effective_until=end,
            visibility=visibility,
        ),
        raw,
        str(uuid4()),
    )
    change_publication(db, manager, document.id, publish=True, request_id=str(uuid4()))
    index = build_index(db, manager, document.id, str(uuid4())) if indexed else None
    return manager, document, index


def url(document_id, citation_id):
    return f"/api/v1/knowledge/documents/{document_id}/citations/{citation_id}"


def counts(db):
    return tuple(
        db.scalar(select(func.count()).select_from(model))
        for model in (
            KnowledgeDocument,
            KnowledgeIndex,
            AuditLog,
            AgentRun,
            AgentExecution,
            Expense,
        )
    )


def test_resolve_returns_complete_same_version_evidence_without_reprocessing(
    client, db, monkeypatch
):
    """解析完整条款而非子片段，不调用检索 / 清洗 / 切分 / 建索引，重复访问不写任何记录。"""
    _, document, index = policy(db)
    unit = deepcopy(index.units[0])
    original_units = deepcopy(index.units)
    before = counts(db)

    def forbidden(*args, **kwargs):
        pytest.fail("引用读取不得触发重新处理或检索")

    for name in [
        "enterprise_agent.knowledge.retrieval.search_documents",
        "enterprise_agent.knowledge.retrieval.build_index",
        "enterprise_agent.knowledge.chunking.build_units",
        "enterprise_agent.knowledge.normalization.normalize_document",
    ]:
        monkeypatch.setattr(name, forbidden)
    path = url(document.id, unit["id"])
    response = client.get(path, headers=headers())
    assert response.status_code == 200
    body = response.json()
    assert body["document"]["id"] == str(document.id) and body["document"]["version"] == 1
    assert body["content_hash"] == sha256(document.original_bytes).hexdigest()
    assert body["citation_id"] == unit["id"] and body["rule_version"] == index.rule_version
    assert body["text"] == unit["text"]
    assert "不能自动报销" in body["text"] and "| 成都 | 有效凭证 |" in body["text"]
    assert (body["source_start_line"], body["source_end_line"]) == (3, 9)
    assert body["heading_path"] == unit["heading_path"]
    assert set(body) == {
        "document",
        "content_hash",
        "citation_id",
        "rule_version",
        "text",
        "heading_path",
        "source_start_line",
        "source_end_line",
    }
    assert not {"tenant_id", "owner_id", "original_bytes"} & body["document"].keys()
    assert client.get(path, headers=headers()).json() == body
    assert counts(db) == before and db.get(KnowledgeIndex, document.id).units == original_units


def test_unknown_citation_wrong_document_and_chunk_id_are_not_resolved(client, db):
    """文档ID与引用ID共同限定目标；子片段ID不能冒充完整条款引用。"""
    _, document, index = policy(db)
    _, other, _ = policy(db, source="other-policy")
    unit = index.units[0]
    paths = [
        url(document.id, "f" * 64),
        url(other.id, unit["id"]),
        url(document.id, unit["chunks"][0]["id"]),
        url(uuid4(), unit["id"]),
    ]
    responses = [client.get(path, headers=headers()) for path in paths]
    assert all(response.status_code == 404 for response in responses)
    assert len({response.json()["message"] for response in responses}) == 1


def test_historical_reference_remains_fixed_after_new_version_is_published(client, db):
    """查阅依据不按今天筛选；即使新版无索引或已经建立索引，旧版引用也不会被替换。"""
    _, old, index = policy(db, end="2026-06-30")
    path = url(old.id, index.units[0]["id"])
    expected = client.get(path, headers=headers()).json()
    manager, newer, _ = policy(db, version=2, indexed=False)
    assert client.get(path, headers=headers()).json() == expected
    build_index(db, manager, newer.id, str(uuid4()))
    assert client.get(path, headers=headers()).json() == expected


def test_current_permissions_and_publication_are_checked_before_index_visibility(client, db):
    """私有 / 跨租户 / 撤回均不可读；先授权再看快照，避免索引错误泄漏来源存在性。"""
    manager, document, index = policy(db, visibility="owner")
    path = url(document.id, index.units[0]["id"])
    assert client.get(path, headers=headers()).status_code == 404
    assert client.get(path, headers=headers("finance")).status_code == 200
    tenant = Tenant(name="虚构外部企业")
    db.add(tenant)
    db.flush()
    outsider = User(tenant_id=tenant.id, name="虚构外部员工", role="employee")
    db.add(outsider)
    db.commit()
    assert (
        client.get(path, headers={"Authorization": f"Bearer {token(str(outsider.id))}"}).status_code
        == 404
    )
    change_publication(db, manager, document.id, publish=False, request_id=str(uuid4()))
    index.rule_version = "unsupported"
    db.commit()
    assert client.get(path, headers=headers("finance")).status_code == 404


def test_unpublished_source_and_missing_identity_are_rejected(client, db):
    _, document, index = policy(db)
    path = url(document.id, index.units[0]["id"])
    assert client.get(path).status_code == 401
    # 模拟未发布的存量来源，即使已有快照也不能仅凭ID读取。
    document.status = "draft"
    db.commit()
    assert client.get(path, headers=headers("finance")).status_code == 404


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "rule",
        "index_hash",
        "raw_hash",
        "unit_count",
        "chunk_count",
        "not_list",
        "empty",
        "malformed",
        "duplicate",
        "range",
        "text_lines",
    ],
)
def test_damaged_snapshots_fail_explicitly_without_rebuilding(client, db, damage):
    """损坏快照统一返回503，不变成无命中、500或隐式重建后的新引用。"""
    _, document, index = policy(db)
    path = url(document.id, index.units[0]["id"])
    units = deepcopy(index.units)
    if damage == "missing":
        db.delete(index)
    elif damage == "rule":
        index.rule_version = "old-rule"
    elif damage == "index_hash":
        index.content_hash = "0" * 64
    elif damage == "raw_hash":
        document.original_bytes += b"\n"
    elif damage == "unit_count":
        index.unit_count += 1
        index.chunk_count += 1
    elif damage == "chunk_count":
        index.chunk_count += 1
    elif damage == "not_list":
        index.units = {"invalid": True}
    elif damage == "empty":
        index.units = []
    elif damage == "malformed":
        index.units = [{}]
    elif damage == "duplicate":
        units[1]["id"] = units[0]["id"]
        index.units = units
    elif damage == "range":
        units[0]["source_end_line"] = 10000
        index.units = units
    elif damage == "text_lines":
        units[0]["text"] += "\n伪造的额外行"
        index.units = units
    db.commit()
    before = counts(db)
    response = client.get(path, headers=headers())
    assert response.status_code == 503
    assert "text" not in response.json() and counts(db) == before


@pytest.mark.parametrize(
    "query",
    [
        "tenant_id=forged",
        "owner_id=forged",
        "version=2",
        "source_start_line=1",
    ],
)
def test_reference_does_not_accept_caller_assigned_mapping(client, db, query):
    _, document, index = policy(db)
    assert (
        client.get(
            url(document.id, index.units[0]["id"]) + "?" + query, headers=headers()
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "document_id,citation_id",
    [
        ("not-a-uuid", "a" * 64),
        (str(uuid4()), "a" * 63),
        (str(uuid4()), "A" * 64),
    ],
)
def test_reference_path_is_validated(client, document_id, citation_id):
    assert client.get(url(document_id, citation_id), headers=headers()).status_code == 422
