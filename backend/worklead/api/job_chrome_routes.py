"""Main-app-authenticated setup for the separate Chrome observation bridge."""
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from ..jobs.chrome_bridge import runtime
from ..jobs.messages import MessageStatus
from ..jobs.applications import ApplicationError
from .job_message_routes import StartMessageWatch


class ChromeConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    token: str = Field(repr=False)


class ChromeStart(BaseModel):
    connection: ChromeConnection
    status: MessageStatus
    extension_directory: str


class ChromeStartIn(StartMessageWatch):
    capture_visible_chat: StrictBool = False


def router():
    routes = APIRouter(prefix="/v1/jobs/messages/chrome", tags=["job-messages"])

    @routes.get("/status", response_model=MessageStatus)
    def status(request: Request):
        return runtime(request.app.state.ctx).status()

    @routes.post("/start", response_model=ChromeStart)
    def start(body: ChromeStartIn, request: Request, response: Response):
        from ..jobs.chrome_extension import prepare_extension_directory
        app = request.app.state.ctx
        with app.ai_control_lock:
            edge = app._jobs_message_runtime
            if edge is not None and edge.status().enabled:
                raise ApplicationError(409, "edge_watch_active", "전용 Edge 감시를 중지한 뒤 기존 크롬을 연결하세요")
            directory = prepare_extension_directory(app.config.data_dir)
            bridge = runtime(app)
            bridge.stop()
            result = bridge.start(capture_visible_chat=body.capture_visible_chat)
            app.jobs_message_provider = "chrome"
            result["extension_directory"] = str(directory)
        response.headers["Cache-Control"] = "no-store"
        return result

    @routes.post("/stop", response_model=MessageStatus)
    def stop(request: Request):
        return runtime(request.app.state.ctx).stop()

    return routes
