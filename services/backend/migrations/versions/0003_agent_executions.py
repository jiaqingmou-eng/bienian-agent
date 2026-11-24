"""为每条新接收消息增加持久执行记录，并在数据库层约束任务归属与结果引用。"""

import sqlalchemy as sa
from alembic import op

revision = "0003_agent_executions"
down_revision = "0002_agent_runs"
branch_labels = None
depends_on = None


def upgrade():
    """只增加执行元数据；历史已完成消息不补造执行时间、状态或次数。"""
    # batch 模式同时支持 PostgreSQL 和测试使用的 SQLite；旧任务数据由 Alembic 保留。
    with op.batch_alter_table("agent_runs") as batch:
        batch.create_unique_constraint("uq_agent_run_scope", ["tenant_id", "owner_id", "id"])
    op.create_table(
        "agent_executions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("accepted_version", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_version", sa.Integer(), nullable=True),
        sa.Column("result_message_id", sa.Uuid(), nullable=True),
        sa.Column("error_code", sa.String(32), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "owner_id", "run_id"],
            ["agent_runs.tenant_id", "agent_runs.owner_id", "agent_runs.id"],
        ),
        sa.UniqueConstraint("run_id", "message_id", name="uq_agent_execution_message"),
        sa.CheckConstraint("accepted_version >= 0", name="ck_agent_execution_accepted_version"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_agent_execution_attempt_count"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_agent_execution_status",
        ),
        sa.CheckConstraint(
            "(status = 'succeeded' AND result_version > accepted_version "
            "AND result_version IS NOT NULL AND result_message_id IS NOT NULL "
            "AND finished_at IS NOT NULL) OR "
            "(status <> 'succeeded' AND result_version IS NULL AND result_message_id IS NULL)",
            name="ck_agent_execution_result",
        ),
    )
    op.create_index(
        "ix_agent_execution_run",
        "agent_executions",
        ["tenant_id", "owner_id", "run_id", "created_at"],
    )


def downgrade():
    """回退只删除本 revision 引入的元数据，不删除原任务或财务记录。"""
    op.drop_index("ix_agent_execution_run", table_name="agent_executions")
    op.drop_table("agent_executions")
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_constraint("uq_agent_run_scope", type_="unique")
