"""消息计算执行记录：接收与完成由业务服务控制事务，外部模型调用前单独提交开始状态。

本模块只记录计算事实，不领取 run 租约、不恢复 checkpoint，也不调用财务写工具。
"""

import hashlib
import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from enterprise_agent.db.models import AgentExecution, AgentRun

logger = logging.getLogger(__name__)


def message_hash(message: str) -> str:
    """对 HTTP schema 已规范化的消息求哈希；不在执行表重复保存敏感正文。"""
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


def accept(
    db: Session, run: AgentRun, message_id: UUID, message: str, request_id: str
) -> AgentExecution:
    """登记已接收的一轮，不自行提交，确保消息与旧确认撤销能一起成功或回滚。

    run 必须已由服务端鉴权；调用方不能注入执行所属租户、所有者或初始状态。
    accepted_version 保存本轮输入真正被接受后的版本，而非重试请求携带的旧版本。
    """
    execution = AgentExecution(
        tenant_id=run.tenant_id,
        owner_id=run.owner_id,
        run_id=run.id,
        message_id=message_id,
        input_hash=message_hash(message),
        accepted_version=run.version,
        request_id=request_id,
    )
    db.add(execution)
    return execution


def load(db: Session, run: AgentRun, message_id: UUID) -> AgentExecution | None:
    """只在已授权的任务范围内读取记录；刷新缓存以观察其他请求已提交的状态。"""
    return db.scalar(
        select(AgentExecution)
        .where(
            AgentExecution.run_id == run.id,
            AgentExecution.tenant_id == run.tenant_id,
            AgentExecution.owner_id == run.owner_id,
            AgentExecution.message_id == message_id,
        )
        .execution_options(populate_existing=True)
    )


def begin(db: Session, execution: AgentExecution, request_id: str) -> int | None:
    """原子标记本条记录开始计算，返回本次尝试序号，然后结束事务。

    这只是同一消息的状态去重，不是 P2-02 的 run 级租约或多 worker 调度。
    queued 可以首次执行，failed 可以由用户手动重试；running 不自动抢占。
    """
    execution_id = execution.id
    attempt = db.scalar(
        update(AgentExecution)
        .where(
            AgentExecution.id == execution_id,
            AgentExecution.status.in_(["queued", "failed"]),
        )
        .values(
            status="running",
            attempt_count=AgentExecution.attempt_count + 1,
            started_at=datetime.now(UTC),
            finished_at=None,
            error_code=None,
        )
        .returning(AgentExecution.attempt_count)
        .execution_options(synchronize_session=False)
    )
    if attempt is None:
        db.rollback()
        # 另一个请求可能刚好完成；已完成请求仍遵守只读重试约定，而非再执行或误报失败。
        if db.scalar(select(AgentExecution.status).where(AgentExecution.id == execution_id)) == (
            "succeeded"
        ):
            return None
        raise HTTPException(409, "此消息正在处理或已完成，请刷新任务查看结果")
    # 必须先持久开始状态，再等待模型；进程退出时留下 running，不伪称明确失败。
    db.commit()
    logger.info(
        "Agent execution started requestId=%s executionId=%s attempt=%s",
        request_id,
        execution_id,
        attempt,
    )
    return attempt


def succeed(
    db: Session, execution_id: UUID, attempt: int, result_version: int, message_id: UUID
) -> None:
    """登记已保存结果的引用，不提交；必须与 run 的消息、预览更新在同一事务提交。"""
    changed = db.execute(
        update(AgentExecution)
        .where(
            AgentExecution.id == execution_id,
            AgentExecution.status == "running",
            AgentExecution.attempt_count == attempt,
        )
        .values(
            status="succeeded",
            result_version=result_version,
            result_message_id=message_id,
            finished_at=datetime.now(UTC),
            error_code=None,
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:
        raise HTTPException(409, "消息执行状态已变化，请刷新任务查看结果")


def fail(db: Session, execution_id: UUID, attempt: int, exc: Exception) -> None:
    """回滚未提交结果后记录明确失败，既不吞掉原异常，也不覆盖已经提交的成功。

    只保存稳定错误类别，禁止保存异常详情。数据库本身不可用时记录可能仍为 running，
    这代表无法确认终态，不能用另一条错误掩盖原请求；后续恢复机制再核对持久事实。
    """
    error_code = (
        f"HTTP_{exc.status_code}"
        if isinstance(exc, HTTPException)
        else "DATABASE_ERROR"
        if isinstance(exc, SQLAlchemyError)
        else "INTERNAL_ERROR"
    )
    db.rollback()
    try:
        changed = db.execute(
            update(AgentExecution)
            .where(
                AgentExecution.id == execution_id,
                AgentExecution.status == "running",
                AgentExecution.attempt_count == attempt,
            )
            .values(status="failed", error_code=error_code, finished_at=datetime.now(UTC))
            .execution_options(synchronize_session=False)
        ).rowcount
        db.commit()
        if changed == 1:
            logger.warning(
                "Agent execution failed executionId=%s attempt=%s code=%s",
                execution_id,
                attempt,
                error_code,
            )
    except SQLAlchemyError:
        db.rollback()
        logger.warning("Agent execution outcome unavailable executionId=%s", execution_id)
