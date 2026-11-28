"""Adminlar va ularning ruxsatlari (RBAC)."""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from admin.filters import IsAdminFilter
from admin.v2.common import AdminV2States, _cancel_kb, _deny_if_no_perm
from config.settings import get_settings
from database.session import get_session
from keyboards.v2 import admin_rbac_menu_kb, admin_rbac_perms_kb, admin_rbac_pick_user_kb
from services.admin_service import AdminService
from services.community_service import CommunityService
from utils.callback_data import AdmV2Cb
from utils.constants import ADMIN_PERMISSION_SECTIONS, PAGE_SIZE
from utils.formatting import esc
from utils.tg_helpers import NotCommandFilter, edit_or_answer, is_super_admin

logger = logging.getLogger(__name__)
router = Router(name="admin_v2_rbac")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


# ===========================================================================
# 3. ADMIN RBAC MANAGEMENT (Zero-pipe, User Picker + Checkboxes)
# ===========================================================================
async def _show_rbac_menu(target, owner_id: int, is_super: bool) -> None:
    async with get_session() as session:
        roles = await CommunityService.list_admin_roles(session)
    super_ids = sorted(get_settings().admin_ids)
    lines = [
        "👑 <b>ADMINLAR VA RUXSATLAR (RBAC)</b>\n",
        f"⭐ Asosiy Super Adminlar: <code>{', '.join(map(str, super_ids))}</code>",
        f"🛡 Qo‘shimcha adminlar: <b>{sum(1 for r in roles if r.is_active)}</b> ta\n",
    ]
    await edit_or_answer(target, "\n".join(lines), reply_markup=admin_rbac_menu_kb(owner_id, roles, is_super))


