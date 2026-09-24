"""FSM states for the admin PayPay login flow."""
from aiogram.fsm.state import State, StatesGroup


class LoginStates(StatesGroup):
    WAITING_CREDENTIALS = State()
    WAITING_OTP = State()
