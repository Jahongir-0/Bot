import math
from typing import Dict, List

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from models.card import Card
from models.market import Offer
from models.user import User
from services.market_service import ListingView
from services.search_service import GlobalCardSearchItem
from utils.callback_data import MarketCb, NavCb, OfferCb, RankingCb, SearchCb, StarsCb, TransferCb, WishCb
from utils.constants import get_rarity_meta, list_rarities
from utils.i18n import normalize_lang, tr


def market_list_kb(
    owner_id: int,
    listings: List[ListingView],
    page: int,
    total: int,
    my_only: bool = False,
    page_size: int = 5,
    lang: str = "uz",
) -> InlineKeyboardMarkup:
    l = normalize_lang(lang)
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
            callback_data=MarketCb(owner_id=owner_id, action=action_name, page=page).pack(),
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

    toggle_text = tr(l, "mkt_all_btn") if my_only else tr(l, "mkt_my_btn")
    toggle_action = "list" if my_only else "my_listings"
    builder.row(
        InlineKeyboardButton(
            text=toggle_text,
            callback_data=MarketCb(owner_id=owner_id, action=toggle_action, page=1).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=tr(l, "btn_back"),
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
        InlineKeyboardButton(
            text=tr(l, "btn_home"),
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def listing_detail_kb(
    owner_id: int,
    listing_id: int,
    is_seller: bool,
    page: int = 1,
    lang: str = "uz",
) -> InlineKeyboardMarkup:
    l = normalize_lang(lang)
    builder = InlineKeyboardBuilder()
    if is_seller:
        builder.row(
            InlineKeyboardButton(
                text=tr(l, "mkt_cancel_btn"),
                callback_data=MarketCb(
                    owner_id=owner_id,
                    action="cancel",
                    listing_id=listing_id,
                    page=page,
                ).pack(),
            )
        )
    else:
        builder.row(
            InlineKeyboardButton(
                text=tr(l, "mkt_buy_btn"),
                callback_data=MarketCb(
                    owner_id=owner_id,
                    action="buy",
                    listing_id=listing_id,
                    page=page,
                ).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text=tr(l, "btn_back"),
            callback_data=MarketCb(owner_id=owner_id, action="list", page=page).pack(),
        ),
        InlineKeyboardButton(
            text=tr(l, "btn_home"),
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def search_menu_kb(owner_id: int, lang: str = "uz") -> InlineKeyboardMarkup:
    l = normalize_lang(lang)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=tr(l, "srch_btn_char"),
            callback_data=SearchCb(owner_id=owner_id, action="mode", mode="character").pack(),
        ),
        InlineKeyboardButton(
            text=tr(l, "srch_btn_anime"),
            callback_data=SearchCb(owner_id=owner_id, action="mode", mode="anime").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=tr(l, "srch_btn_rarity"),
            callback_data=SearchCb(owner_id=owner_id, action="mode", mode="rarity").pack(),
        ),
        InlineKeyboardButton(
            text=tr(l, "srch_btn_id"),
            callback_data=SearchCb(owner_id=owner_id, action="mode", mode="card_id").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=tr(l, "srch_btn_rand"),
            callback_data=SearchCb(owner_id=owner_id, action="random", mode="random", page=1).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=tr(l, "btn_back"),
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
        InlineKeyboardButton(
            text=tr(l, "btn_home"),
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def search_rarity_kb(owner_id: int, lang: str = "uz") -> InlineKeyboardMarkup:
    l = normalize_lang(lang)
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
            text=tr(l, "btn_back"),
            callback_data=SearchCb(owner_id=owner_id, action="menu").pack(),
        ),
        InlineKeyboardButton(
            text=tr(l, "btn_home"),
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
    lang: str = "uz",
) -> InlineKeyboardMarkup:
    l = normalize_lang(lang)
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
                callback_data=SearchCb(
                    owner_id=owner_id,
                    action="results",
                    mode=mode,
                    query=q_short,
                    page=page,
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
                text=tr(l, "srch_again_rand"),
                callback_data=SearchCb(owner_id=owner_id, action="random", mode="random", page=1).pack(),
            )
        )

    builder.row(
        InlineKeyboardButton(
            text=tr(l, "btn_back"),
            callback_data=SearchCb(owner_id=owner_id, action="menu").pack(),
        ),
        InlineKeyboardButton(
            text=tr(l, "btn_home"),
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
    lang: str = "uz",
) -> InlineKeyboardMarkup:
    l = normalize_lang(lang)
    q_short = _cb_q(query)
    builder = InlineKeyboardBuilder()
    if item.active_listing is not None and not is_self_owner:
        builder.row(
            InlineKeyboardButton(
                text=tr(l, "mkt_buy_btn"),
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
                text=tr(l, "srch_make_offer"),
                callback_data=OfferCb(
                    owner_id=owner_id,
                    action="prompt",
                    user_card_id=item.user_card.id,
                ).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text=tr(l, "btn_wishlist"),
            callback_data=WishCb(owner_id=owner_id, act="add", cid=item.card.id).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=tr(l, "btn_back"),
            callback_data=SearchCb(
                owner_id=owner_id,
                action="results",
                mode=mode,
                query=q_short,
                page=page,
            ).pack(),
        ),
        InlineKeyboardButton(
            text=tr(l, "btn_home"),
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
    lang: str = "uz",
) -> InlineKeyboardMarkup:
    l = normalize_lang(lang)
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
                callback_data=SearchCb(
                    owner_id=owner_id,
                    action="results",
                    mode=mode,
                    query=q_short,
                    page=page,
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
                text=tr(l, "srch_again_rand"),
                callback_data=SearchCb(owner_id=owner_id, action="random", mode="random", page=1).pack(),
            )
        )

    builder.row(
        InlineKeyboardButton(
            text=tr(l, "btn_back"),
            callback_data=SearchCb(owner_id=owner_id, action="menu").pack(),
        ),
        InlineKeyboardButton(
            text=tr(l, "btn_home"),
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
    lang: str = "uz",
) -> InlineKeyboardMarkup:
    l = normalize_lang(lang)
    q_short = _cb_q(query)
    builder = InlineKeyboardBuilder()
    if card_id > 0:
        builder.row(
            InlineKeyboardButton(
                text=tr(l, "srch_add_wish"),
                callback_data=WishCb(owner_id=owner_id, act="add", cid=card_id).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text=tr(l, "btn_back"),
            callback_data=SearchCb(
                owner_id=owner_id,
                action="results",
                mode=mode,
                query=q_short,
                page=page,
            ).pack(),
        ),
        InlineKeyboardButton(
            text=tr(l, "btn_home"),
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def offer_decision_kb(owner_id: int, offer_id: int, lang: str = "uz") -> InlineKeyboardMarkup:
    l = normalize_lang(lang)
    acc = {"uz": "✅ Qabul qilish", "en": "✅ Accept", "ru": "✅ Принять"}[l]
    rej = {"uz": "❌ Rad etish", "en": "❌ Reject", "ru": "❌ Отклонить"}[l]
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=acc,
            callback_data=OfferCb(owner_id=owner_id, action="accept", offer_id=offer_id).pack(),
        ),
        InlineKeyboardButton(
            text=rej,
            callback_data=OfferCb(owner_id=owner_id, action="reject", offer_id=offer_id).pack(),
        ),
    )
    return builder.as_markup()


def incoming_offers_kb(owner_id: int, offers: List[tuple[Offer, Card, User]], lang: str = "uz") -> InlineKeyboardMarkup:
    l = normalize_lang(lang)
    rej = {"uz": "❌ Rad", "en": "❌ Reject", "ru": "❌ Откл."}[l]
    builder = InlineKeyboardBuilder()
    for off, card, buyer in offers[:8]:
        builder.row(
            InlineKeyboardButton(
                text=f"✅ {card.character_name} ({off.amount:,} 🪙)",
                callback_data=OfferCb(owner_id=owner_id, action="accept", offer_id=off.id).pack(),
            ),
            InlineKeyboardButton(
                text=rej,
                callback_data=OfferCb(owner_id=owner_id, action="reject", offer_id=off.id).pack(),
            ),
        )
    builder.row(
        InlineKeyboardButton(
            text=tr(l, "btn_back"),
            callback_data=NavCb(owner_id=owner_id, target="cards").pack(),
        ),
        InlineKeyboardButton(
            text=tr(l, "btn_home"),
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def transfer_confirm_kb(actor_telegram_id: int, transfer_id: int, lang: str = "uz") -> InlineKeyboardMarkup:
    l = normalize_lang(lang)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=tr(l, "btn_confirm"),
            callback_data=TransferCb(owner_id=actor_telegram_id, action="confirm", transfer_id=transfer_id).pack(),
        ),
        InlineKeyboardButton(
            text=tr(l, "btn_cancel"),
            callback_data=TransferCb(owner_id=actor_telegram_id, action="cancel", transfer_id=transfer_id).pack(),
        ),
    )
    return builder.as_markup()


def ranking_kb(
    owner_id: int,
    current_scope: str,
    in_group: bool,
    period: str = "all",
    lang: str = "uz",
) -> InlineKeyboardMarkup:
    l = normalize_lang(lang)
    builder = InlineKeyboardBuilder()
    g_lbl = tr(l, "rnk_global")
    grp_lbl = tr(l, "rnk_group")
    grps_lbl = tr(l, "rnk_groups")
    buttons = [
        InlineKeyboardButton(
            text=(f"• {g_lbl} •" if current_scope == "global" else g_lbl),
            callback_data=RankingCb(owner_id=owner_id, scope="global", period=period).pack(),
        )
    ]
    if in_group:
        buttons.append(
            InlineKeyboardButton(
                text=(f"• {grp_lbl} •" if current_scope == "group" else grp_lbl),
                callback_data=RankingCb(owner_id=owner_id, scope="group", period=period).pack(),
            )
        )
    buttons.append(
        InlineKeyboardButton(
            text=(f"• {grps_lbl} •" if current_scope == "groups" else grps_lbl),
            callback_data=RankingCb(owner_id=owner_id, scope="groups", period=period).pack(),
        )
    )
    builder.row(*buttons)
    extra_scopes = [
        ("coins", "🪙 Coins"),
        ("xp", "⚡ XP"),
        ("quiz", "🧠 Quiz"),
        ("games", "🎲 Games"),
        ("trades", "🤝 Trade"),
        ("refs", "📣 Ref"),
        ("rare", "💎 Rare+"),
        ("mythic", "🔴 Mythic"),
        ("divine", "✨ Divine"),
    ]
    for i in range(0, len(extra_scopes), 3):
        builder.row(
            *[
                InlineKeyboardButton(
                    text=("• " if current_scope == k else "") + lbl,
                    callback_data=RankingCb(owner_id=owner_id, scope=k, period=period).pack(),
                )
                for k, lbl in extra_scopes[i : i + 3]
            ]
        )
    periods = {
        "uz": (("all", "Barcha vaqt"), ("monthly", "Oylik"), ("weekly", "Haftalik")),
        "en": (("all", "All time"), ("monthly", "Monthly"), ("weekly", "Weekly")),
        "ru": (("all", "За всё время"), ("monthly", "Месяц"), ("weekly", "Неделя")),
    }[l]
    builder.row(
        *[
            InlineKeyboardButton(
                text=("✅ " if period == p else "") + lbl,
                callback_data=RankingCb(owner_id=owner_id, scope=current_scope, period=p).pack(),
            )
            for p, lbl in periods
        ]
    )
    builder.row(
        InlineKeyboardButton(
            text=tr(l, "btn_back"),
            callback_data=NavCb(owner_id=owner_id, target="profile").pack(),
        ),
        InlineKeyboardButton(
            text=tr(l, "btn_home"),
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def stars_shop_kb(owner_id: int, packages: List[Dict], lang: str = "uz") -> InlineKeyboardMarkup:
    l = normalize_lang(lang)
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
            text=tr(l, "btn_back"),
            callback_data=NavCb(owner_id=owner_id, target="profile").pack(),
        ),
        InlineKeyboardButton(
            text=tr(l, "btn_home"),
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()
