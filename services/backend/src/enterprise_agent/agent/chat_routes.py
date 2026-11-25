"""对话编排 HTTP 入口；原有任务读取、确认和取消接口继续承担明确的业务操作。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from enterprise_agent.agent.chat_model import ChatModel, get_chat_model
from enterprise_agent.agent.chat_schemas import ChatRequest, ChatResponse
from enterprise_agent.agent.model import Extractor, get_extractor
from enterprise_agent.agent.orchestrator import chat
from enterprise_agent.api.routes import Actor, Db
from enterprise_agent.api.schemas import COMMON_ERROR_RESPONSES

router = APIRouter(prefix="/api/v1/agent", tags=["agent"], responses=COMMON_ERROR_RESPONSES)


@router.post("/chat", response_model=ChatResponse)
def converse(
    payload: ChatRequest,
    db: Db,
    actor: Actor,
    request: Request,
    model: Annotated[ChatModel, Depends(get_chat_model)],
    extractor: Annotated[Extractor, Depends(get_extractor)],
):
    """POST /api/v1/agent/chat：所有新 UI 自然语言消息先经过此入口。

    接收稳定 message_id、message，以及可选的成对 run_id / expected_version；
    租户和用户只来自鉴权依赖。制度咨询不写报销任务，报销分支复用原有服务和去重。
    返回 kind 区分任务更新、政策回答和澄清提示；均为 HTTP 200，调用失败使用错误响应。
    此接口不等同于确认授权；创建财务草稿仍必须走带确认凭据的 /runs/{id}/confirm。
    """
    return chat(db, actor, payload, model, extractor, request.state.request_id)
