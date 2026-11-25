"""统一对话入口的协议；咨询响应与报销任务分开，避免只读问题覆盖待确认预览。"""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from enterprise_agent.agent.schemas import MessageText, RunResponse, Version
from enterprise_agent.api.schemas import APIModel
from enterprise_agent.knowledge.schemas import SearchHit


class ChatRequest(APIModel):
    """消息标识沿用报销去重语义；只有关联已有报销任务时才同时提交版本。"""

    message_id: UUID
    message: MessageText
    run_id: UUID | None = None
    expected_version: Version | None = None

    @model_validator(mode="after")
    def paired_task(self):
        """禁止只传版本或只传任务；政策咨询不据此修改任务，报销分支仍独立校验版本。"""
        if (self.run_id is None) != (self.expected_version is None):
            raise ValueError("run_id 和 expected_version 必须同时提供或同时省略")
        return self


class IntentDecision(APIModel):
    """模型只能选择固定能力及查询参数，不能输出任意工具名、权限或数据库操作。"""

    intent: Literal["expense", "policy", "mixed", "clarify", "unsupported"]
    query: str = Field(default="", max_length=300)
    as_of: date | None = None


class EvidenceQuote(APIModel):
    """要求模型提供可在本次条款中逐字找到的原句，不能只返回一个看似合法的引用 ID。"""

    citation_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    quote: str = Field(min_length=1, max_length=2000)


class PolicyGeneration(APIModel):
    """回答结构先做 schema 校验，再由服务端核验引用和证据原句。"""

    status: Literal["answered", "insufficient", "conflict", "needs_clarification"]
    answer: str = Field(min_length=1, max_length=2000)
    citations: list[EvidenceQuote] = Field(default_factory=list, max_length=5)


class ChatResponse(APIModel):
    """统一外壳：expense 才携带任务；政策回答只携带本次核验过的来源。"""

    kind: Literal["expense", "policy", "clarification"]
    status: Literal[
        "expense", "answered", "insufficient", "conflict", "needs_clarification", "unsupported"
    ]
    message: str = Field(min_length=1, max_length=2000)
    as_of: date | None = None
    sources: list[SearchHit] = Field(default_factory=list, max_length=5)
    run: RunResponse | None = None
