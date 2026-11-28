import html
import math
from typing import Any, Dict, List, Optional

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from src.database import DatabaseRepository
from src.keyboards import (
    STATUS_ICONS,
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
from src.models import ExecutionLogDTO, TaskDTO, UserDTO
from src.probes import BOT_VERSION, normalize_endpoint
from src.services import AsyncWorkerPool, ReportGeneratorService
from src.states import CreateTaskSG

router = Router()
PAGE_SIZE = 6
ALLOWED_FILTERS = {"all", "completed", "failed", "pending"}

STATUS_BADGES = {
    "completed": "🟢 UP",
    "failed": "🔴 DOWN",
    "pending": "🟡 PENDING",
    "running": "🔄 RUNNING",
}


def _parse_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _get_part(data: Optional[str], idx: int) -> Optional[str]:
    if not data:
        return None
    parts = data.split(":")
    return parts[idx] if len(parts) > idx else None


async def _safe_edit(
    call: CallbackQuery, text: str, reply_markup=None, parse_mode: str = "HTML"
) -> None:
    try:
        await call.message.edit_text(
            text, reply_markup=reply_markup, parse_mode=parse_mode
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


def _format_interval(sec: int) -> str:
    if sec <= 0:
        return "вручную"
    if sec < 60:
        return f"каждые {sec} с"
    return f"каждые {sec // 60} мин"


def _build_uptime_bar(logs: List[ExecutionLogDTO]) -> str:
    if not logs:
        return "⬜ нет проверок"
    ordered = list(reversed(logs[:10]))
    return "".join(
        "🟩" if entry.result_status == "completed" else "🟥" for entry in ordered
    )


def _build_latency_sparkline(logs: List[ExecutionLogDTO]) -> str:
    if not logs:
        return "—"
    blocks = "▁▂▃▄▅▆▇█"
    vals = [max(0.0, float(entry.latency_ms)) for entry in reversed(logs[:10])]
    peak = max(vals) or 1.0
    chars = [blocks[min(7, int((v / peak) * 7))] for v in vals]
    return "".join(chars)


def _build_sla_meter(pct: float) -> str:
    filled = max(0, min(10, round(pct / 10.0)))
    return ("█" * filled) + ("░" * (10 - filled))


def _render_home_text(stats: Dict[str, Any], name: Optional[str] = None) -> str:
    greet = f"Привет, <b>{html.escape(name)}</b>!\n\n" if name else ""
    summary = (
        f"<code>Мониторов: {stats['total']} · "
        f"UP: {stats['completed']} · "
        f"DOWN: {stats['failed']} · "
        f"SLA 24ч: {stats['uptime_24h_pct']}%</code>"
    )
    return (
        f"{greet}"
        f"<b>tt_bot v{BOT_VERSION}</b> — self-hosted монитор HTTP/HTTPS, SSL и TCP.\n"
        f"{summary}\n\n"
        "Выберите действие или используйте <code>/add Название URL [мин]</code>:"
    )


def _append_log_lines(
    lines: List[str], task: TaskDTO, logs: Optional[List[ExecutionLogDTO]]
) -> None:
    if logs is None:
        return
    lines.append(f"▪️ История: {_build_uptime_bar(logs)}")
    if logs:
        lines.append(f"▪️ Latency: <code>{_build_latency_sparkline(logs)}</code>")
        if logs[0].ssl_days_left is not None:
            lines.append(
                f"▪️ TLS/SSL: <code>{logs[0].ssl_days_left} дн.</code> "
                f"(мин. {task.ssl_min_days} дн.)"
            )


def _render_task_card(
    task: TaskDTO,
    logs: Optional[List[ExecutionLogDTO]] = None,
    task_stats: Optional[Dict[str, Any]] = None,
) -> str:
    badge = STATUS_BADGES.get(task.status, task.status.upper())
    state_label = "активен" if task.enabled else "пауза ⏸"
    lines = [
        f"<b>{badge} · Монитор #{task.id}</b> [P{task.priority}]",
        f"▪️ Сервис: <b>{html.escape(task.title)}</b> "
        f"(<code>{html.escape(task.category)}</code>)",
        f"▪️ Адрес: <code>{html.escape(task.target_endpoint)}</code>",
        f"▪️ Цикл: {_format_interval(task.check_interval_sec)} ({state_label}) · "
        f"алерт ≥ {task.alert_threshold}",
    ]
    if task.expected_text:
        lines.append(
            f"▪️ Тело: содержит <code>{html.escape(task.expected_text)}</code>"
        )
    if task_stats and task_stats["checks_24h"] > 0:
        meter = _build_sla_meter(task_stats["uptime_24h_pct"])
        lines.append(
            f"▪️ SLA 24ч: <code>{meter}</code> "
            f"<b>{task_stats['uptime_24h_pct']}%</b> · "
            f"P95 <code>{task_stats['p95_latency_ms']} мс</code>"
        )
    _append_log_lines(lines, task, logs)
    ts = (task.last_checked_at or "—")[:19].replace("T", " ")
    lines.append(f"▪️ Проверка: <code>{html.escape(ts)}</code>")
    return "\n".join(lines)


def _render_stats_text(stats: dict) -> str:
    return (
        "<b>📊 Аналитика и SLA (24 часа)</b>\n\n"
        f"▪️ Всего мониторов: <b>{stats['total']}</b>\n"
        f"▪️ Работают (UP): <b>{stats['completed']}</b>\n"
        f"▪️ С ошибкой (DOWN): <b>{stats['failed']}</b>\n"
        f"▪️ В ожидании (Pending): <b>{stats['pending']}</b>\n"
        f"▪️ Проверок за 24ч: <b>{stats['checks_24h']}</b>\n"
        f"▪️ Uptime (SLA 24ч): "
        f"<code>{_build_sla_meter(stats['uptime_24h_pct'])}</code> "
        f"<b>{stats['uptime_24h_pct']}%</b>\n"
        f"▪️ Средний latency: <code>{stats['avg_latency_ms']} мс</code>\n"
        f"▪️ P95 latency: <code>{stats['p95_latency_ms']} мс</code>"
    )


def _render_incidents_text(incidents: List[dict]) -> str:
    if not incidents:
        return "🟢 <b>Инцидентов не зафиксировано.</b> Все сервисы работают чисто."
    lines = ["🚨 <b>Последние инциденты (до 10):</b>\n"]
    for item in incidents:
        ts = (item["executed_at"] or "")[:19].replace("T", " ")
        lines.append(
            f"❌ <b>#{item['task_id']} {html.escape(item['title'])}</b>\n"
            f"   <code>{ts}</code> · {item['latency_ms']} мс · "
            f"{html.escape(str(item['details'])[:60])}"
        )
    return "\n".join(lines)


async def _load_card_view(
    repo: DatabaseRepository, task_id: int, user_id: int
) -> tuple[Optional[TaskDTO], List[ExecutionLogDTO], Dict[str, Any]]:
    task = await repo.get_task_by_id(task_id, user_id=user_id)
    if not task:
        return None, [], {}
    logs = await repo.get_task_logs(task_id, limit=10, user_id=user_id)
    stats = await repo.get_task_stats(task_id, user_id=user_id)
    return task, logs, stats


async def _save_new_task(
    user_id: int, prio: int, state: FSMContext, repo: DatabaseRepository
) -> tuple[int, TaskDTO]:
    data = await state.get_data()
    await state.clear()
    new_task = TaskDTO(
        id=None,
        user_id=user_id,
        title=data["title"],
        category=data["category"],
        target_endpoint=data["target_endpoint"],
        check_interval_sec=data.get("check_interval_sec", 0),
        priority=prio,
    )
    task_id = await repo.create_task(new_task)
    new_task.id = task_id
    return task_id, new_task


@router.message(CommandStart())
async def cmd_start(
    message: Message, state: FSMContext, repo: DatabaseRepository
) -> None:
    await state.clear()
    user_dto = UserDTO(
        id=message.from_user.id,
        username=message.from_user.username or "anonymous",
        full_name=message.from_user.full_name or "User",
    )
    await repo.upsert_user(user_dto)
    stats = await repo.get_analytics_summary(message.from_user.id)
    await message.answer(
        _render_home_text(stats, name=user_dto.full_name),
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        f"<b>Справка tt_bot v{BOT_VERSION}</b>\n\n"
        "• /start — главное меню и сводка\n"
        "• /add <code>Название https://url [мин]</code> — быстрое создание\n"
        "• /expect <code>ID строка|-</code> — проверка текста в ответе\n"
        "• /status — список мониторов одной сводкой\n"
        "• /incidents — последние инциденты и быстрый ретрай\n"
        "• /stats — аналитика и SLA за 24 часа\n"
        "• /export — скачать CSV-реестр мониторов\n"
        "• /cancel — отменить текущее действие\n\n"
        "Форматы: <code>https://api.example.com/health</code> "
        "или <code>tcp://host:5432</code>. Для HTTPS проверяется срок SSL."
    )
    await message.answer(text, reply_markup=main_menu_kb(), parse_mode="HTML")


def _looks_like_host(token: str) -> bool:
    return "." in token and not token.isdigit()


def _parse_quick_add_args(text: Optional[str]) -> tuple[str, str, int]:
    parts = (text or "").split()
    if len(parts) < 3:
        raise ValueError(
            "Формат: <code>/add Название https://api.example.com/health [мин]</code>"
        )
    scheme_idx = next((i for i in range(1, len(parts)) if "://" in parts[i]), None)
    host_idx = next(
        (i for i in range(1, len(parts)) if _looks_like_host(parts[i])), None
    )
    ep_idx = scheme_idx if scheme_idx is not None else host_idx
    if ep_idx is None:
        raise ValueError(
            "Не найден эндпоинт. Формат: "
            "<code>/add Название https://api.example.com/health [мин]</code>"
        )
    title = " ".join(parts[1:ep_idx]).strip()[:80]
    if not title:
        raise ValueError("Название монитора не может быть пустым.")
    endpoint = normalize_endpoint(parts[ep_idx])
    tail_from = ep_idx + 1
    tail = parts[tail_from:]
    if len(tail) > 1:
        raise ValueError(
            "Слишком много аргументов. Формат: "
            "<code>/add Название https://api.example.com/health [мин]</code>"
        )
    interval_min = _parse_int(tail[0]) if tail else 0
    if interval_min is None or interval_min < 0:
        raise ValueError("Интервал в минутах должен быть числом &gt;= 0.")
    return title, endpoint, interval_min


@router.message(Command("add"))
async def cmd_quick_add(message: Message, repo: DatabaseRepository) -> None:
    try:
        title, endpoint, interval_min = _parse_quick_add_args(message.text)
    except ValueError as exc:
        await message.answer(str(exc), parse_mode="HTML")
        return

    await repo.upsert_user(
        UserDTO(
            id=message.from_user.id,
            username=message.from_user.username or "anonymous",
            full_name=message.from_user.full_name or "User",
        )
    )
    task = TaskDTO(
        id=None,
        user_id=message.from_user.id,
        title=title,
        category="devops" if endpoint.startswith("tcp://") else "backend",
        target_endpoint=endpoint,
        check_interval_sec=interval_min * 60,
        priority=2,
    )
    task.id = await repo.create_task(task)
    await message.answer(
        f"⚡ Монитор <b>#{task.id}</b> создан!\n\n" + _render_task_card(task, []),
        reply_markup=task_actions_kb(task),
        parse_mode="HTML",
    )


@router.message(Command("expect"))
async def cmd_expect(message: Message, repo: DatabaseRepository) -> None:
    parts = (message.text or "").split(maxsplit=2)
    task_id = _parse_int(parts[1]) if len(parts) >= 2 else None
    if task_id is None or len(parts) < 3:
        await message.answer(
            "Формат: <code>/expect ID строка</code> (или <code>-</code> для сброса)",
            parse_mode="HTML",
        )
        return
    raw_val = parts[2].strip()
    expected = "" if raw_val == "-" else raw_val
    updated = await repo.update_task_expected_text(
        task_id, expected, user_id=message.from_user.id
    )
    if not updated:
        await message.answer("Монитор не найден или нет доступа.")
        return
    task, logs, stats = await _load_card_view(repo, task_id, message.from_user.id)
    msg_prefix = (
        f"🔎 Проверка тела обновлена: <code>{html.escape(expected)}</code>"
        if expected
        else "🔎 Проверка тела ответа отключена."
    )
    await message.answer(
        f"{msg_prefix}\n\n" + _render_task_card(task, logs, stats),
        reply_markup=task_actions_kb(task),
        parse_mode="HTML",
    )


@router.message(Command("status"))
async def cmd_status(message: Message, repo: DatabaseRepository) -> None:
    tasks = await repo.get_user_tasks(message.from_user.id, limit=15)
    if not tasks:
        await message.answer(
            "У вас пока нет мониторов. Создайте первый через меню или /add:",
            reply_markup=main_menu_kb(),
            parse_mode="HTML",
        )
        return
    lines = ["<b>Статус мониторов:</b>\n"]
    for task in tasks:
        icon = STATUS_ICONS.get(task.status, "📌")
        pause = " ⏸" if not task.enabled else ""
        lines.append(
            f"{icon} <b>#{task.id}</b> {html.escape(task.title)}{pause} "
            f"— <code>{html.escape(task.status.upper())}</code>"
        )
    await message.answer(
        "\n".join(lines), reply_markup=main_menu_kb(), parse_mode="HTML"
    )


@router.message(Command("incidents"))
async def cmd_incidents(message: Message, repo: DatabaseRepository) -> None:
    incidents = await repo.get_recent_incidents(message.from_user.id, limit=10)
    await message.answer(
        _render_incidents_text(incidents),
        reply_markup=incidents_kb(bool(incidents)),
        parse_mode="HTML",
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message, repo: DatabaseRepository) -> None:
    stats = await repo.get_analytics_summary(message.from_user.id)
    await message.answer(
        _render_stats_text(stats), reply_markup=main_menu_kb(), parse_mode="HTML"
    )


@router.message(Command("export"))
async def cmd_export(message: Message, report_service: ReportGeneratorService) -> None:
    csv_data = await report_service.export_user_tasks_csv(message.from_user.id)
    doc = BufferedInputFile(
        csv_data.encode("utf-8-sig"),
        filename=f"tt_bot_report_{message.from_user.id}.csv",
    )
    await message.answer_document(
        doc, caption="Выгрузка реестра ваших мониторов в формате CSV."
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Действие отменено. Вы в главном меню:",
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "fsm:cancel")
async def cb_fsm_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _safe_edit(call, "Создание отменено. Главное меню:", main_menu_kb())
    await call.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery) -> None:
    await call.answer()


@router.callback_query(F.data == "menu:home")
async def cb_home(
    call: CallbackQuery, state: FSMContext, repo: DatabaseRepository
) -> None:
    await state.clear()
    stats = await repo.get_analytics_summary(call.from_user.id)
    await _safe_edit(call, _render_home_text(stats), main_menu_kb())
    await call.answer()


@router.callback_query(F.data == "menu:incidents")
async def cb_incidents(call: CallbackQuery, repo: DatabaseRepository) -> None:
    incidents = await repo.get_recent_incidents(call.from_user.id, limit=10)
    await _safe_edit(
        call, _render_incidents_text(incidents), incidents_kb(bool(incidents))
    )
    await call.answer()


@router.callback_query(F.data == "menu:run_all")
async def cb_run_all(call: CallbackQuery, worker_pool: AsyncWorkerPool) -> None:
    queued = await worker_pool.run_all_user_tasks(call.from_user.id)
    if queued == 0:
        await call.answer("Нет активных мониторов для запуска", show_alert=True)
        return
    await _safe_edit(
        call,
        f"🔄 Поставлено в очередь <b>AsyncWorkerPool</b>: <b>{queued}</b> монитор(ов).\n"
        "Результаты появятся в списке через пару секунд.",
        main_menu_kb(),
    )
    await call.answer(f"Запущено проверок: {queued}")


@router.callback_query(F.data == "menu:run_failed")
async def cb_run_failed(call: CallbackQuery, worker_pool: AsyncWorkerPool) -> None:
    queued = await worker_pool.run_failed_user_tasks(call.from_user.id)
    if queued == 0:
        await call.answer("Нет активных упавших мониторов", show_alert=True)
        return
    await _safe_edit(
        call,
        f"🔁 Перепроверка упавших мониторов: поставлено в очередь <b>{queued}</b>.",
        main_menu_kb(),
    )
    await call.answer(f"Перезапущено: {queued}")


@router.callback_query(F.data == "menu:pause_all")
async def cb_pause_all(call: CallbackQuery, repo: DatabaseRepository) -> None:
    count = await repo.set_all_tasks_enabled(call.from_user.id, False)
    if count == 0:
        await call.answer("Нет мониторов для паузы", show_alert=True)
        return
    stats = await repo.get_analytics_summary(call.from_user.id)
    await _safe_edit(
        call,
        f"⏸ <b>Режим обслуживания:</b> поставлено на паузу <b>{count}</b> монитор(ов)."
        "\n\n" + _render_home_text(stats),
        main_menu_kb(),
    )
    await call.answer(f"На паузе: {count}")


@router.callback_query(F.data == "menu:resume_all")
async def cb_resume_all(call: CallbackQuery, repo: DatabaseRepository) -> None:
    count = await repo.set_all_tasks_enabled(call.from_user.id, True)
    if count == 0:
        await call.answer("Нет мониторов для запуска", show_alert=True)
        return
    stats = await repo.get_analytics_summary(call.from_user.id)
    await _safe_edit(
        call,
        f"▶️ <b>Мониторинг возобновлён:</b> активно <b>{count}</b> монитор(ов)."
        "\n\n" + _render_home_text(stats),
        main_menu_kb(),
    )
    await call.answer(f"Активно: {count}")


@router.callback_query(F.data == "menu:create")
async def cb_create_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CreateTaskSG.waiting_for_title)
    await _safe_edit(
        call,
        "<b>Шаг 1/5</b> — Введите название монитора (до 80 символов):",
        cancel_fsm_kb(),
    )
    await call.answer()


