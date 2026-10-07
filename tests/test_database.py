import pytest
from src.models import UserDTO, TaskDTO, ExecutionLogDTO


@pytest.mark.asyncio
async def test_user_upsert_and_task_crud(db_repo):
    user = UserDTO(id=1001, username="ssrjkk", full_name="Ситников С.А.")
    await db_repo.upsert_user(user)

    task = TaskDTO(
        id=None,
        user_id=1001,
        title="Проверка RPC узла",
        category="rpc_node",
        target_endpoint="rpc-node-01.internal",
        priority=3
    )
    task_id = await db_repo.create_task(task)
    assert task_id == 1

    fetched = await db_repo.get_task_by_id(task_id)
    assert fetched is not None
    assert fetched.title == "Проверка RPC узла"
    assert fetched.priority == 3
    assert fetched.status == "pending"

    updated = await db_repo.update_task_status(task_id, "completed")
    assert updated is True
    assert (await db_repo.get_task_by_id(task_id)).status == "completed"

    deleted = await db_repo.delete_task(task_id)
    assert deleted is True
    assert await db_repo.get_task_by_id(task_id) is None


@pytest.mark.asyncio
async def test_invalid_priority_raises_error(db_repo):
    user = UserDTO(id=1002, username="tester", full_name="QA Tester")
    await db_repo.upsert_user(user)
    task_id = await db_repo.create_task(
        TaskDTO(id=None, user_id=1002, title="Task", category="qa_test", target_endpoint="service-a.internal")
    )
    with pytest.raises(ValueError):
        await db_repo.update_task_priority(task_id, 5)


@pytest.mark.asyncio
async def test_cascade_delete_logs(db_repo):
    await db_repo.upsert_user(UserDTO(id=1003, username="u3", full_name="User 3"))
    tid = await db_repo.create_task(
        TaskDTO(id=None, user_id=1003, title="API Check", category="backend", target_endpoint="api-ok.internal")
    )
    await db_repo.add_execution_log(
        ExecutionLogDTO(id=None, task_id=tid, result_status="completed", latency_ms=14.5, details="200 OK")
    )
    logs_before = await db_repo.get_task_logs(tid)
    assert len(logs_before) == 1

    await db_repo.delete_task(tid)
    logs_after = await db_repo.get_task_logs(tid)
    assert len(logs_after) == 0


@pytest.mark.asyncio
async def test_filter_tasks_by_status(db_repo):
    await db_repo.upsert_user(UserDTO(id=1004, username="u4", full_name="User 4"))
    await db_repo.create_task(
        TaskDTO(id=None, user_id=1004, title="T1", category="backend", target_endpoint="a.internal", priority=2)
    )
    t2 = await db_repo.create_task(
        TaskDTO(id=None, user_id=1004, title="T2", category="backend", target_endpoint="b.internal", priority=2)
    )
    await db_repo.update_task_status(t2, "completed")

    pending = await db_repo.get_user_tasks(1004, status_filter="pending")
    assert len(pending) == 1
    assert pending[0].title == "T1"

    completed = await db_repo.get_user_tasks(1004, status_filter="completed")
    assert len(completed) == 1
    assert completed[0].title == "T2"


@pytest.mark.asyncio
async def test_empty_analytics_summary(db_repo):
    await db_repo.upsert_user(UserDTO(id=1005, username="u5", full_name="User 5"))
    summary = await db_repo.get_analytics_summary(1005)
    assert summary["total"] == 0
    assert summary["completed"] == 0
    assert summary["pending"] == 0
    assert summary["failed"] == 0
    assert summary["avg_latency_ms"] == 0.0
