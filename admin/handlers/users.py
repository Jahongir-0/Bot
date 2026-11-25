"""Foydalanuvchilarni boshqarish: qidirish, ban, coin, karta berish, VIP, xabar."""

import logging
from typing import Optional

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from admin.filters import IsAdminFilter
from admin.handlers.common import AdminStates, _AdminRbacCallbackMiddleware
from database.session import get_session
from keyboards.admin import (
    admin_pick_card_for_user_kb,
    admin_user_cards_remove_kb,
    admin_user_detail_kb,
    admin_users_list_kb,
    admin_users_menu_kb,
)
from models.group import Group, GroupMember
from models.user import User
from services.admin_service import AdminService
from services.community_service import CommunityService
from services.security_backup_service import SecurityBackupService
from utils.callback_data import AdminCb
from utils.constants import PAGE_SIZE, get_rarity_meta
from utils.formatting import esc, fmt_coins
from utils.tg_helpers import NotCommandFilter, edit_or_answer
from utils.timeutils import fmt_tashkent

logger = logging.getLogger(__name__)
router = Router(name="admin_users")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())
router.callback_query.middleware(_AdminRbacCallbackMiddleware())


# ---------------------------------------------------------------------------
# USER MANAGEMENT (20/page, Search results inline, DM/Group msg, Temp Ban, Undropped card give)
# ---------------------------------------------------------------------------
@router.callback_query(AdminCb.filter((F.section == "users") & (F.action == "menu")))
async def cb_admin_users_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_or_answer(
        callback,
        "👤 <b>USERLAR BO‘LIMI</b>\n\nKerakli amalni tanlang:",
        reply_markup=admin_users_menu_kb(callback.from_user.id),
    )


async def _show_admin_users_list(callback: CallbackQuery, filter_mode: str, page: int) -> None:
    async with get_session() as session:
        users, total = await AdminService.get_users_paginated(
            session, filter_mode=filter_mode, page=page, page_size=PAGE_SIZE
        )
    titles = {
        "active": f"🚫 <b>BAN QILISH</b> (Faol userlar: {total})",
        "banned": f"🔓 <b>BANDAN CHIQARISH</b> (Banlanganlar: {total})",
        "all": f"👥 <b>USERLAR RO‘YXATI</b> (Jami: {total} | 20/sahifa)",
    }
    text = titles.get(filter_mode, titles["all"])
    if not users:
        text += "\n\n📭 Ro‘yxat bo‘sh."
    await edit_or_answer(
        callback,
        text,
        reply_markup=admin_users_list_kb(callback.from_user.id, users, filter_mode, page, total, page_size=PAGE_SIZE),
    )


@router.callback_query(AdminCb.filter((F.section == "users") & (F.action == "list")))
async def cb_admin_users_list(callback: CallbackQuery, callback_data: AdminCb) -> None:
    await _show_admin_users_list(callback, callback_data.extra or "all", callback_data.page)


async def _notify_user_ban_status(bot, tg_id: int, banned: bool, reason: str = "", hours: Optional[int] = None) -> None:
    try:
        if banned:
            dur = f"{hours} soatga" if hours else "umrbod"
            await bot.send_message(tg_id, f"🚫 <b>Siz botdan {dur} bloklandingiz!</b>\n📌 Sabab: {esc(reason)}")
        else:
            await bot.send_message(tg_id, "🎉 <b>Siz bandan chiqarildingiz!</b> Botdan yana bemalol foydalanishingiz mumkin.")
    except Exception:
        pass


@router.callback_query(AdminCb.filter((F.section == "users") & (F.action.in_({"quick_ban", "quick_unban"}))))
async def cb_admin_quick_ban(callback: CallbackQuery, callback_data: AdminCb) -> None:
    ban = callback_data.action == "quick_ban"
    async with get_session() as session:
        try:
            u = await AdminService.set_global_ban(session, callback.from_user.id, callback_data.target_id, ban=ban)
            await session.commit()
        except PermissionError as exc:
            await session.rollback()
            await callback.answer(f"⛔ {exc}", show_alert=True)
            return
    if u:
        await _notify_user_ban_status(callback.bot, u.telegram_id, ban, reason="Admin qarori")
    await callback.answer("🚫 Ban qo‘yildi!" if ban else "🔓 Bandan chiqarildi!", show_alert=True)
    await _show_admin_users_list(callback, callback_data.extra or ("active" if ban else "banned"), callback_data.page)