@router.callback_query(AdmV2Cb.filter(F.sec == "admins"))
async def cb_v2_admins(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "admins"):
        return
    act = callback_data.act
    owner_id = callback.from_user.id
    is_sup_caller = is_super_admin(callback.from_user)

    if act == "v":
        await state.clear()
        await _show_rbac_menu(callback, owner_id, is_sup_caller)
        return

    if act == "det":
        async with get_session() as session:
            r = await CommunityService.get_admin_role(session, callback_data.tid)
        if r is None:
            await callback.answer("Admin topilmadi.", show_alert=True)
            return
        p_txt = "BARCHA BO‘LIMLAR (Super Admin)" if r.is_super_admin else (", ".join(r.permissions or []) or "Hech narsa")
        txt = (
            f"👑 <b>ADMIN MA’LUMOTI</b>\n\n"
            f"🆔 Telegram ID: <code>{r.telegram_id}</code>\n"
            f"🏷 Lavozim: <b>{esc(r.role_name)}</b>\n"
            f"🔐 Ruxsatlar: <code>{esc(p_txt)}</code>"
        )
        b = InlineKeyboardBuilder()
        if is_sup_caller:
            b.row(
                InlineKeyboardButton(text="✏️ Ruxsatlarni tahrirlash", callback_data=AdmV2Cb(owner_id=owner_id, sec="admins", act="edit_p", tid=r.telegram_id).pack()),
                InlineKeyboardButton(text="🗑 O‘chirish", callback_data=AdmV2Cb(owner_id=owner_id, sec="admins", act="del", tid=r.telegram_id).pack()),
            )
        b.row(InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=owner_id, sec="admins", act="v").pack()))
        await edit_or_answer(callback, txt, reply_markup=b.as_markup())
        return

    if not is_sup_caller:
        await callback.answer("⛔ Faqat Super Admin adminlarni boshqara oladi.", show_alert=True)
        return

    if act == "add_pick":
        page = max(1, callback_data.p)
        async with get_session() as session:
            users, total = await AdminService.get_users_paginated(session, filter_mode="all", page=page, page_size=PAGE_SIZE)
        await edit_or_answer(
            callback,
            f"👑 <b>YANGI ADMIN QO‘SHISH</b>\n\nBot foydalanuvchilaridan birini tanlang (Jami: {total}):",
            reply_markup=admin_rbac_pick_user_kb(owner_id, users, page, total, page_size=PAGE_SIZE),
        )
        return

    if act in ("add_search", "add_id"):
        await state.set_state(AdminV2States.waiting_admin_search)
        await state.update_data(owner_tg_id=owner_id)
        await edit_or_answer(
            callback,
            "🔍 Admin qilmoqchi bo‘lgan odamning <b>Telegram ID</b>, <b>@username</b> yoki <b>ismini</b> yozing:\n<i>Bekor: /cancel</i>",
            reply_markup=_cancel_kb(owner_id, "admins", "v"),
        )
        return

    if act == "pick_u":
        target_tg_id = callback_data.tid
        await state.set_state(AdminV2States.waiting_admin_role_name)
        await state.update_data(owner_tg_id=owner_id, rbac_target_id=target_tg_id)
        b = InlineKeyboardBuilder()
        for rname in ("Moderator", "Editor", "Card Manager", "Co-Admin"):
            b.row(
                InlineKeyboardButton(
                    text=f"🏷 {rname}",
                    callback_data=AdmV2Cb(owner_id=owner_id, sec="admins", act="set_role", tid=target_tg_id, ext=rname).pack(),
                )
            )
        b.row(InlineKeyboardButton(text="❌ Bekor", callback_data=AdmV2Cb(owner_id=owner_id, sec="admins", act="v").pack()))
        await edit_or_answer(
            callback,
            f"🆔 Tanlangan foydalanuvchi: <code>{target_tg_id}</code>\n\nLavozim nomini tanlang yoki o‘zingiz matn bilan yozing:",
            reply_markup=b.as_markup(),
        )
        return

    if act == "set_role":
        target_tg_id = callback_data.tid
        role_name = callback_data.ext or "Moderator"
        async with get_session() as session:
            existing = await CommunityService.get_admin_role(session, target_tg_id)
        perms = list(existing.permissions or []) if existing else ["cards", "support"]
        is_sup = bool(existing.is_super_admin) if existing else False
        await state.set_state(None)
        await state.update_data(owner_tg_id=owner_id, rbac_target_id=target_tg_id, rbac_role_name=role_name, rbac_perms=perms, rbac_is_super=is_sup)
        await edit_or_answer(
            callback,
            f"🔐 <b>RUXSATLARNI BELGILASH</b>\n👤 ID: <code>{target_tg_id}</code> · 🏷 <b>{esc(role_name)}</b>\n\nKerakli bo‘limlarni yoqing/o‘chiring:",
            reply_markup=admin_rbac_perms_kb(owner_id, target_tg_id, set(perms), is_sup),
        )
        return

    if act == "edit_p":
        target_tg_id = callback_data.tid
        async with get_session() as session:
            existing = await CommunityService.get_admin_role(session, target_tg_id)
        if existing is None:
            await callback.answer("Admin topilmadi.", show_alert=True)
            return
        perms = list(existing.permissions or [])
        await state.set_state(None)
        await state.update_data(
            owner_tg_id=owner_id,
            rbac_target_id=target_tg_id,
            rbac_role_name=existing.role_name,
            rbac_perms=perms,
            rbac_is_super=bool(existing.is_super_admin),
        )
        await edit_or_answer(
            callback,
            f"🔐 <b>RUXSATLARNI TAHRIRLASH</b>\n👤 ID: <code>{target_tg_id}</code> · 🏷 <b>{esc(existing.role_name)}</b>",
            reply_markup=admin_rbac_perms_kb(owner_id, target_tg_id, set(perms), bool(existing.is_super_admin)),
        )
        return

    if act in ("psup", "ptog"):
        data = await state.get_data()
        target_tg_id = int(data.get("rbac_target_id") or callback_data.tid)
        role_name = str(data.get("rbac_role_name") or "Moderator")
        perms = set(data.get("rbac_perms") or [])
        is_sup = bool(data.get("rbac_is_super", False))
        if act == "psup":
            is_sup = not is_sup
        else:
            key = callback_data.ext
            if key in perms:
                perms.remove(key)
            else:
                perms.add(key)
        await state.update_data(rbac_target_id=target_tg_id, rbac_role_name=role_name, rbac_perms=sorted(perms), rbac_is_super=is_sup)
        await edit_or_answer(
            callback,
            f"🔐 <b>RUXSATLARNI BELGILASH</b>\n👤 ID: <code>{target_tg_id}</code> · 🏷 <b>{esc(role_name)}</b>",
            reply_markup=admin_rbac_perms_kb(owner_id, target_tg_id, perms, is_sup),
        )
        return

    if act == "psave":
        data = await state.get_data()
        target_tg_id = int(data.get("rbac_target_id") or callback_data.tid)
        role_name = str(data.get("rbac_role_name") or "Moderator")
        is_sup = bool(data.get("rbac_is_super", False))
        all_keys = [k for k, _ in ADMIN_PERMISSION_SECTIONS]
        perms = all_keys if is_sup else [p for p in (data.get("rbac_perms") or []) if p in all_keys]
        await state.clear()
        async with get_session() as session:
            await CommunityService.upsert_admin_role(session, target_tg_id, role_name, perms, owner_id, is_super=is_sup)
            await CommunityService.log_audit(session, owner_id, "upsert_admin", "admin", str(target_tg_id), new_value={"role": role_name, "perms": perms})
            await session.commit()
        await callback.answer("✅ Admin saqlandi!", show_alert=True)
        await _show_rbac_menu(callback, owner_id, is_sup_caller)
        return

    if act == "del":
        async with get_session() as session:
            await CommunityService.remove_admin_role(session, callback_data.tid)
            await CommunityService.log_audit(session, owner_id, "remove_admin", "admin", str(callback_data.tid))
            await session.commit()
        await callback.answer("✅ Admin olib tashlandi.", show_alert=True)
        await _show_rbac_menu(callback, owner_id, is_sup_caller)


