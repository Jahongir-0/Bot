"""Karta sovg‘a qilish va pullik uzatish (/give va karta tafsilotidan tugmali flow)."""

import logging
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ForceReply, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from database.session import get_session
from keyboards.main_menu import back_home_kb
from keyboards.market import transfer_confirm_kb
from models.card import Card
from models.user import User
from models.user_card import UserCard
from services.collection_service import CollectionService
from services.community_service import CommunityService
from services.settings_service import SettingsService
from services.transfer_service import TransferService
from services.user_service import InsufficientCoinsError, UserService
from utils.callback_data import GiveCb, NavCb, TransferCb
from utils.constants import MAX_CARD_PRICE, MIN_CARD_PRICE, get_rarity_meta
from utils.formatting import esc, fmt_coins
from utils.tg_helpers import NotCommandFilter, edit_or_answer, is_private_chat, send_card_message

logger = logging.getLogger(__name__)
router = Router(name="transfer")


CODE_RE = re.compile(r"^crd-\d+$", re.IGNORECASE)


class GiveStates(StatesGroup):
    waiting_recipient = State()
    waiting_price = State()
    waiting_name = State()
    choosing = State()


GIVE_USAGE = (
    "🤝 <b>Karta berish:</b>\n\n"
    "1) <code>/give @username</code> — keyin karta <b>ismini</b> yozasiz, "
    "sizdagi mos kartalar ro‘yxatidan tanlaysiz.\n"
    "2) Tezkor: <code>/give @username Gojo</code>\n"
    "3) Pullik: <code>/give @username Gojo 1000</code>\n"
    "4) Guruhda xabarga <b>reply</b> qilib: <code>/give Gojo</code>\n\n"
    "<i>Qabul qiluvchi botdan kamida bir marta foydalangan bo‘lishi kerak. "
    "Agar unda bu karta allaqachon bo‘lsa, nusxa birlashib uning kartasiga +1 daraja qo‘shiladi.</i>"
)


async def _create_transfer_and_text(db_user: User, recipient_id: int, user_card_id: int, price: int):
    """Transfer so‘rovini yaratadi va tasdiqlash matni/tugmalarini qaytaradi: (res, text, kb)."""
    async with get_session() as session:
        res = await TransferService.create_pending_transfer(
            session=session,
            from_user_id=db_user.id,
            to_user_id=recipient_id,
            user_card_id=user_card_id,
            price=price,
        )
        comm_cfg = await SettingsService.get_commission_config(session) if price > 0 else {}
        if res.success:
            await session.commit()
        else:
            await session.rollback()

    if not res.success:
        return res, f"❌ {res.message}", None

    if price == 0:
        confirm_actor_tg_id = db_user.telegram_id
        text = (
            f"🎁 <b>BEPUL YUBORISHNI TASDIQLANG</b>\n\n"
            f"🎴 Karta: <b>{esc(res.card.character_name)}</b> ({esc(get_rarity_meta(res.card.rarity).name)} · {esc(res.card.card_code)})\n"
            f"👤 Qabul qiluvchi: {esc(res.to_user.display_name)}\n\n"
            f"Ushbu kartani {esc(res.to_user.display_name)} ga berishni tasdiqlaysizmi?"
        )
    else:
        confirm_actor_tg_id = res.to_user.telegram_id
        fee_coins, fee_pct = SettingsService.compute_commission(price, comm_cfg, "card_gift")
        seller_gets = max(1, price - fee_coins)
        fee_txt = f"\n💸 Komissiya ({fee_pct}%): -{fmt_coins(fee_coins)} 🪙 → Sotuvchi: {fmt_coins(seller_gets)} 🪙" if fee_coins else ""
        text = (
            f"💰 <b>PULLIK SOTUV TAKLIFI</b>\n\n"
            f"🎴 Karta: <b>{esc(res.card.character_name)}</b> ({esc(get_rarity_meta(res.card.rarity).name)} · {esc(res.card.card_code)})\n"
            f"👤 Yuboruvchi: {esc(res.from_user.display_name)}\n"
            f"👤 Qabul qiluvchi: {esc(res.to_user.display_name)}\n"
            f"🪙 Narxi: <b>{fmt_coins(price)} coins</b>{fee_txt}\n\n"
            f"{esc(res.to_user.display_name)}, ushbu kartani {fmt_coins(price)} coins ga olishni tasdiqlaysizmi?"
        )
    return res, text, transfer_confirm_kb(confirm_actor_tg_id, res.transfer.id)


