"""统一自然语言入口：先分流，再进入有明确边界的报销或只读制度能力。"""

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_agent.agent import service
from enterprise_agent.agent.chat_model import ChatModel
from enterprise_agent.agent.chat_schemas import ChatRequest, ChatResponse
from enterprise_agent.agent.model import Extractor
from enterprise_agent.agent.policy_dates import policy_date
from enterprise_agent.agent.schemas import RunMessageRequest, StartRunRequest
from enterprise_agent.config import get_settings
from enterprise_agent.db.models import AgentRun, User
from enterprise_agent.knowledge.answering import answer_question
from enterprise_agent.knowledge.schemas import SearchDocumentsRequest


def chat(
    db: Session,
    actor: User,
    request: ChatRequest,
    model: ChatModel,
    extractor: Extractor,
    request_id: str,
) -> ChatResponse:
    """分类前仅验证任务归属，不追加消息、不更新版本、不撤销已有预览。

    制度咨询是当前页面的一轮独立问题，不存入报销消息或自动接续澄清。
    报销写分支保留既有稳定消息标识、执行记录和确认门禁；显式按钮不经过分类。
    """
    run = service.load_run(db, actor, request.run_id) if request.run_id else None
    # 报销请求超时后的相同消息必须优先走原有去重，不让第二次意图分类改写执行目标。
    replay = run
    if replay is None:
        replay = db.scalar(
            select(AgentRun).where(
                AgentRun.tenant_id == actor.tenant_id,
                AgentRun.owner_id == actor.id,
                AgentRun.client_request_id == request.message_id,
            )
        )
    accepted = replay and any(
        message["id"] == str(request.message_id) for message in replay.messages
    )
    if accepted:
        if request.run_id:
            result = service.send_message(
                db,
                actor,
                run.id,
                RunMessageRequest(
                    message_id=request.message_id,
                    expected_version=request.expected_version,
                    message=request.message,
                ),
                extractor,
                request_id,
            )
        else:
            result = service.start_run(
                db,
                actor,
                StartRunRequest(
                    client_request_id=request.message_id,
                    message=request.message,
                ),
                extractor,
                request_id,
            )
        return ChatResponse(kind="expense", status="expense", message="报销任务已恢复", run=result)
    today = datetime.now(ZoneInfo(get_settings().agent_timezone)).date()
    context = {
        "message": request.message,
        "today": today.isoformat(),
        "task_status": run.status if run else None,
        # 仅给分类器少量近期报销上下文，便于识别“标题：出差”等补充；不传身份和确认凭据。
        "recent_expense_messages": [
            {"role": m["role"], "content": m["content"]} for m in run.messages[-4:]
        ]
        if run
        else [],
    }
    owner_id, tenant_id = actor.id, actor.tenant_id
    db.rollback()
    decision = model.classify(context)
    # 模型调用后重新加载可信身份；旧 ORM 数据不能替代当前授权检查。
    actor = service.fresh_actor(db, owner_id, tenant_id)
    if request.run_id:
        service.load_run(db, actor, request.run_id)
    if decision.intent == "policy":
        clear_date, as_of = policy_date(request.message, decision.as_of, today)
        if not clear_date:
            return ChatResponse(
                kind="clarification",
                status="needs_clarification",
                message="请在完整制度问题中写明唯一适用日期（YYYY-MM-DD），不能据含糊时间选择制度版本。",
            )
        # 模型的检索改写是辅助信息，不应因其留空而拒绝一个有效制度问题。
        # 回退只使用用户原话，不猜关键词；超过检索上限则要求缩小范围，不能截断条件。
        query = decision.query.strip() or request.message
        if len(query) > 300:
            return ChatResponse(
                kind="clarification",
                status="needs_clarification",
                message="请将制度问题缩小到300字以内，并保留地点、适用日期等关键条件。",
            )
        return answer_question(
            db,
            actor,
            request.message,
            SearchDocumentsRequest(
                query=query,
                as_of=as_of,
                limit=5,
            ),
            model,
        )
    if decision.intent != "expense":
        messages = {
            "mixed": "请一次选择一个目标：咨询制度，或创建/修改报销。请明确后重新发送。",
            "clarify": "请明确本次目标及适用条件；确认创建请核对预览后点击“确认创建”按钮。",
            "unsupported": "当前支持报销草稿处理和单轮制度咨询，请描述需要办理或查询的内容。",
        }
        return ChatResponse(
            kind="clarification",
            status=("unsupported" if decision.intent == "unsupported" else "needs_clarification"),
            message=messages[decision.intent],
        )
    # 只有确定进入报销分支后才调用会清空旧预览的服务，不绕过其并发版本和权限门禁。
    if request.run_id:
        result = service.send_message(
            db,
            actor,
            request.run_id,
            RunMessageRequest(
                message_id=request.message_id,
                expected_version=request.expected_version,
                message=request.message,
            ),
            extractor,
            request_id,
        )
    else:
        result = service.start_run(
            db,
            actor,
            StartRunRequest(
                client_request_id=request.message_id,
                message=request.message,
            ),
            extractor,
            request_id,
        )
    return ChatResponse(kind="expense", status="expense", message="报销任务已更新", run=result)
