from typing import List

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from database.session import get_session
from keyboards.collection import (
    anime_characters_kb,
    anime_sections_kb,
    card_detail_kb,
    character_variants_kb,
    collection_list_kb,
    collection_rarity_filter_kb,
    collection_summary_kb,
    v2_collection_list_kb,
)
from keyboards.main_menu import back_home_kb
from models.card import Card
from models.user import User
from models.user_card import UserCard
from services.collection_service import CollectionService
from services.market_service import MarketService
from utils.callback_data import CollectionCb, NavCb
from utils.constants import MAX_CARD_PRICE, MIN_CARD_PRICE, PAGE_SIZE, get_rarity_meta
from utils.formatting import esc, event_tag, fmt_coins, format_card_about, format_card_block, format_collection_summary
from utils.i18n import get_lang, tr
from utils.tg_helpers import NotCommandFilter, edit_or_answer, is_private_chat, send_card_message

router = Router(name="collection")

ANIME_PAGE_SIZE = 6
CHAR_PAGE_SIZE = 6


class SellCardStates(StatesGroup):
    waiting_price = State()


def _ints(value: str, count: int) -> List[int]:
    """'3-2' -> [3, 2]; noto'g'ri/yetishmagan qiymatlar 1 bilan to'ldiriladi."""
    out: List[int] = []
    for part in (value or "").split("-")[:count]:
        out.append(int(part) if part.isdigit() and int(part) > 0 else 1)
    while len(out) < count:
        out.append(1)
    return out


async def _show_summary(target, owner_id: int, db_user: User) -> None:
    lang = get_lang(db_user)
    async with get_session() as session:
        anime_counts, rarity_counts, favorites = await CollectionService.get_summary(
            session, db_user.id
        )
    text = format_collection_summary(db_user, anime_counts, rarity_counts, favorites, lang=lang)
    await edit_or_answer(target, text, reply_markup=collection_summary_kb(owner_id, lang=lang))


@router.message(Command("cards", "collection"))
async def cmd_collection(message: Message, db_user: User) -> None:
    await _show_summary(message, message.from_user.id, db_user)


@router.callback_query(NavCb.filter(F.target == "cards"))
@router.callback_query(CollectionCb.filter(F.action == "view"))
async def cb_collection_summary(callback: CallbackQuery, db_user: User) -> None:
    await _show_summary(callback, callback.from_user.id, db_user)


