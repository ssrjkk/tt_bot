import inspect
import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User

from src import handlers as handlers_module
from src.handlers import (
    _parse_link_args,
    _parse_quick_add_args,
    _render_incidents_text,
    _render_stats_text,
    _render_task_card,
    _render_tree_text,
    _safe_edit,
    cb_create_start,
    cb_export_csv,
    cb_fsm_cancel,
    cb_home,
    cb_incidents,
    cb_noop,
    cb_pause_all,
    cb_resume_all,
    cb_run_all,
    cb_run_failed,
    cb_stats,
    cb_task_delete_ask,
    cb_task_delete_confirm,
    cb_task_interval,
    cb_task_logs,
    cb_task_logs_csv,
    cb_task_priority,
    cb_task_run,
    cb_task_threshold,
    cb_task_toggle,
    cb_task_view,
    cb_tasks_list,
    cb_tree,
    cmd_cancel,
    cmd_expect,
    cmd_export,
    cmd_help,
    cmd_incidents,
    cmd_link,
    cmd_quick_add,
    cmd_start,
    cmd_stats,
    cmd_status,
    cmd_tree,
    fsm_process_category,
    fsm_process_endpoint,
    fsm_process_interval,
    fsm_process_priority_cb,
    fsm_process_priority_msg,
    fallback_message,
    fsm_process_title,
)
from src.keyboards import (
    cancel_fsm_kb,
    category_selection_kb,
    delete_confirm_kb,
    incidents_kb,
    interval_selection_kb,
    main_menu_kb,
    priority_selection_kb,
    task_actions_kb,
    tasks_list_kb,
)
from src.models import TaskDTO, UserDTO

TELEGRAM_MAX_LEN = 4096


def _make_user(
    user_id: int, username: str = "testuser", first_name: str = "Test"
) -> User:
    return User(id=user_id, is_bot=False, first_name=first_name, username=username)


def _make_message(
    user_id: int, text: str | None = "", first_name: str = "Test"
) -> MagicMock:
    msg = MagicMock(spec=Message)
    msg.from_user = _make_user(user_id, first_name=first_name)
    msg.text = text
    msg.answer = AsyncMock()
    msg.answer_document = AsyncMock()
    return msg


def _make_call(user_id: int, data: str) -> MagicMock:
    call = MagicMock(spec=CallbackQuery)
    call.from_user = _make_user(user_id)
    call.data = data
    call.message = MagicMock()
    call.message.edit_text = AsyncMock()
    call.message.answer_document = AsyncMock()
    call.answer = AsyncMock()
    return call


def _make_state(data: dict | None = None) -> MagicMock:
    state = MagicMock(spec=FSMContext)
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    state.get_data = AsyncMock(return_value=data or {})
    state.clear = AsyncMock()
    return state


@pytest.mark.asyncio
async def test_commands_start_add_status_incidents_stats_export(
    db_repo, worker_pool, report_service
):
    msg = _make_message(3001, first_name="<script>alert(1)</script>")
    state = _make_state()
    await cmd_start(msg, state, db_repo)
    assert "&lt;script&gt;" in msg.answer.call_args[0][0]

    await cmd_help(msg)
    assert "/add" in msg.answer.call_args[0][0]

    await cmd_status(msg, db_repo)
    assert "нет мониторов" in msg.answer.call_args[0][0]

    # /add validation & success
    await cmd_quick_add(_make_message(3001, "/add"), db_repo)
    msg_add_ok = _make_message(3001, "/add PayAPI https://fail.pay.example 5")
    await cmd_quick_add(msg_add_ok, db_repo)
    assert "создан" in msg_add_ok.answer.call_args[0][0]

    tasks = await db_repo.get_user_tasks(3001)
    assert len(tasks) == 1 and tasks[0].check_interval_sec == 300

    await worker_pool.execute_task_immediately(tasks[0].id)
    await cmd_incidents(msg, db_repo)
    assert "Последние инциденты" in msg.answer.call_args[0][0]

    await cmd_status(msg, db_repo)
    assert "PayAPI" in msg.answer.call_args[0][0]

    await cmd_stats(msg, db_repo)
    assert "Uptime (SLA 24ч)" in msg.answer.call_args[0][0]

    await cmd_export(msg, report_service)
    msg.answer_document.assert_called_once()

    await cmd_cancel(msg, state)
    assert "отменено" in msg.answer.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_safe_edit_ignores_not_modified():
    call = _make_call(3001, "menu:home")
    call.message.edit_text = AsyncMock(
        side_effect=TelegramBadRequest(
            method=MagicMock(), message="Bad Request: message is not modified"
        )
    )
    await _safe_edit(call, "same text")


