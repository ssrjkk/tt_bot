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
        assert task.parent_id is None

        log = await repo.record_check_result(
            task_id=1,
            result_status="failed",
            latency_ms=11.0,
            details="HTTP 500",
            status_code=500,
            consecutive_failures=1,
            suppressed=True,
            root_task_id=None,
        )
        assert log.suppressed is True
        assert (await repo.get_task_logs(1))[-1].suppressed is True
        assert (await repo.get_analytics_summary(1))["suppressed_24h"] == 1
    finally:
        await repo.close()


async def _mk(db_repo, user_id: int, title: str, endpoint: str = "") -> int:
    return await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=user_id,
            title=title,
            category="backend",
            target_endpoint=endpoint or f"https://{title.lower()}.example",
        )
    )


@pytest.mark.asyncio
async def test_parent_link_rejects_self_foreign_user_and_cycle(db_repo):
    await db_repo.upsert_user(UserDTO(id=4101, username="a", full_name="A"))
    await db_repo.upsert_user(UserDTO(id=4102, username="b", full_name="B"))
    own = await _mk(db_repo, 4101, "own")
    other = await _mk(db_repo, 4102, "other")

    with pytest.raises(ValueError, match="сам себе"):
        await db_repo.set_task_parent(own, own, user_id=4101)
    with pytest.raises(ValueError, match="другому пользователю"):
        await db_repo.set_task_parent(own, other, user_id=4101)
    with pytest.raises(ValueError, match="другому пользователю"):
        await db_repo.set_task_parent(own, 99999, user_id=4101)

    # Чужой монитор нельзя перепривязать, даже зная его id.
    assert await db_repo.set_task_parent(other, own, user_id=4101) is False
    assert (await db_repo.get_task_by_id(other)).parent_id is None

    child = await _mk(db_repo, 4101, "child")
    assert await db_repo.set_task_parent(child, own, user_id=4101) is True
    assert (await db_repo.get_task_by_id(child)).parent_id == own
    with pytest.raises(ValueError, match="цикл"):
        await db_repo.set_task_parent(own, child, user_id=4101)
    assert (await db_repo.get_task_by_id(own)).parent_id is None

    assert await db_repo.set_task_parent(child, None, user_id=4101) is True
    assert (await db_repo.get_task_by_id(child)).parent_id is None


@pytest.mark.asyncio
async def test_link_chain_stops_at_max_tree_depth(db_repo):
    from src.database import MAX_TREE_DEPTH

    await db_repo.upsert_user(UserDTO(id=4103, username="d", full_name="D"))
    ids = [await _mk(db_repo, 4103, "root")]
    links = 0
    for idx in range(1, MAX_TREE_DEPTH + 5):
        tid = await _mk(db_repo, 4103, f"node{idx}")
        try:
            await db_repo.set_task_parent(tid, ids[-1], user_id=4103)
        except ValueError as exc:
            assert f"Цепочка длиннее {MAX_TREE_DEPTH}" in str(exc)
            break
        ids.append(tid)
        links += 1
    assert links == MAX_TREE_DEPTH


@pytest.mark.asyncio
async def test_find_root_cause_picks_topmost_failed_ancestor(db_repo):
    await db_repo.upsert_user(UserDTO(id=4104, username="r", full_name="R"))
    top = await _mk(db_repo, 4104, "edge")
    mid = await _mk(db_repo, 4104, "api")
    leaf = await _mk(db_repo, 4104, "worker")
    await db_repo.set_task_parent(mid, top, user_id=4104)
    await db_repo.set_task_parent(leaf, mid, user_id=4104)

    assert await db_repo.find_root_cause(leaf) is None
    await db_repo.update_task_status(mid, "failed")
    assert (await db_repo.find_root_cause(leaf)).id == mid
    await db_repo.update_task_status(top, "failed")
    assert (await db_repo.find_root_cause(leaf)).id == top
    assert (await db_repo.find_root_cause(mid)).id == top
    assert await db_repo.find_root_cause(top) is None

    # Выключенный предок со статусом failed — устаревшие данные, не причина.
    await db_repo.toggle_task_enabled(top, user_id=4104)
    assert (await db_repo.find_root_cause(leaf)).id == mid


@pytest.mark.asyncio
async def test_hand_written_cycle_does_not_hang_ancestor_walk(db_repo):
    await db_repo.upsert_user(UserDTO(id=4105, username="c", full_name="C"))
    a = await _mk(db_repo, 4105, "a")
    b = await _mk(db_repo, 4105, "b")
    await db_repo.set_task_parent(b, a, user_id=4105)
    await db_repo._db.execute("UPDATE tasks SET parent_id = ? WHERE id = ?", (b, a))
    await db_repo._db.execute("UPDATE tasks SET status = 'failed'")
    await db_repo._db.commit()

    chain = await db_repo._ancestor_chain(a)
    assert [node.id for node in chain] == [b]
    assert (await db_repo.find_root_cause(a)).id == b


@pytest.mark.asyncio
async def test_delete_parent_detaches_children(db_repo):
    await db_repo.upsert_user(UserDTO(id=4106, username="p", full_name="P"))
    parent = await _mk(db_repo, 4106, "parent")
    child = await _mk(db_repo, 4106, "child")
    await db_repo.set_task_parent(child, parent, user_id=4106)

    assert await db_repo.delete_task(parent, user_id=4106) is True
    assert (await db_repo.get_task_by_id(child)).parent_id is None
    assert await db_repo.find_root_cause(child) is None


@pytest.mark.asyncio
async def test_suppression_is_persisted_and_surfaced(db_repo):
    await db_repo.upsert_user(UserDTO(id=4107, username="s", full_name="S"))
    root = await _mk(db_repo, 4107, "proxy", "https://proxy.example")
    symptom = await _mk(db_repo, 4107, "api", "https://api.example")
    await db_repo.set_task_parent(symptom, root, user_id=4107)

    log = await db_repo.record_check_result(
        task_id=symptom,
        result_status="failed",
        latency_ms=42.0,
        details="HTTP 502",
        status_code=502,
        consecutive_failures=2,
        suppressed=True,
        root_task_id=root,
    )
    assert log.suppressed is True
    assert log.root_task_id == root

    incidents = await db_repo.get_recent_incidents(4107, limit=5)
    assert incidents[0]["suppressed"] == 1
    assert incidents[0]["root_id"] == root
    assert incidents[0]["root_title"] == "proxy"

    summary = await db_repo.get_analytics_summary(4107)
    assert summary["suppressed_24h"] == 1

    plain = await db_repo.record_check_result(
        task_id=root,
        result_status="failed",
        latency_ms=10.0,
        details="HTTP 500",
        status_code=500,
        consecutive_failures=2,
    )
    assert plain.suppressed is False
    assert plain.root_task_id is None
    assert (await db_repo.get_analytics_summary(4107))["suppressed_24h"] == 1
