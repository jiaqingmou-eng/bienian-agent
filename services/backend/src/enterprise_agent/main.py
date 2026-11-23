"""FastAPI 应用装配、请求追踪及统一异常响应。"""

import logging
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from enterprise_agent.api.routes import router
from enterprise_agent.api.schemas import ErrorResponse, ValidationIssue
from enterprise_agent.config import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """服务启动时先校验密钥和连接配置，再开始接受请求。"""
    get_settings()  # 配置错误在启动时失败，避免运行一段时间后才暴露。
    yield


app = FastAPI(title="Enterprise Finance API", version="0.1.0", lifespan=lifespan)
app.include_router(router)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """沿用有效的请求 UUID，否则生成新值并写入请求状态和响应头。"""
    try:
        request_id = str(UUID(request.headers.get("x-request-id", "")))
    except ValueError:
        request_id = str(uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    response.headers["Cache-Control"] = "no-store"
    return response


def error(
    request: Request, status: int, message: str, details: list[ValidationIssue] | None = None
) -> JSONResponse:
    """用统一的 Pydantic 错误 schema 序列化异常，不泄漏请求正文和密钥。"""
    request_id = getattr(request.state, "request_id", "unknown")
    payload = ErrorResponse(
        code=f"HTTP_{status}",
        message=message,
        requestId=request_id,
        details=details or [],
    )
    return JSONResponse(
        status_code=status,
        headers={"X-Request-Id": request_id, "Cache-Control": "no-store"},
        content=payload.model_dump(mode="json"),
    )


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: HTTPException):
    """统一转换业务主动抛出的 HTTP 异常。"""
    return error(request, exc.status_code, str(exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """将 Pydantic/FastAPI 参数错误映射为稳定的错误位置和消息。"""
    # 不把原始输入值放进响应，避免回显金额说明、身份令牌等敏感数据。
    details = [
        ValidationIssue(path=list(issue["loc"]), message=issue["msg"]) for issue in exc.errors()
    ]
    return error(request, 422, "请求参数不符合要求", details)


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, _exc: SQLAlchemyError):
    """数据库异常只写请求编号到服务日志，对客户端返回通用信息。"""
    logger.error("Database failure requestId=%s", request.state.request_id)
    return error(request, 503, "数据服务暂不可用，请稍后重试")


@app.exception_handler(Exception)
async def unexpected_error(request: Request, _exc: Exception):
    """兜底处理未预期异常；细节留在受控日志，不返回内部堆栈。"""
    logger.error(
        "Unexpected failure requestId=%s",
        getattr(request.state, "request_id", "unknown"),
    )
    return error(request, 500, "服务暂不可用，请提供请求编号联系维护人员")
