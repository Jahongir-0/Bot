import math
from typing import Dict, List

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from models.card import Card
from models.market import Offer
from models.user import User
from models.user_card import UserCard
from services.market_service import BotSellQuote, ListingView
from services.search_service import GlobalCardSearchItem
from utils.callback_data import (
    BotStoreCb,
    CollectionCb,
    MarketCb,
    NavCb,
    OfferCb,
    PageJumpCb,
    RankingCb,
    SearchCb,
    StarsCb,
    TransferCb,
    WishCb,
)
from utils.constants import get_rank_name_by_order, get_rarity_meta, list_rarities


def market_list_kb(
    owner_id: int,
    listings: List[ListingView],
    page: int,
    total: int,
    my_only: bool = False,
    page_size: int = 5,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in listings:
        meta = get_rarity_meta(item.card.rarity)
        builder.row(
            InlineKeyboardButton(
                text=f"{meta.emoji} {item.card.character_name} — {item.listing.price:,} 🪙",
                callback_data=MarketCb(
                    owner_id=owner_id,
                    action="view",
                    listing_id=item.listing.id,
                    page=page,
                ).pack(),
            )
        )

    total_pages = max(1, math.ceil(total / page_size))
    action_name = "my_listings" if my_only else "list"
    nav = []
    if page > 1:
        nav.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=MarketCb(owner_id=owner_id, action=action_name, page=page - 1).pack(),
            )
        )
    nav.append(
        InlineKeyboardButton(
            text=f"{page}/{total_pages}",
            callback_data=PageJumpCb(
                owner_id=owner_id,
                key="m_my" if my_only else "m_all",
                total=total_pages,
            ).pack(),
        )
    )
    if page < total_pages:
        nav.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=MarketCb(owner_id=owner_id, action=action_name, page=page + 1).pack(),
            )
        )
    builder.row(*nav)

    toggle_text = "🌐 BARCHA E‘LONLAR" if my_only else "📦 MENING E‘LONLARIM"
    toggle_action = "list" if my_only else "my_listings"
    builder.row(
        InlineKeyboardButton(
            text=toggle_text,
            callback_data=MarketCb(owner_id=owner_id, action=toggle_action, page=1).pack(),
        ),
        InlineKeyboardButton(
            text="📩 MENING TAKLIFLARIM",
            callback_data=OfferCb(owner_id=owner_id, action="mine", page=1).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🏛 BOT DO‘KONI",
            callback_data=BotStoreCb(owner_id=owner_id, act="v").pack(),
        ),
        InlineKeyboardButton(
            text="🤖 BOTGA SOTISH",
            callback_data=BotStoreCb(owner_id=owner_id, act="sell_list", p=1).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ SAVDO",
            callback_data=NavCb(owner_id=owner_id, target="savdo").pack(),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def bot_sell_list_kb(
    owner_id: int,
    quotes: List[BotSellQuote],
    page: int,
    total: int,
    page_size: int = 8,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for q in quotes:
        meta = get_rarity_meta(q.card.rarity)
        fav = "❤️ " if q.is_favorite else ""
        lv_txt = f" L{q.level}" if q.level > 1 else ""
        builder.row(
            InlineKeyboardButton(
                text=f"{fav}{meta.emoji} {q.card.character_name[:20]}{lv_txt} — {q.total_price:,} 🪙"[:60],
                callback_data=MarketCb(
                    owner_id=owner_id,
                    action="bot_ask",
                    listing_id=q.user_card_id,
                    page=page,
                ).pack(),
            )
        )
    total_pages = max(1, math.ceil(total / page_size))
    if total_pages > 1:
        nav = []
        if page > 1:
            nav.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=MarketCb(owner_id=owner_id, action="bot_list", page=page - 1).pack(),
                )
            )
        nav.append(
            InlineKeyboardButton(
                text=f"{page}/{total_pages}",
                callback_data=PageJumpCb(owner_id=owner_id, key="m_bot", total=total_pages).pack(),
            )
        )
        if page < total_pages:
            nav.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=MarketCb(owner_id=owner_id, action="bot_list", page=page + 1).pack(),
                )
            )
        builder.row(*nav)
    builder.row(
        InlineKeyboardButton(
            text="⬅️ BOZORGA",
            callback_data=MarketCb(owner_id=owner_id, action="list", page=1).pack(),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def bot_sell_confirm_kb(
    owner_id: int,
    user_card_id: int,
    total_price: int,
    page: int = 1,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=f"✅ SOTISH (+{total_price:,} 🪙)",
            callback_data=MarketCb(
                owner_id=owner_id,
                action="bot_do",
                listing_id=user_card_id,
                page=page,
            ).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="❌ BEKOR QILISH",
            callback_data=MarketCb(owner_id=owner_id, action="bot_list", page=page).pack(),
        )
    )
    return builder.as_markup()


def listing_detail_kb(
    owner_id: int,
    listing_id: int,
    is_seller: bool,
    page: int = 1,
    user_card_id: int = 0,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if is_seller:
        builder.row(
            InlineKeyboardButton(
                text="❌ SOTUVDAN OLISH",
                callback_data=MarketCb(
                    owner_id=owner_id,
                    action="cancel",
                    listing_id=listing_id,
                    page=page,
                ).pack(),
            )
        )
    else:
        row_btns = [
            InlineKeyboardButton(
                text="🛒 SOTIB OLISH",
                callback_data=MarketCb(
                    owner_id=owner_id,
                    action="buy_ask",
                    listing_id=listing_id,
                    page=page,
                ).pack(),
            )
        ]
        if user_card_id > 0:
            row_btns.append(
                InlineKeyboardButton(
                    text="💰 TAKLIF BERISH",
                    callback_data=OfferCb(
                        owner_id=owner_id,
                        action="prompt",
                        user_card_id=user_card_id,
                    ).pack(),
                )
            )
        builder.row(*row_btns)
    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=MarketCb(owner_id=owner_id, action="list", page=page).pack(),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def listing_buy_confirm_kb(owner_id: int, listing_id: int, page: int = 1) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ TASDIQLASH VA OLISH",
            callback_data=MarketCb(owner_id=owner_id, action="buy", listing_id=listing_id, page=page).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ BEKOR QILISH",
            callback_data=MarketCb(owner_id=owner_id, action="view", listing_id=listing_id, page=page).pack(),
        )
    )
    return builder.as_markup()


