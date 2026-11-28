import math
from typing import List, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from models.card import Card
from models.limited_event import LimitedEvent
from utils.callback_data import AdminCb, EvtCb, NavCb, PageJumpCb
from utils.constants import get_rarity_meta


def event_buy_kb(event: LimitedEvent) -> InlineKeyboardMarkup:
    """Guruhga yuboriladigan e'londagi ommaviy tugmalar (hamma bosa oladi)."""
    builder = InlineKeyboardBuilder()
    if event.price_coins:
        builder.row(
            InlineKeyboardButton(
                text=f"🪙 {int(event.price_coins):,} coin bilan olish",
                callback_data=EvtCb(action="buy_coins", event_id=event.id).pack(),
            )
        )
    if event.price_stars:
        builder.row(
            InlineKeyboardButton(
                text=f"⭐ {int(event.price_stars):,} Stars bilan olish",
                callback_data=EvtCb(action="buy_stars", event_id=event.id).pack(),
            )
        )
    return builder.as_markup()


def event_list_footer_kb(owner_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🏠 BOSH MENU", callback_data=NavCb(owner_id=owner_id, target="home").pack())
    )
    return builder.as_markup()


# ----------------------------- ADMIN -----------------------------
def _back_row(builder: InlineKeyboardBuilder, owner_id: int, back_cb: str) -> None:
    builder.row(
        InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=back_cb),
        InlineKeyboardButton(text="🏠 ADMIN", callback_data=AdminCb(owner_id=owner_id, section="home").pack()),
    )


def admin_evt_menu_kb(owner_id: int, events: List[Tuple[LimitedEvent, Card]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="➕ YANGI LIMITLI KARTA EVENTI",
            callback_data=AdminCb(owner_id=owner_id, section="evt", action="pick", page=1).pack(),
        )
    )
    for ev, card in events:
        icon = {"active": "🟢", "sold_out": "🔥", "ended": "⌛", "cancelled": "⛔"}.get(ev.status, "⚪")
        builder.row(
            InlineKeyboardButton(
                text=f"{icon} #{ev.id} {card.character_name[:22]} · {ev.sold_count}/{ev.total_copies}",
                callback_data=AdminCb(owner_id=owner_id, section="evt", action="detail", target_id=ev.id).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="⚡ Bonus event (coin/drop ko‘paytirgich)",
            callback_data=AdminCb(owner_id=owner_id, section="events", action="bonus_menu").pack(),
        )
    )
    _back_row(builder, owner_id, AdminCb(owner_id=owner_id, section="home").pack())
    return builder.as_markup()


