"""Mini o‘yin, viktorina va savdo handlerlari uchun umumiy holatlar va doimiylar."""

from aiogram.fsm.state import State, StatesGroup

LINE_SEP = "━━━━━━━━━━━━━━━"


class V2UserStates(StatesGroup):
    waiting_auction_bid = State()
    waiting_user_auc_price = State()
    waiting_support_subject = State()
    waiting_support_body = State()
    waiting_support_reply = State()
    waiting_trade_partner = State()
