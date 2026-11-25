"""Auksion: ro‘yxat, taklif berish va yangi auksion yaratish."""

import logging
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from database.session import get_session
from handlers.common import LINE_SEP, V2UserStates
from keyboards.v2 import auction_detail_kb, auctions_list_kb, user_auction_dur_kb, user_auction_pick_card_kb
from models.card import Card
from models.user import User
from models.user_card import UserCard
from services.auction_wishlist_service import AuctionWishlistService
from utils.callback_data import AucCb
from utils.constants import MAX_CARD_PRICE, MIN_CARD_PRICE, get_rarity_meta
from utils.formatting import esc, fmt_coins, format_card_block, header
from utils.tg_helpers import NotCommandFilter, edit_or_answer, send_card_message
from utils.timeutils import fmt_tashkent

logger = logging.getLogger(__name__)
router = Router(name="auction")


# ------------------------------------------------------------------
# AUCTION (/auction)
# ------------------------------------------------------------------
async def _show_auctions(
    target,
    owner_id: int,
    page: int = 1,
    my_only: bool = False,
    db_user: Optional[User] = None,
) -> None:
    page = max(1, int(page))
    async with get_session() as session:
        if my_only and db_user is not None:
            items, total = await AuctionWishlistService.list_user_created_auctions(session, db_user.id, page=page, page_size=10)
        else:
            items, total = await AuctionWishlistService.list_active_auctions(session, page=page, page_size=10)
    title_hdr = "📦 <b>MENING AUKSIONLARIM</b>" if my_only else "🔨 <b>AUKSION</b>"
    if not items:
        empty_msg = (
            f"{header(title_hdr)}\n📭 Sizda hozircha faol auksion yo‘q.\n\n"
            "💡 «➕ Auksionga qo‘yish» tugmasi orqali o‘z kartangizni kim oshdi savdosiga qo‘yishingiz mumkin."
            if my_only
            else f"{header(title_hdr)}\n📭 Hozircha faol auksion yo‘q.\n\n💡 Istasangiz «➕ Auksionga qo‘yish» orqali birinchi bo‘lib kartangizni qo‘ying!"
        )
        await edit_or_answer(
            target,
            empty_msg,
            reply_markup=auctions_list_kb(owner_id, [], page=1, total=0, my_only=my_only),
        )
        return
    lines = [header(title_hdr), f"🟢 Faol auksionlar: <b>{total}</b> ta\n"]
    for auc, card in items:
        meta = get_rarity_meta(card.rarity)
        tag = " 👤" if auc.seller_user_id else ""
        lines.append(f"{meta.emoji} <b>{esc(card.character_name)}</b>{tag}")
        lines.append(f"      💰 {fmt_coins(auc.current_price)} 🪙 · 📈 {auc.bid_count} stavka")
    lines.append("\n👇 Auksionni tanlang:")
    await edit_or_answer(
        target,
        "\n".join(lines),
        reply_markup=auctions_list_kb(owner_id, items, page=page, total=total, my_only=my_only),
    )


@router.message(Command("auction"))
async def cmd_auction(message: Message) -> None:
    await _show_auctions(message, message.from_user.id)


@router.callback_query(AucCb.filter(F.act == "v"))
async def cb_auction_list(callback: CallbackQuery, callback_data: AucCb, state: FSMContext) -> None:
    await state.clear()
    await _show_auctions(callback, callback.from_user.id, page=max(1, callback_data.p))
    await callback.answer()


@router.callback_query(AucCb.filter(F.act == "mine"))
async def cb_auction_mine(callback: CallbackQuery, callback_data: AucCb, db_user: User) -> None:
    await _show_auctions(callback, callback.from_user.id, page=max(1, callback_data.p), my_only=True, db_user=db_user)
    await callback.answer()


@router.callback_query(AucCb.filter(F.act == "new"))
async def cb_auction_new(callback: CallbackQuery, callback_data: AucCb, db_user: User) -> None:
    page = max(1, callback_data.p)
    async with get_session() as session:
        rows, total = await AuctionWishlistService.list_user_sellable_cards(session, db_user.id, page=page, page_size=8)
    if not rows:
        await callback.answer("📭 Auksionga qo‘yish uchun bo‘sh kartangiz yo‘q (qulflanganlar chiqmaydi).", show_alert=True)
        return
    await edit_or_answer(
        callback,
        (
            f"{header('🔨 <b>AUKSIONGA KARTA QO‘YISH</b>')}\n"
            f"📦 Bo‘sh kartalar: <b>{total}</b> ta\n\n"
            "👇 Qaysi kartangizni auksionga qo‘ymoqchisiz?"
        ),
        reply_markup=user_auction_pick_card_kb(callback.from_user.id, rows, page=page, total=total),
    )
    await callback.answer()