async def _show_matches(target: Message, state: FSMContext, db_user: User, query: str) -> None:
    """Jo‘natuvchidagi shu ismdagi kartalar ro‘yxati (rarity bilan) — o‘zi tanlaydi."""
    data = await state.get_data()
    to_user_id = int(data["to_user_id"])
    async with get_session() as session:
        recipient = await session.get(User, to_user_id)
        matches = await CollectionService.find_cards_by_name(session, db_user.id, query, limit=8)
        recipient_has = set()
        if matches:
            rows = await session.execute(
                select(UserCard.card_id).where(
                    UserCard.user_id == to_user_id,
                    UserCard.card_id.in_([c.id for _, c in matches]),
                )
            )
            recipient_has = {r[0] for r in rows.all()}

    if not matches:
        await target.answer(
            f"❌ Sizda «{esc(query)}» nomli karta topilmadi.\n"
            "Boshqa ism yozing yoki /cancel bilan bekor qiling."
        )
        return

    builder = InlineKeyboardBuilder()
    notes = []
    for uc, card in matches:
        meta = get_rarity_meta(card.rarity)
        tag = " (+1 lvl)" if card.id in recipient_has else ""
        label = f"{meta.emoji} {meta.name} · {card.character_name} ({card.card_code}){tag}"[:60]
        if uc.is_locked:
            notes.append(f"🔒 {esc(card.character_name)} ({esc(meta.name)}) — marketda/almashuvda band")
            continue
        builder.row(
            InlineKeyboardButton(
                text=label,
                callback_data=GiveCb(owner_id=db_user.telegram_id, action="pick", user_card_id=uc.id).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="❌ BEKOR", callback_data=GiveCb(owner_id=db_user.telegram_id, action="cancel").pack()
        )
    )
    await state.set_state(GiveStates.choosing)
    price = int(data.get("price", 0))
    price_line = f"\n🪙 Narx: <b>{fmt_coins(price)} coins</b>" if price else "\n🎁 Bepul sovg‘a"
    text = (
        f"🤝 <b>{esc(recipient.display_name)}</b> ga qaysi kartani berasiz?{price_line}\n"
        f"🔍 Qidiruv: «{esc(query)}»\n\n"
        "Pastdagi ro‘yxatdan tanlang (darajasi yonida ko‘rsatilgan):"
    )
    if notes:
        text += "\n\n" + "\n".join(notes)
    await target.answer(text, reply_markup=builder.as_markup())