@router.message(CreateTaskSG.waiting_for_title)
async def fsm_process_title(message: Message, state: FSMContext) -> None:
    if not message.text or not message.text.strip():
        await message.answer(
            "Отправьте текстовое название.", reply_markup=cancel_fsm_kb()
        )
        return
    title = message.text.strip()[:80]
    await state.update_data(title=title)
    await state.set_state(CreateTaskSG.waiting_for_category)
    await message.answer(
        "<b>Шаг 2/5</b> — Выберите категорию:",
        reply_markup=category_selection_kb(),
        parse_mode="HTML",
    )


@router.callback_query(CreateTaskSG.waiting_for_category, F.data.startswith("cat:"))
async def fsm_process_category(call: CallbackQuery, state: FSMContext) -> None:
    category = call.data.split(":", 1)[1]
    await state.update_data(category=category)
    await state.set_state(CreateTaskSG.waiting_for_endpoint)
    await _safe_edit(
        call,
        "<b>Шаг 3/5</b> — Введите целевой эндпоинт:\n"
        "• HTTP/HTTPS: <code>https://api.example.com/health</code>\n"
        "• TCP-порт: <code>tcp://1.2.3.4:5432</code>",
        cancel_fsm_kb(),
    )
    await call.answer()


@router.message(CreateTaskSG.waiting_for_endpoint)
async def fsm_process_endpoint(message: Message, state: FSMContext) -> None:
    if not message.text or not message.text.strip():
        await message.answer(
            "Отправьте URL или tcp://host:port.", reply_markup=cancel_fsm_kb()
        )
        return
    try:
        endpoint = normalize_endpoint(message.text)
    except ValueError as exc:
        await message.answer(
            f"Ошибка: {html.escape(str(exc))}", reply_markup=cancel_fsm_kb()
        )
        return
    await state.update_data(target_endpoint=endpoint)
    await state.set_state(CreateTaskSG.waiting_for_interval)
    await message.answer(
        "<b>Шаг 4/5</b> — Как часто проверять эндпоинт автоматически?",
        reply_markup=interval_selection_kb(),
        parse_mode="HTML",
    )


