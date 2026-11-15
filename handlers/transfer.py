import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
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
from utils.callback_data import TransferCb
from utils.formatting import esc, fmt_coins
from utils.tg_helpers import edit_or_answer

logger = logging.getLogger(__name__)
router = Router(name="transfer")


@router.message(Command("give"))
async def cmd_give(message: Message, db_user: User) -> None:
    """
    Usage:
      /give @username <card_id_or_code> [price]
      OR reply to a user's message in a group:
      /give <card_id_or_code> [price]
    """
    parts = (message.text or "").split()
    reply_msg = message.reply_to_message
    if reply_msg is not None and getattr(reply_msg, "forum_topic_created", None) is not None:
        reply_msg = None
    reply_user = reply_msg.from_user if reply_msg else None

    target_query = ""
    card_ref = ""
    price = 0

    if reply_user is not None and not reply_user.is_bot and len(parts) >= 2 and not parts[1].startswith("@"):
        target_query = str(reply_user.id)
        card_ref = parts[1]
        if len(parts) >= 3:
            if not parts[2].isdigit():
                await message.answer("⚠️ Narx faqat musbat butun son bo‘lishi kerak.")
                return
            price = int(parts[2])
    elif len(parts) >= 3:
        target_query = parts[1]
        card_ref = parts[2]
        if len(parts) >= 4:
            if not parts[3].isdigit():
                await message.answer("⚠️ Narx faqat musbat butun son bo‘lishi kerak.")
                return
            price = int(parts[3])
    else:
        await message.answer(
            "🤝 <b>Karta transfer qilish:</b>\n\n"
            "Bepul berish: <code>/give @username CRD-0001</code>\n"
            "Pullik berish: <code>/give @username CRD-0001 1000</code>\n"
            "Yoki guruhda xabarga reply qilib: <code>/give CRD-0001</code>"
        )
        return

    async with get_session() as session:
        if reply_user is not None and target_query == str(reply_user.id):
            recipient = await UserService.upsert_user(
                session,
                telegram_id=reply_user.id,
                username=reply_user.username,
                first_name=reply_user.first_name or "Collector",
            )
        else:
            recipient = await UserService.find_user(session, target_query)
        if recipient is None:
            await message.answer(
                "❌ Foydalanuvchi topilmadi. U botdan kamida bir marta foydalangan bo‘lishi kerak (yoki xabariga reply qiling)."
            )
            return

        # Resolve either direct UserCard.id or Card.card_code
        uc: UserCard | None = None
        if card_ref.isdigit():
            cand = await session.get(UserCard, int(card_ref))
            if cand is not None and cand.user_id == db_user.id and not cand.is_locked:
                uc = cand
        else:
            card = (
                await session.execute(
                    select(Card).where(func.lower(Card.card_code) == card_ref.strip().lower())
                )
            ).scalar_one_or_none()
            if card is not None:
                uc = await CollectionService.get_unlocked_copy(session, db_user.id, card.id)

        if uc is None:
            await message.answer(
                "❌ Sizda bunday bo‘sh karta topilmadi (yoki karta marketda/transferda band)."
            )
            return

        res = await TransferService.create_pending_transfer(
            session=session,
            from_user_id=db_user.id,
            to_user_id=recipient.id,
            user_card_id=uc.id,
            price=price,
        )
        if res.success:
            await session.commit()
        else:
            await session.rollback()

    if not res.success:
        await message.answer(f"❌ {res.message}")
        return

    if price == 0:
        confirm_actor_tg_id = db_user.telegram_id
        text = (
            f"🎁 <b>FREE TRANSFER TASDIQLASH</b>\n\n"
            f"🎴 Karta: <b>{esc(res.card.character_name)}</b> ({esc(res.card.card_code)})\n"
            f"👤 Qabul qiluvchi: {esc(res.to_user.display_name)}\n\n"
            f"Ushbu kartani {esc(res.to_user.display_name)} ga berishni tasdiqlaysizmi?"
        )
    else:
        confirm_actor_tg_id = res.to_user.telegram_id
        text = (
            f"💰 <b>PAID TRANSFER TAKLIFI</b>\n\n"
            f"🎴 Karta: <b>{esc(res.card.character_name)}</b> ({esc(res.card.card_code)})\n"
            f"👤 Yuboruvchi: {esc(res.from_user.display_name)}\n"
            f"👤 Qabul qiluvchi: {esc(res.to_user.display_name)}\n"
            f"🪙 Narxi: <b>{fmt_coins(price)} coins</b>\n\n"
            f"{esc(res.to_user.display_name)}, ushbu kartani {fmt_coins(price)} coins ga olishni tasdiqlaysizmi?"
        )

    await message.answer(
        text,
        reply_markup=transfer_confirm_kb(confirm_actor_tg_id, res.transfer.id),
    )


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
