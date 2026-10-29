import pytest
import pytest_asyncio
from src.database import DatabaseRepository
from src.services import AsyncWorkerPool, ReportGeneratorService


@pytest_asyncio.fixture
async def db_repo(tmp_path):
    db_file = tmp_path / "test_var18.db"
    repo = DatabaseRepository(str(db_file))
    await repo.init_db()
    try:
        yield repo
    finally:
        await repo.close()


@pytest_asyncio.fixture
async def worker_pool(db_repo):
    pool = AsyncWorkerPool(repo=db_repo, workers_count=2)
    await pool.start()
    yield pool
    await pool.stop()


@pytest.fixture
def report_service(db_repo):
    return ReportGeneratorService(repo=db_repo)
