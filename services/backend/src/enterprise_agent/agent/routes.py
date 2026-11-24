"""所有任务访问与恢复都复用可信身份；不接受浏览器指定图节点或 resume 参数。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from enterprise_agent.agent import service
from enterprise_agent.agent.model import Extractor, get_extractor
from enterprise_agent.agent.schemas import (
    CancelRunRequest,
    ConfirmRunRequest,
    RunListQuery,
    RunListResponse,
    RunMessageRequest,
    RunResponse,
    StartRunRequest,
)
from enterprise_agent.api.routes import Actor, Db
from enterprise_agent.api.schemas import COMMON_ERROR_RESPONSES

# 下方装饰器中的相对路径统一拼接此前缀；这是 Python 内部 API，浏览器通过 BFF 访问。
router = APIRouter(prefix="/api/v1/agent/runs", tags=["agent"], responses=COMMON_ERROR_RESPONSES)
# FastAPI 根据类型声明注入依赖：Db 是请求级数据库会话，Actor 是服务端鉴权后的用户；
# Model 是提取器，只有新建任务和发送消息需要它。声明依赖本身不会发起模型调用。
Model = Annotated[Extractor, Depends(get_extractor)]


@router.get("", response_model=RunListResponse)
def list_tasks(query: Annotated[RunListQuery, Query()], db: Db, actor: Actor):
    # GET /api/v1/agent/runs?limit=20&offset=0：分页查看本人助手任务的摘要。
    # list_runs 按可信租户和所有者过滤，并按创建时间、ID 倒序返回；财务也不能看他人对话。
    # 只返回标题、状态、创建时间等摘要和分页信息，不读取完整对话，也不调用模型或创建草稿。
    return service.list_runs(db, actor, query)


@router.post("", response_model=RunResponse, status_code=201)
def start(payload: StartRunRequest, db: Db, actor: Actor, model: Model, request: Request):
    # POST /api/v1/agent/runs：接收首条 message，用 client_request_id 对新建请求去重。
    # start_run 在同一事务创建 run、首条消息和执行记录，再进入 continue_message / advance：
    # 缺项返回 collecting，信息完整返回 awaiting_confirmation 预览；此时尚未创建财务草稿。
    # 同标识同内容复用任务；同标识换内容返回 409。执行成功的重试只读现有结果，
    # 明确失败的有效末条消息可手动重试；running 不自动抢占。正常响应状态码为 201。
    # request_id 由请求中间件生成或校验，用于关联日志，与客户端业务去重标识不是一回事。
    return service.start_run(db, actor, payload, model, request.state.request_id)


@router.get("/{run_id}", response_model=RunResponse)
def get(run_id: UUID, db: Db, actor: Actor):
    # GET /api/v1/agent/runs/{run_id}：读取指定任务，供刷新页面、历史选择和结果核验使用。
    # load_run 同时校验任务 ID、当前租户和所有者；不存在或无权访问均返回 404，防止探测。
    # response 将持久 run 转成公开结构：对话、缺项、预览、版本和结果单号，不返回内部溯源数据。
    # 这里恢复的是已保存内容的展示，不推进计算、不重签确认，也不是 checkpoint 断点恢复。
    return service.response(service.load_run(db, actor, run_id))


@router.post("/{run_id}/messages", response_model=RunResponse)
def message(
    run_id: UUID, payload: RunMessageRequest, db: Db, actor: Actor, model: Model, request: Request
):
    # POST /api/v1/agent/runs/{run_id}/messages：给现有任务补充信息、修改费用或表达取消意图。
    # payload 包含 message_id、expected_version 和 message；send_message 先做消息去重，
    # 新消息再检查版本与允许状态，仅 collecting / awaiting_confirmation 可接受修改。
    # 接收消息、递增版本、撤销旧预览确认和创建 queued 执行记录同事务提交，之后才调用模型。
    # advance 保存本轮答复及执行结果；缺项继续追问，完整则生成新预览，识别取消则标记 cancelled。
    # 文字“确认”不能代替有效确认请求；executing / completed 任务不接受新修改。
    return service.send_message(db, actor, run_id, payload, model, request.state.request_id)


@router.post("/{run_id}/confirm", response_model=RunResponse)
def confirm(run_id: UUID, payload: ConfirmRunRequest, db: Db, actor: Actor, request: Request):
    # POST /api/v1/agent/runs/{run_id}/confirm：用户核对预览后，明确授权创建一张报销草稿。
    # payload 必须提供服务端签发的 confirmation_id、预览 expected_version 和 content_hash。
    # confirm_run 校验归属、内容哈希及有效期，再用条件更新将 awaiting_confirmation 变为 executing；
    # 领取确认成功后，以稳定确认 ID 作为幂等键调用受控工具，再由 finance 服务写主单、明细和审计。
    # 财务成功后回填 expense_id，任务变为 completed；同一确认重试返回同一单据。
    # 财务已提交但结果回填失败时保持 executing，重试原确认可对账。该接口不调用模型，
    # “确认创建草稿”不等于提交审批、财务批准或付款授权。
    return service.confirm_run(db, actor, run_id, payload, request.state.request_id)


@router.post("/{run_id}/cancel", response_model=RunResponse)
def cancel(run_id: UUID, payload: CancelRunRequest, db: Db, actor: Actor):
    # POST /api/v1/agent/runs/{run_id}/cancel：直接取消仍可编辑的任务，不需要模型理解自然语言。
    # cancel_run 校验归属和 expected_version，以条件更新标记 cancelled、递增版本并清空确认预览。
    # 已 cancelled 的请求可重复调用；executing / completed 或陈旧版本返回 409。
    # 取消不会删除对话，也不会撤销已提交的财务事实。若旧提取仍在返回途中，其结果无法通过
    # 原版本的条件更新，因此不能把已取消的任务重新写回待确认状态。
    return service.cancel_run(db, actor, run_id, payload.expected_version)
