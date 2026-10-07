from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from src.models import TaskDTO
from typing import List


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Создать задачу / узел", callback_data="menu:create")],
        [InlineKeyboardButton(text="Список моих задач", callback_data="menu:list")],
        [InlineKeyboardButton(text="Аналитика и SLA", callback_data="menu:stats")],
        [InlineKeyboardButton(text="Экспорт отчета (CSV)", callback_data="menu:export")]
    ])


def category_selection_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Backend API", callback_data="cat:backend"),
            InlineKeyboardButton(text="QA AutoTest", callback_data="cat:qa_test")
        ],
        [
            InlineKeyboardButton(text="RPC Node", callback_data="cat:rpc_node"),
            InlineKeyboardButton(text="DevOps / VPS", callback_data="cat:devops")
        ]
    ])


def tasks_list_kb(tasks: List[TaskDTO]) -> InlineKeyboardMarkup:
    rows = []
    status_icons = {"pending": "⏳", "running": "🔄", "completed": "✅", "failed": "❌"}
    for t in tasks:
        icon = status_icons.get(t.status, "📌")
        rows.append([
            InlineKeyboardButton(
                text=f"{icon} #{t.id} [{t.category}] {t.title} (P{t.priority})",
                callback_data=f"task:view:{t.id}"
            )
        ])
    rows.append([InlineKeyboardButton(text="В главное меню", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def task_actions_kb(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Запустить проверку (WorkerPool)", callback_data=f"task:run:{task_id}")],
        [
            InlineKeyboardButton(text="Приоритет Max (3)", callback_data=f"task:prio:{task_id}:3"),
            InlineKeyboardButton(text="Логи запуска", callback_data=f"task:logs:{task_id}")
        ],
        [InlineKeyboardButton(text="Удалить задачу", callback_data=f"task:del:{task_id}")],
        [InlineKeyboardButton(text="К списку задач", callback_data="menu:list")]
    ])
