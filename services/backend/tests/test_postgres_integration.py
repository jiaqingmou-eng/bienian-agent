"""可选 PostgreSQL 集成测试；仅使用随机隔离 schema，不触碰其他表。"""

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
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


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="TEST_POSTGRES_URL not configured")
def test_agent_parallel_confirmation_creates_one_expense():
    """两个真实 PostgreSQL Session 同时确认，CAS 与财务唯一键共同避免重复业务写入。"""
    from enterprise_agent.agent.schemas import (
        ConfirmRunRequest,
        Extraction,
        RunListQuery,
        StartRunRequest,
    )
    from enterprise_agent.agent.service import confirm_run, list_runs, load_run, response, start_run
    from enterprise_agent.db.models import AgentRun, AuditLog

    schema = f"test_{uuid4().hex}"
    engine = create_engine(
        os.environ["TEST_POSTGRES_URL"],
        hide_parameters=True,
        connect_args={"options": f"-c search_path={schema}"},
    )

    class FixedExtractor:
        def extract(self, _context):
            return Extraction.model_validate(
                {
                    "intent": "draft",
                    "candidate": {
                        "title": "并发确认测试",
                        "purpose": "虚构客户拜访",
                        "items": [
                            {
                                "category": "transport",
                                "incurred_on": "2026-11-22",
                                "description": "虚构高铁",
                                "amount_yuan": "286.00",
                            }
                        ],
                    },
                }
            )

    with engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    try:
        with engine.begin() as connection:
            config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        with Session(engine) as db:
            seed(db)
            actor = db.get(User, DEMO_ACCOUNTS["employee_a"])
            run = start_run(
                db,
                actor,
                StartRunRequest(client_request_id=uuid4(), message="虚构高铁286元"),
                FixedExtractor(),
                str(uuid4()),
            )
            confirm = ConfirmRunRequest(
                confirmation_id=run.preview.confirmation_id,
                expected_version=run.preview.version,
                content_hash=run.preview.content_hash,
            )
            expense_count = db.scalar(select(func.count()).select_from(Expense))
            audit_count = db.scalar(select(func.count()).select_from(AuditLog))
        # 完全关闭原 Session 后重新发现并读取任务，验证恢复来自 PostgreSQL 而非进程状态。
        with Session(engine) as db:
            actor = db.get(User, DEMO_ACCOUNTS["employee_a"])
            other = db.get(User, DEMO_ACCOUNTS["employee_b"])
            history = list_runs(db, actor, RunListQuery())
            assert history.total == 1 and history.items[0].id == run.id
            assert list_runs(db, other, RunListQuery()).total == 0
            assert response(load_run(db, actor, run.id)).model_dump() == run.model_dump()
            second = start_run(
                db,
                actor,
                StartRunRequest(client_request_id=uuid4(), message="另一项虚构费用"),
                FixedExtractor(),
                str(uuid4()),
            )
            assert second.id != run.id
            assert list_runs(db, actor, RunListQuery(limit=1)).total == 2
        barrier = Barrier(2)

        def worker():
            with Session(engine) as db:
                actor = db.get(User, DEMO_ACCOUNTS["employee_a"])
                barrier.wait(timeout=10)
                try:
                    return confirm_run(db, actor, run.id, confirm, str(uuid4()))
                except HTTPException as exc:
                    # 竞争初始 CAS 的请求可能得到 409；原确认可以安全重试。
                    assert exc.status_code == 409
                    db.rollback()
                    return confirm_run(db, actor, run.id, confirm, str(uuid4()))

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        assert all(result.status == "completed" for result in results)
        assert results[0].expense_id == results[1].expense_id
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(Expense)) == expense_count + 1
            assert db.scalar(select(func.count()).select_from(AuditLog)) == audit_count + 1
            assert db.get(AgentRun, run.id).status == "completed"
            actor = db.get(User, DEMO_ACCOUNTS["employee_a"])
            history = list_runs(db, actor, RunListQuery())
            assert {item.id: item.status for item in history.items} == {
                run.id: "completed",
                second.id: "awaiting_confirmation",
            }
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()
