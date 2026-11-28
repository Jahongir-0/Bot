import math
from typing import List

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from models.card import Card
from models.user_card import UserCard
from services.collection_service import AnimeSection, CardVariant, CharacterSection, CharacterStack
from utils.callback_data import AucCb, CollectionCb, GiveCb, NavCb, PageJumpCb, SearchCb
from utils.constants import get_rarity_meta, list_rarities
from utils.formatting import event_tag, level_badge

# Callback data 64 baytdan oshmasligi va ':' belgisini o'z ichiga olmasligi shart.
CARDS_PAGE_SIZE = 5


def _footer(builder: InlineKeyboardBuilder, owner_id: int, back_pack: str) -> None:
    builder.row(
        InlineKeyboardButton(text="⬅️ Orqaga", callback_data=back_pack),
        InlineKeyboardButton(
            text="🏠 Bosh Menu",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )


def _view_back(owner_id: int) -> str:
    return CollectionCb(owner_id=owner_id, action="view").pack()


def _pager(
    builder: InlineKeyboardBuilder,
    page: int,
    total_pages: int,
    make_cb,
    jump_cb: str | None = None,
) -> None:
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=make_cb(page - 1)))
    nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data=jump_cb or make_cb(page)))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=make_cb(page + 1)))
    builder.row(*nav)


