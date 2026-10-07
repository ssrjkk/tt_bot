from aiogram.fsm.state import StatesGroup, State


class CreateTaskSG(StatesGroup):
    waiting_for_title = State()
    waiting_for_category = State()
    waiting_for_endpoint = State()
    waiting_for_priority = State()
