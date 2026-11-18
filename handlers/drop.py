import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest

try:  # aiogram 3.x: SkipHandler joylashuvi versiyaga qarab farq qiladi
    from aiogram.dispatcher.event.bases import SkipHandler
except ImportError:  # pragma: no cover
    from aiogram.exceptions import SkipHandler  # type: ignore
from aiogram.types import CallbackQuery, Message

from database.session import get_session
from models.user import User
from services.drop_service import DropService
from utils.callback_data import DropCb
from utils.formatting import esc, format_drop_claimed_message

logger = logging.getLogger(__name__)
router = Router(name="drop")


async def _announce_drop_claimed(msg: Message, result) -> None:
    claimed_text = format_drop_claimed_message(
        card=result.card,
        winner=result.winner,
        reward_coins=result.reward_coins,
    )
    try:
        if msg.photo:
            await msg.edit_caption(caption=claimed_text, reply_markup=None)
        else:
            await msg.edit_text(text=claimed_text, reply_markup=None)
    except TelegramBadRequest as exc:
        logger.debug("Could not edit drop message after claim: %s", exc)


@router.callback_query(DropCb.filter(F.action == "claim"))
async def cb_claim_drop(callback: CallbackQuery, callback_data: DropCb, db_user: User) -> None:
    """Eski tugmali droplar uchun backward compatibility."""
    async with get_session() as session:
        result = await DropService.claim_drop(
            session=session,
            drop_id=callback_data.drop_id,
            claimer_user_id=db_user.id,
        )
        if result.success:
            await session.commit()
        else:
            await session.rollback()

    if not result.success:
        if result.reason == "already_claimed":
            await callback.answer("⚡ Kechikdingiz! Bu karta allaqachon olingan.", show_alert=True)
        elif result.reason == "already_owned":
            await callback.answer("📌 Bu kartadan sizda bor! Bir xil kartadan faqat bitta nusxa olish mumkin.", show_alert=True)
        elif result.reason == "locked":
            await callback.answer("⏳ Boshqa foydalanuvchi hozirgina oldi!", show_alert=True)
        else:
            await callback.answer("❌ Karta topilmadi.", show_alert=True)
        return

    if callback.message is not None:
        await _announce_drop_claimed(callback.message, result)
        await callback.message.reply(
            f"🎉 {esc(result.winner.display_name)} — <b>{esc(result.card.character_name)}</b> kartasini oldi!"
        )
    await callback.answer(f"🎉 {result.card.character_name} kolleksiyangizga qo‘shildi!", show_alert=True)


@router.message(F.chat.type.in_({"group", "supergroup"}), F.reply_to_message, F.text)
async def msg_reply_claim_drop(message: Message, db_user: User) -> None:
    """V2 Asosiy Drop Mexanikasi: guruhda drop xabariga personaj ismini REPLY qilib yozish orqali olish."""
    txt = (message.text or "").strip()
    if not txt or txt.startswith("/"):
        raise SkipHandler()
    replied = message.reply_to_message
    if replied is None or replied.from_user is None or not replied.from_user.is_bot:
        raise SkipHandler()

    async with get_session() as session:
        drops = await DropService.get_recent_unclaimed_drops(session, message.chat.id, limit=10)
        if not drops:
            raise SkipHandler()

        target_drop = None
        target_card = None
        for d, c in drops:
            if d.telegram_message_id and d.telegram_message_id == replied.message_id:
                target_drop, target_card = d, c
                break

        if target_drop is None or target_card is None:
            logger.info("Drop reply: reply xabar (%s) faol dropga mos emas (chat %s)", replied.message_id, message.chat.id)
            raise SkipHandler()

        if not DropService.matches_character_guess(target_card, txt):
            raise SkipHandler()

        result = await DropService.claim_drop(
            session=session,
            drop_id=target_drop.id,
            claimer_user_id=db_user.id,
        )
        if result.success:
            await session.commit()
        else:
            await session.rollback()

    if not result.success:
        if result.reason == "already_owned":
            await message.reply("📌 Bu kartadan sizda allaqachon bor! Drop boshqalar uchun ochiq qoladi.")
        return

    await _announce_drop_claimed(replied, result)
    await message.reply(
        f"🏆 <b>{esc(result.winner.display_name)}</b> to‘g‘ri topdi!\n"
        f"🎴 <b>{esc(result.card.character_name)}</b> kolleksiyangizga qo‘shildi! (+20 XP)"
    )
