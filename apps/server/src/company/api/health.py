"""Health endpoints check infrastructure only and never call a model."""

from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

router = APIRouter(prefix="/health", tags=["health"])


class LiveResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["company-api"] = "company-api"
    milestone: Literal["M7"] = "M7"


class ReadyResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    database: Literal["connected", "unavailable"]
    schema_state: Literal["initialized", "not_initialized", "unknown"] = Field(alias="schema")
    message: str
    business_workflow_implemented: Literal[True] = True
    intake_workflow_implemented: Literal[True] = True
    real_amazon_implemented: Literal[False] = False
    model_mode: Literal["live", "fake"]
    publish_mode: Literal["simulated", "sandbox", "production"]


@router.get("/live", response_model=LiveResponse)
async def live() -> LiveResponse:
    return LiveResponse()


@router.get("/ready", response_model=ReadyResponse, responses={503: {"model": ReadyResponse}})
async def ready(request: Request, response: Response) -> ReadyResponse:
    check = await request.app.state.database.readiness()
    response.status_code = 200 if check.ready else 503
    return ReadyResponse(
        status="ready" if check.ready else "not_ready",
        database=check.database,
        schema=check.schema,
        message=check.message,
        model_mode=request.app.state.settings.mimo_mode,
        publish_mode=request.app.state.settings.publish_mode,
    )

