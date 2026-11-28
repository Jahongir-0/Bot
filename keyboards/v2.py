import math
from typing import Optional, Dict, List, Set, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from models.card import Card
from models.group import Group
from models.user import User
from models.user_card import UserCard
from models.v2_features import Achievement, AdminRole, Auction, BotStoreListing, Quiz, QuizQuestion, SupportTicket
from services.market_service import BotSellQuote
from utils.callback_data import (
    AchCb,
    AdmV2Cb,
    AdminCb,
    AucCb,
    BotStoreCb,
    GameCb,
    NavCb,
    NotifCb,
    PackCb,
    QuizCb,
    SuggestCb,
    SupCb,
    TradeCb,
    VipCb,
    WishCb,
)
from utils import admin_nav
from utils.constants import ADMIN_PERMISSION_SECTIONS, PROFILE_BACKGROUNDS, PROFILE_FRAMES, get_pack_configs, get_rarity_meta


ADMIN_HUB_CATEGORIES: List[Tuple[str, str, str, Tuple[str, ...]]] = [
    ("cards_evt", "🎴 Kartalar", "Kartalar markazi: boshqarish, drop, ranklar, bot do‘koni, auksion, eventlar, komissiya", ("cards", "ranks", "events", "auction")),
    ("community", "👥 Foydalanuvchilar", "Foydalanuvchilar, guruhlar, kanallar, adminlar, referal va VIP", ("users", "groups", "channels", "admins")),
    ("history", "📜 Tarix va hisobot", "Kim nima qildi, savdolar, komissiya, hisobotlar va jurnal", ("history", "report", "stats", "logs")),
    ("gameplay", "🎮 O‘yin va mukofot", "Viktorina, mini-o‘yinlar, Stars, mavsum, kunlik bonus va coin sovg‘alar", ("quiz", "games", "stars", "season")),
    ("comms", "📣 Aloqa", "Murojaatlar (reply bilan javob), xabar yuborish va reklama", ("support", "message", "reklama")),
    ("system", "⚙️ Tizim", "Bot sozlamalari, xavfsizlik, zaxira va texnik rejim", ("settings", "security", "memory")),
]

# Toifa ichidagi qo'shimcha tugmalar: (matn, callback turi, qiymat, kerakli ruxsat)
_HUB_EXTRAS = {
    "community": [
        ("🤝 Referal & Guruh mukofoti", "legacy", ("settings", "rewards_menu"), "settings"),
        ("💎 VIP narxlari", "legacy", ("settings", "vip_menu"), "settings"),
        ("👋 Kirish matni", "legacy", ("settings", "welcome_edit"), "settings"),
    ],
    "gameplay": [
        ("🎁 Kunlik bonus", "legacy", ("settings", "daily_menu"), "settings"),
        ("💸 Coin yuborish", "legacy", ("settings", "cg_menu"), "settings"),
    ],
}


def _admin_section_cb(owner_id: int, sec_key: str) -> str:
    legacy_routes = {
        "events": AdminCb(owner_id=owner_id, section="evt", action="menu").pack(),
        "users": AdminCb(owner_id=owner_id, section="users", action="menu").pack(),
        "groups": AdminCb(owner_id=owner_id, section="groups", action="list", page=1).pack(),
        "stars": AdminCb(owner_id=owner_id, section="stars", action="list").pack(),
        "stats": AdminCb(owner_id=owner_id, section="stats", action="view").pack(),
        "settings": AdminCb(owner_id=owner_id, section="settings", action="view").pack(),
    }
    return legacy_routes.get(sec_key) or AdmV2Cb(owner_id=owner_id, sec=sec_key, act="v").pack()


def admin_v2_home_kb(owner_id: int, allowed_sections: Set[str]) -> InlineKeyboardMarkup:
    """Admin Panel asosiy markazi: ixcham 5 ta toifa (RBAC ruxsatlariga mos)."""
    builder = InlineKeyboardBuilder()
    cat_btns: List[InlineKeyboardButton] = []
    for cat_key, title, _desc, secs in ADMIN_HUB_CATEGORIES:
        if any(s in allowed_sections for s in secs):
            cat_btns.append(
                InlineKeyboardButton(
                    text=title,
                    callback_data=AdmV2Cb(owner_id=owner_id, sec="hub", act="v", ext=cat_key).pack(),
                )
            )
    for i in range(0, len(cat_btns), 2):
        builder.row(*cat_btns[i : i + 2])
    builder.row(
        InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack())
    )
    return builder.as_markup()