@router.message(AdminV2States.waiting_admin_search, NotCommandFilter())
async def fsm_v2_admin_search(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id or not is_super_admin(message.from_user):
        return
    q = (message.text or "").strip()
    if q.lstrip("-").isdigit() and len(q) >= 5:
        tg_id = int(q)
        await state.set_state(AdminV2States.waiting_admin_role_name)
        await state.update_data(owner_tg_id=message.from_user.id, rbac_target_id=tg_id)
        b = InlineKeyboardBuilder()
        for rname in ("Moderator", "Editor", "Card Manager", "Co-Admin"):
            b.row(InlineKeyboardButton(text=f"🏷 {rname}", callback_data=AdmV2Cb(owner_id=message.from_user.id, sec="admins", act="set_role", tid=tg_id, ext=rname).pack()))
        await message.answer(f"🆔 ID: <code>{tg_id}</code>\n\nLavozim nomini tanlang yoki yozing:", reply_markup=b.as_markup())
        return
    async with get_session() as session:
        matches = await AdminService.search_users_for_admin(session, q, limit=20)
    if not matches:
        await message.answer("❌ Bunday foydalanuvchi topilmadi. Telegram ID raqamini yozing.")
        return
    b = InlineKeyboardBuilder()
    for u in matches:
        b.row(
            InlineKeyboardButton(
                text=f"👤 {u.display_name[:28]} ({u.telegram_id})"[:60],
                callback_data=AdmV2Cb(owner_id=message.from_user.id, sec="admins", act="pick_u", tid=u.telegram_id).pack(),
            )
        )
    b.row(InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=message.from_user.id, sec="admins", act="v").pack()))
    await message.answer("👇 Qidiruv natijalaridan birini tanlang:", reply_markup=b.as_markup())


@router.message(AdminV2States.waiting_admin_role_name, NotCommandFilter())
async def fsm_v2_admin_role_name(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id or not is_super_admin(message.from_user):
        return
    role_name = (message.text or "Moderator").strip()[:48]
    target_tg_id = int(data["rbac_target_id"])
    async with get_session() as session:
        existing = await CommunityService.get_admin_role(session, target_tg_id)
    perms = list(existing.permissions or []) if existing else ["cards", "support"]
    is_sup = bool(existing.is_super_admin) if existing else False
    await state.set_state(None)
    await state.update_data(rbac_role_name=role_name, rbac_perms=perms, rbac_is_super=is_sup)
    await message.answer(
        f"🔐 <b>RUXSATLARNI BELGILASH</b>\n👤 ID: <code>{target_tg_id}</code> · 🏷 <b>{esc(role_name)}</b>",
        reply_markup=admin_rbac_perms_kb(message.from_user.id, target_tg_id, set(perms), is_sup),
    )
