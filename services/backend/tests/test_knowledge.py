"""制度原件保真、发布事务、版本不可覆盖和阅读授权的功能验证。"""

from hashlib import sha256
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import event, func, select

from enterprise_agent.db.models import AuditLog, KnowledgeDocument, Tenant, User
from enterprise_agent.db.seed import DEMO_ACCOUNTS, seed_knowledge
from enterprise_agent.knowledge.normalization import normalize_document
from enterprise_agent.knowledge.schemas import RegisterDocument
from enterprise_agent.knowledge.service import change_publication, register_document

from conftest import headers, token


@pytest.fixture
def manager(db):
    """管理身份由已迁移数据库的财务用户读取，测试不伪造角色参数。"""
    return db.get(User, DEMO_ACCOUNTS["finance"])


@pytest.fixture
def command():
    """除正文外均为固定来源元数据，测试按需生成新版本或私有范围。"""
    return RegisterDocument(
        source_key="test-policy",
        title="虚构制度",
        version=1,
        format="markdown",
        effective_from="2026-01-01",
        visibility="tenant",
    )


def register(db, manager, command, raw=None):
    """复用正式登记服务，默认正文含不能被清洗改写的金额和否定条件。"""
    return register_document(
        db,
        manager,
        command,
        raw if raw is not None else "# 虚构制度\n\n第一条 成都住宿每晚不超过 400.00 元。".encode(),
        str(uuid4()),
    )


def publish(db, manager, row):
    """发布操作独立于登记，读取 API 不得暴露中间草稿。"""
    return change_publication(db, manager, row.id, publish=True, request_id=str(uuid4()))


def test_normalize_preserves_facts_tables_and_original_lines():
    """BOM、CRLF 与无意义控制字符可清理，金额 / 否定 / 表格关系及物理行号必须保留。"""
    raw = (
        "\ufeff# 虚构制度\r\n\r\n第一章 住宿\r\n第一条 不超过 400.00 元。\x00\r\n\r\n"
        "| 日期 | 金额 |\r\n| --- | --- |\r\n| 2026-11-24 | 400.00 元 |"
    ).encode()
    normalized = normalize_document(raw, "markdown")
    blocks = normalized.blocks
    assert [b.kind for b in blocks] == ["heading", "heading", "paragraph", "table"]
    assert blocks[2].text == "第一条 不超过 400.00 元。"
    assert blocks[2].source_start_line == blocks[2].source_end_line == 4
    assert blocks[2].heading_path == ["虚构制度", "第一章 住宿"]
    assert blocks[3].source_start_line == 6 and blocks[3].source_end_line == 8
    assert "2026-11-24 | 400.00 元" in blocks[3].text
    assert normalize_document(raw, "markdown").model_dump() == normalized.model_dump()


def test_text_clauses_and_fenced_content_keep_structure():
    """纯文本条款不被误删为标题；代码围栏内部不生成伪章节。"""
    text = normalize_document("第一条 不得自批。\n第二条 金额为 1.00 元。".encode(), "text")
    assert len(text.blocks) == 2 and text.blocks[0].text == "第一条 不得自批。"
    markdown = normalize_document(b"# policy\n\n```text\n# not heading\n```", "markdown")
    assert [b.kind for b in markdown.blocks] == ["heading", "code"]
    assert markdown.blocks[1].source_end_line == 5
    # 标题中的 # 可能是实际名称，只有空格分隔的尾部标记才可以去掉。
    sharp = normalize_document(b"# C#\n\npolicy body", "markdown")
    assert sharp.blocks[0].text == "C#"


@pytest.mark.parametrize(
    "raw",
    [b"", b" \r\n\x00", b"# title", b"\xff", b"```text\nbody", b"a" * 131073],
    ids=["empty", "blank-controls", "heading-only", "bad-encoding", "open-fence", "oversize"],
)
def test_invalid_documents_never_create_rows(db, manager, command, raw):
    """空正文、坏编码、未闭合围栏和超限原件在写入前被拒绝。"""
    before = db.scalar(select(func.count()).select_from(AuditLog))
    with pytest.raises(ValueError):
        register(db, manager, command, raw)
    assert db.scalar(select(func.count()).select_from(KnowledgeDocument)) == 0
    assert db.scalar(select(func.count()).select_from(AuditLog)) == before


def test_registration_preserves_bytes_is_idempotent_and_new_versions_survive(db, manager, command):
    """相同版本重放复用记录；正文、日期或阅读范围变化必须用新版本，原件不可覆盖。"""
    raw = "\ufeff# 制度\r\n\r\n不得超过 400.00 元。\x00".encode()
    row = register(db, manager, command, raw)
    assert row.original_bytes == raw and row.content_hash == sha256(raw).hexdigest()
    assert row.status == "draft"
    assert register(db, manager, command, raw).id == row.id
    for changed_command, changed_raw in [
        (command, raw + b"\nchanged"),
        (command.model_copy(update={"visibility": "owner"}), raw),
        (command.model_copy(update={"effective_until": command.effective_from}), raw),
    ]:
        with pytest.raises(HTTPException) as conflict:
            register(db, manager, changed_command, changed_raw)
        assert conflict.value.status_code == 409
    newer = register(db, manager, command.model_copy(update={"version": 2}), raw + b"\nnew")
    assert newer.id != row.id and db.get(KnowledgeDocument, row.id).original_bytes == raw


