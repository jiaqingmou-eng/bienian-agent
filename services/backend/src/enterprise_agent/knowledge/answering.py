"""制度单轮问答：受权检索、证据预算、模型回答及来源校验；不写报销任务。"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from enterprise_agent.agent.chat_model import ChatModel
from enterprise_agent.agent.chat_schemas import ChatResponse
from enterprise_agent.agent.service import fresh_actor
from enterprise_agent.db.models import User
from enterprise_agent.knowledge import retrieval
from enterprise_agent.knowledge.schemas import SearchDocumentsRequest, SearchHit

# 用字符预算约束当前词项基线；不冒充模型 token 计数，超预算时拒绝而不是截断限制条件。
MAX_EVIDENCE_CHARS = 16_000


def format_evidence(items: list[SearchHit]) -> list[dict]:
    """模型只需要正文与版本语义；身份、词项、匹配片段、行号由服务端保存并补回。"""
    return [
        {
            "citation_id": hit.citation_id,
            "document_title": hit.document.title,
            "document_version": hit.document.version,
            "effective_from": hit.document.effective_from.isoformat(),
            "effective_until": (
                hit.document.effective_until.isoformat() if hit.document.effective_until else None
            ),
            "heading_path": hit.heading_path,
            "text": hit.text,
        }
        for hit in items
    ]


def answer_question(
    db: Session, actor: User, question: str, query: SearchDocumentsRequest, model: ChatModel
) -> ChatResponse:
    """先检索再生成；空命中不调用作答模型，异常不转换成“制度没有规定”。"""
    result = retrieval.search_documents(db, actor, query)
    base = {"kind": "policy", "as_of": result.as_of}
    if not result.items:
        return ChatResponse(
            **base,
            status="insufficient",
            message="未找到匹配的可用条款，暂时无法确认；请核对关键词或适用日期。",
        )
    evidence = format_evidence(result.items)
    if sum(len(str(item)) for item in evidence) > MAX_EVIDENCE_CHARS:
        return ChatResponse(
            **base,
            status="insufficient",
            message="相关条款超过本次问答长度，请缩小问题范围后重试。",
        )
    # 检索完即可释放只读事务；模型慢调用期间不持续占用数据库连接或旧事务快照。
    owner_id, tenant_id = actor.id, actor.tenant_id
    db.rollback()
    generated = model.answer(
        {"question": question, "as_of": result.as_of.isoformat(), "evidence": evidence}
    )
    by_id = {hit.citation_id: hit for hit in result.items}
    if generated.status != "answered":
        # 模型即使在非确定分支附带引用，也全部丢弃；不展示其正文中可能夹带的政策结论。
        messages = {
            "insufficient": "现有条款不足以确认答案，请补充问题或联系制度责任人。",
            "conflict": "检索依据存在冲突，暂时无法确认，请联系制度责任人核对。",
            "needs_clarification": "请在完整问题中补充适用日期、地点或人员等条件后重新提问。",
        }
        return ChatResponse(**base, status=generated.status, message=messages[generated.status])
    if not generated.citations:
        raise HTTPException(502, "模型回答缺少制度引用，已停止展示")
    # 作答期间可能撤权、撤回或发布了更高适用版本；固定本次日期重新检索并核对引用。
    actor = fresh_actor(db, owner_id, tenant_id)
    current_sources = {
        hit.citation_id: hit
        for hit in retrieval.search_documents(
            db, actor, query.model_copy(update={"as_of": result.as_of})
        ).items
    }
    sources = []
    seen = set()
    for citation in generated.citations:
        hit = by_id.get(citation.citation_id)
        if (
            hit is None
            or not citation.quote.strip()
            or citation.quote not in hit.text
            or citation.citation_id in seen
        ):
            raise HTTPException(502, "模型引用或原文摘录无法核验，已停止展示")
        if citation.citation_id not in current_sources:
            raise HTTPException(409, "制度权限、发布状态或适用版本已变化，请重新提问")
        seen.add(citation.citation_id)
        sources.append(hit)
    # 引用和摘录校验只证明来源真实，不等同于对自然语言推理正确性的形式证明。
    return ChatResponse(**base, status="answered", message=generated.answer, sources=sources)
