"""Authenticated local controls. External UI sends use a different capability."""
from fastapi import APIRouter, Request, Response

from ..jobs.auto_apply import runtime
from ..jobs.auto_apply_models import AutoApplyStartIn, AutoApplyStartOut, AutoApplyStatus


def router():
    routes = APIRouter(prefix="/v1/jobs/auto-apply", tags=["job-auto-apply"])

    @routes.get("/status", response_model=AutoApplyStatus)
    def status(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return runtime(request.app.state.ctx).status()

    @routes.post("/start", response_model=AutoApplyStartOut)
    def start(body: AutoApplyStartIn, request: Request, response: Response):
        from ..jobs.chrome_extension import prepare_extension_directory
        app = request.app.state.ctx
        with app.ai_control_lock:
            directory = prepare_extension_directory(app.config.data_dir)
            result = runtime(app).start(body)
            result["extension_directory"] = directory
        response.headers["Cache-Control"] = "no-store"
        return result

    @routes.post("/stop", response_model=AutoApplyStatus)
    def stop(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return runtime(request.app.state.ctx).stop()

    return routes