@pytest.mark.asyncio
async def test_fsm_creation_flow_and_validation(db_repo):
    await db_repo.upsert_user(UserDTO(id=3002, username="fsm", full_name="FSM"))
    state = _make_state()

    await cb_create_start(_make_call(3002, "menu:create"), state)
    bad_title = _make_message(3002, text=None)
    await fsm_process_title(bad_title, state)
    assert "текстовое" in bad_title.answer.call_args[0][0]

    await fsm_process_title(_make_message(3002, text="<b>API</b>"), state)
    await fsm_process_category(_make_call(3002, "cat:backend"), state)
    await fsm_process_endpoint(_make_message(3002, text="bad space"), state)
    await fsm_process_endpoint(
        _make_message(3002, text="api.example.com/health"), state
    )
    await fsm_process_interval(_make_call(3002, "int:300"), state)

    saved_state = _make_state(
        {
            "title": "<b>API</b>",
            "category": "backend",
            "target_endpoint": "https://api.example.com/health",
            "check_interval_sec": 300,
        }
    )
    call_prio = _make_call(3002, "prio:3")
    await fsm_process_priority_cb(call_prio, saved_state, db_repo)
    assert "&lt;b&gt;API&lt;/b&gt;" in call_prio.message.edit_text.call_args[0][0]

    await fsm_process_priority_msg(_make_message(3002, text="9"), saved_state, db_repo)
    await fsm_process_priority_msg(_make_message(3002, text="2"), saved_state, db_repo)
    assert len(await db_repo.get_user_tasks(3002)) == 2


@pytest.mark.asyncio
async def test_navigation_incidents_run_all_and_pagination(db_repo, worker_pool):
    await db_repo.upsert_user(UserDTO(id=3003, username="nav", full_name="Nav"))
    state = _make_state()

    await cb_fsm_cancel(_make_call(3003, "fsm:cancel"), state)
    await cb_home(_make_call(3003, "menu:home"), state, db_repo)
    await cb_noop(_make_call(3003, "noop"))

    call_inc = _make_call(3003, "menu:incidents")
    await cb_incidents(call_inc, db_repo)
    assert "Инцидентов не зафиксировано" in call_inc.message.edit_text.call_args[0][0]

    call_run_empty = _make_call(3003, "menu:run_all")
    await cb_run_all(call_run_empty, worker_pool)
    assert call_run_empty.answer.call_args.kwargs.get("show_alert") is True

    for i in range(8):
        await db_repo.create_task(
            TaskDTO(
                id=None,
                user_id=3003,
                title=f"Monitor {i}",
                category="backend",
                target_endpoint=f"https://m{i}.example",
            )
        )
    call_page2 = _make_call(3003, "menu:list:all:1")
    await cb_tasks_list(call_page2, db_repo)
    assert "стр. 2/2" in call_page2.message.edit_text.call_args[0][0]

    call_run_all = _make_call(3003, "menu:run_all")
    await cb_run_all(call_run_all, worker_pool)
    assert "Поставлено в очередь" in call_run_all.message.edit_text.call_args[0][0]
    await worker_pool.queue.join()


