"""Takliflar (coin taklifi va karta-karta taklifi) handlerlari."""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from database.session import get_session
from keyboards.main_menu import back_home_kb
from keyboards.market import (
    incoming_offers_kb,
    offer_card_pick_kb,
    offer_decision_kb,
    offer_type_kb,
    outgoing_offers_kb,
)
from models.card import Card
from models.market import CardListing
from models.user import User
from models.user_card import UserCard
from services.community_service import CommunityService
from services.offer_service import OfferService
from services.search_service import SearchService
from services.settings_service import SettingsService
from utils.callback_data import OfferCb
from utils.constants import MAX_CARD_PRICE, MIN_CARD_PRICE, PAGE_SIZE, get_rarity_meta
from utils.formatting import esc, fmt_coins, header
from utils.tg_helpers import NotCommandFilter, edit_or_answer, is_private_chat, send_card_message

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
    await state.clear()
    async with get_session() as session:
        item = await SearchService.inspect_user_card(session, callback_data.user_card_id)
        has_active_listing = False
        if item and item.user_card.is_locked:
            has_active_listing = (
                await session.execute(
                    select(CardListing.id).where(
                        CardListing.user_card_id == item.user_card.id,
                        CardListing.status == "active",
                    )
                )
            ).first() is not None

    if item is None:
        await callback.answer("Karta topilmadi.", show_alert=True)
        return
    if item.owner.id == db_user.id:
        await callback.answer("O‘z kartangizga taklif yubora olmaysiz!", show_alert=True)
        return
    if item.user_card.is_locked and not has_active_listing:
        await callback.answer("Bu karta hozir transfer yoki auksion jarayonida band.", show_alert=True)
        return

    meta = get_rarity_meta(item.card.rarity)
    await edit_or_answer(
        callback,
        f"💰 <b>TAKLIF YUBORISH</b>\n\n"
        f"🎴 Karta: {meta.emoji} <b>{esc(item.card.character_name)}</b> ({esc(item.card.anime_name)})\n"
        f"👤 Egasi: <b>{esc(item.owner.display_name)}</b>\n\n"
        "Nima taklif qilmoqchisiz?",
        reply_markup=offer_type_kb(callback.from_user.id, item.user_card.id),
    )


@router.callback_query(OfferCb.filter(F.action == "coin_ask"))
async def cb_offer_coin_ask(
    callback: CallbackQuery,
    callback_data: OfferCb,
    state: FSMContext,
    db_user: User,
) -> None:
    async with get_session() as session:
        item = await SearchService.inspect_user_card(session, callback_data.user_card_id)
        comm_cfg = await SettingsService.get_commission_config(session)
    if item is None or item.owner.id == db_user.id:
        await callback.answer("Karta topilmadi.", show_alert=True)
        return

    comm = comm_cfg.get("market_offer") or {}
    comm_note = (
        f"\n💸 <i>Eslatma: qabul qilinsa tizim komissiyasi {int(comm.get('percent', 0))}% egasining tushumidan ushlanadi.</i>"
        if comm.get("enabled") and int(comm.get("percent", 0)) > 0
        else ""
    )
    await state.set_state(OfferStates.waiting_amount)
    await state.update_data(
        user_card_id=item.user_card.id,
        owner_tg_id=callback.from_user.id,
    )
    await edit_or_answer(
        callback,
        f"🪙 <b>COIN TAKLIFI YUBORISH</b>\n\n"
        f"🎴 Karta: <b>{esc(item.card.character_name)}</b>\n"
        f"👤 Egasi: {esc(item.owner.display_name)}\n\n"
        f"Taklif qilmoqchi bo‘lgan coin miqdorini yozing ({MIN_CARD_PRICE:,} — {MAX_CARD_PRICE:,}):{comm_note}\n"
        f"<i>(Taklif qabul qilinmasa yoki rad etilsa coinlar to‘liq qaytariladi. Bekor qilish: /cancel)</i>",
        reply_markup=back_home_kb(callback.from_user.id, "search"),
    )


@router.callback_query(OfferCb.filter(F.action == "card_pick"))
async def cb_offer_card_pick(
    callback: CallbackQuery,
    callback_data: OfferCb,
    db_user: User,
) -> None:
    target_uc_id = int(callback_data.offer_id)
    page = max(1, int(callback_data.page))
    async with get_session() as session:
        cond = [UserCard.user_id == db_user.id, UserCard.is_locked.is_(False)]
        total = int((await session.execute(select(func.count(UserCard.id)).where(*cond))).scalar_one() or 0)
        rows = (
            await session.execute(
                select(UserCard, Card)
                .join(Card, Card.id == UserCard.card_id)
                .where(*cond)
                .order_by(Card.base_value.desc(), UserCard.id.desc())
                .offset((page - 1) * PAGE_SIZE)
                .limit(PAGE_SIZE)
            )
        ).all()

    if not rows:
        await callback.answer("📭 Taklif qilish uchun bo‘sh kartalaringiz yo‘q.", show_alert=True)
        return

    await edit_or_answer(
        callback,
        f"🎴 <b>KARTA TAKLIF QILISH</b>\n\n"
        f"Almashish uchun bermoqchi bo‘lgan kartangizni tanlang ({total} ta bo‘sh karta):",
        reply_markup=offer_card_pick_kb(
            callback.from_user.id,
            target_uc_id,
            [(r[0], r[1]) for r in rows],
            page=page,
            total=total,
            page_size=PAGE_SIZE,
        ),
    )


