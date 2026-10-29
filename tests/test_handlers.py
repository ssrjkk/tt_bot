import pytest
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import Message, CallbackQuery, User
from aiogram.fsm.context import FSMContext
from src.handlers import (
    cmd_start,
    cb_home,
    cb_create_start,
    fsm_process_title,
    fsm_process_category,
    fsm_process_endpoint,
    fsm_process_priority,
    cb_tasks_list,
    cb_task_view,
    cb_task_run,
    cb_task_delete,
    cb_stats,
    cb_export_csv,
)
from src.models import UserDTO, TaskDTO
from src.keyboards import (
    main_menu_kb,
    category_selection_kb,
    tasks_list_kb,
    task_actions_kb,
)


def _make_user(
    user_id: int, username: str = "testuser", first_name: str = "Test"
) -> User:
    return User(id=user_id, is_bot=False, first_name=first_name, username=username)


def _make_message(user_id: int, text: str = "") -> MagicMock:
    user = _make_user(user_id)
    msg = MagicMock(spec=Message)
    msg.from_user = user
    msg.text = text
    msg.answer = AsyncMock()
    return msg


def _make_call(user_id: int, data: str) -> MagicMock:
    user = _make_user(user_id)
    call = MagicMock(spec=CallbackQuery)
    call.from_user = user
    call.data = data
    call.message = MagicMock()
    call.message.edit_text = AsyncMock()
    call.message.answer_document = AsyncMock()
    call.answer = AsyncMock()
    return call


@pytest.mark.asyncio
async def test_start_command_registers_user(db_repo):
    msg = _make_message(3001)
    await cmd_start(msg, db_repo)
    msg.answer.assert_called_once()


@pytest.mark.asyncio
async def test_cb_home(db_repo):
    call = _make_call(3001, "menu:home")
    await cb_home(call)
    call.message.edit_text.assert_called_once()
    call.answer.assert_called_once()


@pytest.mark.asyncio
async def test_cb_create_start(db_repo):
    call = _make_call(3001, "menu:create")
    state = MagicMock(spec=FSMContext)
    state.set_state = AsyncMock()
    await cb_create_start(call, state)
    state.set_state.assert_called_once()
    call.message.edit_text.assert_called_once()


@pytest.mark.asyncio
async def test_fsm_process_title(db_repo):
    msg = _make_message(3001, "Test Task Title")
    state = MagicMock(spec=FSMContext)
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()
    await fsm_process_title(msg, state)
    state.update_data.assert_called_once_with(title="Test Task Title")


@pytest.mark.asyncio
async def test_fsm_process_category(db_repo):
    call = _make_call(3001, "cat:backend")
    state = MagicMock(spec=FSMContext)
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()
    await fsm_process_category(call, state)
    state.update_data.assert_called_once_with(category="backend")


@pytest.mark.asyncio
async def test_fsm_process_endpoint(db_repo):
    msg = _make_message(3001, "api.internal/health")
    state = MagicMock(spec=FSMContext)
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()
    await fsm_process_endpoint(msg, state)
    state.update_data.assert_called_once_with(target_endpoint="api.internal/health")


@pytest.mark.asyncio
async def test_fsm_task_creation_flow(db_repo):
    await db_repo.upsert_user(
        UserDTO(id=3002, username="fsm_user", full_name="FSM User")
    )
    msg = _make_message(3002, "3")
    state = MagicMock(spec=FSMContext)
    state.get_data = AsyncMock(
        return_value={
            "title": "FSM Test Task",
            "category": "backend",
            "target_endpoint": "fsm.internal",
        }
    )
    state.clear = AsyncMock()
    await fsm_process_priority(msg, state, db_repo)
    tasks = await db_repo.get_user_tasks(3002)
    assert len(tasks) == 1
    assert tasks[0].title == "FSM Test Task"
    assert tasks[0].priority == 3


@pytest.mark.asyncio
async def test_cb_tasks_list_empty(db_repo):
    await db_repo.upsert_user(UserDTO(id=3003, username="empty", full_name="Empty"))
    call = _make_call(3003, "menu:list")
    await cb_tasks_list(call, db_repo)
    call.message.edit_text.assert_called_once()
    assert "пуст" in call.message.edit_text.call_args[0][0]


@pytest.mark.asyncio
async def test_cb_tasks_list_with_tasks(db_repo):
    await db_repo.upsert_user(
        UserDTO(id=3004, username="has_tasks", full_name="Has Tasks")
    )
    await db_repo.create_task(
        TaskDTO(
            id=None, user_id=3004, title="T1", category="backend", target_endpoint="a"
        )
    )
    call = _make_call(3004, "menu:list")
    await cb_tasks_list(call, db_repo)
    assert "Выберите задачу" in call.message.edit_text.call_args[0][0]


@pytest.mark.asyncio
async def test_cb_task_view(db_repo):
    await db_repo.upsert_user(UserDTO(id=3005, username="viewer", full_name="Viewer"))
    tid = await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=3005,
            title="View Me",
            category="qa_test",
            target_endpoint="v",
        )
    )
    call = _make_call(3005, f"task:view:{tid}")
    await cb_task_view(call, db_repo)
    assert "View Me" in call.message.edit_text.call_args[0][0]


@pytest.mark.asyncio
async def test_cb_task_run(db_repo, worker_pool):
    await db_repo.upsert_user(UserDTO(id=3006, username="runner", full_name="Runner"))
    tid = await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=3006,
            title="Run Me",
            category="rpc_node",
            target_endpoint="ok.node",
        )
    )
    call = _make_call(3006, f"task:run:{tid}")
    await cb_task_run(call, worker_pool, db_repo)
    assert "COMPLETED" in call.message.edit_text.call_args[0][0]


@pytest.mark.asyncio
async def test_cb_task_delete(db_repo):
    await db_repo.upsert_user(UserDTO(id=3007, username="deleter", full_name="Deleter"))
    tid = await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=3007,
            title="Del Me",
            category="devops",
            target_endpoint="d",
        )
    )
    call = _make_call(3007, f"task:del:{tid}")
    await cb_task_delete(call, db_repo)
    assert "удалена" in call.message.edit_text.call_args[0][0]


@pytest.mark.asyncio
async def test_stats_callback_rendering(db_repo):
    await db_repo.upsert_user(
        UserDTO(id=3008, username="stats_user", full_name="Stats User")
    )
    await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=3008,
            title="Stats Task",
            category="backend",
            target_endpoint="s",
        )
    )
    call = _make_call(3008, "menu:stats")
    await cb_stats(call, db_repo)
    assert "Аналитическая сводка" in call.message.edit_text.call_args[0][0]


@pytest.mark.asyncio
async def test_cb_export_csv(db_repo):
    await db_repo.upsert_user(
        UserDTO(id=3009, username="exporter", full_name="Exporter")
    )
    await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=3009,
            title="Export Me",
            category="backend",
            target_endpoint="e",
        )
    )
    from src.services import ReportGeneratorService

    report_service = ReportGeneratorService(repo=db_repo)
    call = _make_call(3009, "menu:export")
    await cb_export_csv(call, report_service)
    call.message.answer_document.assert_called_once()


def test_keyboard_functions():
    assert main_menu_kb() is not None
    assert category_selection_kb() is not None
    task = TaskDTO(
        id=1,
        user_id=1,
        title="T",
        category="backend",
        target_endpoint="e",
        priority=2,
        status="pending",
    )
    assert tasks_list_kb([task]) is not None
    assert task_actions_kb(1) is not None
