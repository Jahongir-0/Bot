import math
from typing import Dict, List, Set, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from models.card import Card
from models.group import Group
from models.user import User
from models.v2_features import Achievement, AdminRole, Auction, Quiz, QuizQuestion, SupportTicket
from utils.callback_data import (
    AchCb,
    AdmV2Cb,
    AdminCb,
    AucCb,
    GameCb,
    NavCb,
    NotifCb,
    QuizCb,
    SupCb,
    TradeCb,
    VipCb,
    WishCb,
)
from utils.constants import ADMIN_PERMISSION_SECTIONS, PROFILE_BACKGROUNDS, PROFILE_FRAMES, get_rarity_meta


def admin_v2_home_kb(owner_id: int, allowed_sections: Set[str]) -> InlineKeyboardMarkup:
    """Admin Panel V2 asosiy menyusi (RBAC bo'yicha 2 ustunli tugmalar)."""
    legacy_routes = {
        "events": AdminCb(owner_id=owner_id, section="evt", action="menu").pack(),
        "users": AdminCb(owner_id=owner_id, section="users", action="menu").pack(),
        "groups": AdminCb(owner_id=owner_id, section="groups", action="list", page=1).pack(),
        "stars": AdminCb(owner_id=owner_id, section="stars", action="list").pack(),
        "stats": AdminCb(owner_id=owner_id, section="stats", action="view").pack(),
        "settings": AdminCb(owner_id=owner_id, section="settings", action="view").pack(),
    }
    builder = InlineKeyboardBuilder()
    btns: List[InlineKeyboardButton] = []
    for sec_key, label in ADMIN_PERMISSION_SECTIONS:
        if sec_key not in allowed_sections:
            continue
        cb = legacy_routes.get(sec_key) or AdmV2Cb(owner_id=owner_id, sec=sec_key, act="v").pack()
        btns.append(InlineKeyboardButton(text=label, callback_data=cb))
    for i in range(0, len(btns), 2):
        builder.row(*btns[i : i + 2])
    builder.row(
        InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack())
    )
    return builder.as_markup()


def admin_v2_cards_menu_kb(owner_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="➕ Add Card", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="add_menu").pack()),
        InlineKeyboardButton(text="👁 View Card", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="view_tabs", ext="sent_group", p=1).pack()),
    )
    b.row(
        InlineKeyboardButton(text="✏️ Edit Card", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="edit_list", p=1).pack()),
        InlineKeyboardButton(text="🗑 Delete Card", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="del_list", p=1).pack()),
    )
    b.row(
        InlineKeyboardButton(text="🎬 Anime", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="titles", ext="anime", p=1).pack()),
        InlineKeyboardButton(text="📖 Manga", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="titles", ext="manga", p=1).pack()),
    )
    b.row(
        InlineKeyboardButton(text="🔍 Qidirish", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="search").pack()),
        InlineKeyboardButton(text="♻️ O‘chirilganlar", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="view_tabs", ext="deleted", p=1).pack()),
    )
    b.row(
        InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdminCb(owner_id=owner_id, section="home").pack()),
        InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return b.as_markup()


def admin_v2_add_choice_kb(owner_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="⚡ Tezkor qo‘shish (tavsiya)", callback_data=AdmV2Cb(owner_id=owner_id, sec="qadd", act="start").pack()),
    )
    b.row(
        InlineKeyboardButton(text="📝 Qo‘lda qo‘shish", callback_data=AdminCb(owner_id=owner_id, section="cards", action="add_start").pack()),
        InlineKeyboardButton(text="📦 ZIP yuklash", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="zip").pack()),
    )
    b.row(
        InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="v").pack()),
    )
    return b.as_markup()


def _view_tabs_rows(builder: InlineKeyboardBuilder, owner_id: int, active_tab: str) -> None:
    tabs = [
        ("sent_group", "Sent Group"),
        ("not_sent", "Not Sent"),
        ("anime", "Anime"),
        ("manga", "Manga"),
        ("movie", "Kino"),
        ("cartoon", "Cartoon"),
        ("event", "Event"),
        ("other", "Other"),
        ("all", "Barchasi"),
    ]
    for i in range(0, len(tabs), 3):
        row_btns = []
        for k, lbl in tabs[i : i + 3]:
            if k in ("anime", "manga", "movie", "cartoon"):
                cb = AdmV2Cb(owner_id=owner_id, sec="cards", act="titles", ext=k, p=1).pack()
            else:
                cb = AdmV2Cb(owner_id=owner_id, sec="cards", act="view_tabs", ext=k, p=1).pack()
            row_btns.append(InlineKeyboardButton(text=("✅ " if active_tab == k else "") + lbl, callback_data=cb))
        builder.row(*row_btns)


