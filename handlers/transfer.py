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
from services.transfer_service import TransferService
from services.user_service import InsufficientCoinsError, UserService
from utils.callback_data import GiveCb, TransferCb
from utils.constants import get_rarity_meta
from utils.formatting import esc, fmt_coins
from utils.tg_helpers import NotCommandFilter, edit_or_answer, is_private_chat

logger = logging.getLogger(__name__)
router = Router(name="transfer")


CODE_RE = re.compile(r"^crd-\d+$", re.IGNORECASE)


class GiveStates(StatesGroup):
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
    "Bir xil kartadan faqat bitta nusxa bo‘lishi mumkin.</i>"
)


async def _create_transfer_and_text(db_user: User, recipient_id: int, user_card_id: int, price: int):
    """Transfer so'rovini yaratadi va tasdiqlash matni/tugmalarini qaytaradi: (res, text, kb)."""
    async with get_session() as session:
        res = await TransferService.create_pending_transfer(
            session=session,
            from_user_id=db_user.id,
            to_user_id=recipient_id,
            user_card_id=user_card_id,
            price=price,
        )
        if res.success:
            await session.commit()
        else:
            await session.rollback()

    if not res.success:
        return res, f"❌ {res.message}", None

    if price == 0:
        confirm_actor_tg_id = db_user.telegram_id
        text = (
            f"🎁 <b>FREE TRANSFER TASDIQLASH</b>\n\n"
            f"🎴 Karta: <b>{esc(res.card.character_name)}</b> ({esc(res.card.rarity)} · {esc(res.card.card_code)})\n"
            f"👤 Qabul qiluvchi: {esc(res.to_user.display_name)}\n\n"
            f"Ushbu kartani {esc(res.to_user.display_name)} ga berishni tasdiqlaysizmi?"
        )
    else:
        confirm_actor_tg_id = res.to_user.telegram_id
        text = (
            f"💰 <b>PAID TRANSFER TAKLIFI</b>\n\n"
            f"🎴 Karta: <b>{esc(res.card.character_name)}</b> ({esc(res.card.rarity)} · {esc(res.card.card_code)})\n"
            f"👤 Yuboruvchi: {esc(res.from_user.display_name)}\n"
            f"👤 Qabul qiluvchi: {esc(res.to_user.display_name)}\n"
            f"🪙 Narxi: <b>{fmt_coins(price)} coins</b>\n\n"
            f"{esc(res.to_user.display_name)}, ushbu kartani {fmt_coins(price)} coins ga olishni tasdiqlaysizmi?"
        )
    return res, text, transfer_confirm_kb(confirm_actor_tg_id, res.transfer.id)


async def _show_matches(target: Message, state: FSMContext, db_user: User, query: str) -> None:
    """Jo'natuvchidagi shu ismdagi kartalar ro'yxati (rarity bilan) — o'zi tanlaydi."""
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
        label = f"{meta.emoji} {card.rarity} · {card.character_name} ({card.card_code})"[:60]
        if uc.is_locked:
            notes.append(f"🔒 {esc(card.character_name)} ({esc(card.rarity)}) — marketda/transferda band")
            continue
        if card.id in recipient_has:
            notes.append(f"🚫 {esc(card.character_name)} ({esc(card.rarity)}) — qabul qiluvchida allaqachon bor")
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

    # Eski usul (karta kodi yoki nusxa ID) ham ishlayveradi
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

    await edit_or_answer(
        callback,
        f"✅ <b>TRANSFER YAKUNLANDI!</b>\n\n"
        f"🎴 <b>{esc(res.card.character_name)}</b> → {esc(res.to_user.display_name)} ga o‘tkazildi!",
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

    await edit_or_answer(
        callback,
        f"❌ {res.message}",
        reply_markup=back_home_kb(callback.from_user.id),
    )
