from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from src.database import DatabaseRepository
from src.services import AsyncWorkerPool, ReportGeneratorService
from src.models import UserDTO, TaskDTO
from src.states import CreateTaskSG
from src.keyboards import (
    main_menu_kb,
    category_selection_kb,
    tasks_list_kb,
    task_actions_kb,
)

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, repo: DatabaseRepository) -> None:
    user_dto = UserDTO(
        id=message.from_user.id,
        username=message.from_user.username or "anonymous",
        full_name=message.from_user.full_name or "Student GUAP",
    )
    await repo.upsert_user(user_dto)
    text = (
        f"Приветствую, {user_dto.full_name}!\n\n"
        "Автоматизированная система управления задачами и мониторинга сервисов (Вариант № 18).\n"
        "Выберите действие в меню ниже:"
    )
    await message.answer(text, reply_markup=main_menu_kb(), parse_mode="HTML")


@router.callback_query(F.data == "menu:home")
async def cb_home(call: CallbackQuery) -> None:
    await call.message.edit_text(
        "Главное меню управления задачами и мониторинга:", reply_markup=main_menu_kb()
    )
    await call.answer()


@router.callback_query(F.data == "menu:create")
async def cb_create_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CreateTaskSG.waiting_for_title)
    await call.message.edit_text("Введите название новой задачи или проверяемого узла:")
    await call.answer()


@router.message(CreateTaskSG.waiting_for_title)
async def fsm_process_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=message.text.strip())
    await state.set_state(CreateTaskSG.waiting_for_category)
    await message.answer(
        "Выберите категорию задачи:", reply_markup=category_selection_kb()
    )


@router.callback_query(CreateTaskSG.waiting_for_category, F.data.startswith("cat:"))
async def fsm_process_category(call: CallbackQuery, state: FSMContext) -> None:
    category = call.data.split(":")[1]
    await state.update_data(category=category)
    await state.set_state(CreateTaskSG.waiting_for_endpoint)
    await call.message.edit_text(
        "Введите целевой эндпоинт для проверки (например, node-01.internal/health):"
    )
    await call.answer()


@router.message(CreateTaskSG.waiting_for_endpoint)
async def fsm_process_endpoint(message: Message, state: FSMContext) -> None:
    await state.update_data(target_endpoint=message.text.strip())
    await state.set_state(CreateTaskSG.waiting_for_priority)
    await message.answer(
        "Укажите приоритет задачи числом (1 — низкий, 2 — обычный, 3 — критический):"
    )


@router.message(CreateTaskSG.waiting_for_priority)
async def fsm_process_priority(
    message: Message, state: FSMContext, repo: DatabaseRepository
) -> None:
    try:
        prio = int(message.text.strip())
        if prio not in (1, 2, 3):
            raise ValueError()
    except ValueError:
        await message.answer("Ошибка: введите число 1, 2 или 3.")
        return

    data = await state.get_data()
    await state.clear()
    new_task = TaskDTO(
        id=None,
        user_id=message.from_user.id,
        title=data["title"],
        category=data["category"],
        target_endpoint=data["target_endpoint"],
        priority=prio,
    )
    task_id = await repo.create_task(new_task)
    await message.answer(
        f"Задача #{task_id} «{new_task.title}» успешно сохранена в БД!",
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "menu:list")
async def cb_tasks_list(call: CallbackQuery, repo: DatabaseRepository) -> None:
    tasks = await repo.get_user_tasks(call.from_user.id)
    if not tasks:
        await call.message.edit_text(
            "Список ваших задач пуст.", reply_markup=main_menu_kb()
        )
    else:
        await call.message.edit_text(
            "Выберите задачу для управления:", reply_markup=tasks_list_kb(tasks)
        )
    await call.answer()


@router.callback_query(F.data.startswith("task:view:"))
async def cb_task_view(call: CallbackQuery, repo: DatabaseRepository) -> None:
    task_id = int(call.data.split(":")[2])
    task = await repo.get_task_by_id(task_id)
    if not task:
        await call.answer("Задача не найдена", show_alert=True)
        return
    info = (
        f"Карточка задачи #{task.id}\n"
        f"▪️ Название: {task.title}\n"
        f"▪️ Категория: {task.category}\n"
        f"▪️ Эндпоинт: {task.target_endpoint}\n"
        f"▪️ Приоритет: {task.priority}\n"
        f"▪️ Статус: {task.status.upper()}\n"
        f"▪️ Обновлено: {task.updated_at}"
    )
    await call.message.edit_text(
        info, reply_markup=task_actions_kb(task.id), parse_mode="HTML"
    )
    await call.answer()


@router.callback_query(F.data.startswith("task:run:"))
async def cb_task_run(
    call: CallbackQuery, worker_pool: AsyncWorkerPool, repo: DatabaseRepository
) -> None:
    task_id = int(call.data.split(":")[2])
    log_entry = await worker_pool.execute_task_immediately(task_id)
    task = await repo.get_task_by_id(task_id)
    result_text = (
        f"Результат выполнения задачи #{task_id}\n"
        f"▪️ Итоговый статус: {log_entry.result_status.upper()}\n"
        f"▪️ Задержка (Latency): {log_entry.latency_ms} мс\n"
        f"▪️ Детали: {log_entry.details}"
    )
    await call.message.edit_text(
        result_text, reply_markup=task_actions_kb(task.id), parse_mode="HTML"
    )
    await call.answer("Проверка выполнена!")


@router.callback_query(F.data.startswith("task:del:"))
async def cb_task_delete(call: CallbackQuery, repo: DatabaseRepository) -> None:
    task_id = int(call.data.split(":")[2])
    await repo.delete_task(task_id)
    tasks = await repo.get_user_tasks(call.from_user.id)
    await call.message.edit_text(
        "Задача удалена из базы данных.", reply_markup=tasks_list_kb(tasks)
    )
    await call.answer()


@router.callback_query(F.data == "menu:stats")
async def cb_stats(call: CallbackQuery, repo: DatabaseRepository) -> None:
    stats = await repo.get_analytics_summary(call.from_user.id)
    text = (
        "Аналитическая сводка по задачам и узлам:\n\n"
        f"▪️ Всего задач в БД: {stats['total']}\n"
        f"▪️ Успешно выполнено (Completed): {stats['completed']}\n"
        f"▪️ В ожидании (Pending): {stats['pending']}\n"
        f"▪️ Ошибки (Failed): {stats['failed']}\n"
        f"▪️ Среднее время отклика (Latency): {stats['avg_latency_ms']} мс"
    )
    await call.message.edit_text(text, reply_markup=main_menu_kb(), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data == "menu:export")
async def cb_export_csv(
    call: CallbackQuery, report_service: ReportGeneratorService
) -> None:
    csv_data = await report_service.export_user_tasks_csv(call.from_user.id)
    doc = BufferedInputFile(
        csv_data.encode("utf-8-sig"), filename=f"tasks_report_{call.from_user.id}.csv"
    )
    await call.message.answer_document(
        doc, caption="Выгрузка реестра ваших задач в формате CSV."
    )
    await call.answer()
