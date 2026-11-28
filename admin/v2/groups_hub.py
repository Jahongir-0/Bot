"""Admin: GURUHLAR bo'limi — umumiy ko'rinish, statistikasi va tez o'tish tugmalari."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from admin.filters import IsAdminFilter
from admin.v2.common import _deny_if_no_perm
from database.session import get_session
from models.group import Group
from utils.callback_data import AdminCb, AdmV2Cb, NavCb
from utils.formatting import esc, fmt_coins
from utils.tg_helpers import edit_or_answer

router = Router(name="admin_v2_groups_hub")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())

SEC = "grp"


def _count(*conds):
    return select(func.count(Group.id)).where(*conds)


@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act.in_({"v", "top"}))))
async def cb_groups_hub(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "groups"):
        return
    await state.clear()
    o = callback.from_user.id
    async with get_session() as session:
        total = int((await session.execute(_count())).scalar_one() or 0)
        active = int((await session.execute(_count(Group.is_active.is_(True)))).scalar_one() or 0)
        rewarded = int((await session.execute(_count(Group.add_reward_claimed.is_(True)))).scalar_one() or 0)
        members = int((await session.execute(select(func.coalesce(func.sum(Group.member_count), 0)).where(Group.is_active.is_(True)))).scalar_one() or 0)
        drops = int((await session.execute(select(func.coalesce(func.sum(Group.daily_drop_count), 0)).where(Group.is_active.is_(True)))).scalar_one() or 0)
        top = (
            await session.execute(
                select(Group).where(Group.is_active.is_(True)).order_by(func.coalesce(Group.member_count, 0).desc()).limit(10)
            )
        ).scalars().all()

    lines = [
        "🏘 <b>GURUHLAR</b>\n━━━━━━━━━━━━━━━",
        f"📊 Jami: <b>{total}</b> · 🟢 faol: <b>{active}</b> · 🗑 chiqib ketgan: <b>{total - active}</b>",
        f"👥 Faol guruhlardagi a’zolar: <b>{fmt_coins(members)}</b>",
        f"🎴 Kunlik drop (jami reja): <b>{fmt_coins(drops)}</b>",
        f"🎁 Qo‘shish mukofoti berilgan: <b>{rewarded}</b>",
    ]
    if callback_data.act == "top" or top:
        lines.append("\n🏆 <b>Eng katta guruhlar:</b>")
        for i, g in enumerate(top, 1):
            lines.append(f"{i}. {esc(g.title[:30])} — {fmt_coins(g.member_count or 0)} a’zo · {g.daily_drop_count}/kun")
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="🟢 Faol guruhlar", callback_data=AdminCb(owner_id=o, section="groups", action="list", extra="active", page=1).pack()),
        InlineKeyboardButton(text="📋 Hammasi", callback_data=AdminCb(owner_id=o, section="groups", action="list", extra="all", page=1).pack()),
    )
    b.row(
        InlineKeyboardButton(text="🗑 Chiqib ketgan", callback_data=AdminCb(owner_id=o, section="groups", action="list", extra="left", page=1).pack()),
        InlineKeyboardButton(text="🗓 Drop jadvali", callback_data=AdminCb(owner_id=o, section="schedule", action="overview").pack()),
    )
    b.row(
        InlineKeyboardButton(text="📡 Drop qoidalari", callback_data=AdminCb(owner_id=o, section="settings", action="drop_rules").pack()),
        InlineKeyboardButton(text="🎁 Qo‘shish mukofoti", callback_data=AdminCb(owner_id=o, section="settings", action="rewards_menu").pack()),
    )
    b.row(
        InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=o, sec="hub", act="v", ext="community").pack()),
        InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=o, target="home").pack()),
    )
    await edit_or_answer(callback, "\n".join(lines), reply_markup=b.as_markup())
