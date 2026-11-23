"""财务服务内部使用的数据模型和金额约束，不负责 HTTP 响应格式。"""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints

# 演示系统上限为100万元；这是安全边界，不代替企业报销政策额度。
MAX_TOTAL_FEN = 100_000_000
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Purpose = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class ExpenseItemInput(BaseModel):
    """进入财务服务的一条费用数据；StrictInt 防止小数、字符串和布尔值混入金额。"""

    model_config = ConfigDict(extra="forbid")
    category: Literal["transport", "lodging", "meal", "other"]
    incurred_on: date
    description: Description
    amount_fen: Annotated[StrictInt, Field(gt=0, le=MAX_TOTAL_FEN)]


class CreateExpenseInput(BaseModel):
    """创建报销草稿的内部命令；申请人和总额由财务服务从可信上下文生成。"""

    model_config = ConfigDict(extra="forbid")
    title: Title
    purpose: Purpose
    items: Annotated[list[ExpenseItemInput], Field(min_length=1, max_length=50)]
