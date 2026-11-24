"""真实 PostgreSQL 验证索引迁移、完整快照与并发重复构建；只操作随机 schema。"""

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
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from enterprise_agent.db.models import AuditLog, Base, KnowledgeDocument, KnowledgeIndex, User
from enterprise_agent.db.seed import DEMO_ACCOUNTS, seed
from enterprise_agent.knowledge.retrieval import build_index, search_documents
from enterprise_agent.knowledge.schemas import RegisterDocument, SearchDocumentsRequest
from enterprise_agent.knowledge.service import change_publication, register_document


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="TEST_POSTGRES_URL not configured")
def test_index_migration_parallel_build_and_restart_search():
    """带已有原文升级，不自动改写来源；两次并发构建最终只保留一个完整索引及审计。"""
    schema = f"test_{uuid4().hex}"
    engine = create_engine(
        os.environ["TEST_POSTGRES_URL"],
        hide_parameters=True,
        connect_args={"options": f"-c search_path={schema}"},
    )
    with engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0004_knowledge_documents")
        with Session(engine) as db:
            seed(db)
            manager = db.get(User, DEMO_ACCOUNTS["finance"])
            doc = register_document(
                db,
                manager,
                RegisterDocument(
                    source_key="pg-search",
                    title="虚构检索制度",
                    version=1,
                    format="text",
                    effective_from="2026-01-01",
                ),
                "第一条 成都住宿每晚不超过 400.00 元。\n\n超标部分不得自动报销。".encode(),
                str(uuid4()),
            )
            change_publication(db, manager, doc.id, publish=True, request_id=str(uuid4()))
            doc_id = doc.id
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        barrier = Barrier(2)

        def worker():
            """每个执行者独立连接，不共享 Session 或内存索引。"""
            with Session(engine) as db:
                actor = db.get(User, DEMO_ACCOUNTS["finance"])
                barrier.wait(timeout=10)
                return build_index(db, actor, doc_id, str(uuid4())).units

        with ThreadPoolExecutor(max_workers=2) as pool:
            snapshots = list(pool.map(lambda _: worker(), range(2)))
        assert snapshots[0] == snapshots[1]
        with Session(engine) as db:
            employee = db.get(User, DEMO_ACCOUNTS["employee_a"])
            result = search_documents(
                db,
                employee,
                SearchDocumentsRequest(
                    query="成都住宿标准",
                    as_of="2026-11-24",
                ),
            )
            assert result.items[0].document.id == doc_id
            assert "超标部分不得自动报销" in result.items[0].text
            assert db.scalar(select(func.count()).select_from(KnowledgeIndex)) == 1
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(
                        AuditLog.action == "knowledge.index",
                    )
                )
                == 1
            )
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0004_knowledge_documents")
            command.upgrade(config, "head")
        with Session(engine) as db:
            assert db.get(KnowledgeDocument, doc_id).status == "published"
            assert db.scalar(select(func.count()).select_from(KnowledgeIndex)) == 0
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()
