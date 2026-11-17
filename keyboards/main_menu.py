from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from utils.callback_data import AdminCb, DropCb, NavCb


def main_menu_kb(owner_id: int, is_admin: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🎴 KARTALAR",
            callback_data=NavCb(owner_id=owner_id, target="cards").pack(),
        ),
        InlineKeyboardButton(
            text="🎁 BONUS",
            callback_data=NavCb(owner_id=owner_id, target="daily").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🛒 MARKET",
            callback_data=NavCb(owner_id=owner_id, target="market").pack(),
        ),
        InlineKeyboardButton(
            text="🔍 QIDIRUV",
            callback_data=NavCb(owner_id=owner_id, target="search").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🏆 REYTING",
            callback_data=NavCb(owner_id=owner_id, target="ranking").pack(),
        ),
        InlineKeyboardButton(
            text="👤 PROFIL",
            callback_data=NavCb(owner_id=owner_id, target="profile").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🎯 EVENT",
            callback_data=NavCb(owner_id=owner_id, target="event").pack(),
        ),
        InlineKeyboardButton(
            text="⭐ COINS (STARS)",
            callback_data=NavCb(owner_id=owner_id, target="stars").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🤝 REFERAL",
            callback_data=NavCb(owner_id=owner_id, target="referral").pack(),
        ),
        InlineKeyboardButton(
            text="📋 VAZIFALAR",
            callback_data=NavCb(owner_id=owner_id, target="quests").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="❓ YORDAM",
            callback_data=NavCb(owner_id=owner_id, target="help").pack(),
        )
    )
    if is_admin:
        builder.row(
            InlineKeyboardButton(
                text="🛡️ ADMIN PANEL",
                callback_data=AdminCb(owner_id=owner_id, section="home").pack(),
            )
        )
    return builder.as_markup()


def profile_kb(owner_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🎴 KOLLEKSIYA",
            callback_data=NavCb(owner_id=owner_id, target="cards").pack(),
        ),
        InlineKeyboardButton(
            text="🪙 MARKET",
            callback_data=NavCb(owner_id=owner_id, target="market").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🏆 REYTING",
            callback_data=NavCb(owner_id=owner_id, target="ranking").pack(),
        ),
        InlineKeyboardButton(
            text="🔍 QIDIRUV",
            callback_data=NavCb(owner_id=owner_id, target="search").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🎁 DAILY",
            callback_data=NavCb(owner_id=owner_id, target="daily").pack(),
        ),
        InlineKeyboardButton(
            text="⭐ COINS OLISH",
            callback_data=NavCb(owner_id=owner_id, target="stars").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🤝 REFERAL",
            callback_data=NavCb(owner_id=owner_id, target="referral").pack(),
        ),
        InlineKeyboardButton(
            text="📋 VAZIFALAR",
            callback_data=NavCb(owner_id=owner_id, target="quests").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        )
    )
    return builder.as_markup()


def back_home_kb(owner_id: int, back_target: str = "home") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=NavCb(owner_id=owner_id, target=back_target).pack(),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def drop_claim_kb(drop_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🎴 KARTANI OLISH",
            callback_data=DropCb(action="claim", drop_id=drop_id).pack(),
        )
    )
    return builder.as_markup()