def admin_evt_candidates_kb(
    owner_id: int, cards: List[Card], page: int, total: int, page_size: int = 6
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for c in cards:
        meta = get_rarity_meta(c.rarity)
        builder.row(
            InlineKeyboardButton(
                text=f"{meta.emoji} {c.character_name[:20]} · {c.anime_name[:16]} · {c.card_code}"[:60],
                callback_data=AdminCb(owner_id=owner_id, section="evt", action="card", target_id=c.id).pack(),
            )
        )
    pages = max(1, math.ceil(total / page_size))
    nav = []
    if page > 1:
        nav.append(
            InlineKeyboardButton(
                text="⬅️", callback_data=AdminCb(owner_id=owner_id, section="evt", action="pick", page=page - 1).pack()
            )
        )
    nav.append(
        InlineKeyboardButton(
            text=f"{page}/{pages}",
            callback_data=PageJumpCb(owner_id=owner_id, key="ad_ev", total=pages).pack(),
        )
    )
    if page < pages:
        nav.append(
            InlineKeyboardButton(
                text="➡️", callback_data=AdminCb(owner_id=owner_id, section="evt", action="pick", page=page + 1).pack()
            )
        )
    builder.row(*nav)
    _back_row(builder, owner_id, AdminCb(owner_id=owner_id, section="evt", action="menu").pack())
    return builder.as_markup()


def admin_evt_price_type_kb(owner_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for label, mode in (("🪙 Coin bilan", "coins"), ("⭐ Stars bilan", "stars"), ("🪙 + ⭐ Ikkalasi ham", "both")):
        builder.row(
            InlineKeyboardButton(
                text=label,
                callback_data=AdminCb(owner_id=owner_id, section="evt", action="ptype", extra=mode).pack(),
            )
        )
    _back_row(builder, owner_id, AdminCb(owner_id=owner_id, section="evt", action="menu").pack())
    return builder.as_markup()


def admin_evt_stars_kb(owner_id: int, packages: List[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for p in packages:
        builder.row(
            InlineKeyboardButton(
                text=f"⭐ {p['stars']:,} Stars  (= {p['coins']:,} coin tarifi)",
                callback_data=AdminCb(owner_id=owner_id, section="evt", action="pstars", extra=str(p["id"])).pack(),
            )
        )
    _back_row(builder, owner_id, AdminCb(owner_id=owner_id, section="evt", action="menu").pack())
    return builder.as_markup()


def admin_evt_duration_kb(owner_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="⏱ 6 soat",
            callback_data=AdminCb(owner_id=owner_id, section="evt", action="dur", extra="6").pack(),
        ),
        InlineKeyboardButton(
            text="⏱ 12 soat",
            callback_data=AdminCb(owner_id=owner_id, section="evt", action="dur", extra="12").pack(),
        ),
        InlineKeyboardButton(
            text="⏱ 24 soat",
            callback_data=AdminCb(owner_id=owner_id, section="evt", action="dur", extra="24").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="📆 48 soat (2 kun)",
            callback_data=AdminCb(owner_id=owner_id, section="evt", action="dur", extra="48").pack(),
        ),
        InlineKeyboardButton(
            text="📆 72 soat (3 kun)",
            callback_data=AdminCb(owner_id=owner_id, section="evt", action="dur", extra="72").pack(),
        ),
        InlineKeyboardButton(
            text="📆 7 kun (168 soat)",
            callback_data=AdminCb(owner_id=owner_id, section="evt", action="dur", extra="168").pack(),
        ),
    )
    _back_row(builder, owner_id, AdminCb(owner_id=owner_id, section="evt", action="menu").pack())
    return builder.as_markup()


def admin_evt_confirm_kb(owner_id: int, target: str = "all") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    opts = [
        ("groups", "🏰 Guruhlarga"),
        ("users", "👤 Userlarga"),
        ("all", "🌍 Ikkalasiga"),
        ("none", "🔇 E’lonsiz"),
    ]
    for i in range(0, len(opts), 2):
        builder.row(
            *[
                InlineKeyboardButton(
                    text=("✅ " if target == k else "") + lbl,
                    callback_data=AdminCb(owner_id=owner_id, section="evt", action="etgt", extra=k).pack(),
                )
                for k, lbl in opts[i : i + 2]
            ]
        )
    builder.row(
        InlineKeyboardButton(
            text="✅ EVENTNI BOSHLASH",
            callback_data=AdminCb(owner_id=owner_id, section="evt", action="go").pack(),
        ),
        InlineKeyboardButton(
            text="❌ BEKOR",
            callback_data=AdminCb(owner_id=owner_id, section="evt", action="menu").pack(),
        ),
    )
    return builder.as_markup()


def admin_evt_detail_kb(owner_id: int, event: LimitedEvent) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if event.status == "active":
        builder.row(
            InlineKeyboardButton(
                text="⏹ EVENTNI TO‘XTATISH",
                callback_data=AdminCb(owner_id=owner_id, section="evt", action="stop", target_id=event.id).pack(),
            )
        )
    _back_row(builder, owner_id, AdminCb(owner_id=owner_id, section="evt", action="menu").pack())
    return builder.as_markup()
