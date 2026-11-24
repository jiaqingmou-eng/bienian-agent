"""财务数据库 ORM 模型；金额使用整数人民币分，避免浮点误差。

这些类映射到数据库表；真正的表结构由 Alembic 迁移创建和升级。
"""

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    """生成带 UTC 时区的时间，保持跨地区服务的时间记录一致。"""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """所有 ORM 表模型共享的 SQLAlchemy 声明式基类。"""


class Tenant(Base):
    """企业租户表；用户和业务记录均通过 tenant_id 隔离。"""

    __tablename__ = "tenants"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(100))


class User(Base):
    """员工与财务账号表；role 是服务端从数据库读取的权限依据。"""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_user_tenant_id"),
        CheckConstraint("role IN ('employee', 'finance')", name="ck_user_role"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20))


class Expense(Base):
    """一张报销申请主单；通过 items 关系包含多条费用明细。"""

    __tablename__ = "expenses"
    __table_args__ = (
        # 复合外键保证申请人属于同一家企业，防止跨租户引用用户。
        ForeignKeyConstraint(["tenant_id", "applicant_id"], ["users.tenant_id", "users.id"]),
        CheckConstraint("total_fen > 0 AND total_fen <= 100000000", name="ck_expense_total"),
        CheckConstraint("currency = 'CNY'", name="ck_expense_currency"),
        CheckConstraint("version >= 1", name="ck_expense_version"),
        CheckConstraint(
            "status IN ('draft', 'pending', 'approved', 'rejected')", name="ck_expense_status"
        ),
        # 同一企业、同一申请人的重试键必须唯一，防止重复生成报销单。
        UniqueConstraint("tenant_id", "applicant_id", "idempotency_key", name="uq_expense_request"),
        UniqueConstraint("tenant_id", "id", name="uq_expense_tenant_id"),
        Index("ix_expense_owner_created", "tenant_id", "applicant_id", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    applicant_id: Mapped[UUID] = mapped_column(Uuid)
    title: Mapped[str] = mapped_column(String(100))
    purpose: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    total_fen: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)
    idempotency_key: Mapped[str] = mapped_column(String(36))
    request_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    # cascade 确保新增主单时明细一并保存，删除主单时其明细也随之清理。
    items: Mapped[list["ExpenseItem"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="ExpenseItem.position"
    )


class ExpenseItem(Base):
    """报销明细表；每行属于一张主单，金额单位为人民币分。"""

    __tablename__ = "expense_items"
    __table_args__ = (
        CheckConstraint("amount_fen > 0 AND amount_fen <= 100000000", name="ck_item_amount"),
        CheckConstraint(
            "category IN ('transport', 'lodging', 'meal', 'other')", name="ck_item_category"
        ),
        UniqueConstraint("expense_id", "position", name="uq_item_position"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    expense_id: Mapped[UUID] = mapped_column(ForeignKey("expenses.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(20))
    incurred_on: Mapped[date]
    description: Mapped[str] = mapped_column(String(200))
    amount_fen: Mapped[int] = mapped_column(Integer)


class Approval(Base):
    """审批记录；关联具体申请版本，后续修改申请时可要求重新审批。"""

    __tablename__ = "approvals"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "expense_id"], ["expenses.tenant_id", "expenses.id"]),
        ForeignKeyConstraint(["tenant_id", "approver_id"], ["users.tenant_id", "users.id"]),
        UniqueConstraint("expense_id", "expense_version", name="uq_approval_expense_version"),
        CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="ck_approval_status"),
        CheckConstraint("expense_version >= 1", name="ck_approval_version"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    expense_id: Mapped[UUID] = mapped_column(Uuid)
    expense_version: Mapped[int] = mapped_column(Integer)
    approver_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    comment: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuditLog(Base):
    """关键财务操作审计表；与对应业务变更放在同一个事务内提交。"""

    __tablename__ = "audit_logs"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "actor_id"], ["users.tenant_id", "users.id"]),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    actor_id: Mapped[UUID] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(60))
    target_id: Mapped[UUID] = mapped_column(Uuid)
    request_id: Mapped[str] = mapped_column(String(36))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AgentRun(Base):
    """P1 持久任务快照；确认与业务结果可对账，不等同于 LangGraph checkpoint。"""

    __tablename__ = "agent_runs"
    __table_args__ = (
        # 执行记录通过复合外键绑定任务及其所有者，防止在数据库层关联到其他租户的任务。
        UniqueConstraint("tenant_id", "owner_id", "id", name="uq_agent_run_scope"),
        ForeignKeyConstraint(["tenant_id", "owner_id"], ["users.tenant_id", "users.id"]),
        ForeignKeyConstraint(["tenant_id", "expense_id"], ["expenses.tenant_id", "expenses.id"]),
        UniqueConstraint("tenant_id", "owner_id", "client_request_id", name="uq_agent_run_request"),
        CheckConstraint("version >= 0", name="ck_agent_run_version"),
        CheckConstraint(
            "status IN ('collecting', 'awaiting_confirmation', 'executing', "
            "'completed', 'cancelled')",
            name="ck_agent_run_status",
        ),
        Index("ix_agent_run_owner", "tenant_id", "owner_id", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    owner_id: Mapped[UUID] = mapped_column(Uuid)
    client_request_id: Mapped[UUID] = mapped_column(Uuid)
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="collecting")
    version: Mapped[int] = mapped_column(Integer, default=0)
    anchor_date: Mapped[str] = mapped_column(String(10))
    timezone: Mapped[str] = mapped_column(String(64))
    candidate: Mapped[dict] = mapped_column(JSON, default=dict)
    messages: Mapped[list] = mapped_column(JSON, default=list)
    missing_fields: Mapped[list] = mapped_column(JSON, default=list)
    preview_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confirmation_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    preview_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    preview_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expense_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AgentExecution(Base):
    """一条用户消息的一轮计算记录；不是财务操作台账，也不是后台任务队列。

    同一消息失败后手动重试仍使用此记录，attempt_count 表示实际开始计算的次数。
    结果通过 run 版本和助手消息 ID 定位，避免再存一份对话或确认凭据。
    """

    __tablename__ = "agent_executions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "owner_id", "run_id"],
            ["agent_runs.tenant_id", "agent_runs.owner_id", "agent_runs.id"],
        ),
        UniqueConstraint("run_id", "message_id", name="uq_agent_execution_message"),
        CheckConstraint("accepted_version >= 0", name="ck_agent_execution_accepted_version"),
        CheckConstraint("attempt_count >= 0", name="ck_agent_execution_attempt_count"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_agent_execution_status",
        ),
        CheckConstraint(
            "(status = 'succeeded' AND result_version > accepted_version "
            "AND result_version IS NOT NULL AND result_message_id IS NOT NULL "
            "AND finished_at IS NOT NULL) OR "
            "(status <> 'succeeded' AND result_version IS NULL AND result_message_id IS NULL)",
            name="ck_agent_execution_result",
        ),
        Index("ix_agent_execution_run", "tenant_id", "owner_id", "run_id", "created_at"),
    )
    # 身份只由已鉴权的 run 注入，消息正文保存在 run 中，此处仅保存规范化输入的哈希。
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid)
    owner_id: Mapped[UUID] = mapped_column(Uuid)
    run_id: Mapped[UUID] = mapped_column(Uuid)
    message_id: Mapped[UUID] = mapped_column(Uuid)
    input_hash: Mapped[str] = mapped_column(String(64))
    accepted_version: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    # started_at 是最近一次手动尝试的开始时间；running 没有租约，不因经过时间而自动失败。
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_message_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    # 仅记录稳定错误类别，禁止把模型原文、异常正文或凭据写入追踪记录。
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)