@pytest.mark.asyncio
async def test_task_actions_uptime_bar_and_idor(db_repo, worker_pool, report_service):
    await db_repo.upsert_user(UserDTO(id=3004, username="owner", full_name="Owner"))
    await db_repo.upsert_user(UserDTO(id=3005, username="other", full_name="Other"))
    tid = await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=3004,
            title="Prod API",
            category="backend",
            target_endpoint="https://ok.prod.example",
            priority=2,
        )
    )

    for fn, payload in (
        (cb_task_view, f"task:view:{tid}"),
        (cb_task_view, "task:view:bad"),
        (cb_task_priority, f"task:prio:{tid}:3"),
        (cb_task_interval, f"task:int:{tid}:60"),
        (cb_task_threshold, f"task:thr:{tid}:3"),
        (cb_task_toggle, f"task:toggle:{tid}"),
        (cb_task_logs, f"task:logs:{tid}"),
        (cb_task_delete_ask, f"task:del_ask:{tid}"),
        (cb_task_delete_confirm, f"task:del_confirm:{tid}"),
    ):
        foreign_call = _make_call(3005, payload)
        await fn(foreign_call, db_repo)
        assert foreign_call.answer.call_args.kwargs.get("show_alert") is True

    foreign_run = _make_call(3005, f"task:run:{tid}")
    await cb_task_run(foreign_run, worker_pool, db_repo)
    assert foreign_run.answer.call_args.kwargs.get("show_alert") is True

    foreign_csv = _make_call(3005, f"task:logscsv:{tid}")
    await cb_task_logs_csv(foreign_csv, report_service)
    assert foreign_csv.answer.call_args.kwargs.get("show_alert") is True

    call_run = _make_call(3004, f"task:run:{tid}")
    await cb_task_run(call_run, worker_pool, db_repo)
    rendered = call_run.message.edit_text.call_args[0][0]
    assert "✅" in rendered and "🟢 UP" in rendered
    # A completed run renders as the ✅ result line + STATUS_BADGES["completed"],
    # the word "COMPLETED" is never part of the card text.
    assert "🟩" in rendered
    assert "84 дн." in rendered

    await cb_task_view(_make_call(3004, f"task:view:{tid}"), db_repo)
    await cb_task_priority(_make_call(3004, f"task:prio:{tid}:3"), db_repo)
    await cb_task_interval(_make_call(3004, f"task:int:{tid}:300"), db_repo)
    await cb_task_threshold(_make_call(3004, f"task:thr:{tid}:3"), db_repo)
    assert (await db_repo.get_task_by_id(tid)).alert_threshold == 3
    await cb_task_toggle(_make_call(3004, f"task:toggle:{tid}"), db_repo)
    await cb_task_logs(_make_call(3004, f"task:logs:{tid}"), db_repo)
    await cb_task_logs_csv(_make_call(3004, f"task:logscsv:{tid}"), report_service)
    await cb_task_delete_ask(_make_call(3004, f"task:del_ask:{tid}"), db_repo)
    await cb_task_delete_confirm(_make_call(3004, f"task:del_confirm:{tid}"), db_repo)
    assert await db_repo.get_task_by_id(tid) is None


@pytest.mark.asyncio
async def test_stats_export_and_keyboards(db_repo, report_service):
    await db_repo.upsert_user(UserDTO(id=3006, username="st", full_name="Stats"))
    await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=3006,
            title="Long title " + "x" * 60,
            category="devops",
            target_endpoint="https://ok.example",
        )
    )
    call_stats = _make_call(3006, "menu:stats")
    await cb_stats(call_stats, db_repo)
    assert "Uptime (SLA 24ч)" in call_stats.message.edit_text.call_args[0][0]

    call_csv = _make_call(3006, "menu:export")
    await cb_export_csv(call_csv, report_service)
    call_csv.message.answer_document.assert_called_once()

    sample_task = TaskDTO(
        id=1,
        user_id=1,
        title="Very long monitor title that should truncate cleanly in button",
        category="backend",
        target_endpoint="https://example.com",
        priority=2,
        status="pending",
    )
    assert main_menu_kb() is not None
    assert cancel_fsm_kb() is not None
    assert category_selection_kb() is not None
    assert interval_selection_kb() is not None
    assert priority_selection_kb() is not None
    assert tasks_list_kb([sample_task], page=1, total_pages=3) is not None
    assert task_actions_kb(sample_task) is not None
    assert delete_confirm_kb(1) is not None