def admin_v2_hub_kb(owner_id: int, cat_key: str, allowed_sections: Set[str]) -> InlineKeyboardMarkup:
    """Tanlangan admin toifasi ichidagi bo'limlar."""
    sec_labels = dict(ADMIN_PERMISSION_SECTIONS)
    target_secs = next((secs for k, _t, _d, secs in ADMIN_HUB_CATEGORIES if k == cat_key), ())
    builder = InlineKeyboardBuilder()
    btns: List[InlineKeyboardButton] = []
    for sec_key in target_secs:
        if sec_key in allowed_sections:
            btns.append(
                InlineKeyboardButton(
                    text=sec_labels.get(sec_key, sec_key.title()),
                    callback_data=_admin_section_cb(owner_id, sec_key),
                )
            )
    for text, kind, val, need in _HUB_EXTRAS.get(cat_key, []):
        if need in allowed_sections and kind == "legacy":
            btns.append(
                InlineKeyboardButton(
                    text=text,
                    callback_data=AdminCb(owner_id=owner_id, section=val[0], action=val[1]).pack(),
                )
            )
    for i in range(0, len(btns), 2):
        builder.row(*btns[i : i + 2])
    builder.row(
        InlineKeyboardButton(text="⬅️ Admin Panel", callback_data=AdminCb(owner_id=owner_id, section="home").pack()),
        InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return builder.as_markup()


def admin_v2_cards_menu_kb(owner_id: int, allowed: Optional[Set[str]] = None) -> InlineKeyboardMarkup:
    """🎴 KARTALAR MARKAZI: karta bilan bog'liq hamma narsa shu yerda (boshqarish, drop, savdo, eventlar, sozlamalar)."""
    b = InlineKeyboardBuilder()

    def ok(sec: str) -> bool:
        return allowed is None or sec in allowed

    def adm(act: str, sec: str = "cards", **kw) -> str:
        return AdmV2Cb(owner_id=owner_id, sec=sec, act=act, **kw).pack()

    def legacy(section: str, action: str) -> str:
        return AdminCb(owner_id=owner_id, section=section, action=action).pack()

    def rows(items: List[Tuple[str, str, str]]) -> None:
        btns = [InlineKeyboardButton(text=t, callback_data=cb) for t, cb, sec in items if ok(sec)]
        for i in range(0, len(btns), 2):
            b.row(*btns[i : i + 2])

    # 1. Boshqarish
    rows([
        ("➕ Karta qo‘shish", adm("add_menu"), "cards"),
        ("👁 Kartalarni ko‘rish", adm("view_start", ext="sent_group", p=1), "cards"),
        ("✏️ Tahrirlash", adm("edit_list", p=1), "cards"),
        ("🗑 O‘chirish", adm("del_list", p=1), "cards"),
        ("🎬 Anime bo‘limi", adm("titles_start", ext="anime", p=1), "cards"),
        ("📖 Manga bo‘limi", adm("titles_start", ext="manga", p=1), "cards"),
        ("🔍 Qidirish", adm("search"), "cards"),
        ("♻️ O‘chirilganlar", adm("view_start", ext="deleted", p=1), "cards"),
    ])
    # 2. Drop va ranklar
    rows([
        ("⭐ Ranklar", adm("v", sec="ranks"), "ranks"),
        ("🎯 Drop, narx, daraja", adm("v", sec="crules"), "cards"),
        ("📡 Drop qoidalari", legacy("settings", "drop_rules"), "settings"),
        ("🎯 Navbatdagi karta", legacy("priority", "view"), "settings"),
    ])
    # 3. Savdo va tadbirlar
    rows([
        ("🏛 Bot do‘koni", adm("bs", sec="econ"), "settings"),
        ("🔨 Auksion", adm("v", sec="auction"), "auction"),
        ("🎯 Eventlar", AdminCb(owner_id=owner_id, section="evt", action="menu").pack(), "events"),
        ("💸 Komissiyalar", adm("comm", sec="econ"), "settings"),
        ("🛒 Bozor savdolari", adm("mk", sec="hist", ext="d7"), "history"),
        ("💡 Karta takliflari", SuggestCb(owner_id=owner_id, act="adm", p=1).pack(), "cards"),
    ])
    # 4. Kartaga bog'liq mukofotlar
    rows([
        ("🎁 Kunlik bonus (karta)", legacy("settings", "daily_menu"), "settings"),
        ("🎁 Paket limiti", legacy("settings", "pack_limit"), "settings"),
    ])
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
        InlineKeyboardButton(text="📄 JSON + rasmlar ketma-ket (tavsiya)", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="jsonq").pack()),
    )
    b.row(
        InlineKeyboardButton(text="📋 Matn / CSV / JSON (URL bilan)", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="batch").pack()),
    )
    b.row(
        InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="v").pack()),
    )
    return b.as_markup()


