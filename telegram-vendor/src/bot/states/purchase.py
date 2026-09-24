"""FSM states for the buyer purchase flow."""
from aiogram.fsm.state import State, StatesGroup


class PurchaseStates(StatesGroup):
    CHOOSING_QUANTITY = State()
    WAITING_PAYPAY_LINK = State()
    WAITING_TRANSACTION_ID = State()
