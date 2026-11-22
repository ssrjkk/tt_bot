from aiogram.fsm.state import State, StatesGroup


class CreateTaskSG(StatesGroup):
    waiting_for_title = State()
    waiting_for_category = State()
    waiting_for_endpoint = State()
    waiting_for_interval = State()
    waiting_for_priority = State()
