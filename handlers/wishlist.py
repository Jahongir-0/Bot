"""Istaklar ro‘yxati."""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database.session import get_session
from keyboards.main_menu import back_home_kb
from keyboards.v2 import wishlist_kb
from models.user import User
from services.auction_wishlist_service import AuctionWishlistService
from utils.callback_data import WishCb
from utils.constants import get_rarity_meta
from utils.formatting import esc, header
from utils.tg_helpers import edit_or_answer

logger = logging.getLogger(__name__)
router = Router(name="wishlist")


# ------------------------------------------------------------------
# WISHLIST (/wishlist)
# ------------------------------------------------------------------
async def _show_wishlist(target, owner_id: int, db_user: User) -> None:
    async with get_session() as session:
        cards = await AuctionWishlistService.get_user_wishlist(session, db_user.id)
    txt = (
        f"{header('💖 <b>ISTAKLAR RO‘YXATI</b>')}\n"
        f"📌 Ro‘yxatda: <b>{len(cards)}</b> ta karta\n\n"
        "Kerakli kartani qo‘shib qo‘ying: u guruhga tushsa, bozorga yoki auksionga chiqsa — sizga darhol xabar beramiz!\n\n"
        "💡 <i>Karta qo‘shish: 🔍 Qidiruvdan kartani oching va «💖 Istaklarga» tugmasini bosing.</i>"
    )
    await edit_or_answer(target, txt, reply_markup=wishlist_kb(owner_id, cards))


@router.message(Command("wishlist"))
async def cmd_wishlist(message: Message, db_user: User) -> None:
    await _show_wishlist(message, message.from_user.id, db_user)


@router.callback_query(WishCb.filter(F.act == "v"))
async def cb_wishlist(callback: CallbackQuery, db_user: User) -> None:
    await _show_wishlist(callback, callback.from_user.id, db_user)


@router.callback_query(WishCb.filter(F.act == "add"))
async def cb_wishlist_add(callback: CallbackQuery, callback_data: WishCb, db_user: User) -> None:
    async with get_session() as session:
        _added, msg = await AuctionWishlistService.toggle_wishlist(session, db_user.id, callback_data.cid)
        await session.commit()
    await callback.answer(msg[:190], show_alert=True)


@router.callback_query(WishCb.filter(F.act == "del"))
async def cb_wishlist_del(callback: CallbackQuery, callback_data: WishCb, db_user: User) -> None:
    async with get_session() as session:
        await AuctionWishlistService.toggle_wishlist(session, db_user.id, callback_data.cid)
        await session.commit()
    await _show_wishlist(callback, callback.from_user.id, db_user)


@router.callback_query(WishCb.filter(F.act == "top"))
async def cb_wishlist_top(callback: CallbackQuery) -> None:
    async with get_session() as session:
        top = await AuctionWishlistService.get_most_wanted(session, limit=10)
    lines = [header("🔥 <b>ENG KO‘P ISTALGAN KARTALAR</b>")]
    for idx, (c, cnt) in enumerate(top, start=1):
        meta = get_rarity_meta(c.rarity)
        lines.append(f"{idx}. {meta.emoji} <b>{esc(c.character_name)}</b> ({esc(c.anime_name)}) — 💖 {cnt} ta")
    if not top:
        lines.append("📭 Hozircha bo‘sh.")
    await edit_or_answer(callback, "\n".join(lines), reply_markup=back_home_kb(callback.from_user.id, "home"))
