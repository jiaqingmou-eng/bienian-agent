"""创建 SQLAlchemy Engine，并为每个 FastAPI 请求提供独立数据库会话。"""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from enterprise_agent.config import get_settings


@lru_cache
def get_engine() -> Engine:
    """按需创建并缓存连接池；调用此函数本身不会立即建立数据库连接。"""
    return create_engine(get_settings().database_url, pool_pre_ping=True, hide_parameters=True)


def get_db() -> Iterator[Session]:
    """通过生成器依赖打开请求级 Session；请求完成后关闭并归还连接。"""
    with Session(get_engine()) as session:
        yield session
