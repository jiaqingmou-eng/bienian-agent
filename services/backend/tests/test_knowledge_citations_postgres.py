"""真实PostgreSQL：固定引用跨连接读取、只读事务与撤回后的重新授权。"""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from enterprise_agent.db.models import User
from enterprise_agent.db.seed import DEMO_ACCOUNTS, seed
from enterprise_agent.knowledge.citations import resolve_citation
from enterprise_agent.knowledge.retrieval import build_index
from enterprise_agent.knowledge.schemas import RegisterDocument
from enterprise_agent.knowledge.service import change_publication, register_document


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="TEST_POSTGRES_URL not configured")
def test_persisted_citation_in_read_only_transaction_and_withdrawal():
    """仅创建随机隔离schema；不迁移开发业务库，不依赖进程内索引或旧Session授权结果。"""
    schema = f"test_{uuid4().hex}"
    engine = create_engine(
        os.environ["TEST_POSTGRES_URL"],
        hide_parameters=True,
        connect_args={"options": f"-c search_path={schema}"},
    )
    with engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    try:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        with Session(engine) as db:
            seed(db)
            manager = db.get(User, DEMO_ACCOUNTS["finance"])
            document = register_document(
                db,
                manager,
                RegisterDocument(
                    source_key="pg-citation",
                    title="虚构引用制度",
                    version=1,
                    format="text",
                    effective_from="2026-01-01",
                    effective_until="2026-06-30",
                ),
                "第一条 成都住宿400.00元。\r\n超标部分不得自动报销。".encode(),
                str(uuid4()),
            )
            change_publication(db, manager, document.id, publish=True, request_id=str(uuid4()))
            index = build_index(db, manager, document.id, str(uuid4()))
            document_id, citation_id = document.id, index.units[0]["id"]
        with Session(engine) as db:
            # 数据库本身禁止该事务写入，验证解析引用不隐式建索引或追加审计。
            db.execute(text("SET TRANSACTION READ ONLY"))
            employee = db.get(User, DEMO_ACCOUNTS["employee_a"])
            result = resolve_citation(db, employee, document_id, citation_id)
            assert result.document.version == 1
            assert result.source_start_line == 1 and result.source_end_line == 2
            assert "不得自动报销" in result.text
        with Session(engine) as db:
            manager = db.get(User, DEMO_ACCOUNTS["finance"])
            change_publication(db, manager, document_id, publish=False, request_id=str(uuid4()))
        with Session(engine) as db:
            db.execute(text("SET TRANSACTION READ ONLY"))
            employee = db.get(User, DEMO_ACCOUNTS["employee_a"])
            with pytest.raises(HTTPException) as rejected:
                resolve_citation(db, employee, document_id, citation_id)
            assert rejected.value.status_code == 404
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()
