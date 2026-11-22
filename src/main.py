import asyncio
import html
import json
import logging
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from src.config import load_settings
from src.database import DatabaseRepository
from src.handlers import router
from src.middlewares import ThrottleMiddleware
from src.models import ExecutionLogDTO, TaskDTO
from src.probes import EndpointProber
from src.services import (
    AsyncWorkerPool,
    MonitorScheduler,
    ReportGeneratorService,
)

logger = logging.getLogger(__name__)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(log_format: str = "text") -> None:
    handler = logging.StreamHandler()
    if log_format == "json":
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
        )
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def build_alert_sender(bot: Bot):
    async def send_alert(
        task: TaskDTO, log_dto: ExecutionLogDTO, recovered: bool
    ) -> None:
        if recovered:
            header = f"🟢 <b>Восстановлен монитор #{task.id}</b>"
        else:
            header = f"🔴 <b>Инцидент на мониторе #{task.id}</b>"
        text = (
            f"{header}\n"
            f"▪️ Сервис: {html.escape(task.title)}\n"
            f"▪️ Эндпоинт: <code>{html.escape(task.target_endpoint)}</code>\n"
            f"▪️ Ответ: <code>{html.escape(log_dto.details)}</code>\n"
            f"▪️ Latency: <code>{log_dto.latency_ms} мс</code>"
        )
        await bot.send_message(task.user_id, text, parse_mode="HTML")

    return send_alert


async def register_bot_commands(bot: Bot) -> None:
    commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="add", description="Быстро добавить монитор"),
        BotCommand(command="expect", description="Проверка текста в ответе"),
        BotCommand(command="status", description="Быстрый статус мониторов"),
        BotCommand(command="incidents", description="Последние инциденты"),
        BotCommand(command="stats", description="Аналитика и SLA за 24ч"),
        BotCommand(command="export", description="Экспорт мониторов в CSV"),
        BotCommand(command="help", description="Справка и форматы эндпоинтов"),
        BotCommand(command="cancel", description="Отменить создание монитора"),
    ]
    await bot.set_my_commands(commands)


async def main() -> None:
    settings = load_settings(require_token=True)
    configure_logging(settings.log_format)

    repo = DatabaseRepository(settings.db_path)
    await repo.init_db()
    recovered_count = await repo.reset_stuck_running_tasks()
    if recovered_count:
        logger.info("Recovered %s stuck 'running' tasks on startup", recovered_count)

    bot = Bot(token=settings.bot_token)
    await register_bot_commands(bot)

    prober = EndpointProber(
        timeout_sec=settings.probe_timeout_sec,
        allow_private=settings.allow_private_endpoints,
    )
    worker_pool = AsyncWorkerPool(
        repo=repo,
        workers_count=settings.workers_count,
        prober=prober,
        alert_callback=build_alert_sender(bot),
    )
    scheduler = MonitorScheduler(
        repo=repo,
        worker_pool=worker_pool,
        tick_sec=settings.scheduler_tick_sec,
        retention_days=settings.log_retention_days,
    )
    report_service = ReportGeneratorService(repo=repo)

    await worker_pool.start()
    await scheduler.start()

    dp = Dispatcher()
    throttle = ThrottleMiddleware(rate_limit_sec=settings.throttle_rate_sec)
    dp.message.middleware(throttle)
    dp.callback_query.middleware(throttle)
    dp["repo"] = repo
    dp["worker_pool"] = worker_pool
    dp["report_service"] = report_service
    dp.include_router(router)

    try:
        await dp.start_polling(bot)
    finally:
        await scheduler.stop()
        await worker_pool.stop()
        await prober.close()
        await repo.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