@router.callback_query(AucCb.filter(F.act == "uc_new"))
async def cb_auction_pick_uc(callback: CallbackQuery, callback_data: AucCb, state: FSMContext, db_user: User) -> None:
    uc_id = int(callback_data.aid)
    async with get_session() as session:
        row = (
            await session.execute(
                select(UserCard, Card)
                .join(Card, Card.id == UserCard.card_id)
                .where(UserCard.id == uc_id, UserCard.user_id == db_user.id)
            )
        ).first()
    if row is None:
        await callback.answer("❌ Karta topilmadi.", show_alert=True)
        return
    uc, card = row
    if uc.is_locked:
        await callback.answer("🔒 Bu karta hozir bozorda yoki boshqa bitimda band!", show_alert=True)
        return
    await state.set_state(V2UserStates.waiting_user_auc_price)
    await state.update_data(owner_tg_id=callback.from_user.id, auc_uc_id=uc.id, auc_card_id=card.id)
    meta = get_rarity_meta(card.rarity)
    lv_txt = f" · Lv.{uc.level}" if int(uc.level or 1) > 1 else ""
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Bekor qilish", callback_data=AucCb(owner_id=callback.from_user.id, act="v").pack())]]
    )
    await edit_or_answer(
        callback,
        (
            f"🔨 <b>AUKSION YARATISH (1/2)</b>\n{LINE_SEP}\n"
            f"🎴 Karta: {meta.emoji} <b>{esc(card.character_name)}</b>{lv_txt}\n\n"
            f"💰 Boshlang‘ich narxni yozing (<b>{MIN_CARD_PRICE:,}</b> – <b>{MAX_CARD_PRICE:,}</b> 🪙):\n"
            "<i>Bekor qilish: /cancel</i>"
        ),
        reply_markup=cancel_kb,
    )
    await callback.answer()


