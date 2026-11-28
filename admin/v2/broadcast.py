"""Xabar yuborish (broadcast): nishon, vaqt, forward/copy."""

import asyncio
import logging
from datetime import datetime
from typing import List

from aiogram import F, Router
from aiogram.exceptions import TelegramMigrateToChat, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, update

from admin.filters import IsAdminFilter, resolve_admin_perms
from admin.v2.common import AdminV2States, _back_home_btn, _cancel_kb, _deny_if_no_perm, _parse_local_dt
from database.session import get_session
from keyboards.v2 import admin_v2_home_kb
from models.group import Group
from models.user import User
from services.community_service import CommunityService
from utils.callback_data import AdmV2Cb
from utils.tg_helpers import NotCommandFilter, edit_or_answer

logger = logging.getLogger(__name__)
router = Router(name="admin_v2_broadcast")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


# ===========================================================================
# 11. MESSAGE / BROADCAST (sec="message", Target + Now/Sched + Forward/Copy)
# ===========================================================================
@router.callback_query(AdmV2Cb.filter(F.sec == "message"))
async def cb_v2_message_menu(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "message"):
        return
    act = callback_data.act
    owner_id = callback.from_user.id

    if act == "who":
        scope_val = callback_data.ext or "all"
        scope_names = {"all": "🌍 Barchaga (Userlar + Guruhlar)", "users": "👤 Faqat Userlarga", "groups": "👥 Faqat Guruhlarga"}
        await state.update_data(owner_tg_id=owner_id, msg_scope=scope_val)
        b = InlineKeyboardBuilder()
        b.row(
            InlineKeyboardButton(text="⚡ Hozir yuborish", callback_data=AdmV2Cb(owner_id=owner_id, sec="message", act="when_now").pack()),
            InlineKeyboardButton(text="⏰ Vaqtga rejalash", callback_data=AdmV2Cb(owner_id=owner_id, sec="message", act="when_sched").pack()),
        )
        b.row(InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=owner_id, sec="message", act="v").pack()))
        await edit_or_answer(
            callback,
            f"🎯 Qabul qiluvchilar: <b>{scope_names.get(scope_val, scope_val)}</b>\n\nQachon yuborilsin?",
            reply_markup=b.as_markup(),
        )
        return

    if act == "when_now":
        data = await state.get_data()
        await state.update_data(owner_tg_id=owner_id, msg_scope=data.get("msg_scope", "all"), msg_when="now", msg_sched_dt=None)
        await state.set_state(AdminV2States.waiting_msg_content)
        await edit_or_answer(
            callback,
            "✉️ <b>XABARNI YUBORING</b>\n\nMatn, rasm, video, stiker yoki istalgan kanal postini forward qiling — bot hammaga aynan shunday yuboradi:",
            reply_markup=_cancel_kb(owner_id, "message", "v"),
        )
        return

    if act == "when_sched":
        data = await state.get_data()
        await state.update_data(owner_tg_id=owner_id, msg_scope=data.get("msg_scope", "all"))
        await state.set_state(AdminV2States.waiting_msg_sched_dt)
        await edit_or_answer(
            callback,
            "⏰ Yuborilish vaqtini yozing (<code>2026-12-01 18:00</code>):",
            reply_markup=_cancel_kb(owner_id, "message", "v"),
        )
        return

    await state.clear()
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🌍 Barchaga (Userlar + Guruhlar)", callback_data=AdmV2Cb(owner_id=owner_id, sec="message", act="who", ext="all").pack()))
    b.row(
        InlineKeyboardButton(text="👤 Faqat Userlarga", callback_data=AdmV2Cb(owner_id=owner_id, sec="message", act="who", ext="users").pack()),
        InlineKeyboardButton(text="👥 Faqat Guruhlarga", callback_data=AdmV2Cb(owner_id=owner_id, sec="message", act="who", ext="groups").pack()),
    )
    b.row(_back_home_btn(owner_id))
    await edit_or_answer(callback, "📨 <b>XABARLAR VA BROADCAST</b>\n━━━━━━━━━━━━━━━\nKimga xabar yuboramiz?", reply_markup=b.as_markup())


