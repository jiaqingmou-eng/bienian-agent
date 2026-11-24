"""制度登记、发布和读取授权；所有数据库写入仅在 Python 知识服务完成。"""

from hashlib import sha256
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from enterprise_agent.db.models import AuditLog, KnowledgeDocument, User
from enterprise_agent.knowledge.normalization import decode_original, normalize_document
from enterprise_agent.knowledge.schemas import (
    DocumentDetail,
    DocumentListQuery,
    DocumentListResponse,
    DocumentSummary,
    RegisterDocument,
)


def require_manager(actor: User) -> None:
    """暂复用财务角色管理制度；不增加角色体系，员工即使能读原文也无管理权限。"""
    if actor.role != "finance":
        raise HTTPException(403, "只有财务角色可以登记、发布或撤回制度")


def register_document(
    db: Session, actor: User, command: RegisterDocument, raw: bytes, request_id: str
) -> KnowledgeDocument:
    """先确定性校验，再将原件、统一结构和登记审计在一个短事务中提交。

    责任人固定为当前管理者，租户来自其可信用户记录；命令不接受这些权威字段。
    唯一键保证同来源 / 版本不可覆盖；重放同一输入只读复用，不重新发布已撤回版本。
    """
    require_manager(actor)
    normalized = normalize_document(raw, command.format)
    values = command.model_dump()
    digest = sha256(raw).hexdigest()

    def existing() -> KnowledgeDocument | None:
        return db.scalar(
            select(KnowledgeDocument).where(
                KnowledgeDocument.tenant_id == actor.tenant_id,
                KnowledgeDocument.source_key == command.source_key,
                KnowledgeDocument.version == command.version,
            )
        )

    def check_same(row: KnowledgeDocument) -> KnowledgeDocument:
        """正文或元数据有变化必须新增版本，不能借登记重试修改旧的引用依据。"""
        if (
            row.owner_id != actor.id
            or row.content_hash != digest
            or any(getattr(row, key) != value for key, value in values.items())
        ):
            raise HTTPException(409, "该制度版本已存在且内容或元数据不同，请新增版本")
        return row

    if row := existing():
        return check_same(row)
    row = KnowledgeDocument(
        **values,
        tenant_id=actor.tenant_id,
        owner_id=actor.id,
        original_bytes=raw,
        content_hash=digest,
        normalized_document=normalized.model_dump(),
        status="draft",
    )
    try:
        db.add(row)
        db.flush()
        db.add(
            AuditLog(
                tenant_id=actor.tenant_id,
                actor_id=actor.id,
                action="knowledge.register",
                target_id=row.id,
                request_id=request_id,
                details={"source_key": row.source_key, "version": row.version},
            )
        )
        db.commit()
    except IntegrityError:
        # 并发登记由数据库唯一键裁决；只复用确实相同的版本，其余约束错误继续上抛。
        db.rollback()
        if winner := existing():
            return check_same(winner)
        raise
    return row


def change_publication(
    db: Session, actor: User, document_id: UUID, *, publish: bool, request_id: str
) -> KnowledgeDocument:
    """状态变化与审计同事务；只允许责任人发布 / 撤回自己的制度版本。

    使用条件更新避免重复发布产生重复审计；撤回版本不能原地再发布，需登记新版本。
    发布前重验原件哈希及结构，不能让损坏或使用未知规则的记录变成可信来源。
    """
    require_manager(actor)
    row = db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.tenant_id == actor.tenant_id,
            KnowledgeDocument.owner_id == actor.id,
        )
    )
    if row is None:
        raise HTTPException(404, "制度不存在或无管理权限")
    expected, target = ("draft", "published") if publish else ("published", "withdrawn")
    if row.status == target:
        return row
    if row.status != expected:
        raise HTTPException(409, "当前制度状态不允许此操作；撤回后需登记新版本")
    if publish:
        normalized = normalize_document(row.original_bytes, row.format)
        if (
            sha256(row.original_bytes).hexdigest() != row.content_hash
            or normalized.model_dump() != row.normalized_document
        ):
            raise HTTPException(409, "原件或统一结构校验不一致，禁止发布")
    changed = db.execute(
        update(KnowledgeDocument)
        .where(KnowledgeDocument.id == row.id, KnowledgeDocument.status == expected)
        .values(status=target)
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "制度状态已变化，请重新读取")
    db.add(
        AuditLog(
            tenant_id=actor.tenant_id,
            actor_id=actor.id,
            action="knowledge.publish" if publish else "knowledge.withdraw",
            target_id=row.id,
            request_id=request_id,
            details={"version": row.version},
        )
    )
    db.commit()
    db.refresh(row)
    return row


def readable(actor: User):
    """列表和详情复用同一授权条件；已过适用期的已发布原文仍可查阅日期，供历史核对。

    本步尚无问答；P3A-02 再按问题适用日期筛选检索。未发布、已撤回和无权记录不可读。
    """
    return (
        KnowledgeDocument.tenant_id == actor.tenant_id,
        KnowledgeDocument.status == "published",
        or_(KnowledgeDocument.visibility == "tenant", KnowledgeDocument.owner_id == actor.id),
    )


def summary(row: KnowledgeDocument, owner_name: str) -> DocumentSummary:
    """显式裁剪公开字段，避免原始 ORM 对象泄漏内部身份、草稿状态或原件字节。"""
    return DocumentSummary(
        id=row.id,
        source_key=row.source_key,
        title=row.title,
        version=row.version,
        owner_name=owner_name,
        format=row.format,
        effective_from=row.effective_from,
        effective_until=row.effective_until,
        visibility=row.visibility,
    )


def list_documents(db: Session, actor: User, query: DocumentListQuery) -> DocumentListResponse:
    """分页列出有权查阅的已发布版本；不将多个版本静默合并为一个最新文档。"""
    total = db.scalar(select(func.count()).select_from(KnowledgeDocument).where(*readable(actor)))
    rows = db.execute(
        select(KnowledgeDocument, User.name)
        .join(User, User.id == KnowledgeDocument.owner_id)
        .where(*readable(actor))
        .order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id)
        .offset(query.offset)
        .limit(query.limit)
    )
    return DocumentListResponse(
        items=[summary(row, name) for row, name in rows],
        total=total or 0,
        limit=query.limit,
        offset=query.offset,
    )


def get_document(db: Session, actor: User, document_id: UUID) -> DocumentDetail:
    """每次打开原文重新鉴权；未知、私有和撤回统一返回 404，避免探测受限来源。"""
    result = db.execute(
        select(KnowledgeDocument, User.name)
        .join(User, User.id == KnowledgeDocument.owner_id)
        .where(KnowledgeDocument.id == document_id, *readable(actor))
    ).first()
    if result is None:
        raise HTTPException(404, "制度不存在或当前不可访问")
    row, owner_name = result
    return DocumentDetail(
        **summary(row, owner_name).model_dump(),
        original_text=decode_original(row.original_bytes),
        content_hash=row.content_hash,
        normalized_document=row.normalized_document,
    )
