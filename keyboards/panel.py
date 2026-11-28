"""Admin panel: ma'lumotga asoslangan (data-driven) menyular.

Har bir menu: sarlavha, tavsif, ota-menu (orqaga) va tugmalar ro'yxati.
Tugma: (matn, callback yaratuvchi, kerakli ruxsat). Ruxsati bo'lmagan tugmalar ko'rinmaydi.
Hamma tugmalar mavjud ishlovchilarga (handler) ulanadi — «Orqaga» doim aynan ota-menuga qaytaradi.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Set, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from utils.callback_data import AdminCb, AdmV2Cb, NavCb, SuggestCb

CbFn = Callable[[int], str]
Item = Tuple[str, CbFn, str]  # (matn, callback, kerakli ruxsat)


def _v2(sec: str, act: str = "v", **kw) -> CbFn:
    return lambda o: AdmV2Cb(owner_id=o, sec=sec, act=act, **kw).pack()


def _old(section: str, action: str, **kw) -> CbFn:
    return lambda o: AdminCb(owner_id=o, section=section, action=action, **kw).pack()


def _menu(key: str) -> CbFn:
    return lambda o: AdmV2Cb(owner_id=o, sec="hub", act="v", ext=key).pack()


def _suggest() -> CbFn:
    return lambda o: SuggestCb(owner_id=o, act="adm", p=1).pack()


PANEL: Dict[str, dict] = {
    # ------------------------------------------------------------------ 🎴 KARTA
    "cards_evt": {
        "title": "🎴 KARTA BO‘LIMI",
        "desc": "Kartalar bilan bog‘liq hamma narsa shu yerda.",
        "parent": None,
        "items": [
            ("➕ Karta qo‘shish", _v2("cards", "add_menu"), "cards"),
            ("📋 Kartalar ro‘yxati", _v2("cards", "view_start", ext="sent_group", p=1), "cards"),
            ("🔍 Qidirish", _v2("cards", "search"), "cards"),
            ("📡 Drop sozlamalari", _menu("drop"), "settings"),
            ("⚙️ Karta sozlamalari", _menu("card_set"), "cards"),
            ("🔨 Auksion", _v2("auction"), "auction"),
            ("🎯 Event", _old("evt", "menu"), "events"),
            ("🏛 Bot Market", _v2("econ", "bs"), "settings"),
        ],
    },
    "drop": {
        "title": "📡 DROP SOZLAMALARI",
        "desc": (
            "Guruhlarga karta tushishining hamma sozlamalari:\n"
            "• Qoidalar — qancha tez-tez, qancha kutish\n"
            "• Rank bo‘yicha tushish — qaysi rank tushadi/tushmaydi\n"
            "• Navbatdagi karta va jadval — aniq karta/vaqtni belgilash"
        ),
        "parent": "cards_evt",
        "items": [
            ("📡 Drop qoidalari", _old("settings", "drop_rules"), "settings"),
            ("🎲 Rank bo‘yicha tushish", _v2("crules", ext="drop"), "cards"),
            ("🎯 Navbatdagi karta", _old("priority", "view"), "settings"),
            ("🗓 Drop jadvali (slotlar)", _old("schedule", "overview"), "groups"),
            ("👥 Guruhga karta tashlash", _old("groups", "list", page=1), "groups"),
            ("🎁 Paket limiti", _old("settings", "pack_limit"), "settings"),
            ("🎁 Kunlik bonus (karta)", _old("settings", "daily_menu"), "settings"),
        ],
    },
    "card_set": {
        "title": "⚙️ KARTA SOZLAMALARI",
        "desc": "Faqat kartaning o‘ziga tegishli sozlamalar: daraja, birlashtirish, bo‘limlar, takliflar.",
        "parent": "cards_evt",
        "items": [
            ("📈 Daraja va narx", _v2("crules", ext="level"), "cards"),
            ("🔮 Birlashtirish", _v2("crules", ext="merge"), "cards"),
            ("🎬 Anime bo‘limi", _v2("cards", "titles_start", ext="anime", p=1), "cards"),
            ("📖 Manga bo‘limi", _v2("cards", "titles_start", ext="manga", p=1), "cards"),
            ("♻️ O‘chirilgan kartalar", _v2("cards", "view_start", ext="deleted", p=1), "cards"),
            ("✏️ Tahrirlash", _v2("cards", "edit_list", p=1), "cards"),
            ("🗑 O‘chirish", _v2("cards", "del_list", p=1), "cards"),
            ("💡 Karta takliflari", _suggest(), "cards"),
            ("🛒 Bozor savdolari", _v2("hist", "mk", ext="d7"), "history"),
        ],
    },
    # ------------------------------------------------------------------ 🪙 RANK VA COIN
    "rankcoin": {
        "title": "🪙 RANK VA COIN",
        "desc": "Rank va coin iqtisodiyoti: ranklar, narxlar, komissiya, VIP, hisobotlar.",
        "parent": None,
        "items": [
            ("⭐ Rank bo‘limi", _menu("rank"), "ranks"),
            ("🪙 Coin bo‘limi", _menu("coin"), "settings"),
        ],
    },
    "rank": {
        "title": "⭐ RANK BO‘LIMI",
        "desc": "Rank qo‘shish, tahrirlash, o‘chirish, narx va tushish ehtimoli, birlashtirish.",
        "parent": "rankcoin",
        "items": [
            ("⭐ Ranklar (qo‘shish/tahrir/o‘chirish)", _v2("ranks"), "ranks"),
            ("💎 Rank narxlari", _v2("ranks"), "ranks"),
            ("🔮 Birlashtirish", _v2("crules", ext="merge"), "cards"),
            ("🎲 Rank bo‘yicha tushish", _v2("crules", ext="drop"), "cards"),
            ("📈 Daraja va narx", _v2("crules", ext="level"), "cards"),
        ],
    },
    "coin": {
        "title": "🪙 COIN BO‘LIMI",
        "desc": "Komissiya, VIP, coin yuborish, kunlik bonus, coin tarixi va hisobotlari.",
        "parent": "rankcoin",
        "items": [
            ("💸 Komissiya sozlamalari", _v2("econ", "comm"), "settings"),
            ("📊 Komissiya hisoboti", _v2("hist", "comm", ext="d7"), "history"),
            ("💎 VIP", _old("settings", "vip_menu"), "settings"),
            ("💸 Coin yuborish sozlamasi", _old("settings", "cg_menu"), "settings"),
            ("🎁 Kunlik bonus", _old("settings", "daily_menu"), "settings"),
            ("🏛 Bot do‘koni narxlari", _v2("econ", "bs"), "settings"),
            ("🧾 Coin tarixi", _v2("hist", "tx", ext="d7-all"), "history"),
            ("📈 Coin hisoboti", _v2("hist", "rep", ext="d7"), "history"),
            ("🏆 Top foydalanuvchilar", _v2("hist", "top", ext="d7-seller"), "history"),
            ("⭐ Stars", _old("stars", "list"), "stars"),
        ],
    },
    # ------------------------------------------------------------------ 👥 FOYDALANUVCHI
    "community": {
        "title": "👥 FOYDALANUVCHI BO‘LIMI",
        "desc": "Foydalanuvchilar, guruhlar, adminlar, kanallar, referal va sozlamalar.",
        "parent": None,
        "items": [
            ("👥 Foydalanuvchilar", _old("users", "menu"), "users"),
            ("🏘 Guruhlar", _v2("grp"), "groups"),
            ("🛡 Adminlar", _v2("admins"), "admins"),
            ("📣 Kanallar", _v2("channels"), "channels"),
            ("🤝 Referal", _old("settings", "rewards_menu"), "settings"),
            ("⚙️ Foydalanuvchi sozlamalari", _menu("user_set"), "settings"),
        ],
    },
    "user_set": {
        "title": "⚙️ FOYDALANUVCHI SOZLAMALARI",
        "desc": "Kirish matni, xush kelibsiz bonusi, guruh va referal mukofotlari.",
        "parent": "community",
        "items": [
            ("👋 Kirish matni", _old("settings", "welcome_edit"), "settings"),
            ("🤝 Referal & guruh mukofoti", _old("settings", "rewards_menu"), "settings"),
            ("💎 VIP narxlari", _old("settings", "vip_menu"), "settings"),
        ],
    },
    # ------------------------------------------------------------------ 📜 TARIX
    "history": {
        "title": "📜 TARIX VA HISOBOT",
        "desc": "Foydalanuvchi harakatlari, savdolar, komissiya, statistika va jurnal.",
        "parent": None,
        "items": [
            ("📜 Tarix va hisobotlar", _v2("hist"), "history"),
            ("📊 Statistika", _old("stats", "view"), "stats"),
            ("📈 Davr bo‘yicha hisobot", _v2("hist", "rep", ext="d7"), "history"),
            ("📈 Faollik hisoboti", _v2("report"), "report"),
            ("📋 Jurnal", _v2("logs"), "logs"),
        ],
    },
    # ------------------------------------------------------------------ 🎮 O'YIN
    "gameplay": {
        "title": "🎮 O‘YIN VA MUKOFOT",
        "desc": "Viktorina, mini-o‘yinlar, mavsum, Stars va kunlik bonus.",
        "parent": None,
        "items": [
            ("🧠 Viktorina", _v2("quiz"), "quiz"),
            ("🎮 Mini-o‘yinlar", _v2("games"), "games"),
            ("🏆 Mavsum", _v2("season"), "season"),
            ("⭐ Stars", _old("stars", "list"), "stars"),
            ("🎁 Kunlik bonus", _old("settings", "daily_menu"), "settings"),
            ("💸 Coin yuborish", _old("settings", "cg_menu"), "settings"),
        ],
    },
    # ------------------------------------------------------------------ 📣 ALOQA
    "comms": {
        "title": "📣 ALOQA",
        "desc": "Murojaatlar (xabarga reply qilib ham javob berasiz), xabar yuborish, reklama.",
        "parent": None,
        "items": [
            ("💬 Murojaatlar", _v2("support"), "support"),
            ("📢 Xabar yuborish", _v2("message"), "message"),
            ("📣 Reklama", _v2("reklama"), "reklama"),
        ],
    },
    # ------------------------------------------------------------------ ⚙️ TIZIM
    "system": {
        "title": "⚙️ TIZIM",
        "desc": "Bot sozlamalari, xavfsizlik, zaxira nusxa va texnik rejim.",
        "parent": None,
        "items": [
            ("⚙️ Bot sozlamalari", _old("settings", "view"), "settings"),
            ("🔐 Xavfsizlik", _v2("security"), "security"),
            ("💾 Zaxira / xotira", _v2("memory"), "memory"),
            ("👋 Kirish matni", _old("settings", "welcome_edit"), "settings"),
        ],
    },
}

# Bosh admin ekrandagi toifalar: (kalit, sarlavha, tavsif, har biri uchun kamida bittasi kerak bo'lgan ruxsatlar)
ROOT_CATEGORIES: List[Tuple[str, str, str, Tuple[str, ...]]] = [
    ("cards_evt", "🎴 Karta", PANEL["cards_evt"]["desc"], ("cards", "ranks", "events", "auction", "settings")),
    ("rankcoin", "🪙 Rank va Coin", PANEL["rankcoin"]["desc"], ("ranks", "settings", "history", "stars")),
    ("community", "👥 Foydalanuvchi", PANEL["community"]["desc"], ("users", "groups", "admins", "channels", "settings")),
    ("history", "📜 Tarix va hisobot", PANEL["history"]["desc"], ("history", "report", "stats", "logs")),
    ("gameplay", "🎮 O‘yin va mukofot", PANEL["gameplay"]["desc"], ("quiz", "games", "season", "stars", "settings")),
    ("comms", "📣 Aloqa", PANEL["comms"]["desc"], ("support", "message", "reklama")),
    ("system", "⚙️ Tizim", PANEL["system"]["desc"], ("settings", "security", "memory")),
]


def panel_text(key: str) -> str:
    m = PANEL[key]
    return f"<b>{m['title']}</b>\n━━━━━━━━━━━━━━━\n{m['desc']}\n\n👇 Kerakli bo‘limni tanlang:"


def panel_kb(owner_id: int, key: str, allowed: Optional[Set[str]]) -> InlineKeyboardMarkup:
    """Menu tugmalari (2 ustun). «Orqaga» ota-menuga, ota yo'q bo'lsa Admin Panelga qaytaradi."""
    m = PANEL[key]
    b = InlineKeyboardBuilder()
    btns = [
        InlineKeyboardButton(text=text, callback_data=fn(owner_id))
        for text, fn, perm in m["items"]
        if allowed is None or perm in allowed
    ]
    for i in range(0, len(btns), 2):
        b.row(*btns[i : i + 2])
    parent = m.get("parent")
    back = (
        AdmV2Cb(owner_id=owner_id, sec="hub", act="v", ext=parent).pack()
        if parent
        else AdminCb(owner_id=owner_id, section="home").pack()
    )
    b.row(
        InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=back),
        InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return b.as_markup()
