from aiogram.fsm.state import State, StatesGroup


class AddMovie(StatesGroup):
    file = State()
    code = State()
    title = State()
    desc = State()
    preview = State()


class DelMovie(StatesGroup):
    code = State()


class Broadcast(StatesGroup):
    msg = State()
    confirm = State()


class AddChannel(StatesGroup):
    ref = State()
    link = State()


class SetPostChannel(StatesGroup):
    ref = State()


class AddAdmin(StatesGroup):
    uid = State()


class BanUser(StatesGroup):
    uid = State()
