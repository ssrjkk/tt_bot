import sqlite3
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from src.database import DatabaseRepository
from src.models import ExecutionLogDTO, TaskDTO, UserDTO


@pytest.mark.asyncio
async def test_user_upsert_and_task_crud(db_repo):
    user = UserDTO(id=1001, username="ssrjkk", full_name="Sergey Sitnikov")
    await db_repo.upsert_user(user)

    task = TaskDTO(
        id=None,
        user_id=1001,
        title="Проверка RPC узла",
        category="rpc_node",
        target_endpoint="https://rpc.example.com/health",
        priority=3,
        check_interval_sec=60,
        expected_text="ok",
    )
    task_id = await db_repo.create_task(task)
    assert task_id == 1

    fetched = await db_repo.get_task_by_id(task_id, user_id=1001)
    assert fetched is not None
    assert fetched.expected_text == "ok"
    assert fetched.ssl_min_days == 14

    assert await db_repo.get_task_by_id(task_id, user_id=9999) is None
    assert await db_repo.update_task_priority(task_id, 1, user_id=9999) is False
    assert await db_repo.update_task_interval(task_id, 300, user_id=9999) is False
    assert await db_repo.delete_task(task_id, user_id=9999) is False

    assert await db_repo.update_task_interval(task_id, 300, user_id=1001) is True
    assert (await db_repo.get_task_by_id(task_id)).check_interval_sec == 300

    assert await db_repo.update_task_status(task_id, "completed") is True
    assert await db_repo.toggle_task_enabled(task_id, user_id=1001) is False
    assert await db_repo.toggle_task_enabled(task_id, user_id=9999) is None

    assert await db_repo.delete_task(task_id, user_id=1001) is True
    assert await db_repo.get_task_by_id(task_id) is None


@pytest.mark.asyncio
async def test_invalid_priority_and_interval_raise_error(db_repo):
    await db_repo.upsert_user(UserDTO(id=1002, username="u2", full_name="User 2"))
    task_id = await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=1002,
            title="Task",
            category="qa_test",
            target_endpoint="https://service.example/health",
        )
    )
    with pytest.raises(ValueError):
        await db_repo.update_task_priority(task_id, 5)
    with pytest.raises(ValueError):
        await db_repo.update_task_interval(task_id, -10)
    with pytest.raises(ValueError):
        await db_repo.update_task_threshold(task_id, 9)


@pytest.mark.asyncio
async def test_incidents_and_cascade_delete_logs(db_repo):
    await db_repo.upsert_user(UserDTO(id=1003, username="u3", full_name="User 3"))
    tid = await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=1003,
            title="API Check",
            category="backend",
            target_endpoint="https://api.example/health",
        )
    )
    await db_repo.record_check_result(
        task_id=tid,
        result_status="failed",
        latency_ms=500.0,
        details="HTTP 503",
        status_code=503,
        consecutive_failures=1,
        ssl_days_left=5,
    )
    incidents = await db_repo.get_recent_incidents(1003)
    assert len(incidents) == 1
    assert incidents[0]["title"] == "API Check"
    assert await db_repo.get_recent_incidents(9999) == []

    logs = await db_repo.get_task_logs(tid, user_id=1003)
    assert len(logs) == 1
    assert logs[0].ssl_days_left == 5

    await db_repo.delete_task(tid, user_id=1003)
    assert len(await db_repo.get_task_logs(tid)) == 0


