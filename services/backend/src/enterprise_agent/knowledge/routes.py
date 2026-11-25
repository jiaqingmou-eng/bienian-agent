"""制度只读 HTTP 入口；管理操作暂不开放给浏览器或 Agent。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from enterprise_agent.api.schemas import COMMON_ERROR_RESPONSES
from enterprise_agent.auth import get_actor
from enterprise_agent.db.models import User
from enterprise_agent.db.session import get_db
from enterprise_agent.knowledge import citations, retrieval, service
from enterprise_agent.knowledge.schemas import (
    CitationDetail,
    CitationQuery,
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


@router.get(
    "/documents/{document_id}/citations/{citation_id}",
    response_model=CitationDetail,
    responses=COMMON_ERROR_RESPONSES,
)
def citation_detail(
    document_id: UUID,
    citation_id: Annotated[str, Path(pattern=r"^[a-f0-9]{64}$")],
    db: Db,
    actor: Actor,
    query: Annotated[CitationQuery, Query()],
):
    """按文档UUID和引用ID读取固定版本的完整条款，供后续原文定位使用。

    身份只来自鉴权依赖；不接受查询参数指定版本、行号、正文或租户。
    每次访问重新检查当前权限与发布状态；历史已发布版本仍可读取，不自动替换新版。
    返回可信版本、哈希、条款和原文范围；404表示不存在或不可访问，503表示索引异常。
    此接口纯读取，不重新搜索、调用模型、清洗 / 切分、建立索引或写入审计 / 财务表。
    """
    return citations.resolve_citation(db, actor, document_id, citation_id)


@router.post("/search", response_model=SearchDocumentsResponse, responses=COMMON_ERROR_RESPONSES)
def search(payload: SearchDocumentsRequest, db: Db, actor: Actor):
    """只读条款查询使用 POST 正文传问题，避免问题出现在 URL / 访问日志里。"""
    return retrieval.search_documents(db, actor, payload)