async def _render_admin_user(target, admin_tg_id: int, target_user_id: int) -> None:
    async with get_session() as session:
        user = await session.get(User, target_user_id)
        if user is None:
            await edit_or_answer(target, "❌ Foydalanuvchi topilmadi.", reply_markup=admin_users_menu_kb(admin_tg_id))
            return
        groups = await AdminService.get_user_groups(session, user.id)
        added_groups = (
            await session.execute(
                select(Group).where(Group.added_by_user_id == user.id).order_by(Group.id.desc()).limit(8)
            )
        ).scalars().all()
        added_total = int(
            (await session.execute(select(func.count(Group.id)).where(Group.added_by_user_id == user.id))).scalar_one() or 0
        )

    if user.is_banned:
        if user.ban_until:
            ban_str = f"⏱ Vaqtli ban ({fmt_tashkent(user.ban_until)} gacha (Toshkent vaqti))"
        else:
            ban_str = "🚫 Umrbod BANNED"
    else:
        ban_str = "🟢 Active"
    vip_str = f"💎 {esc(user.vip_tier)}" if CommunityService.is_vip_active(user) else "▫️ Oddiy"
    groups_str = ", ".join(esc(g) for g in groups[:5]) if groups else "—"
    added_groups_str = ""
    for g in added_groups:
        title = esc(g.title)
        added_groups_str += (
            f"   • <a href=\"https://t.me/{esc(g.username)}\">{title}</a>\n" if g.username else f"   • {title}\n"
        )
    text = (
        f"👤 <b>USER BOSHQARUVI</b>\n\n"
        f"🆔 Telegram ID: <code>{user.telegram_id}</code>\n"
        f"👤 Ism/Username: {esc(user.display_name)}\n"
        f"💎 Status: {vip_str}\n"
        f"🪙 Coins: <b>{fmt_coins(user.coins)}</b> (Band: {fmt_coins(user.locked_coins)})\n"
        f"⚡ Level: <b>{user.level}</b> ({user.xp} XP)\n"
        f"🎴 Cards: <b>{user.total_cards}</b> (Unique: <b>{user.unique_cards}</b>)\n"
        f"👥 Guruhlar: {groups_str}\n"
        f"🏰 Botni qo‘shgan guruhlari: <b>{added_total}</b> ta\n"
        f"{added_groups_str}"
        f"📌 Holat: <b>{ban_str}</b>\n"
        f"🕒 Oxirgi faollik: <code>{fmt_tashkent(user.last_activity, '%Y-%m-%d %H:%M')}</code>"
    )
    await edit_or_answer(target, text, reply_markup=admin_user_detail_kb(admin_tg_id, user))


@router.callback_query(AdminCb.filter((F.section == "users") & (F.action == "detail")))
async def cb_admin_user_detail(callback: CallbackQuery, callback_data: AdminCb) -> None:
    await _render_admin_user(callback, callback.from_user.id, callback_data.target_id)


# ----------------------------------------------------------------------
# USER VIP: admin tomonidan berish / olib tashlash
# ----------------------------------------------------------------------
@router.callback_query(AdminCb.filter((F.section == "users") & (F.action == "vip_menu")))
async def cb_admin_user_vip_menu(callback: CallbackQuery, callback_data: AdminCb) -> None:
    owner_id = callback.from_user.id
    async with get_session() as session:
        vcfg = await CommunityService.get_vip_config(session)
        user = await session.get(User, callback_data.target_id)
    if user is None:
        await callback.answer("Foydalanuvchi topilmadi", show_alert=True)
        return
    b = InlineKeyboardBuilder()
    for key, t in (vcfg.get("tiers") or {}).items():
        b.row(InlineKeyboardButton(
            text=f"{t.get('emoji', '💎')} {t.get('name', key)}",
            callback_data=AdminCb(owner_id=owner_id, section="users", action="vip_days", target_id=user.id, extra=key).pack(),
        ))
    if CommunityService.is_vip_active(user):
        b.row(InlineKeyboardButton(
            text="❌ VIP ni olib tashlash",
            callback_data=AdminCb(owner_id=owner_id, section="users", action="vip_revoke", target_id=user.id).pack(),
        ))
    b.row(InlineKeyboardButton(
        text="⬅️ ORQAGA",
        callback_data=AdminCb(owner_id=owner_id, section="users", action="detail", target_id=user.id).pack(),
    ))
    cur = f"{esc(user.vip_tier)} ({fmt_tashkent(user.vip_until, '%d.%m.%Y')} gacha)" if CommunityService.is_vip_active(user) else "yo‘q"
    await edit_or_answer(
        callback,
        f"💎 <b>VIP BERISH</b>\n\n👤 {esc(user.display_name)}\nHozirgi VIP: <b>{cur}</b>\n\nQaysi darajani beramiz?",
        reply_markup=b.as_markup(),
    )