@router.callback_query(CreateTaskSG.waiting_for_interval, F.data.startswith("int:"))
async def fsm_process_interval(call: CallbackQuery, state: FSMContext) -> None:
    interval = _parse_int(call.data.split(":", 1)[1])
    if interval is None or interval < 0:
        await call.answer("Некорректный интервал", show_alert=True)
        return
    await state.update_data(check_interval_sec=interval)
    await state.set_state(CreateTaskSG.waiting_for_priority)
    await _safe_edit(
        call,
        "<b>Шаг 5/5</b> — Выберите приоритет (или введите 1, 2, 3):",
        priority_selection_kb(),
    )
    await call.answer()


@router.callback_query(CreateTaskSG.waiting_for_priority, F.data.startswith("prio:"))
async def fsm_process_priority_cb(
    call: CallbackQuery, state: FSMContext, repo: DatabaseRepository
) -> None:
    prio = _parse_int(call.data.split(":", 1)[1])
    if prio not in (1, 2, 3):
        await call.answer("Некорректный приоритет", show_alert=True)
        return
    task_id, task = await _save_new_task(call.from_user.id, prio, state, repo)
    await _safe_edit(
        call,
        f"✅ Монитор <b>#{task_id}</b> создан!\n\n" + _render_task_card(task, []),
        task_actions_kb(task),
    )
    await call.answer("Сохранено!")


