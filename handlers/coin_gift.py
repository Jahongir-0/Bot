"""Foydalanuvchilar o'rtasida COIN yuborish (sovg'a). Komissiya va limitlarni admin boshqaradi.

Kirish: «🎁 Sovg'a» menyusi → «💸 Coin yuborish», yoki buyruq:
  /sendcoin @username 500      (yoki xabarga reply qilib: /sendcoin 500)
"""
from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.session import get_session
from models.user import User
from services.coin_gift_service import CoinGiftService
from services.settings_service import SettingsService
from services.user_service import UserService
from utils.callback_data import NavCb
from utils.formatting import esc, fmt_coins
from utils.tg_helpers import NotCommandFilter, edit_or_answer

logger = logging.getLogger(__name__)
router = Router(name="coin_gift")


class CoinGiftCb(CallbackData, prefix="cgf"):
    owner_id: int
    action: str  # menu | start | confirm | cancel
    to_user_id: int = 0
    amount: int = 0


class CoinGiftStates(StatesGroup):
    waiting_recipient = State()
    waiting_amount = State()


def _parse_amount(raw: str) -> int:
    clean = re.sub(r"[\s,._]", "", (raw or "").lower())
    mult = 1
    if clean.endswith("k"):
        mult, clean = 1000, clean[:-1]
    elif clean.endswith("m"):
        mult, clean = 1_000_000, clean[:-1]
    return int(clean) * mult if clean.isdigit() else 0


async def _fee_preview(session, cfg: dict, amount: int) -> tuple[int, int]:
    """(komissiya, qabul qiluvchi oladigan) — send_coins bilan bir xil hisob."""
    comm_cfg = await SettingsService.get_commission_config(session)
    fee, _pct = SettingsService.compute_commission(amount, comm_cfg, "coin_gift")
    if fee == 0 and cfg["fee_percent"] > 0 and (comm_cfg.get("coin_gift") or {}).get("enabled", True):
        fee = amount * cfg["fee_percent"] // 100
    return fee, amount - fee


def _cancel_kb(owner: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Bekor qilish", callback_data=CoinGiftCb(owner_id=owner, action="cancel").pack())]]
    )


# --------------------------------------------------------------- menyu
@router.callback_query(NavCb.filter(F.target == "give"))
async def cb_give_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    o = callback.from_user.id
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🎴 Karta sovg‘a qilish", callback_data=NavCb(owner_id=o, target="give_card").pack()))
    kb.row(InlineKeyboardButton(text="💸 Coin yuborish", callback_data=CoinGiftCb(owner_id=o, action="start").pack()))
    kb.row(InlineKeyboardButton(text="⬅️ Orqaga", callback_data=NavCb(owner_id=o, target="home").pack()))
    await edit_or_answer(
        callback,
        "🎁 <b>SOVG‘A</b>\n━━━━━━━━━━━━━━━\nDo‘stingizga karta yoki coin yuboring:",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(CoinGiftCb.filter(F.action == "start"))
async def cb_coin_start(callback: CallbackQuery, state: FSMContext) -> None:
    async with get_session() as session:
        cfg = await CoinGiftService.get_config(session)
    if not cfg["enabled"]:
        await callback.answer("Coin yuborish hozircha o‘chirilgan.", show_alert=True)
        return
    await state.clear()
    await state.set_state(CoinGiftStates.waiting_recipient)
    await state.update_data(owner_tg_id=callback.from_user.id)
    limit_txt = f"{fmt_coins(cfg['daily_limit'])} 🪙" if cfg["daily_limit"] else "cheklovsiz"
    await edit_or_answer(
        callback,
        "💸 <b>COIN YUBORISH</b>\n━━━━━━━━━━━━━━━\n"
        f"• Minimal miqdor: <b>{fmt_coins(cfg['min_amount'])}</b> 🪙\n"
        f"• Kunlik limit: <b>{limit_txt}</b>\n\n"
        "👤 Qabul qiluvchining <b>@username</b>, <b>ismi</b> yoki <b>ID</b> raqamini yozing:\n"
        "<i>Qabul qiluvchi botdan kamida bir marta foydalangan bo‘lishi kerak.</i>",
        reply_markup=_cancel_kb(callback.from_user.id),
    )


@router.callback_query(CoinGiftCb.filter(F.action == "cancel"))
async def cb_coin_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Bekor qilindi.")
    o = callback.from_user.id
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🎁 Sovg‘a menyusi", callback_data=NavCb(owner_id=o, target="give").pack())]]
    )
    await edit_or_answer(callback, "❌ Coin yuborish bekor qilindi.", reply_markup=kb)


# --------------------------------------------------------------- qabul qiluvchi
@router.message(CoinGiftStates.waiting_recipient, NotCommandFilter())
async def fsm_recipient(message: Message, state: FSMContext, db_user: User) -> None:
    query = (message.text or "").strip()
    async with get_session() as session:
        recipient = await UserService.find_user(session, query)
    if recipient is None:
        await message.answer("❌ Foydalanuvchi topilmadi. @username yoki ID ni qayta yozing:", reply_markup=_cancel_kb(message.from_user.id))
        return
    if recipient.id == db_user.id:
        await message.answer("❌ O‘zingizga coin yubora olmaysiz. Boshqa foydalanuvchini yozing:", reply_markup=_cancel_kb(message.from_user.id))
        return
    if recipient.is_banned:
        await message.answer("❌ Bu foydalanuvchiga coin yuborib bo‘lmaydi.", reply_markup=_cancel_kb(message.from_user.id))
        return
    await state.set_state(CoinGiftStates.waiting_amount)
    await state.update_data(to_user_id=recipient.id)
    await message.answer(
        f"👤 Qabul qiluvchi: <b>{esc(recipient.display_name)}</b>\n"
        f"💰 Balansingiz: <b>{fmt_coins(db_user.coins)}</b> 🪙\n\n"
        "Qancha coin yuborasiz? (masalan: <code>500</code> yoki <code>2k</code>)",
        reply_markup=_cancel_kb(message.from_user.id),
    )