@router.callback_query(AdminCb.filter((F.section == "users") & (F.action == "vip_days")))
async def cb_admin_user_vip_days(callback: CallbackQuery, callback_data: AdminCb) -> None:
    owner_id = callback.from_user.id
    b = InlineKeyboardBuilder()
    for d in (7, 30, 90, 180, 365):
        b.add(InlineKeyboardButton(
            text=f"{d} kun",
            callback_data=AdminCb(owner_id=owner_id, section="users", action="vip_give", target_id=callback_data.target_id, extra=callback_data.extra, page=d).pack(),
        ))
    b.adjust(3)
    b.row(InlineKeyboardButton(
        text="⬅️ ORQAGA",
        callback_data=AdminCb(owner_id=owner_id, section="users", action="vip_menu", target_id=callback_data.target_id).pack(),
    ))
    await edit_or_answer(callback, f"💎 <b>{esc(callback_data.extra)}</b> VIP — necha kunga beramiz?", reply_markup=b.as_markup())


@router.callback_query(AdminCb.filter((F.section == "users") & (F.action == "vip_give")))
async def cb_admin_user_vip_give(callback: CallbackQuery, callback_data: AdminCb) -> None:
    days = max(1, min(3650, int(callback_data.page or 30)))
    async with get_session() as session:
        vcfg = await CommunityService.get_vip_config(session)
        if callback_data.extra not in (vcfg.get("tiers") or {}):
            await callback.answer("VIP daraja topilmadi", show_alert=True)
            return
        user = await session.get(User, callback_data.target_id)
        if user is None:
            await callback.answer("Foydalanuvchi topilmadi", show_alert=True)
            return
        await CommunityService.grant_vip(session, user, callback_data.extra, days)
        await CommunityService.log_audit(
            session, callback.from_user.id, "vip_grant", "user", str(user.id),
            details={"tier": callback_data.extra, "days": days},
        )
        tg_id = user.telegram_id
        await session.commit()
    try:
        await callback.bot.send_message(tg_id, f"💎 Sizga admin tomonidan <b>{esc(callback_data.extra)} VIP</b> berildi ({days} kun)! 🎉")
    except Exception:
        pass
    await callback.answer("✅ VIP berildi", show_alert=True)
    await _render_admin_user(callback, callback.from_user.id, callback_data.target_id)


@router.callback_query(AdminCb.filter((F.section == "users") & (F.action == "vip_revoke")))
async def cb_admin_user_vip_revoke(callback: CallbackQuery, callback_data: AdminCb) -> None:
    async with get_session() as session:
        user = await session.get(User, callback_data.target_id)
        if user is None:
            await callback.answer("Foydalanuvchi topilmadi", show_alert=True)
            return
        user.vip_tier = None
        user.vip_until = None
        await CommunityService.log_audit(session, callback.from_user.id, "vip_revoke", "user", str(user.id), details={})
        tg_id = user.telegram_id
        await session.commit()
    try:
        await callback.bot.send_message(tg_id, "ℹ️ VIP obunangiz admin tomonidan bekor qilindi.")
    except Exception:
        pass
    await callback.answer("✅ VIP olib tashlandi", show_alert=True)
    await _render_admin_user(callback, callback.from_user.id, callback_data.target_id)


