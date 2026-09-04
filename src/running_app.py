from telegram import Bot
from telegram.ext import Application

_running_app: Application | None = None


def set_running_app(application: Application) -> None:
    global _running_app
    _running_app = application


def get_bot() -> Bot:
    assert _running_app, 'Application not initialized'
    return _running_app.bot