@pytest.mark.asyncio
async def test_run_failed_and_fallback_message(db_repo, worker_pool):
    await db_repo.upsert_user(UserDTO(id=3009, username="rf", full_name="RF"))
    call_none = _make_call(3009, "menu:run_failed")
    await cb_run_failed(call_none, worker_pool)
    assert call_none.answer.call_args.kwargs.get("show_alert") is True

    tid = await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=3009,
            title="Broken API",
            category="backend",
            target_endpoint="https://fail.rf.example",
            status="failed",
        )
    )
    call_retry = _make_call(3009, "menu:run_failed")
    await cb_run_failed(call_retry, worker_pool)
    assert "Перепроверка упавших" in call_retry.message.edit_text.call_args[0][0]
    await worker_pool.queue.join()
    assert (await db_repo.get_task_stats(tid, 3009))["checks_24h"] == 1
    assert incidents_kb(True) is not None

    msg_url = _make_message(3009, "https://quick.example/health")
    await fallback_message(msg_url)
    assert "/add Service" in msg_url.answer.call_args[0][0]

    msg_other = _make_message(3009, "привет бот")
    await fallback_message(msg_other)
    assert "/help" in msg_other.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_expect_command_and_maintenance_mode(db_repo, worker_pool):
    await db_repo.upsert_user(UserDTO(id=3010, username="ops", full_name="Ops"))
    await db_repo.upsert_user(UserDTO(id=3011, username="bad", full_name="Bad"))

    call_empty_p = _make_call(3010, "menu:pause_all")
    await cb_pause_all(call_empty_p, db_repo)
    assert call_empty_p.answer.call_args.kwargs.get("show_alert") is True

    call_empty_r = _make_call(3010, "menu:resume_all")
    await cb_resume_all(call_empty_r, db_repo)
    assert call_empty_r.answer.call_args.kwargs.get("show_alert") is True

    tid = await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=3010,
            title="Health API",
            category="backend",
            target_endpoint="https://ok.health.example",
        )
    )
    await worker_pool.execute_task_immediately(tid, user_id=3010)

    # /expect usage error, IDOR block, set and reset
    m_bad = _make_message(3010, "/expect")
    await cmd_expect(m_bad, db_repo)
    assert "Формат" in m_bad.answer.call_args[0][0]

    m_idor = _make_message(3011, f"/expect {tid} ok")
    await cmd_expect(m_idor, db_repo)
    assert "нет доступа" in m_idor.answer.call_args[0][0]

    m_set = _make_message(3010, f"/expect {tid} healthy")
    await cmd_expect(m_set, db_repo)
    card = m_set.answer.call_args[0][0]
    assert "healthy" in card
    assert "Latency:" in card
    assert (await db_repo.get_task_by_id(tid)).expected_text == "healthy"

    m_clr = _make_message(3010, f"/expect {tid} -")
    await cmd_expect(m_clr, db_repo)
    assert (await db_repo.get_task_by_id(tid)).expected_text == ""

    # Maintenance pause_all / resume_all
    c_p = _make_call(3010, "menu:pause_all")
    await cb_pause_all(c_p, db_repo)
    assert (await db_repo.get_task_by_id(tid)).enabled is False

    c_r = _make_call(3010, "menu:resume_all")
    await cb_resume_all(c_r, db_repo)
    assert (await db_repo.get_task_by_id(tid)).enabled is True


def test_quick_add_args_accept_multiword_titles():
    assert _parse_quick_add_args(
        "/add Мой платёжный API https://api.example.com/health 5"
    ) == ("Мой платёжный API", "https://api.example.com/health", 5)
    assert _parse_quick_add_args("/add Pay API example.com:8545") == (
        "Pay API",
        "https://example.com:8545",
        0,
    )
    assert _parse_quick_add_args("/add replica tcp://db.internal:5432 30") == (
        "replica",
        "tcp://db.internal:5432",
        30,
    )
    # A dotted word inside the title must not win over the real endpoint.
    assert _parse_quick_add_args("/add v1.0 API https://x.example/health") == (
        "v1.0 API",
        "https://x.example/health",
        0,
    )