@router.message(Command("give"))
async def cmd_give(message: Message, state: FSMContext, db_user: User) -> None:
    parts = (message.text or "").split()
    reply_user = message.reply_to_message.from_user if message.reply_to_message else None

    recipient_query = ""
    rest: list = []
    if reply_user is not None and not reply_user.is_bot and not (len(parts) >= 2 and parts[1].startswith("@")):
        recipient_query = str(reply_user.id)
        rest = parts[1:]
    elif len(parts) >= 2 and (parts[1].startswith("@") or parts[1].isdigit()):
        recipient_query = parts[1]
        rest = parts[2:]
    else:
        await message.answer(GIVE_USAGE)
        return

    price = 0
    if len(rest) >= 2 and rest[-1].isdigit():
        price = int(rest[-1])
        rest = rest[:-1]

    async with get_session() as session:
        if reply_user is not None and recipient_query == str(reply_user.id):
            recipient = await UserService.upsert_user(
                session,
                telegram_id=reply_user.id,
                username=reply_user.username,
                first_name=reply_user.first_name or "Collector",
            )
            await session.commit()
        else:
            recipient = await UserService.find_user(session, recipient_query)
    if recipient is None:
        await message.answer(
            "❌ Foydalanuvchi topilmadi. U botdan kamida bir marta foydalangan bo‘lishi kerak (yoki xabariga reply qiling)."
        )
        return
    if recipient.id == db_user.id:
        await message.answer("❌ O‘zingizga karta bera olmaysiz.")
        return

    if len(rest) == 1 and (CODE_RE.match(rest[0]) or rest[0].isdigit()):
        await _legacy_give(message, db_user, recipient, rest[0], price)
        return

    await state.clear()
    await state.update_data(
        to_user_id=recipient.id,
        from_tg=db_user.telegram_id,
        price=price,
    )
    name = " ".join(rest).strip()
    if name:
        await _show_matches(message, state, db_user, name)
        return

    await state.set_state(GiveStates.waiting_name)
    await message.reply(
        f"🎴 <b>{esc(recipient.display_name)}</b> ga qaysi kartani berasiz?\n"
        "Karta <b>ismini</b> yozing (masalan: <code>Gojo</code>). Bekor qilish: /cancel",
        reply_markup=ForceReply(selective=True, input_field_placeholder="Karta ismi..."),
    )


@router.callback_query(NavCb.filter(F.target == "give_card"))
async def cb_give_start(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    await state.clear()
    await state.set_state(GiveStates.waiting_recipient)
    await state.update_data(from_tg=db_user.telegram_id, price=0, preset_uc_id=0, flow_mode="gift")
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data=GiveCb(owner_id=callback.from_user.id, action="cancel", user_card_id=0).pack()))
    await edit_or_answer(
        callback,
        "🎁 <b>SOVG‘A BERISH</b>\n"
        "━━━━━━━━━━━━━━━\n"
        "Do‘stingizga kartangizni sovg‘a qiling.\n\n"
        "1️⃣ Qabul qiluvchining <b>@username</b>, <b>ismi</b> yoki <b>ID</b> raqamini yozing\n"
        "2️⃣ Karta <b>ismini</b> yozing\n"
        "3️⃣ Ro‘yxatdan kartani tanlang va tasdiqlang\n\n"
        "<i>Qabul qiluvchi botdan kamida bir marta foydalangan bo‘lishi kerak. "
        "Pullik sotish uchun: <code>/give @username Gojo 1000</code></i>",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(GiveCb.filter(F.action.in_({"gift_uc", "sell_uc"})))
async def cb_give_preset_uc(
    callback: CallbackQuery,
    callback_data: GiveCb,
    state: FSMContext,
    db_user: User,
) -> None:
    uc_id = int(callback_data.user_card_id)
    async with get_session() as session:
        uc = await session.get(UserCard, uc_id)
        card = await session.get(Card, uc.card_id) if uc else None
    if uc is None or uc.user_id != db_user.id or card is None:
        await callback.answer("❌ Karta topilmadi.", show_alert=True)
        return
    if uc.is_locked:
        await callback.answer("🔒 Bu karta hozir band!", show_alert=True)
        return

    mode = "sell" if callback_data.action == "sell_uc" else "gift"
    await state.clear()
    await state.set_state(GiveStates.waiting_recipient)
    await state.update_data(
        from_tg=db_user.telegram_id,
        price=0,
        preset_uc_id=uc.id,
        flow_mode=mode,
    )
    meta = get_rarity_meta(card.rarity)
    title = "💰 <b>DO‘STGA PULLIK YUBORISH</b>" if mode == "sell" else "🎁 <b>KARTA SOVG‘A QILISH</b>"
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data=GiveCb(owner_id=callback.from_user.id, action="cancel").pack()))
    await edit_or_answer(
        callback,
        f"{title}\n"
        f"🎴 Karta: {meta.emoji} <b>{esc(card.character_name)}</b> ({esc(card.anime_name)})\n\n"
        "👤 Qabul qiluvchining <b>@username</b>, <b>ismi</b> yoki <b>Telegram ID</b> raqamini yozing:\n"
        "<i>Bekor qilish: /cancel</i>",
        reply_markup=kb.as_markup(),
    )


