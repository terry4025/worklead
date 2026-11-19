"""User-started browser session. No remote chat API or credential input."""
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict

from ..jobs.messages import MessageStatus, runtime


class StartMessageWatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: Literal[True]


def router():
    routes = APIRouter(prefix="/v1/jobs/messages", tags=["job-messages"])

    @routes.get("/status", response_model=MessageStatus)
    def status(request: Request):
        ctx = request.app.state.ctx
        if ctx.jobs_message_provider == "chrome" and ctx._jobs_chrome_runtime is not None:
            return ctx._jobs_chrome_runtime.status()
        return runtime(ctx).status()

    from ..jobs.chat_view import ChatView

    @routes.get("/inbox", response_model=ChatView)
    def inbox(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        ctx = request.app.state.ctx
        return ctx._jobs_chrome_runtime.chat_view() if ctx._jobs_chrome_runtime is not None else ChatView()

    @routes.post("/start", response_model=MessageStatus)
    def start(body: StartMessageWatch, request: Request):
        from ..jobs.applications import ApplicationError
        ctx = request.app.state.ctx
        with ctx.ai_control_lock:
            if ctx._jobs_chrome_runtime is not None and ctx._jobs_chrome_runtime.status().enabled:
                raise ApplicationError(409, "message_watch_active", "크롬 감시를 먼저 중지한 뒤 Edge를 연결하세요")
            if ctx._jobs_message_pairing_runtime is not None:
                ctx._jobs_message_pairing_runtime.disable()
            ctx.jobs_message_provider = "edge"
            return runtime(ctx).start()

    @routes.post("/stop", response_model=MessageStatus)
    def stop(request: Request):
        ctx = request.app.state.ctx
        with ctx.ai_control_lock:
            if ctx._jobs_chrome_runtime is not None:
                ctx._jobs_chrome_runtime.stop()
            result = runtime(ctx).stop()
            return ctx._jobs_chrome_runtime.status() if ctx.jobs_message_provider == "chrome" and ctx._jobs_chrome_runtime is not None else result

    return routes