def test_quick_add_args_reject_ambiguous_input():
    for bad in (
        "/add",
        "/add ТолькоНазвание",
        "/add https://only.endpoint.example/x extra",
        "/add A https://x.example 5 6",
        "/add A https://x.example not-a-number",
        "/add A https://x.example -5",
    ):
        with pytest.raises(ValueError):
            _parse_quick_add_args(bad)


@pytest.mark.asyncio
async def test_quick_add_stores_full_title_and_interval(db_repo):
    await db_repo.upsert_user(UserDTO(id=3011, username="qa", full_name="QA"))
    msg = _make_message(3011, "/add Прод API здоровья https://ok.example/health 5")
    await cmd_quick_add(msg, db_repo)
    assert "создан" in msg.answer.call_args[0][0]

    stored = (await db_repo.get_user_tasks(3011))[0]
    assert stored.title == "Прод API здоровья"
    assert stored.check_interval_sec == 300


@pytest.mark.asyncio
async def test_task_run_announces_unsaved_result(db_repo, worker_pool, monkeypatch):
    await db_repo.upsert_user(UserDTO(id=3012, username="lost", full_name="Lost"))
    tid = await db_repo.create_task(
        TaskDTO(
            id=None,
            user_id=3012,
            title="Write Lost",
            category="backend",
            target_endpoint="https://ok.example",
        )
    )

    async def result_not_persisted(*_args, **_kwargs):
        return None

    monkeypatch.setattr(worker_pool, "execute_task_immediately", result_not_persisted)
    call = _make_call(3012, f"task:run:{tid}")
    await cb_task_run(call, worker_pool, db_repo)

    rendered = call.message.edit_text.call_args[0][0]
    assert "не сохранён" in rendered
    assert "✅" not in rendered
    assert "❌" not in rendered


def _tree_task(user_id: int, title: str, endpoint: str) -> TaskDTO:
    return TaskDTO(
        id=None,
        user_id=user_id,
        title=title,
        category="backend",
        target_endpoint=endpoint,
        status="failed",
    )


@pytest.mark.asyncio
async def test_cmd_link_and_cmd_tree_surfaces(db_repo):
    await db_repo.upsert_user(UserDTO(id=3201, username="lnk", full_name="Lnk"))
    parent = await db_repo.create_task(
        _tree_task(3201, "Edge proxy", "https://p.example")
    )
    child = await db_repo.create_task(_tree_task(3201, "Pay API", "https://c.example"))

    msg = _make_message(3201, "/link")
    await cmd_link(msg, db_repo)
    assert "Формат:" in msg.answer.call_args[0][0]

    await cmd_link(_make_message(3201, "/link abc 1"), db_repo)
    assert "Формат:" in msg.answer.call_args[0][0]

    missing = _make_message(3201, f"/link {child} 99999")
    await cmd_link(missing, db_repo)
    assert "Связь отклонена" in missing.answer.call_args[0][0]

    loop = _make_message(3201, f"/link {child} {child}")
    await cmd_link(loop, db_repo)
    assert "сам себе" in loop.answer.call_args[0][0]

    ok = _make_message(3201, f"/link {child} {parent}")
    await cmd_link(ok, db_repo)
    rendered = ok.answer.call_args[0][0]
    assert "Связь обновлена" in rendered
    assert "└─" in rendered and f"#{child}" in rendered
    assert (await db_repo.get_task_by_id(child)).parent_id == parent

    tree_msg = _make_message(3201, "/tree")
    await cmd_tree(tree_msg, db_repo)
    tree_text = tree_msg.answer.call_args[0][0]
    assert "Дерево мониторов" in tree_text
    assert "узлов 2 · связей 1" in tree_text
    assert "✗" in tree_text

    reset = _make_message(3201, f"/link {child} -")
    await cmd_link(reset, db_repo)
    assert "сброшена" in reset.answer.call_args[0][0]
    assert (await db_repo.get_task_by_id(child)).parent_id is None


