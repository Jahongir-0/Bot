from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from utils.callback_data import (
    AchCb,
    AdminCb,
    AucCb,
    BotStoreCb,
    DropCb,
    GameCb,
    NavCb,
    NotifCb,
    OfferCb,
    QuizCb,
    SupCb,
    TradeCb,
    VipCb,
    WishCb,
)


def _nav(owner_id: int, text: str, target: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=NavCb(owner_id=owner_id, target=target).pack())


def main_menu_kb(owner_id: int, is_admin: bool = False) -> InlineKeyboardMarkup:
    """BOSH MENU (Bot 2.5):
    Profilim | Reyting
    Savdo    | Qidiruv
    Vip      | Stars
    Bonus    | O'yinlar
    Xabarlar | Aloqa
    Qo'llanma
    """
    b = InlineKeyboardBuilder()
    b.row(_nav(owner_id, "👤 Profilim", "profile"), _nav(owner_id, "🏆 Reyting", "ranking"))
    b.row(_nav(owner_id, "🛒 Savdo", "savdo"), _nav(owner_id, "🔍 Qidiruv", "search"))
    b.row(
        InlineKeyboardButton(text="💎 Vip", callback_data=VipCb(owner_id=owner_id, act="v").pack()),
        _nav(owner_id, "⭐ Stars", "stars"),
    )
    b.row(_nav(owner_id, "🎁 Bonus", "daily"), _nav(owner_id, "🎮 O‘yinlar", "grp_play"))
    b.row(
        InlineKeyboardButton(text="🔔 Xabarlar", callback_data=NotifCb(owner_id=owner_id, act="v").pack()),
        InlineKeyboardButton(text="💬 Aloqa", callback_data=SupCb(owner_id=owner_id, act="v").pack()),
    )
    b.row(_nav(owner_id, "❓ Qo‘llanma", "help"))
    if is_admin:
        b.row(InlineKeyboardButton(text="🛡️ Admin Panel", callback_data=AdminCb(owner_id=owner_id, section="home").pack()))
    return b.as_markup()


def savdo_hub_kb(owner_id: int) -> InlineKeyboardMarkup:
    """SAVDO MARKAZI (Bot 2.5):
    Market    | Bot Do'koni
    Auksion   | Event
    Almashish | Sovg'a berish
    Bosh Menu
    """
    b = InlineKeyboardBuilder()
    b.row(
        _nav(owner_id, "🛒 Market", "market"),
        InlineKeyboardButton(text="🏛 Bot Do‘koni", callback_data=BotStoreCb(owner_id=owner_id, act="v").pack()),
    )
    b.row(
        InlineKeyboardButton(text="🔨 Auksion", callback_data=AucCb(owner_id=owner_id, act="v").pack()),
        _nav(owner_id, "🎯 Event", "event"),
    )
    b.row(
        InlineKeyboardButton(text="🤝 Almashish", callback_data=TradeCb(owner_id=owner_id, act="v").pack()),
        _nav(owner_id, "🎁 Sovg‘a berish", "give"),
    )
    b.row(_nav(owner_id, "🏠 Bosh Menu", "home"))
    return b.as_markup()


def games_hub_kb(owner_id: int, has_games: bool = True, has_pack: bool = True) -> InlineKeyboardMarkup:
    """O‘YINLAR MARKAZI: admin o‘chirgan bo‘limlar umuman ko‘rsatilmaydi."""
    b = InlineKeyboardBuilder()
    buttons = []
    if has_games:
        buttons.append(InlineKeyboardButton(text="🎲 Mini o‘yinlar", callback_data=GameCb(owner_id=owner_id, act="v").pack()))
    buttons.append(InlineKeyboardButton(text="🧠 Viktorina", callback_data=QuizCb(owner_id=owner_id, act="v").pack()))
    buttons.append(_nav(owner_id, "🎯 Eventlar", "event"))
    buttons.append(_nav(owner_id, "📋 Vazifalar", "quests"))
    if has_pack:
        buttons.append(_nav(owner_id, "🎁 Paketlar", "packs"))
    b.add(*buttons)
    b.adjust(2)
    b.row(_nav(owner_id, "🏠 Bosh Menu", "home"))
    return b.as_markup()


def profile_kb(owner_id: int) -> InlineKeyboardMarkup:
    """PROFILIM (Bot 2.5):
    Kartalarim | Istaklar
    Yutuqlar   | Vazifalar
    Do'stlar   | Sozlamalar
    Takliflar  | Bosh Menu
    """
    b = InlineKeyboardBuilder()
    b.row(
        _nav(owner_id, "🎴 Kartalarim", "cards"),
        InlineKeyboardButton(text="💖 Istaklar", callback_data=WishCb(owner_id=owner_id, act="v").pack()),
    )
    b.row(
        InlineKeyboardButton(text="🏅 Yutuqlar", callback_data=AchCb(owner_id=owner_id, act="v").pack()),
        _nav(owner_id, "📋 Vazifalar", "quests"),
    )
    b.row(
        _nav(owner_id, "👥 Do‘stlar", "referral"),
        _nav(owner_id, "⚙️ Sozlamalar", "settings"),
    )
    b.row(
        InlineKeyboardButton(text="📩 Takliflar", callback_data=OfferCb(owner_id=owner_id, action="incoming", page=1).pack()),
        _nav(owner_id, "🏠 Bosh Menu", "home"),
    )
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
