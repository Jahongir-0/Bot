"""Hisobot va jonli statistika paneli."""

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from admin.filters import IsAdminFilter
from admin.v2.common import _back_home_btn, _deny_if_no_perm
from database.session import get_session
from services.admin_service import AdminService
from utils.callback_data import AdmV2Cb
from utils.formatting import esc
from utils.tg_helpers import edit_or_answer

logger = logging.getLogger(__name__)
router = Router(name="admin_v2_report")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


# ===========================================================================
# 12. REPORT (sec="report" -> Live Activity & Analytics Dashboard)
# ===========================================================================
@router.callback_query(AdmV2Cb.filter(F.sec == "report"))
async def cb_v2_report(callback: CallbackQuery, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "report"):
        return
    owner_id = callback.from_user.id
    async with get_session() as session:
        rep = await AdminService.get_v2_activity_report(session)
    lines = [
        "📈 <b>FAOLLIK HISOBOTI</b>\n━━━━━━━━━━━━━━━",
        f"🟢 24 soatda faol userlar: <b>{rep['active_users_24h']:,}</b>",
        f"👥 Faol guruhlar: <b>{rep['active_groups']:,}</b>",
        f"🎮 Bugungi o‘yinlar: <b>{rep['games_today']}</b> (Jami: {rep['games_total']:,})",
        f"🎁 Bugungi kunlik bonus: <b>{rep['daily_claims_today']}</b>",
        f"🧠 Jami viktorinalar: <b>{rep['quizzes_total']}</b>",
        f"💬 Ochiq murojaatlar: <b>{rep['open_tickets']}</b>",
        f"🔐 Hal qilinmagan xavfsizlik hodisalari: <b>{rep['unresolved_sec']}</b>\n",
        "🏆 <b>Eng faol guruhlar (olingan kartalar):</b>",
    ]
    for idx, (gname, cnt) in enumerate(rep["top_groups"], 1):
        lines.append(f"{idx}. {esc(gname)} — <b>{cnt:,}</b>")
    if not rep["top_groups"]:
        lines.append("• Ma’lumot yo‘q")
    lines.append("\n⚡ <b>Oxirgi faol foydalanuvchilar:</b>")
    for u in rep["top_users"]:
        lines.append(f"• {esc(u.display_name)} — 🎴 {u.total_cards} · 🪙 {u.coins:,}")

    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="💬 Murojaatlar", callback_data=AdmV2Cb(owner_id=owner_id, sec="support", act="v").pack()),
        InlineKeyboardButton(text="🔐 Xavfsizlik", callback_data=AdmV2Cb(owner_id=owner_id, sec="security", act="v").pack()),
        InlineKeyboardButton(text="📋 Jurnal", callback_data=AdmV2Cb(owner_id=owner_id, sec="logs", act="v").pack()),
    )
    b.row(_back_home_btn(owner_id))
    await edit_or_answer(callback, "\n".join(lines), reply_markup=b.as_markup())
