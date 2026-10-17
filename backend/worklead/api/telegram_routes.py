"""Phone alerts through the user's own Telegram bot. The token is write-only."""
from fastapi import APIRouter, Request

from ..services import telegram
from ..services.telegram import TelegramStatus, TelegramTokenIn


def router():
    routes = APIRouter(prefix="/v1/notifications/telegram", tags=["settings"])

    @routes.get("", response_model=TelegramStatus)
    def status(request: Request):
        return telegram.status(request.app.state.ctx)

    @routes.post("/token", response_model=TelegramStatus)
    def save_token(body: TelegramTokenIn, request: Request):
        return telegram.save_token(request.app.state.ctx, body.token.get_secret_value())

    @routes.post("/connect", response_model=TelegramStatus)
    def connect(request: Request):
        return telegram.connect(request.app.state.ctx)

    @routes.post("/delete", response_model=TelegramStatus)
    def delete(request: Request):
        return telegram.delete(request.app.state.ctx)

    return routes
