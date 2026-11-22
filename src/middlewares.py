import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject


class ThrottleMiddleware(BaseMiddleware):
    """Защита от флуда: ограничивает частоту запросов на пользователя."""

    def __init__(self, rate_limit_sec: float = 0.4) -> None:
        self.rate_limit_sec = max(0.0, rate_limit_sec)
        self._last_seen: Dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if self.rate_limit_sec <= 0:
            return await handler(event, data)

        user = getattr(event, "from_user", None)
        if user is None:
            return await handler(event, data)

        now = time.monotonic()
        prev = self._last_seen.get(user.id)
        if prev is not None and (now - prev) < self.rate_limit_sec:
            if isinstance(event, CallbackQuery):
                await event.answer("Слишком часто — подождите секунду")
            return None

        self._last_seen[user.id] = now
        if len(self._last_seen) > 4096:
            cutoff = now - max(60.0, self.rate_limit_sec * 10)
            self._last_seen = {
                uid: ts for uid, ts in self._last_seen.items() if ts >= cutoff
            }
        return await handler(event, data)
