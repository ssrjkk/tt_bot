import asyncio
import sqlite3

import pytest

from src.models import ProbeResult, TaskDTO, UserDTO
from src.services import AsyncWorkerPool, MonitorScheduler, _sanitize_csv_cell


@pytest.mark.asyncio
async def test_worker_pool_run_all_dedup_and_exports(
    db_repo, worker_pool, report_service
):
    await db_repo.upsert_user(
        UserDTO(id=2001, username="ssrjkk", full_name="Sergey Sitnikov")
    )
    t1 = await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=2001,
            title="Good Node",
            category="rpc_node",
            target_endpoint="https://ok.node.example",
        )
    )
    await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=2001,
            title="Bad Node",
            category="rpc_node",
            target_endpoint="https://fail.node.example",
        )
    )
    await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=2001,
            title="Paused Node",
            category="rpc_node",
            target_endpoint="https://paused.example",
            enabled=False,
        )
    )

    queued = await worker_pool.run_all_user_tasks(2001)
    assert queued == 2
    await worker_pool.queue.join()

    summary = await db_repo.get_analytics_summary(2001)
    assert summary["total"] == 3
    assert summary["completed"] == 1
    assert summary["failed"] == 1
    assert summary["uptime_24h_pct"] == 50.0

    csv_text = await report_service.export_user_tasks_csv(2001)
    assert "Good Node" in csv_text

    logs_csv = await report_service.export_task_logs_csv(2001, t1)
    assert logs_csv is not None
    assert "SSL_Days" in logs_csv
    assert "84" in logs_csv
    assert await report_service.export_task_logs_csv(9999, t1) is None


@pytest.mark.asyncio
async def test_probe_exception_does_not_kill_worker(db_repo):
    async def crashing_probe(_endpoint: str):
        raise RuntimeError("socket exploded")

    await db_repo.upsert_user(UserDTO(id=2002, username="crash", full_name="Crash"))
    tid = await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=2002,
            title="Crash Task",
            category="backend",
            target_endpoint="https://boom.example",
        )
    )

    pool = AsyncWorkerPool(repo=db_repo, workers_count=1, probe_func=crashing_probe)
    await pool.start()
    try:
        await pool.enqueue_task(tid)
        await asyncio.wait_for(pool.queue.join(), timeout=2.0)

        task = await db_repo.get_task_by_id(tid)
        assert task.status == "failed"
        assert pool._workers[0].done() is False
    finally:
        await pool.stop(drain_timeout=2.0)


@pytest.mark.asyncio
async def test_worker_pool_stop_drain_timeout(db_repo):
    started = asyncio.Event()

    async def slow_probe(_ep: str):
        started.set()
        await asyncio.sleep(5.0)
        return True, "ok"

    await db_repo.upsert_user(UserDTO(id=2006, username="slow", full_name="Slow"))
    tid = await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=2006,
            title="Slow Task",
            category="backend",
            target_endpoint="https://slow.example",
        )
    )
    pool = AsyncWorkerPool(repo=db_repo, workers_count=1, probe_func=slow_probe)
    await pool.start()
    await pool.enqueue_task(tid)
    await asyncio.wait_for(started.wait(), timeout=2.0)
    await pool.stop(drain_timeout=0.05)
    assert pool._workers == []


@pytest.mark.asyncio
async def test_alert_threshold_and_recovery_notifications(db_repo):
    state = {"should_fail": True}
    events = []

    async def dynamic_probe(_ep: str) -> ProbeResult:
        if state["should_fail"]:
            return ProbeResult(ok=False, details="HTTP 502", status_code=502)
        return ProbeResult(ok=True, details="HTTP 200 OK", status_code=200)

    async def on_alert(task, log_dto, recovered: bool) -> None:
        events.append((task.id, recovered, log_dto.result_status))

    await db_repo.upsert_user(UserDTO(id=2003, username="al", full_name="Alert User"))
    tid = await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=2003,
            title="Critical API",
            category="backend",
            target_endpoint="https://crit.example",
            alert_threshold=2,
        )
    )

    pool = AsyncWorkerPool(
        repo=db_repo,
        workers_count=1,
        probe_func=dynamic_probe,
        alert_callback=on_alert,
    )
    await pool.execute_task_immediately(tid)
    assert events == []

    await pool.execute_task_immediately(tid)
    assert events == [(tid, False, "failed")]

    await pool.execute_task_immediately(tid)
    assert len(events) == 1

    state["should_fail"] = False
    await pool.execute_task_immediately(tid)
    assert events == [(tid, False, "failed"), (tid, True, "completed")]