def _view_tabs_rows(builder: InlineKeyboardBuilder, owner_id: int, active_tab: str) -> None:
    tabs = [
        ("sent_group", "Tushganlar"),
        ("not_sent", "Tushmaganlar"),
        ("owned", "Userda bor"),
        ("in_auction", "Auksionda"),
        ("anime", "Anime"),
        ("manga", "Manga"),
        ("movie", "Kino"),
        ("cartoon", "Multfilm"),
        ("event", "Event"),
        ("other", "Boshqa"),
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
        mode = admin_nav.get_mode(owner_id)
        b.row(
            InlineKeyboardButton(
                text=f"{admin_nav.MODE_CARD_PREFIX[mode]}{st} {meta.emoji} {c.character_name[:22]} · {c.anime_name[:18]}"[:60],
                callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act=admin_nav.MODE_CARD_ACT[mode], tid=c.id, ext=tab, p=page).pack(),
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
        mode = admin_nav.get_mode(owner_id)
        b.row(
            InlineKeyboardButton(
                text=f"{admin_nav.MODE_CARD_PREFIX[mode]}{st} {meta.emoji} {c.character_name[:26]} ({c.card_code})"[:60],
                callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act=admin_nav.MODE_CARD_ACT[mode], tid=c.id, ext=mgroup, p=page).pack(),
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
        InlineKeyboardButton(text="✏️ Tahrirlash", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="edit_pick", tid=card.id).pack()),
        InlineKeyboardButton(text="🗑 O‘chirish", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="del_one", tid=card.id).pack()),
    )
    b.row(
        InlineKeyboardButton(text="📖 Batafsil", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="about", tid=card.id).pack()),
        InlineKeyboardButton(text="🔨 Auksion", callback_data=AdmV2Cb(owner_id=owner_id, sec="auction", act="new_card", tid=card.id).pack()),
    )
    if card.dropped_count == 0 and not card.is_event_card and not card.is_deleted:
        preset_lvl = max(1, int(getattr(card, "preset_level", 1) or 1))
        b.row(
            InlineKeyboardButton(text="➖ Lvl", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="lvl_dec", tid=card.id, ext=tab, p=page).pack()),
            InlineKeyboardButton(text=f"⭐ Lvl {preset_lvl}", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="lvl_info", tid=card.id).pack()),
            InlineKeyboardButton(text="➕ Lvl", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="lvl_inc", tid=card.id, ext=tab, p=page).pack()),
        )
        b.row(
            InlineKeyboardButton(
                text="📤 Guruhga tashlash",
                callback_data=AdminCb(owner_id=owner_id, section="cards", action="drop_pick_grp", target_id=card.id, page=page).pack(),
            ),
            InlineKeyboardButton(
                text="🎯 Event",
                callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="evt_go", tid=card.id).pack(),
            ),
        )
    if not card.is_deleted:
        b.row(
            InlineKeyboardButton(
                text="🎁 Userga berish",
                callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="give_u", tid=card.id, ext=tab, p=1).pack(),
            )
        )
    b.row(
        InlineKeyboardButton(text="👤 Egalari", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="owners", tid=card.id).pack()),
        InlineKeyboardButton(text="📜 Tarixi", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="hist", tid=card.id).pack()),
    )
    if card.is_deleted and is_super:
        b.row(
            InlineKeyboardButton(text="♻️ Kartani tiklash", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="restore", tid=card.id).pack())
        )
    b.row(
        InlineKeyboardButton(text="⬅️ ORQAGA (ro‘yxat)", callback_data=admin_nav.back_callback(owner_id)),
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
        InlineKeyboardButton(text="📋 Ro‘yxatga qaytish", callback_data=admin_nav.back_callback(owner_id)),
    )
    return b.as_markup()


# ---------------- Admin RBAC / Ranks / Quiz / Undropped Picker Keyboards ----------------

