"""Initial, immutable finance schema. Future changes require a new revision."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.UniqueConstraint("tenant_id", "id", name="uq_user_tenant_id"),
        sa.CheckConstraint("role IN ('employee', 'finance')", name="ck_user_role"),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])
    op.create_table(
        "expenses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("applicant_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("purpose", sa.String(1000), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("total_fen", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(36), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "applicant_id"], ["users.tenant_id", "users.id"]),
        sa.UniqueConstraint("tenant_id", "id", name="uq_expense_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "applicant_id", "idempotency_key", name="uq_expense_request"
        ),
        sa.CheckConstraint("total_fen > 0 AND total_fen <= 100000000", name="ck_expense_total"),
        sa.CheckConstraint("currency = 'CNY'", name="ck_expense_currency"),
        sa.CheckConstraint("version >= 1", name="ck_expense_version"),
        sa.CheckConstraint(
            "status IN ('draft', 'pending', 'approved', 'rejected')", name="ck_expense_status"
        ),
    )
    op.create_index(
        "ix_expense_owner_created", "expenses", ["tenant_id", "applicant_id", "created_at"]
    )
    op.create_table(
        "expense_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "expense_id",
            sa.Uuid(),
            sa.ForeignKey("expenses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("incurred_on", sa.Date(), nullable=False),
        sa.Column("description", sa.String(200), nullable=False),
        sa.Column("amount_fen", sa.Integer(), nullable=False),
        sa.UniqueConstraint("expense_id", "position", name="uq_item_position"),
        sa.CheckConstraint("amount_fen > 0 AND amount_fen <= 100000000", name="ck_item_amount"),
        sa.CheckConstraint(
            "category IN ('transport', 'lodging', 'meal', 'other')", name="ck_item_category"
        ),
    )
    op.create_table(
        "approvals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("expense_id", sa.Uuid(), nullable=False),
        sa.Column("expense_version", sa.Integer(), nullable=False),
        sa.Column("approver_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("comment", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "expense_id"], ["expenses.tenant_id", "expenses.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "approver_id"], ["users.tenant_id", "users.id"]),
        sa.UniqueConstraint("expense_id", "expense_version", name="uq_approval_expense_version"),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')", name="ck_approval_status"
        ),
        sa.CheckConstraint("expense_version >= 1", name="ck_approval_version"),
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "actor_id"], ["users.tenant_id", "users.id"]),
    )
    op.create_index("ix_audit_logs_tenant_id", "audit_logs", ["tenant_id"])


def downgrade() -> None:
    for table in ["audit_logs", "approvals", "expense_items", "expenses", "users", "tenants"]:
        op.drop_table(table)
