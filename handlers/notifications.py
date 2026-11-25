"""Xabarnomalar sozlamalari."""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database.session import get_session
from keyboards.v2 import notifications_kb
from models.user import User
from services.community_service import CommunityService
from utils.callback_data import NotifCb
from utils.formatting import esc, header
from utils.tg_helpers import edit_or_answer

logger = logging.getLogger(__name__)
router = Router(name="notifications")


# ------------------------------------------------------------------
# NOTIFICATIONS (/notif)
# ------------------------------------------------------------------
async def _show_notifs(target, owner_id: int, db_user: User) -> None:
    async with get_session() as session:
        notifs, total = await CommunityService.list_notifications(session, db_user.id, page=1, page_size=12)
    unread = sum(1 for n in notifs if not n.is_read)
    lines = [header("🔔 <b>XABARLAR</b>"), f"📬 Jami: <b>{total}</b> · 🔵 O‘qilmagan: <b>{unread}</b>\n"]
    for n in notifs:
        dot = "🔵" if not n.is_read else "⚪"
        lines.append(f"{dot} <b>{esc(n.title)}</b>\n{esc(n.body)}\n")
    if not notifs:
        lines.append("📭 Hozircha xabarlar yo‘q.")
    await edit_or_answer(target, "\n".join(lines), reply_markup=notifications_kb(owner_id))


@router.message(Command("notif", "notifications"))
async def cmd_notifs(message: Message, db_user: User) -> None:
    await _show_notifs(message, message.from_user.id, db_user)


@router.callback_query(NotifCb.filter(F.act == "v"))
async def cb_notifs(callback: CallbackQuery, db_user: User) -> None:
    await _show_notifs(callback, callback.from_user.id, db_user)


@router.callback_query(NotifCb.filter(F.act == "read_all"))
async def cb_notifs_read(callback: CallbackQuery, db_user: User) -> None:
    async with get_session() as session:
        await CommunityService.mark_all_read(session, db_user.id)
        await session.commit()
    await callback.answer("✅ Barcha xabarlar o‘qilgan deb belgilandi.")
    await _show_notifs(callback, callback.from_user.id, db_user)
