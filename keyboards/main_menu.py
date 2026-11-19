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
from utils.i18n import LANG_LABELS, SUPPORTED_LANGS, normalize_lang, tr


def main_menu_kb(
    owner_id: int,
    is_admin: bool = False,
    lang: str = "uz",
    unread_notifs: int = 0,
) -> InlineKeyboardMarkup:
    l = normalize_lang(lang)
    builder = InlineKeyboardBuilder()
    # 1. Kartalar & Qidiruv
    builder.row(
        InlineKeyboardButton(text=tr(l, "m_cards"), callback_data=NavCb(owner_id=owner_id, target="cards").pack()),
        InlineKeyboardButton(text=tr(l, "m_search"), callback_data=NavCb(owner_id=owner_id, target="search").pack()),
    )
    # 2. Bonus & O'yinlar
    builder.row(
        InlineKeyboardButton(text=tr(l, "m_daily"), callback_data=NavCb(owner_id=owner_id, target="daily").pack()),
        InlineKeyboardButton(text=tr(l, "m_quiz"), callback_data=QuizCb(owner_id=owner_id, act="v").pack()),
    )
    builder.row(
        InlineKeyboardButton(text=tr(l, "m_games"), callback_data=GameCb(owner_id=owner_id, act="v").pack()),
        InlineKeyboardButton(text=tr(l, "m_event"), callback_data=NavCb(owner_id=owner_id, target="event").pack()),
    )
    # 3. Savdo & Almashish
    builder.row(
        InlineKeyboardButton(text=tr(l, "m_market"), callback_data=NavCb(owner_id=owner_id, target="market").pack()),
        InlineKeyboardButton(text=tr(l, "m_auction"), callback_data=AucCb(owner_id=owner_id, act="v").pack()),
    )
    builder.row(
        InlineKeyboardButton(text=tr(l, "m_trade"), callback_data=TradeCb(owner_id=owner_id, act="v").pack()),
        InlineKeyboardButton(text=tr(l, "m_wishlist"), callback_data=WishCb(owner_id=owner_id, act="v").pack()),
    )
    # 4. Profil & Reyting
    builder.row(
        InlineKeyboardButton(text=tr(l, "m_profile"), callback_data=NavCb(owner_id=owner_id, target="profile").pack()),
        InlineKeyboardButton(text=tr(l, "m_ranking"), callback_data=NavCb(owner_id=owner_id, target="ranking").pack()),
    )
    # 5. VIP & Stars
    builder.row(
        InlineKeyboardButton(text=tr(l, "m_vip"), callback_data=VipCb(owner_id=owner_id, act="v").pack()),
        InlineKeyboardButton(text=tr(l, "m_stars"), callback_data=NavCb(owner_id=owner_id, target="stars").pack()),
    )
    # 6. Vazifalar & Yutuqlar
    builder.row(
        InlineKeyboardButton(text=tr(l, "m_quests"), callback_data=NavCb(owner_id=owner_id, target="quests").pack()),
        InlineKeyboardButton(text=tr(l, "m_ach"), callback_data=AchCb(owner_id=owner_id, act="v").pack()),
    )
    # 7. Referal & Xabarlar
    notif_lbl = tr(l, "m_notif_n", n=unread_notifs) if unread_notifs > 0 else tr(l, "m_notif")
    builder.row(
        InlineKeyboardButton(text=tr(l, "m_ref"), callback_data=NavCb(owner_id=owner_id, target="referral").pack()),
        InlineKeyboardButton(text=notif_lbl, callback_data=NotifCb(owner_id=owner_id, act="v").pack()),
    )
    # 8. Support & Til & Yordam
    builder.row(
        InlineKeyboardButton(text=tr(l, "m_support"), callback_data=SupCb(owner_id=owner_id, act="v").pack()),
        InlineKeyboardButton(text=tr(l, "btn_lang"), callback_data=NavCb(owner_id=owner_id, target="lang").pack()),
    )
    builder.row(
        InlineKeyboardButton(text=tr(l, "m_help"), callback_data=NavCb(owner_id=owner_id, target="help").pack()),
    )
    if is_admin:
        builder.row(
            InlineKeyboardButton(
                text=tr(l, "btn_admin_panel"),
                callback_data=AdminCb(owner_id=owner_id, section="home").pack(),
            )
        )
    return builder.as_markup()


def profile_kb(owner_id: int, lang: str = "uz") -> InlineKeyboardMarkup:
    l = normalize_lang(lang)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=tr(l, "p_collection"), callback_data=NavCb(owner_id=owner_id, target="cards").pack()),
        InlineKeyboardButton(text=tr(l, "p_custom"), callback_data=AchCb(owner_id=owner_id, act="frames").pack()),
    )
    builder.row(
        InlineKeyboardButton(text=tr(l, "p_market"), callback_data=NavCb(owner_id=owner_id, target="market").pack()),
        InlineKeyboardButton(text=tr(l, "p_auction"), callback_data=AucCb(owner_id=owner_id, act="v").pack()),
    )
    builder.row(
        InlineKeyboardButton(text=tr(l, "p_ranking"), callback_data=NavCb(owner_id=owner_id, target="ranking").pack()),
        InlineKeyboardButton(text=tr(l, "p_search"), callback_data=NavCb(owner_id=owner_id, target="search").pack()),
    )
    builder.row(
        InlineKeyboardButton(text=tr(l, "p_daily"), callback_data=NavCb(owner_id=owner_id, target="daily").pack()),
        InlineKeyboardButton(text=tr(l, "p_vip"), callback_data=VipCb(owner_id=owner_id, act="v").pack()),
    )
    builder.row(
        InlineKeyboardButton(text=tr(l, "p_ref"), callback_data=NavCb(owner_id=owner_id, target="referral").pack()),
        InlineKeyboardButton(text=tr(l, "p_lang"), callback_data=NavCb(owner_id=owner_id, target="lang").pack()),
    )
    builder.row(
        InlineKeyboardButton(text=tr(l, "btn_home"), callback_data=NavCb(owner_id=owner_id, target="home").pack())
    )
    return builder.as_markup()


def language_kb(owner_id: int, current_lang: str = "uz") -> InlineKeyboardMarkup:
    cur = normalize_lang(current_lang)
    builder = InlineKeyboardBuilder()
    for code in SUPPORTED_LANGS:
        mark = "✅ " if code == cur else ""
        builder.row(
            InlineKeyboardButton(
                text=f"{mark}{LANG_LABELS[code]}",
                callback_data=NavCb(owner_id=owner_id, target=f"lang_{code}").pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(text=tr(cur, "btn_home"), callback_data=NavCb(owner_id=owner_id, target="home").pack())
    )
    return builder.as_markup()


def back_home_kb(owner_id: int, back_target: str = "home", lang: str = "uz") -> InlineKeyboardMarkup:
    l = normalize_lang(lang)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=tr(l, "btn_back"), callback_data=NavCb(owner_id=owner_id, target=back_target).pack()),
        InlineKeyboardButton(text=tr(l, "btn_home"), callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return builder.as_markup()


def drop_claim_kb(drop_id: int, lang: str = "uz") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=tr(lang, "drop_claim_btn"),
            callback_data=DropCb(action="claim", drop_id=drop_id).pack(),
        )
    )
    return builder.as_markup()
