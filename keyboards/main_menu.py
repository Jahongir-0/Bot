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
    """BOSH MENU: 10 ta bo‘lim, 2 ustunli tartib. Savdo bo‘limlari (bozor, auksion, almashish, sovg‘a) — «Savdo markazi» ichida."""
    b = InlineKeyboardBuilder()
    b.row(_nav(owner_id, "👤 PROFILIM", "profile"), _nav(owner_id, "🎴 KARTALARIM", "cards"))
    b.row(_nav(owner_id, "🛒 SAVDO MARKAZI", "savdo"), _nav(owner_id, "🎮 O‘YINLAR", "grp_play"))
    b.row(_nav(owner_id, "🎁 KUNLIK BONUS", "daily"), _nav(owner_id, "🏆 REYTING", "ranking"))
    b.row(_nav(owner_id, "🔍 QIDIRUV", "search"), _nav(owner_id, "👥 DO‘STLAR", "referral"))
    b.row(
        _nav(owner_id, "❓ QO‘LLANMA", "help"),
        InlineKeyboardButton(text="💬 ALOQA", callback_data=SupCb(owner_id=owner_id, act="v").pack()),
    )
    if is_admin:
        b.row(InlineKeyboardButton(text="🛡️ ADMIN PANEL", callback_data=AdminCb(owner_id=owner_id, section="home").pack()))
    return b.as_markup()


def savdo_hub_kb(owner_id: int) -> InlineKeyboardMarkup:
    """SAVDO MARKAZI: karta bilan bog‘liq barcha savdo amallari bir joyda."""
    b = InlineKeyboardBuilder()
    b.row(
        _nav(owner_id, "🛒 BOZOR", "market"),
        InlineKeyboardButton(text="🔨 AUKSION", callback_data=AucCb(owner_id=owner_id, act="v").pack()),
    )
    b.row(
        InlineKeyboardButton(text="🤝 ALMASHISH", callback_data=TradeCb(owner_id=owner_id, act="v").pack()),
        _nav(owner_id, "🎁 SOVG‘A BERISH", "give"),
    )
    b.row(
        _nav(owner_id, "🔍 QIDIRUV", "search"),
        InlineKeyboardButton(text="💖 ISTAKLAR", callback_data=WishCb(owner_id=owner_id, act="v").pack()),
    )
    b.row(_nav(owner_id, "🏠 BOSH MENU", "home"))
    return b.as_markup()


def games_hub_kb(owner_id: int, has_games: bool = True, has_pack: bool = True) -> InlineKeyboardMarkup:
    """O‘YINLAR MARKAZI: admin o‘chirgan bo‘limlar umuman ko‘rsatilmaydi."""
    b = InlineKeyboardBuilder()
    buttons = []
    if has_games:
        buttons.append(InlineKeyboardButton(text="🎲 MINI O‘YINLAR", callback_data=GameCb(owner_id=owner_id, act="v").pack()))
    buttons.append(InlineKeyboardButton(text="🧠 VIKTORINA", callback_data=QuizCb(owner_id=owner_id, act="v").pack()))
    buttons.append(_nav(owner_id, "🎯 EVENTLAR", "event"))
    buttons.append(_nav(owner_id, "📋 VAZIFALAR", "quests"))
    if has_pack:
        buttons.append(_nav(owner_id, "🎁 PAKETLAR", "packs"))
    b.add(*buttons)
    b.adjust(2)
    b.row(_nav(owner_id, "🏠 BOSH MENU", "home"))
    return b.as_markup()


def profile_kb(owner_id: int) -> InlineKeyboardMarkup:
    """PROFIL markazi: faqat shaxsiy narsalar."""
    b = InlineKeyboardBuilder()
    b.row(
        _nav(owner_id, "🎴 KARTALARIM", "cards"),
        InlineKeyboardButton(text="🎨 BEZAK", callback_data=AchCb(owner_id=owner_id, act="frames").pack()),
    )
    b.row(_nav(owner_id, "🎁 KUNLIK BONUS", "daily"), _nav(owner_id, "📋 VAZIFALAR", "quests"))
    b.row(
        InlineKeyboardButton(text="💎 VIP", callback_data=VipCb(owner_id=owner_id, act="v").pack()),
        _nav(owner_id, "⭐ STARS DO‘KONI", "stars"),
    )
    b.row(
        InlineKeyboardButton(text="🏅 YUTUQLAR", callback_data=AchCb(owner_id=owner_id, act="v").pack()),
        InlineKeyboardButton(text="💖 ISTAKLAR", callback_data=WishCb(owner_id=owner_id, act="v").pack()),
    )
    b.row(
        _nav(owner_id, "👥 DO‘STLAR", "referral"),
        InlineKeyboardButton(text="🔔 XABARLAR", callback_data=NotifCb(owner_id=owner_id, act="v").pack()),
    )
    b.row(_nav(owner_id, "🏠 BOSH MENU", "home"))
    return b.as_markup()


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
