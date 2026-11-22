import asyncio

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
