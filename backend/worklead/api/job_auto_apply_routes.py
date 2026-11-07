"""Authenticated local controls. External UI sends use a different capability."""
from fastapi import APIRouter, Request, Response

from ..jobs.auto_apply import runtime
from ..jobs.auto_apply_models import AutoApplyStartIn, AutoApplyStartOut, AutoApplyStatus


def router():
    routes = APIRouter(prefix="/v1/jobs/auto-apply", tags=["job-auto-apply"])

    @routes.get("/status", response_model=AutoApplyStatus)
    def status(request: Request, response: Response):
        from ..jobs.managed_setup import current_status
        response.headers["Cache-Control"] = "no-store"
        return current_status(request.app.state.ctx)

    @routes.post("/setup", response_model=AutoApplyStatus)
    def setup(body: AutoApplyStartIn, request: Request, response: Response):
        from ..jobs.managed_setup import setup as setup_managed
        app = request.app.state.ctx
        with app.ai_control_lock:
            result = setup_managed(app, body)
        response.headers["Cache-Control"] = "no-store"
        return result

    @routes.post("/start", response_model=AutoApplyStartOut)
    def start(body: AutoApplyStartIn, request: Request, response: Response):
        from ..jobs.chrome_extension import prepare_extension_directory
        app = request.app.state.ctx
        with app.ai_control_lock:
            from ..jobs.managed_setup import use_chrome
            use_chrome(app)
            directory = prepare_extension_directory(app.config.data_dir)
            result = runtime(app).start(body)
            result["extension_directory"] = directory
        response.headers["Cache-Control"] = "no-store"
        return result

    @routes.post("/stop", response_model=AutoApplyStatus)
    def stop(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        app = request.app.state.ctx
        with app.ai_control_lock:
            from ..jobs.managed_setup import current_status, disable
            try:
                runtime(app).stop()
            finally:
                disable(app)
            return current_status(app)

    @routes.post("/forget", response_model=AutoApplyStatus)
    def forget(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        app = request.app.state.ctx
        with app.ai_control_lock:
            from ..jobs.managed_setup import current_status, disable
            try:
                runtime(app).forget()
            finally:
                disable(app)
            return current_status(app)

    return routes
