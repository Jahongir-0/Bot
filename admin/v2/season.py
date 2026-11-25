"""Mavsum (season): yaratish, uzaytirish, tugatish."""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from admin.filters import IsAdminFilter
from admin.v2.common import AdminV2States, _back_home_btn, _cancel_kb, _deny_if_no_perm
from database.session import get_session
from services.gameplay_service import GameplayService
from utils.callback_data import AdmV2Cb
from utils.formatting import esc
from utils.tg_helpers import NotCommandFilter, edit_or_answer
from utils.timeutils import fmt_tashkent

logger = logging.getLogger(__name__)
router = Router(name="admin_v2_season")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


# ===========================================================================
# 9. SEASON (sec="season", Create / Extend / End)
# ===========================================================================
async def _show_season_home(target, owner_id: int) -> None:
    async with get_session() as session:
        s = await GameplayService.get_active_season(session)
        top = await GameplayService.get_season_leaderboard(session, s.id, 10) if s else []
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="➕ Yangi Mavsum Boshlash", callback_data=AdmV2Cb(owner_id=owner_id, sec="season", act="new").pack()))
    if s:
        lines = [f"🏆 <b>{esc(s.name)}</b> (#{s.season_number})\n⏳ Tugash: <b>{fmt_tashkent(s.ends_at)}</b> (Toshkent vaqti)\n"]
        for i, (sp, u) in enumerate(top, 1):
            lines.append(f"{i}. {esc(u.display_name)} — {sp.points} ball")
        b.row(
            InlineKeyboardButton(text="⏳ Muddat uzaytirish", callback_data=AdmV2Cb(owner_id=owner_id, sec="season", act="ext_menu").pack()),
            InlineKeyboardButton(text="🏁 Yakunlash", callback_data=AdmV2Cb(owner_id=owner_id, sec="season", act="end_conf").pack()),
        )
    else:
        lines = ["🏆 Hozircha faol mavsum yo‘q."]
    b.row(_back_home_btn(owner_id))
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


@router.callback_query(AdmV2Cb.filter(F.sec == "season"))
async def cb_v2_season(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "season"):
        return
    act = callback_data.act
    owner_id = callback.from_user.id

    if act == "new":
        await state.set_state(AdminV2States.waiting_season_name)
        await state.update_data(owner_tg_id=owner_id)
        await edit_or_answer(callback, "🏆 <b>YANGI MAVSUM (1/3)</b>\n\nMavsum nomini yozing (masalan: <code>Season 2: Eclipse</code>):", reply_markup=_cancel_kb(owner_id, "season", "v"))
        return

    if act == "days_p":
        await state.update_data(s_days=callback_data.tid)
        await state.set_state(AdminV2States.waiting_season_prize)
        await edit_or_answer(callback, f"📅 Davomiylik: <b>{callback_data.tid} kun</b>.\n\n3/3: 1-o‘rin g‘olibi uchun coin mukofotini yozing (masalan: <code>15000</code>):")
        return

    if act == "ext_menu":
        b = InlineKeyboardBuilder()
        for d in (7, 15, 30):
            b.add(InlineKeyboardButton(text=f"+{d} kun", callback_data=AdmV2Cb(owner_id=owner_id, sec="season", act="ext_go", tid=d).pack()))
        b.row(InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=owner_id, sec="season", act="v").pack()))
        await edit_or_answer(callback, "⏳ Mavsum muddatini necha kunga uzaytiramiz?", reply_markup=b.as_markup())
        return

    if act == "ext_go":
        async with get_session() as session:
            await GameplayService.extend_active_season(session, callback_data.tid)
            await session.commit()
        await callback.answer(f"✅ +{callback_data.tid} kun uzaytirildi!", show_alert=True)
        await _show_season_home(callback, owner_id)
        return

    if act == "end_conf":
        b = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Ha, hozir yakunlash (mukofotlarni berish)", callback_data=AdmV2Cb(owner_id=owner_id, sec="season", act="end_go").pack())],
                [InlineKeyboardButton(text="❌ Bekor", callback_data=AdmV2Cb(owner_id=owner_id, sec="season", act="v").pack())],
            ]
        )
        await edit_or_answer(callback, "🏁 Faol mavsumni hozir yakunlab, Top-3 g‘oliblarga coin tarqatamizmi?", reply_markup=b)
        return

    if act == "end_go":
        async with get_session() as session:
            await GameplayService.end_active_season_now(session)
            await session.commit()
        await callback.answer("🏁 Mavsum yakunlandi va g‘oliblar taqdirlandi!", show_alert=True)
    await state.clear()
    await _show_season_home(callback, owner_id)


@router.message(AdminV2States.waiting_season_name, NotCommandFilter())
async def fsm_v2_season_name(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id:
        return
    name = (message.text or "").strip()[:120]
    if not name:
        await message.answer("⚠️ Mavsum nomini yozing.")
        return
    await state.update_data(s_name=name)
    await state.set_state(AdminV2States.waiting_season_days)
    b = InlineKeyboardBuilder()
    for d in (14, 30, 60):
        b.add(InlineKeyboardButton(text=f"{d} kun", callback_data=AdmV2Cb(owner_id=message.from_user.id, sec="season", act="days_p", tid=d).pack()))
    await message.answer("2/3: Necha kun davom etsin? Tanlang yoki kun sonini yozing:", reply_markup=b.as_markup())


@router.message(AdminV2States.waiting_season_days, NotCommandFilter())
async def fsm_v2_season_days(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) < 1:
        await message.answer("⚠️ Kamida 1 kun yozing.")
        return
    await state.update_data(s_days=int(raw))
    await state.set_state(AdminV2States.waiting_season_prize)
    await message.answer("3/3: 1-o‘rin mukofoti (coin) ni yozing (masalan: <code>15000</code>):")


@router.message(AdminV2States.waiting_season_prize, NotCommandFilter())
async def fsm_v2_season_prize(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "")
    if not raw.isdigit():
        await message.answer("⚠️ Musbat son yozing.")
        return
    await state.clear()
    async with get_session() as session:
        await GameplayService.create_season(session, data["s_name"], int(data["s_days"]), int(raw))
        await session.commit()
    await _show_season_home(message, message.from_user.id)