@router.message(CreateTaskSG.waiting_for_priority)
async def fsm_process_priority_msg(
    message: Message, state: FSMContext, repo: DatabaseRepository
) -> None:
    if not message.text or message.text.strip() not in {"1", "2", "3"}:
        await message.answer(
            "Ошибка: выберите кнопку ниже или введите 1, 2 или 3.",
            reply_markup=priority_selection_kb(),
        )
        return
    prio = int(message.text.strip())
    task_id, task = await _save_new_task(message.from_user.id, prio, state, repo)
    await message.answer(
        f"✅ Монитор <b>#{task_id}</b> создан!\n\n" + _render_task_card(task, []),
        reply_markup=task_actions_kb(task),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("menu:list"))
async def cb_tasks_list(call: CallbackQuery, repo: DatabaseRepository) -> None:
    parts = call.data.split(":")
    status_filter = (
        parts[2] if len(parts) > 2 and parts[2] in ALLOWED_FILTERS else "all"
    )
    raw_page = parts[3] if len(parts) > 3 else "0"
    page = max(0, _parse_int(raw_page) or 0)

    total = await repo.count_user_tasks(call.from_user.id, status_filter=status_filter)
    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(page, total_pages - 1)
    tasks = await repo.get_user_tasks(
        call.from_user.id,
        status_filter=status_filter,
        limit=PAGE_SIZE,
        offset=page * PAGE_SIZE,
    )

    if not tasks:
        header = (
            "Список ваших мониторов пуст."
            if status_filter == "all"
            else f"Нет мониторов со статусом <b>{status_filter}</b>."
        )
    else:
        header = (
            f"<b>Ваши мониторы</b> " f"(всего: {total}, стр. {page + 1}/{total_pages}):"
        )
    await _safe_edit(
        call, header, tasks_list_kb(tasks, page, total_pages, status_filter)
    )
    await call.answer()


