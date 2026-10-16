"""User-started browser session. No remote chat API or credential input."""
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from ..jobs.messages import MessageStatus, runtime


class StartMessageWatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: Literal[True]


def router():
    routes = APIRouter(prefix="/v1/jobs/messages", tags=["job-messages"])

    @routes.get("/status", response_model=MessageStatus)
    def status(request: Request):
        return runtime(request.app.state.ctx).status()

    @routes.post("/start", response_model=MessageStatus)
    def start(body: StartMessageWatch, request: Request):
        return runtime(request.app.state.ctx).start()

    @routes.post("/stop", response_model=MessageStatus)
    def stop(request: Request):
        return runtime(request.app.state.ctx).stop()

    return routes