async def _after_recipient_chosen(target, state: FSMContext, db_user: User, recipient: User) -> None:
    data = await state.get_data()
    preset_uc_id = int(data.get("preset_uc_id") or 0)
    flow_mode = data.get("flow_mode", "gift")
    await state.update_data(to_user_id=recipient.id, from_tg=db_user.telegram_id)

    if preset_uc_id > 0:
        if flow_mode == "sell":
            await state.set_state(GiveStates.waiting_price)
            kb = InlineKeyboardBuilder()
            kb.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data=GiveCb(owner_id=db_user.telegram_id, action="cancel").pack()))
            await edit_or_answer(
                target,
                f"👤 Qabul qiluvchi: <b>{esc(recipient.display_name)}</b>\n\n"
                f"💰 Karta narxini kiriting (<b>{MIN_CARD_PRICE:,}</b> – <b>{MAX_CARD_PRICE:,}</b> 🪙):\n"
                "<i>Bekor qilish: /cancel</i>",
                reply_markup=kb.as_markup(),
            )
            return

        res, text, kb = await _create_transfer_and_text(db_user, recipient.id, preset_uc_id, 0)
        await state.clear()
        if not res.success:
            await edit_or_answer(target, text, reply_markup=back_home_kb(db_user.telegram_id, "cards"))
            return
        await edit_or_answer(target, text, reply_markup=kb)
        return

    await state.set_state(GiveStates.waiting_name)
    msg_target = target.message if isinstance(target, CallbackQuery) else target
    if msg_target is not None:
        await msg_target.answer(
            f"🎴 <b>{esc(recipient.display_name)}</b> ga qaysi kartani berasiz?\n"
            "Karta <b>ismini</b> yozing (masalan: <code>Gojo</code>). Bekor qilish: /cancel",
            reply_markup=ForceReply(selective=True, input_field_placeholder="Karta ismi..."),
        )


@router.message(GiveStates.waiting_recipient, NotCommandFilter())
async def fsm_give_recipient(message: Message, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    if data.get("from_tg") != message.from_user.id:
        return
    raw = (message.text or "").strip()
    if not raw:
        if is_private_chat(message):
            await message.answer("⚠️ @username, ism yoki ID raqam yozing. Bekor qilish: /cancel")
        return

    async with get_session() as session:
        users = await UserService.search_users_list(session, raw, exclude_user_id=db_user.id, limit=8)

    if not users:
        await message.answer("❌ Foydalanuvchi topilmadi. U botdan kamida bir marta foydalangan bo‘lishi kerak. Qayta yozing yoki /cancel.")
        return

    if len(users) == 1 and (raw.isdigit() or raw.startswith("@")):
        await _after_recipient_chosen(message, state, db_user, users[0])
        return

    b = InlineKeyboardBuilder()
    for u in users:
        uname = f" (@{u.username})" if u.username else ""
        b.row(
            InlineKeyboardButton(
                text=f"👤 {u.display_name}{uname}"[:58],
                callback_data=GiveCb(owner_id=message.from_user.id, action="rcp", user_card_id=u.id).pack(),
            )
        )
    b.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data=GiveCb(owner_id=message.from_user.id, action="cancel").pack()))
    await message.answer("👇 Qabul qiluvchini tanlang:", reply_markup=b.as_markup())


