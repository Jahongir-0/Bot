from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from database.session import get_session
from keyboards.main_menu import back_home_kb
from keyboards.market import (
    search_inspect_kb,
    search_menu_kb,
    search_rarity_kb,
    search_results_kb,
)
from models.user import User
from services.search_service import SearchService
from utils.callback_data import NavCb, SearchCb
from utils.constants import PAGE_SIZE, get_rarity_meta
from utils.formatting import esc, fmt_coins, format_card_block
from utils.tg_helpers import edit_or_answer

router = Router(name="search")


class SearchStates(StatesGroup):
    waiting_query = State()


async def _render_results(
    target,
    owner_id: int,
    mode: str,
    query: str,
    page: int,
) -> None:
    async with get_session() as session:
        items, total = await SearchService.search_global_cards(
            session=session,
            mode=mode,
            query=query,
            page=page,
            page_size=PAGE_SIZE,
        )

    if not items:
        text = f"🔍 <b>GLOBAL QIDIRUV</b>\n\nSo‘rov: <code>{esc(query or mode)}</code>\n📭 Hech qanday karta topilmadi."
    else:
        lines = [f"🔍 <b>GLOBAL QIDIRUV NATIJALARI</b> (Jami: {total})\n"]
        for item in items:
            meta = get_rarity_meta(item.card.rarity)
            status_line = (
                f"🟢 Sotuvda — <b>{fmt_coins(item.active_listing.price)} 🪙</b>"
                if item.active_listing is not None
                else "🔴 Sotuvda emas"
            )
            lines.append(
                f"🎴 <b>{esc(item.card.character_name)}</b>\n"
                f"📺 {esc(item.card.anime_name)} · {meta.emoji} {esc(item.card.rarity)}\n"
                f"👤 Owner: {esc(item.owner.display_name)} | {status_line}\n"
            )
        text = "\n".join(lines)

    await edit_or_answer(
        target,
        text,
        reply_markup=search_results_kb(
            owner_id=owner_id,
            items=items,
            mode=mode,
            query=query,
            page=page,
            total=total,
            page_size=PAGE_SIZE,
        ),
    )


@router.message(Command("search"))
async def cmd_search(message: Message, state: FSMContext) -> None:
    await state.clear()
    parts = message.text.split(maxsplit=1) if message.text else []
    if len(parts) == 2 and parts[1].strip():
        await _render_results(message, message.from_user.id, "character", parts[1].strip(), 1)
        return
    await message.answer(
        "🔍 <b>GLOBAL KARTA QIDIRUVI</b>\n\n"
        "Barcha guruhlardagi barcha foydalanuvchilar kartalaridan qidiring:",
        reply_markup=search_menu_kb(message.from_user.id),
    )


@router.callback_query(NavCb.filter(F.target == "search"))
@router.callback_query(SearchCb.filter(F.action == "menu"))
async def cb_search_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_or_answer(
        callback,
        "🔍 <b>GLOBAL KARTA QIDIRUVI</b>\n\nQidiruv turini tanlang:",
        reply_markup=search_menu_kb(callback.from_user.id),
    )


@router.callback_query(SearchCb.filter(F.action == "mode"))
async def cb_search_mode(
    callback: CallbackQuery,
    callback_data: SearchCb,
    state: FSMContext,
) -> None:
    if callback_data.mode == "rarity":
        await edit_or_answer(
            callback,
            "⭐ <b>Rarity bo‘yicha qidirish:</b>",
            reply_markup=search_rarity_kb(callback.from_user.id),
        )
        return

    prompts = {
        "character": "🔍 Qahramon ismini yozing (masalan: <code>Gojo</code>):",
        "anime": "📺 Anime nomini yozing (masalan: <code>Jujutsu Kaisen</code>):",
        "card_id": "🎴 Card ID yoki nusxa ID yozing (masalan: <code>CRD-0001</code> yoki <code>12</code>):",
    }
    await state.set_state(SearchStates.waiting_query)
    await state.update_data(search_mode=callback_data.mode, owner_tg_id=callback.from_user.id)
    await edit_or_answer(
        callback,
        prompts.get(callback_data.mode, "Qidiruv so‘zini yozing:"),
        reply_markup=back_home_kb(callback.from_user.id, "search"),
    )


@router.message(SearchStates.waiting_query)
async def fsm_search_query(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    query = (message.text or "").strip()
    mode = data.get("search_mode", "character")
    await state.set_state(None)
    await state.update_data(last_search_query=query, last_search_mode=mode)
    await _render_results(message, message.from_user.id, mode, query, 1)


@router.callback_query(SearchCb.filter(F.action.in_({"results", "random"})))
async def cb_search_results(
    callback: CallbackQuery,
    callback_data: SearchCb,
    state: FSMContext,
) -> None:
    query = callback_data.query
    if callback_data.mode != "random" and callback_data.mode != "rarity":
        data = await state.get_data()
        saved_q = data.get("last_search_query", "")
        if saved_q and saved_q.startswith(query):
            query = saved_q
    await _render_results(
        callback,
        owner_id=callback.from_user.id,
        mode=callback_data.mode,
        query=query,
        page=callback_data.page,
    )


@router.callback_query(SearchCb.filter(F.action == "inspect"))
async def cb_search_inspect(
    callback: CallbackQuery,
    callback_data: SearchCb,
    db_user: User,
) -> None:
    async with get_session() as session:
        item = await SearchService.inspect_user_card(session, callback_data.user_card_id)

    if item is None:
        await callback.answer("Karta topilmadi.", show_alert=True)
        return

    status = (
        f"🟢 Sotuvda — <b>{fmt_coins(item.active_listing.price)} 🪙</b>"
        if item.active_listing is not None
        else "🔒 Sotuvda emas"
    )
    text = (
        f"{format_card_block(item.card)}\n\n"
        f"👤 <b>Owner:</b> {esc(item.owner.display_name)}\n"
        f"📌 <b>Status:</b> {status}\n"
        f"🆔 <b>Nusxa ID:</b> <code>{item.user_card.id}</code>"
    )
    kb = search_inspect_kb(
        owner_id=callback.from_user.id,
        item=item,
        is_self_owner=(item.owner.id == db_user.id),
        mode=callback_data.mode,
        query=callback_data.query,
        page=callback_data.page,
    )
    await edit_or_answer(callback, text, reply_markup=kb)