@pytest.mark.asyncio
async def test_cmd_link_blocks_other_users_monitors(db_repo):
    await db_repo.upsert_user(UserDTO(id=3202, username="own", full_name="Own"))
    await db_repo.upsert_user(UserDTO(id=3203, username="intr", full_name="Intr"))
    victim = await db_repo.create_task(
        _tree_task(3202, "Secret <b>svc</b>", "https://v.example")
    )
    attacker = await db_repo.create_task(_tree_task(3203, "Mine", "https://a.example"))

    msg = _make_message(3203, f"/link {attacker} {victim}")
    await cmd_link(msg, db_repo)
    answer = msg.answer.call_args[0][0]
    assert "Связь отклонена" in answer
    assert "Secret" not in answer
    assert (await db_repo.get_task_by_id(attacker)).parent_id is None

    steal = _make_message(3203, f"/link {victim} {attacker}")
    await cmd_link(steal, db_repo)
    assert "нет доступа" in steal.answer.call_args[0][0]
    assert (await db_repo.get_task_by_id(victim)).parent_id is None


@pytest.mark.asyncio
async def test_cb_tree_renders_dependency_tree(db_repo):
    await db_repo.upsert_user(UserDTO(id=3204, username="cb", full_name="Cb"))
    parent = await db_repo.create_task(_tree_task(3204, "Root", "https://r.example"))
    child = await db_repo.create_task(_tree_task(3204, "Leaf", "https://l.example"))
    await db_repo.set_task_parent(child, parent, user_id=3204)

    call = _make_call(3204, "menu:tree")
    await cb_tree(call, db_repo)
    text = call.message.edit_text.call_args[0][0]
    assert "└─" in text and f"#{child}" in text
    call.answer.assert_called_once()


def test_render_tree_text_covers_empty_orphan_and_deep_shapes():
    assert "пусто" in _render_tree_text([])

    def node(tid, title, parent=None, status="completed", depth_hint=""):
        return TaskDTO(
            id=tid,
            user_id=1,
            title=f"{title}{depth_hint}",
            category="backend",
            target_endpoint=f"https://{title}.example",
            status=status,
            parent_id=parent,
        )

    closed = _render_tree_text([node(1, "a"), node(2, "b", parent=1)])
    assert "связей 1" in closed and "└─" in closed

    cycle = _render_tree_text(
        [
            node(1, "a", parent=2, status="failed"),
            node(2, "b", parent=1, status="failed"),
        ]
    )
    assert "Вне дерева:" in cycle and "связей 2" in cycle

    deep = [node(1, "n1")]
    for idx in range(2, 13):
        deep.append(node(idx, f"n{idx}", parent=idx - 1))
    rendered = _render_tree_text(deep)
    assert "глубина обрезана" in rendered
    assert rendered.count("└─") <= handlers_module.TREE_MAX_DEPTH + 1

    wide = [node(1, "root")] + [node(idx, f"x{idx}", parent=1) for idx in range(2, 400)]
    capped = _render_tree_text(wide)
    assert "ещё" in capped and len(capped) < TELEGRAM_MAX_LEN

    # html.escape раздувает заголовок в 6 раз уже после обрезки до 32 символов,
    # поэтому лимит должен считаться по знакам, а не по числу строк.
    loud = [node(1, "root")] + [
        node(idx, "<&>\"'=" * 40, parent=1) for idx in range(2, 400)
    ]
    noisy = _render_tree_text(loud)
    assert "&lt;&amp;&gt;" in noisy and len(noisy) < TELEGRAM_MAX_LEN


def test_tree_rendering_escapes_html_in_titles():
    task = TaskDTO(
        id=5,
        user_id=1,
        title="<script>alert(1)</script>",
        category="backend",
        target_endpoint="https://x.example",
        status="failed",
    )
    out = _render_tree_text([task])
    assert "<script>" not in out and "&lt;script&gt;" in out


