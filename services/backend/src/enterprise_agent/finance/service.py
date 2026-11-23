"""财务领域应用服务；HTTP API 和未来 Agent Tool 必须共享这层业务规则。"""

import hashlib
import json
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from enterprise_agent.db.models import AuditLog, Expense, ExpenseItem, User
from enterprise_agent.finance.schemas import MAX_TOTAL_FEN, CreateExpenseInput


def visible_expenses(actor: User):
    """按企业、角色和申请人生成可见报销的基础查询。"""
    # 财务可以看已提交单据，但其他员工的草稿保持私有。
    stmt = select(Expense).where(Expense.tenant_id == actor.tenant_id)
    if actor.role == "finance":
        return stmt.where(or_(Expense.applicant_id == actor.id, Expense.status != "draft"))
    return stmt.where(Expense.applicant_id == actor.id)


def list_expenses(db: Session, actor: User, limit: int, offset: int) -> list[Expense]:
    """查询当前用户有权读取的报销列表，并使用稳定的排序支持分页。"""
    stmt = visible_expenses(actor).order_by(Expense.created_at.desc(), Expense.id)
    return list(db.scalars(stmt.limit(limit).offset(offset)))


def get_expense(db: Session, actor: User, expense_id: UUID) -> Expense:
    """读取单张报销；无记录和无权访问统一返回404以减少编号探测。"""
    expense = db.scalar(visible_expenses(actor).where(Expense.id == expense_id))
    if expense is None:
        raise HTTPException(status_code=404, detail="报销申请不存在或无权访问")
    return expense


def payload_hash(payload: CreateExpenseInput) -> str:
    """对规范化后的请求计算摘要，以区分相同操作重试和键值误复用。"""
    canonical = json.dumps(
        payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_expense(
    db: Session,
    actor: User,
    payload: CreateExpenseInput,
    idempotency_key: UUID,
    request_id: str,
) -> Expense:
    """校验重试、计算总额，并原子性地保存主单、明细与审计记录。"""
    request_hash = payload_hash(payload)
    lookup = select(Expense).where(
        Expense.tenant_id == actor.tenant_id,
        Expense.applicant_id == actor.id,
        Expense.idempotency_key == str(idempotency_key),
    )

    def existing_or_conflict() -> Expense | None:
        """重复内容复用已有结果；相同键不同内容明确报冲突。"""
        existing = db.scalar(lookup)
        if existing is not None and existing.request_hash != request_hash:
            raise HTTPException(status_code=409, detail="同一幂等键不能用于不同的报销内容")
        return existing

    existing = existing_or_conflict()
    if existing is not None:
        return existing

    # 总额由可信的明细重新计算，不接受客户端传来的 total_fen。
    total_fen = sum(item.amount_fen for item in payload.items)
    if total_fen > MAX_TOTAL_FEN:
        raise HTTPException(status_code=422, detail="报销总金额超过演示系统上限")

    expense = Expense(
        tenant_id=actor.tenant_id,
        applicant_id=actor.id,
        title=payload.title,
        purpose=payload.purpose,
        total_fen=total_fen,
        status="draft",
        currency="CNY",
        version=1,
        idempotency_key=str(idempotency_key),
        request_hash=request_hash,
        items=[
            ExpenseItem(position=index, **item.model_dump())
            for index, item in enumerate(payload.items)
        ],
    )
    try:
        db.add(expense)
        # flush 把主单和关联明细发送给数据库，但仍留在当前事务中，尚未对其他事务可见。
        db.flush()
        db.add(
            AuditLog(
                tenant_id=actor.tenant_id,
                actor_id=actor.id,
                action="expense.draft_created",
                target_id=expense.id,
                request_id=request_id,
                details={"version": 1},
            )
        )
        # 仅在主单、明细和审计记录均成功后提交，保证三者不会部分落库。
        db.commit()
    except IntegrityError:
        # 约束冲突时撤销整个事务，再检查是否为并发重试赢得了唯一键。
        db.rollback()
        existing = existing_or_conflict()
        if existing is not None:
            return existing
        raise
    # 提交后刷新数据库生成的字段，再交给 API schema 序列化。
    db.refresh(expense)
    return expense
