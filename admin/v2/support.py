"""Aloqa so‘rovlari, xavfsizlik (ban) va jurnallar."""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from admin.filters import IsAdminFilter
from admin.v2.common import AdminV2States, _back_home_btn, _cancel_kb, _deny_if_no_perm
from database.session import get_session
from models.admin import AdminAction
from models.group import Group
from models.user import User
from models.v2_features import SecurityIncident, SupportTicket
from services.admin_service import AdminService
from services.community_service import CommunityService
from services.security_backup_service import SecurityBackupService
from services.settings_service import SettingsService
from services.user_service import UserService
from utils.callback_data import AdmV2Cb
from utils.formatting import esc
from utils.tg_helpers import NotCommandFilter, edit_or_answer
from utils.timeutils import fmt_tashkent

logger = logging.getLogger(__name__)
router = Router(name="admin_v2_support")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


# ===========================================================================
# 14. SUPPORT, SECURITY (zero-pipe ban) & LOGS (paginated)
# ===========================================================================
@router.callback_query(AdmV2Cb.filter(F.sec == "support"))
async def cb_v2_support(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "support"):
        return
    owner_id = callback.from_user.id
    if callback_data.act == "reply":
        await state.set_state(AdminV2States.waiting_support_reply)
        await state.update_data(owner_tg_id=owner_id, ticket_id=callback_data.tid)
        await edit_or_answer(callback, f"✍️ Murojaat <b>#{callback_data.tid}</b> uchun javob matnini yozing:", reply_markup=_cancel_kb(owner_id, "support", "v"))
        return
    async with get_session() as session:
        if callback_data.act == "close":
            t = await session.get(SupportTicket, callback_data.tid)
            if t:
                t.status = "closed"
                await session.commit()
        if callback_data.act == "det":
            res = await CommunityService.get_ticket_with_messages(session, callback_data.tid)
            if res is None:
                await callback.answer("Topilmadi.", show_alert=True)
                return
            t, msgs, u = res
            lines = [f"💬 <b>Murojaat #{t.id}</b> ({t.status}) — 👤 {esc(u.display_name if u else t.user_id)}\n<b>{esc(t.subject)}</b>\n"]
            for m in msgs[-10:]:
                lines.append(f"<b>{m.sender_type}:</b> {esc(m.body)}")
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✍️ Javob berish", callback_data=AdmV2Cb(owner_id=owner_id, sec="support", act="reply", tid=t.id).pack()),
                        InlineKeyboardButton(text="🔒 Yopish", callback_data=AdmV2Cb(owner_id=owner_id, sec="support", act="close", tid=t.id).pack()),
                    ],
                    [InlineKeyboardButton(text="⬅️ Ro‘yxat", callback_data=AdmV2Cb(owner_id=owner_id, sec="support", act="v").pack())],
                ]
            )
            await edit_or_answer(callback, "\n".join(lines), reply_markup=kb)
            return
        tickets = await CommunityService.list_tickets(session, limit=20)
    b = InlineKeyboardBuilder()
    lines = ["💬 <b>SUPPORT VA MUROJAATLAR</b>\n"]
    for t in tickets:
        ico = "🟡" if t.status == "open" else ("🟢" if t.status == "answered" else "🔴")
        lines.append(f"{ico} <b>#{t.id}</b> {esc(t.subject)}")
        b.row(InlineKeyboardButton(text=f"{ico} #{t.id} {t.subject[:28]}", callback_data=AdmV2Cb(owner_id=owner_id, sec="support", act="det", tid=t.id).pack()))
    if not tickets:
        lines.append("📭 Hozircha murojaatlar yo‘q.")
    b.row(_back_home_btn(owner_id))
    await edit_or_answer(callback, "\n".join(lines), reply_markup=b.as_markup())


