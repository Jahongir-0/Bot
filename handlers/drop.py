import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from database.session import get_session
from models.user import User
from services.drop_service import DropService
from utils.callback_data import DropCb
from utils.formatting import esc, format_drop_claimed_message

logger = logging.getLogger(__name__)
router = Router(name="drop")


@router.callback_query(DropCb.filter(F.action == "claim"))
async def cb_claim_drop(callback: CallbackQuery, callback_data: DropCb, db_user: User) -> None:
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
            await callback.answer(
                "📌 Bu kartadan sizda bor! Bir xil kartadan faqat bitta nusxa olish mumkin.", show_alert=True
            )
        elif result.reason == "locked":
            await callback.answer("⏳ Boshqa foydalanuvchi hozirgina oldi!", show_alert=True)
        else:
            await callback.answer("❌ Karta topilmadi.", show_alert=True)
        return

    # Update original drop message: remove button and show winner
    claimed_text = format_drop_claimed_message(
        card=result.card,
        winner=result.winner,
        reward_coins=result.reward_coins,
    )
    msg = callback.message
    if msg is not None:
        try:
            if msg.photo:
                await msg.edit_caption(caption=claimed_text, reply_markup=None)
            else:
                await msg.edit_text(text=claimed_text, reply_markup=None)
        except TelegramBadRequest as exc:
            logger.debug("Could not edit drop message after claim: %s", exc)

    await callback.answer(
        f"🎉 {result.card.character_name} kolleksiyangizga qo‘shildi! +{result.reward_coins} 🪙",
        show_alert=True,
    )
    if msg is not None:
        await msg.reply(
            f"🎉 {esc(result.winner.display_name)} — <b>{esc(result.card.character_name)}</b> "
            f"kolleksiyangizga qo‘shildi!\n➕ <b>+{result.reward_coins:,} 🪙</b>"
        )
