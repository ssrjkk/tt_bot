import asyncio
import logging
from aiogram import Bot, Dispatcher
from src.config import load_settings
from src.database import DatabaseRepository
from src.services import AsyncWorkerPool, ReportGeneratorService
from src.handlers import router


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = load_settings()

    repo = DatabaseRepository(settings.db_path)
    await repo.init_db()

    worker_pool = AsyncWorkerPool(repo=repo, workers_count=settings.workers_count)
    await worker_pool.start()
    report_service = ReportGeneratorService(repo=repo)

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    dp["repo"] = repo
    dp["worker_pool"] = worker_pool
    dp["report_service"] = report_service
    dp.include_router(router)

    try:
        await dp.start_polling(bot)
    finally:
        await worker_pool.stop()
        await repo.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
