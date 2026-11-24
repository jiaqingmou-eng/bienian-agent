"""任务快照、乐观并发与确认门禁；财务副作用只能经受控工具执行。"""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from enterprise_agent.agent import executions
from enterprise_agent.agent.model import Extractor
from enterprise_agent.agent.prompts import PROMPT_VERSION
from enterprise_agent.agent.schemas import (
    ConfirmRunRequest,
    DraftPreview,
    RunListQuery,
    RunListResponse,
    RunMessageRequest,
    RunResponse,
    RunSummary,
    StartRunRequest,
)
from enterprise_agent.agent.workflow import interpret
from enterprise_agent.config import get_settings
from enterprise_agent.db.models import AgentExecution, AgentRun, User
from enterprise_agent.finance.schemas import CreateExpenseInput
from enterprise_agent.finance.service import payload_hash
from enterprise_agent.tools.expenses import TOOL_NAME, TOOL_VERSION, create_expense_draft

logger = logging.getLogger(__name__)


def list_runs(db: Session, actor: User, query: RunListQuery) -> RunListResponse:
    # 私人任务始终按企业和所有者过滤，财务角色也不能列举员工对话。
    scope = (AgentRun.tenant_id == actor.tenant_id, AgentRun.owner_id == actor.id)
    total = db.scalar(select(func.count()).select_from(AgentRun).where(*scope)) or 0
    rows = db.execute(
        select(AgentRun.id, AgentRun.status, AgentRun.candidate, AgentRun.created_at)
        .where(*scope)
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .limit(query.limit)
        .offset(query.offset)
    )
    # 列表仅返回摘要，不传输完整对话、确认凭据或财务明细。
    return RunListResponse(
        items=[
            RunSummary(
                id=row.id,
                status=row.status,
                title=row.candidate.get("title") or "未命名报销任务",
                created_at=row.created_at,
            )
            for row in rows
        ],
        total=total,
        limit=query.limit,
        offset=query.offset,
    )


