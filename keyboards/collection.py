import math
from typing import List

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from services.collection_service import CharacterStack
from utils.callback_data import CollectionCb, NavCb, OfferCb
from utils.constants import get_rarity_meta, list_rarities


def collection_summary_kb(owner_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="📋 BARCHA KARTALAR",
            callback_data=CollectionCb(owner_id=owner_id, action="char_list", filter_val="all", page=1).pack(),
        ),
        InlineKeyboardButton(
            text="📩 KELGAN TAKLIFLAR",
            callback_data=OfferCb(owner_id=owner_id, action="incoming", page=1).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="📺 ANIME BO‘YICHA",
            callback_data=CollectionCb(owner_id=owner_id, action="by_anime").pack(),
        ),
        InlineKeyboardButton(
            text="⭐ RARITY BO‘YICHA",
            callback_data=CollectionCb(owner_id=owner_id, action="by_rarity").pack(),
        ),
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


def collection_anime_filter_kb(owner_id: int, anime_items: List[tuple[str, str]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for name, anime_id in anime_items[:12]:
        builder.row(
            InlineKeyboardButton(
                text=f"📺 {name[:32]}",
                callback_data=CollectionCb(
                    owner_id=owner_id,
                    action="char_list",
                    filter_val=f"a:{anime_id[:14]}",
                    page=1,
                ).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=CollectionCb(owner_id=owner_id, action="view").pack(),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
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
                    filter_val=f"rarity:{rarity}",
                    page=1,
                ).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=CollectionCb(owner_id=owner_id, action="view").pack(),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
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
                text=f"{fav_prefix}{meta.emoji} {st.card.character_name} × {st.quantity}",
                callback_data=CollectionCb(
                    owner_id=owner_id,
                    action="card_detail",
                    filter_val=filter_val,
                    page=page,
                    item_id=st.sample_user_card_id,
                ).pack(),
            )
        )

    total_pages = max(1, math.ceil(total / page_size))
    nav_buttons = []
    if page > 1:
        nav_buttons.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=CollectionCb(
                    owner_id=owner_id,
                    action="char_list",
                    filter_val=filter_val,
                    page=page - 1,
                ).pack(),
            )
        )
    nav_buttons.append(
        InlineKeyboardButton(
            text=f"{page}/{total_pages}",
            callback_data=CollectionCb(
                owner_id=owner_id,
                action="char_list",
                filter_val=filter_val,
                page=page,
            ).pack(),
        )
    )
    if page < total_pages:
        nav_buttons.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=CollectionCb(
                    owner_id=owner_id,
                    action="char_list",
                    filter_val=filter_val,
                    page=page + 1,
                ).pack(),
            )
        )
    builder.row(*nav_buttons)
    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=CollectionCb(owner_id=owner_id, action="view").pack(),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def card_detail_kb(
    owner_id: int,
    user_card_id: int,
    is_locked: bool,
    is_favorite: bool,
    filter_val: str = "all",
    page: int = 1,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    fav_text = "💔 Sevimlidan olish" if is_favorite else "❤️ Sevimliga qo‘shish"
    builder.row(
        InlineKeyboardButton(
            text=fav_text,
            callback_data=CollectionCb(
                owner_id=owner_id,
                action="toggle_fav",
                filter_val=filter_val,
                page=page,
                item_id=user_card_id,
            ).pack(),
        )
    )
    if not is_locked:
        builder.row(
            InlineKeyboardButton(
                text="🛒 MARKETGA QO‘YISH",
                callback_data=CollectionCb(
                    owner_id=owner_id,
                    action="list_prompt",
                    filter_val=filter_val,
                    page=page,
                    item_id=user_card_id,
                ).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=CollectionCb(
                owner_id=owner_id,
                action="char_list",
                filter_val=filter_val,
                page=page,
            ).pack(),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()
