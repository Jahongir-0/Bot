import math
from typing import List

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from models.card import Card
from models.user_card import UserCard
from services.collection_service import AnimeSection, CardVariant, CharacterSection, CharacterStack
from utils.callback_data import CollectionCb, NavCb, OfferCb, WishCb
from utils.constants import get_rarity_meta, list_rarities
from utils.formatting import level_badge, event_tag

# Callback data 64 baytdan oshmasligi va ':' belgisini o'z ichiga olmasligi shart,
# shuning uchun filtrlar faqat qisqa raqam/lotin matn ko'rinishida uzatiladi.


def _footer(builder: InlineKeyboardBuilder, owner_id: int, back_pack: str) -> None:
    builder.row(
        InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=back_pack),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )


def _view_back(owner_id: int) -> str:
    return CollectionCb(owner_id=owner_id, action="view").pack()


def _pager(builder: InlineKeyboardBuilder, page: int, total_pages: int, make_cb) -> None:
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=make_cb(page - 1)))
    nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data=make_cb(page)))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=make_cb(page + 1)))
    builder.row(*nav)


def collection_summary_kb(owner_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🗂 HAMMASI",
            callback_data=CollectionCb(owner_id=owner_id, action="v2_list", filter_val="all", sort_by="newest", page=1).pack(),
        ),
        InlineKeyboardButton(
            text="📺 ASAR BO‘YICHA",
            callback_data=CollectionCb(owner_id=owner_id, action="anime_list", page=1).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="⭐ RANK BO‘YICHA",
            callback_data=CollectionCb(owner_id=owner_id, action="by_rarity").pack(),
        ),
        InlineKeyboardButton(
            text="📩 TAKLIFLAR",
            callback_data=OfferCb(owner_id=owner_id, action="incoming", page=1).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🔮 BIRLASHTIRISH",
            callback_data=CollectionCb(owner_id=owner_id, action="merge").pack(),
        )
    )
    _footer(builder, owner_id, NavCb(owner_id=owner_id, target="profile").pack())
    return builder.as_markup()


