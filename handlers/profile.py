from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database.session import get_session
from keyboards.main_menu import profile_kb
from models.group import Group
from models.user import User
from services.ranking_service import RankingService
from utils.callback_data import NavCb
from utils.formatting import format_profile_message
from utils.tg_helpers import edit_or_answer

router = Router(name="profile")


async def _render_own_profile(db_user: User, db_group: Optional[Group]) -> str:
    async with get_session() as session:
        group_id = db_group.id if db_group else None
        global_rank, group_rank = await RankingService.get_user_ranks(
            session=session,
            user_id=db_user.id,
            group_id=group_id,
        )
    return format_profile_message(db_user, global_rank=global_rank, group_rank=group_rank)


@router.message(Command("me", "profil", "profilim"))
async def cmd_profile(message: Message, db_user: User, db_group: Optional[Group] = None) -> None:
    """
    Strictly opens ONLY the caller's own profile.
    Arguments like `/profil @username` are intentionally ignored/disallowed per privacy rules.
    """
    text = await _render_own_profile(db_user, db_group)
    await message.answer(text, reply_markup=profile_kb(message.from_user.id))


@router.callback_query(NavCb.filter(F.target == "profile"))
async def cb_profile(
    callback: CallbackQuery,
    db_user: User,
    db_group: Optional[Group] = None,
) -> None:
    text = await _render_own_profile(db_user, db_group)
    await edit_or_answer(callback, text, reply_markup=profile_kb(callback.from_user.id))
