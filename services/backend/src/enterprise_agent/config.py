"""集中读取和校验运行配置，避免业务模块直接访问 os.environ。"""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 从当前文件位置推导仓库根目录，不依赖启动命令所在的工作目录。
ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    """应用配置；系统环境变量优先于仓库根目录中的 .env 文件。"""

    model_config = SettingsConfigDict(
        env_file=(str(ROOT / ".env"), ".env"), extra="ignore", case_sensitive=False
    )

    database_url: str
    internal_jwt_secret: SecretStr
    enable_demo_login: bool = False
    node_env: str = "development"
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000

    @field_validator("internal_jwt_secret")
    @classmethod
    def strong_internal_secret(cls, value: SecretStr) -> SecretStr:
        """拒绝占位或过短的签名密钥，避免服务以明显不安全的配置启动。"""
        secret = value.get_secret_value()
        if len(secret) < 32 or secret.startswith("replace-"):
            raise ValueError("INTERNAL_JWT_SECRET must be a generated secret of at least 32 chars")
        return value

    @model_validator(mode="after")
    def demo_is_local_only(self) -> "Settings":
        """生产环境不允许启用无需密码的演示账号切换。"""
        if self.node_env == "production" and self.enable_demo_login:
            raise ValueError("Demo authentication is disabled in production")
        return self


@lru_cache
def get_settings() -> Settings:
    """缓存校验后的设置对象，避免每个依赖调用重复解析环境配置。"""
    return Settings()