@router.callback_query(F.data.startswith("task:view:"))
async def cb_task_view(call: CallbackQuery, repo: DatabaseRepository) -> None:
    task_id = _parse_int(_get_part(call.data, 2))
    if task_id is None:
        await call.answer("Некорректный ID", show_alert=True)
        return
    task, logs, stats = await _load_card_view(repo, task_id, call.from_user.id)
    if not task:
        await call.answer("Монитор не найден или нет доступа", show_alert=True)
        return
    await _safe_edit(call, _render_task_card(task, logs, stats), task_actions_kb(task))
    await call.answer()


@router.callback_query(F.data.startswith("task:run:"))
async def cb_task_run(
    call: CallbackQuery, worker_pool: AsyncWorkerPool, repo: DatabaseRepository
) -> None:
    task_id = _parse_int(_get_part(call.data, 2))
    if task_id is None:
        await call.answer("Некорректный ID", show_alert=True)
        return
    task = await repo.get_task_by_id(task_id, user_id=call.from_user.id)
    if not task:
        await call.answer("Монитор не найден или нет доступа", show_alert=True)
        return
    await call.answer("Выполняю проверку…")
    log_entry = await worker_pool.execute_task_immediately(
        task_id, user_id=call.from_user.id
    )
    updated_task, logs, stats = await _load_card_view(repo, task_id, call.from_user.id)
    if not updated_task:
        return
    if log_entry is None:
        await _safe_edit(
            call,
            "<b>Результат проверки не сохранён</b> — монитор удалён "
            "или база данных недоступна.\n\n"
            + _render_task_card(updated_task, logs, stats),
            task_actions_kb(updated_task),
        )
        return
    icon = "✅" if log_entry.result_status == "completed" else "❌"
    result_text = (
        f"{icon} <b>Проверка #{task_id}:</b> "
        f"<code>{log_entry.latency_ms} мс</code> · "
        f"<code>{html.escape(log_entry.details)}</code>\n\n"
        + _render_task_card(updated_task, logs, stats)
    )
    await _safe_edit(call, result_text, task_actions_kb(updated_task))