@router.callback_query(GiveCb.filter(F.action == "rcp"))
async def cb_give_choose_recipient(
    callback: CallbackQuery,
    callback_data: GiveCb,
    state: FSMContext,
    db_user: User,
) -> None:
    async with get_session() as session:
        recipient = await session.get(User, callback_data.user_card_id)
    if recipient is None or recipient.id == db_user.id or recipient.is_banned:
        await callback.answer("❌ Foydalanuvchi topilmadi.", show_alert=True)
        return
    await _after_recipient_chosen(callback, state, db_user, recipient)


@router.message(GiveStates.waiting_price, NotCommandFilter())
async def fsm_give_price(message: Message, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    if data.get("from_tg") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit():
        if is_private_chat(message):
            await message.answer("⚠️ Faqat butun coin sonini kiriting. Bekor qilish: /cancel")
        return
    price = int(raw)
    if not (MIN_CARD_PRICE <= price <= MAX_CARD_PRICE):
        await message.answer(f"⚠️ Narx {MIN_CARD_PRICE:,} dan {MAX_CARD_PRICE:,} 🪙 gacha bo‘lishi kerak.")
        return

    to_user_id = int(data["to_user_id"])
    preset_uc_id = int(data["preset_uc_id"])
    res, text, kb = await _create_transfer_and_text(db_user, to_user_id, preset_uc_id, price)
    await state.clear()
    if not res.success:
        await message.answer(text, reply_markup=back_home_kb(message.from_user.id, "cards"))
        return

    # Pullik sotuv taklifini qabul qiluvchining DM xabariga yuboramiz
    if res.to_user is not None:
        try:
            await send_card_message(message.bot, res.to_user.telegram_id, res.card, text, reply_markup=kb)
            await message.answer(
                f"✅ Pullik taklif <b>{esc(res.to_user.display_name)}</b> ga yuborildi!\n"
                f"💰 Narxi: <b>{fmt_coins(price)} 🪙</b>\n\n"
                "<i>Ushbu taklif 15 daqiqa amal qiladi.</i>",
                reply_markup=back_home_kb(message.from_user.id, "cards"),
            )
            return
        except Exception:
            pass
    await message.answer(text, reply_markup=kb)


@router.message(GiveStates.waiting_name, NotCommandFilter())
async def fsm_give_name(message: Message, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    if data.get("from_tg") != message.from_user.id:
        return
    query = (message.text or "").strip()
    if len(query) < 2:
        if is_private_chat(message):
            await message.answer("⚠️ Karta ismini yozing (kamida 2 harf). Bekor qilish: /cancel")
        return
    await _show_matches(message, state, db_user, query[:40])


@router.callback_query(GiveCb.filter(F.action == "cancel"))
async def cb_give_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_or_answer(callback, "❌ Karta berish bekor qilindi.", reply_markup=back_home_kb(callback.from_user.id))


@router.callback_query(GiveCb.filter(F.action == "pick"))
async def cb_give_pick(callback: CallbackQuery, callback_data: GiveCb, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    to_user_id = data.get("to_user_id")
    if not to_user_id or data.get("from_tg") != callback.from_user.id:
        await callback.answer("Muddat tugagan. /give ni qaytadan yozing.", show_alert=True)
        return

    res, text, kb = await _create_transfer_and_text(
        db_user, int(to_user_id), callback_data.user_card_id, int(data.get("price", 0))
    )
    if not res.success:
        await callback.answer(res.message, show_alert=True)
        return
    await state.clear()
    await edit_or_answer(callback, text, reply_markup=kb)


async def _legacy_give(message: Message, db_user: User, recipient: User, card_ref: str, price: int) -> None:
    async with get_session() as session:
        uc = None
        if card_ref.isdigit():
            cand = await session.get(UserCard, int(card_ref))
            if cand is not None and cand.user_id == db_user.id and not cand.is_locked:
                uc = cand
        else:
            card = (
                await session.execute(select(Card).where(func.lower(Card.card_code) == card_ref.strip().lower()))
            ).scalar_one_or_none()
            if card is not None:
                uc = await CollectionService.get_unlocked_copy(session, db_user.id, card.id)

    if uc is None:
        await message.answer("❌ Sizda bunday bo‘sh karta topilmadi (yoki karta marketda/transferda band).")
        return
    res, text, kb = await _create_transfer_and_text(db_user, recipient.id, uc.id, price)
    await message.answer(text, reply_markup=kb)


@router.callback_query(TransferCb.filter(F.action == "confirm"))
async def cb_transfer_confirm(callback: CallbackQuery, callback_data: TransferCb, db_user: User) -> None:
    async with get_session() as session:
        try:
            res = await TransferService.confirm_transfer(
                session=session,
                transfer_id=callback_data.transfer_id,
                actor_user_id=db_user.id,
            )
            if res.success:
                if res.to_user and res.from_user:
                    other_id = res.from_user.id if db_user.id == res.to_user.id else res.to_user.id
                    await CommunityService.notify_user(
                        session,
                        other_id,
                        "transfer",
                        "🎁 Karta topshirildi!",
                        f"{res.card.character_name} kartasi {res.to_user.display_name} ga muvaffaqiyatli o‘tkazildi.",
                    )
                await session.commit()
            else:
                await session.rollback()
        except InsufficientCoinsError:
            await session.rollback()
            await callback.answer("❌ Transfer uchun coinlaringiz yetarli emas!", show_alert=True)
            return
        except Exception as exc:
            await session.rollback()
            logger.exception("Transfer confirm failed: %s", exc)
            await callback.answer("❌ Transferda xatolik yuz berdi.", show_alert=True)
            return

    if not res.success:
        await callback.answer(f"❌ {res.message}", show_alert=True)
        return

    if res.to_user and res.from_user:
        other_tg = res.from_user.telegram_id if db_user.id == res.to_user.id else res.to_user.telegram_id
        try:
            await callback.bot.send_message(
                other_tg,
                f"✅ <b>Transfer yakunlandi!</b>\n"
                f"🎴 <b>{esc(res.card.character_name)}</b> → 👤 {esc(res.to_user.display_name)}",
            )
        except Exception:
            pass

    extra = ""
    if res.is_duplicate:
        if res.maxed:
            extra = f"\n👑 Qabul qiluvchida karta maksimal darajada — +{fmt_coins(res.refund)} 🪙 qaytarildi!"
        else:
            extra = f"\n🔁 Qabul qiluvchida bu karta bor edi — daraja ko‘tarildi: <b>{res.level_before} → {res.level_after}</b>!"
    fee_note = f"\n💸 Komissiya ({res.fee_pct}%): {fmt_coins(res.fee_coins)} 🪙" if res.fee_coins else ""

    await edit_or_answer(
        callback,
        f"✅ <b>TRANSFER YAKUNLANDI!</b>\n\n"
        f"🎴 <b>{esc(res.card.character_name)}</b> → {esc(res.to_user.display_name)} ga o‘tkazildi!{fee_note}{extra}",
        reply_markup=back_home_kb(callback.from_user.id),
    )


@router.callback_query(TransferCb.filter(F.action == "cancel"))
async def cb_transfer_cancel(callback: CallbackQuery, callback_data: TransferCb, db_user: User) -> None:
    async with get_session() as session:
        res = await TransferService.cancel_transfer(
            session=session,
            transfer_id=callback_data.transfer_id,
            actor_user_id=db_user.id,
        )
        if res.success:
            await session.commit()
        else:
            await session.rollback()

    if res.success and res.from_user and res.to_user:
        other_tg = res.from_user.telegram_id if db_user.id == res.to_user.id else res.to_user.telegram_id
        try:
            await callback.bot.send_message(other_tg, "❌ Karta transferi bekor qilindi.")
        except Exception:
            pass

    await edit_or_answer(
        callback,
        f"❌ {res.message}",
        reply_markup=back_home_kb(callback.from_user.id),
    )
