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
from services.card_level_service import CardLevelService
from services.gameplay_service import GameplayService
from services.ranking_service import RankingService
from utils.callback_data import NavCb
from utils.formatting import format_profile_message
from utils.tg_helpers import append_private_ad, edit_or_answer

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

    async with get_session() as _ps:
        t_power = await CardLevelService.total_power(_ps, fresh_user.id)

    return format_profile_message(
        fresh_user,
        global_rank=global_rank,
        group_rank=group_rank,
        rarest_card=rarest,
        showcase_cards=showcase,
        achievements_count=ach_cnt,
        season_points=s_points,
        total_power=t_power,
    )


@router.message(Command("me", "profil", "profilim"))
async def cmd_profile(message: Message, db_user: User, db_group: Optional[Group] = None) -> None:
    text = await _render_own_profile(db_user, db_group)
    if message.chat.type == "private":
        text = await append_private_ad(text, message.bot, db_user)
    await message.answer(text, reply_markup=profile_kb(message.from_user.id))


@router.callback_query(NavCb.filter(F.target == "profile"))
async def cb_profile(
    callback: CallbackQuery,
    db_user: User,
    db_group: Optional[Group] = None,
) -> None:
    text = await _render_own_profile(db_user, db_group)
    if callback.message and callback.message.chat.type == "private":
        text = await append_private_ad(text, callback.bot, db_user)
    await edit_or_answer(callback, text, reply_markup=profile_kb(callback.from_user.id))


async def _render_settings(callback: CallbackQuery, db_user: User) -> None:
    from aiogram.types import InlineKeyboardButton
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from utils.callback_data import AchCb
    from utils.constants import PROFILE_BACKGROUNDS, PROFILE_FRAMES
    from utils.formatting import esc, header

    async with get_session() as session:
        u = await session.get(User, db_user.id) or db_user

    stats = dict(u.stats or {}) if isinstance(u.stats, dict) else {}
    notif_muted = bool(stats.get("mute_dm_notifs", False))
    frame_lbl = PROFILE_FRAMES.get(u.equipped_frame or "default", "Oddiy")
    bg_lbl = PROFILE_BACKGROUNDS.get(u.equipped_bg or "classic", "Klassik")

    text = (
        f"{header('⚙️ <b>SOZLAMALAR</b>')}\n"
        f"🎨 <b>Profil bezagi:</b> {esc(frame_lbl)} · {esc(bg_lbl)}\n"
        f"🔔 <b>Shaxsiy bildirishnomalar:</b> {'🔕 O‘chirilgan' if notif_muted else '🔔 Yoqilgan'}\n"
        "🌐 <b>Til:</b> 🇺🇿 O‘zbekcha\n\n"
        "👇 Kerakli sozlamani tanlang:"
    )
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text="🎨 Profil bezagi (Ramka & Fon)",
            callback_data=AchCb(owner_id=callback.from_user.id, act="frames").pack(),
        )
    )
    b.row(
        InlineKeyboardButton(
            text=("🔔 Bildirishnomalarni yoqish" if notif_muted else "🔕 Bildirishnomalarni o‘chirish"),
            callback_data=NavCb(owner_id=callback.from_user.id, target="set_ntf_tog").pack(),
        )
    )
    b.row(
        InlineKeyboardButton(
            text="⬅️ Profilim",
            callback_data=NavCb(owner_id=callback.from_user.id, target="profile").pack(),
        ),
        InlineKeyboardButton(
            text="🏠 Bosh Menu",
            callback_data=NavCb(owner_id=callback.from_user.id, target="home").pack(),
        ),
    )
    await edit_or_answer(callback, text, reply_markup=b.as_markup())


@router.callback_query(NavCb.filter(F.target == "settings"))
async def cb_settings(callback: CallbackQuery, db_user: User) -> None:
    await _render_settings(callback, db_user)


@router.callback_query(NavCb.filter(F.target == "set_ntf_tog"))
async def cb_settings_toggle_notif(callback: CallbackQuery, db_user: User) -> None:
    async with get_session() as session:
        u = await session.get(User, db_user.id)
        if u is not None:
            stats = dict(u.stats or {}) if isinstance(u.stats, dict) else {}
            stats["mute_dm_notifs"] = not bool(stats.get("mute_dm_notifs", False))
            u.stats = stats
            muted = stats["mute_dm_notifs"]
            await session.commit()
        else:
            muted = False
    await callback.answer("🔕 Bildirishnomalar o‘chirildi" if muted else "🔔 Bildirishnomalar yoqildi")
    await _render_settings(callback, db_user)

