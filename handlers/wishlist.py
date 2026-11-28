"""Istaklar ro‘yxati va Karta qo‘shish takliflari."""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.session import get_session
from handlers.common import V2UserStates
from keyboards.v2 import (
    suggest_media_kb,
    suggest_note_skip_kb,
    wishlist_kb,
    wishlist_top_kb,
)
from models.user import User
from services.auction_wishlist_service import AuctionWishlistService
from services.community_service import CommunityService
from utils.callback_data import SuggestCb, WishCb
from utils.constants import get_rarity_meta
from utils.formatting import esc, header
from utils.tg_helpers import NotCommandFilter, edit_or_answer

logger = logging.getLogger(__name__)
router = Router(name="wishlist")
WISH_PAGE_SIZE = 5


async def _show_wishlist(target, owner_id: int, db_user: User, page: int = 1) -> None:
    async with get_session() as session:
        cards, total = await AuctionWishlistService.get_user_wishlist_page(
            session, db_user.id, page=page, page_size=WISH_PAGE_SIZE
        )
    txt = (
        f"{header('💖 <b>ISTAKLAR RO‘YXATI</b>')}\n"
        f"📌 Ro‘yxatda: <b>{total}</b> ta karta\n\n"
        "Kerakli kartani qo‘shib qo‘ying: u guruhga tushsa, bozorga yoki auksionga chiqsa — darhol xabar beramiz!\n"
        "<i>(Sizda allaqachon bor kartalar Istaklarga qo‘shilmaydi.)</i>\n\n"
        "💡 <i>Yangi karta ko‘rmoqchimisiz? «➕ Karta qo‘shish taklifi» orqali adminga taklif yuboring!</i>"
    )
    await edit_or_answer(
        target,
        txt,
        reply_markup=wishlist_kb(owner_id, cards, page=page, total=total, page_size=WISH_PAGE_SIZE),
    )


@router.message(Command("wishlist"))
async def cmd_wishlist(message: Message, db_user: User) -> None:
    await _show_wishlist(message, message.from_user.id, db_user, page=1)


@router.callback_query(WishCb.filter(F.act == "v"))
async def cb_wishlist(callback: CallbackQuery, callback_data: WishCb, state: FSMContext, db_user: User) -> None:
    await state.clear()
    await _show_wishlist(callback, callback.from_user.id, db_user, page=max(1, callback_data.p))


@router.callback_query(WishCb.filter(F.act == "add"))
async def cb_wishlist_add(callback: CallbackQuery, callback_data: WishCb, db_user: User) -> None:
    async with get_session() as session:
        _added, msg = await AuctionWishlistService.toggle_wishlist(session, db_user.id, callback_data.cid)
        await session.commit()
    await callback.answer(msg[:190], show_alert=True)


@router.callback_query(WishCb.filter(F.act == "notif"))
async def cb_wishlist_notif(callback: CallbackQuery, callback_data: WishCb, db_user: User) -> None:
    async with get_session() as session:
        _on, msg = await AuctionWishlistService.toggle_wishlist_notify(session, db_user.id, callback_data.cid)
        await session.commit()
    await callback.answer(msg[:190], show_alert=True)


@router.callback_query(WishCb.filter(F.act == "del"))
async def cb_wishlist_del(callback: CallbackQuery, callback_data: WishCb, db_user: User) -> None:
    async with get_session() as session:
        await AuctionWishlistService.toggle_wishlist(session, db_user.id, callback_data.cid)
        await session.commit()
    await _show_wishlist(callback, callback.from_user.id, db_user, page=max(1, callback_data.p))


async def _render_wishlist_top(target, owner_id: int, page: int = 1) -> None:
    page = max(1, page)
    async with get_session() as session:
        top, total = await AuctionWishlistService.get_most_wanted_page(
            session, page=page, page_size=WISH_PAGE_SIZE
        )
    lines = [header("🔥 <b>ENG KO‘P ISTALGAN KARTALAR</b>")]
    start_idx = (page - 1) * WISH_PAGE_SIZE + 1
    for idx, (c, cnt) in enumerate(top, start=start_idx):
        meta = get_rarity_meta(c.rarity)
        lines.append(f"{idx}. {meta.emoji} <b>{esc(c.character_name)}</b> ({esc(c.anime_name)}) — 💖 {cnt} ta")
    if not top:
        lines.append("📭 Hozircha bo‘sh.")
    await edit_or_answer(
        target,
        "\n".join(lines),
        reply_markup=wishlist_top_kb(owner_id, top, page=page, total=total, page_size=WISH_PAGE_SIZE),
    )


@router.callback_query(WishCb.filter(F.act == "top"))
async def cb_wishlist_top(callback: CallbackQuery, callback_data: WishCb) -> None:
    await _render_wishlist_top(callback, callback.from_user.id, page=max(1, callback_data.p))


