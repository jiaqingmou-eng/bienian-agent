"""受控索引构建与小语料词项检索；不生成答案，不调用模型或写入财务数据。"""

from datetime import datetime
from hashlib import sha256
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import exists, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from enterprise_agent.config import get_settings
from enterprise_agent.db.models import AuditLog, KnowledgeDocument, KnowledgeIndex, User
from enterprise_agent.knowledge.chunking import RULE_VERSION, EvidenceUnit, build_units, terms
from enterprise_agent.knowledge.normalization import normalize_document
from enterprise_agent.knowledge.schemas import (
    SearchDocumentsRequest,
    SearchDocumentsResponse,
    SearchHit,
)
from enterprise_agent.knowledge.service import require_manager, summary


def build_index(db: Session, actor: User, document_id: UUID, request_id: str) -> KnowledgeIndex:
    """完整切分与词项准备完成后，一次提交索引快照和审计，不暴露半完成片段。

    只允许责任人处理已发布版本；重复命令复用同一份确定性快照，不追加重复片段。
    将来切分规则升级须另行明确重建策略，此版本不静默覆盖未知规则的索引。
    """
    require_manager(actor)
    document = db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.tenant_id == actor.tenant_id,
            KnowledgeDocument.owner_id == actor.id,
            KnowledgeDocument.status == "published",
        )
    )
    if document is None:
        raise HTTPException(404, "制度未发布或无索引管理权限")
    normalized = normalize_document(document.original_bytes, document.format)
    if (
        sha256(document.original_bytes).hexdigest() != document.content_hash
        or normalized.model_dump() != document.normalized_document
    ):
        raise HTTPException(409, "原件或统一结构不一致，禁止建立索引")
    units = build_units(document.id, document.content_hash, document.original_bytes, normalized)
    payload = [unit.model_dump() for unit in units]

    def reuse(row: KnowledgeIndex) -> KnowledgeIndex:
        """已有快照必须与本次输入 / 规则完全一致；不借重试修补不明来源的残缺数据。"""
        if (
            row.content_hash != document.content_hash
            or row.rule_version != RULE_VERSION
            or row.units != payload
            or row.unit_count != len(units)
            or row.chunk_count != sum(len(unit.chunks) for unit in units)
        ):
            raise HTTPException(409, "索引内容或规则不一致，需要受控重建")
        return row

    if row := db.get(KnowledgeIndex, document_id):
        return reuse(row)
    row = KnowledgeIndex(
        document_id=document_id,
        content_hash=document.content_hash,
        rule_version=RULE_VERSION,
        units=payload,
        unit_count=len(units),
        chunk_count=sum(len(unit.chunks) for unit in units),
    )
    try:
        db.add(row)
        db.flush()
        db.add(
            AuditLog(
                tenant_id=actor.tenant_id,
                actor_id=actor.id,
                action="knowledge.index",
                target_id=document_id,
                request_id=request_id,
                details={
                    "rule_version": RULE_VERSION,
                    "unit_count": row.unit_count,
                    "chunk_count": row.chunk_count,
                },
            )
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        if winner := db.get(KnowledgeIndex, document_id):
            return reuse(winner)
        raise
    return row


def search_documents(
    db: Session, actor: User, request: SearchDocumentsRequest
) -> SearchDocumentsResponse:
    """先筛选受权有效版本，再匹配其已完成索引；查询不隐式触发清洗 / 切分 / 入库。

    同一来源若存在多个适用且可读的已发布版本，取最高版本号；新版缺索引时不退回
    旧版。日期未提供时使用项目已有业务时区，避免服务器 UTC 跨日导致政策选择变化。
    """
    as_of = request.as_of or datetime.now(ZoneInfo(get_settings().agent_timezone)).date()
    response = SearchDocumentsResponse(
        query=request.query, as_of=as_of, limit=request.limit, items=[]
    )
    query_terms = terms(request.query)
    if not query_terms:
        return response

    def eligible(model):
        """索引不持有独立 ACL，始终跟随原文的当前租户、责任人和发布状态。"""
        return (
            model.tenant_id == actor.tenant_id,
            model.status == "published",
            or_(model.visibility == "tenant", model.owner_id == actor.id),
            model.effective_from <= as_of,
            or_(model.effective_until.is_(None), model.effective_until >= as_of),
        )

    newer = aliased(KnowledgeDocument)
    rows = db.execute(
        select(KnowledgeDocument, KnowledgeIndex, User.name)
        .outerjoin(KnowledgeIndex, KnowledgeIndex.document_id == KnowledgeDocument.id)
        .join(User, User.id == KnowledgeDocument.owner_id)
        .where(
            *eligible(KnowledgeDocument),
            ~exists(
                select(newer.id).where(
                    *eligible(newer),
                    newer.source_key == KnowledgeDocument.source_key,
                    newer.version > KnowledgeDocument.version,
                )
            ),
        )
        .order_by(KnowledgeDocument.source_key, KnowledgeDocument.id)
        .limit(129)
    ).all()
    # 本步只服务受控小语料；超过上限直接说明不可用，不截断候选后假装已查完整知识库。
    if len(rows) > 128:
        raise HTTPException(503, "当前词项检索基线最多支持 128 个有效来源，请先扩展检索能力")
    ranked: list[tuple[float, str, SearchHit]] = []
    for document, index, owner_name in rows:
        if index is None:
            continue
        if index.rule_version != RULE_VERSION or index.content_hash != document.content_hash:
            raise HTTPException(503, "制度索引需要受控重建，请联系责任人")
        units = [EvidenceUnit.model_validate(value) for value in index.units]
        if (
            len(units) != index.unit_count
            or sum(len(unit.chunks) for unit in units) != index.chunk_count
        ):
            raise HTTPException(503, "制度索引不完整，请联系责任人")
        for unit in units:
            # 返回完整条款，排名取其最佳子片段；多片段命中同条款只返回一次。
            matches = [
                (len(query_terms & set(chunk.terms)) / len(query_terms), chunk)
                for chunk in unit.chunks
            ]
            score, chunk = max(matches, key=lambda item: item[0])
            if score < 0.5:
                continue
            hit = SearchHit(
                document=summary(document, owner_name),
                citation_id=unit.id,
                matched_chunk_id=chunk.id,
                text=unit.text,
                heading_path=unit.heading_path,
                source_start_line=unit.source_start_line,
                source_end_line=unit.source_end_line,
            )
            ranked.append((score, unit.id, hit))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    response.items = [item[2] for item in ranked[: request.limit]]
    return response