@pytest.mark.asyncio
async def test_failed_write_restores_status_and_keeps_task_schedulable(
    db_repo, monkeypatch
):
    async def ok_probe(_ep: str) -> ProbeResult:
        return ProbeResult(ok=True, details="HTTP 200 OK", status_code=200)

    await db_repo.upsert_user(UserDTO(id=2007, username="wr", full_name="Write Fail"))
    tid = await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=2007,
            title="Flaky Storage",
            category="backend",
            target_endpoint="https://ok.example",
            check_interval_sec=60,
        )
    )
    assert (await db_repo.get_task_by_id(tid)).status == "pending"

    async def broken_write(**_kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(db_repo, "record_check_result", broken_write)
    pool = AsyncWorkerPool(repo=db_repo, workers_count=1, probe_func=ok_probe)

    assert await pool.execute_task_immediately(tid) is None

    task = await db_repo.get_task_by_id(tid)
    assert task.status == "pending"
    assert task.last_checked_at is None
    assert tid in [t.id for t in await db_repo.get_due_tasks()]


@pytest.mark.asyncio
async def test_monitor_deleted_mid_check_returns_none_without_orphan_log(db_repo):
    await db_repo.upsert_user(UserDTO(id=2008, username="gone", full_name="Gone"))
    tid = await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=2008,
            title="Deleted Mid Check",
            category="backend",
            target_endpoint="https://race.example",
            check_interval_sec=60,
        )
    )

    async def delete_during_probe(_ep: str) -> ProbeResult:
        assert await db_repo.delete_task(tid, user_id=2008) is True
        return ProbeResult(ok=False, details="HTTP 503", status_code=503)

    pool = AsyncWorkerPool(
        repo=db_repo, workers_count=1, probe_func=delete_during_probe
    )
    assert await pool.execute_task_immediately(tid) is None
    assert await db_repo.get_task_by_id(tid) is None
    assert await db_repo.get_task_logs(tid) == []
    assert await db_repo.get_due_tasks() == []


@pytest.mark.asyncio
async def test_scheduler_start_stop(db_repo, worker_pool):
    await db_repo.upsert_user(UserDTO(id=2004, username="sch", full_name="Sched"))
    tid = await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=2004,
            title="Scheduled API",
            category="backend",
            target_endpoint="https://ok.sched.example",
            check_interval_sec=60,
        )
    )
    scheduler = MonitorScheduler(repo=db_repo, worker_pool=worker_pool, tick_sec=60.0)
    await scheduler.start()
    await asyncio.sleep(0.05)
    await scheduler.stop()
    await asyncio.wait_for(worker_pool.queue.join(), timeout=2.0)

    task = await db_repo.get_task_by_id(tid)
    assert task.status == "completed"


@pytest.mark.asyncio
async def test_csv_formula_injection_sanitization(db_repo, report_service):
    assert _sanitize_csv_cell("=SUM(A1:A2)") == "'=SUM(A1:A2)"
    assert _sanitize_csv_cell("+cmd") == "'+cmd"
    assert _sanitize_csv_cell("-1+1") == "'-1+1"
    assert _sanitize_csv_cell("@evil") == "'@evil"
    assert _sanitize_csv_cell("Normal Title") == "Normal Title"

    await db_repo.upsert_user(UserDTO(id=2005, username="csv", full_name="CSV"))
    await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=2005,
            title='=HYPERLINK("http://evil.example","CLICK")',
            category="devops",
            target_endpoint="https://vps.example",
        )
    )
    csv_text = await report_service.export_user_tasks_csv(2005)
    assert "'=HYPERLINK" in csv_text


