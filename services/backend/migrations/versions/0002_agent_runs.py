"""Persist P1 conversations, versioned confirmations and business result references."""

import sqlalchemy as sa
from alembic import op

revision = "0002_agent_runs"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("client_request_id", sa.Uuid(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("anchor_date", sa.String(10), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("candidate", sa.JSON(), nullable=False),
        sa.Column("messages", sa.JSON(), nullable=False),
        sa.Column("missing_fields", sa.JSON(), nullable=False),
        sa.Column("preview_payload", sa.JSON(), nullable=True),
        sa.Column("confirmation_id", sa.Uuid(), nullable=True),
        sa.Column("preview_version", sa.Integer(), nullable=True),
        sa.Column("preview_hash", sa.String(64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expense_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "owner_id"], ["users.tenant_id", "users.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "expense_id"], ["expenses.tenant_id", "expenses.id"]),
        sa.UniqueConstraint(
            "tenant_id", "owner_id", "client_request_id", name="uq_agent_run_request"
        ),
        sa.CheckConstraint("version >= 0", name="ck_agent_run_version"),
        sa.CheckConstraint(
            "status IN ('collecting', 'awaiting_confirmation', 'executing', "
            "'completed', 'cancelled')",
            name="ck_agent_run_status",
        ),
    )
    op.create_index("ix_agent_run_owner", "agent_runs", ["tenant_id", "owner_id", "created_at"])


def downgrade():
    op.drop_index("ix_agent_run_owner", table_name="agent_runs")
    op.drop_table("agent_runs")
