"""新增制度版本存储；不修改已有财务表和 Agent 执行表。"""

import sqlalchemy as sa
from alembic import op

revision = "0004_knowledge_documents"
down_revision = "0003_agent_executions"
branch_labels = None
depends_on = None


def upgrade():
    """原件、结构化正文和发布状态共同落库，适用于 PostgreSQL 与 SQLite 测试。"""
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("source_key", sa.String(80), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_until", sa.Date(), nullable=True),
        sa.Column("visibility", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("original_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("normalized_document", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "owner_id"], ["users.tenant_id", "users.id"]),
        sa.UniqueConstraint(
            "tenant_id", "source_key", "version", name="uq_knowledge_source_version"
        ),
        sa.CheckConstraint("version >= 1", name="ck_knowledge_version"),
        sa.CheckConstraint("format IN ('markdown', 'text')", name="ck_knowledge_format"),
        sa.CheckConstraint("visibility IN ('tenant', 'owner')", name="ck_knowledge_visibility"),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'withdrawn')", name="ck_knowledge_status"
        ),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_until >= effective_from",
            name="ck_knowledge_dates",
        ),
    )
    op.create_index(
        "ix_knowledge_access", "knowledge_documents", ["tenant_id", "status", "created_at"]
    )


def downgrade():
    """只删除本次新增的制度版本表，不触碰其他业务数据。"""
    op.drop_index("ix_knowledge_access", table_name="knowledge_documents")
    op.drop_table("knowledge_documents")
