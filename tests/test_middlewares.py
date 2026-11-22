from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import CallbackQuery, Message, User

from src.middlewares import ThrottleMiddleware


@pytest.mark.asyncio
async def test_throttle_blocks_rapid_callback_and_allows_after_reset():
    mw = ThrottleMiddleware(rate_limit_sec=1.0)
    handler = AsyncMock(return_value="ok")

    call = MagicMock(spec=CallbackQuery)
    call.from_user = User(id=77, is_bot=False, first_name="U")
    call.answer = AsyncMock()

    res1 = await mw(handler, call, {})
    assert res1 == "ok"
    assert handler.await_count == 1

    res2 = await mw(handler, call, {})
    assert res2 is None
    assert handler.await_count == 1
    call.answer.assert_awaited_once()

    # Different user is not throttled
    msg = MagicMock(spec=Message)
    msg.from_user = User(id=88, is_bot=False, first_name="U2")
    res3 = await mw(handler, msg, {})
    assert res3 == "ok"
    assert handler.await_count == 2


@pytest.mark.asyncio
async def test_throttle_disabled_and_no_user_passthrough():
    mw_zero = ThrottleMiddleware(rate_limit_sec=0.0)
    handler = AsyncMock(return_value="pass")
    ev = MagicMock()
    ev.from_user = None
    assert await mw_zero(handler, ev, {}) == "pass"

    mw_on = ThrottleMiddleware(rate_limit_sec=0.5)
    assert await mw_on(handler, ev, {}) == "pass"
