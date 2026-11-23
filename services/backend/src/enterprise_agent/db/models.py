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