def v2_collection_list_kb(
    owner_id: int,
    rows: List[tuple[UserCard, Card]],
    filter_tab: str,
    sort_by: str,
    page: int,
    total: int,
    page_size: int = 20,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    tabs = [
        ("all", "Hammasi"),
        ("anime", "Anime"),
        ("manga", "Manga"),
        ("movie", "Kino"),
        ("cartoon", "Multfilm"),
        ("event", "Event"),
        ("limited", "Limitli"),
        ("favorites", "❤️ Sevimli"),
    ]
    for i in range(0, len(tabs), 4):
        builder.row(
            *[
                InlineKeyboardButton(
                    text=("✅ " if filter_tab == k else "") + lbl,
                    callback_data=CollectionCb(
                        owner_id=owner_id, action="v2_list", filter_val=k, sort_by=sort_by, page=1
                    ).pack(),
                )
                for k, lbl in tabs[i : i + 4]
            ]
        )
    sorts = [("newest", "🆕 Yangi"), ("rarest", "💎 Qiymatli"), ("alpha", "🔤 A-Z"), ("media", "📺 Asar")]
    builder.row(
        *[
            InlineKeyboardButton(
                text=("• " if sort_by == s else "") + lbl,
                callback_data=CollectionCb(
                    owner_id=owner_id, action="v2_list", filter_val=filter_tab, sort_by=s, page=1
                ).pack(),
            )
            for s, lbl in sorts
        ]
    )
    for uc, card in rows:
        meta = get_rarity_meta(card.rarity)
        fav = "❤️ " if uc.is_favorite else ""
        sc = "🖼 " if getattr(uc, "is_showcased", False) else ""
        ser = f"#{uc.serial_number}" if getattr(uc, "serial_number", None) else ""
        lv = level_badge(getattr(uc, "level", 1))
        builder.row(
            InlineKeyboardButton(
                text=f"{fav}{sc}{lv}{event_tag(card)}{meta.emoji} {card.character_name[:22]} {ser}"[:60],
                callback_data=CollectionCb(
                    owner_id=owner_id, action="card_detail", filter_val=filter_tab, sort_by=sort_by, page=page, item_id=uc.id
                ).pack(),
            )
        )
    total_pages = max(1, math.ceil(total / page_size))
    _pager(
        builder,
        page,
        total_pages,
        lambda p: CollectionCb(
            owner_id=owner_id, action="v2_list", filter_val=filter_tab, sort_by=sort_by, page=p
        ).pack(),
    )
    _footer(builder, owner_id, _view_back(owner_id))
    return builder.as_markup()


def anime_sections_kb(
    owner_id: int,
    sections: List[AnimeSection],
    page: int,
    total: int,
    page_size: int = 6,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for sec in sections:
        builder.row(
            InlineKeyboardButton(
                text=f"📺 {sec.anime_name[:30]} · {sec.copies} ta",
                callback_data=CollectionCb(
                    owner_id=owner_id,
                    action="anime_open",
                    item_id=sec.rep_card_id,
                    filter_val=str(page),
                    page=1,
                ).pack(),
            )
        )
    total_pages = max(1, math.ceil(total / page_size))
    _pager(
        builder,
        page,
        total_pages,
        lambda p: CollectionCb(owner_id=owner_id, action="anime_list", page=p).pack(),
    )
    _footer(builder, owner_id, _view_back(owner_id))
    return builder.as_markup()


def anime_characters_kb(
    owner_id: int,
    anime_rep_id: int,
    anime_page: int,
    chars: List[CharacterSection],
    page: int,
    total: int,
    page_size: int = 6,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for ch in chars:
        fav = ("❤️ " if ch.is_favorite else "") + ("🎯 " if ch.has_event_card else "")
        variants_note = f" ({ch.variants} xil)" if ch.variants > 1 else ""
        builder.row(
            InlineKeyboardButton(
                text=f"{fav}🎴 {ch.character_name[:26]} × {ch.copies}{variants_note}",
                callback_data=CollectionCb(
                    owner_id=owner_id,
                    action="char_open",
                    item_id=ch.rep_card_id,
                    filter_val=f"{page}-{anime_page}",
                    page=1,
                ).pack(),
            )
        )
    total_pages = max(1, math.ceil(total / page_size))
    _pager(
        builder,
        page,
        total_pages,
        lambda p: CollectionCb(
            owner_id=owner_id,
            action="anime_open",
            item_id=anime_rep_id,
            filter_val=str(anime_page),
            page=p,
        ).pack(),
    )
    _footer(
        builder,
        owner_id,
        CollectionCb(owner_id=owner_id, action="anime_list", page=anime_page).pack(),
    )
    return builder.as_markup()


def character_variants_kb(
    owner_id: int,
    variants: List[CardVariant],
    anime_rep_id: int,
    char_page: int,
    anime_page: int,
    char_rep_id: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for v in variants:
        meta = get_rarity_meta(v.card.rarity)
        builder.row(
            InlineKeyboardButton(
                text=f"{event_tag(v.card)}{meta.emoji} {meta.name} · {v.card.card_code} × {v.quantity}",
                callback_data=CollectionCb(
                    owner_id=owner_id,
                    action="card_detail",
                    item_id=v.sample_user_card_id,
                    filter_val="d",
                    page=1,
                ).pack(),
            )
        )
    _footer(
        builder,
        owner_id,
        CollectionCb(
            owner_id=owner_id,
            action="anime_open",
            item_id=anime_rep_id,
            filter_val=str(anime_page),
            page=char_page,
        ).pack(),
    )
    return builder.as_markup()


def collection_rarity_filter_kb(owner_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for rarity in list_rarities():
        meta = get_rarity_meta(rarity)
        builder.row(
            InlineKeyboardButton(
                text=f"{meta.emoji} {rarity}",
                callback_data=CollectionCb(
                    owner_id=owner_id,
                    action="char_list",
                    filter_val=f"rar_{rarity}",  # ':' ishlatilmaydi (CallbackData ajratuvchisi)
                    page=1,
                ).pack(),
            )
        )
    _footer(builder, owner_id, _view_back(owner_id))
    return builder.as_markup()


def collection_list_kb(
    owner_id: int,
    stacks: List[CharacterStack],
    filter_val: str,
    page: int,
    total: int,
    page_size: int = 6,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for st in stacks:
        meta = get_rarity_meta(st.card.rarity)
        fav_prefix = "❤️ " if st.is_favorite else ""
        builder.row(
            InlineKeyboardButton(
                text=f"{fav_prefix}{event_tag(st.card)}{meta.emoji} {st.card.character_name[:24]} × {st.quantity}",
                callback_data=CollectionCb(
                    owner_id=owner_id,
                    action="card_detail",
                    filter_val="d",
                    page=page,
                    item_id=st.sample_user_card_id,
                ).pack(),
            )
        )
    total_pages = max(1, math.ceil(total / page_size))
    _pager(
        builder,
        page,
        total_pages,
        lambda p: CollectionCb(owner_id=owner_id, action="char_list", filter_val=filter_val, page=p).pack(),
    )
    _footer(builder, owner_id, CollectionCb(owner_id=owner_id, action="by_rarity").pack())
    return builder.as_markup()


def card_detail_kb(
    owner_id: int,
    user_card_id: int,
    is_locked: bool,
    is_favorite: bool,
    char_rep_id: int,
    card_id: int = 0,
    is_showcased: bool = False,
    level_label: str = "",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if level_label:
        builder.row(
            InlineKeyboardButton(
                text=level_label,
                callback_data=CollectionCb(
                    owner_id=owner_id, action="lvl_ask", filter_val="d", page=1, item_id=user_card_id
                ).pack(),
            )
        )
    fav_text = "💔 Sevimlidan olish" if is_favorite else "❤️ Sevimliga qo‘shish"
    show_text = "🖼 Vitrinadan olish" if is_showcased else "🖼 Vitrinaga qo‘yish"
    builder.row(
        InlineKeyboardButton(
            text=fav_text,
            callback_data=CollectionCb(
                owner_id=owner_id, action="toggle_fav", filter_val="d", page=1, item_id=user_card_id
            ).pack(),
        ),
        InlineKeyboardButton(
            text=show_text,
            callback_data=CollectionCb(
                owner_id=owner_id, action="toggle_show", filter_val="d", page=1, item_id=user_card_id
            ).pack(),
        ),
    )
    row2 = [
        InlineKeyboardButton(
            text="📖 Batafsil",
            callback_data=CollectionCb(
                owner_id=owner_id, action="about", filter_val="d", page=1, item_id=user_card_id
            ).pack(),
        )
    ]
    if card_id > 0:
        row2.append(
            InlineKeyboardButton(
                text="💖 Istaklarga",
                callback_data=WishCb(owner_id=owner_id, act="add", cid=card_id).pack(),
            )
        )
    builder.row(*row2)
    if not is_locked:
        builder.row(
            InlineKeyboardButton(
                text="🛒 MARKETGA QO‘YISH",
                callback_data=CollectionCb(
                    owner_id=owner_id, action="list_prompt", filter_val="d", page=1, item_id=user_card_id
                ).pack(),
            )
        )
    _footer(
        builder,
        owner_id,
        CollectionCb(
            owner_id=owner_id, action="char_open", item_id=char_rep_id, filter_val="1-1", page=1
        ).pack(),
    )
    return builder.as_markup()
