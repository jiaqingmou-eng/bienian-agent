"""从已有快照解析固定版本引用；不检索、不调用模型，也不清洗、切分或写入数据。"""

import re
from hashlib import sha256
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_agent.db.models import KnowledgeDocument, KnowledgeIndex, User
from enterprise_agent.knowledge.chunking import RULE_VERSION, EvidenceUnit
from enterprise_agent.knowledge.schemas import CitationDetail
from enterprise_agent.knowledge.service import readable, summary


def resolve_citation(
    db: Session, actor: User, document_id: UUID, citation_id: str
) -> CitationDetail:
    """先授权读取指定文档，再核对快照并查找完整引用；整个路径只有数据库读取。

    UUID本身已标识不可变的内容版本，不按当前日期或最高版本筛选。已过适用期但仍发布
    且可读的旧制度可以核对；撤回、未知、跨租户与私有来源统一404，避免探测受限来源。
    文档与索引使用同一条查询读取，检查前不向调用方透露索引是否存在。
    """
    row = db.execute(
        select(KnowledgeDocument, KnowledgeIndex, User.name)
        .outerjoin(KnowledgeIndex, KnowledgeIndex.document_id == KnowledgeDocument.id)
        .join(User, User.id == KnowledgeDocument.owner_id)
        .where(KnowledgeDocument.id == document_id, *readable(actor))
    ).first()
    if row is None:
        raise HTTPException(404, "引用不存在或当前不可访问")
    document, index, owner_name = row
    # 原件哈希、快照哈希与规则均需一致。索引异常不是“没有条款”，也不能在读取请求中
    # 自动重建或偷偷改用其他版本；由既有受控管理流程处理。
    if (
        index is None
        or index.content_hash != document.content_hash
        or sha256(document.original_bytes).hexdigest() != document.content_hash
        or index.rule_version != RULE_VERSION
    ):
        raise HTTPException(503, "引用索引不可用或需要受控重建，请联系制度责任人")
    try:
        # JSON快照虽由服务端构建，仍需防止残缺 / 旧结构变成500或错误的定位信息。
        # 不重新生成条款，只读取既有快照并检查结构、数量、唯一标识及原文范围。
        if not isinstance(index.units, list) or not 1 <= len(index.units) <= 4096:
            raise ValueError("invalid unit collection")
        units = [EvidenceUnit.model_validate(value, strict=True) for value in index.units]
        if (
            len(units) != index.unit_count
            or sum(len(unit.chunks) for unit in units) != index.chunk_count
            or not len(units) <= index.chunk_count <= 4096
        ):
            raise ValueError("incomplete index")
        # 这里只计数原始换行，不清洗正文或重做切分；CRLF / CR约定与已有行映射一致。
        line_count = (
            document.original_bytes.replace(b"\r\n", b"\n").replace(b"\r", b"\n").count(b"\n") + 1
        )
        seen = set()
        previous_end = 0
        for unit in units:
            if (
                not re.fullmatch(r"[a-f0-9]{64}", unit.id)
                or unit.id in seen
                or not unit.text
                or len(unit.text) > 131072
                or not previous_end < unit.source_start_line <= unit.source_end_line <= line_count
                or unit.text.count("\n") + 1 != unit.source_end_line - unit.source_start_line + 1
            ):
                raise ValueError("invalid citation mapping")
            seen.add(unit.id)
            previous_end = unit.source_end_line
    except (ValidationError, ValueError, TypeError) as exc:
        # 不返回校验异常中的原文片段；外部只能知道当前索引不可用于可靠定位。
        raise HTTPException(503, "引用索引不完整或映射无效，请联系制度责任人") from exc
    unit = next((unit for unit in units if unit.id == citation_id), None)
    if unit is None:
        # 只在已授权的指定版本内查找，禁止拿其他文档或子片段的ID拼装成完整条款。
        raise HTTPException(404, "引用不存在或当前不可访问")
    return CitationDetail(
        document=summary(document, owner_name),
        content_hash=document.content_hash,
        citation_id=unit.id,
        rule_version=index.rule_version,
        text=unit.text,
        heading_path=unit.heading_path,
        source_start_line=unit.source_start_line,
        source_end_line=unit.source_end_line,
    )