def admin_rbac_menu_kb(owner_id: int, roles: List[AdminRole], is_super: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if is_super:
        b.row(
            InlineKeyboardButton(text="➕ Admin qo‘shish", callback_data=AdmV2Cb(owner_id=owner_id, sec="admins", act="add_pick", p=1).pack()),
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
        val = int(r.get("base_value", 0) or 0)
        val_txt = f" · {val:,}🪙" if val > 0 else ""
        b.row(
            InlineKeyboardButton(
                text=f"{r.get('emoji', '⭐')} {r['name']} — {r.get('drop_probability', 0)}%{val_txt}"[:60],
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
        InlineKeyboardButton(text="🎯 Drop, narx va daraja sozlamalari", callback_data=AdmV2Cb(owner_id=owner_id, sec="crules", act="v").pack()),
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
        InlineKeyboardButton(text="💎 Rank narxi (coin)", callback_data=AdmV2Cb(owner_id=owner_id, sec="ranks", act="ef", tid=rank_idx, ext="value").pack()),
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


def games_menu_kb(
    owner_id: int,
    can_dice: bool = True,
    can_flip: bool = True,
    can_lucky: bool = True,
    can_slots: bool = True,
    can_mines: bool = True,
    can_wheel: bool = True,
    can_dart: bool = True,
    can_bowling: bool = True,
    can_basket: bool = True,
    can_chest: bool = True,
    can_seq: bool = True,
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    btns: List[InlineKeyboardButton] = []
    if can_seq:
        btns.append(InlineKeyboardButton(text="🧠 Xotira Zanjiri", callback_data=GameCb(owner_id=owner_id, act="seq").pack()))
    if can_slots:
        btns.append(InlineKeyboardButton(text="🎰 Slot 777", callback_data=GameCb(owner_id=owner_id, act="slots").pack()))
    if can_mines:
        btns.append(InlineKeyboardButton(text="💣 Xazina ovi", callback_data=GameCb(owner_id=owner_id, act="mines").pack()))
    if can_wheel:
        btns.append(InlineKeyboardButton(text="🎡 Omad g‘ildiragi", callback_data=GameCb(owner_id=owner_id, act="wheel").pack()))
    if can_chest:
        btns.append(InlineKeyboardButton(text="📦 Sirli sandiq", callback_data=GameCb(owner_id=owner_id, act="chest").pack()))
    if can_dart:
        btns.append(InlineKeyboardButton(text="🎯 Dart", callback_data=GameCb(owner_id=owner_id, act="an", extra="dart").pack()))
    if can_bowling:
        btns.append(InlineKeyboardButton(text="🎳 Boulling", callback_data=GameCb(owner_id=owner_id, act="an", extra="bowling").pack()))
    if can_basket:
        btns.append(InlineKeyboardButton(text="🏀 Basketbol", callback_data=GameCb(owner_id=owner_id, act="an", extra="basket").pack()))
    if can_dice:
        btns.append(InlineKeyboardButton(text="🎲 Tosh o‘yini", callback_data=GameCb(owner_id=owner_id, act="dice").pack()))
    if can_flip:
        btns.append(InlineKeyboardButton(text="🪙 Tanga (2x)", callback_data=GameCb(owner_id=owner_id, act="flip").pack()))
    if can_lucky:
        btns.append(InlineKeyboardButton(text="🃏 Omadli karta", callback_data=GameCb(owner_id=owner_id, act="lucky").pack()))
    for i in range(0, len(btns), 2):
        b.row(*btns[i : i + 2])
    b.row(InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()))
    return b.as_markup()


def seq_bet_kb(owner_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for bet in (50, 100, 250, 500):
        b.add(
            InlineKeyboardButton(
                text=f"🧠 {bet} 🪙",
                callback_data=GameCb(owner_id=owner_id, act="seq_go", val=bet).pack(),
            )
        )
    b.adjust(2)
    b.row(InlineKeyboardButton(text="⬅️ O‘yinlar", callback_data=GameCb(owner_id=owner_id, act="v").pack()))
    return b.as_markup()


def seq_pad_kb(owner_id: int, runes: tuple[str, ...]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for idx, sym in enumerate(runes):
        b.add(
            InlineKeyboardButton(
                text=sym,
                callback_data=GameCb(owner_id=owner_id, act="seq_tap", val=idx).pack(),
            )
        )
    b.adjust(3)
    return b.as_markup()


def dice_pick_kb(owner_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    faces = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣"]
    for n, face in enumerate(faces, start=1):
        b.add(InlineKeyboardButton(text=face, callback_data=GameCb(owner_id=owner_id, act="pick", val=n).pack()))
    b.adjust(3)
    b.row(InlineKeyboardButton(text="⬅️ Orqaga", callback_data=GameCb(owner_id=owner_id, act="v").pack()))
    return b.as_markup()


def coinflip_pick_kb(owner_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for bet in (50, 100, 250, 500):
        b.row(
            InlineKeyboardButton(
                text=f"🦅 Gerb · {bet} 🪙",
                callback_data=GameCb(owner_id=owner_id, act="flip_go", val=bet, extra="heads").pack(),
            ),
            InlineKeyboardButton(
                text=f"🪙 Raqam · {bet} 🪙",
                callback_data=GameCb(owner_id=owner_id, act="flip_go", val=bet, extra="tails").pack(),
            ),
        )
    b.row(InlineKeyboardButton(text="⬅️ O‘yinlar", callback_data=GameCb(owner_id=owner_id, act="v").pack()))
    return b.as_markup()


def lucky_pick_kb(owner_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="🃏 #1", callback_data=GameCb(owner_id=owner_id, act="lucky_pick", val=1).pack()),
        InlineKeyboardButton(text="🃏 #2", callback_data=GameCb(owner_id=owner_id, act="lucky_pick", val=2).pack()),
        InlineKeyboardButton(text="🃏 #3", callback_data=GameCb(owner_id=owner_id, act="lucky_pick", val=3).pack()),
    )
    b.row(InlineKeyboardButton(text="⬅️ O‘yinlar", callback_data=GameCb(owner_id=owner_id, act="v").pack()))
    return b.as_markup()


def slots_bet_kb(owner_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for bet in (50, 100, 250, 500):
        b.add(
            InlineKeyboardButton(
                text=f"🎰 {bet} 🪙",
                callback_data=GameCb(owner_id=owner_id, act="slots_go", val=bet).pack(),
            )
        )
    b.adjust(2)
    b.row(InlineKeyboardButton(text="⬅️ O‘yinlar", callback_data=GameCb(owner_id=owner_id, act="v").pack()))
    return b.as_markup()


def mines_bet_kb(owner_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for bet in (50, 100, 250, 500):
        b.add(
            InlineKeyboardButton(
                text=f"💣 {bet} 🪙",
                callback_data=GameCb(owner_id=owner_id, act="mines_go", val=bet).pack(),
            )
        )
    b.adjust(2)
    b.row(InlineKeyboardButton(text="⬅️ O‘yinlar", callback_data=GameCb(owner_id=owner_id, act="v").pack()))
    return b.as_markup()


def mines_board_kb(
    owner_id: int,
    opened: List[int],
    bombs: List[int],
    finished: bool,
    cashout_amt: int = 0,
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    btns: List[InlineKeyboardButton] = []
    for idx in range(6):
        if idx in opened:
            sym = "💥" if idx in bombs else "💎"
            btns.append(
                InlineKeyboardButton(
                    text=sym,
                    callback_data=GameCb(owner_id=owner_id, act="mines_noop", val=idx).pack(),
                )
            )
        elif finished:
            sym = "💣" if idx in bombs else "🪙"
            btns.append(
                InlineKeyboardButton(
                    text=sym,
                    callback_data=GameCb(owner_id=owner_id, act="mines_noop", val=idx).pack(),
                )
            )
        else:
            btns.append(
                InlineKeyboardButton(
                    text=f"🧰 #{idx + 1}",
                    callback_data=GameCb(owner_id=owner_id, act="mines_open", val=idx).pack(),
                )
            )
    b.row(*btns[:3])
    b.row(*btns[3:])
    if not finished and cashout_amt > 0:
        b.row(
            InlineKeyboardButton(
                text=f"💰 OLIB CHIQISH ({cashout_amt:,} 🪙)",
                callback_data=GameCb(owner_id=owner_id, act="mines_cash").pack(),
            )
        )
    if finished:
        b.row(
            InlineKeyboardButton(text="💣 Yana o‘ynash", callback_data=GameCb(owner_id=owner_id, act="mines").pack()),
            InlineKeyboardButton(text="🎮 O‘yinlar", callback_data=GameCb(owner_id=owner_id, act="v").pack()),
        )
    return b.as_markup()


def wheel_menu_kb(owner_id: int, ticket_cost: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    lbl = f"🎡 AYLANTIRISH ({ticket_cost:,} 🪙)" if ticket_cost > 0 else "🎡 BEPUL AYLANTIRISH"
    b.row(InlineKeyboardButton(text=lbl, callback_data=GameCb(owner_id=owner_id, act="wheel_go").pack()))
    b.row(InlineKeyboardButton(text="⬅️ O‘yinlar", callback_data=GameCb(owner_id=owner_id, act="v").pack()))
    return b.as_markup()


def packs_menu_kb(owner_id: int, can_open: bool = True) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if can_open:
        for tier, cfg in get_pack_configs().items():
            b.row(
                InlineKeyboardButton(
                    text=f"{cfg['emoji']} {cfg['name']} — {int(cfg['price']):,} 🪙",
                    callback_data=PackCb(owner_id=owner_id, act="open", tier=tier).pack(),
                )
            )
    else:
        b.row(
            InlineKeyboardButton(text="🎮 Mini o‘yinlar", callback_data=GameCb(owner_id=owner_id, act="v").pack()),
            InlineKeyboardButton(text="🛒 Bozor", callback_data=NavCb(owner_id=owner_id, target="market").pack()),
        )
    b.row(InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()))
    return b.as_markup()


def auctions_list_kb(
    owner_id: int,
    items: List[tuple[Auction, Card]],
    page: int = 1,
    total: int = 0,
    page_size: int = 10,
    my_only: bool = False,
    owner_filter: str = "all",
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if not my_only:
        b.row(
            InlineKeyboardButton(
                text=("✅ " if owner_filter == "all" else "") + "Hammasi",
                callback_data=AucCb(owner_id=owner_id, act="v", p=1).pack(),
            ),
            InlineKeyboardButton(
                text=("✅ " if owner_filter == "users" else "") + "👤 Userlar",
                callback_data=AucCb(owner_id=owner_id, act="f_usr", p=1).pack(),
            ),
            InlineKeyboardButton(
                text=("✅ " if owner_filter == "admin" else "") + "🏛 Adminlar",
                callback_data=AucCb(owner_id=owner_id, act="f_adm", p=1).pack(),
            ),
        )
    for auc, card in items:
        meta = get_rarity_meta(card.rarity)
        b.row(
            InlineKeyboardButton(
                text=f"🔨 {meta.emoji} {card.character_name[:20]} — {auc.current_price:,} 🪙"[:60],
                callback_data=AucCb(owner_id=owner_id, act="det", aid=auc.id).pack(),
            )
        )
    total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 1
    if total_pages > 1:
        list_act = (
            "mine"
            if my_only
            else ("f_usr" if owner_filter == "users" else ("f_adm" if owner_filter == "admin" else "v"))
        )
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=AucCb(owner_id=owner_id, act=list_act, p=page - 1).pack()))
        nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data=AucCb(owner_id=owner_id, act=list_act, p=page).pack()))
        if page < total_pages:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=AucCb(owner_id=owner_id, act=list_act, p=page + 1).pack()))
        b.row(*nav)
    b.row(
        InlineKeyboardButton(text="➕ Auksionga qo‘yish", callback_data=AucCb(owner_id=owner_id, act="new", p=1).pack()),
        InlineKeyboardButton(
            text=("🌐 Barcha auksionlar" if my_only else "📦 Mening auksionlarim"),
            callback_data=AucCb(owner_id=owner_id, act=("v" if my_only else "mine"), p=1).pack(),
        ),
    )
    b.row(
        InlineKeyboardButton(text="⬅️ Savdo", callback_data=NavCb(owner_id=owner_id, target="savdo").pack()),
        InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return b.as_markup()


def user_auction_pick_card_kb(
    owner_id: int,
    rows: List[tuple[UserCard, Card]],
    page: int,
    total: int,
    page_size: int = 8,
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for uc, card in rows:
        meta = get_rarity_meta(card.rarity)
        lv_txt = f" L{uc.level}" if int(getattr(uc, "level", 1) or 1) > 1 else ""
        b.row(
            InlineKeyboardButton(
                text=f"{meta.emoji} {card.character_name[:22]}{lv_txt} ({card.card_code})"[:60],
                callback_data=AucCb(owner_id=owner_id, act="uc_new", aid=uc.id).pack(),
            )
        )
    total_pages = max(1, math.ceil(total / page_size))
    if total_pages > 1:
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=AucCb(owner_id=owner_id, act="new", p=page - 1).pack()))
        nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data=AucCb(owner_id=owner_id, act="new", p=page).pack()))
        if page < total_pages:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=AucCb(owner_id=owner_id, act="new", p=page + 1).pack()))
        b.row(*nav)
    b.row(
        InlineKeyboardButton(text="⬅️ Auksionlar", callback_data=AucCb(owner_id=owner_id, act="v").pack()),
        InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return b.as_markup()


def user_auction_dur_kb(owner_id: int, uc_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for h in (6, 12, 24, 48, 72):
        b.add(
            InlineKeyboardButton(
                text=f"⏳ {h} soat",
                callback_data=AucCb(owner_id=owner_id, act="dur", aid=uc_id, amt=h).pack(),
            )
        )
    b.adjust(3)
    b.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data=AucCb(owner_id=owner_id, act="v").pack()))
    return b.as_markup()


def auction_detail_kb(owner_id: int, auc: Auction, is_seller: bool = False) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if is_seller:
        if int(auc.bid_count or 0) == 0:
            b.row(
                InlineKeyboardButton(
                    text="❌ Auksionni bekor qilish",
                    callback_data=AucCb(owner_id=owner_id, act="cancel_u", aid=auc.id).pack(),
                )
            )
        b.row(InlineKeyboardButton(text="🔄 Yangilash", callback_data=AucCb(owner_id=owner_id, act="det", aid=auc.id).pack()))
    else:
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


def wishlist_kb(
    owner_id: int,
    cards: List[Card],
    page: int = 1,
    total: int = 0,
    page_size: int = 5,
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for c in cards:
        meta = get_rarity_meta(c.rarity)
        b.row(
            InlineKeyboardButton(
                text=f"💔 {meta.emoji} {c.character_name[:26]} ({c.card_code})"[:60],
                callback_data=WishCb(owner_id=owner_id, act="del", cid=c.id, p=page).pack(),
            )
        )
    total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 1
    if total_pages > 1:
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=WishCb(owner_id=owner_id, act="v", p=page - 1).pack()))
        nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data=WishCb(owner_id=owner_id, act="v", p=page).pack()))
        if page < total_pages:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=WishCb(owner_id=owner_id, act="v", p=page + 1).pack()))
        b.row(*nav)
    b.row(
        InlineKeyboardButton(text="➕ Karta qo‘shish taklifi", callback_data=SuggestCb(owner_id=owner_id, act="new").pack()),
        InlineKeyboardButton(text="🔥 Eng ko‘p izlangan", callback_data=WishCb(owner_id=owner_id, act="top", p=1).pack()),
    )
    b.row(
        InlineKeyboardButton(text="⬅️ Profilim", callback_data=NavCb(owner_id=owner_id, target="profile").pack()),
        InlineKeyboardButton(text="🏠 Bosh Menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return b.as_markup()


def wishlist_top_kb(
    owner_id: int,
    items: List[Tuple[Card, int]],
    page: int = 1,
    total: int = 0,
    page_size: int = 5,
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for c, cnt in items:
        meta = get_rarity_meta(c.rarity)
        b.row(
            InlineKeyboardButton(
                text=f"💖 {meta.emoji} {c.character_name[:22]} — {cnt} ta"[:60],
                callback_data=WishCb(owner_id=owner_id, act="add", cid=c.id, p=page).pack(),
            )
        )
    total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 1
    if total_pages > 1:
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=WishCb(owner_id=owner_id, act="top", p=page - 1).pack()))
        nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data=WishCb(owner_id=owner_id, act="top", p=page).pack()))
        if page < total_pages:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=WishCb(owner_id=owner_id, act="top", p=page + 1).pack()))
        b.row(*nav)
    b.row(
        InlineKeyboardButton(text="⬅️ Istaklarim", callback_data=WishCb(owner_id=owner_id, act="v", p=1).pack()),
        InlineKeyboardButton(text="🏠 Bosh Menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return b.as_markup()


def suggest_media_kb(owner_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    types = ["Anime", "Manga", "Manhwa", "Kino", "Game", "Boshqa"]
    for i in range(0, len(types), 2):
        b.row(
            *[
                InlineKeyboardButton(
                    text=mt,
                    callback_data=SuggestCb(owner_id=owner_id, act="med", ext=mt).pack(),
                )
                for mt in types[i : i + 2]
            ]
        )
    b.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data=WishCb(owner_id=owner_id, act="v", p=1).pack()))
    return b.as_markup()


def suggest_note_skip_kb(owner_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="⏭ Izohsiz yuborish", callback_data=SuggestCb(owner_id=owner_id, act="skip").pack()),
        InlineKeyboardButton(text="❌ Bekor qilish", callback_data=WishCb(owner_id=owner_id, act="v", p=1).pack()),
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


VIP_ONLY_FRAMES = {"royal", "diamond", "cyber"}


def profile_style_kb(owner_id: int, frame: str, bg: str, is_vip: bool) -> InlineKeyboardMarkup:
    """🎨 BEZAK: joriy tanlov ✅, VIP uchun yopiq ramkalar 🔒."""
    b = InlineKeyboardBuilder()
    for i, (k, lbl) in enumerate(PROFILE_FRAMES.items()):
        locked = k in VIP_ONLY_FRAMES and not is_vip
        mark = "✅ " if k == frame else ("🔒 " if locked else "")
        b.add(InlineKeyboardButton(text=f"{mark}{lbl}", callback_data=AchCb(owner_id=owner_id, act="equip", code=f"f_{k}").pack()))
    b.adjust(2)
    bg_buttons = []
    for k, lbl in PROFILE_BACKGROUNDS.items():
        mark = "✅ " if k == bg else ""
        bg_buttons.append(InlineKeyboardButton(text=f"{mark}{lbl}", callback_data=AchCb(owner_id=owner_id, act="equip", code=f"b_{k}").pack()))
    for i in range(0, len(bg_buttons), 2):
        b.row(*bg_buttons[i : i + 2])
    if not is_vip:
        b.row(InlineKeyboardButton(text="💎 VIP olish — hamma ramkalar ochiladi", callback_data=VipCb(owner_id=owner_id, act="v").pack()))
    b.row(InlineKeyboardButton(text="⬅️ Profil", callback_data=NavCb(owner_id=owner_id, target="profile").pack()))
    return b.as_markup()


def ach_kb(owner_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="🏆 Mavsum", callback_data=AchCb(owner_id=owner_id, act="season").pack()),
        InlineKeyboardButton(text="🎨 Bezak", callback_data=AchCb(owner_id=owner_id, act="frames").pack()),
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
    b.row(InlineKeyboardButton(text="🏠 Bosh Menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()))
    return b.as_markup()


def support_ticket_detail_kb(owner_id: int, ticket: SupportTicket) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if ticket.status != "closed":
        b.row(
            InlineKeyboardButton(
                text="💬 Yana yozish / Media yuborish",
                callback_data=SupCb(owner_id=owner_id, act="reply", tid=ticket.id).pack(),
            )
        )
    b.row(
        InlineKeyboardButton(text="⬅️ Murojaatlar", callback_data=SupCb(owner_id=owner_id, act="v").pack()),
        InlineKeyboardButton(text="🏠 Bosh Menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return b.as_markup()


def notifications_kb(owner_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="✅ Barchasini o‘qilgan qilish", callback_data=NotifCb(owner_id=owner_id, act="read_all").pack()),
        InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return b.as_markup()


def bot_store_menu_kb(owner_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="🛒 Do‘kondan olish", callback_data=BotStoreCb(owner_id=owner_id, act="buy_list", p=1).pack()),
        InlineKeyboardButton(text="🤖 Botga sotish", callback_data=BotStoreCb(owner_id=owner_id, act="sell_list", p=1).pack()),
    )
    b.row(
        InlineKeyboardButton(text="⬅️ Savdo", callback_data=NavCb(owner_id=owner_id, target="savdo").pack()),
        InlineKeyboardButton(text="🏠 Bosh Menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return b.as_markup()


def bot_store_list_kb(
    owner_id: int,
    items: List[Tuple[BotStoreListing, Card]],
    page: int = 1,
    total: int = 0,
    page_size: int = 5,
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for listing, card in items:
        meta = get_rarity_meta(card.rarity)
        lv = f" L{listing.card_level}" if int(listing.card_level or 1) > 1 else ""
        b.row(
            InlineKeyboardButton(
                text=f"{meta.emoji} {card.character_name[:22]}{lv} — {listing.price_coins:,} 🪙"[:60],
                callback_data=BotStoreCb(owner_id=owner_id, act="buy_det", lid=listing.id, p=page).pack(),
            )
        )
    total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 1
    if total_pages > 1:
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=BotStoreCb(owner_id=owner_id, act="buy_list", p=page - 1).pack()))
        nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data=BotStoreCb(owner_id=owner_id, act="buy_list", p=page).pack()))
        if page < total_pages:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=BotStoreCb(owner_id=owner_id, act="buy_list", p=page + 1).pack()))
        b.row(*nav)
    b.row(
        InlineKeyboardButton(text="🤖 Botga sotish", callback_data=BotStoreCb(owner_id=owner_id, act="sell_list", p=1).pack()),
        InlineKeyboardButton(text="⬅️ Bot Do‘koni", callback_data=BotStoreCb(owner_id=owner_id, act="v").pack()),
    )
    b.row(InlineKeyboardButton(text="🏠 Bosh Menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()))
    return b.as_markup()


def bot_store_detail_kb(owner_id: int, listing: BotStoreListing, page: int = 1) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text=f"🛒 SOTIB OLISH ({listing.price_coins:,} 🪙)",
            callback_data=BotStoreCb(owner_id=owner_id, act="buy_go", lid=listing.id, p=page).pack(),
        )
    )
    b.row(
        InlineKeyboardButton(text="⬅️ Ro‘yxat", callback_data=BotStoreCb(owner_id=owner_id, act="buy_list", p=page).pack()),
        InlineKeyboardButton(text="🏠 Bosh Menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return b.as_markup()


def bot_store_sell_list_kb(
    owner_id: int,
    quotes: List[BotSellQuote],
    page: int = 1,
    total: int = 0,
    page_size: int = 5,
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for q in quotes:
        meta = get_rarity_meta(q.card.rarity)
        fav = "❤️ " if q.is_favorite else ""
        lv = f" L{q.level}" if q.level > 1 else ""
        b.row(
            InlineKeyboardButton(
                text=f"{fav}{meta.emoji} {q.card.character_name[:20]}{lv} — {q.total_price:,} 🪙"[:60],
                callback_data=BotStoreCb(owner_id=owner_id, act="sell_det", lid=q.user_card_id, p=page).pack(),
            )
        )
    total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 1
    if total_pages > 1:
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=BotStoreCb(owner_id=owner_id, act="sell_list", p=page - 1).pack()))
        nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data=BotStoreCb(owner_id=owner_id, act="sell_list", p=page).pack()))
        if page < total_pages:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=BotStoreCb(owner_id=owner_id, act="sell_list", p=page + 1).pack()))
        b.row(*nav)
    b.row(
        InlineKeyboardButton(text="🛒 Do‘kondan olish", callback_data=BotStoreCb(owner_id=owner_id, act="buy_list", p=1).pack()),
        InlineKeyboardButton(text="⬅️ Bot Do‘koni", callback_data=BotStoreCb(owner_id=owner_id, act="v").pack()),
    )
    b.row(InlineKeyboardButton(text="🏠 Bosh Menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()))
    return b.as_markup()


def bot_store_sell_confirm_kb(
    owner_id: int,
    user_card_id: int,
    total_price: int,
    page: int = 1,
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text=f"✅ SOTISH (+{total_price:,} 🪙)",
            callback_data=BotStoreCb(owner_id=owner_id, act="sell_go", lid=user_card_id, p=page).pack(),
        )
    )
    b.row(
        InlineKeyboardButton(text="❌ BEKOR QILISH", callback_data=BotStoreCb(owner_id=owner_id, act="sell_list", p=page).pack()),
    )
    return b.as_markup()


def admin_card_give_users_kb(
    owner_id: int,
    card_id: int,
    users: List[User],
    page: int = 1,
    total: int = 0,
    current_admin_tg_id: int = 0,
    back_tab: str = "all",
    page_size: int = 12,
    query: str = "",
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text="🔍 User qidirish (ID / @user)",
            callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="give_us", tid=card_id, ext=back_tab, p=1).pack(),
        )
    )
    for u in users:
        self_tag = "👑 (O‘zingiz) " if current_admin_tg_id and u.telegram_id == current_admin_tg_id else "👤 "
        b.row(
            InlineKeyboardButton(
                text=f"{self_tag}{u.display_name[:26]} ({u.telegram_id})"[:60],
                callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="give_go", tid=card_id, ext=str(u.id), p=page).pack(),
            )
        )
    total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 1
    if total_pages > 1 and not query:
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="give_u", tid=card_id, ext=back_tab, p=page - 1).pack()))
        nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="give_u", tid=card_id, ext=back_tab, p=page).pack()))
        if page < total_pages:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="give_u", tid=card_id, ext=back_tab, p=page + 1).pack()))
        b.row(*nav)
    b.row(
        InlineKeyboardButton(text="⬅️ Kartaga qaytish", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="card_det", tid=card_id, ext=back_tab, p=1).pack()),
        InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return b.as_markup()