def collection_summary_kb(owner_id: int) -> InlineKeyboardMarkup:
    """Kartalarim (Bot 2.5):
    Hammasi   | Anime bo'yicha
    Sevimlilar| Rank bo'yicha
    Qidiruv   | Birlashtirish
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🗂 Hammasi",
            callback_data=CollectionCb(
                owner_id=owner_id, action="v2_list", filter_val="all", sort_by="newest", page=1
            ).pack(),
        ),
        InlineKeyboardButton(
            text="📺 Anime bo‘yicha",
            callback_data=CollectionCb(owner_id=owner_id, action="anime_list", page=1).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="❤️ Sevimlilar",
            callback_data=CollectionCb(
                owner_id=owner_id, action="v2_list", filter_val="favorites", sort_by="newest", page=1
            ).pack(),
        ),
        InlineKeyboardButton(
            text="⭐ Rank bo‘yicha",
            callback_data=CollectionCb(owner_id=owner_id, action="by_rarity").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🔍 Qidiruv",
            callback_data=CollectionCb(owner_id=owner_id, action="search").pack(),
        ),
        InlineKeyboardButton(
            text="🔮 Birlashtirish",
            callback_data=CollectionCb(owner_id=owner_id, action="merge").pack(),
        ),
    )
    _footer(builder, owner_id, NavCb(owner_id=owner_id, target="profile").pack())
    return builder.as_markup()


def collection_search_hub_kb(owner_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🎴 O‘zimni kartalarim",
            callback_data=CollectionCb(owner_id=owner_id, action="my_search").pack(),
        ),
        InlineKeyboardButton(
            text="🌐 Boshqa kartalar",
            callback_data=SearchCb(owner_id=owner_id, action="menu").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🎲 Tasodifiy kartalar",
            callback_data=SearchCb(owner_id=owner_id, action="random", mode="random", page=1).pack(),
        )
    )
    _footer(builder, owner_id, _view_back(owner_id))
    return builder.as_markup()


def collection_my_search_results_kb(
    owner_id: int,
    rows: List[tuple[UserCard, Card]],
    page: int,
    total: int,
    page_size: int = CARDS_PAGE_SIZE,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for uc, card in rows:
        meta = get_rarity_meta(card.rarity)
        fav = "❤️ " if uc.is_favorite else ""
        lv = level_badge(getattr(uc, "level", 1))
        builder.row(
            InlineKeyboardButton(
                text=f"{fav}{lv}{meta.emoji} {card.character_name[:24]} ({card.anime_name[:16]})"[:60],
                callback_data=CollectionCb(
                    owner_id=owner_id, action="card_detail", filter_val="srch", page=page, item_id=uc.id
                ).pack(),
            )
        )
    total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 1
    if total_pages > 1:
        _pager(
            builder,
            page,
            total_pages,
            lambda p: CollectionCb(owner_id=owner_id, action="my_search_p", page=p).pack(),
            jump_cb=PageJumpCb(owner_id=owner_id, key="c_ms", total=total_pages).pack(),
        )
    builder.row(
        InlineKeyboardButton(
            text="🔍 Yangi qidiruv",
            callback_data=CollectionCb(owner_id=owner_id, action="my_search").pack(),
        )
    )
    _footer(builder, owner_id, CollectionCb(owner_id=owner_id, action="search").pack())
    return builder.as_markup()


def v2_collection_list_kb(
    owner_id: int,
    rows: List[tuple[UserCard, Card]],
    filter_tab: str,
    sort_by: str,
    page: int,
    total: int,
    page_size: int = CARDS_PAGE_SIZE,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if filter_tab != "favorites":
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
        lv = level_badge(getattr(uc, "level", 1))
        builder.row(
            InlineKeyboardButton(
                text=f"{fav}{sc}{lv}{event_tag(card)}{meta.emoji} {card.character_name[:26]} · {card.anime_name[:14]}"[:60],
                callback_data=CollectionCb(
                    owner_id=owner_id,
                    action="card_detail",
                    filter_val=filter_tab,
                    sort_by=sort_by,
                    page=page,
                    item_id=uc.id,
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
        jump_cb=PageJumpCb(
            owner_id=owner_id, key="c_v2", total=total_pages, ext=f"{filter_tab}|{sort_by}"[:20]
        ).pack(),
    )
    _footer(builder, owner_id, _view_back(owner_id))
    return builder.as_markup()


def anime_sections_kb(
    owner_id: int,
    sections: List[AnimeSection],
    page: int,
    total: int,
    page_size: int = CARDS_PAGE_SIZE,
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
        jump_cb=PageJumpCb(owner_id=owner_id, key="c_an", total=total_pages).pack(),
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
    page_size: int = CARDS_PAGE_SIZE,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for ch in chars:
        fav = ("❤️ " if ch.is_favorite else "") + ("🎯 " if ch.has_event_card else "")
        variants_note = f" ({ch.variants} xil)" if ch.variants > 1 else ""
        builder.row(
            InlineKeyboardButton(
                text=f"{fav}🎴 {ch.character_name[:26]} · {ch.copies} ta{variants_note}"[:60],
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
        jump_cb=PageJumpCb(
            owner_id=owner_id, key="c_ch", total=total_pages, tid=anime_rep_id, ext=str(anime_page)
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
                text=f"{event_tag(v.card)}{meta.emoji} {meta.name} · {v.card.card_code} × {v.quantity}"[:60],
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
                    filter_val=f"rar_{rarity}",
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
    page_size: int = CARDS_PAGE_SIZE,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for st in stacks:
        meta = get_rarity_meta(st.card.rarity)
        fav_prefix = "❤️ " if st.is_favorite else ""
        builder.row(
            InlineKeyboardButton(
                text=f"{fav_prefix}{event_tag(st.card)}{meta.emoji} {st.card.character_name[:26]} ({st.card.anime_name[:14]})"[:60],
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
        jump_cb=PageJumpCb(
            owner_id=owner_id, key="c_rl", total=total_pages, ext=filter_val.replace(":", " ")[:18]
        ).pack(),
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
    can_upgrade: bool = True,
) -> InlineKeyboardMarkup:
    """Bot 2.5 karta tafsiloti tugmalari:
    Batafsil    | Daraja ko'tarish
    Sevimliga   | Vitrinaga
    Marketga    | Botga sotish
    Auksionga   | Userga sotish
    Sovg'a qilish
    """
    builder = InlineKeyboardBuilder()
    row1 = [
        InlineKeyboardButton(
            text="📖 Batafsil",
            callback_data=CollectionCb(
                owner_id=owner_id, action="about", filter_val="d", page=1, item_id=user_card_id
            ).pack(),
        )
    ]
    if can_upgrade:
        row1.append(
            InlineKeyboardButton(
                text="⬆️ Daraja ko‘tarish",
                callback_data=CollectionCb(
                    owner_id=owner_id, action="lvl_ask", filter_val="d", page=1, item_id=user_card_id
                ).pack(),
            )
        )
    builder.row(*row1)

    fav_text = "❤️ Sevimli ✅" if is_favorite else "❤️ Sevimliga"
    show_text = "🖼 Vitrina ✅" if is_showcased else "🖼 Vitrinaga"
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

    if not is_locked:
        builder.row(
            InlineKeyboardButton(
                text="🛒 Marketga",
                callback_data=CollectionCb(
                    owner_id=owner_id, action="list_prompt", filter_val="d", page=1, item_id=user_card_id
                ).pack(),
            ),
            InlineKeyboardButton(
                text="🤖 Botga sotish",
                callback_data=CollectionCb(
                    owner_id=owner_id, action="bot_sell_ask", filter_val="d", page=1, item_id=user_card_id
                ).pack(),
            ),
        )
        builder.row(
            InlineKeyboardButton(
                text="🔨 Auksionga",
                callback_data=AucCb(owner_id=owner_id, act="uc_new", aid=user_card_id).pack(),
            ),
            InlineKeyboardButton(
                text="💸 Userga sotish",
                callback_data=GiveCb(owner_id=owner_id, action="sell_uc", user_card_id=user_card_id).pack(),
            ),
        )
        builder.row(
            InlineKeyboardButton(
                text="🎁 Sovg‘a qilish",
                callback_data=GiveCb(owner_id=owner_id, action="gift_uc", user_card_id=user_card_id).pack(),
            )
        )
    else:
        builder.row(
            InlineKeyboardButton(
                text="🔓 Bandlikni tekshirish / yechish",
                callback_data=CollectionCb(
                    owner_id=owner_id, action="unlock_uc", filter_val="d", page=1, item_id=user_card_id
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