@router.message(V2UserStates.waiting_user_auc_price, NotCommandFilter())
async def fsm_user_auc_price(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit():
        await message.answer("⚠️ Faqat butun coin sonini kiriting (masalan: <code>5000</code>).")
        return
    price = int(raw)
    if not (MIN_CARD_PRICE <= price <= MAX_CARD_PRICE):
        await message.answer(f"⚠️ Boshlang‘ich narx <b>{MIN_CARD_PRICE:,}</b> dan <b>{MAX_CARD_PRICE:,}</b> 🪙 gacha bo‘lishi kerak.")
        return
    uc_id = int(data["auc_uc_id"])
    await state.update_data(auc_price=price)
    await state.set_state(None)
    await message.answer(
        (
            f"🔨 <b>AUKSION YARATISH (2/2)</b>\n{LINE_SEP}\n"
            f"💰 Boshlang‘ich narx: <b>{price:,} 🪙</b>\n\n"
            "⏳ Auksion qancha vaqt davom etsin?"
        ),
        reply_markup=user_auction_dur_kb(message.from_user.id, uc_id),
    )


@router.callback_query(AucCb.filter(F.act == "dur"))
async def cb_auction_dur(callback: CallbackQuery, callback_data: AucCb, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    if int(data.get("auc_uc_id") or 0) != int(callback_data.aid) or not data.get("auc_price"):
        await callback.answer("⏳ Ma’lumot eskirgan. Qaytadan boshlang.", show_alert=True)
        return
    price = int(data["auc_price"])
    card_id = int(data["auc_card_id"])
    uc_id = int(data["auc_uc_id"])
    hours = max(1, min(72, int(callback_data.amt)))
    min_step = max(50, min(5000, ((price // 10) // 50) * 50 or 50))
    await state.clear()

    async with get_session() as session:
        ok, msg, _auc = await AuctionWishlistService.create_auction(
            session,
            card_id=card_id,
            start_price=price,
            hours=hours,
            created_by_tg_id=callback.from_user.id,
            min_step=min_step,
            seller_user_id=db_user.id,
            user_card_id=uc_id,
        )
        if ok:
            await session.commit()
        else:
            await session.rollback()
    await callback.answer(msg[:190], show_alert=True)
    await _show_auctions(callback, callback.from_user.id, my_only=True, db_user=db_user)


@router.callback_query(AucCb.filter(F.act == "cancel_u"))
async def cb_auction_cancel_user(callback: CallbackQuery, callback_data: AucCb, db_user: User) -> None:
    async with get_session() as session:
        ok, msg = await AuctionWishlistService.cancel_user_auction(session, callback_data.aid, db_user.id)
        if ok:
            await session.commit()
        else:
            await session.rollback()
    await callback.answer(msg[:190], show_alert=True)
    await _show_auctions(callback, callback.from_user.id, my_only=True, db_user=db_user)


@router.callback_query(AucCb.filter(F.act == "det"))
async def cb_auction_detail(callback: CallbackQuery, callback_data: AucCb, db_user: User) -> None:
    async with get_session() as session:
        det = await AuctionWishlistService.get_auction_detail(session, callback_data.aid)
        seller_name = "🏛 Tizim (Admin)"
        uc_level = 1
        if det is not None:
            auc_tmp = det[0]
            uc_level = max(1, int(getattr(auc_tmp, "card_level", 1) or 1))
            if auc_tmp.seller_user_id:
                s_user = await session.get(User, auc_tmp.seller_user_id)
                if s_user:
                    seller_name = f"👤 {esc(s_user.display_name)}"
            if auc_tmp.user_card_id:
                uc = await session.get(UserCard, auc_tmp.user_card_id)
                if uc:
                    uc_level = int(uc.level or 1)
    if det is None:
        await callback.answer("Auksion topilmadi.", show_alert=True)
        return
    auc, card, leader = det
    leader_name = esc(leader.display_name) if leader else "Hali yo‘q"
    is_seller = bool(auc.seller_user_id and auc.seller_user_id == db_user.id)
    lvl_line = f"⚡ Daraja: <b>Lv.{uc_level}</b>\n" if uc_level > 1 else ""
    txt = (
        f"{header(f'🔨 <b>AUKSION #{auc.id}</b>')}\n"
        f"{format_card_block(card)}\n"
        f"{LINE_SEP}\n"
        f"🏷 Sotuvchi: <b>{seller_name}</b>\n"
        f"{lvl_line}"
        f"💰 Joriy narx: <b>{fmt_coins(auc.current_price)} 🪙</b>\n"
        f"📈 Eng kam qadam: <b>+{fmt_coins(auc.min_step)}</b> 🪙\n"
        f"👑 Yetakchi: <b>{leader_name}</b> · {auc.bid_count} stavka\n"
        f"⏳ Tugaydi: <b>{fmt_tashkent(auc.ends_at)}</b> (Toshkent vaqti)"
    )
    if callback.message:
        await send_card_message(
            callback.bot,
            callback.message.chat.id,
            card,
            txt,
            auction_detail_kb(callback.from_user.id, auc, is_seller=is_seller),
        )
    await callback.answer()


@router.callback_query(AucCb.filter(F.act == "bid"))
async def cb_auction_bid(callback: CallbackQuery, callback_data: AucCb, db_user: User) -> None:
    async with get_session() as session:
        user = await session.get(User, db_user.id)
        ok, msg = await AuctionWishlistService.place_bid(session, callback_data.aid, user, callback_data.amt)
        if ok:
            await session.commit()
        else:
            await session.rollback()
    await callback.answer(msg[:190], show_alert=True)


@router.callback_query(AucCb.filter(F.act == "custom"))
async def cb_auction_custom(callback: CallbackQuery, callback_data: AucCb, state: FSMContext) -> None:
    await state.set_state(V2UserStates.waiting_auction_bid)
    await state.update_data(auc_id=callback_data.aid, owner_tg_id=callback.from_user.id)
    await callback.message.answer(f"🔨 Auksion #{callback_data.aid} uchun stavka summasini kiriting (masalan: <code>5000</code>):\n/cancel")
    await callback.answer()


@router.message(V2UserStates.waiting_auction_bid, NotCommandFilter())
async def fsm_auction_bid(message: Message, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    txt = (message.text or "").strip()
    if not txt.isdigit():
        await message.answer("⚠️ Faqat musbat son kiriting.")
        return
    aid = int(data["auc_id"])
    await state.clear()
    async with get_session() as session:
        user = await session.get(User, db_user.id)
        ok, msg = await AuctionWishlistService.place_bid(session, aid, user, int(txt))
        if ok:
            await session.commit()
        else:
            await session.rollback()
    await message.answer(msg)
