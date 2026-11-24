"""Agent HTTP 协议和模型输出协议，拒绝未知及权威字段。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints

from enterprise_agent.api.schemas import APIModel, CreateExpenseRequest

MessageText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]
Version = Annotated[int, Field(strict=True, ge=0)]
RunStatus = Literal["collecting", "awaiting_confirmation", "executing", "completed", "cancelled"]


class RunListQuery(APIModel):
    limit: Annotated[int, Field(ge=1, le=100)] = 20
    offset: Annotated[int, Field(ge=0)] = 0


class RunSummary(APIModel):
    id: UUID
    status: RunStatus
    title: Annotated[str, Field(min_length=1, max_length=100)]
    created_at: datetime


class RunListResponse(APIModel):
    items: list[RunSummary]
    total: int
    limit: int
    offset: int


class StartRunRequest(APIModel):
    client_request_id: UUID
    message: MessageText


class RunMessageRequest(APIModel):
    message_id: UUID
    expected_version: Version
    message: MessageText


class ConfirmRunRequest(APIModel):
    confirmation_id: UUID
    expected_version: Version
    content_hash: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class CancelRunRequest(APIModel):
    expected_version: Version


class CandidateItem(APIModel):
    category: Literal["transport", "lodging", "meal", "other"] | None = None
    incurred_on: Annotated[str, Field(max_length=32)] | None = None
    description: Annotated[str, Field(max_length=200)] | None = None
    amount_yuan: Annotated[str, Field(max_length=32)] | None = None


class DraftCandidate(APIModel):
    title: Annotated[str, Field(max_length=100)] | None = None
    purpose: Annotated[str, Field(max_length=1000)] | None = None
    items: Annotated[list[CandidateItem], Field(max_length=50)] = Field(default_factory=list)


class Extraction(APIModel):
    intent: Literal["draft", "cancel", "unsupported"]
    candidate: DraftCandidate


class ConversationMessage(APIModel):
    id: UUID
    role: Literal["user", "assistant"]
    content: str


class DraftPreview(APIModel):
    confirmation_id: UUID
    version: int
    content_hash: str
    expires_at: datetime
    payload: CreateExpenseRequest
    total_fen: int


class RunResponse(APIModel):
    id: UUID
    status: RunStatus
    version: int
    anchor_date: str
    timezone: str
    messages: list[ConversationMessage]
    missing_fields: list[str]
    preview: DraftPreview | None
    expense_id: UUID | None
