"""Ikki tomonlama karta almashish (/trade va Savdo tugmali qidiruvi)."""

import logging
import math

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from database.session import get_session
from handlers.common import V2UserStates
from keyboards.main_menu import back_home_kb
from models.card import Card
from models.user import User
from models.user_card import UserCard
from models.v2_features import CardTrade
from services.auction_wishlist_service import AuctionWishlistService
from services.card_level_service import CardLevelService
from services.community_service import CommunityService
from services.user_service import UserService
from utils.callback_data import NavCb, TradeCb
from utils.constants import PAGE_SIZE, get_rarity_meta
from utils.formatting import esc, header
from utils.tg_helpers import NotCommandFilter, edit_or_answer, is_private_chat, send_card_message

logger = logging.getLogger(__name__)
router = Router(name="trade")


async def _show_sender_cards_for_trade(
    target, owner_tg_id: int, sender_id: int, partner: User, page: int = 1
) -> None:
    page = max(1, int(page))
    async with get_session() as session:
        cond = [UserCard.user_id == sender_id, UserCard.is_locked.is_(False)]
        total = int((await session.execute(select(func.count(UserCard.id)).where(*cond))).scalar_one() or 0)
        my_cards = (
            await session.execute(
                select(UserCard, Card)
                .join(Card, Card.id == UserCard.card_id)
                .where(*cond)
                .order_by(Card.base_value.desc(), UserCard.id.desc())
                .offset((page - 1) * PAGE_SIZE)
                .limit(PAGE_SIZE)
            )
        ).all()

    if not my_cards:
        await edit_or_answer(
            target,
            "📭 Almashish uchun bo‘sh kartalaringiz yo‘q.",
            reply_markup=back_home_kb(owner_tg_id, "savdo"),
        )
        return

    b = InlineKeyboardBuilder()
    for uc, c in my_cards:
        meta = get_rarity_meta(c.rarity)
        lv = f" L{uc.level}" if int(getattr(uc, "level", 1) or 1) > 1 else ""
        b.row(
            InlineKeyboardButton(
                text=f"🎴 {meta.emoji} {c.character_name[:24]}{lv}"[:60],
                callback_data=TradeCb(owner_id=owner_tg_id, act="pick", tid=partner.id, ucid=uc.id).pack(),
            )
        )
    total_pages = max(1, math.ceil(total / PAGE_SIZE)) if total > 0 else 1
    if total_pages > 1:
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=TradeCb(owner_id=owner_tg_id, act="cards", tid=partner.id, p=page - 1).pack()))
        nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data=TradeCb(owner_id=owner_tg_id, act="cards", tid=partner.id, p=page).pack()))
        if page < total_pages:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=TradeCb(owner_id=owner_tg_id, act="cards", tid=partner.id, p=page + 1).pack()))
        b.row(*nav)
    b.row(
        InlineKeyboardButton(text="⬅️ Hamkor qidirish", callback_data=TradeCb(owner_id=owner_tg_id, act="v").pack()),
        InlineKeyboardButton(text="🏠 Bosh Menu", callback_data=NavCb(owner_id=owner_tg_id, target="home").pack()),
    )
    await edit_or_answer(
        target,
        f"🤝 <b>{esc(partner.display_name)}</b> ga taklif qilmoqchi bo‘lgan kartangizni tanlang:",
        reply_markup=b.as_markup(),
    )


@router.message(Command("trade"))
async def cmd_trade(message: Message, state: FSMContext, db_user: User) -> None:
    await state.clear()
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await state.set_state(V2UserStates.waiting_trade_partner)
        await state.update_data(owner_tg_id=message.from_user.id)
        await message.answer(
            f"{header('🤝 <b>KARTA ALMASHISH</b>')}\n"
            "Kim bilan karta almashmoqchisiz?\n"
            "Foydalanuvchining <b>@username</b>, <b>ismi</b> yoki <b>Telegram ID</b> raqamini yozing:\n\n"
            "<i>Bekor qilish: /cancel</i>",
            reply_markup=back_home_kb(message.from_user.id, "savdo"),
        )
        return

    async with get_session() as session:
        partner = await UserService.find_user(session, parts[1].strip())
    if partner is None or partner.id == db_user.id or partner.is_banned:
        await message.answer("❌ Foydalanuvchi topilmadi.")
        return
    await _show_sender_cards_for_trade(message, message.from_user.id, db_user.id, partner, page=1)


