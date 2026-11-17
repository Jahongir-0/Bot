from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from database.session import get_session
from keyboards.main_menu import profile_kb
from models.card import Card
from models.group import Group
from models.user import User
from models.user_card import UserCard
from models.v2_features import SeasonProgress, UserAchievement
from services.gameplay_service import GameplayService
from services.ranking_service import RankingService
from utils.callback_data import NavCb
from utils.formatting import format_profile_message
from utils.tg_helpers import edit_or_answer

router = Router(name="profile")


async def _render_own_profile(db_user: User, db_group: Optional[Group]) -> str:
    async with get_session() as session:
        fresh_user = await session.get(User, db_user.id) or db_user
        group_id = db_group.id if db_group else None
        global_rank, group_rank = await RankingService.get_user_ranks(
            session=session,
            user_id=fresh_user.id,
            group_id=group_id,
        )
        rarest = (
            await session.execute(
                select(Card)
                .join(UserCard, UserCard.card_id == Card.id)
                .where(UserCard.user_id == fresh_user.id)
                .order_by(Card.base_value.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        showcase = list(
            (
                await session.execute(
                    select(Card)
                    .join(UserCard, UserCard.card_id == Card.id)
                    .where(UserCard.user_id == fresh_user.id, UserCard.is_showcased.is_(True))
                    .limit(3)
                )
            )
            .scalars()
            .all()
        )
        ach_cnt = int(
            (
                await session.execute(
                    select(func.count(UserAchievement.id)).where(UserAchievement.user_id == fresh_user.id)
                )
            ).scalar_one()
            or 0
        )
        season = await GameplayService.get_active_season(session)
        s_points = None
        if season is not None:
            sp = (
                await session.execute(
                    select(SeasonProgress.points).where(
                        SeasonProgress.season_id == season.id,
                        SeasonProgress.user_id == fresh_user.id,
                    )
                )
            ).scalar_one_or_none()
            s_points = int(sp or 0)

    return format_profile_message(
        fresh_user,
        global_rank=global_rank,
        group_rank=group_rank,
        rarest_card=rarest,
        showcase_cards=showcase,
        achievements_count=ach_cnt,
        season_points=s_points,
    )


@router.message(Command("me", "profil", "profilim"))
async def cmd_profile(message: Message, db_user: User, db_group: Optional[Group] = None) -> None:
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