@router.message(AdminV2States.waiting_support_reply, NotCommandFilter())
async def fsm_v2_support_reply(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    tid = int(data["ticket_id"])
    body = (message.text or "").strip()
    await state.clear()
    async with get_session() as session:
        t = await CommunityService.reply_ticket(session, tid, "admin", message.from_user.id, body)
        user = await session.get(User, t.user_id) if t else None
        if user:
            await CommunityService.notify_user(session, user.id, "support", f"💬 Murojaat #{tid} ga javob", body)
        await session.commit()
    if user:
        try:
            await message.bot.send_message(user.telegram_id, f"💬 <b>Admin javobi (#{tid}):</b>\n\n{esc(body)}")
        except Exception:
            pass
    await message.answer(f"✅ Javob yuborildi (Murojaat #{tid})!")


async def _show_security_home(target, owner_id: int) -> None:
    async with get_session() as session:
        maint = await SettingsService.get_value(session, "maintenance", {"enabled": False})
        incidents = await SecurityBackupService.list_incidents(session, unresolved_only=True, limit=10)
    m_txt = "🔴 YOQILGAN" if maint.get("enabled") else "🟢 O‘chirilgan"
    lines = [f"🔐 <b>XAVFSIZLIK VA ANTI-CHEAT</b>\n━━━━━━━━━━━━━━━\n🛠 Texnik rejim: {m_txt}\n\n🚨 <b>Faol hodisalar ({len(incidents)}):</b>"]
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="🛠 Texnik rejim (+/-)", callback_data=AdmV2Cb(owner_id=owner_id, sec="security", act="maint").pack()),
        InlineKeyboardButton(text="🚫 Vaqtli / Umrbod ban", callback_data=AdmV2Cb(owner_id=owner_id, sec="security", act="ban_start").pack()),
    )
    for inc in incidents:
        lines.append(f"• #{inc.id} [{inc.severity}] {esc(inc.description[:70])}")
        b.row(InlineKeyboardButton(text=f"✅ Yopish #{inc.id}", callback_data=AdmV2Cb(owner_id=owner_id, sec="security", act="res", tid=inc.id).pack()))
    b.row(_back_home_btn(owner_id))
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


@router.callback_query(AdmV2Cb.filter(F.sec == "security"))
async def cb_v2_security(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "security"):
        return
    act = callback_data.act
    owner_id = callback.from_user.id

    if act == "ban_start":
        b = InlineKeyboardBuilder()
        b.row(
            InlineKeyboardButton(text="👤 Foydalanuvchi", callback_data=AdmV2Cb(owner_id=owner_id, sec="security", act="ban_type", ext="user").pack()),
            InlineKeyboardButton(text="👥 Guruh", callback_data=AdmV2Cb(owner_id=owner_id, sec="security", act="ban_type", ext="group").pack()),
        )
        b.row(InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=owner_id, sec="security", act="v").pack()))
        await edit_or_answer(callback, "🚫 <b>KIMNI BLOKLAYMIZ?</b>", reply_markup=b.as_markup())
        return

    if act == "ban_type":
        await state.set_state(AdminV2States.waiting_sec_target)
        await state.update_data(owner_tg_id=owner_id, sec_kind=callback_data.ext or "user")
        hint = "Telegram ID yoki @username yozing:" if callback_data.ext == "user" else "Guruh Chat ID (-100...) yozing:"
        await edit_or_answer(callback, f"🚫 <b>BAN (1/3)</b>\n\n{hint}", reply_markup=_cancel_kb(owner_id, "security", "v"))
        return

    if act == "ban_dur":
        await state.update_data(sec_hrs=callback_data.tid)
        await state.set_state(AdminV2States.waiting_sec_reason)
        await edit_or_answer(callback, "3/3: Bloklash <b>sababini</b> yozing:", reply_markup=_cancel_kb(owner_id, "security", "v"))
        return

    async with get_session() as session:
        if act == "maint":
            maint = await SettingsService.get_value(session, "maintenance", {"enabled": False})
            maint["enabled"] = not bool(maint.get("enabled", False))
            await SettingsService.set_value(session, "maintenance", maint)
            await CommunityService.log_audit(session, owner_id, "toggle_maintenance", "settings", new_value=maint)
            await session.commit()
        elif act == "res":
            inc = await session.get(SecurityIncident, callback_data.tid)
            if inc:
                inc.is_resolved = True
                await session.commit()
    await state.clear()
    await _show_security_home(callback, owner_id)


