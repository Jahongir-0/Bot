"""Yutuqlar, profil bezaklari va mavsum (season)."""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database.session import get_session
from keyboards.main_menu import back_home_kb
from keyboards.v2 import VIP_ONLY_FRAMES, ach_kb, profile_style_kb
from models.user import User
from services.community_service import CommunityService
from services.gameplay_service import GameplayService
from utils.callback_data import AchCb
from utils.constants import PROFILE_BACKGROUNDS, PROFILE_FRAMES
from utils.formatting import esc
from utils.tg_helpers import edit_or_answer
from utils.timeutils import fmt_tashkent

logger = logging.getLogger(__name__)
router = Router(name="achievements")


# ------------------------------------------------------------------
# ACHIEVEMENTS, PROFILE CUSTOMIZATION & SEASON
# ------------------------------------------------------------------
def _ach_text(all_ach, unlocked) -> str:
    done = [a for a in all_ach if a.id in unlocked]
    todo = [a for a in all_ach if a.id not in unlocked]
    lines = [
        "🏅 <b>YUTUQLAR</b>",
        "━━━━━━━━━━━━━━━",
        f"✅ Ochilgan: <b>{len(done)}</b> / {len(all_ach)}",
    ]
    if done:
        lines += ["", "🏆 <b>Sizning nishonlaringiz</b>"]
        lines += [f"{a.icon} <b>{esc(a.title)}</b> — {esc(a.description)}" for a in done]
    if todo:
        lines += ["", "🔒 <b>Hali ochilmagan</b>"]
        lines += [f"{a.icon} {esc(a.title)} — {esc(a.description)}  <i>(+{a.reward_coins:,} 🪙)</i>" for a in todo]
    return "\n".join(lines)


@router.message(Command("achievements", "season"))
async def cmd_ach(message: Message, db_user: User) -> None:
    async with get_session() as session:
        all_ach, unlocked = await GameplayService.get_user_achievements(session, db_user.id)
    await message.answer(_ach_text(all_ach, unlocked), reply_markup=ach_kb(message.from_user.id))


@router.callback_query(AchCb.filter(F.act == "v"))
async def cb_ach_list(callback: CallbackQuery, db_user: User) -> None:
    async with get_session() as session:
        all_ach, unlocked = await GameplayService.get_user_achievements(session, db_user.id)
    await edit_or_answer(callback, _ach_text(all_ach, unlocked), reply_markup=ach_kb(callback.from_user.id))
    await callback.answer()


async def _render_style(callback: CallbackQuery, user_id: int) -> None:
    async with get_session() as session:
        u = await session.get(User, user_id)
    is_vip = CommunityService.is_vip_active(u)
    frame = u.equipped_frame or "default"
    bg = u.equipped_bg or "classic"
    text = (
        "🎨 <b>PROFIL BEZAGI</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"🖼 Ramka: <b>{esc(PROFILE_FRAMES.get(frame, '—'))}</b>\n"
        f"🌌 Fon: <b>{esc(PROFILE_BACKGROUNDS.get(bg, '—'))}</b>\n\n"
        "✅ — hozir tanlangan   🔒 — faqat 💎 VIP uchun\n"
        "👇 Tanlang, profilingiz shu zahoti o‘zgaradi:"
    )
    await edit_or_answer(callback, text, reply_markup=profile_style_kb(callback.from_user.id, frame, bg, is_vip))


@router.callback_query(AchCb.filter(F.act == "frames"))
async def cb_profile_custom(callback: CallbackQuery, db_user: User) -> None:
    await _render_style(callback, db_user.id)
    await callback.answer()


@router.callback_query(AchCb.filter(F.act == "equip"))
async def cb_equip_item(callback: CallbackQuery, callback_data: AchCb, db_user: User) -> None:
    code = callback_data.code
    kind, val = ("frame", code[2:]) if code.startswith("f_") else ("bg", code[2:])
    async with get_session() as session:
        u = await session.get(User, db_user.id)
        if kind == "frame" and val in VIP_ONLY_FRAMES and not CommunityService.is_vip_active(u):
            await callback.answer("🔒 Bu ramka faqat 💎 VIP a’zolar uchun.", show_alert=True)
            return
        ok = await GameplayService.equip_profile_item(session, u, kind, val)
        if ok:
            await session.commit()
    if not ok:
        await callback.answer("❌ Topilmadi.", show_alert=True)
        return
    await callback.answer("✅ Saqlandi!")
    await _render_style(callback, db_user.id)


@router.callback_query(AchCb.filter(F.act == "season"))
async def cb_season_view(callback: CallbackQuery) -> None:
    async with get_session() as session:
        s = await GameplayService.get_active_season(session)
        top = await GameplayService.get_season_leaderboard(session, s.id, limit=10) if s else []
    if s is None:
        await callback.answer("Hozircha faol mavsum yo‘q.", show_alert=True)
        return
    lines = [
        f"🏆 <b>{esc(s.name)}</b>\n",
        f"⏳ Tugash: <b>{fmt_tashkent(s.ends_at)}</b> (Toshkent vaqti)\n",
        "<b>TOP 10 O‘YINCHILAR:</b>",
    ]
    for idx, (sp, u) in enumerate(top, start=1):
        lines.append(f"{idx}. {esc(u.display_name)} — ⭐ <b>{sp.points}</b> ball ({sp.cards_claimed} karta)")
    if not top:
        lines.append("📭 Hali ball yig‘ilmagan.")
    await edit_or_answer(callback, "\n".join(lines), reply_markup=back_home_kb(callback.from_user.id, "profile"))
