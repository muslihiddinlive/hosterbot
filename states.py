from aiogram.fsm.state import State, StatesGroup


class ContactAdmin(StatesGroup):
    waiting_message = State()


class AdminReply(StatesGroup):
    waiting_reply_text = State()


class AddBot(StatesGroup):
    waiting_code = State()
    waiting_requirements = State()
    waiting_build_cmd = State()
    waiting_start_cmd = State()
    waiting_env = State()


class ConfirmDelete(StatesGroup):
    waiting_text = State()


class AdminMessageUser(StatesGroup):
    waiting_text = State()
