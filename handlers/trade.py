"""Ikki tomonlama karta almashish (/trade)."""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from database.session import get_session
from keyboards.main_menu import back_home_kb
from models.card import Card
from models.user import User
from models.user_card import UserCard
from models.v2_features import CardTrade
from services.auction_wishlist_service import AuctionWishlistService
from services.user_service import UserService
from utils.callback_data import TradeCb
from utils.constants import get_rarity_meta
from utils.formatting import esc, header
from utils.tg_helpers import edit_or_answer

logger = logging.getLogger(__name__)
router = Router(name="trade")


# ------------------------------------------------------------------
# TWO-SIDED CARD-FOR-CARD TRADING (/trade)
# ------------------------------------------------------------------
@router.message(Command("trade"))
async def cmd_trade(message: Message, state: FSMContext, db_user: User) -> None:
    await state.clear()
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("🤝 <b>KARTA ALMASHISH</b>\n\nFoydalanish: <code>/trade @username</code>\nMasalan: <code>/trade @doston</code>")
        return
    async with get_session() as session:
        partner = await UserService.find_user(session, parts[1].strip())
        if partner is None or partner.id == db_user.id:
            await message.answer("❌ Foydalanuvchi topilmadi.")
            return
        my_cards = (
            await session.execute(
                select(UserCard, Card)
                .join(Card, Card.id == UserCard.card_id)
                .where(UserCard.user_id == db_user.id, UserCard.is_locked.is_(False))
                .limit(20)
            )
        ).all()
    if not my_cards:
        await message.answer("📭 Almashish uchun bo‘sh kartalaringiz yo‘q.")
        return
    rows = [
        [
            InlineKeyboardButton(
                text=f"🎴 {c.character_name[:28]} ({get_rarity_meta(c.rarity).name})",
                callback_data=TradeCb(owner_id=message.from_user.id, act="pick", tid=partner.id, ucid=uc.id).pack(),
            )
        ]
        for uc, c in my_cards
    ]
    await message.answer(
        f"🤝 <b>{esc(partner.display_name)}</b> ga taklif qilmoqchi bo‘lgan kartangizni tanlang:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(TradeCb.filter(F.act == "v"))
async def cb_trade_help(callback: CallbackQuery) -> None:
    await edit_or_answer(
        callback,
        f"{header('🤝 <b>KARTA ALMASHISH</b>')}\n"
        "Do‘stingiz bilan kartani karta bilan <b>xavfsiz</b> almashing — ikki tomon ham rozi bo‘lmaguncha hech narsa o‘tmaydi.\n\n"
        "<b>Qanday?</b>\n"
        "1️⃣ <code>/trade @username</code> deb yozing\n"
        "2️⃣ O‘z kartangizni tanlang\n"
        "3️⃣ Do‘stingiz o‘z kartasini tanlaydi\n"
        "4️⃣ Ikkalangiz tasdiqlaysiz — almashuv bo‘ladi ✅\n\n"
        "🎁 Bepul sovg‘a uchun: <code>/give @username</code>",
        reply_markup=back_home_kb(callback.from_user.id, "savdo"),
    )


@router.callback_query(TradeCb.filter(F.act == "pick"))
async def cb_trade_pick(callback: CallbackQuery, callback_data: TradeCb, db_user: User) -> None:
    async with get_session() as session:
        ok, msg, trade = await AuctionWishlistService.create_trade(
            session, db_user.id, callback_data.tid, callback_data.ucid
        )
        if not ok or trade is None:
            await session.rollback()
            await callback.answer(msg[:190], show_alert=True)
            return
        partner = await session.get(User, callback_data.tid)
        p_cards = (
            await session.execute(
                select(UserCard, Card)
                .join(Card, Card.id == UserCard.card_id)
                .where(UserCard.user_id == partner.id, UserCard.is_locked.is_(False))
                .limit(15)
            )
        ).all()
        await session.commit()

    if partner:
        rows = [
            [
                InlineKeyboardButton(
                    text=f"🔁 {c.character_name[:26]} ({get_rarity_meta(c.rarity).name})",
                    callback_data=TradeCb(owner_id=partner.telegram_id, act="accept", tid=trade.id, ucid=uc.id).pack(),
                )
            ]
            for uc, c in p_cards
        ]
        rows.append(
            [
                InlineKeyboardButton(
                    text="❌ Rad etish",
                    callback_data=TradeCb(owner_id=partner.telegram_id, act="reject", tid=trade.id).pack(),
                )
            ]
        )
        try:
            await callback.bot.send_message(
                partner.telegram_id,
                f"🤝 <b>{esc(db_user.display_name)}</b> sizga karta almashishni taklif qildi!\n"
                "Almashtirmoqchi bo‘lgan kartangizni tanlang:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            )
        except Exception:
            pass
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Taklifni bekor qilish",
                    callback_data=TradeCb(owner_id=callback.from_user.id, act="cancel", tid=trade.id).pack(),
                )
            ]
        ]
    )
    await edit_or_answer(
        callback,
        "✅ Almashuv taklifi yuborildi! Hamkoringiz karta tanlashini kuting.\n<i>Taklif 12 soat ichida javob berilmasa avtomatik bekor qilinadi.</i>",
        reply_markup=cancel_kb,
    )


@router.callback_query(TradeCb.filter(F.act == "accept"))
async def cb_trade_accept(callback: CallbackQuery, callback_data: TradeCb, db_user: User) -> None:
    async with get_session() as session:
        ok, msg = await AuctionWishlistService.confirm_trade(
            session, callback_data.tid, db_user.id, callback_data.ucid
        )
        if ok:
            await session.commit()
        else:
            await session.rollback()
    await callback.answer(msg[:190], show_alert=True)
    if ok:
        await edit_or_answer(callback, msg)


@router.callback_query(TradeCb.filter(F.act == "reject"))
async def cb_trade_reject(callback: CallbackQuery, callback_data: TradeCb, db_user: User) -> None:
    async with get_session() as session:
        tr = await session.get(CardTrade, callback_data.tid)
        if tr and tr.partner_user_id == db_user.id and tr.status == "pending_pick":
            tr.status = "rejected"
            uc = await session.get(UserCard, tr.initiator_uc_id)
            if uc:
                uc.is_locked = False
                uc.card_state = "OWNED"
            await session.commit()
    await callback.answer("❌ Almashuv rad etildi.")
    await edit_or_answer(callback, "❌ Almashuv rad etildi.")


@router.callback_query(TradeCb.filter(F.act == "cancel"))
async def cb_trade_cancel(callback: CallbackQuery, callback_data: TradeCb, db_user: User) -> None:
    async with get_session() as session:
        ok, msg = await AuctionWishlistService.cancel_trade(session, callback_data.tid, db_user.id)
        if ok:
            await session.commit()
        else:
            await session.rollback()
    await callback.answer(msg[:190], show_alert=True)
    if ok:
        await edit_or_answer(callback, msg, reply_markup=back_home_kb(callback.from_user.id, "home"))