@router.message(AdminV2States.waiting_msg_sched_dt, NotCommandFilter())
async def fsm_v2_msg_sched_dt(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id:
        return
    dt_utc = _parse_local_dt(message.text or "")
    if dt_utc is None:
        await message.answer("⚠️ Sana formati noto‘g‘ri. Misol: <code>2026-12-01 18:00</code>")
        return
    await state.update_data(msg_when="sched", msg_sched_dt=dt_utc.isoformat())
    await state.set_state(AdminV2States.waiting_msg_content)
    await message.answer(
        "✉️ Endi rejalangan xabarni (matn/media/forward) yuboring:",
        reply_markup=_cancel_kb(message.from_user.id, "message", "v"),
    )


@router.message(AdminV2States.waiting_msg_content, NotCommandFilter())
async def fsm_v2_msg_content(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    scope = data.get("msg_scope", "all")
    when = data.get("msg_when", "now")
    sched_iso = data.get("msg_sched_dt")
    await state.clear()

    async with get_session() as session:
        role = await CommunityService.get_admin_role(session, message.from_user.id)
        if when == "sched" and sched_iso:
            dt_utc = datetime.fromisoformat(sched_iso)
            body = (message.caption or message.text or "Media xabar").strip()
            await CommunityService.create_scheduled_message(
                session,
                title=body[:120],
                body=body,
                send_at=dt_utc,
                target_scope=scope,
                admin_tg_id=message.from_user.id,
                from_chat_id=message.chat.id,
                forward_message_id=message.message_id,
            )
            await session.commit()
            await message.answer(
                f"✅ Xabar rejalashtirildi! ({scope})",
                reply_markup=admin_v2_home_kb(message.from_user.id, resolve_admin_perms(message.from_user, role)),
            )
            return

        user_ids: List[int] = []
        group_ids: List[int] = []
        if scope in ("users", "all"):
            u_rows = (await session.execute(select(User.telegram_id).where(User.is_banned.is_(False)))).scalars().all()
            user_ids = list(dict.fromkeys(int(x) for x in u_rows))
        if scope in ("groups", "all"):
            g_rows = (await session.execute(select(Group.telegram_chat_id).where(Group.is_active.is_(True), Group.is_banned.is_(False)))).scalars().all()
            group_ids = list(dict.fromkeys(int(x) for x in g_rows))

    perms = resolve_admin_perms(message.from_user, role)
    wait = await message.answer(
        f"⏳ Xabar yuborilmoqda...\n👤 Userlar: {len(user_ids)} ta · 👥 Guruhlar: {len(group_ids)} ta"
    )

    ordered_targets: List[tuple[str, int]] = []
    seen_cids: set = set()
    for cid in group_ids:
        if cid not in seen_cids:
            seen_cids.add(cid)
            ordered_targets.append(("group", cid))
    for cid in user_ids:
        if cid not in seen_cids:
            seen_cids.add(cid)
            ordered_targets.append(("user", cid))

    sent_users = 0
    sent_groups = 0
    failed_users = 0
    failed_groups = 0
    migrated: List[tuple[int, int]] = []
    sem = asyncio.Semaphore(4)

    async def _deliver_one(kind: str, cid: int) -> None:
        nonlocal sent_users, sent_groups, failed_users, failed_groups
        async with sem:
            target_cid = cid
            delivered = False
            for attempt in range(3):
                try:
                    await message.bot.copy_message(
                        chat_id=target_cid,
                        from_chat_id=message.chat.id,
                        message_id=message.message_id,
                    )
                    delivered = True
                    break
                except TelegramRetryAfter as exc:
                    await asyncio.sleep(float(exc.retry_after) + 0.5)
                except TelegramMigrateToChat as exc:
                    new_cid = int(exc.migrate_to_chat_id)
                    migrated.append((target_cid, new_cid))
                    target_cid = new_cid
                except Exception:
                    if attempt == 0 and (message.text or message.caption):
                        try:
                            await message.bot.send_message(target_cid, message.text or message.caption)
                            delivered = True
                        except Exception:
                            pass
                    break
            if delivered:
                if kind == "group":
                    sent_groups += 1
                else:
                    sent_users += 1
            else:
                if kind == "group":
                    failed_groups += 1
                else:
                    failed_users += 1
            await asyncio.sleep(0.05)

    if ordered_targets:
        await asyncio.gather(*[_deliver_one(k, cid) for k, cid in ordered_targets])

    if migrated:
        async with get_session() as session:
            from services.group_migration import apply_group_migrations

            await apply_group_migrations(session, migrated)
            await session.commit()

    total_sent = sent_users + sent_groups
    total_targets = len(ordered_targets)
    breakdown = []
    if scope in ("users", "all"):
        breakdown.append(f"👤 Userlar: <b>{sent_users}</b> / {len(user_ids)} (xato: {failed_users})")
    if scope in ("groups", "all"):
        breakdown.append(f"👥 Guruhlar: <b>{sent_groups}</b> / {len(group_ids)} (xato: {failed_groups})")
    detail = "\n".join(breakdown)
    summary_text = (
        f"✅ <b>XABAR YUBORISH YAKUNLANDI!</b>\n\n{detail}\n"
        f"📤 Jami yetkazildi: <b>{total_sent}</b> / {total_targets}"
    )
    markup = admin_v2_home_kb(message.from_user.id, perms)
    try:
        await wait.edit_text(summary_text, reply_markup=markup)
    except Exception:
        await message.answer(summary_text, reply_markup=markup)
