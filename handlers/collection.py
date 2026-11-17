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
from utils.tg_helpers import NotCommandFilter, edit_or_answer, is_private_chat

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
    async with get_session() as session:
        anime_counts, rarity_counts, favorites = await CollectionService.get_summary(
            session, db_user.id
        )
    text = format_collection_summary(db_user, anime_counts, rarity_counts, favorites)
    await edit_or_answer(target, text, reply_markup=collection_summary_kb(owner_id))


@router.message(Command("cards", "collection"))
async def cmd_collection(message: Message, db_user: User) -> None:
    await _show_summary(message, message.from_user.id, db_user)


@router.callback_query(NavCb.filter(F.target == "cards"))
@router.callback_query(CollectionCb.filter(F.action == "view"))
async def cb_collection_summary(callback: CallbackQuery, db_user: User) -> None:
    await _show_summary(callback, callback.from_user.id, db_user)


@router.callback_query(CollectionCb.filter(F.action == "v2_list"))
async def cb_v2_collection_list(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
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
    lines = [f"🗂 <b>KARTALARIM ({filter_tab.upper()})</b> — Jami: <b>{total}</b> ta (20/sahifa)\n"]
    if not rows:
        lines.append("📭 Bu bo‘limda kartalar topilmadi.")
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
            callback.from_user.id, rows, filter_tab, sort_by, page, total, page_size=PAGE_SIZE
        ),
    )


# ---------------------------------------------------------------------------
# 1-daraja: ANIME BO'LIMLARI (bir xil nomli anime = bitta bo'lim)
# ---------------------------------------------------------------------------
@router.callback_query(CollectionCb.filter(F.action.in_({"anime_list", "by_anime"})))
async def cb_anime_list(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
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
            "📭 Sizda hali kartalar yo‘q. Guruhdagi dropni kuting yoki marketdan sotib oling!",
            reply_markup=back_home_kb(callback.from_user.id, "cards"),
        )
        return

    lines = [f"📺 <b>ANIME BO‘LIMLARI</b> (Jami: {total} ta anime)\n"]
    for sec in sections:
        lines.append(f"• <b>{esc(sec.anime_name)}</b> — {sec.characters} personaj · {sec.copies} ta karta")
    await edit_or_answer(
        callback,
        "\n".join(lines),
        reply_markup=anime_sections_kb(callback.from_user.id, sections, page, total, ANIME_PAGE_SIZE),
    )