@router.callback_query(AdminCb.filter((F.section == "users") & (F.action == "search_prompt")))
async def cb_admin_user_search(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.user_search)
    await state.update_data(owner_tg_id=callback.from_user.id)
    await edit_or_answer(callback, "🔍 Foydalanuvchi <b>Telegram ID</b>, <b>ism</b> yoki <b>@username</b> ni yozing:\n<i>Bekor: /cancel</i>")


@router.message(AdminStates.user_search, NotCommandFilter())
async def fsm_admin_user_search(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    q = (message.text or "").strip()
    await state.clear()
    async with get_session() as session:
        matches = await AdminService.search_users_for_admin(session, q, limit=20)
    if not matches:
        await message.answer("❌ Foydalanuvchi topilmadi.", reply_markup=admin_users_menu_kb(message.from_user.id))
        return
    if len(matches) == 1:
        await _render_admin_user(message, message.from_user.id, matches[0].id)
        return
    b = InlineKeyboardBuilder()
    for u in matches:
        st = "🚫" if u.is_banned else "👤"
        b.row(
            InlineKeyboardButton(
                text=f"{st} {u.display_name[:26]} ({u.telegram_id})"[:60],
                callback_data=AdminCb(owner_id=message.from_user.id, section="users", action="detail", target_id=u.id).pack(),
            )
        )
    b.row(InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdminCb(owner_id=message.from_user.id, section="users", action="menu").pack()))
    await message.answer(f"🔍 Topildi: <b>{len(matches)}</b> ta foydalanuvchi:", reply_markup=b.as_markup())