@router.callback_query(F.data.startswith("task:prio:"))
async def cb_task_priority(call: CallbackQuery, repo: DatabaseRepository) -> None:
    task_id = _parse_int(_get_part(call.data, 2))
    new_prio = _parse_int(_get_part(call.data, 3))
    if task_id is None or new_prio not in (1, 2, 3):
        await call.answer("Некорректные параметры", show_alert=True)
        return
    updated = await repo.update_task_priority(
        task_id, new_prio, user_id=call.from_user.id
    )
    if not updated:
        await call.answer("Монитор не найден или нет доступа", show_alert=True)
        return
    task, logs, stats = await _load_card_view(repo, task_id, call.from_user.id)
    await _safe_edit(call, _render_task_card(task, logs, stats), task_actions_kb(task))
    await call.answer(f"Приоритет обновлён: P{new_prio}")


@router.callback_query(F.data.startswith("task:int:"))
async def cb_task_interval(call: CallbackQuery, repo: DatabaseRepository) -> None:
    task_id = _parse_int(_get_part(call.data, 2))
    new_iv = _parse_int(_get_part(call.data, 3))
    if task_id is None or new_iv is None or new_iv < 0:
        await call.answer("Некорректный интервал", show_alert=True)
        return
    updated = await repo.update_task_interval(
        task_id, new_iv, user_id=call.from_user.id
    )
    if not updated:
        await call.answer("Монитор не найден или нет доступа", show_alert=True)
        return
    task, logs, stats = await _load_card_view(repo, task_id, call.from_user.id)
    await _safe_edit(call, _render_task_card(task, logs, stats), task_actions_kb(task))
    await call.answer(f"Интервал: {_format_interval(new_iv)}")


