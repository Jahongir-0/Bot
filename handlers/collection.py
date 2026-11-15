from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from database.session import get_session
from keyboards.collection import (
    card_detail_kb,
    collection_anime_filter_kb,
    collection_list_kb,
    collection_rarity_filter_kb,
    collection_summary_kb,
)
from keyboards.main_menu import back_home_kb
from models.card import Card
from models.user import User
from models.user_card import UserCard
from services.collection_service import CollectionService
from services.market_service import MarketService
from utils.callback_data import CollectionCb, NavCb
from utils.constants import MAX_CARD_PRICE, MIN_CARD_PRICE
from utils.formatting import esc, format_card_block, format_collection_summary
from utils.tg_helpers import edit_or_answer

router = Router(name="collection")


class SellCardStates(StatesGroup):
    waiting_price = State()


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


@router.callback_query(CollectionCb.filter(F.action == "by_anime"))
async def cb_by_anime(callback: CallbackQuery, db_user: User) -> None:
    async with get_session() as session:
        animes = await CollectionService.get_user_anime_names(session, db_user.id)
    text = "📺 <b>Anime bo‘yicha filtrlash:</b>" if animes else "📭 Sizda hali kartalar yo‘q."
    await edit_or_answer(
        callback,
        text,
        reply_markup=collection_anime_filter_kb(callback.from_user.id, animes),
    )


@router.callback_query(CollectionCb.filter(F.action == "by_rarity"))
async def cb_by_rarity(callback: CallbackQuery) -> None:
    await edit_or_answer(
        callback,
        "⭐ <b>Rarity bo‘yicha filtrlash:</b>",
        reply_markup=collection_rarity_filter_kb(callback.from_user.id),
    )


@router.callback_query(CollectionCb.filter(F.action == "char_list"))
async def cb_char_list(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    anime_filter = None
    rarity_filter = None
    if callback_data.filter_val.startswith("a:"):
        anime_filter = f"id:{callback_data.filter_val[2:]}"
    elif callback_data.filter_val.startswith("anime:"):
        anime_filter = callback_data.filter_val[6:]
    elif callback_data.filter_val.startswith("rarity:"):
        rarity_filter = callback_data.filter_val[7:]

    async with get_session() as session:
        stacks, total = await CollectionService.get_character_stacks(
            session=session,
            user_id=db_user.id,
            anime_filter=anime_filter,
            rarity_filter=rarity_filter,
            page=callback_data.page,
            page_size=6,
        )

    if not stacks:
        text = "📭 Ushbu bo‘limda kartalar topilmadi."
    else:
        lines = [f"🎴 <b>KOLLEKSIYA RO‘YXATI</b> (Jami: {total} xil qahramon)\n"]
        for st in stacks:
            fav = "❤️ " if st.is_favorite else ""
            lines.append(f"{fav}• <b>{esc(st.card.character_name)}</b> × {st.quantity} ({esc(st.card.anime_name)})")
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


@router.callback_query(CollectionCb.filter(F.action == "card_detail"))
async def cb_card_detail(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    async with get_session() as session:
        uc = await session.get(UserCard, callback_data.item_id)
        if uc is None or uc.user_id != db_user.id:
            await callback.answer("Karta topilmadi.", show_alert=True)
            return
        card = await session.get(Card, uc.card_id)
        unlocked_copy = await CollectionService.get_unlocked_copy(session, db_user.id, card.id)
        stacks, _ = await CollectionService.get_character_stacks(session, db_user.id, page=1, page_size=500)
        qty = next((s.quantity for s in stacks if s.card.id == card.id), 1)

    target_uc_id = unlocked_copy.id if unlocked_copy else uc.id
    is_locked = unlocked_copy is None
    text = (
        f"{format_card_block(card)}\n\n"
        f"📦 <b>Sizdagi nusxalar soni:</b> {qty} ta\n"
        f"🆔 <b>Nusxa ID:</b> <code>{target_uc_id}</code>\n"
        f"📌 <b>Holati:</b> {'🔒 Marketda/Transferda band' if is_locked else '🟢 Sotish/Transferga tayyor'}"
    )
    kb = card_detail_kb(
        owner_id=callback.from_user.id,
        user_card_id=target_uc_id,
        is_locked=is_locked,
        is_favorite=uc.is_favorite,
        filter_val=callback_data.filter_val,
        page=callback_data.page,
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
        f"Narxni kiriting ({MIN_CARD_PRICE:,} — {MAX_CARD_PRICE:,} coins):",
        reply_markup=back_home_kb(callback.from_user.id, back_target="cards"),
    )


@router.message(SellCardStates.waiting_price)
async def fsm_sell_price(message: Message, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return

    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit():
        await message.answer("⚠️ Iltimos, faqat musbat raqam (coins) kiriting.")
        return

    price = int(raw)
    user_card_id = int(data["user_card_id"])
    async with get_session() as session:
        res = await MarketService.list_card(session, db_user.id, user_card_id, price)
        await session.commit()

    await state.clear()
    if not res.success:
        await message.answer(f"❌ {res.message}", reply_markup=back_home_kb(message.from_user.id, "cards"))
        return

    await message.answer(
        f"✅ <b>{esc(res.card.character_name)}</b> marketga <b>{price:,} 🪙</b> narxda qo‘yildi!",
        reply_markup=back_home_kb(message.from_user.id, "market"),
    )
