import asyncio
import logging

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import TelegramMethod

from app.config import settings

log = logging.getLogger(__name__)


class RetrySession(AiohttpSession):
    """Повторяет сетевые ошибки Telegram: связь до api.telegram.org нестабильна."""

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None):
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return await super().make_request(bot, method, timeout)
            except TelegramNetworkError as exc:
                last_error = exc
                log.warning("telegram retry %s: %s", attempt + 1, str(exc)[:120])
                await asyncio.sleep(1.5 * (attempt + 1))
        raise last_error  # type: ignore[misc]


def make_bot() -> Bot:
    """Telegram Bot API is blocked from Russian networks; set TELEGRAM_PROXY_URL if needed."""
    session = (
        AiohttpSession(proxy=settings.telegram_proxy_url)
        if settings.telegram_proxy_url
        else RetrySession()
    )
    return Bot(token=settings.telegram_bot_token, session=session)