@router.callback_query(TradeCb.filter(F.act == "v"))
async def cb_trade_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(V2UserStates.waiting_trade_partner)
    await state.update_data(owner_tg_id=callback.from_user.id)
    await edit_or_answer(
        callback,
        f"{header('🤝 <b>KARTA ALMASHISH</b>')}\n"
        "Ikki tomonlama xavfsiz almashish — hamkoringiz karta tanlab tasdiqlamaguncha hech narsa o‘tmaydi.\n\n"
        "👤 Hamkorning <b>@username</b>, <b>ismi</b> yoki <b>Telegram ID</b> raqamini yozing:\n"
        "<i>Bekor qilish: /cancel</i>",
        reply_markup=back_home_kb(callback.from_user.id, "savdo"),
    )


@router.message(V2UserStates.waiting_trade_partner, NotCommandFilter())
async def fsm_trade_partner(message: Message, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    q = (message.text or "").strip()
    if not q:
        if is_private_chat(message):
            await message.answer("⚠️ @username, ism yoki ID yozing. Bekor qilish: /cancel")
        return

    async with get_session() as session:
        users = await UserService.search_users_list(session, q, exclude_user_id=db_user.id, limit=8)

    if not users:
        await message.answer("❌ Foydalanuvchi topilmadi. Qaytadan yozing yoki /cancel bilan bekor qiling.")
        return

    if len(users) == 1 and (q.isdigit() or q.startswith("@")):
        await state.clear()
        await _show_sender_cards_for_trade(message, message.from_user.id, db_user.id, users[0], page=1)
        return

    await state.clear()
    b = InlineKeyboardBuilder()
    for u in users:
        uname = f" (@{u.username})" if u.username else ""
        b.row(
            InlineKeyboardButton(
                text=f"👤 {u.display_name}{uname} · {u.total_cards} ta"[:58],
                callback_data=TradeCb(owner_id=message.from_user.id, act="rcp", tid=u.id).pack(),
            )
        )
    b.row(InlineKeyboardButton(text="⬅️ Qayta qidirish", callback_data=TradeCb(owner_id=message.from_user.id, act="v").pack()))
    await message.answer("👇 Almashmoqchi bo‘lgan foydalanuvchini tanlang:", reply_markup=b.as_markup())


@router.callback_query(TradeCb.filter(F.act.in_({"rcp", "cards"})))
async def cb_trade_pick_partner(callback: CallbackQuery, callback_data: TradeCb, db_user: User) -> None:
    async with get_session() as session:
        partner = await session.get(User, callback_data.tid)
    if partner is None or partner.id == db_user.id or partner.is_banned:
        await callback.answer("❌ Foydalanuvchi topilmadi.", show_alert=True)
        return
    await _show_sender_cards_for_trade(
        callback, callback.from_user.id, db_user.id, partner, page=max(1, callback_data.p)
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
        uc_init = await session.get(UserCard, trade.initiator_uc_id)
        card_init = await session.get(Card, uc_init.card_id) if uc_init else None
        p_cards = (
            await session.execute(
                select(UserCard, Card)
                .join(Card, Card.id == UserCard.card_id)
                .where(UserCard.user_id == partner.id, UserCard.is_locked.is_(False))
                .order_by(Card.base_value.desc())
                .limit(15)
            )
        ).all()
        await CommunityService.notify_user(
            session,
            partner.id,
            "trade",
            "🤝 Yangi almashuv taklifi!",
            f"{db_user.display_name} sizga {card_init.character_name if card_init else 'karta'} kartasini almashishni taklif qildi.",
        )
        await session.commit()

    if partner:
        rows = [
            [
                InlineKeyboardButton(
                    text=f"🔁 {c.character_name[:24]} ({get_rarity_meta(c.rarity).name})"[:58],
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
        kb = InlineKeyboardMarkup(inline_keyboard=rows)
        try:
            if card_init is not None:
                meta = get_rarity_meta(card_init.rarity)
                lv = int(getattr(uc_init, "level", 1) or 1)
                await send_card_message(
                    callback.bot,
                    partner.telegram_id,
                    card_init,
                    f"🤝 <b>{esc(db_user.display_name)}</b> sizga karta almashishni taklif qildi!\n\n"
                    f"🎴 Taklif qilingan karta: {meta.emoji} <b>{esc(card_init.character_name)}</b> "
                    f"({esc(card_init.anime_name)}) · Lvl {lv}\n\n"
                    "Almashtirmoqchi bo‘lgan kartangizni tanlang:",
                    reply_markup=kb,
                )
            else:
                await callback.bot.send_message(
                    partner.telegram_id,
                    f"🤝 <b>{esc(db_user.display_name)}</b> sizga karta almashishni taklif qildi!\n"
                    "Almashtirmoqchi bo‘lgan kartangizni tanlang:",
                    reply_markup=kb,
                )
        except Exception as exc:
            logger.debug("Trade DM to partner failed: %s", exc)

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
    initiator = None
    async with get_session() as session:
        tr = await session.get(CardTrade, callback_data.tid)
        if tr:
            initiator = await session.get(User, tr.initiator_user_id)
        ok, msg = await AuctionWishlistService.confirm_trade(
            session, callback_data.tid, db_user.id, callback_data.ucid
        )
        if ok:
            if initiator:
                await CommunityService.notify_user(
                    session,
                    initiator.id,
                    "trade",
                    "🤝 Almashuv yakunlandi!",
                    f"{db_user.display_name} bilan karta almashuvingiz muvaffaqiyatli yakunlandi!",
                )
            await session.commit()
        else:
            await session.rollback()

    await callback.answer(msg[:190], show_alert=True)
    if ok:
        if initiator:
            try:
                await callback.bot.send_message(
                    initiator.telegram_id,
                    f"🤝 <b>Almashuv yakunlandi!</b>\n\n{esc(db_user.display_name)} taklifingizni qabul qildi.\n{msg}",
                )
            except Exception:
                pass
        await edit_or_answer(callback, msg, reply_markup=back_home_kb(callback.from_user.id, "cards"))


@router.callback_query(TradeCb.filter(F.act == "reject"))
async def cb_trade_reject(callback: CallbackQuery, callback_data: TradeCb, db_user: User) -> None:
    initiator = None
    async with get_session() as session:
        tr = await session.get(CardTrade, callback_data.tid)
        if tr and tr.partner_user_id == db_user.id and tr.status == "pending_pick":
            tr.status = "rejected"
            uc = await session.get(UserCard, tr.initiator_uc_id)
            if uc:
                uc.is_locked = False
                uc.card_state = "OWNED"
            initiator = await session.get(User, tr.initiator_user_id)
            if initiator:
                await CommunityService.notify_user(
                    session,
                    initiator.id,
                    "trade",
                    "❌ Almashuv rad etildi",
                    f"{db_user.display_name} almashuv taklifingizni rad etdi.",
                )
            await session.commit()

    if initiator:
        try:
            await callback.bot.send_message(
                initiator.telegram_id,
                f"❌ <b>{esc(db_user.display_name)}</b> karta almashish taklifingizni rad etdi. Kartangiz qulfdan chiqarildi.",
            )
        except Exception:
            pass
    await callback.answer("❌ Almashuv rad etildi.")
    await edit_or_answer(callback, "❌ Almashuv rad etildi.", reply_markup=back_home_kb(callback.from_user.id, "savdo"))


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
