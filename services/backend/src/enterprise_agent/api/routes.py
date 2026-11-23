"""API 路由层：校验 HTTP 输入、声明响应 schema，并委托财务服务处理业务。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from enterprise_agent.api.schemas import (
    COMMON_ERROR_RESPONSES,
    CreateExpenseRequest,
    DemoAccountResponse,
    DemoLoginRequest,
    ExpenseListQuery,
    ExpenseListResponse,
    ExpenseResponse,
    HealthResponse,
    ReadyResponse,
    UserResponse,
)
from enterprise_agent.auth import get_actor, require_demo
from enterprise_agent.db.models import User
from enterprise_agent.db.seed import DEMO_ACCOUNTS
from enterprise_agent.db.session import get_db
from enterprise_agent.finance import service
from enterprise_agent.finance.schemas import CreateExpenseInput

router = APIRouter()

# Depends 让 FastAPI 为每个请求创建 Session，并在请求结束后关闭它。
Db = Annotated[Session, Depends(get_db)]
# Actor 由已验签的 BFF 凭据解析，用户角色和企业归属以数据库记录为准。
Actor = Annotated[User, Depends(get_actor)]


@router.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    """检查进程是否能响应请求；该接口不访问数据库。"""
    return HealthResponse(status="ok", service="backend")


@router.get(
    "/ready",
    response_model=ReadyResponse,
    responses=COMMON_ERROR_RESPONSES,
    tags=["health"],
)
def ready(db: Db) -> ReadyResponse:
    """执行轻量 SQL，供部署探针确认后端与数据库均已就绪。"""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="数据库暂不可用") from exc
    return ReadyResponse(status="ready")


@router.get(
    "/api/v1/demo/accounts",
    response_model=list[DemoAccountResponse],
    responses=COMMON_ERROR_RESPONSES,
    dependencies=[Depends(require_demo)],
)
def demo_accounts(db: Db) -> list[DemoAccountResponse]:
    """列出演示账号；返回 schema 防止数据库内部字段泄漏到客户端。"""
    return [
        DemoAccountResponse(key=key, name=user.name, role=user.role)
        for key, user_id in DEMO_ACCOUNTS.items()
        if (user := db.get(User, user_id)) is not None
    ]


@router.post(
    "/api/v1/demo/login",
    response_model=UserResponse,
    responses=COMMON_ERROR_RESPONSES,
    dependencies=[Depends(require_demo)],
)
def demo_login(payload: DemoLoginRequest, db: Db) -> User:
    """按固定账号 key 读取演示用户；调用方不能提交任意用户 ID 或角色。"""
    user = db.get(User, DEMO_ACCOUNTS[payload.account_key])
    if user is None:
        raise HTTPException(status_code=503, detail="演示数据尚未初始化，请执行数据库种子脚本")
    return user


@router.get(
    "/api/v1/me",
    response_model=UserResponse,
    responses=COMMON_ERROR_RESPONSES,
)
def me(actor: Actor) -> User:
    """返回当前已认证用户的公开资料。"""
    return actor


@router.get(
    "/api/v1/expenses",
    response_model=ExpenseListResponse,
    responses=COMMON_ERROR_RESPONSES,
)
def expenses(
    db: Db,
    actor: Actor,
    query: Annotated[ExpenseListQuery, Query()],
) -> ExpenseListResponse:
    """按当前用户权限查询报销；query schema 同时约束分页范围并记录在 OpenAPI。"""
    rows = service.list_expenses(db, actor, query.limit, query.offset)
    return ExpenseListResponse(
        items=[ExpenseResponse.model_validate(row) for row in rows],
        limit=query.limit,
        offset=query.offset,
    )


@router.get(
    "/api/v1/expenses/{expense_id}",
    response_model=ExpenseResponse,
    responses=COMMON_ERROR_RESPONSES,
)
def expense_detail(expense_id: UUID, db: Db, actor: Actor) -> ExpenseResponse:
    """FastAPI 先将路径值解析为 UUID，再由财务服务检查数据访问权限。"""
    row = service.get_expense(db, actor, expense_id)
    return ExpenseResponse.model_validate(row)


@router.post(
    "/api/v1/expenses",
    response_model=ExpenseResponse,
    status_code=201,
    responses=COMMON_ERROR_RESPONSES,
)
def expense_create(
    payload: CreateExpenseRequest,
    request: Request,
    db: Db,
    actor: Actor,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> ExpenseResponse:
    """校验请求体与幂等头，将 HTTP schema 转换为财务服务内部命令。"""
    command = CreateExpenseInput.model_validate(payload.model_dump())
    row = service.create_expense(db, actor, command, idempotency_key, request.state.request_id)
    return ExpenseResponse.model_validate(row)
