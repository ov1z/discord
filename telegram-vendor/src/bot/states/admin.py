"""FSM states for the button-driven admin panel input flows."""
from aiogram.fsm.state import State, StatesGroup


class AdminStates(StatesGroup):
    ADD_PRODUCT = State()   # waiting for "name|price|description"
    RESTOCK = State()       # waiting for stock lines (product_id in state data)
    SET_NOTE = State()      # waiting for note text (product_id in state data)
    SET_TIERS = State()     # waiting for price tiers (product_id in state data)
    BROADCAST = State()     # waiting for the message to send to all users