def admin_v2_view_cards_kb(
    owner_id: int, cards: List[Card], tab: str, page: int, total: int, page_size: int = 20
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    _view_tabs_rows(b, owner_id, tab)
    for c in cards:
        meta = get_rarity_meta(c.rarity)
        st = "🗑" if c.is_deleted else ("🎯" if c.is_event_card else ("🟢" if c.is_available else "🔴"))
        b.row(
            InlineKeyboardButton(
                text=f"{st} {meta.emoji} {c.character_name[:22]} · {c.anime_name[:18]}"[:60],
                callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="card_det", tid=c.id, ext=tab, p=page).pack(),
            )
        )
    total_pages = max(1, math.ceil(total / page_size))
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="view_tabs", ext=tab, p=page - 1).pack()))
    nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="view_tabs", ext=tab, p=page).pack()))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="view_tabs", ext=tab, p=page + 1).pack()))
    b.row(*nav)
    b.row(
        InlineKeyboardButton(text="🔍 Qidirish", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="search").pack()),
        InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="v").pack()),
    )
    b.row(InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()))
    return b.as_markup()


def admin_v2_media_titles_kb(
    owner_id: int,
    titles: List[Tuple[int, str, int]],
    mgroup: str,
    page: int,
    total: int,
    page_size: int = 20,
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    _view_tabs_rows(b, owner_id, mgroup)
    for rep_id, title, cnt in titles:
        b.row(
            InlineKeyboardButton(
                text=f"📁 {title[:36]} — {cnt} ta"[:60],
                callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="mtitle", tid=rep_id, ext=mgroup, p=1).pack(),
            )
        )
    total_pages = max(1, math.ceil(total / page_size))
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="titles", ext=mgroup, p=page - 1).pack()))
    nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="titles", ext=mgroup, p=page).pack()))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="titles", ext=mgroup, p=page + 1).pack()))
    b.row(*nav)
    b.row(
        InlineKeyboardButton(text="📋 Barcha kartalar", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="view_tabs", ext=mgroup, p=1).pack()),
        InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="v").pack()),
    )
    return b.as_markup()


def admin_v2_title_cards_kb(
    owner_id: int,
    cards: List[Card],
    rep_card_id: int,
    mgroup: str,
    page: int,
    total: int,
    page_size: int = 20,
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for c in cards:
        meta = get_rarity_meta(c.rarity)
        st = "🟢" if c.is_available else "🔴"
        b.row(
            InlineKeyboardButton(
                text=f"{st} {meta.emoji} {c.character_name[:26]} ({c.card_code})"[:60],
                callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="card_det", tid=c.id, ext=mgroup, p=page).pack(),
            )
        )
    total_pages = max(1, math.ceil(total / page_size))
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="mtitle", tid=rep_card_id, ext=mgroup, p=page - 1).pack()))
    nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="mtitle", tid=rep_card_id, ext=mgroup, p=page).pack()))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="mtitle", tid=rep_card_id, ext=mgroup, p=page + 1).pack()))
    b.row(*nav)
    b.row(
        InlineKeyboardButton(text="⬅️ Nomlar ro‘yxati", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="titles", ext=mgroup, p=1).pack()),
        InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return b.as_markup()


def admin_v2_cards_action_list_kb(
    owner_id: int,
    cards: List[Card],
    mode: str,  # "edit_list" | "del_list"
    page: int,
    total: int,
    page_size: int = 20,
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text="🔍 Qidirish (nom / kod)",
            callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="edit_search" if mode == "edit_list" else "del_search").pack(),
        )
    )
    for c in cards:
        meta = get_rarity_meta(c.rarity)
        prefix = "✏️" if mode == "edit_list" else "🗑"
        target_act = "edit_pick" if mode == "edit_list" else "del_one"
        b.row(
            InlineKeyboardButton(
                text=f"{prefix} {meta.emoji} {c.character_name[:22]} · {c.anime_name[:16]} ({c.card_code})"[:60],
                callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act=target_act, tid=c.id, p=page).pack(),
            )
        )
    total_pages = max(1, math.ceil(total / page_size))
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act=mode, p=page - 1).pack()))
    nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act=mode, p=page).pack()))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act=mode, p=page + 1).pack()))
    b.row(*nav)
    b.row(
        InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="v").pack()),
        InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return b.as_markup()


