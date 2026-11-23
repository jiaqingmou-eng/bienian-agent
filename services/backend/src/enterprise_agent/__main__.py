"""Python 服务启动入口：读取配置并启动 ASGI Web 服务器。"""

import uvicorn

from enterprise_agent.config import get_settings

# 启动前读取并校验配置；数据库连接在第一次数据库操作时按需建立。
settings = get_settings()
uvicorn.run(
    "enterprise_agent.main:app",
    host=settings.backend_host,
    port=settings.backend_port,
    reload=False,
)
