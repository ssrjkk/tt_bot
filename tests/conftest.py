import pytest
import pytest_asyncio

from src.database import DatabaseRepository
from src.models import ProbeResult
from src.services import AsyncWorkerPool, ReportGeneratorService


async def fake_probe(endpoint: str) -> ProbeResult:
    if not endpoint or "fail" in endpoint.lower() or "error" in endpoint.lower():
        return ProbeResult(
            ok=False, details="HTTP 500 Internal Server Error", status_code=500
        )
    return ProbeResult(
        ok=True, details="HTTP 200 OK", status_code=200, ssl_days_left=84
    )


@pytest_asyncio.fixture
async def db_repo(tmp_path):
    db_file = tmp_path / "test_tt_bot_v2_1.db"
    repo = DatabaseRepository(str(db_file))
    await repo.init_db()
    try:
        yield repo
    finally:
        await repo.close()


@pytest_asyncio.fixture
async def worker_pool(db_repo):
    pool = AsyncWorkerPool(repo=db_repo, workers_count=2, probe_func=fake_probe)
    await pool.start()
    try:
        yield pool
    finally:
        await pool.stop()


@pytest.fixture
def report_service(db_repo):
    return ReportGeneratorService(repo=db_repo)
