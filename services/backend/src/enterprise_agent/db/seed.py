"""创建可重复运行的虚构演示数据；脚本不会清空或覆盖现有业务记录。"""

from uuid import UUID

from sqlalchemy.orm import Session

from enterprise_agent.config import get_settings
from enterprise_agent.db.models import Approval, Tenant, User
from enterprise_agent.db.session import get_engine
from enterprise_agent.finance.schemas import CreateExpenseInput
from enterprise_agent.finance.service import create_expense

# 固定 UUID 让演示账号在重复初始化和测试中保持稳定。
DEMO_TENANT_ID = UUID("10000000-0000-4000-8000-000000000001")
DEMO_ACCOUNTS = {
    "employee_a": UUID("20000000-0000-4000-8000-000000000001"),
    "employee_b": UUID("20000000-0000-4000-8000-000000000002"),
    "finance": UUID("20000000-0000-4000-8000-000000000003"),
}


def seed(db: Session) -> None:
    """补齐演示企业、用户和样例单据；已存在的数据保持不变。"""
    if db.get(Tenant, DEMO_TENANT_ID) is None:
        db.add(Tenant(id=DEMO_TENANT_ID, name="示例科技（虚构）"))
        db.flush()
    for key, name, role in [
        ("employee_a", "员工小林", "employee"),
        ("employee_b", "员工小陈", "employee"),
        ("finance", "财务小周", "finance"),
    ]:
        if db.get(User, DEMO_ACCOUNTS[key]) is None:
            db.add(User(id=DEMO_ACCOUNTS[key], tenant_id=DEMO_TENANT_ID, name=name, role=role))
    db.commit()

    for index, key in enumerate(["employee_a", "employee_b"], start=1):
        idempotency_key = UUID(f"30000000-0000-4000-8000-{index:012d}")
        actor = db.get(User, DEMO_ACCOUNTS[key])
        assert actor is not None
        payload = CreateExpenseInput.model_validate(
            {
                "title": "上海差旅报销（演示）" if index == 1 else "客户拜访交通费（演示）",
                "purpose": "虚构业务数据，仅用于演示和开发。",
                "items": [
                    {
                        "category": "transport",
                        "incurred_on": "2026-11-20",
                        "description": "高铁往返",
                        "amount_fen": 60000,
                    },
                    {
                        "category": "lodging",
                        "incurred_on": "2026-11-20",
                        "description": "住宿费用，晚数待补充",
                        "amount_fen": 90000,
                    },
                ],
            }
        )
        # 演示数据也复用正式服务，确保幂等、金额计算和审计路径一致。
        expense = create_expense(db, actor, payload, idempotency_key, str(idempotency_key))
        # 仅为演示财务列表而构造待审批样例，不代表真实提交审批接口已经上线。
        if index == 2 and expense.status == "draft":
            expense.status = "pending"
            db.add(
                Approval(
                    tenant_id=actor.tenant_id,
                    expense_id=expense.id,
                    expense_version=expense.version,
                    status="pending",
                )
            )
            db.commit()


def main() -> None:
    """仅在显式开启演示登录时运行种子数据，避免误写其他环境。"""
    if not get_settings().enable_demo_login:
        raise SystemExit("Set ENABLE_DEMO_LOGIN=true to explicitly enable fictional seed data")
    with Session(get_engine()) as db:
        seed(db)
    print("Demo fixtures ready; existing business records were preserved.")


if __name__ == "__main__":
    main()