def admin_v2_card_detail_kb(owner_id: int, card: Card, tab: str = "all", page: int = 1, is_super: bool = False) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="✏️ EDIT", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="edit_pick", tid=card.id).pack()),
        InlineKeyboardButton(text="🗑 DELETE", callback_data=AdminCb(owner_id=owner_id, section="cards", action="delete_confirm", target_id=card.id).pack()),
    )
    b.row(
        InlineKeyboardButton(text="📖 ABOUT", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="about", tid=card.id).pack()),
        InlineKeyboardButton(text="🔨 AUCTION", callback_data=AdmV2Cb(owner_id=owner_id, sec="auction", act="new_card", tid=card.id).pack()),
    )
    if card.dropped_count == 0 and not card.is_event_card and not card.is_deleted:
        b.row(
            InlineKeyboardButton(
                text="📤 SEND TO GROUP",
                callback_data=AdminCb(owner_id=owner_id, section="cards", action="drop_pick_grp", target_id=card.id, page=page).pack(),
            )
        )
    b.row(
        InlineKeyboardButton(text="👤 OWNER", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="owners", tid=card.id).pack()),
        InlineKeyboardButton(text="📜 HISTORY", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="hist", tid=card.id).pack()),
    )
    if card.is_deleted and is_super:
        b.row(
            InlineKeyboardButton(text="♻️ RESTORE CARD", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="restore", tid=card.id).pack())
        )
    b.row(
        InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="view_tabs", ext=tab, p=page).pack()),
        InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return b.as_markup()


def admin_v2_edit_fields_kb(owner_id: int, card_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    fields = [
        ("char", "👤 Personaj ismi"),
        ("mtype", "🎬 Media turi"),
        ("anime", "📺 Media nomi"),
        ("desc", "📝 Ma’lumot"),
        ("rank", "⭐ Rank"),
        ("img", "🖼 Rasm"),
        ("alias", "🏷 Aliaslar"),
        ("limit", "🔁 Tashlash soni"),
        ("value", "💎 Qiymat (coin)"),
    ]
    for i in range(0, len(fields) - 1, 2):
        b.row(
            *[
                InlineKeyboardButton(
                    text=lbl,
                    callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="edit_f", tid=card_id, ext=f).pack(),
                )
                for f, lbl in fields[i : i + 2]
            ]
        )
    last_f, last_lbl = fields[-1]
    b.row(
        InlineKeyboardButton(
            text=last_lbl,
            callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="edit_f", tid=card_id, ext=last_f).pack(),
        )
    )
    b.row(
        InlineKeyboardButton(text="⬅️ Karta", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="card_det", tid=card_id).pack()),
        InlineKeyboardButton(text="📋 Ro‘yxat", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="edit_list", p=1).pack()),
    )
    return b.as_markup()


# ---------------- Admin RBAC / Ranks / Quiz / Undropped Picker Keyboards ----------------

