"""FSM states for the button-driven admin panel input flows."""
from aiogram.fsm.state import State, StatesGroup


class AdminStates(StatesGroup):
    ADD_PRODUCT = State()
    RESTOCK = State()
    SET_NOTE = State()
    SET_DESC = State()
    SET_TIERS = State()
    BROADCAST = State()
