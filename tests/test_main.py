import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.main import (
    JsonLogFormatter,
    build_alert_sender,
    configure_logging,
    register_bot_commands,
)
from src.models import ExecutionLogDTO, TaskDTO


def test_json_log_formatter_and_configure():
    fmt = JsonLogFormatter()
    rec = logging.LogRecord(
        "tt_bot", logging.INFO, "x.py", 1, "hello %s", ("ok",), None
    )
    parsed = json.loads(fmt.format(rec))
    assert parsed["level"] == "INFO"
    assert parsed["msg"] == "hello ok"

    configure_logging("json")
    configure_logging("text")


@pytest.mark.asyncio
async def test_build_alert_sender_incident_and_recovery():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    sender = build_alert_sender(bot)

    task = TaskDTO(
        id=7,
        user_id=42,
        title="<Prod> API",
        category="backend",
        target_endpoint="https://api.example.com/health",
    )
    log_fail = ExecutionLogDTO(
        id=1,
        task_id=7,
        result_status="failed",
        latency_ms=5000.0,
        details="Timeout <5s>",
    )
    await sender(task, log_fail, False)
    text_fail = bot.send_message.call_args[0][1]
    assert "Инцидент" in text_fail
    assert "&lt;Prod&gt; API" in text_fail

    log_ok = ExecutionLogDTO(
        id=2,
        task_id=7,
        result_status="completed",
        latency_ms=32.5,
        details="HTTP 200 OK",
    )
    await sender(task, log_ok, True)
    assert "Восстановлен" in bot.send_message.call_args[0][1]


@pytest.mark.asyncio
async def test_register_bot_commands():
    bot = MagicMock()
    bot.set_my_commands = AsyncMock()
    await register_bot_commands(bot)
    commands = bot.set_my_commands.call_args[0][0]
    names = {cmd.command for cmd in commands}
    assert {
        "start",
        "add",
        "expect",
        "status",
        "incidents",
        "stats",
        "export",
        "help",
        "cancel",
    } <= names
