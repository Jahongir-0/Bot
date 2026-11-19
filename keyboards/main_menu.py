from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from utils.callback_data import (
    AchCb,
    AdminCb,
    AucCb,
    DropCb,
    GameCb,
    NavCb,
    NotifCb,
    QuizCb,
    SupCb,
    TradeCb,
    VipCb,
    WishCb,
)


def _nav(owner_id: int, text: str, target: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=NavCb(owner_id=owner_id, target=target).pack())


def main_menu_kb(owner_id: int, is_admin: bool = False) -> InlineKeyboardMarkup:
    """Bosh menyu: 5 qator, har bir tugma aniq. Qolgan bo'limlar guruh-menyularda."""
    b = InlineKeyboardBuilder()
    b.row(_nav(owner_id, "🎴 KARTALARIM", "cards"), _nav(owner_id, "🎁 KUNLIK BONUS", "daily"))
    b.row(_nav(owner_id, "🎮 O‘YIN VA MUSOBAQA", "grp_play"), _nav(owner_id, "🛒 SAVDO", "grp_trade"))
    b.row(_nav(owner_id, "🏆 REYTING", "ranking"), _nav(owner_id, "👤 PROFIL", "profile"))
    b.row(_nav(owner_id, "💎 VIP VA STARS", "grp_vip"), _nav(owner_id, "👥 DO‘STLAR", "referral"))
    b.row(
        InlineKeyboardButton(text="🔔 XABARLAR", callback_data=NotifCb(owner_id=owner_id, act="v").pack()),
        InlineKeyboardButton(text="💬 ALOQA", callback_data=SupCb(owner_id=owner_id, act="v").pack()),
    )
    b.row(_nav(owner_id, "❓ QANDAY O‘YNALADI?", "help"))
    if is_admin:
        b.row(InlineKeyboardButton(text="🛡️ ADMIN PANEL", callback_data=AdminCb(owner_id=owner_id, section="home").pack()))
    return b.as_markup()


GROUP_MENUS = {
    "grp_play": (
        "🎮 <b>O‘YIN VA MUSOBAQA</b>\n\nO‘ynang, coin va XP yutib oling:\n\n"
        "🎲 <b>Tosh o‘yini</b> — sonni toping, coin yuting\n"
        "🧠 <b>Viktorina</b> — savollarga birinchi javob bering\n"
        "🎯 <b>Event</b> — cheklangan maxsus kartalar\n"
        "📋 <b>Vazifalar</b> — bajaring, mukofot oling\n"
        "🏅 <b>Yutuqlar</b> — mavsum va nishonlar"
    ),
    "grp_trade": (
        "🛒 <b>SAVDO</b>\n\nKartalarni soting, sotib oling va almashing:\n\n"
        "🛒 <b>Bozor</b> — kartalarni narx bilan soting/sotib oling\n"
        "🔨 <b>Auksion</b> — eng yuqori taklif yutadi\n"
        "🤝 <b>Almashish</b> — kartani karta bilan almashtiring\n"
        "💖 <b>Istaklar</b> — kerakli kartani yozib qo‘ying, chiqsa xabar keladi\n"
        "🔍 <b>Qidiruv</b> — kartani nomi bo‘yicha toping"
    ),
    "grp_vip": (
        "💎 <b>VIP VA STARS</b>\n\n"
        "💎 <b>VIP</b> — reklamasiz, bonus coin va ko‘proq imkoniyat\n"
        "⭐ <b>Stars</b> — Telegram Stars bilan coin sotib oling"
    ),
}


def group_menu_kb(owner_id: int, group: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if group == "grp_play":
        b.row(
            InlineKeyboardButton(text="🎲 TOSH O‘YINI", callback_data=GameCb(owner_id=owner_id, act="v").pack()),
            InlineKeyboardButton(text="🧠 VIKTORINA", callback_data=QuizCb(owner_id=owner_id, act="v").pack()),
        )
        b.row(_nav(owner_id, "🎯 EVENT", "event"), _nav(owner_id, "📋 VAZIFALAR", "quests"))
        b.row(InlineKeyboardButton(text="🏅 YUTUQLAR VA MAVSUM", callback_data=AchCb(owner_id=owner_id, act="v").pack()))
    elif group == "grp_trade":
        b.row(_nav(owner_id, "🛒 BOZOR", "market"), InlineKeyboardButton(text="🔨 AUKSION", callback_data=AucCb(owner_id=owner_id, act="v").pack()))
        b.row(
            InlineKeyboardButton(text="🤝 ALMASHISH", callback_data=TradeCb(owner_id=owner_id, act="v").pack()),
            InlineKeyboardButton(text="💖 ISTAKLAR", callback_data=WishCb(owner_id=owner_id, act="v").pack()),
        )
        b.row(_nav(owner_id, "🔍 QIDIRUV", "search"))
    elif group == "grp_vip":
        b.row(
            InlineKeyboardButton(text="💎 VIP", callback_data=VipCb(owner_id=owner_id, act="v").pack()),
            _nav(owner_id, "⭐ STARS", "stars"),
        )
    b.row(_nav(owner_id, "🏠 BOSH MENU", "home"))
    return b.as_markup()


def profile_kb(owner_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎴 KOLLEKSIYA", callback_data=NavCb(owner_id=owner_id, target="cards").pack()),
        InlineKeyboardButton(text="🎨 BEZAK & YUTUQLAR", callback_data=AchCb(owner_id=owner_id, act="frames").pack()),
    )
    builder.row(
        InlineKeyboardButton(text="🪙 MARKET", callback_data=NavCb(owner_id=owner_id, target="market").pack()),
        InlineKeyboardButton(text="🔨 AUCTION", callback_data=AucCb(owner_id=owner_id, act="v").pack()),
    )
    builder.row(
        InlineKeyboardButton(text="🏆 REYTING", callback_data=NavCb(owner_id=owner_id, target="ranking").pack()),
        InlineKeyboardButton(text="🔍 QIDIRUV", callback_data=NavCb(owner_id=owner_id, target="search").pack()),
    )
    builder.row(
        InlineKeyboardButton(text="🎁 DAILY", callback_data=NavCb(owner_id=owner_id, target="daily").pack()),
        InlineKeyboardButton(text="💎 VIP", callback_data=VipCb(owner_id=owner_id, act="v").pack()),
    )
    builder.row(
        InlineKeyboardButton(text="🤝 REFERAL", callback_data=NavCb(owner_id=owner_id, target="referral").pack()),
        InlineKeyboardButton(text="🔔 XABARLAR", callback_data=NotifCb(owner_id=owner_id, act="v").pack()),
    )
    builder.row(
        InlineKeyboardButton(text="🏠 BOSH MENU", callback_data=NavCb(owner_id=owner_id, target="home").pack())
    )
    return builder.as_markup()


def back_home_kb(owner_id: int, back_target: str = "home") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=NavCb(owner_id=owner_id, target=back_target).pack()),
        InlineKeyboardButton(text="🏠 BOSH MENU", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
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
