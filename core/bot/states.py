"""FSM state groups for editorial workflows."""

from aiogram.fsm.state import State, StatesGroup


class EditNewsStates(StatesGroup):
    waiting_for_text = State()


class AddLinkStates(StatesGroup):
    waiting_for_url = State()