def admin_rbac_menu_kb(owner_id: int, roles: List[AdminRole], is_super: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if is_super:
        b.row(
            InlineKeyboardButton(text="➕ Add Admin", callback_data=AdmV2Cb(owner_id=owner_id, sec="admins", act="add_pick", p=1).pack()),
        )
    for r in roles:
        if not r.is_active:
            continue
        crown = "👑" if r.is_super_admin else "🛡"
        lbl = f"{crown} {r.role_name[:16]} ({r.telegram_id})"
        b.row(
            InlineKeyboardButton(
                text=lbl[:60],
                callback_data=AdmV2Cb(owner_id=owner_id, sec="admins", act="det", tid=r.telegram_id).pack(),
            )
        )
    b.row(
        InlineKeyboardButton(text="⬅️ Admin Panel", callback_data=AdminCb(owner_id=owner_id, section="home").pack()),
        InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return b.as_markup()


def admin_rbac_pick_user_kb(
    owner_id: int, users: List[User], page: int, total: int, page_size: int = 20
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="🔍 Qidirish (ID / @user)", callback_data=AdmV2Cb(owner_id=owner_id, sec="admins", act="add_search").pack()),
        InlineKeyboardButton(text="🆔 ID yozish", callback_data=AdmV2Cb(owner_id=owner_id, sec="admins", act="add_id").pack()),
    )
    for u in users:
        b.row(
            InlineKeyboardButton(
                text=f"👤 {u.display_name[:28]} ({u.telegram_id})"[:60],
                callback_data=AdmV2Cb(owner_id=owner_id, sec="admins", act="pick_u", tid=u.telegram_id).pack(),
            )
        )
    total_pages = max(1, math.ceil(total / page_size))
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=AdmV2Cb(owner_id=owner_id, sec="admins", act="add_pick", p=page - 1).pack()))
    nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data=AdmV2Cb(owner_id=owner_id, sec="admins", act="add_pick", p=page).pack()))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=AdmV2Cb(owner_id=owner_id, sec="admins", act="add_pick", p=page + 1).pack()))
    b.row(*nav)
    b.row(InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=owner_id, sec="admins", act="v").pack()))
    return b.as_markup()


def admin_rbac_perms_kb(
    owner_id: int, target_tg_id: int, selected: Set[str], is_super_target: bool
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text=("✅ 👑 Super Admin (Hammasi)" if is_super_target else "⬜ 👑 Super Admin (Hammasi)"),
            callback_data=AdmV2Cb(owner_id=owner_id, sec="admins", act="psup", tid=target_tg_id).pack(),
        )
    )
    if not is_super_target:
        for i in range(0, len(ADMIN_PERMISSION_SECTIONS), 2):
            row_btns = []
            for key, label in ADMIN_PERMISSION_SECTIONS[i : i + 2]:
                mark = "✅ " if key in selected else "⬜ "
                row_btns.append(
                    InlineKeyboardButton(
                        text=f"{mark}{label}",
                        callback_data=AdmV2Cb(owner_id=owner_id, sec="admins", act="ptog", tid=target_tg_id, ext=key).pack(),
                    )
                )
            b.row(*row_btns)
    b.row(
        InlineKeyboardButton(
            text="💾 SAQLASH",
            callback_data=AdmV2Cb(owner_id=owner_id, sec="admins", act="psave", tid=target_tg_id).pack(),
        ),
        InlineKeyboardButton(
            text="❌ Bekor",
            callback_data=AdmV2Cb(owner_id=owner_id, sec="admins", act="v").pack(),
        ),
    )
    return b.as_markup()


def admin_undropped_cards_kb(
    owner_id: int,
    cards: List[Card],
    sec: str,
    pick_act: str,
    page: int,
    total: int,
    back_cb: str,
    target_ctx_id: int = 0,
    page_size: int = 20,
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for c in cards:
        meta = get_rarity_meta(c.rarity)
        b.row(
            InlineKeyboardButton(
                text=f"🎴 {meta.emoji} {c.character_name[:24]} · {c.anime_name[:16]}"[:60],
                callback_data=AdmV2Cb(owner_id=owner_id, sec=sec, act=pick_act, tid=c.id, ext=str(target_ctx_id), p=page).pack(),
            )
        )
    total_pages = max(1, math.ceil(total / page_size))
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=AdmV2Cb(owner_id=owner_id, sec=sec, act="pick_card", tid=target_ctx_id, p=page - 1).pack()))
    nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data=AdmV2Cb(owner_id=owner_id, sec=sec, act="pick_card", tid=target_ctx_id, p=page).pack()))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=AdmV2Cb(owner_id=owner_id, sec=sec, act="pick_card", tid=target_ctx_id, p=page + 1).pack()))
    b.row(*nav)
    b.row(InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=back_cb))
    return b.as_markup()