@router.callback_query(OfferCb.filter(F.action == "card_send"))
async def cb_offer_card_send(
    callback: CallbackQuery,
    callback_data: OfferCb,
    db_user: User,
) -> None:
    target_uc_id = int(callback_data.offer_id)
    offered_uc_id = int(callback_data.user_card_id)
    async with get_session() as session:
        res = await OfferService.create_card_offer(
            session=session,
            buyer_id=db_user.id,
            target_user_card_id=target_uc_id,
            offered_user_card_id=offered_uc_id,
        )
        if res.success:
            await CommunityService.notify_user(
                session,
                res.owner.id,
                "offer",
                "🎴 Yangi karta taklifi!",
                f"{res.buyer.display_name} sizning {res.card.character_name} kartangizga "
                f"{res.offered_card.character_name} kartasini taklif qildi!",
            )
            await session.commit()
        else:
            await session.rollback()

    if not res.success:
        await callback.answer(f"❌ {res.message}", show_alert=True)
        return

    await callback.answer("✅ Karta taklifi yuborildi!", show_alert=True)
    await edit_or_answer(
        callback,
        f"✅ <b>KARTA TAKLIFI YUBORILDI!</b>\n\n"
        f"🎯 Siz so‘ragan karta: <b>{esc(res.card.character_name)}</b>\n"
        f"🎴 Siz taklif qilgan karta: <b>{esc(res.offered_card.character_name)}</b>\n"
        f"👤 Egasi: {esc(res.owner.display_name)}\n\n"
        "<i>Egasi javob berguncha taklif qilgan kartangiz vaqtincha band turadi.</i>",
        reply_markup=back_home_kb(callback.from_user.id, "market"),
    )

    try:
        t_meta = get_rarity_meta(res.card.rarity)
        o_meta = get_rarity_meta(res.offered_card.rarity)
        await send_card_message(
            callback.bot,
            res.owner.telegram_id,
            res.card,
            f"📩 <b>YANGI KARTA TAKLIFI!</b>\n\n"
            f"🎯 Sizning kartangiz: {t_meta.emoji} <b>{esc(res.card.character_name)}</b>\n"
            f"👤 Taklif yuboruvchi: <b>{esc(res.buyer.display_name)}</b>",
        )
        await send_card_message(
            callback.bot,
            res.owner.telegram_id,
            res.offered_card,
            f"🎴 <b>TAKLIF QILINAYOTGAN KARTA</b>\n\n"
            f"{o_meta.emoji} <b>{esc(res.offered_card.character_name)}</b> ({esc(res.offered_card.anime_name)})\n"
            f"⭐ Lvl {int(getattr(res.offered_user_card, 'level', 1) or 1)} · 🆔 <code>{esc(res.offered_card.card_code)}</code>\n\n"
            "Almashishga rozimisiz?",
            reply_markup=offer_decision_kb(res.owner.telegram_id, res.offer.id),
        )
    except Exception as exc:
        logger.debug("Could not DM owner %s about card offer: %s", res.owner.telegram_id, exc)


