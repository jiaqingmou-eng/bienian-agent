"""可选 PostgreSQL 集成测试；仅使用随机隔离 schema，不触碰其他表。"""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from enterprise_agent.db.models import Base, Expense, User
from enterprise_agent.db.seed import DEMO_ACCOUNTS, seed
from enterprise_agent.finance.schemas import CreateExpenseInput
from enterprise_agent.finance.service import create_expense, get_expense


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="TEST_POSTGRES_URL not configured")
def test_postgres_migration_rules_and_idempotency(payload):
    """验证 PostgreSQL 迁移与 SQLite 难以模拟的真实数据库约束。"""
    engine = create_engine(os.environ["TEST_POSTGRES_URL"], hide_parameters=True)
    schema = f"test_{uuid4().hex}"
    with engine.begin() as admin:
        admin.execute(CreateSchema(schema))
    try:
        with engine.connect() as connection:
            # schema 名来自本地 UUID，不使用外部输入拼接 SQL 标识符。
            connection.exec_driver_sql(f'SET search_path TO "{schema}"')
            connection.commit()
            config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            context = MigrationContext.configure(connection)
            assert compare_metadata(context, Base.metadata) == []
            connection.commit()
            with Session(connection) as db:
                seed(db)
                actor = db.get(User, DEMO_ACCOUNTS["employee_a"])
                other = db.get(User, DEMO_ACCOUNTS["employee_b"])
                assert actor is not None and other is not None
                key = uuid4()
                expense = create_expense(
                    db, actor, CreateExpenseInput(**payload), key, str(uuid4())
                )
                retry = create_expense(db, actor, CreateExpenseInput(**payload), key, str(uuid4()))
                assert expense.id == retry.id
                assert expense.total_fen == 303
                assert db.scalar(select(Expense).where(Expense.id == expense.id)) is not None
                with pytest.raises(HTTPException) as denied:
                    get_expense(db, other, expense.id)
                assert denied.value.status_code == 404
    finally:
        # 只删除本测试创建的随机 schema，保留测试数据库内的其他内容。
        with engine.begin() as admin:
            admin.execute(DropSchema(schema, cascade=True))
        engine.dispose()