@router.callback_query(F.data.startswith("task:thr:"))
async def cb_task_threshold(call: CallbackQuery, repo: DatabaseRepository) -> None:
    task_id = _parse_int(_get_part(call.data, 2))
    new_thr = _parse_int(_get_part(call.data, 3))
    if task_id is None or new_thr is None or not (1 <= new_thr <= 5):
        await call.answer("Некорректный порог алертов", show_alert=True)
        return
    updated = await repo.update_task_threshold(
        task_id, new_thr, user_id=call.from_user.id
    )
    if not updated:
        await call.answer("Монитор не найден или нет доступа", show_alert=True)
        return
    task, logs, stats = await _load_card_view(repo, task_id, call.from_user.id)
    await _safe_edit(call, _render_task_card(task, logs, stats), task_actions_kb(task))
    await call.answer(f"Порог алертов: {new_thr} ош.")


@router.callback_query(F.data.startswith("task:toggle:"))
async def cb_task_toggle(call: CallbackQuery, repo: DatabaseRepository) -> None:
    task_id = _parse_int(_get_part(call.data, 2))
    if task_id is None:
        await call.answer("Некорректный ID", show_alert=True)
        return
    new_state = await repo.toggle_task_enabled(task_id, user_id=call.from_user.id)
    if new_state is None:
        await call.answer("Монитор не найден или нет доступа", show_alert=True)
        return
    task, logs, stats = await _load_card_view(repo, task_id, call.from_user.id)
    await _safe_edit(call, _render_task_card(task, logs, stats), task_actions_kb(task))
    await call.answer("Авто-проверка включена" if new_state else "Поставлено на паузу")


@router.callback_query(F.data.startswith("task:logs:"))
async def cb_task_logs(call: CallbackQuery, repo: DatabaseRepository) -> None:
    task_id = _parse_int(_get_part(call.data, 2))
    if task_id is None:
        await call.answer("Некорректный ID", show_alert=True)
        return
    task = await repo.get_task_by_id(task_id, user_id=call.from_user.id)
    if not task:
        await call.answer("Монитор не найден или нет доступа", show_alert=True)
        return
    logs = await repo.get_task_logs(task_id, limit=10, user_id=call.from_user.id)
    if not logs:
        body = "История проверок пока пуста."
    else:
        lines = []
        for entry in logs:
            icon = "✅" if entry.result_status == "completed" else "❌"
            ts = (entry.executed_at or "")[:19].replace("T", " ")
            lines.append(
                f"{icon} <code>{ts}</code> · {entry.latency_ms} мс · "
                f"{html.escape(entry.details[:60])}"
            )
        body = "\n".join(lines)
    text = (
        f"<b>Последние проверки #{task.id} "
        f"({html.escape(task.title)}):</b>\n\n{body}"
    )
    await _safe_edit(call, text, task_actions_kb(task))
    await call.answer()