def load_run(db: Session, actor: User, run_id: UUID) -> AgentRun:
    run = db.scalar(
        select(AgentRun)
        .where(
            AgentRun.id == run_id,
            AgentRun.tenant_id == actor.tenant_id,
            AgentRun.owner_id == actor.id,
        )
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise HTTPException(404, "任务不存在或无权访问")
    return run


def response(run: AgentRun) -> RunResponse:
    preview = None
    if run.preview_payload and run.confirmation_id:
        preview = DraftPreview(
            confirmation_id=run.confirmation_id,
            version=run.preview_version,
            content_hash=run.preview_hash,
            expires_at=run.expires_at.replace(tzinfo=UTC),
            payload=run.preview_payload,
            total_fen=sum(item["amount_fen"] for item in run.preview_payload["items"]),
        )
    return RunResponse(
        id=run.id,
        status=run.status,
        version=run.version,
        anchor_date=run.anchor_date,
        timezone=run.timezone,
        messages=[
            {key: message[key] for key in ("id", "role", "content")} for message in run.messages
        ],
        missing_fields=run.missing_fields,
        preview=preview,
        expense_id=run.expense_id,
    )


def fresh_actor(db: Session, owner_id: UUID, tenant_id: UUID) -> User:
    actor = db.get(User, owner_id, populate_existing=True)
    if actor is None or actor.tenant_id != tenant_id:
        raise HTTPException(403, "用户权限已变化，请重新登录")
    return actor


def start_run(
    db: Session, actor: User, request: StartRunRequest, extractor: Extractor, request_id: str
) -> RunResponse:
    input_hash = executions.message_hash(request.message)
    owner_id, tenant_id = actor.id, actor.tenant_id
    lookup = select(AgentRun).where(
        AgentRun.tenant_id == tenant_id,
        AgentRun.owner_id == owner_id,
        AgentRun.client_request_id == request.client_request_id,
    )
    run = db.scalar(lookup)
    if run is None:
        settings = get_settings()
        anchor_date = datetime.now(ZoneInfo(settings.agent_timezone)).date().isoformat()
        run = AgentRun(
            tenant_id=tenant_id,
            owner_id=owner_id,
            client_request_id=request.client_request_id,
            input_hash=input_hash,
            anchor_date=anchor_date,
            timezone=settings.agent_timezone,
            candidate={},
            missing_fields=[],
            messages=[
                {
                    "id": str(request.client_request_id),
                    "role": "user",
                    "content": request.message,
                    "anchor_date": anchor_date,
                    "timezone": settings.agent_timezone,
                }
            ],
        )
        db.add(run)
        try:
            # flush 取得服务端任务 ID 和默认版本；随后将任务、初始消息和执行记录一起提交。
            # 任一写入失败都回滚，不能留下没有执行记录的新消息。
            db.flush()
            executions.accept(db, run, request.client_request_id, request.message, request_id)
            db.commit()
        except IntegrityError:
            db.rollback()
            run = db.scalar(lookup)
            if run is None:
                raise
    if run.input_hash != input_hash:
        raise HTTPException(409, "相同请求标识不能用于不同消息")
    return continue_message(
        db,
        fresh_actor(db, owner_id, tenant_id),
        run,
        request.client_request_id,
        request.message,
        extractor,
        request_id,
    )


def send_message(
    db: Session,
    actor: User,
    run_id: UUID,
    request: RunMessageRequest,
    extractor: Extractor,
    request_id: str,
) -> RunResponse:
    run = load_run(db, actor, run_id)
    for message in run.messages:
        if message["id"] == str(request.message_id):
            if message["role"] != "user" or message["content"] != request.message:
                raise HTTPException(409, "相同消息标识不能用于不同内容")
            return continue_message(
                db, actor, run, request.message_id, request.message, extractor, request_id
            )
    if run.version != request.expected_version:
        raise HTTPException(409, "任务已变化，请刷新后再修改")
    if run.status not in {"collecting", "awaiting_confirmation"}:
        raise HTTPException(409, "此任务不能继续修改，请查看处理结果或新建任务")
    # 给本轮用户消息和助手答复各预留一条，失败重试复用已接受的用户消息。
    if len(run.messages) >= 39:
        raise HTTPException(422, "已达到本任务对话上限，请使用表单或新建任务")
    # 先接受修改并撤销旧确认，再调用模型，慢调用期间旧预览也不能被另一标签页确认。
    anchor_date = datetime.now(ZoneInfo(run.timezone)).date().isoformat()
    messages = [
        *run.messages,
        {
            "id": str(request.message_id),
            "role": "user",
            "content": request.message,
            "anchor_date": anchor_date,
            "timezone": run.timezone,
        },
    ]
    changed = db.execute(
        update(AgentRun)
        .where(
            AgentRun.id == run_id,
            AgentRun.version == request.expected_version,
            AgentRun.status.in_(["collecting", "awaiting_confirmation"]),
        )
        .values(
            status="collecting",
            version=AgentRun.version + 1,
            messages=messages,
            anchor_date=anchor_date,
            confirmation_id=None,
            preview_payload=None,
            preview_version=None,
            preview_hash=None,
            expires_at=None,
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:
        db.rollback()
        # 相同消息可能由另一请求刚刚接收。先重新走去重分支，不把已接收消息再追加一次；
        # 不同消息 / 版本的竞争仍按原有 409 规则处理，这里不实现 run 级调度或租约。
        latest = load_run(db, actor, run_id)
        if any(message["id"] == str(request.message_id) for message in latest.messages):
            return send_message(db, actor, run_id, request, extractor, request_id)
        raise HTTPException(409, "任务已变化，请刷新后再修改")
    # populate_existing 读取本事务刚更新的版本，执行记录必须绑定消息接收后的版本。
    run = load_run(db, actor, run_id)
    executions.accept(db, run, request.message_id, request.message, request_id)
    db.commit()
    return continue_message(
        db,
        actor,
        load_run(db, actor, run_id),
        request.message_id,
        request.message,
        extractor,
        request_id,
    )


def continue_message(
    db: Session,
    actor: User,
    run: AgentRun,
    message_id: UUID,
    message: str,
    extractor: Extractor,
    request_id: str,
) -> RunResponse:
    """原请求只命中其执行记录；成功返回当前任务，明确失败才允许手动重试。

    升级前已完成消息不补造历史记录；只有旧任务末尾尚未完成的用户消息在首次再次处理时
    纳入记录管理，created_at 表示此次纳管时间，不伪造过去的执行时间或次数。
    """
    execution = executions.load(db, run, message_id)
    pending = (
        run.status == "collecting"
        and run.messages[-1]["id"] == str(message_id)
        and run.messages[-1]["role"] == "user"
    )
    if execution is None:
        if not pending:
            return response(run)
        # 新接收的消息已有原子创建的记录；此分支仅兼容升级前未完成的最后一条消息。
        executions.accept(db, run, message_id, message, request_id)
        try:
            db.commit()
        except IntegrityError:
            # 两个旧任务重试同时首次纳管时，由唯一约束决定一条记录，另一请求复用它。
            db.rollback()
        run_id = run.id
        run = load_run(db, actor, run_id)
        execution = executions.load(db, run, message_id)
        if execution is None:
            raise HTTPException(503, "消息执行记录暂不可用，请稍后重试")
    if execution.input_hash != executions.message_hash(message):
        raise HTTPException(409, "相同消息标识不能用于不同内容")
    # 首次纳管过程可能与其他请求交错，最终以重新读取的任务状态判断是否还能处理该消息。
    pending = (
        run.status == "collecting"
        and run.messages[-1]["id"] == str(message_id)
        and run.messages[-1]["role"] == "user"
    )
    if execution.status == "succeeded" or not pending or execution.accepted_version != run.version:
        # 已完成、被后续消息取代、已取消或已进入财务执行的轮次只读当前 run。
        # 不能为了返回旧结果重新生成确认 ID、延长预览期限或回退业务状态。
        return response(run)
    return advance(db, actor, run, execution, extractor, request_id)


def advance(
    db: Session,
    actor: User,
    run: AgentRun,
    execution: AgentExecution,
    extractor: Extractor,
    request_id: str,
) -> RunResponse:
    """执行一轮计算；开始状态先提交，最终 run 与成功记录在同一事务提交。"""
    run_id, expected_version = run.id, run.version
    owner_id, tenant_id = actor.id, actor.tenant_id
    execution_id = execution.id
    messages = list(run.messages)
    context = {
        "current_candidate": dict(run.candidate),
        "history": [
            {
                key: message[key]
                for key in ("id", "role", "content", "anchor_date", "timezone")
                if key in message
            }
            for message in messages[-6:]
        ],
        "anchor_date": run.anchor_date,
        "timezone": run.timezone,
    }
    # begin 提交 running 并结束读取事务；模型网络等待期间没有数据库事务或行锁。
    attempt = executions.begin(db, execution, request_id)
    if attempt is None:
        return response(load_run(db, actor, run_id))
    try:
        result = interpret(extractor, context)
        actor = fresh_actor(db, owner_id, tenant_id)
        extraction = result["extraction"]
        missing, payload = result["missing"], result["payload"]
        status = "awaiting_confirmation" if payload else "collecting"
        if extraction.intent == "cancel":
            status, missing, payload = "cancelled", [], None
            text = "任务已取消，没有创建报销草稿。"
        elif extraction.intent == "unsupported":
            status, payload, missing = "collecting", None, []
            text = (
                "目前支持整理报销草稿。请描述费用，或使用表单；"
                "知识问答和审批尚未接入。文字确认不会触发创建。"
            )
        elif missing:
            text = "请补充或修正：" + "；".join(missing[:6]) + "。尚未创建草稿。"
        else:
            text = (
                "已整理草稿预览，请核对全部字段。只有点击“确认创建”才会创建草稿；"
                "修改信息会使旧确认失效。"
            )
        candidate = (
            context["current_candidate"]
            if extraction.intent == "unsupported"
            else extraction.candidate.model_dump(mode="json")
        )
        # 原始消息+每轮候选快照可定位字段在哪轮提出。快照是待确认提取，不代表已核实事实。
        messages.append(
            {
                "id": str(uuid4()),
                "role": "assistant",
                "content": text,
                "provenance": {
                    "source_message_ids": [
                        message["id"] for message in messages if message["role"] == "user"
                    ],
                    "prompt_version": PROMPT_VERSION,
                    "candidate_snapshot": candidate,
                },
            }
        )
        new_version = expected_version + 1
        values = {
            "status": status,
            "version": new_version,
            "messages": messages,
            "candidate": candidate,
            "missing_fields": missing,
            "preview_payload": payload,
            "preview_version": new_version if payload else None,
            "confirmation_id": uuid4() if payload else None,
            "preview_hash": payload_hash(CreateExpenseInput.model_validate(payload))
            if payload
            else None,
            "expires_at": datetime.now(UTC)
            + timedelta(seconds=get_settings().agent_confirmation_ttl_seconds)
            if payload
            else None,
        }
        changed = db.execute(
            update(AgentRun)
            .where(
                AgentRun.id == run_id,
                AgentRun.owner_id == owner_id,
                AgentRun.tenant_id == tenant_id,
                AgentRun.version == expected_version,
                AgentRun.status.in_(["collecting", "awaiting_confirmation"]),
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        ).rowcount
        if changed != 1:
            db.rollback()
            raise HTTPException(409, "任务已被其他请求更新，请刷新；本次没有创建草稿")
        # 助手消息、预览与执行成功必须原子提交；结果引用只指向本次确实保存的版本。
        executions.succeed(db, execution_id, attempt, new_version, UUID(messages[-1]["id"]))
        db.commit()
        logger.info(
            "Agent message requestId=%s runId=%s executionId=%s attempt=%s version=%s status=%s",
            request_id,
            run_id,
            execution_id,
            attempt,
            new_version,
            status,
        )
        return response(load_run(db, actor, run_id))
    except Exception as exc:
        executions.fail(db, execution_id, attempt, exc)
        raise


def confirm_run(
    db: Session, actor: User, run_id: UUID, request: ConfirmRunRequest, request_id: str
) -> RunResponse:
    run = load_run(db, actor, run_id)
    if (
        run.confirmation_id != request.confirmation_id
        or run.preview_version != request.expected_version
        or run.preview_hash != request.content_hash
        or not run.preview_payload
    ):
        raise HTTPException(409, "确认已失效，请刷新预览并重新确认")
    if payload_hash(CreateExpenseInput.model_validate(run.preview_payload)) != run.preview_hash:
        raise HTTPException(409, "预览内容已变化，请重新核对")
    if run.status == "completed":
        return response(run)
    if run.status == "awaiting_confirmation":
        expires_at = run.expires_at
        if expires_at is None or expires_at.replace(tzinfo=UTC) <= datetime.now(UTC):
            raise HTTPException(409, "预览确认已过期，请补充消息以重新生成预览")
        claimed = db.execute(
            update(AgentRun)
            .where(
                AgentRun.id == run_id,
                AgentRun.version == request.expected_version,
                AgentRun.status == "awaiting_confirmation",
                AgentRun.confirmation_id == request.confirmation_id,
            )
            .values(status="executing", version=AgentRun.version + 1)
            .execution_options(synchronize_session=False)
        ).rowcount
        if claimed != 1:
            db.rollback()
            raise HTTPException(409, "任务正在变化，请刷新后查看或重试原确认")
        db.commit()
    elif run.status != "executing":
        raise HTTPException(409, "此任务不能创建草稿")

    # executing 代表确认已持久接受。网络断线或过期后可继续原操作，但不能改内容或换键。
    run = load_run(db, actor, run_id)
    owner_id, tenant_id = run.owner_id, run.tenant_id
    payload, operation_id = dict(run.preview_payload), run.confirmation_id
    actor = fresh_actor(db, owner_id, tenant_id)
    result = create_expense_draft(db, actor, payload, operation_id, request_id)
    # 财务服务已经提交主单+明细+审计；此后的失败保持 executing，原键重试可对账恢复。
    db.execute(
        update(AgentRun)
        .where(
            AgentRun.id == run_id,
            AgentRun.status == "executing",
            AgentRun.confirmation_id == operation_id,
        )
        .values(status="completed", expense_id=result.expense_id, version=AgentRun.version + 1)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    logger.info(
        "Agent tool success requestId=%s runId=%s operationId=%s expenseId=%s tool=%s version=%s",
        request_id,
        run_id,
        operation_id,
        result.expense_id,
        TOOL_NAME,
        TOOL_VERSION,
    )
    return response(load_run(db, actor, run_id))


def cancel_run(db: Session, actor: User, run_id: UUID, expected_version: int) -> RunResponse:
    run = load_run(db, actor, run_id)
    if run.status == "cancelled":
        return response(run)
    if run.version != expected_version or run.status not in {"collecting", "awaiting_confirmation"}:
        raise HTTPException(409, "任务已变化或已开始执行，不能取消，请刷新")
    changed = db.execute(
        update(AgentRun)
        .where(
            AgentRun.id == run_id,
            AgentRun.version == expected_version,
            AgentRun.status.in_(["collecting", "awaiting_confirmation"]),
        )
        .values(
            status="cancelled",
            version=AgentRun.version + 1,
            confirmation_id=None,
            preview_payload=None,
            preview_hash=None,
            preview_version=None,
            expires_at=None,
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:
        db.rollback()
        raise HTTPException(409, "任务已变化，请刷新")
    db.commit()
    return response(load_run(db, actor, run_id))