def test_incidents_and_stats_surface_root_cause():
    incidents = [
        {
            "task_id": 12,
            "title": "Pay API",
            "executed_at": "2026-11-28T10:00:00",
            "latency_ms": 30.0,
            "details": "HTTP 502",
            "suppressed": 1,
            "root_id": 7,
            "root_title": "Edge proxy",
        },
        {
            "task_id": 13,
            "title": "Standalone",
            "executed_at": "2026-11-28T10:05:00",
            "latency_ms": 40.0,
            "details": "timeout",
            "suppressed": 0,
            "root_id": None,
            "root_title": None,
        },
        {
            "task_id": 14,
            "title": "Workers",
            "executed_at": "2026-11-28T10:07:00",
            "latency_ms": 55.0,
            "details": "HTTP 500",
            "suppressed": 0,
            "root_id": 8,
            "root_title": "DB",
        },
    ]
    text = _render_incidents_text(incidents)
    assert "<code>симптом</code> · причина <b>#7</b> Edge proxy" in text
    assert "<code>в тени</code> · причина <b>#8</b> DB" in text
    assert text.count("причина") == 2

    stats = {
        "total": 3,
        "completed": 1,
        "failed": 1,
        "pending": 1,
        "running": 0,
        "checks_24h": 10,
        "suppressed_24h": 4,
        "uptime_24h_pct": 60.0,
        "avg_latency_ms": 12.0,
        "p95_latency_ms": 20.0,
    }
    assert "Алертов подавлено (24ч): <b>4</b>" in _render_stats_text(stats)

    task = TaskDTO(
        id=9,
        user_id=1,
        title="Pay API",
        category="backend",
        target_endpoint="https://api.example",
        status="failed",
        parent_id=7,
    )
    card = _render_task_card(task)
    assert "Зависит от: <b>#7</b>" in card and "/link 9 -" in card
    assert "Зависит от" not in _render_task_card(
        TaskDTO(
            id=9,
            user_id=1,
            title="Solo",
            category="backend",
            target_endpoint="https://api.example",
            status="failed",
        )
    )


def _all_keyboard_callbacks() -> set:
    task = TaskDTO(
        id=7,
        user_id=7,
        title="Btn",
        category="backend",
        target_endpoint="https://btn.example",
        priority=2,
        status="failed",
    )
    keyboards = [
        main_menu_kb(),
        incidents_kb(True),
        incidents_kb(False),
        cancel_fsm_kb(),
        category_selection_kb(),
        interval_selection_kb(),
        priority_selection_kb(),
        tasks_list_kb([task], page=0, total_pages=2),
        task_actions_kb(task),
        delete_confirm_kb(7),
    ]
    return {
        button.callback_data
        for kb in keyboards
        for row in kb.inline_keyboard
        for button in row
    }


def test_every_keyboard_callback_is_routed():
    """Инвариант ARCHITECTURE.md: у каждой кнопки есть обработчик."""
    source = inspect.getsource(handlers_module)
    exact = set(re.findall(r'F\.data == "([^"]+)"', source))
    prefixes = set(re.findall(r'F\.data\.startswith\("([^"]+)"\)', source))
    callbacks = _all_keyboard_callbacks()
    unrouted = {
        data
        for data in callbacks
        if data not in exact and not any(data.startswith(p) for p in prefixes)
    }
    assert unrouted == set()
    assert "menu:tree" in callbacks


def test_parse_link_args_shapes():
    assert _parse_link_args("/link 3 5") == (3, 5, True)
    assert _parse_link_args("/link 3 -") == (3, None, True)
    assert _parse_link_args("/link 3") == (None, None, False)
    assert _parse_link_args("/link") == (None, None, False)
    assert _parse_link_args("/link a 5") == (None, None, False)
    assert _parse_link_args("/link 5 шесть") == (None, None, False)
    assert _parse_link_args(None) == (None, None, False)