# ---------------------------------------------------------------------------
# 2-daraja: anime ichidagi PERSONAJLAR (bir personajning hamma kartalari birlashgan)
# ---------------------------------------------------------------------------
@router.callback_query(CollectionCb.filter(F.action == "anime_open"))
async def cb_anime_open(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
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
        await callback.answer("Bu bo‘limda kartalaringiz qolmagan.", show_alert=True)
        await cb_anime_list(callback, CollectionCb(owner_id=callback.from_user.id, action="anime_list", page=anime_page), db_user)
        return

    anime_name, chars, total = info
    lines = [f"📺 <b>{esc(anime_name)}</b> — personajlar (Jami: {total})\n"]
    for ch in chars:
        fav = ("❤️ " if ch.is_favorite else "") + ("🎯 " if ch.has_event_card else "")
        note = f" ({ch.variants} xil karta)" if ch.variants > 1 else ""
        lines.append(f"{fav}🎴 <b>{esc(ch.character_name)}</b> × {ch.copies}{note}")
    await edit_or_answer(
        callback,
        "\n".join(lines),
        reply_markup=anime_characters_kb(
            callback.from_user.id, callback_data.item_id, anime_page, chars, page, total, CHAR_PAGE_SIZE
        ),
    )


# ---------------------------------------------------------------------------
# 3-daraja: personajning kartalari (turli rarity/rasm variantlari)
# ---------------------------------------------------------------------------
@router.callback_query(CollectionCb.filter(F.action == "char_open"))
async def cb_char_open(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    char_page, anime_page = _ints(callback_data.filter_val, 2)
    async with get_session() as session:
        info = await CollectionService.get_character_variants(session, db_user.id, callback_data.item_id)

    if info is None:
        await callback.answer("Bu personaj kartalari sizda qolmagan.", show_alert=True)
        await cb_anime_list(callback, CollectionCb(owner_id=callback.from_user.id, action="anime_list", page=anime_page), db_user)
        return

    character_name, anime_name, variants, anime_rep_id = info
    total_copies = sum(v.quantity for v in variants)
    lines = [
        f"🎴 <b>{esc(character_name)}</b>",
        f"📺 {esc(anime_name)}",
        "",
        f"📦 Jami: <b>{total_copies}</b> ta nusxa · {len(variants)} xil karta\n",
    ]
    for v in variants:
        meta = get_rarity_meta(v.card.rarity)
        tag = "  🎯 <i>EVENT · cheklangan nusxa</i>" if v.card.is_event_card else ""
        lines.append(
            f"{event_tag(v.card)}{meta.emoji} {esc(v.card.rarity)} · <code>{esc(v.card.card_code)}</code> × {v.quantity} "
            f"— 💰 {fmt_coins(v.card.base_value)}{tag}"
        )
    await edit_or_answer(
        callback,
        "\n".join(lines),
        reply_markup=character_variants_kb(
            callback.from_user.id, variants, anime_rep_id, char_page, anime_page, callback_data.item_id
        ),
    )


# ---------------------------------------------------------------------------
# Rarity bo'yicha tekis ro'yxat
# ---------------------------------------------------------------------------
@router.callback_query(CollectionCb.filter(F.action == "by_rarity"))
async def cb_by_rarity(callback: CallbackQuery) -> None:
    await edit_or_answer(
        callback,
        "⭐ <b>Rarity bo‘yicha filtrlash:</b>",
        reply_markup=collection_rarity_filter_kb(callback.from_user.id),
    )


@router.callback_query(CollectionCb.filter(F.action == "char_list"))
async def cb_char_list(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
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
        text = "📭 Ushbu bo‘limda kartalar topilmadi."
    else:
        lines = [f"🎴 <b>KOLLEKSIYA RO‘YXATI</b> (Jami: {total} xil karta)\n"]
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
        ),
    )


# ---------------------------------------------------------------------------
# Karta tafsiloti
# ---------------------------------------------------------------------------
@router.callback_query(CollectionCb.filter(F.action == "card_detail"))
async def cb_card_detail(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    async with get_session() as session:
        uc = await session.get(UserCard, callback_data.item_id)
        if uc is None or uc.user_id != db_user.id:
            await callback.answer("Karta topilmadi.", show_alert=True)
            return
        card = await session.get(Card, uc.card_id)
        unlocked_copy = await CollectionService.get_unlocked_copy(session, db_user.id, card.id)
        qty = await CollectionService.count_copies(session, db_user.id, card.id)
        reps = await CollectionService.get_card_group_reps(session, card.id)

    char_rep_id = reps[1] if reps else card.id
    target_uc_id = unlocked_copy.id if unlocked_copy else uc.id
    is_locked = unlocked_copy is None
    text = (
        f"{format_card_block(card, serial_number=getattr(uc, 'serial_number', None), state_label=getattr(uc, 'card_state', 'OWNED'))}\n\n"
        f"📦 <b>Sizdagi nusxalar soni:</b> {qty} ta\n"
        f"🆔 <b>Nusxa ID:</b> <code>{target_uc_id}</code>\n"
        f"📌 <b>Holati:</b> {'🔒 Marketda/Transferda band' if is_locked else '🟢 Sotish/Transferga tayyor'}"
    )
    kb = card_detail_kb(
        owner_id=callback.from_user.id,
        user_card_id=target_uc_id,
        is_locked=is_locked,
        is_favorite=uc.is_favorite,
        char_rep_id=char_rep_id,
        card_id=card.id,
        is_showcased=bool(getattr(uc, "is_showcased", False)),
    )
    if callback.message and card.image and (card.image.startswith("http") or len(card.image) > 20):
        try:
            await callback.message.delete()
            await callback.message.answer_photo(photo=card.image, caption=text, reply_markup=kb)
            await callback.answer()
            return
        except Exception:
            pass
    await edit_or_answer(callback, text, reply_markup=kb)


@router.callback_query(CollectionCb.filter(F.action == "toggle_fav"))
async def cb_toggle_fav(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    async with get_session() as session:
        new_state = await CollectionService.toggle_favorite_for_card(
            session, db_user.id, callback_data.item_id
        )
        await session.commit()
    await callback.answer("❤️ Sevimlilarga qo‘shildi!" if new_state else "💔 Sevimlilardan olindi.")
    await cb_card_detail(callback, callback_data, db_user)


@router.callback_query(CollectionCb.filter(F.action == "toggle_show"))
async def cb_toggle_showcase(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    async with get_session() as session:
        uc = await session.get(UserCard, callback_data.item_id)
        if uc is None or uc.user_id != db_user.id:
            await callback.answer("Karta topilmadi.", show_alert=True)
            return
        uc.is_showcased = not bool(getattr(uc, "is_showcased", False))
        new_state = uc.is_showcased
        await session.commit()
    await callback.answer("🖼 Showcasega qo‘shildi!" if new_state else "🖼 Showcasedan olindi.")
    await cb_card_detail(callback, callback_data, db_user)


@router.callback_query(CollectionCb.filter(F.action == "about"))
async def cb_card_about(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    async with get_session() as session:
        uc = await session.get(UserCard, callback_data.item_id)
        if uc is None or uc.user_id != db_user.id:
            await callback.answer("Karta topilmadi.", show_alert=True)
            return
        card = await session.get(Card, uc.card_id)
    if card is None:
        await callback.answer("Karta topilmadi.", show_alert=True)
        return
    kb = back_home_kb(callback.from_user.id, "cards")
    await edit_or_answer(callback, format_card_about(card), reply_markup=kb)


# ---------------------------------------------------------------------------
# Marketga qo'yish (narx so'rash)
# ---------------------------------------------------------------------------
@router.callback_query(CollectionCb.filter(F.action == "list_prompt"))
async def cb_list_prompt(
    callback: CallbackQuery,
    callback_data: CollectionCb,
    state: FSMContext,
    db_user: User,
) -> None:
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
        f"🛒 <b>{esc(card.character_name)}</b> kartasini sotuvga qo‘yish\n\n"
        f"💰 Tavsiya etilgan qiymat: {card.base_value:,} coins\n"
        f"Narxni kiriting ({MIN_CARD_PRICE:,} — {MAX_CARD_PRICE:,} coins).\n"
        f"<i>Bekor qilish: /cancel</i>",
        reply_markup=back_home_kb(callback.from_user.id, back_target="cards"),
    )


@router.message(SellCardStates.waiting_price, NotCommandFilter())
async def fsm_sell_price(message: Message, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return

    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit():
        # Guruhda oddiy suhbatga javob bermaymiz (spam bo'lmasin); shaxsiy chatda esa eslatamiz
        if is_private_chat(message):
            await message.answer("⚠️ Iltimos, faqat musbat raqam (coins) kiriting. Bekor qilish: /cancel")
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
        await message.answer(f"❌ {res.message}", reply_markup=back_home_kb(message.from_user.id, "cards"))
        return

    await message.answer(
        f"✅ <b>{esc(res.card.character_name)}</b> marketga <b>{price:,} 🪙</b> narxda qo‘yildi!",
        reply_markup=back_home_kb(message.from_user.id, "market"),
    )
