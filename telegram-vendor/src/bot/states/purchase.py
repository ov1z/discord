"""FSM states for the buyer purchase flow."""
from aiogram.fsm.state import State, StatesGroup


class PurchaseStates(StatesGroup):
    CHOOSING_QUANTITY = State()   # waiting for a custom quantity (product_id in data)
    WAITING_PAYPAY_LINK = State()
