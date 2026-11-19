from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database.session import get_session
from keyboards.main_menu import back_home_kb
from models.user import User
from services.daily_service import DailyService
from utils.callback_data import NavCb
from utils.constants import get_rarity_meta
from utils.formatting import esc, fmt_coins
from utils.i18n import get_lang, tr
from utils.tg_helpers import edit_or_answer, send_card_message

router = Router(name="daily")


async def _handle_daily(target, owner_id: int, db_user: User) -> None:
    lang = get_lang(db_user)
    async with get_session() as session:
        res = await DailyService.claim_daily(session, db_user.id)
        if res.success:
            await session.commit()
        else:
            await session.rollback()

    if res.success:
        card_line = ""
        if res.card_awarded is not None:
            meta = get_rarity_meta(res.card_awarded.rarity)
            card_line = tr(
                lang,
                "daily_card_line",
                emoji=meta.emoji,
                char=esc(res.card_awarded.character_name),
                anime=esc(res.card_awarded.anime_name),
            )
        text = tr(
            lang,
            "daily_ok",
            coins=fmt_coins(res.coins_awarded),
            streak=res.streak,
            card_line=card_line,
        )
        if res.card_awarded is not None and isinstance(target, Message):
            await send_card_message(
                target.bot,
                target.chat.id,
                res.card_awarded,
                text,
                back_home_kb(owner_id, "profile", lang),
            )
            return
    else:
        text = tr(lang, "daily_wait", msg=esc(res.message), streak=res.streak)
    await edit_or_answer(target, text, reply_markup=back_home_kb(owner_id, "profile", lang))


@router.message(Command("daily"))
async def cmd_daily(message: Message, db_user: User) -> None:
    await _handle_daily(message, message.from_user.id, db_user)


@router.callback_query(NavCb.filter(F.target == "daily"))
async def cb_daily(callback: CallbackQuery, db_user: User) -> None:
    await _handle_daily(callback, callback.from_user.id, db_user)
