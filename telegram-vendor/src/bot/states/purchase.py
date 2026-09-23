"""FSM states for the buyer purchase flow."""
from aiogram.fsm.state import State, StatesGroup


class PurchaseStates(StatesGroup):
    WAITING_PAYPAY_LINK = State()