@router.message(OfferStates.waiting_amount, NotCommandFilter())
async def fsm_offer_amount(message: Message, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return

    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit():
        if is_private_chat(message):
            await message.answer("⚠️ Iltimos, faqat musbat son kiriting. Bekor qilish: /cancel")
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
            await CommunityService.notify_user(
                session,
                res.owner.id,
                "offer",
                "💰 Yangi coin taklifi!",
                f"{res.buyer.display_name} sizning {res.card.character_name} kartangiz uchun {amount:,} 🪙 taklif qildi.",
            )
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

    try:
        await message.bot.send_message(
            chat_id=res.owner.telegram_id,
            text=(
                f"📩 <b>Yangi taklif!</b>\n\n"
                f"🎴 {esc(res.card.character_name)}\n"
                f"💰 Taklif: <b>{fmt_coins(amount)} coins</b>\n"
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
        text = f"{header('📩 <b>KELGAN TAKLIFLAR</b>')}\n📭 Hozircha faol takliflar yo‘q."
    else:
        lines = [header("📩 <b>KELGAN TAKLIFLAR</b>"), f"📌 Jami: <b>{len(offers)}</b> ta\n"]
        for off, card, buyer in offers[:8]:
            val_txt = "🎴 Karta almashuv" if getattr(off, "offer_type", "coins") == "card" else f"{fmt_coins(off.amount)} 🪙"
            lines.append(f"• <b>{esc(card.character_name)}</b> — <b>{val_txt}</b> | 👤 {esc(buyer.display_name)}")
        text = "\n".join(lines)

    await edit_or_answer(
        callback,
        text,
        reply_markup=incoming_offers_kb(callback.from_user.id, offers),
    )


@router.callback_query(OfferCb.filter(F.action == "mine"))
async def cb_outgoing_offers(callback: CallbackQuery, db_user: User) -> None:
    async with get_session() as session:
        offers = await OfferService.get_outgoing_offers(session, db_user.id)
        await session.commit()

    if not offers:
        text = f"{header('📤 <b>YUBORGAN TAKLIFLARIM</b>')}\n📭 Sizda hozircha faol yuborilgan takliflar yo‘q."
    else:
        lines = [header("📤 <b>YUBORGAN TAKLIFLARIM</b>"), f"📌 Jami: <b>{len(offers)}</b> ta\n"]
        for off, card, owner in offers[:8]:
            val_txt = "🎴 Karta almashuv" if getattr(off, "offer_type", "coins") == "card" else f"{fmt_coins(off.amount)} 🪙"
            lines.append(f"• <b>{esc(card.character_name)}</b> — <b>{val_txt}</b> | 👤 {esc(owner.display_name)}")
        lines.append("\n👇 Bekor qilmoqchi bo‘lsangiz tanlang:")
        text = "\n".join(lines)

    await edit_or_answer(
        callback,
        text,
        reply_markup=outgoing_offers_kb(callback.from_user.id, offers),
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
            if res.buyer is not None:
                await CommunityService.notify_user(
                    session,
                    res.buyer.id,
                    "offer",
                    "✅ Taklifingiz qabul qilindi!",
                    f"{res.card.character_name} kartasi bo‘yicha taklifingiz qabul qilindi!",
                )
            await session.commit()
        else:
            await session.rollback()

    if not res.success:
        await callback.answer(f"❌ {res.message}", show_alert=True)
        return

    if res.buyer is not None:
        try:
            await callback.bot.send_message(
                res.buyer.telegram_id,
                f"🎉 <b>Taklifingiz qabul qilindi!</b>\n\n"
                f"🎴 <b>{esc(res.card.character_name)}</b> kartasi kolleksiyangizga o‘tkazildi!",
            )
        except Exception:
            pass

    extra = ""
    if res.is_duplicate:
        if res.maxed:
            extra = f"\n👑 Xaridorda karta maksimal darajada edi — +{fmt_coins(res.refund)} 🪙 qaytarildi."
        else:
            extra = f"\n🔁 Xaridorda bu karta bor edi — daraja ko‘tarildi: <b>{res.level_before} → {res.level_after}</b>!"

    if res.offer_type == "card":
        gain_txt = f"🎴 Sizga <b>{esc(res.offered_card.character_name if res.offered_card else 'karta')}</b> o‘tdi!"
    else:
        payout = max(1, int(res.amount) - int(res.fee_coins or 0))
        fee_txt = f" (komissiya -{res.fee_pct}%)" if res.fee_coins else ""
        gain_txt = f"➕ Sizga <b>{fmt_coins(payout)} 🪙</b> qo‘shildi!{fee_txt}"

    await edit_or_answer(
        callback,
        f"✅ <b>TAKLIF QABUL QILINDI!</b>\n\n"
        f"🎴 <b>{esc(res.card.character_name)}</b> → {esc(res.buyer.display_name)} ga o‘tdi!\n"
        f"{gain_txt}{extra}",
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
            if res.buyer is not None:
                await CommunityService.notify_user(
                    session,
                    res.buyer.id,
                    "offer",
                    "❌ Taklifingiz rad etildi",
                    f"{res.card.character_name if res.card else 'Karta'} bo‘yicha taklifingiz rad etildi.",
                )
            await session.commit()
        else:
            await session.rollback()

    if res.success and res.buyer is not None:
        try:
            await callback.bot.send_message(
                res.buyer.telegram_id,
                f"❌ <b>Taklifingiz rad etildi</b>\n"
                f"🎴 {esc(res.card.character_name if res.card else 'Karta')} bo‘yicha band qilingan qiymat qaytarildi.",
            )
        except Exception:
            pass

    await callback.answer(res.message, show_alert=not res.success)
    await edit_or_answer(
        callback,
        f"❌ {res.message}",
        reply_markup=back_home_kb(callback.from_user.id, "cards"),
    )


@router.callback_query(OfferCb.filter(F.action == "cancel"))
async def cb_cancel_offer(callback: CallbackQuery, callback_data: OfferCb, db_user: User) -> None:
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
    await cb_outgoing_offers(callback, db_user)