@router.callback_query(CollectionCb.filter(F.action == "v2_list"))
async def cb_v2_collection_list(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    lang = get_lang(db_user)
    filter_tab = callback_data.filter_val or "all"
    sort_by = callback_data.sort_by or "newest"
    page = max(1, callback_data.page)
    async with get_session() as session:
        rows, total = await CollectionService.get_filtered_user_cards(
            session, db_user.id, filter_tab=filter_tab, sort_by=sort_by, page=page, page_size=PAGE_SIZE
        )
    if not rows and total > 0 and page > 1:
        page = 1
        async with get_session() as session:
            rows, total = await CollectionService.get_filtered_user_cards(
                session, db_user.id, filter_tab=filter_tab, sort_by=sort_by, page=1, page_size=PAGE_SIZE
            )
    lines = [tr(lang, "col_list_hdr", tab=filter_tab.upper(), total=total)]
    if not rows:
        lines.append(tr(lang, "col_empty_tab"))
    else:
        for uc, c in rows:
            meta = get_rarity_meta(c.rarity)
            fav = "❤️ " if uc.is_favorite else ""
            ser = f"#{uc.serial_number}" if getattr(uc, "serial_number", None) else ""
            lines.append(f"{fav}{event_tag(c)}{meta.emoji} <b>{esc(c.character_name)}</b> · {esc(c.anime_name)} {ser}")
    await edit_or_answer(
        callback,
        "\n".join(lines),
        reply_markup=v2_collection_list_kb(
            callback.from_user.id, rows, filter_tab, sort_by, page, total, page_size=PAGE_SIZE, lang=lang
        ),
    )


@router.callback_query(CollectionCb.filter(F.action.in_({"anime_list", "by_anime"})))
async def cb_anime_list(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    lang = get_lang(db_user)
    page = max(1, callback_data.page)
    async with get_session() as session:
        sections, total = await CollectionService.get_anime_sections(
            session, db_user.id, page=page, page_size=ANIME_PAGE_SIZE
        )
        if not sections and total > 0:
            page = 1
            sections, total = await CollectionService.get_anime_sections(
                session, db_user.id, page=1, page_size=ANIME_PAGE_SIZE
            )

    if total == 0:
        await edit_or_answer(
            callback,
            tr(lang, "col_empty"),
            reply_markup=back_home_kb(callback.from_user.id, "cards", lang),
        )
        return

    lines = [tr(lang, "col_media_hdr", total=total)]
    for sec in sections:
        lines.append(f"• <b>{esc(sec.anime_name)}</b> — {sec.characters} / {sec.copies}")
    await edit_or_answer(
        callback,
        "\n".join(lines),
        reply_markup=anime_sections_kb(callback.from_user.id, sections, page, total, ANIME_PAGE_SIZE, lang=lang),
    )


@router.callback_query(CollectionCb.filter(F.action == "anime_open"))
async def cb_anime_open(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    lang = get_lang(db_user)
    anime_page = _ints(callback_data.filter_val, 1)[0]
    page = max(1, callback_data.page)
    async with get_session() as session:
        info = await CollectionService.get_anime_characters(
            session, db_user.id, callback_data.item_id, page=page, page_size=CHAR_PAGE_SIZE
        )
        if info is not None and not info[1]:
            page = 1
            info = await CollectionService.get_anime_characters(
                session, db_user.id, callback_data.item_id, page=1, page_size=CHAR_PAGE_SIZE
            )

    if info is None:
        await callback.answer(tr(lang, "col_empty_tab"), show_alert=True)
        await cb_anime_list(callback, CollectionCb(owner_id=callback.from_user.id, action="anime_list", page=anime_page), db_user)
        return

    anime_name, chars, total = info
    lines = [f"📺 <b>{esc(anime_name)}</b> ({total})\n"]
    for ch in chars:
        fav = ("❤️ " if ch.is_favorite else "") + ("🎯 " if ch.has_event_card else "")
        note = f" ({ch.variants}x)" if ch.variants > 1 else ""
        lines.append(f"{fav}🎴 <b>{esc(ch.character_name)}</b> × {ch.copies}{note}")
    await edit_or_answer(
        callback,
        "\n".join(lines),
        reply_markup=anime_characters_kb(
            callback.from_user.id, callback_data.item_id, anime_page, chars, page, total, CHAR_PAGE_SIZE, lang=lang
        ),
    )


@router.callback_query(CollectionCb.filter(F.action == "char_open"))
async def cb_char_open(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    lang = get_lang(db_user)
    char_page, anime_page = _ints(callback_data.filter_val, 2)
    async with get_session() as session:
        info = await CollectionService.get_character_variants(session, db_user.id, callback_data.item_id)

    if info is None:
        await callback.answer(tr(lang, "card_not_found"), show_alert=True)
        await cb_anime_list(callback, CollectionCb(owner_id=callback.from_user.id, action="anime_list", page=anime_page), db_user)
        return

    character_name, anime_name, variants, anime_rep_id = info
    total_copies = sum(v.quantity for v in variants)
    lines = [
        f"🎴 <b>{esc(character_name)}</b>",
        f"📺 {esc(anime_name)}",
        "",
        f"📦 Total: <b>{total_copies}</b> · {len(variants)} variants\n",
    ]
    for v in variants:
        meta = get_rarity_meta(v.card.rarity)
        tag = "  🎯 <i>EVENT</i>" if v.card.is_event_card else ""
        lines.append(
            f"{event_tag(v.card)}{meta.emoji} {esc(v.card.rarity)} · <code>{esc(v.card.card_code)}</code> × {v.quantity} "
            f"— 💰 {fmt_coins(v.card.base_value)}{tag}"
        )
    await edit_or_answer(
        callback,
        "\n".join(lines),
        reply_markup=character_variants_kb(
            callback.from_user.id, variants, anime_rep_id, char_page, anime_page, callback_data.item_id, lang=lang
        ),
    )


@router.callback_query(CollectionCb.filter(F.action == "by_rarity"))
async def cb_by_rarity(callback: CallbackQuery, db_user: User) -> None:
    lang = get_lang(db_user)
    await edit_or_answer(
        callback,
        tr(lang, "col_rarity_prompt"),
        reply_markup=collection_rarity_filter_kb(callback.from_user.id, lang=lang),
    )


@router.callback_query(CollectionCb.filter(F.action == "char_list"))
async def cb_char_list(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    lang = get_lang(db_user)
    rarity_filter = None
    if callback_data.filter_val.startswith("rar_"):
        rarity_filter = callback_data.filter_val[4:]

    async with get_session() as session:
        stacks, total = await CollectionService.get_character_stacks(
            session=session,
            user_id=db_user.id,
            rarity_filter=rarity_filter,
            page=callback_data.page,
            page_size=6,
        )

    if not stacks:
        text = tr(lang, "col_empty_tab")
    else:
        lines = [f"🎴 <b>{esc(rarity_filter or 'COLLECTION')}</b> ({total})\n"]
        for st in stacks:
            fav = "❤️ " if st.is_favorite else ""
            lines.append(f"{fav}• {event_tag(st.card)}<b>{esc(st.card.character_name)}</b> × {st.quantity} ({esc(st.card.anime_name)})")
        text = "\n".join(lines)

    await edit_or_answer(
        callback,
        text,
        reply_markup=collection_list_kb(
            owner_id=callback.from_user.id,
            stacks=stacks,
            filter_val=callback_data.filter_val,
            page=callback_data.page,
            total=total,
            page_size=6,
            lang=lang,
        ),
    )


@router.callback_query(CollectionCb.filter(F.action == "card_detail"))
async def cb_card_detail(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    lang = get_lang(db_user)
    async with get_session() as session:
        uc = await session.get(UserCard, callback_data.item_id)
        if uc is None or uc.user_id != db_user.id:
            await callback.answer(tr(lang, "card_not_found"), show_alert=True)
            return
        card = await session.get(Card, uc.card_id)
        unlocked_copy = await CollectionService.get_unlocked_copy(session, db_user.id, card.id)
        qty = await CollectionService.count_copies(session, db_user.id, card.id)
        reps = await CollectionService.get_card_group_reps(session, card.id)

    char_rep_id = reps[1] if reps else card.id
    target_uc_id = unlocked_copy.id if unlocked_copy else uc.id
    is_locked = unlocked_copy is None
    st_text = tr(lang, "col_detail_locked") if is_locked else tr(lang, "col_detail_ready")
    text = (
        f"{format_card_block(card, serial_number=getattr(uc, 'serial_number', None), state_label=getattr(uc, 'card_state', 'OWNED'), lang=lang)}\n\n"
        f"{tr(lang, 'col_detail_copies', qty=qty)}\n"
        f"{tr(lang, 'col_detail_copy_id', uc_id=target_uc_id)}\n"
        f"{tr(lang, 'col_detail_status', st=st_text)}"
    )
    kb = card_detail_kb(
        owner_id=callback.from_user.id,
        user_card_id=target_uc_id,
        is_locked=is_locked,
        is_favorite=uc.is_favorite,
        char_rep_id=char_rep_id,
        card_id=card.id,
        is_showcased=bool(getattr(uc, "is_showcased", False)),
        lang=lang,
    )
    if callback.message and (getattr(card, "image_file_id", None) or (card.image and len(card.image) > 3)):
        try:
            await callback.message.delete()
            await send_card_message(callback.bot, callback.message.chat.id, card, text, kb)
            await callback.answer()
            return
        except Exception:
            pass
    await edit_or_answer(callback, text, reply_markup=kb)


@router.callback_query(CollectionCb.filter(F.action == "toggle_fav"))
async def cb_toggle_fav(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    lang = get_lang(db_user)
    async with get_session() as session:
        new_state = await CollectionService.toggle_favorite_for_card(
            session, db_user.id, callback_data.item_id
        )
        await session.commit()
    await callback.answer(tr(lang, "fav_added" if new_state else "fav_removed"))
    await cb_card_detail(callback, callback_data, db_user)


@router.callback_query(CollectionCb.filter(F.action == "toggle_show"))
async def cb_toggle_showcase(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    lang = get_lang(db_user)
    async with get_session() as session:
        uc = await session.get(UserCard, callback_data.item_id)
        if uc is None or uc.user_id != db_user.id:
            await callback.answer(tr(lang, "card_not_found"), show_alert=True)
            return
        uc.is_showcased = not bool(getattr(uc, "is_showcased", False))
        new_state = uc.is_showcased
        await session.commit()
    await callback.answer(tr(lang, "show_added" if new_state else "show_removed"))
    await cb_card_detail(callback, callback_data, db_user)


@router.callback_query(CollectionCb.filter(F.action == "about"))
async def cb_card_about(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    lang = get_lang(db_user)
    async with get_session() as session:
        uc = await session.get(UserCard, callback_data.item_id)
        if uc is None or uc.user_id != db_user.id:
            await callback.answer(tr(lang, "card_not_found"), show_alert=True)
            return
        card = await session.get(Card, uc.card_id)
    if card is None:
        await callback.answer(tr(lang, "card_not_found"), show_alert=True)
        return
    kb = back_home_kb(callback.from_user.id, "cards", lang)
    await edit_or_answer(callback, format_card_about(card, lang=lang), reply_markup=kb)


@router.callback_query(CollectionCb.filter(F.action == "list_prompt"))
async def cb_list_prompt(
    callback: CallbackQuery,
    callback_data: CollectionCb,
    state: FSMContext,
    db_user: User,
) -> None:
    lang = get_lang(db_user)
    async with get_session() as session:
        uc = await session.get(UserCard, callback_data.item_id)
        if uc is None or uc.user_id != db_user.id or uc.is_locked:
            await callback.answer("Bu kartani hozir sotuvga qo‘yib bo‘lmaydi.", show_alert=True)
            return
        card = await session.get(Card, uc.card_id)

    await state.set_state(SellCardStates.waiting_price)
    await state.update_data(user_card_id=uc.id, owner_tg_id=callback.from_user.id)
    await edit_or_answer(
        callback,
        f"🛒 <b>{esc(card.character_name)}</b>\n\n"
        f"💰 Value: {card.base_value:,} coins\n"
        f"Min/Max: {MIN_CARD_PRICE:,} — {MAX_CARD_PRICE:,} coins.\n"
        f"<i>/cancel</i>",
        reply_markup=back_home_kb(callback.from_user.id, back_target="cards", lang=lang),
    )


@router.message(SellCardStates.waiting_price, NotCommandFilter())
async def fsm_sell_price(message: Message, state: FSMContext, db_user: User) -> None:
    lang = get_lang(db_user)
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return

    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit():
        if is_private_chat(message):
            await message.answer("⚠️ Faqat musbat raqam kiriting / Positive integer only (/cancel)")
        return

    price = int(raw)
    user_card_id = int(data["user_card_id"])
    async with get_session() as session:
        res = await MarketService.list_card(session, db_user.id, user_card_id, price)
        if res.success:
            await session.commit()
        else:
            await session.rollback()

    await state.clear()
    if not res.success:
        await message.answer(f"❌ {res.message}", reply_markup=back_home_kb(message.from_user.id, "cards", lang))
        return

    await message.answer(
        f"✅ <b>{esc(res.card.character_name)}</b> → <b>{price:,} 🪙</b>!",
        reply_markup=back_home_kb(message.from_user.id, "market", lang),
    )