def search_menu_kb(owner_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🎴 O‘zimni kartalarim",
            callback_data=CollectionCb(owner_id=owner_id, action="my_search").pack(),
        ),
        InlineKeyboardButton(
            text="📖 Hali hech kimda yo‘q",
            callback_data=SearchCb(owner_id=owner_id, action="mode", mode="unowned").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🔍 Personaj ismi",
            callback_data=SearchCb(owner_id=owner_id, action="mode", mode="character").pack(),
        ),
        InlineKeyboardButton(
            text="📺 Anime / Asar",
            callback_data=SearchCb(owner_id=owner_id, action="mode", mode="anime").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="⭐ Rank bo‘yicha",
            callback_data=SearchCb(owner_id=owner_id, action="mode", mode="rarity").pack(),
        ),
        InlineKeyboardButton(
            text="🎴 Karta ID / Kod",
            callback_data=SearchCb(owner_id=owner_id, action="mode", mode="card_id").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🎲 Tasodifiy karta",
            callback_data=SearchCb(owner_id=owner_id, action="random", mode="random", page=1).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def search_rarity_kb(owner_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for r in list_rarities():
        meta = get_rarity_meta(r)
        builder.row(
            InlineKeyboardButton(
                text=f"{meta.emoji} {r}",
                callback_data=SearchCb(
                    owner_id=owner_id,
                    action="results",
                    mode="rarity",
                    query=r,
                    page=1,
                ).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=SearchCb(owner_id=owner_id, action="menu").pack(),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def _cb_q(text: str, max_bytes: int = 12) -> str:
    safe = text.replace(":", " ")
    encoded = safe.encode("utf-8")[:max_bytes]
    return encoded.decode("utf-8", errors="ignore")


def search_results_kb(
    owner_id: int,
    items: List[GlobalCardSearchItem],
    mode: str,
    query: str,
    page: int,
    total: int,
    page_size: int = 5,
) -> InlineKeyboardMarkup:
    q_short = _cb_q(query)
    builder = InlineKeyboardBuilder()
    for item in items:
        meta = get_rarity_meta(item.card.rarity)
        if item.active_listing is not None:
            btn_text = f"🛒 {meta.emoji} {item.card.character_name} — {item.active_listing.price:,} 🪙"
            cb = MarketCb(
                owner_id=owner_id,
                action="view",
                listing_id=item.active_listing.id,
                page=1,
            ).pack()
        else:
            btn_text = f"💰 {meta.emoji} {item.card.character_name} (@{item.owner.username or item.owner.first_name})"
            cb = SearchCb(
                owner_id=owner_id,
                action="inspect",
                mode=mode,
                query=q_short,
                page=page,
                user_card_id=item.user_card.id,
            ).pack()
        builder.row(InlineKeyboardButton(text=btn_text[:60], callback_data=cb))

    total_pages = max(1, math.ceil(total / page_size))
    if mode != "random":
        nav = []
        if page > 1:
            nav.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=SearchCb(
                        owner_id=owner_id,
                        action="results",
                        mode=mode,
                        query=q_short,
                        page=page - 1,
                    ).pack(),
                )
            )
        nav.append(
            InlineKeyboardButton(
                text=f"{page}/{total_pages}",
                callback_data=PageJumpCb(
                    owner_id=owner_id,
                    key="s_res",
                    total=total_pages,
                    ext=_cb_q(f"{mode}|{q_short}", 22),
                ).pack(),
            )
        )
        if page < total_pages:
            nav.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=SearchCb(
                        owner_id=owner_id,
                        action="results",
                        mode=mode,
                        query=q_short,
                        page=page + 1,
                    ).pack(),
                )
            )
        builder.row(*nav)
    else:
        builder.row(
            InlineKeyboardButton(
                text="🎲 YANA TASODIFIY",
                callback_data=SearchCb(owner_id=owner_id, action="random", mode="random", page=1).pack(),
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=SearchCb(owner_id=owner_id, action="menu").pack(),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def search_inspect_kb(
    owner_id: int,
    item: GlobalCardSearchItem,
    is_self_owner: bool,
    mode: str,
    query: str,
    page: int,
) -> InlineKeyboardMarkup:
    q_short = _cb_q(query)
    builder = InlineKeyboardBuilder()
    if item.active_listing is not None and not is_self_owner:
        builder.row(
            InlineKeyboardButton(
                text="🛒 SOTIB OLISH",
                callback_data=MarketCb(
                    owner_id=owner_id,
                    action="buy",
                    listing_id=item.active_listing.id,
                    page=1,
                ).pack(),
            )
        )
    elif item.active_listing is None and not is_self_owner:
        builder.row(
            InlineKeyboardButton(
                text="💰 TAKLIF YUBORISH",
                callback_data=OfferCb(
                    owner_id=owner_id,
                    action="prompt",
                    user_card_id=item.user_card.id,
                ).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="💖 ISTAKLARGA QO‘SHISH",
            callback_data=WishCb(owner_id=owner_id, act="add", cid=item.card.id).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=SearchCb(
                owner_id=owner_id,
                action="results",
                mode=mode,
                query=q_short,
                page=page,
            ).pack(),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def search_catalog_results_kb(
    owner_id: int,
    cards: List[Card],
    mode: str,
    query: str,
    page: int,
    total: int,
    page_size: int = 5,
) -> InlineKeyboardMarkup:
    q_short = _cb_q(query)
    builder = InlineKeyboardBuilder()
    for card in cards:
        meta = get_rarity_meta(card.rarity)
        btn_text = f"🎴 {meta.emoji} {card.character_name} — {card.base_value:,} 🪙"
        cb = SearchCb(
            owner_id=owner_id,
            action="cat",
            mode=mode,
            query=q_short,
            page=page,
            user_card_id=card.id,
        ).pack()
        builder.row(InlineKeyboardButton(text=btn_text[:60], callback_data=cb))

    total_pages = max(1, math.ceil(total / page_size))
    if mode != "random":
        nav = []
        if page > 1:
            nav.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=SearchCb(
                        owner_id=owner_id,
                        action="results",
                        mode=mode,
                        query=q_short,
                        page=page - 1,
                    ).pack(),
                )
            )
        nav.append(
            InlineKeyboardButton(
                text=f"{page}/{total_pages}",
                callback_data=PageJumpCb(
                    owner_id=owner_id,
                    key="s_res",
                    total=total_pages,
                    ext=_cb_q(f"{mode}|{q_short}", 22),
                ).pack(),
            )
        )
        if page < total_pages:
            nav.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=SearchCb(
                        owner_id=owner_id,
                        action="results",
                        mode=mode,
                        query=q_short,
                        page=page + 1,
                    ).pack(),
                )
            )
        builder.row(*nav)
    else:
        builder.row(
            InlineKeyboardButton(
                text="🎲 YANA TASODIFIY",
                callback_data=SearchCb(owner_id=owner_id, action="random", mode="random", page=1).pack(),
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=SearchCb(owner_id=owner_id, action="menu").pack(),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def search_catalog_inspect_kb(
    owner_id: int,
    mode: str,
    query: str,
    page: int,
    card_id: int = 0,
    notify_on: bool = False,
) -> InlineKeyboardMarkup:
    q_short = _cb_q(query)
    builder = InlineKeyboardBuilder()
    if card_id > 0:
        builder.row(
            InlineKeyboardButton(
                text="💖 ISTAKLARGA QO‘SHISH",
                callback_data=WishCb(owner_id=owner_id, act="add", cid=card_id).pack(),
            )
        )
        notif_txt = "🔕 Xabarnoma YOQILGAN (o‘chirish)" if notify_on else "🔔 Sotuv/Drop xabarnomasi"
        builder.row(
            InlineKeyboardButton(
                text=notif_txt,
                callback_data=WishCb(owner_id=owner_id, act="notif", cid=card_id).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=SearchCb(
                owner_id=owner_id,
                action="results",
                mode=mode,
                query=q_short,
                page=page,
            ).pack(),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def offer_type_kb(owner_id: int, target_uc_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🪙 Coin taklif qilish",
            callback_data=OfferCb(owner_id=owner_id, action="coin_ask", user_card_id=target_uc_id).pack(),
        ),
        InlineKeyboardButton(
            text="🎴 Karta taklif qilish",
            callback_data=OfferCb(owner_id=owner_id, action="card_pick", offer_id=target_uc_id, page=1).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ Qidiruv", callback_data=SearchCb(owner_id=owner_id, action="menu").pack()),
        InlineKeyboardButton(text="🏠 Bosh Menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return builder.as_markup()


def offer_card_pick_kb(
    owner_id: int,
    target_uc_id: int,
    rows: List[tuple[UserCard, Card]],
    page: int,
    total: int,
    page_size: int = 5,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for uc, card in rows:
        meta = get_rarity_meta(card.rarity)
        lv = f" L{uc.level}" if int(getattr(uc, "level", 1) or 1) > 1 else ""
        builder.row(
            InlineKeyboardButton(
                text=f"{meta.emoji} {card.character_name[:22]}{lv} ({card.card_code})"[:60],
                callback_data=OfferCb(
                    owner_id=owner_id,
                    action="card_send",
                    offer_id=target_uc_id,
                    user_card_id=uc.id,
                    page=page,
                ).pack(),
            )
        )
    total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 1
    if total_pages > 1:
        nav = []
        if page > 1:
            nav.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=OfferCb(owner_id=owner_id, action="card_pick", offer_id=target_uc_id, page=page - 1).pack(),
                )
            )
        nav.append(
            InlineKeyboardButton(
                text=f"{page}/{total_pages}",
                callback_data=PageJumpCb(
                    owner_id=owner_id,
                    key="o_pk",
                    total=total_pages,
                    tid=target_uc_id,
                ).pack(),
            )
        )
        if page < total_pages:
            nav.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=OfferCb(owner_id=owner_id, action="card_pick", offer_id=target_uc_id, page=page + 1).pack(),
                )
            )
        builder.row(*nav)
    builder.row(
        InlineKeyboardButton(
            text="⬅️ Orqaga",
            callback_data=OfferCb(owner_id=owner_id, action="prompt", user_card_id=target_uc_id).pack(),
        ),
        InlineKeyboardButton(text="🏠 Bosh Menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return builder.as_markup()


def offer_decision_kb(owner_id: int, offer_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ QABUL QILISH",
            callback_data=OfferCb(owner_id=owner_id, action="accept", offer_id=offer_id).pack(),
        ),
        InlineKeyboardButton(
            text="❌ RAD ETISH",
            callback_data=OfferCb(owner_id=owner_id, action="reject", offer_id=offer_id).pack(),
        ),
    )
    return builder.as_markup()


def incoming_offers_kb(owner_id: int, offers: List[tuple[Offer, Card, User]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for off, card, buyer in offers[:8]:
        val_txt = "🎴 Karta almashuv" if getattr(off, "offer_type", "coins") == "card" else f"{off.amount:,} 🪙"
        builder.row(
            InlineKeyboardButton(
                text=f"✅ {card.character_name[:18]} ({val_txt})"[:42],
                callback_data=OfferCb(owner_id=owner_id, action="accept", offer_id=off.id).pack(),
            ),
            InlineKeyboardButton(
                text="❌ Rad etish",
                callback_data=OfferCb(owner_id=owner_id, action="reject", offer_id=off.id).pack(),
            ),
        )
    builder.row(
        InlineKeyboardButton(
            text="📤 Yuborgan takliflarim",
            callback_data=OfferCb(owner_id=owner_id, action="mine", page=1).pack(),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def outgoing_offers_kb(owner_id: int, offers: List[tuple[Offer, Card, User]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for off, card, owner in offers[:8]:
        val_txt = "🎴 Karta" if getattr(off, "offer_type", "coins") == "card" else f"{off.amount:,} 🪙"
        builder.row(
            InlineKeyboardButton(
                text=f"❌ Bekor: {card.character_name[:18]} ({val_txt})"[:58],
                callback_data=OfferCb(owner_id=owner_id, action="cancel", offer_id=off.id).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="📩 Kelgan takliflar",
            callback_data=OfferCb(owner_id=owner_id, action="incoming", page=1).pack(),
        ),
        InlineKeyboardButton(
            text="🛒 Bozor",
            callback_data=MarketCb(owner_id=owner_id, action="list", page=1).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(text="🏠 BOSH MENU", callback_data=NavCb(owner_id=owner_id, target="home").pack())
    )
    return builder.as_markup()


def transfer_confirm_kb(actor_telegram_id: int, transfer_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ TASDIQLASH",
            callback_data=TransferCb(owner_id=actor_telegram_id, action="confirm", transfer_id=transfer_id).pack(),
        ),
        InlineKeyboardButton(
            text="❌ BEKOR",
            callback_data=TransferCb(owner_id=actor_telegram_id, action="cancel", transfer_id=transfer_id).pack(),
        ),
    )
    return builder.as_markup()


EXTENDED_RANK_SCOPES = ("coins", "xp", "quiz", "games", "trades", "auction", "refs", "rare", "mythic", "divine")


def ranking_kb(
    owner_id: int,
    current_scope: str,
    in_group: bool,
    period: str = "all",
) -> InlineKeyboardMarkup:
    """REYTING: 1-qator asosiy reytinglar, keyingi qatorlar maxsus reytinglar, davr faqat kerak joyda."""
    builder = InlineKeyboardBuilder()

    def _btn(scope: str, label: str) -> InlineKeyboardButton:
        text = f"• {label} •" if current_scope == scope else label
        return InlineKeyboardButton(text=text, callback_data=RankingCb(owner_id=owner_id, scope=scope, period=period).pack())

    main = [_btn("global", "🌍 Global")]
    if in_group:
        main.append(_btn("group", "👥 Guruh"))
    main.append(_btn("groups", "🏰 Guruhlar"))
    builder.row(*main)
    builder.row(_btn("coins", "🪙 Boylik"), _btn("xp", "⚡ Daraja"), _btn("rare", "⭐ Yuqori rank"))
    builder.row(_btn("quiz", "🧠 Viktorina"), _btn("games", "🎲 O‘yin"), _btn("trades", "🤝 Savdo"))
    builder.row(_btn("power", "💪 Eng kuchli kolleksiya"))
    r5_name = get_rank_name_by_order(5, "Mythic")
    r6_name = get_rank_name_by_order(6, "Divine")
    m5 = get_rarity_meta(r5_name)
    m6 = get_rarity_meta(r6_name)
    builder.row(_btn("refs", "👥 Do‘stlar"), _btn("mythic", f"{m5.emoji} {r5_name}"), _btn("divine", f"{m6.emoji} {r6_name}"))
    if current_scope in EXTENDED_RANK_SCOPES:
        builder.row(
            *[
                InlineKeyboardButton(
                    text=("✅ " if period == p else "") + lbl,
                    callback_data=RankingCb(owner_id=owner_id, scope=current_scope, period=p).pack(),
                )
                for p, lbl in (("all", "Hammasi"), ("monthly", "Oy"), ("weekly", "Hafta"))
            ]
        )
    builder.row(InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()))
    return builder.as_markup()


def stars_shop_kb(owner_id: int, packages: List[Dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for pkg in packages:
        builder.row(
            InlineKeyboardButton(
                text=str(pkg.get("label", f"⭐ {pkg['stars']} Stars → {pkg['coins']:,} coins")),
                callback_data=StarsCb(owner_id=owner_id, action="buy", package_id=str(pkg["id"])).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=NavCb(owner_id=owner_id, target="profile").pack(),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()
