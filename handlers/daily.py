from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from config.settings import get_settings
from database.session import get_session
from keyboards.main_menu import back_home_kb
from models.user import User
from services.daily_service import DailyService
from utils.callback_data import NavCb
from utils.constants import get_rarity_meta
from utils.formatting import esc, fmt_coins, header
from utils.tg_helpers import edit_or_answer, safe_delete, send_card_message

router = Router(name="daily")


def _week_dots(streak: int) -> str:
    """Haftalik seriya: 7 ta katak."""
    if streak <= 0:
        return "⬜" * 7
    filled = ((streak - 1) % 7) + 1
    return "🟩" * filled + "⬜" * (7 - filled)


def _time_left_text() -> str:
    tz = ZoneInfo(get_settings().timezone)
    now = datetime.now(tz)
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    mins = max(1, int((nxt - now).total_seconds() // 60))
    h, m = divmod(mins, 60)
    return f"{h} soat {m} daqiqa" if h else f"{m} daqiqa"


async def _handle_daily(target, owner_id: int, db_user: User) -> None:
    async with get_session() as session:
        res = await DailyService.claim_daily(session, db_user.id)
        if res.success:
            await session.commit()
        else:
            await session.rollback()

    kb = back_home_kb(owner_id, "profile")
    if not res.success:
        already = "Allaqachon" in res.message or "allaqachon" in res.message
        text = (
            f"{header('🎁 <b>KUNLIK BONUS</b>')}\n"
            f"{'✅ Bugungi bonusni olib bo‘ldingiz.' if already else '⏳ ' + esc(res.message)}\n\n"
            f"🔥 Seriya: <b>{res.streak}</b> kun\n{_week_dots(res.streak)}\n\n"
            f"⏰ Keyingi bonus: <b>{_time_left_text()}</b> dan keyin"
        )
        await edit_or_answer(target, text, reply_markup=kb)
        return

    card_line = ""
    if res.card_awarded is not None:
        meta = get_rarity_meta(res.card_awarded.rarity)
        card_line = (
            f"\n🎴 <b>Tasodifiy karta:</b>\n"
            f"{meta.emoji} <b>{esc(res.card_awarded.character_name)}</b> · {esc(res.card_awarded.anime_name)}\n"
        )
    text = (
        f"{header('🎁 <b>KUNLIK BONUS OLINDI!</b>')}\n"
        f"🪙 <b>+{fmt_coins(res.coins_awarded)}</b> coin   ⚡ <b>+15</b> XP\n"
        f"{card_line}\n"
        f"🔥 Seriya: <b>{res.streak}</b> kun\n{_week_dots(res.streak)}\n\n"
        "💡 Har kuni kiring: seriya uzaygan sari bonus oshadi!"
    )
    if res.card_awarded is not None:
        if isinstance(target, CallbackQuery) and target.message is not None:
            await target.answer()
            await safe_delete(target.bot, target.message.chat.id, target.message.message_id)
            await send_card_message(target.bot, target.message.chat.id, res.card_awarded, text, kb)
            return
        if isinstance(target, Message):
            await send_card_message(target.bot, target.chat.id, res.card_awarded, text, kb)
            return
    await edit_or_answer(target, text, reply_markup=kb)



@router.message(Command("daily"))
async def cmd_daily(message: Message, db_user: User) -> None:
    await _handle_daily(message, message.from_user.id, db_user)


@router.callback_query(NavCb.filter(F.target == "daily"))
async def cb_daily(callback: CallbackQuery, db_user: User) -> None:
    await _handle_daily(callback, callback.from_user.id, db_user)
