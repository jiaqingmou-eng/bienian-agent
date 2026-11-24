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

router = APIRouter(prefix="/api/v1/agent/runs", tags=["agent"], responses=COMMON_ERROR_RESPONSES)
Model = Annotated[Extractor, Depends(get_extractor)]


@router.get("", response_model=RunListResponse)
def list_tasks(query: Annotated[RunListQuery, Query()], db: Db, actor: Actor):
    return service.list_runs(db, actor, query)


@router.post("", response_model=RunResponse, status_code=201)
def start(payload: StartRunRequest, db: Db, actor: Actor, model: Model, request: Request):
    return service.start_run(db, actor, payload, model, request.state.request_id)


@router.get("/{run_id}", response_model=RunResponse)
def get(run_id: UUID, db: Db, actor: Actor):
    return service.response(service.load_run(db, actor, run_id))


@router.post("/{run_id}/messages", response_model=RunResponse)
def message(
    run_id: UUID, payload: RunMessageRequest, db: Db, actor: Actor, model: Model, request: Request
):
    return service.send_message(db, actor, run_id, payload, model, request.state.request_id)


@router.post("/{run_id}/confirm", response_model=RunResponse)
def confirm(run_id: UUID, payload: ConfirmRunRequest, db: Db, actor: Actor, request: Request):
    return service.confirm_run(db, actor, run_id, payload, request.state.request_id)


@router.post("/{run_id}/cancel", response_model=RunResponse)
def cancel(run_id: UUID, payload: CancelRunRequest, db: Db, actor: Actor):
    return service.cancel_run(db, actor, run_id, payload.expected_version)
