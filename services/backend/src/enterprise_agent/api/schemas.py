"""财务 HTTP API 的请求和响应模型；FastAPI 会据此校验数据并生成 OpenAPI。"""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints

from enterprise_agent.finance.schemas import MAX_TOTAL_FEN, Purpose, Title


class APIModel(BaseModel):
    """所有 API 数据模型的基类；拒绝未声明字段，避免调用方偷偷传入权限字段。"""

    model_config = ConfigDict(extra="forbid")


class HealthResponse(APIModel):
    """服务存活检查的响应结构。"""

    status: Literal["ok"]
    service: Literal["backend"]


class ReadyResponse(APIModel):
    """数据库连通性检查成功时的响应结构。"""

    status: Literal["ready"]


class DemoLoginRequest(APIModel):
    """本地演示登录请求；账号只能从预先配置的演示账号中选择。"""

    account_key: Literal["employee_a", "employee_b", "finance"]


class UserResponse(APIModel):
    """对外返回的用户资料；字段清单决定哪些用户数据可被 API 看见。"""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    tenant_id: UUID
    name: str
    role: Literal["employee", "finance"]


class DemoAccountResponse(APIModel):
    """演示登录页可显示的账号资料，不包含认证凭据。"""

    key: str
    name: str
    role: Literal["employee", "finance"]


class ExpenseItemRequest(APIModel):
    """创建报销时提交的一条费用明细。金额使用整数分，拒绝字符串和小数。"""

    category: Literal["transport", "lodging", "meal", "other"]
    incurred_on: date
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]
    amount_fen: Annotated[StrictInt, Field(gt=0, le=MAX_TOTAL_FEN)]


class CreateExpenseRequest(APIModel):
    """创建草稿请求；申请人、企业、总额和状态由服务端确定，不能由客户端指定。"""

    title: Title
    purpose: Purpose
    items: Annotated[list[ExpenseItemRequest], Field(min_length=1, max_length=50)]


class ExpenseItemResponse(APIModel):
    """返回给客户端的费用明细字段。"""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    category: Literal["transport", "lodging", "meal", "other"]
    incurred_on: date
    description: str
    amount_fen: int


class ExpenseResponse(APIModel):
    """报销申请响应；FastAPI 会按此模型过滤数据库对象中的其他字段。"""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    applicant_id: UUID
    title: str
    purpose: str
    status: Literal["draft", "pending", "approved", "rejected"]
    currency: Literal["CNY"]
    total_fen: int
    version: int
    created_at: datetime
    items: list[ExpenseItemResponse]


class ExpenseListResponse(APIModel):
    """报销列表响应，并附上实际应用的分页参数。"""

    items: list[ExpenseResponse]
    limit: int
    offset: int


class ExpenseListQuery(APIModel):
    """报销列表查询参数；Pydantic 对 URL query string 执行范围校验。"""

    limit: Annotated[int, Field(ge=1, le=100)] = 20
    offset: Annotated[int, Field(ge=0)] = 0


class ValidationIssue(APIModel):
    """参数错误的位置与说明，不回显请求中的财务数据或敏感值。"""

    path: list[str | int]
    message: str


class ErrorResponse(APIModel):
    """统一错误响应；requestId 用于将用户反馈与服务端日志关联。"""

    code: str
    message: str
    requestId: str
    details: list[ValidationIssue] = Field(default_factory=list)


# 在 OpenAPI 中为各接口声明统一错误格式，方便 NestJS 客户端生成一致协议。
COMMON_ERROR_RESPONSES = {
    status: {"model": ErrorResponse, "description": description}
    for status, description in {
        400: "请求格式错误",
        401: "身份凭据缺失、无效或过期",
        403: "无权执行该操作",
        404: "资源不存在或无权访问",
        409: "幂等键与现有请求冲突",
        422: "请求参数未通过校验",
        503: "数据库或依赖服务暂不可用",
    }.items()
}
