"""通过 SQLite 执行真实迁移和 HTTP 测试，快速覆盖接口与权限边界。"""

import os
from pathlib import Path
from uuid import uuid4

import jwt
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

os.environ["DATABASE_URL"] = "sqlite+pysqlite://"
os.environ["INTERNAL_JWT_SECRET"] = "test-internal-secret-only-for-tests-0123456789"
os.environ["ENABLE_DEMO_LOGIN"] = "true"
os.environ["NODE_ENV"] = "test"

from enterprise_agent.config import get_settings  # noqa: E402
from enterprise_agent.db.seed import DEMO_ACCOUNTS, seed  # noqa: E402
from enterprise_agent.db.session import get_db  # noqa: E402
from enterprise_agent.main import app  # noqa: E402


@pytest.fixture
def db():
    """对每个测试创建独立内存数据库，并应用项目正式 Alembic 迁移。"""
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def foreign_keys_on(connection, _record):
        """SQLite 默认不强制外键；显式开启后使测试更接近 PostgreSQL。"""
        connection.execute("PRAGMA foreign_keys=ON")

    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    with Session(engine) as session:
        seed(session)
        yield session
    engine.dispose()


@pytest.fixture
def client(db):
    """将 API 的数据库依赖替换为测试 Session，并在测试后恢复依赖配置。"""
    get_settings.cache_clear()
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def token(subject: str, scope: str = "user", secret: str | None = None, expired=False):
    """生成覆盖身份、演示作用域、伪造密钥和过期场景的测试 JWT。"""
    import time

    return jwt.encode(
        {
            "sub": subject,
            "scope": scope,
            "iss": "enterprise-bff",
            "aud": "enterprise-backend",
            "iat": int(time.time()) - 120 if expired else int(time.time()),
            "exp": int(time.time()) - 60 if expired else int(time.time()) + 60,
        },
        secret or os.environ["INTERNAL_JWT_SECRET"],
        algorithm="HS256",
    )


def headers(key="employee_a", idempotency_key=None):
    """构造测试请求的 Bearer 身份和默认幂等键。"""
    return {
        "Authorization": f"Bearer {token(str(DEMO_ACCOUNTS[key]))}",
        "Idempotency-Key": str(idempotency_key or uuid4()),
    }


@pytest.fixture
def payload():
    """提供整数分金额的有效创建请求，供测试按需修改。"""
    return {
        "title": "测试报销",
        "purpose": "测试整数金额与访问权限",
        "items": [
            {
                "category": "transport",
                "incurred_on": "2026-11-20",
                "description": "交通",
                "amount_fen": 101,
            },
            {
                "category": "meal",
                "incurred_on": "2026-11-20",
                "description": "餐饮",
                "amount_fen": 202,
            },
        ],
    }
