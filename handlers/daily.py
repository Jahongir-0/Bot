from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database.session import get_session
from keyboards.main_menu import back_home_kb
from models.user import User
from services.daily_service import DailyService
from utils.callback_data import NavCb
from utils.formatting import fmt_coins
from utils.tg_helpers import edit_or_answer

router = Router(name="daily")


async def _handle_daily(target, owner_id: int, db_user: User) -> None:
    async with get_session() as session:
        res = await DailyService.claim_daily(session, db_user.id)
        if res.success:
            await session.commit()
        else:
            await session.rollback()

    if res.success:
        text = (
            "🎁 <b>DAILY BONUS</b>\n\n"
            f"➕ <b>+{fmt_coins(res.coins_awarded)} 🪙</b>\n"
            f"🔥 <b>Streak:</b> {res.streak} kun\n\n"
            "Ertaga yana kiring va yanada ko‘proq bonus oling!"
        )
    else:
        text = (
            "⏳ <b>DAILY BONUS</b>\n\n"
            f"{res.message}\n"
            f"🔥 Joriy streak: <b>{res.streak} kun</b>"
        )
    await edit_or_answer(target, text, reply_markup=back_home_kb(owner_id, "profile"))


@router.message(Command("daily"))
async def cmd_daily(message: Message, db_user: User) -> None:
    await _handle_daily(message, message.from_user.id, db_user)


@router.callback_query(NavCb.filter(F.target == "daily"))
async def cb_daily(callback: CallbackQuery, db_user: User) -> None:
    await _handle_daily(callback, callback.from_user.id, db_user)