@router.callback_query(AdminCb.filter((F.section == "users") & (F.action.in_({"add_coins", "sub_coins"}))))
async def cb_admin_coin_prompt(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    await state.set_state(AdminStates.user_coin_delta)
    await state.update_data(owner_tg_id=callback.from_user.id, target_user_id=callback_data.target_id, sign=1 if callback_data.action == "add_coins" else -1)
    await edit_or_answer(callback, "🪙 Coin miqdorini kiriting (musbat son):")


@router.message(AdminStates.user_coin_delta, NotCommandFilter())
async def fsm_admin_coin_delta(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("⚠️ Musbat butun son kiriting.")
        return
    await state.clear()
    delta = int(raw) * int(data["sign"])
    uid = int(data["target_user_id"])

    async with get_session() as session:
        ok, msg_text, _ = await AdminService.adjust_user_coins(session, message.from_user.id, uid, delta)
        u = await session.get(User, uid) if ok else None
        if ok:
            await session.commit()
        else:
            await session.rollback()
    if ok and u:
        try:
            verb = f"+{abs(delta):,} 🪙 qo‘shildi" if delta > 0 else f"-{abs(delta):,} 🪙 yechildi"
            await message.bot.send_message(u.telegram_id, f"💰 Admin tomonidan balansingizga {verb}.")
        except Exception:
            pass
    await message.answer(msg_text)
    await _render_admin_user(message, message.from_user.id, uid)


@router.callback_query(AdminCb.filter((F.section == "users") & (F.action == "give_pick")))
async def cb_admin_give_card_pick(callback: CallbackQuery, callback_data: AdminCb) -> None:
    async with get_session() as session:
        cards, total = await AdminService.get_undropped_cards_paginated(
            session, page=callback_data.page, page_size=PAGE_SIZE
        )
    txt = (
        f"➕ <b>FOYDALANUVCHIGA KARTA BERISH</b>\n"
        f"Hali guruhga tashlanmagan kartalar (<b>{total}</b> ta):"
        if cards
        else "📭 Hozircha guruhga tashlanmagan bo‘sh kartalar yo‘q."
    )
    await edit_or_answer(
        callback,
        txt,
        reply_markup=admin_pick_card_for_user_kb(
            callback.from_user.id, callback_data.target_id, cards, callback_data.page, total, page_size=PAGE_SIZE
        ),
    )


@router.callback_query(AdminCb.filter((F.section == "users") & (F.action == "give_exec")))
async def cb_admin_give_card_exec(callback: CallbackQuery, callback_data: AdminCb) -> None:
    card_id = int(callback_data.extra) if callback_data.extra.isdigit() else 0
    async with get_session() as session:
        card = await AdminService.give_card_to_user(session, callback.from_user.id, callback_data.target_id, card_id)
        u = await session.get(User, callback_data.target_id) if card else None
        granted_lvl = max(1, int(getattr(card, "preset_level", 1) or 1)) if card else 1
        await session.commit()
    if card:
        lvl_note = f" (Lvl {granted_lvl})" if granted_lvl > 1 else ""
        if u:
            try:
                await callback.bot.send_message(
                    u.telegram_id,
                    f"🎁 Admin sizga <b>{esc(card.character_name)}</b>{lvl_note} ({esc(get_rarity_meta(card.rarity).name)}) kartasini sovg‘a qildi!",
                )
            except Exception:
                pass
        await callback.answer(f"✅ {card.character_name}{lvl_note} berildi!", show_alert=True)
    else:
        await callback.answer("❌ Berilmadi: karta yoki foydalanuvchi topilmadi.", show_alert=True)
    await _render_admin_user(callback, callback.from_user.id, callback_data.target_id)


@router.callback_query(AdminCb.filter((F.section == "users") & (F.action == "rem_pick")))
async def cb_admin_rem_card_pick(callback: CallbackQuery, callback_data: AdminCb) -> None:
    async with get_session() as session:
        items = await AdminService.get_user_cards_for_admin(session, callback_data.target_id, limit=20)
    text = "➖ <b>FOYDALANUVCHIDAN KARTA OLISH</b>\n\nOlib qo‘yiladigan kartani tanlang:" if items else "📭 Bu foydalanuvchida bo‘sh karta yo‘q."
    await edit_or_answer(
        callback,
        text,
        reply_markup=admin_user_cards_remove_kb(callback.from_user.id, callback_data.target_id, items),
    )


@router.callback_query(AdminCb.filter((F.section == "users") & (F.action == "rem_exec")))
async def cb_admin_rem_card_exec(callback: CallbackQuery, callback_data: AdminCb) -> None:
    card_id = int(callback_data.extra) if callback_data.extra.isdigit() else 0
    async with get_session() as session:
        ok = await AdminService.remove_card_from_user(session, callback.from_user.id, callback_data.target_id, card_id)
        if ok:
            await session.commit()
        else:
            await session.rollback()
    await callback.answer("✅ Karta olib tashlandi!" if ok else "❌ Bo‘sh nusxa topilmadi.", show_alert=True)
    await _render_admin_user(callback, callback.from_user.id, callback_data.target_id)


@router.callback_query(AdminCb.filter((F.section == "users") & (F.action == "msg_prompt")))
async def cb_admin_user_msg_prompt(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    await state.set_state(AdminStates.user_dm_text)
    await state.update_data(owner_tg_id=callback.from_user.id, target_user_id=callback_data.target_id)
    await edit_or_answer(
        callback,
        "✉️ <b>USERGA XABAR YUBORISH</b>\n\n"
        "Xabarni yozing. Bot avval shaxsiy lichkaga yuboradi, agar botga start bosmagan bo‘lsa — u faol bo‘lgan guruhda uni belgilab yuboradi!\n<i>Bekor: /cancel</i>",
    )


@router.message(AdminStates.user_dm_text, NotCommandFilter())
async def fsm_admin_user_dm(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    uid = int(data["target_user_id"])
    text = (message.text or "").strip()
    if not text:
        await message.answer("⚠️ Xabar matnini yozing.")
        return
    await state.clear()

    async with get_session() as session:
        user = await session.get(User, uid)
        grp_chat_id = None
        if user:
            grp_chat_id = (
                await session.execute(
                    select(Group.telegram_chat_id)
                    .join(GroupMember, GroupMember.group_id == Group.id)
                    .where(GroupMember.user_id == user.id, Group.is_active.is_(True))
                    .order_by(GroupMember.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
    if user is None:
        await message.answer("❌ Foydalanuvchi topilmadi.")
        return

    delivered = ""
    try:
        await message.bot.send_message(user.telegram_id, f"📩 <b>Admin xabari:</b>\n\n{esc(text)}")
        delivered = "✅ Xabar foydalanuvchining shaxsiy chatiga yuborildi!"
    except Exception:
        if grp_chat_id:
            mention = f"@{user.username}" if user.username else f"<a href='tg://user?id={user.telegram_id}'>{esc(user.first_name)}</a>"
            try:
                await message.bot.send_message(grp_chat_id, f"📩 {mention}, sizga admindan xabar:\n\n{esc(text)}")
                delivered = "✅ Lichka yopiq ekan — xabar foydalanuvchi bor guruhga teglab yuborildi!"
            except Exception as exc:
                delivered = f"❌ Yuborib bo‘lmadi: {esc(str(exc))}"
        else:
            delivered = "❌ User botga start bosmagan va umumiy guruh topilmadi."
    await message.answer(delivered)
    await _render_admin_user(message, message.from_user.id, uid)


@router.callback_query(AdminCb.filter(F.section == "ban"))
async def cb_admin_ban_toggle(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    act = callback_data.action
    uid = callback_data.target_id
    if act in ("temp_menu", "temp_hrs"):
        async with get_session() as session:
            u_chk = await session.get(User, uid)
            if u_chk is not None and await AdminService.is_protected_admin_user(session, u_chk):
                await callback.answer("⛔ Bu admin! Uni ban qilib bo‘lmaydi.", show_alert=True)
                return
    if act == "temp_menu":
        b = InlineKeyboardBuilder()
        for lbl, hrs in [("⏱ 1 soat", 1), ("⏱ 6 soat", 6), ("⏱ 24 soat", 24), ("📅 3 kun", 72), ("📅 7 kun", 168), ("📅 30 kun", 720)]:
            b.row(InlineKeyboardButton(text=lbl, callback_data=AdminCb(owner_id=callback.from_user.id, section="ban", action="temp_hrs", target_id=uid, extra=str(hrs)).pack()))
        b.row(InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdminCb(owner_id=callback.from_user.id, section="users", action="detail", target_id=uid).pack()))
        await edit_or_answer(callback, "⏱ <b>VAQTLI BAN MUDDATINI TANLANG:</b>", reply_markup=b.as_markup())
        return
    if act == "temp_hrs" and callback_data.extra.isdigit():
        await state.set_state(AdminStates.user_temp_ban_reason)
        await state.update_data(owner_tg_id=callback.from_user.id, target_user_id=uid, hours=int(callback_data.extra))
        await edit_or_answer(callback, f"⏱ Muddat: <b>{callback_data.extra} soat</b>.\nEndi ban <b>sababini</b> yozing:\n<i>Bekor: /cancel</i>")
        return

    ban = act == "ban"
    async with get_session() as session:
        try:
            u = await AdminService.set_global_ban(session, callback.from_user.id, uid, ban=ban)
            await session.commit()
        except PermissionError as exc:
            await session.rollback()
            await callback.answer(f"⛔ {exc}", show_alert=True)
            return
    if u:
        await _notify_user_ban_status(callback.bot, u.telegram_id, ban, reason="Admin qarori")
    await callback.answer("🚫 Global ban qo‘yildi!" if ban else "🔓 Ban olib tashlandi!", show_alert=True)
    await _render_admin_user(callback, callback.from_user.id, uid)


@router.message(AdminStates.user_temp_ban_reason, NotCommandFilter())
async def fsm_user_temp_ban_reason(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    uid = int(data["target_user_id"])
    hrs = int(data["hours"])
    reason = (message.text or "Vaqtinchalik blok").strip()
    await state.clear()
    async with get_session() as session:
        u = await session.get(User, uid)
        if u is None:
            await message.answer("❌ User topilmadi.")
            return
        try:
            await SecurityBackupService.ban_user(session, message.from_user.id, u, reason, hours=hrs)
            await session.commit()
        except ValueError as exc:
            await session.rollback()
            await message.answer(f"⛔ {exc}")
            await _render_admin_user(message, message.from_user.id, uid)
            return
    await _notify_user_ban_status(message.bot, u.telegram_id, True, reason=reason, hours=hrs)
    await message.answer(f"✅ User <b>{hrs} soatga</b> bloklandi!")
    await _render_admin_user(message, message.from_user.id, uid)
