"""使用随机隔离 schema 验证制度迁移、字节保真、数据库约束及并发登记。"""

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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from enterprise_agent.db.models import AuditLog, Base, KnowledgeDocument, User
from enterprise_agent.db.seed import DEMO_ACCOUNTS, seed
from enterprise_agent.knowledge.schemas import RegisterDocument
from enterprise_agent.knowledge.service import change_publication, get_document, register_document


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="TEST_POSTGRES_URL not configured")
def test_postgres_upgrade_concurrency_bytes_and_constraints():
    """只在本测试 schema 执行升级 / 回退，不迁移开发业务表或修改其他 schema。"""
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
            command.upgrade(config, "0003_agent_executions")
            # 先存在旧用户 / 财务数据，再升级到新增制度表，验证增量升级不会要求清库。
        with Session(engine) as db:
            seed(db)
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        barrier = Barrier(2)
        metadata = RegisterDocument(
            source_key="pg-policy",
            title="虚构制度",
            version=1,
            format="text",
            effective_from="2026-01-01",
        )
        raw = "\ufeff第一条 不得超过 400.00 元。\r\n第二条 不得自批。\x00".encode()

        def worker():
            """独立 Session 同时登记同版本，数据库唯一键应裁定为同一原件记录。"""
            with Session(engine) as db:
                actor = db.get(User, DEMO_ACCOUNTS["finance"])
                barrier.wait(timeout=10)
                return register_document(db, actor, metadata, raw, str(uuid4())).id

        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(lambda _: worker(), range(2)))
        assert ids[0] == ids[1]
        with Session(engine) as db:
            actor = db.get(User, DEMO_ACCOUNTS["finance"])
            change_publication(db, actor, ids[0], publish=True, request_id=str(uuid4()))
        # 关闭写入 Session 后重新读取，证明原件和统一结构来自真实 PostgreSQL。
        with Session(engine) as db:
            employee = db.get(User, DEMO_ACCOUNTS["employee_a"])
            detail = get_document(db, employee, ids[0])
            row = db.get(KnowledgeDocument, ids[0])
            assert row.original_bytes == raw and detail.original_text.encode() == raw
            assert detail.normalized_document.blocks[1].source_start_line == 2
            assert db.scalar(select(func.count()).select_from(KnowledgeDocument)) == 1
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(
                        AuditLog.action == "knowledge.register",
                        AuditLog.target_id == row.id,
                    )
                )
                == 1
            )
            # 同企业 / 责任人复合外键不能引用不存在的用户，数据库也约束发布状态。
            for invalid in ["owner_id", "status"]:
                values = {
                    key: getattr(row, key)
                    for key in [
                        "tenant_id",
                        "owner_id",
                        "source_key",
                        "title",
                        "version",
                        "format",
                        "effective_from",
                        "effective_until",
                        "visibility",
                        "status",
                        "original_bytes",
                        "content_hash",
                        "normalized_document",
                    ]
                }
                values.update(source_key=f"invalid-{invalid}")
                values[invalid] = uuid4() if invalid == "owner_id" else "unknown"
                db.add(KnowledgeDocument(**values))
                with pytest.raises(IntegrityError):
                    db.commit()
                db.rollback()
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0003_agent_executions")
            command.upgrade(config, "head")
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(User)) == 3
            assert db.scalar(select(func.count()).select_from(KnowledgeDocument)) == 0
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()