def test_publication_visibility_and_withdrawal(client, db, manager, command):
    """未发布不可读，发布后可读，撤回后列表 / 详情立即拒绝；重复发布不重复审计。"""
    row = register(db, manager, command)
    path = f"/api/v1/knowledge/documents/{row.id}"
    assert client.get(path, headers=headers()).status_code == 404
    publish(db, manager, row)
    audits = db.scalar(select(func.count()).select_from(AuditLog))
    publish(db, manager, row)
    assert db.scalar(select(func.count()).select_from(AuditLog)) == audits
    detail = client.get(path, headers=headers()).json()
    assert detail["version"] == 1 and detail["owner_name"] == manager.name
    assert detail["original_text"] == row.original_bytes.decode()
    assert detail["normalized_document"]["blocks"]
    assert "tenant_id" not in detail and "original_bytes" not in detail
    assert client.get("/api/v1/knowledge/documents", headers=headers()).json()["total"] == 1
    change_publication(db, manager, row.id, publish=False, request_id=str(uuid4()))
    assert client.get(path, headers=headers()).status_code == 404
    assert client.get("/api/v1/knowledge/documents", headers=headers()).json()["total"] == 0
    with pytest.raises(HTTPException):
        publish(db, manager, row)
    assert register(db, manager, command).status == "withdrawn"


def test_private_cross_tenant_and_anonymous_access(client, db, manager, command):
    """同企业员工不能读责任人私有版本；其他租户即使财务角色也不可读。"""
    private = register(db, manager, command.model_copy(update={"visibility": "owner"}))
    publish(db, manager, private)
    public = register(db, manager, command.model_copy(update={"version": 2}))
    publish(db, manager, public)
    assert (
        client.get(f"/api/v1/knowledge/documents/{private.id}", headers=headers()).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/knowledge/documents/{private.id}", headers=headers("finance")
        ).status_code
        == 200
    )
    tenant = Tenant(name="其他企业")
    db.add(tenant)
    db.flush()
    other = User(tenant_id=tenant.id, name="其他财务", role="finance")
    db.add(other)
    db.commit()
    auth = {"Authorization": f"Bearer {token(str(other.id))}"}
    assert client.get(f"/api/v1/knowledge/documents/{public.id}", headers=auth).status_code == 404
    assert client.get("/api/v1/knowledge/documents", headers=auth).json()["items"] == []
    assert client.get("/api/v1/knowledge/documents").status_code == 401
    for invalid in ["tenant_id=x", "status=draft", "limit=51", "offset=-1"]:
        assert (
            client.get(f"/api/v1/knowledge/documents?{invalid}", headers=headers()).status_code
            == 422
        )
    assert (
        client.get("/api/v1/knowledge/documents?limit=1&offset=1", headers=headers()).json()[
            "items"
        ]
        == []
    )


def test_employee_cannot_manage_and_authoritative_fields_rejected(db, manager, command):
    """管理命令也做权限校验；本机脚本不是绕过领域授权的直接写库入口。"""
    employee = db.get(User, DEMO_ACCOUNTS["employee_a"])
    with pytest.raises(HTTPException) as denied:
        register(db, employee, command)
    assert denied.value.status_code == 403
    row = register(db, manager, command)
    with pytest.raises(HTTPException) as denied:
        publish(db, employee, row)
    assert denied.value.status_code == 403
    for field in ["tenant_id", "owner_id", "status"]:
        with pytest.raises(ValidationError):
            RegisterDocument.model_validate({**command.model_dump(), field: "forged"})
    with pytest.raises(ValidationError):
        RegisterDocument.model_validate({**command.model_dump(), "effective_until": "2025-01-01"})


def test_publish_revalidates_structure_and_registration_audit_rolls_back(db, manager, command):
    """持久结构损坏不可发布；登记的审计写入失败时原件与结构也必须回滚。"""
    row = register(db, manager, command)
    row.normalized_document = {"schema_version": "unknown", "blocks": []}
    db.commit()
    with pytest.raises(HTTPException) as rejected:
        publish(db, manager, row)
    assert rejected.value.status_code == 409 and row.status == "draft"
    db.rollback()

    def fail_audit(_mapper, _connection, log):
        if log.action == "knowledge.register":
            raise RuntimeError("simulated audit failure")

    event.listen(AuditLog, "before_insert", fail_audit)
    try:
        with pytest.raises(RuntimeError):
            register(db, manager, command.model_copy(update={"version": 2}))
        db.rollback()
    finally:
        event.remove(AuditLog, "before_insert", fail_audit)
    assert db.scalar(select(func.count()).select_from(KnowledgeDocument)) == 1


def test_seed_is_repeatable_and_does_not_republish_withdrawn_policy(db, manager):
    """演示初始化可重复运行，但不能恢复已撤回制度或制造新版本。"""
    seed_knowledge(db)
    row = db.scalar(select(KnowledgeDocument))
    seed_knowledge(db)
    assert db.scalar(select(func.count()).select_from(KnowledgeDocument)) == 1
    change_publication(db, manager, row.id, publish=False, request_id=str(uuid4()))
    seed_knowledge(db)
    assert row.status == "withdrawn"
