from typing import List

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.models import TaskDTO

STATUS_ICONS = {
    "pending": "·",
    "running": "…",
    "completed": "✓",
    "failed": "✗",
}

INTERVAL_CYCLE = (0, 60, 300, 900)
THRESHOLD_CYCLE = (1, 2, 3)


def _truncate(text: str, limit: int = 34) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _next_interval(current: int) -> int:
    if current in INTERVAL_CYCLE:
        idx = (INTERVAL_CYCLE.index(current) + 1) % len(INTERVAL_CYCLE)
        return INTERVAL_CYCLE[idx]
    return 60


def _next_threshold(current: int) -> int:
    if current in THRESHOLD_CYCLE:
        idx = (THRESHOLD_CYCLE.index(current) + 1) % len(THRESHOLD_CYCLE)
        return THRESHOLD_CYCLE[idx]
    return 2


def _interval_short(sec: int) -> str:
    return "вручную" if sec <= 0 else f"{sec // 60}м"


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="+ Добавить монитор",
                    callback_data="menu:create",
                ),
                InlineKeyboardButton(
                    text="Мои мониторы",
                    callback_data="menu:list:all:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Аналитика и SLA",
                    callback_data="menu:stats",
                ),
                InlineKeyboardButton(
                    text="Инциденты",
                    callback_data="menu:incidents",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Дерево зависимостей",
                    callback_data="menu:tree",
                ),
                InlineKeyboardButton(
                    text="↻ Проверить все",
                    callback_data="menu:run_all",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="↓ Экспорт CSV",
                    callback_data="menu:export",
                ),
            ],
        ]
    )


def incidents_kb(has_incidents: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if has_incidents:
        rows.append(
            [
                InlineKeyboardButton(
                    text="↻ Перепроверить упавшие",
                    callback_data="menu:run_failed",
                ),
                InlineKeyboardButton(
                    text="✗ Список с ошибкой",
                    callback_data="menu:list:failed:0",
                ),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text="Все мониторы", callback_data="menu:list:all:0"),
            InlineKeyboardButton(text="Меню", callback_data="menu:home"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_fsm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✗ Отмена", callback_data="fsm:cancel")]
        ]
    )


def category_selection_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Backend API", callback_data="cat:backend"),
                InlineKeyboardButton(text="QA AutoTest", callback_data="cat:qa_test"),
            ],
            [
                InlineKeyboardButton(text="RPC Node", callback_data="cat:rpc_node"),
                InlineKeyboardButton(text="DevOps / VPS", callback_data="cat:devops"),
            ],
            [InlineKeyboardButton(text="✗ Отмена", callback_data="fsm:cancel")],
        ]
    )


def interval_selection_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Только вручную", callback_data="int:0"),
                InlineKeyboardButton(text="Каждую 1 мин", callback_data="int:60"),
            ],
            [
                InlineKeyboardButton(text="Каждые 5 мин", callback_data="int:300"),
                InlineKeyboardButton(text="Каждые 15 мин", callback_data="int:900"),
            ],
            [InlineKeyboardButton(text="✗ Отмена", callback_data="fsm:cancel")],
        ]
    )


def priority_selection_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1 — Низкий", callback_data="prio:1"),
                InlineKeyboardButton(text="2 — Обычный", callback_data="prio:2"),
                InlineKeyboardButton(text="3 — Критический", callback_data="prio:3"),
            ],
            [InlineKeyboardButton(text="✗ Отмена", callback_data="fsm:cancel")],
        ]
    )


def _build_filter_row(status_filter: str) -> List[InlineKeyboardButton]:
    options = (
        ("all", "Все"),
        ("completed", "✓"),
        ("failed", "✗"),
        ("pending", "·"),
    )
    return [
        InlineKeyboardButton(
            text=f"• {label} •" if status_filter == key else label,
            callback_data=f"menu:list:{key}:0",
        )
        for key, label in options
    ]


def _build_pagination_row(
    page: int, total_pages: int, status_filter: str
) -> List[InlineKeyboardButton]:
    nav: List[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="←",
                callback_data=f"menu:list:{status_filter}:{page - 1}",
            )
        )
    nav.append(
        InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}",
            callback_data="noop",
        )
    )
    if page + 1 < total_pages:
        nav.append(
            InlineKeyboardButton(
                text="→",
                callback_data=f"menu:list:{status_filter}:{page + 1}",
            )
        )
    return nav


def tasks_list_kb(
    tasks: List[TaskDTO],
    page: int = 0,
    total_pages: int = 1,
    status_filter: str = "all",
) -> InlineKeyboardMarkup:
    rows = [_build_filter_row(status_filter)]

    for task in tasks:
        icon = STATUS_ICONS.get(task.status, "·")
        pause = "‖" if not task.enabled else ""
        label = f"{icon}{pause} #{task.id} {_truncate(task.title)} (P{task.priority})"
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"task:view:{task.id}")]
        )

    if total_pages > 1:
        rows.append(_build_pagination_row(page, total_pages, status_filter))

    rows.append(
        [
            InlineKeyboardButton(text="‖ Пауза всех", callback_data="menu:pause_all"),
            InlineKeyboardButton(
                text="▸ Запустить всех", callback_data="menu:resume_all"
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text="↻ Проверить все", callback_data="menu:run_all"),
            InlineKeyboardButton(text="Меню", callback_data="menu:home"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def task_actions_kb(task: TaskDTO) -> InlineKeyboardMarkup:
    next_prio = (task.priority % 3) + 1
    next_iv = _next_interval(task.check_interval_sec)
    next_thr = _next_threshold(task.alert_threshold)
    toggle_label = (
        "‖ Пауза авто-проверки" if task.enabled else "▸ Возобновить авто-проверку"
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="▸ Проверить сейчас",
                    callback_data=f"task:run:{task.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"P{task.priority} → P{next_prio}",
                    callback_data=f"task:prio:{task.id}:{next_prio}",
                ),
                InlineKeyboardButton(
                    text=f"интервал: {_interval_short(task.check_interval_sec)} → "
                    f"{_interval_short(next_iv)}",
                    callback_data=f"task:int:{task.id}:{next_iv}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"Порог {task.alert_threshold} → {next_thr}",
                    callback_data=f"task:thr:{task.id}:{next_thr}",
                ),
                InlineKeyboardButton(
                    text=toggle_label,
                    callback_data=f"task:toggle:{task.id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Логи (10)",
                    callback_data=f"task:logs:{task.id}",
                ),
                InlineKeyboardButton(
                    text="↓ Логи CSV",
                    callback_data=f"task:logscsv:{task.id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✗ Удалить",
                    callback_data=f"task:del_ask:{task.id}",
                ),
                InlineKeyboardButton(
                    text="← К списку",
                    callback_data="menu:list:all:0",
                ),
            ],
        ]
    )


def delete_confirm_kb(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✓ Да, удалить",
                    callback_data=f"task:del_confirm:{task_id}",
                ),
                InlineKeyboardButton(
                    text="✗ Отмена",
                    callback_data=f"task:view:{task_id}",
                ),
            ]
        ]
    )