@pytest.mark.asyncio
async def test_pagination_analytics_due_and_prune(db_repo):
    await db_repo.upsert_user(UserDTO(id=1005, username="u5", full_name="User 5"))
    empty = await db_repo.get_analytics_summary(1005)
    assert empty["uptime_24h_pct"] == 100.0

    old_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    tid = await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=1005,
            title="SLA API",
            category="backend",
            target_endpoint="https://sla.example",
            check_interval_sec=60,
            last_checked_at=old_ts,
        )
    )
    assert len(await db_repo.get_due_tasks()) == 1

    await db_repo.update_task_status(tid, "running")
    assert await db_repo.reset_stuck_running_tasks() == 1

    for lat in (10.0, 20.0, 30.0, 100.0):
        await db_repo.record_check_result(tid, "completed", lat, "200 OK", 200, 0)
    await db_repo.record_check_result(tid, "failed", 200.0, "500", 500, 1)

    summary = await db_repo.get_analytics_summary(1005)
    assert summary["uptime_24h_pct"] == 80.0
    assert summary["p95_latency_ms"] >= 100.0

    very_old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    await db_repo.add_execution_log(
        ExecutionLogDTO(
            id=None,
            task_id=tid,
            result_status="completed",
            latency_ms=10.0,
            details="old",
            executed_at=very_old,
        )
    )
    assert await db_repo.prune_old_logs(retention_days=30) == 1


@pytest.mark.asyncio
async def test_record_check_result_on_missing_task_returns_none(db_repo):
    await db_repo.upsert_user(UserDTO(id=1006, username="u6", full_name="User 6"))
    tid = await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=1006,
            title="Gone API",
            category="backend",
            target_endpoint="https://gone.example",
        )
    )
    assert await db_repo.delete_task(tid, user_id=1006) is True

    assert (
        await db_repo.record_check_result(tid, "failed", 12.0, "HTTP 503", 503, 1)
        is None
    )
    assert await db_repo.get_task_logs(tid) == []

    surviving = await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=1006,
            title="Still Here",
            category="backend",
            target_endpoint="https://alive.example",
        )
    )
    dto = await db_repo.record_check_result(surviving, "completed", 9.0, "200", 200, 0)
    assert dto is not None
    assert dto.id is not None
    assert dto.task_id == surviving
    assert len(await db_repo.get_task_logs(surviving)) == 1


@pytest.mark.asyncio
async def test_unwritable_result_rolls_back_without_half_written_state(db_repo):
    await db_repo.upsert_user(UserDTO(id=1007, username="u7", full_name="User 7"))
    tid = await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=1007,
            title="Atomic API",
            category="backend",
            target_endpoint="https://atomic.example",
        )
    )

    with pytest.raises(sqlite3.Error):
        await db_repo.record_check_result(tid, "failed", object(), "HTTP 503", 503, 4)

    task = await db_repo.get_task_by_id(tid)
    assert task.status == "pending"
    assert task.consecutive_failures == 0
    assert await db_repo.get_task_logs(tid) == []


@pytest.mark.asyncio
async def test_schema_migration_from_v1(tmp_path):
    legacy_db = tmp_path / "legacy_v1.db"
    async with aiosqlite.connect(str(legacy_db)) as conn:
        await conn.execute("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL
            );
            """)
        await conn.execute("""
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                target_endpoint TEXT NOT NULL DEFAULT '',
                priority INTEGER NOT NULL DEFAULT 2,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """)
        await conn.execute("""
            CREATE TABLE execution_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                result_status TEXT NOT NULL,
                latency_ms REAL NOT NULL,
                details TEXT NOT NULL,
                executed_at TEXT NOT NULL
            );
            """)
        await conn.execute(
            "INSERT INTO users VALUES (1, 'old', 'Old User', 'user', '2026-01-01')"
        )
        await conn.execute(
            "INSERT INTO tasks (user_id, title, category, target_endpoint, "
            "priority, status, created_at, updated_at) "
            "VALUES (1, 'Legacy Task', 'backend', 'https://old.example', "
            "2, 'pending', '2026-01-01', '2026-01-01')"
        )
        await conn.commit()

    repo = DatabaseRepository(str(legacy_db))
    await repo.init_db()
    try:
        task = await repo.get_task_by_id(1)
        assert task is not None
        assert task.expected_text == ""
        assert task.ssl_min_days == 14
        assert task.enabled is True
    finally:
        await repo.close()
