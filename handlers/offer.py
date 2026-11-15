import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from database.session import get_session
from keyboards.main_menu import back_home_kb
from keyboards.market import incoming_offers_kb, offer_decision_kb
from models.user import User
from services.offer_service import OfferService
from services.search_service import SearchService
from utils.callback_data import OfferCb
from utils.constants import MAX_CARD_PRICE, MIN_CARD_PRICE
from utils.formatting import esc, fmt_coins
from utils.tg_helpers import edit_or_answer

logger = logging.getLogger(__name__)
router = Router(name="offer")


class OfferStates(StatesGroup):
    waiting_amount = State()


@router.callback_query(OfferCb.filter(F.action == "prompt"))
async def cb_offer_prompt(
    callback: CallbackQuery,
    callback_data: OfferCb,
    state: FSMContext,
    db_user: User,
) -> None:
    async with get_session() as session:
        item = await SearchService.inspect_user_card(session, callback_data.user_card_id)

    if item is None:
        await callback.answer("Karta topilmadi.", show_alert=True)
        return
    if item.owner.id == db_user.id:
        await callback.answer("O‘z kartangizga taklif yubora olmaysiz!", show_alert=True)
        return
    if item.user_card.is_locked:
        await callback.answer("Bu karta hozir marketda yoki band.", show_alert=True)
        return

    await state.set_state(OfferStates.waiting_amount)
    await state.update_data(
        user_card_id=item.user_card.id,
        owner_tg_id=callback.from_user.id,
    )
    await edit_or_answer(
        callback,
        f"💰 <b>TAKLIF YUBORISH</b>\n\n"
        f"🎴 Karta: <b>{esc(item.card.character_name)}</b>\n"
        f"👤 Egasi: {esc(item.owner.display_name)}\n\n"
        f"Taklif qilmoqchi bo‘lgan coin miqdorini yozing ({MIN_CARD_PRICE:,} — {MAX_CARD_PRICE:,}):\n"
        f"<i>(Taklif qabul qilinmasa yoki rad etilsa coinlar to‘liq qaytariladi)</i>",
        reply_markup=back_home_kb(callback.from_user.id, "search"),
    )


@router.message(OfferStates.waiting_amount)
async def fsm_offer_amount(message: Message, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return

    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit():
        await message.answer("⚠️ Iltimos, faqat musbat son kiriting.")
        return

    amount = int(raw)
    user_card_id = int(data["user_card_id"])

    async with get_session() as session:
        res = await OfferService.create_offer(
            session=session,
            buyer_id=db_user.id,
            user_card_id=user_card_id,
            amount=amount,
        )
        if res.success:
            await session.commit()
        else:
            await session.rollback()

    await state.clear()
    if not res.success:
        await message.answer(f"❌ {res.message}", reply_markup=back_home_kb(message.from_user.id, "search"))
        return

    await message.answer(
        f"✅ Taklif yuborildi!\n\n"
        f"“Men <b>{esc(res.card.character_name)}</b> kartasi uchun <b>{fmt_coins(amount)} coins</b> taklif qilaman.”",
        reply_markup=back_home_kb(message.from_user.id, "search"),
    )

    # Notify card owner via DM if bot can message them
    try:
        await message.bot.send_message(
            chat_id=res.owner.telegram_id,
            text=(
                f"📩 <b>Yangi taklif!</b>\n\n"
                f"🎴 {esc(res.card.character_name)}\n"
                f"💰 Offer: <b>{fmt_coins(amount)} coins</b>\n"
                f"👤 Xaridor: {esc(res.buyer.display_name)}"
            ),
            reply_markup=offer_decision_kb(res.owner.telegram_id, res.offer.id),
        )
    except Exception as exc:
        logger.debug("Could not DM owner %s about new offer: %s", res.owner.telegram_id, exc)


@router.callback_query(OfferCb.filter(F.action == "incoming"))
async def cb_incoming_offers(callback: CallbackQuery, db_user: User) -> None:
    async with get_session() as session:
        offers = await OfferService.get_incoming_offers(session, db_user.id)
        await session.commit()

    if not offers:
        text = "📩 <b>KELGAN TAKLIFLAR</b>\n\n📭 Hozircha faol takliflar yo‘q."
    else:
        lines = [f"📩 <b>KELGAN TAKLIFLAR</b> ({len(offers)} ta)\n"]
        for off, card, buyer in offers[:8]:
            lines.append(
                f"• <b>{esc(card.character_name)}</b> — <b>{fmt_coins(off.amount)} 🪙</b> | 👤 {esc(buyer.display_name)}"
            )
        text = "\n".join(lines)

    await edit_or_answer(
        callback,
        text,
        reply_markup=incoming_offers_kb(callback.from_user.id, offers),
    )


@router.callback_query(OfferCb.filter(F.action == "accept"))
async def cb_accept_offer(callback: CallbackQuery, callback_data: OfferCb, db_user: User) -> None:
    async with get_session() as session:
        res = await OfferService.accept_offer(
            session=session,
            offer_id=callback_data.offer_id,
            actor_owner_id=db_user.id,
        )
        if res.success:
            await session.commit()
        else:
            await session.rollback()

    if not res.success:
        await callback.answer(f"❌ {res.message}", show_alert=True)
        return

    await edit_or_answer(
        callback,
        f"✅ <b>TAKLIF QABUL QILINDI!</b>\n\n"
        f"🎴 <b>{esc(res.card.character_name)}</b> → {esc(res.buyer.display_name)} ga o‘tdi!\n"
        f"➕ Sizga <b>{fmt_coins(res.offer.amount)} 🪙</b> qo‘shildi.",
        reply_markup=back_home_kb(callback.from_user.id, "cards"),
    )


@router.callback_query(OfferCb.filter(F.action == "reject"))
async def cb_reject_offer(callback: CallbackQuery, callback_data: OfferCb, db_user: User) -> None:
    async with get_session() as session:
        res = await OfferService.reject_offer(
            session=session,
            offer_id=callback_data.offer_id,
            actor_user_id=db_user.id,
        )
        if res.success:
            await session.commit()
        else:
            await session.rollback()

    await callback.answer(res.message, show_alert=not res.success)
    await edit_or_answer(
        callback,
        f"❌ {res.message}",
        reply_markup=back_home_kb(callback.from_user.id, "cards"),
    )