class KnowledgeDocument(Base):
    """一份制度的不可变内容版本；原件与清洗结果分开保存，不充当检索片段表。

    同一企业的 source_key 标识逻辑来源，version 标识该来源的具体版本。
    管理服务只允许发布状态改变，正文、适用日期或阅读范围调整必须登记新版本。
    """

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "owner_id"], ["users.tenant_id", "users.id"]),
        UniqueConstraint("tenant_id", "source_key", "version", name="uq_knowledge_source_version"),
        CheckConstraint("version >= 1", name="ck_knowledge_version"),
        CheckConstraint("format IN ('markdown', 'text')", name="ck_knowledge_format"),
        CheckConstraint("visibility IN ('tenant', 'owner')", name="ck_knowledge_visibility"),
        CheckConstraint(
            "status IN ('draft', 'published', 'withdrawn')", name="ck_knowledge_status"
        ),
        CheckConstraint(
            "effective_until IS NULL OR effective_until >= effective_from",
            name="ck_knowledge_dates",
        ),
        Index("ix_knowledge_access", "tenant_id", "status", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    owner_id: Mapped[UUID] = mapped_column(Uuid)
    source_key: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer)
    format: Mapped[str] = mapped_column(String(16))
    effective_from: Mapped[date]
    effective_until: Mapped[date | None] = mapped_column(nullable=True)
    visibility: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="draft")
    # 精确保留输入字节（包括 BOM 和原换行）；哈希用于识别同版本内容冲突。
    original_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    content_hash: Mapped[str] = mapped_column(String(64))
    # JSON 保存经确定性校验的章节结构及原文行映射，P3A-02 才生成检索 chunk。
    normalized_document: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class KnowledgeIndex(Base):
    """单个制度版本的完整检索快照；只有一次性提交成功的快照才对查询可见。

    不重复存租户 / 所有者，所有读取必须关联原文表检查当前权限与发布状态。
    units 同时保存完整条款、子片段及词项，适用于当前受控小语料，不是向量索引。
    """

    __tablename__ = "knowledge_indexes"
    __table_args__ = (
        CheckConstraint(
            "unit_count > 0 AND chunk_count >= unit_count", name="ck_knowledge_index_count"
        ),
    )
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_documents.id"), primary_key=True
    )
    content_hash: Mapped[str] = mapped_column(String(64))
    rule_version: Mapped[str] = mapped_column(String(40))
    units: Mapped[list] = mapped_column(JSON)
    unit_count: Mapped[int] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