@router.message(AdminV2States.waiting_sec_target, NotCommandFilter())
async def fsm_v2_sec_target(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("⚠️ ID yoki @username yozing.")
        return
    if data.get("sec_kind", "user") != "group":
        async with get_session() as session:
            u = await UserService.find_user(session, raw)
            if u is not None and await AdminService.is_protected_admin_user(session, u):
                await state.clear()
                await message.answer("⛔ Bu admin! Uni ban qilib bo‘lmaydi.")
                await _show_security_home(message, message.from_user.id)
                return
    await state.update_data(sec_target=raw)
    await state.set_state(None)
    b = InlineKeyboardBuilder()
    for lbl, hrs in [("⏱ 1 soat", 1), ("⏱ 24 soat", 24), ("📅 7 kun", 168), ("🚫 Umrbod", 0)]:
        b.row(InlineKeyboardButton(text=lbl, callback_data=AdmV2Cb(owner_id=message.from_user.id, sec="security", act="ban_dur", tid=hrs).pack()))
    await message.answer("2/3: Bloklash muddatini tanlang:", reply_markup=b.as_markup())


@router.message(AdminV2States.waiting_sec_reason, NotCommandFilter())
async def fsm_v2_sec_reason(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    kind = data.get("sec_kind", "user")
    ref = data.get("sec_target", "")
    hrs = int(data.get("sec_hrs", 0))
    hours = hrs if hrs > 0 else None
    reason = (message.text or "Admin qarori").strip()
    await state.clear()

    async with get_session() as session:
        if kind == "group":
            grp = (
                await session.execute(select(Group).where(Group.telegram_chat_id == int(ref)))
            ).scalar_one_or_none() if ref.lstrip("-").isdigit() else None
            if grp is None:
                await message.answer("❌ Guruh topilmadi.")
                return
            await SecurityBackupService.ban_group(session, message.from_user.id, grp, reason, hours=hours)
        else:
            u = await UserService.find_user(session, ref)
            if u is None:
                await message.answer("❌ Foydalanuvchi topilmadi.")
                return
            try:
                await SecurityBackupService.ban_user(session, message.from_user.id, u, reason, hours=hours)
            except ValueError as exc:
                await session.rollback()
                await message.answer(f"⛔ {exc}")
                await _show_security_home(message, message.from_user.id)
                return
            try:
                dur = f"{hours} soatga" if hours else "umrbod"
                await message.bot.send_message(u.telegram_id, f"🚫 <b>Siz botdan {dur} bloklandingiz!</b>\n📌 Sabab: {esc(reason)}")
            except Exception:
                pass
        await session.commit()
    await message.answer("✅ Ban qo‘llandi!")
    await _show_security_home(message, message.from_user.id)


@router.callback_query(AdmV2Cb.filter(F.sec == "logs"))
async def cb_v2_logs(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "logs"):
        return
    page = max(1, callback_data.p)
    per_page = 15
    async with get_session() as session:
        total = int((await session.execute(select(func.count(AdminAction.id)))).scalar_one() or 0)
        logs = list(
            (await session.execute(select(AdminAction).order_by(AdminAction.id.desc()).offset((page - 1) * per_page).limit(per_page)))
            .scalars()
            .all()
        )
    lines = [f"📋 <b>ADMIN HARAKATLARI JURNALI</b> (Jami: {total} ta | Sahifa {page})\n━━━━━━━━━━━━━━━"]
    for lg in logs:
        dt = fmt_tashkent(lg.created_at, "%d.%m %H:%M")
        lines.append(f"• <code>{dt}</code> | <code>{lg.admin_telegram_id}</code> | <b>{esc(lg.action_type)}</b> → {esc(lg.target_type)}:{esc(lg.target_id or '')}")
    if not logs:
        lines.append("📭 Loglar bo‘sh.")
    b = InlineKeyboardBuilder()
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="logs", act="v", p=page - 1).pack()))
    if page * per_page < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="logs", act="v", p=page + 1).pack()))
    if nav:
        b.row(*nav)
    b.row(_back_home_btn(callback.from_user.id))
    await edit_or_answer(callback, "\n".join(lines), reply_markup=b.as_markup())
