from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

from app.config import settings


def make_bot() -> Bot:
    """Telegram Bot API is blocked from Russian networks; set TELEGRAM_PROXY_URL if needed."""
    session = (
        AiohttpSession(proxy=settings.telegram_proxy_url)
        if settings.telegram_proxy_url
        else None
    )
    return Bot(token=settings.telegram_bot_token, session=session)
