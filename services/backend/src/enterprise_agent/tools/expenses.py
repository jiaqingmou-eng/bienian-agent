"""P1 唯一写工具，复用财务授权、幂等、金额和事务边界。"""

from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from enterprise_agent.api.schemas import APIModel, ExpenseResponse
from enterprise_agent.db.models import User
from enterprise_agent.finance.schemas import CreateExpenseInput
from enterprise_agent.finance.service import create_expense

TOOL_NAME = "create_expense_draft"
TOOL_VERSION = "1"


class DraftToolResult(APIModel):
    outcome: Literal["success"] = "success"
    schema_version: Literal["1"] = "1"
    provenance: Literal["finance_service"] = "finance_service"
    operation_id: UUID
    expense_id: UUID
    total_fen: int


def create_expense_draft(
    db: Session, actor: User, payload: dict, operation_id: UUID, request_id: str
) -> DraftToolResult:
    """只接受服务端门禁取得的身份与幂等键；不对模型开放身份字段。"""
    command = CreateExpenseInput.model_validate(payload)
    expense = create_expense(db, actor, command, operation_id, request_id)
    # 结果先按公开业务 schema 校验，再只向编排层传递所需字段。
    public = ExpenseResponse.model_validate(expense)
    return DraftToolResult(
        operation_id=operation_id, expense_id=public.id, total_fen=public.total_fen
    )