def _tree_task(user_id: int, title: str, endpoint: str) -> TaskDTO:
    return TaskDTO(
        id=None,
        user_id=user_id,
        title=title,
        category="backend",
        target_endpoint=endpoint,
        alert_threshold=2,
    )


def test_alert_event_fires_only_on_threshold_edges():
    task = TaskDTO(
        id=None,
        user_id=1,
        title="t",
        category="backend",
        target_endpoint="https://t.example",
        alert_threshold=2,
    )
    assert AsyncWorkerPool._alert_event(task, 0, 1) is None
    assert AsyncWorkerPool._alert_event(task, 1, 2) is False
    assert AsyncWorkerPool._alert_event(task, 2, 3) is None
    assert AsyncWorkerPool._alert_event(task, 3, 0) is True
    assert AsyncWorkerPool._alert_event(task, 1, 0) is None


@pytest.mark.asyncio
async def test_dependency_tree_keeps_one_alert_per_root_cause(db_repo):
    state = {"proxy": False, "api": False}
    events = []

    async def probe(endpoint: str) -> ProbeResult:
        key = "proxy" if "proxy" in endpoint else "api"
        ok = state[key]
        return ProbeResult(
            ok=ok,
            details="HTTP 200 OK" if ok else "HTTP 503 Unavailable",
            status_code=200 if ok else 503,
        )

    async def on_alert(task, log_dto, recovered: bool) -> None:
        events.append((task.id, recovered, log_dto.suppressed))

    await db_repo.upsert_user(UserDTO(id=2010, username="tree", full_name="Tree"))
    proxy = await db_repo.create_task(
        _tree_task(2010, "Edge proxy", "https://proxy.example")
    )
    api = await db_repo.create_task(_tree_task(2010, "Pay API", "https://api.example"))
    assert await db_repo.set_task_parent(api, proxy, user_id=2010) is True

    pool = AsyncWorkerPool(
        repo=db_repo, workers_count=1, probe_func=probe, alert_callback=on_alert
    )
    for _ in range(2):
        await pool.execute_task_immediately(proxy)
    assert events == [(proxy, False, False)]

    for _ in range(2):
        await pool.execute_task_immediately(api)
    assert events == [(proxy, False, False)]

    symptom_log = (await db_repo.get_task_logs(api))[0]
    assert symptom_log.suppressed is True
    assert symptom_log.root_task_id == proxy
    assert (await db_repo.get_analytics_summary(2010))["suppressed_24h"] == 1

    # Ребёнок починился, причина ещё лежит — «восстановлено» не шлём.
    state["api"] = True
    await pool.execute_task_immediately(api)
    assert events == [(proxy, False, False)]
    assert (await db_repo.get_task_by_id(api)).status == "completed"

    state["proxy"] = True
    await pool.execute_task_immediately(proxy)
    assert [(tid, rec) for tid, rec, _ in events] == [
        (proxy, False),
        (proxy, True),
    ]

    # Ниже здоровой причины падение снова объявляется как отдельный инцидент.
    state["api"] = False
    for _ in range(2):
        await pool.execute_task_immediately(api)
    assert len(events) == 3
    assert events[2] == (api, False, False)


@pytest.mark.asyncio
async def test_symptom_without_parent_still_alerts(db_repo):
    events = []

    async def failing_probe(_ep: str) -> ProbeResult:
        return ProbeResult(ok=False, details="HTTP 500", status_code=500)

    async def on_alert(task, log_dto, recovered: bool) -> None:
        events.append((task.id, recovered, log_dto.suppressed))

    await db_repo.upsert_user(UserDTO(id=2011, username="solo", full_name="Solo"))
    solo = await db_repo.create_task(
        _tree_task(2011, "Solo API", "https://fail.example")
    )
    pool = AsyncWorkerPool(
        repo=db_repo, workers_count=1, probe_func=failing_probe, alert_callback=on_alert
    )
    for _ in range(2):
        await pool.execute_task_immediately(solo)
    assert events == [(solo, False, False)]
    assert (await db_repo.get_analytics_summary(2011))["suppressed_24h"] == 0