# --------------------------------------------------------------- miqdor
async def _show_confirm(target: Message, db_user: User, to_user_id: int, amount: int) -> None:
    async with get_session() as session:
        cfg = await CoinGiftService.get_config(session)
        recipient = await session.get(User, to_user_id)
        fee, received = await _fee_preview(session, cfg, amount)
    if recipient is None:
        await target.answer("❌ Foydalanuvchi topilmadi.")
        return
    o = target.from_user.id if target.from_user else db_user.telegram_id
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Yuborish", callback_data=CoinGiftCb(owner_id=db_user.telegram_id, action="confirm", to_user_id=to_user_id, amount=amount).pack())],
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data=CoinGiftCb(owner_id=db_user.telegram_id, action="cancel").pack())],
        ]
    )
    fee_line = f"\n💸 Komissiya: <b>{fmt_coins(fee)}</b> 🪙" if fee else ""
    await target.answer(
        "💸 <b>TASDIQLANG</b>\n━━━━━━━━━━━━━━━\n"
        f"👤 Kimga: <b>{esc(recipient.display_name)}</b>\n"
        f"📤 Yuboriladi: <b>{fmt_coins(amount)}</b> 🪙{fee_line}\n"
        f"📥 Qabul qiluvchi oladi: <b>{fmt_coins(received)}</b> 🪙",
        reply_markup=kb,
    )


@router.message(CoinGiftStates.waiting_amount, NotCommandFilter())
async def fsm_amount(message: Message, state: FSMContext, db_user: User) -> None:
    amount = _parse_amount(message.text or "")
    if amount <= 0:
        await message.answer("❌ Miqdorni raqam bilan yozing (masalan: <code>500</code>):", reply_markup=_cancel_kb(message.from_user.id))
        return
    data = await state.get_data()
    to_user_id = int(data.get("to_user_id") or 0)
    if not to_user_id:
        await state.clear()
        await message.answer("Sessiya tugadi. Qaytadan boshlang.")
        return
    await state.clear()
    await _show_confirm(message, db_user, to_user_id, amount)


# --------------------------------------------------------------- tasdiq
@router.callback_query(CoinGiftCb.filter(F.action == "confirm"))
async def cb_confirm(callback: CallbackQuery, callback_data: CoinGiftCb, db_user: User) -> None:
    async with get_session() as session:
        ok, msg, received, new_balance = await CoinGiftService.send_coins(
            session, db_user.id, callback_data.to_user_id, callback_data.amount
        )
        recipient = await session.get(User, callback_data.to_user_id) if ok else None
        if ok:
            await session.commit()
        else:
            await session.rollback()
    o = callback.from_user.id
    back = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🎁 Sovg‘a menyusi", callback_data=NavCb(owner_id=o, target="give").pack())]]
    )
    if not ok:
        await edit_or_answer(callback, f"❌ {esc(msg)}", reply_markup=back)
        return
    await edit_or_answer(
        callback,
        f"✅ <b>Coin yuborildi!</b>\n👤 {esc(recipient.display_name)} ga <b>{fmt_coins(received)}</b> 🪙 yetib bordi.\n"
        f"💰 Yangi balansingiz: <b>{fmt_coins(new_balance)}</b> 🪙",
        reply_markup=back,
    )
    try:
        await callback.bot.send_message(
            recipient.telegram_id,
            f"🎁 <b>{esc(db_user.display_name)}</b> sizga <b>{fmt_coins(received)}</b> 🪙 yubordi!",
        )
    except Exception:
        logger.debug("Coin qabul qiluvchiga xabar yuborilmadi")


# --------------------------------------------------------------- /sendcoin
@router.message(Command("sendcoin"))
async def cmd_sendcoin(message: Message, state: FSMContext, db_user: User) -> None:
    await state.clear()
    parts = (message.text or "").split()
    reply_user = message.reply_to_message.from_user if message.reply_to_message else None
    usage = "ℹ️ Foydalanish: <code>/sendcoin @username 500</code> yoki xabarga reply qilib <code>/sendcoin 500</code>"
    if reply_user is not None and not reply_user.is_bot and len(parts) == 2:
        query, amount_raw = str(reply_user.id), parts[1]
    elif len(parts) >= 3:
        query, amount_raw = parts[1], parts[2]
    else:
        await message.answer(usage)
        return
    amount = _parse_amount(amount_raw)
    if amount <= 0:
        await message.answer(usage)
        return
    async with get_session() as session:
        recipient = await UserService.find_user(session, query)
    if recipient is None:
        await message.answer("❌ Foydalanuvchi topilmadi (u botdan kamida bir marta foydalangan bo‘lishi kerak).")
        return
    if recipient.id == db_user.id:
        await message.answer("❌ O‘zingizga coin yubora olmaysiz.")
        return
    await _show_confirm(message, db_user, recipient.id, amount)