# ------------------------------------------------------------------
# KARTA QO‘SHISH TAKLIFI (CardSuggestion)
# ------------------------------------------------------------------
async def _finalize_suggestion(bot, db_user: User, owner_tg_id: int, media_type: str, anime: str, char: str, note: str | None) -> str:
    async with get_session() as session:
        sug = await AuctionWishlistService.create_suggestion(
            session,
            user_id=db_user.id,
            media_type=media_type,
            anime_name=anime,
            character_name=char,
            note=note,
        )
        admin_ids = await CommunityService.list_admin_tg_ids(session, section="cards")
        await session.commit()

    note_txt = f"\n📝 Izoh: {esc(sug.note)}" if sug.note else ""
    admin_msg = (
        f"💡 <b>YANGI KARTA TAKLIFI (#{sug.id})</b>\n"
        f"👤 Kimdan: <b>{esc(db_user.display_name)}</b> (<code>{owner_tg_id}</code>)\n"
        f"🎬 Turi: <b>{esc(sug.media_type)}</b>\n"
        f"📺 Anime/Nom: <b>{esc(sug.anime_name)}</b>\n"
        f"🦸 Personaj: <b>{esc(sug.character_name)}</b>{note_txt}"
    )
    for aid in admin_ids:
        try:
            await bot.send_message(aid, admin_msg)
        except Exception:
            pass
    return (
        f"✅ <b>Karta taklifingiz adminga yuborildi (#{sug.id})!</b>\n"
        f"🎬 {esc(sug.media_type)} · {esc(sug.anime_name)}\n"
        f"🦸 {esc(sug.character_name)}\n\n"
        "Ko‘rib chiqilgach xabar beramiz."
    )


@router.callback_query(SuggestCb.filter(F.act == "new"))
async def cb_suggest_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(owner_tg_id=callback.from_user.id)
    await edit_or_answer(
        callback,
        f"{header('💡 <b>KARTA QO‘SHISH TAKLIFI (1/4)</b>')}\n"
        "Qaysi turdagi karta qo‘shilishini xohlaysiz? Media turini tanlang:",
        reply_markup=suggest_media_kb(callback.from_user.id),
    )


@router.callback_query(SuggestCb.filter(F.act == "med"))
async def cb_suggest_media(callback: CallbackQuery, callback_data: SuggestCb, state: FSMContext) -> None:
    media_type = (callback_data.ext or "Anime").strip()
    await state.set_state(V2UserStates.waiting_suggest_anime)
    await state.update_data(owner_tg_id=callback.from_user.id, sug_media=media_type)
    await edit_or_answer(
        callback,
        f"💡 <b>KARTA TAKLIFI (2/4)</b>\n"
        f"🎬 Media turi: <b>{esc(media_type)}</b>\n\n"
        "Endi anime, manga, manhwa yoki kino <b>nomini</b> yozing:\n"
        "<i>Bekor qilish: /cancel</i>",
    )


@router.message(V2UserStates.waiting_suggest_anime, NotCommandFilter())
async def fsm_suggest_anime(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    anime = (message.text or "").strip()
    if len(anime) < 2:
        await message.answer("⚠️ Kamida 2 ta belgi yozing.")
        return
    await state.update_data(sug_anime=anime[:160])
    await state.set_state(V2UserStates.waiting_suggest_char)
    await message.answer("🦸 Endi <b>personaj ismini</b> yozing:")


@router.message(V2UserStates.waiting_suggest_char, NotCommandFilter())
async def fsm_suggest_char(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    char = (message.text or "").strip()
    if len(char) < 2:
        await message.answer("⚠️ Kamida 2 ta belgi yozing.")
        return
    await state.update_data(sug_char=char[:160])
    await state.set_state(V2UserStates.waiting_suggest_note)
    await message.answer(
        "📝 Qo‘shimcha izoh yozishingiz mumkin (masalan qaysi forma/ko‘rinishi) yoki izohsiz yuboring:",
        reply_markup=suggest_note_skip_kb(message.from_user.id),
    )


@router.message(V2UserStates.waiting_suggest_note, NotCommandFilter())
async def fsm_suggest_note(message: Message, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    note = (message.text or "").strip()[:500] or None
    media_type = data.get("sug_media", "Anime")
    anime = data.get("sug_anime", "")
    char = data.get("sug_char", "")
    await state.clear()
    txt = await _finalize_suggestion(message.bot, db_user, message.from_user.id, media_type, anime, char, note)
    await message.answer(txt)
    await _show_wishlist(message, message.from_user.id, db_user, page=1)


@router.callback_query(SuggestCb.filter(F.act == "skip"))
async def cb_suggest_skip_note(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    anime = data.get("sug_anime", "")
    char = data.get("sug_char", "")
    if not anime or not char:
        await state.clear()
        await _show_wishlist(callback, callback.from_user.id, db_user, page=1)
        return
    media_type = data.get("sug_media", "Anime")
    await state.clear()
    txt = await _finalize_suggestion(callback.bot, db_user, callback.from_user.id, media_type, anime, char, None)
    await edit_or_answer(callback, txt)
    await _show_wishlist(callback, callback.from_user.id, db_user, page=1)