@router.callback_query(F.data.startswith("task:logscsv:"))
async def cb_task_logs_csv(
    call: CallbackQuery, report_service: ReportGeneratorService
) -> None:
    task_id = _parse_int(_get_part(call.data, 2))
    if task_id is None:
        await call.answer("Некорректный ID", show_alert=True)
        return
    csv_data = await report_service.export_task_logs_csv(call.from_user.id, task_id)
    if csv_data is None:
        await call.answer("Монитор не найден или нет доступа", show_alert=True)
        return
    doc = BufferedInputFile(
        csv_data.encode("utf-8-sig"),
        filename=f"tt_bot_task_{task_id}_logs.csv",
    )
    await call.message.answer_document(
        doc, caption=f"История проверок монитора #{task_id} (CSV)."
    )
    await call.answer("Логи выгружены")


@router.callback_query(F.data.startswith("task:del_ask:"))
async def cb_task_delete_ask(call: CallbackQuery, repo: DatabaseRepository) -> None:
    task_id = _parse_int(_get_part(call.data, 2))
    if task_id is None:
        await call.answer("Некорректный ID", show_alert=True)
        return
    task = await repo.get_task_by_id(task_id, user_id=call.from_user.id)
    if not task:
        await call.answer("Монитор не найден или нет доступа", show_alert=True)
        return
    await _safe_edit(
        call,
        f"Удалить монитор <b>#{task.id} «{html.escape(task.title)}»</b> "
        "и всю его историю логов?",
        delete_confirm_kb(task_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("task:del_confirm:"))
async def cb_task_delete_confirm(call: CallbackQuery, repo: DatabaseRepository) -> None:
    task_id = _parse_int(_get_part(call.data, 2))
    if task_id is None:
        await call.answer("Некорректный ID", show_alert=True)
        return
    deleted = await repo.delete_task(task_id, user_id=call.from_user.id)
    if not deleted:
        await call.answer("Монитор уже удалён или нет доступа", show_alert=True)
        return
    tasks = await repo.get_user_tasks(call.from_user.id, limit=PAGE_SIZE)
    total = await repo.count_user_tasks(call.from_user.id)
    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    await _safe_edit(
        call,
        f"🗑 Монитор #{task_id} удалён.",
        tasks_list_kb(tasks, 0, total_pages, "all"),
    )
    await call.answer("Удалено")


@router.callback_query(F.data == "menu:stats")
async def cb_stats(call: CallbackQuery, repo: DatabaseRepository) -> None:
    stats = await repo.get_analytics_summary(call.from_user.id)
    await _safe_edit(call, _render_stats_text(stats), main_menu_kb())
    await call.answer()


@router.callback_query(F.data == "menu:export")
async def cb_export_csv(
    call: CallbackQuery, report_service: ReportGeneratorService
) -> None:
    csv_data = await report_service.export_user_tasks_csv(call.from_user.id)
    doc = BufferedInputFile(
        csv_data.encode("utf-8-sig"),
        filename=f"tt_bot_report_{call.from_user.id}.csv",
    )
    await call.message.answer_document(
        doc, caption="Выгрузка реестра ваших мониторов в формате CSV."
    )
    await call.answer("Отчёт сформирован")


@router.message()
async def fallback_message(message: Message) -> None:
    raw = (message.text or "").strip()
    if raw.startswith(("http://", "https://", "tcp://")):
        safe_ep = html.escape(raw.split()[0])
        await message.answer(
            "Похоже на эндпоинт. Чтобы добавить его одной строкой, отправьте:\n"
            f"<code>/add Service {safe_ep} 5</code>",
            reply_markup=main_menu_kb(),
            parse_mode="HTML",
        )
        return
    await message.answer(
        "Не понял сообщение. Используйте меню ниже или /help:",
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )
