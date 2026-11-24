"""制度只读 HTTP 入口；管理操作暂不开放给浏览器或 Agent。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from enterprise_agent.api.schemas import COMMON_ERROR_RESPONSES
from enterprise_agent.auth import get_actor
from enterprise_agent.db.models import User
from enterprise_agent.db.session import get_db
from enterprise_agent.knowledge import retrieval, service
from enterprise_agent.knowledge.schemas import (
    DocumentDetail,
    DocumentListQuery,
    DocumentListResponse,
    SearchDocumentsRequest,
    SearchDocumentsResponse,
)

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[User, Depends(get_actor)]


@router.get("/documents", response_model=DocumentListResponse, responses=COMMON_ERROR_RESPONSES)
def documents(db: Db, actor: Actor, query: Annotated[DocumentListQuery, Query()]):
    """分页读取当前身份可见的制度；调用方不能通过参数替换租户或责任人。"""
    return service.list_documents(db, actor, query)


@router.get(
    "/documents/{document_id}", response_model=DocumentDetail, responses=COMMON_ERROR_RESPONSES
)
def document_detail(document_id: UUID, db: Db, actor: Actor):
    """读取固定制度版本的原文和统一结构；不以知道 UUID 作为访问凭证。"""
    return service.get_document(db, actor, document_id)


@router.post("/search", response_model=SearchDocumentsResponse, responses=COMMON_ERROR_RESPONSES)
def search(payload: SearchDocumentsRequest, db: Db, actor: Actor):
    """只读条款查询使用 POST 正文传问题，避免问题出现在 URL / 访问日志里。"""
    return retrieval.search_documents(db, actor, payload)
