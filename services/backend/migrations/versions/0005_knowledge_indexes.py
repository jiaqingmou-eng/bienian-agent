"""新增完整制度检索快照；原文与已发布迁移均保持不变。"""

import sqlalchemy as sa
from alembic import op

revision = "0005_knowledge_indexes"
down_revision = "0004_knowledge_documents"
branch_labels = None
depends_on = None


def upgrade():
    """每个文档版本只允许一份完整快照，未提交的片段不能被检索。"""
    op.create_table(
        "knowledge_indexes",
        sa.Column(
            "document_id", sa.Uuid(), sa.ForeignKey("knowledge_documents.id"), primary_key=True
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("rule_version", sa.String(40), nullable=False),
        sa.Column("units", sa.JSON(), nullable=False),
        sa.Column("unit_count", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "unit_count > 0 AND chunk_count >= unit_count", name="ck_knowledge_index_count"
        ),
    )


def downgrade():
    """仅删除派生索引，保留原件、统一结构和制度发布记录。"""
    op.drop_table("knowledge_indexes")