def admin_ranks_list_kb(owner_id: int, ranks: List[dict], total_prob: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for idx, r in enumerate(ranks):
        b.row(
            InlineKeyboardButton(
                text=f"{r.get('emoji', '⭐')} {r['name']} — {r.get('drop_probability', 0)}% ({r.get('drop_limit', 1)} drop)"[:60],
                callback_data=AdmV2Cb(owner_id=owner_id, sec="ranks", act="det", tid=idx).pack(),
            )
        )
    if total_prob != 100:
        b.row(
            InlineKeyboardButton(
                text=f"⚖️ 100% ga avtomatik moslash (hozir {total_prob}%)",
                callback_data=AdmV2Cb(owner_id=owner_id, sec="ranks", act="norm").pack(),
            )
        )
    b.row(
        InlineKeyboardButton(text="➕ Yangi rank qo‘shish", callback_data=AdmV2Cb(owner_id=owner_id, sec="ranks", act="add").pack()),
    )
    b.row(
        InlineKeyboardButton(text="⬅️ Admin Panel", callback_data=AdminCb(owner_id=owner_id, section="home").pack()),
        InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return b.as_markup()


def admin_rank_detail_kb(owner_id: int, rank_idx: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="✏️ Nomini o‘zgartirish", callback_data=AdmV2Cb(owner_id=owner_id, sec="ranks", act="ef", tid=rank_idx, ext="name").pack()),
        InlineKeyboardButton(text="😀 Emojisini o‘zgartirish", callback_data=AdmV2Cb(owner_id=owner_id, sec="ranks", act="ef", tid=rank_idx, ext="emoji").pack()),
    )
    b.row(
        InlineKeyboardButton(text="📊 Ehtimol (%)", callback_data=AdmV2Cb(owner_id=owner_id, sec="ranks", act="ef", tid=rank_idx, ext="prob").pack()),
        InlineKeyboardButton(text="🔁 Tashlash soni", callback_data=AdmV2Cb(owner_id=owner_id, sec="ranks", act="ef", tid=rank_idx, ext="drops").pack()),
    )
    b.row(
        InlineKeyboardButton(text="🗑 Rankni o‘chirish", callback_data=AdmV2Cb(owner_id=owner_id, sec="ranks", act="del", tid=rank_idx).pack()),
    )
    b.row(
        InlineKeyboardButton(text="⬅️ Ranklar", callback_data=AdmV2Cb(owner_id=owner_id, sec="ranks", act="v").pack()),
    )
    return b.as_markup()


# ---------------- User V2 Keyboards ----------------

def quiz_list_kb(owner_id: int, quizzes: List[Quiz]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for q in quizzes:
        b.row(
            InlineKeyboardButton(
                text=f"🧠 {q.title[:30]} (+{q.reward_coins:,} 🪙)",
                callback_data=QuizCb(owner_id=owner_id, act="play", qid=q.id).pack(),
            )
        )
    b.row(
        InlineKeyboardButton(text="📜 Quiz tarixim", callback_data=QuizCb(owner_id=owner_id, act="hist").pack()),
        InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return b.as_markup()


def quiz_question_kb(owner_id: int, question: QuizQuestion) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for idx, opt in enumerate(question.options or []):
        b.row(
            InlineKeyboardButton(
                text=f"{idx + 1}. {str(opt)[:45]}",
                callback_data=QuizCb(owner_id=owner_id, act="ans", qid=question.id, opt=idx).pack(),
            )
        )
    b.row(InlineKeyboardButton(text="⬅️ Quizlar", callback_data=QuizCb(owner_id=owner_id, act="v").pack()))
    return b.as_markup()


def games_menu_kb(owner_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🎲 DICE ROLL (Tosh otish)", callback_data=GameCb(owner_id=owner_id, act="dice").pack()))
    b.row(InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()))
    return b.as_markup()


def auctions_list_kb(owner_id: int, items: List[tuple[Auction, Card]]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for auc, card in items:
        meta = get_rarity_meta(card.rarity)
        b.row(
            InlineKeyboardButton(
                text=f"🔨 {meta.emoji} {card.character_name[:20]} — {auc.current_price:,} 🪙",
                callback_data=AucCb(owner_id=owner_id, act="det", aid=auc.id).pack(),
            )
        )
    b.row(InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()))
    return b.as_markup()


def auction_detail_kb(owner_id: int, auc: Auction) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    base = auc.start_price if auc.bid_count == 0 else (auc.current_price + auc.min_step)
    b.row(
        InlineKeyboardButton(
            text=f"⚡ Stavka: {base:,} 🪙",
            callback_data=AucCb(owner_id=owner_id, act="bid", aid=auc.id, amt=base).pack(),
        ),
        InlineKeyboardButton(
            text=f"🔥 +{auc.min_step * 3:,} 🪙",
            callback_data=AucCb(owner_id=owner_id, act="bid", aid=auc.id, amt=base + auc.min_step * 2).pack(),
        ),
    )
    b.row(
        InlineKeyboardButton(text="💬 Boshqa summa", callback_data=AucCb(owner_id=owner_id, act="custom", aid=auc.id).pack()),
        InlineKeyboardButton(text="🔄 Yangilash", callback_data=AucCb(owner_id=owner_id, act="det", aid=auc.id).pack()),
    )
    b.row(
        InlineKeyboardButton(text="⬅️ Auksionlar", callback_data=AucCb(owner_id=owner_id, act="v").pack()),
        InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return b.as_markup()


def wishlist_kb(owner_id: int, cards: List[Card]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for c in cards[:15]:
        meta = get_rarity_meta(c.rarity)
        b.row(
            InlineKeyboardButton(
                text=f"💔 {meta.emoji} {c.character_name[:28]} ({c.card_code})",
                callback_data=WishCb(owner_id=owner_id, act="del", cid=c.id).pack(),
            )
        )
    b.row(
        InlineKeyboardButton(text="🔥 Eng ko‘p izlanayotganlar", callback_data=WishCb(owner_id=owner_id, act="top").pack()),
        InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return b.as_markup()


def vip_shop_kb(owner_id: int, vip_cfg: Dict) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    tiers = vip_cfg.get("tiers") or {}
    for key, t in tiers.items():
        emoji = t.get("emoji", "💎")
        name = t.get("name", key)
        coins = int(t.get("coins", 0))
        stars = int(t.get("stars", 0))
        if coins > 0:
            b.row(
                InlineKeyboardButton(
                    text=f"{emoji} {name} — {coins:,} 🪙",
                    callback_data=VipCb(owner_id=owner_id, act="buy_coins", tier=key).pack(),
                )
            )
        if stars > 0:
            b.row(
                InlineKeyboardButton(
                    text=f"{emoji} {name} — {stars:,} ⭐ Stars",
                    callback_data=VipCb(owner_id=owner_id, act="buy_stars", tier=key).pack(),
                )
            )
    b.row(InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()))
    return b.as_markup()


def profile_custom_kb(owner_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for k, lbl in PROFILE_FRAMES.items():
        b.row(InlineKeyboardButton(text=f"🖼 {lbl}", callback_data=AchCb(owner_id=owner_id, act="equip", code=f"f_{k}").pack()))
    for k, lbl in PROFILE_BACKGROUNDS.items():
        b.row(InlineKeyboardButton(text=f"🌌 {lbl}", callback_data=AchCb(owner_id=owner_id, act="equip", code=f"b_{k}").pack()))
    b.row(
        InlineKeyboardButton(text="🏅 Yutuqlar", callback_data=AchCb(owner_id=owner_id, act="v").pack()),
        InlineKeyboardButton(text="🏆 Mavsum", callback_data=AchCb(owner_id=owner_id, act="season").pack()),
    )
    b.row(InlineKeyboardButton(text="⬅️ Profil", callback_data=NavCb(owner_id=owner_id, target="profile").pack()))
    return b.as_markup()


def support_menu_kb(owner_id: int, tickets: List[SupportTicket]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="➕ Yangi murojaat / shikoyat", callback_data=SupCb(owner_id=owner_id, act="new").pack()))
    for t in tickets[:10]:
        st_ico = "🟢" if t.status == "answered" else ("🔴" if t.status == "closed" else "🟡")
        b.row(
            InlineKeyboardButton(
                text=f"{st_ico} #{t.id} {t.subject[:28]}",
                callback_data=SupCb(owner_id=owner_id, act="view", tid=t.id).pack(),
            )
        )
    b.row(InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()))
    return b.as_markup()


def notifications_kb(owner_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="✅ Barchasini o‘qilgan qilish", callback_data=NotifCb(owner_id=owner_id, act="read_all").pack()),
        InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return b.as_markup()
