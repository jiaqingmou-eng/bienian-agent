"""P1 有界 LangGraph：提取 → 确定性校验。确认与写入由服务端门禁控制。"""

import re
from datetime import date
from decimal import Decimal
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from pydantic import ValidationError

from enterprise_agent.agent.model import Extractor
from enterprise_agent.agent.schemas import DraftCandidate, Extraction
from enterprise_agent.finance.schemas import MAX_TOTAL_FEN, CreateExpenseInput


class WorkflowState(TypedDict, total=False):
    context: dict
    extraction: Extraction
    missing: list[str]
    payload: dict | None


def validate_candidate(candidate: DraftCandidate) -> tuple[list[str], dict | None]:
    """严格十进制转分，不用模型计算总额，不用默认值掩盖缺项。"""
    missing = []
    if not candidate.title or not candidate.title.strip():
        missing.append("报销标题")
    if not candidate.purpose or not candidate.purpose.strip():
        missing.append("业务用途")
    if not candidate.items:
        missing.append("至少一条费用明细（日期、类别、说明和人民币金额）")
    items = []
    for index, item in enumerate(candidate.items, 1):
        prefix = f"费用{index}："
        if item.category is None:
            missing.append(prefix + "费用类别")
        try:
            if item.incurred_on is None or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", item.incurred_on):
                raise ValueError("date required")
            incurred_on = date.fromisoformat(item.incurred_on)
        except ValueError:
            missing.append(prefix + "准确日期")
            incurred_on = None
        if not item.description or not item.description.strip():
            missing.append(prefix + "费用说明")
        amount_fen = 0
        if item.amount_yuan and re.fullmatch(r"(?:0|[1-9]\d{0,6})(?:\.\d{1,2})?", item.amount_yuan):
            amount_fen = int(Decimal(item.amount_yuan) * 100)
        if not 0 < amount_fen <= MAX_TOTAL_FEN:
            missing.append(prefix + "合法的人民币元金额（明确单位、最多两位小数）")
        items.append(
            {
                "category": item.category,
                "incurred_on": incurred_on,
                "description": item.description,
                "amount_fen": amount_fen,
            }
        )
    if missing:
        return missing, None
    if sum(item["amount_fen"] for item in items) > MAX_TOTAL_FEN:
        return ["合计超过演示系统上限，请修正明细金额"], None
    try:
        command = CreateExpenseInput(title=candidate.title, purpose=candidate.purpose, items=items)
        return [], command.model_dump(mode="json")
    except ValidationError:
        return ["请补充有效的标题、用途和费用明细"], None


def interpret(extractor: Extractor, context: dict) -> WorkflowState:
    def understand(state: WorkflowState):
        return {"extraction": extractor.extract(state["context"])}

    def check_fields(state: WorkflowState):
        missing, payload = validate_candidate(state["extraction"].candidate)
        return {"missing": missing, "payload": payload}

    graph = StateGraph(WorkflowState)
    graph.add_node("understand", understand)
    graph.add_node("check_fields", check_fields)
    graph.add_edge(START, "understand")
    graph.add_edge("understand", "check_fields")
    graph.add_edge("check_fields", END)
    # 每条消息至多一次模型调用，无模型驱动的开放工具循环。
    # P1 只记录脱敏本地事件，不受 shell 的 LangSmith 自动追踪开关影响。
    with tracing_context(enabled=False):
        return graph.compile().invoke({"context": context}, config={"recursion_limit": 5})
