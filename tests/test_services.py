import pytest
from src.models import UserDTO, TaskDTO


@pytest.mark.asyncio
async def test_worker_pool_execution_and_analytics(
    db_repo, worker_pool, report_service
):
    await db_repo.upsert_user(
        UserDTO(id=2001, username="ssrjkk", full_name="Sitnikov S.A.")
    )
    t1 = await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=2001,
            title="Good Node",
            category="rpc_node",
            target_endpoint="ok.node.internal",
        )
    )
    t2 = await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=2001,
            title="Bad Node",
            category="rpc_node",
            target_endpoint="fail.node.internal",
        )
    )

    await worker_pool.enqueue_task(t1)
    await worker_pool.enqueue_task(t2)
    await worker_pool.queue.join()

    summary = await db_repo.get_analytics_summary(2001)
    assert summary["total"] == 2
    assert summary["completed"] == 1
    assert summary["failed"] == 1
    assert summary["avg_latency_ms"] > 0.0

    csv_text = await report_service.export_user_tasks_csv(2001)
    assert "Good Node" in csv_text
    assert "Bad Node" in csv_text


@pytest.mark.asyncio
async def test_immediate_execution_success(db_repo, worker_pool):
    await db_repo.upsert_user(UserDTO(id=2002, username="u_imm", full_name="Imm User"))
    tid = await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=2002,
            title="Imm OK",
            category="backend",
            target_endpoint="good.api.internal",
        )
    )
    log = await worker_pool.execute_task_immediately(tid)
    assert log.result_status == "completed"
    assert log.latency_ms > 0
    assert "OK" in log.details

    task = await db_repo.get_task_by_id(tid)
    assert task.status == "completed"


@pytest.mark.asyncio
async def test_immediate_execution_failure(db_repo, worker_pool):
    await db_repo.upsert_user(
        UserDTO(id=2003, username="u_fail", full_name="Fail User")
    )
    tid = await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=2003,
            title="Imm Fail",
            category="backend",
            target_endpoint="fail.api.internal",
        )
    )
    log = await worker_pool.execute_task_immediately(tid)
    assert log.result_status == "failed"
    assert log.latency_ms > 0

    task = await db_repo.get_task_by_id(tid)
    assert task.status == "failed"


@pytest.mark.asyncio
async def test_csv_report_generation(db_repo, report_service):
    await db_repo.upsert_user(UserDTO(id=2004, username="u_csv", full_name="CSV User"))
    await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=2004,
            title="CSV Task 1",
            category="devops",
            target_endpoint="vps.internal",
        )
    )
    await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=2004,
            title="CSV Task 2",
            category="qa_test",
            target_endpoint="test.internal",
        )
    )
    csv_text = await report_service.export_user_tasks_csv(2004)
    assert "CSV Task 1" in csv_text
    assert "CSV Task 2" in csv_text
    assert "ID;Title;Category" in csv_text
