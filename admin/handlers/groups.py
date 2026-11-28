"""Ustuvor karta, kunlik drop jadvali va guruhlarni boshqarish."""

import logging
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select

from admin.filters import IsAdminFilter, resolve_admin_perms
from admin.handlers.common import AdminStates, _AdminRbacCallbackMiddleware
from config.settings import get_settings
from database.session import get_session
from keyboards.admin import (
    admin_group_settings_kb,
    admin_groups_list_kb,
    admin_home_kb,
    admin_schedule_detail_kb,
)
from keyboards.v2 import admin_v2_home_kb
from models.card import Card
from models.group import Group
from models.schedule import ActiveDrop
from models.user import User
from services.admin_service import AdminService
from services.card_service import CardService
from services.community_service import CommunityService
from services.drop_service import DropService
from services.settings_service import SettingsService
from utils.callback_data import AdminCb
from utils.constants import PAGE_SIZE, get_rarity_meta
from utils.formatting import esc, format_drop_message, format_group_settings
from utils.tg_helpers import NotCommandFilter, edit_or_answer, get_bot_username, send_card_visual
from utils.timeutils import fmt_tashkent

logger = logging.getLogger(__name__)
router = Router(name="admin_groups")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())
router.callback_query.middleware(_AdminRbacCallbackMiddleware())


# ---------------------------------------------------------------------------
# PRIORITY CARD
# ---------------------------------------------------------------------------
@router.callback_query(AdminCb.filter(F.section == "priority"))
async def cb_admin_priority(callback: CallbackQuery, callback_data: AdminCb) -> None:
    async with get_session() as session:
        if callback_data.action == "set":
            await SettingsService.set_next_priority_card_id(session, callback_data.target_id)
            await AdminService.log_action(session, callback.from_user.id, "set_priority_card", "card", str(callback_data.target_id))
            await session.commit()
            await callback.answer("⭐ Keyingi drop uchun priority karta belgilandi!", show_alert=True)
        elif callback_data.action == "clear":
            await SettingsService.set_next_priority_card_id(session, None)
            await session.commit()
            await callback.answer("Priority o‘chirildi.")

        pid = await SettingsService.get_next_priority_card_id(session)
        p_card = await session.get(Card, pid) if pid else None

    if p_card:
        pm = get_rarity_meta(p_card.rarity)
        text = (
            f"🎯 <b>NAVBATDAGI MAXSUS KARTA</b>\n━━━━━━━━━━━━━━━\n"
            f"🎴 {esc(p_card.character_name)} ({pm.emoji} {esc(pm.name)}) [<code>{esc(p_card.card_code)}</code>]\n"
            f"Keyingi dropda shu karta tushadi va navbat avtomatik o‘chadi."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="❌ Bekor qilish", callback_data=AdminCb(owner_id=callback.from_user.id, section="priority", action="clear").pack())],
                [InlineKeyboardButton(text="⬅️ Sozlamalar", callback_data=AdminCb(owner_id=callback.from_user.id, section="settings").pack())],
            ]
        )
    else:
        text = (
            "🎯 <b>NAVBATDAGI MAXSUS KARTA</b>\n━━━━━━━━━━━━━━━\n"
            "Hozircha navbatdagi karta tanlanmagan.\n"
            "Jadvaldagi istalgan bo‘sh slotga karta tanlashingiz mumkin."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📅 Drop jadvali", callback_data=AdminCb(owner_id=callback.from_user.id, section="schedule", action="overview").pack())],
                [InlineKeyboardButton(text="⬅️ Sozlamalar", callback_data=AdminCb(owner_id=callback.from_user.id, section="settings").pack())],
            ]
        )
    await edit_or_answer(callback, text, reply_markup=kb)


# ---------------------------------------------------------------------------
# DAILY DROP SCHEDULE & GROUP MANAGEMENT
# ---------------------------------------------------------------------------
@router.callback_query(AdminCb.filter((F.section == "schedule") & (F.action == "overview")))
async def cb_schedule_overview(callback: CallbackQuery) -> None:
    tz = ZoneInfo(get_settings().timezone)
    async with get_session() as session:
        groups = list((await session.execute(select(Group).order_by(Group.id.desc()))).scalars().all())
        for g in groups:
            if g.is_active and g.drop_enabled:
                await DropService.ensure_daily_schedule_for_group(session, g)
        await session.commit()
        upcoming = await AdminService.get_upcoming_schedules_all_groups(session, limit=15)

    lines = ["📅 <b>BUGUNGI DROP JADVALI</b>\n"]
    if not upcoming:
        lines.append("⏳ Hozircha rejalashtirilgan navbatdagi droplar yo‘q.")
    else:
        for sched, grp, card in upcoming:
            t_str = sched.scheduled_at.astimezone(tz).strftime("%H:%M")
            c_name = f"{card.character_name} ({get_rarity_meta(card.rarity).name})" if card else "Tasodifiy"
            mark = "⭐" if sched.is_priority_override else "⏰"
            lines.append(f"{mark} <code>{t_str}</code> — <b>{esc(grp.title)}</b> → {esc(c_name)}")
    await edit_or_answer(
        callback,
        "\n".join(lines),
        reply_markup=admin_groups_list_kb(callback.from_user.id, groups[:20], section="schedule", page=1, total=len(groups)),
    )


@router.callback_query(AdminCb.filter((F.section == "schedule") & (F.action == "groups")))
@router.callback_query(AdminCb.filter((F.section == "groups") & (F.action == "list")))
async def cb_admin_groups_list(callback: CallbackQuery, callback_data: AdminCb) -> None:
    page = max(1, callback_data.page)
    filter_mode = callback_data.extra or ("active" if callback_data.section == "groups" else "all")
    cond = []
    if callback_data.section == "groups":
        if filter_mode == "active":
            cond.append(Group.is_active.is_(True))
            cond.append(Group.bot_status == "active")
        elif filter_mode == "left":
            cond.append((Group.bot_status.in_(["left", "kicked"])) | Group.is_active.is_(False))
    async with get_session() as session:
        total = int((await session.execute(select(func.count(Group.id)).where(*cond))).scalar_one() or 0)
        offset = (page - 1) * PAGE_SIZE
        groups = list(
            (await session.execute(select(Group).where(*cond).order_by(Group.is_active.desc(), Group.id.desc()).offset(offset).limit(PAGE_SIZE)))
            .scalars()
            .all()
        )
    f_labels = {"active": "FAOL GURUHLAR", "left": "CHIQARILGAN / O‘CHGAN GURUHLAR", "all": "BARCHA GURUHLAR"}
    title = (
        "📅 <b>DROP JADVALI — GURUH TANLANG</b>"
        if callback_data.section == "schedule"
        else f"👥 <b>{f_labels.get(filter_mode, 'GURUHLAR')} ({total} ta)</b>"
    )
    if not groups and callback_data.section != "groups":
        await edit_or_answer(
            callback,
            f"{title}\n\n📭 Hozircha ro‘yxatga olingan guruhlar yo‘q.",
            reply_markup=admin_home_kb(callback.from_user.id),
        )
        return
    if not groups:
        title += "\n\n📭 Bu bo‘limda guruhlar yo‘q."
    await edit_or_answer(
        callback,
        title,
        reply_markup=admin_groups_list_kb(
            callback.from_user.id,
            groups,
            section=callback_data.section,
            page=page,
            total=total,
            page_size=PAGE_SIZE,
            filter_mode=filter_mode,
        ),
    )


async def _render_schedule_detail(callback: CallbackQuery, group_id: int) -> None:
    tz = ZoneInfo(get_settings().timezone)
    async with get_session() as session:
        group = await session.get(Group, group_id)
        if group is None:
            await callback.answer("Guruh topilmadi.", show_alert=True)
            return
        schedules = await DropService.ensure_daily_schedule_for_group(session, group)
        await session.commit()

        lines = [f"📅 <b>BUGUNGI JADVAL — {esc(group.title)}</b> ({group.daily_drop_count}/kun)\n"]
        for sc in schedules:
            local_t = sc.scheduled_at.astimezone(tz).strftime("%H:%M")
            card_label = "Tasodifiy"
            if sc.card_id:
                c = await session.get(Card, sc.card_id)
                if c:
                    card_label = f"{c.character_name} ({get_rarity_meta(c.rarity).name})"
            st_icon = "✅" if sc.status == "sent" else ("⭐" if sc.is_priority_override else "⏳")
            lines.append(f"{st_icon} <code>{local_t}</code> — {esc(card_label)} [#{sc.id}]")
        text = "\n".join(lines) if schedules else "Bugun uchun drop rejalashtirilmagan."
    await edit_or_answer(callback, text, reply_markup=admin_schedule_detail_kb(callback.from_user.id, group_id, schedules))


@router.callback_query(AdminCb.filter((F.section == "schedule") & (F.action == "group_view")))
async def cb_schedule_group_view(callback: CallbackQuery, callback_data: AdminCb) -> None:
    await _render_schedule_detail(callback, callback_data.target_id)


@router.callback_query(AdminCb.filter((F.section == "schedule") & (F.action == "regen")))
async def cb_schedule_regen(callback: CallbackQuery, callback_data: AdminCb) -> None:
    async with get_session() as session:
        group = await session.get(Group, callback_data.target_id)
        if group:
            await DropService.ensure_daily_schedule_for_group(session, group, force_regenerate=True)
            await session.commit()
    await callback.answer("🔄 Jadval qayta yaratildi!")
    await _render_schedule_detail(callback, callback_data.target_id)


@router.callback_query(AdminCb.filter((F.section == "schedule") & (F.action == "slot_prio")))
async def cb_slot_priority_prompt(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    await state.set_state(AdminStates.slot_priority_card)
    await state.update_data(slot_id=callback_data.target_id)
    await edit_or_answer(
        callback,
        f"⭐ Slot #{callback_data.target_id} ga qo‘ymoqchi bo‘lgan Card kodini (masalan <code>CRD-0001</code>) yozing:",
    )


@router.message(AdminStates.slot_priority_card, NotCommandFilter())
async def fsm_slot_priority_card(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    slot_id = int(data["slot_id"])
    ref = (message.text or "").strip()
    await state.clear()
    async with get_session() as session:
        card = (await session.execute(select(Card).where(func.lower(Card.card_code) == ref.lower()))).scalar_one_or_none()
        if card is None and ref.isdigit():
            card = await session.get(Card, int(ref))
        if card is None:
            await message.answer("❌ Karta topilmadi.", reply_markup=admin_home_kb(message.from_user.id))
            return
        sched = await DropService.replace_schedule_card(session, slot_id, card.id)
        await session.commit()
    if sched is None:
        await message.answer("❌ Slot topilmadi yoki allaqachon yuborilgan.")
        return
    from utils.formatting import format_card_block
    from utils.tg_helpers import send_card_message

    cap = f"✅ <b>Slot #{slot_id} ga qo‘yildi!</b>\n\n{format_card_block(card, show_status=True)}"
    try:
        await send_card_message(message.bot, message.chat.id, card, cap[:1020], admin_home_kb(message.from_user.id))
    except Exception:
        await message.answer(cap, reply_markup=admin_home_kb(message.from_user.id))


async def _render_group_detail(target, owner_id: int, group_id: int) -> None:
    async with get_session() as session:
        group = await session.get(Group, group_id)
        if group is None:
            await edit_or_answer(target, "❌ Guruh topilmadi.", reply_markup=admin_home_kb(owner_id))
            return
        analytics = await AdminService.get_group_analytics(session, group.id)
        from services.group_policy_service import GroupPolicyService

        link = await GroupPolicyService.resolve_link(
            getattr(target, "bot", None), session, group.telegram_chat_id, group.username, try_create=False
        )
        adder = await session.get(User, group.added_by_user_id) if group.added_by_user_id else None
        await session.commit()
    adder_line = (
        f"👤 Botni qo‘shgan: <b>{esc(adder.display_name)}</b> · <code>{adder.telegram_id}</code>\n"
        if adder is not None
        else "👤 Botni qo‘shgan: noma’lum\n"
    )
    link_line = "" if group.username else (f"🔗 Havola: {esc(link)}\n" if link else "🔗 Havola: yo‘q (yopiq guruh)\n")
    bot_st = getattr(group, "bot_status", "active")
    if bot_st in ("left", "kicked") or not group.is_active:
        st_txt = f"🗑 Guruhdan chiqarilgan ({esc(bot_st)})"
        if getattr(group, "left_at", None):
            st_txt += f" · <code>{fmt_tashkent(group.left_at, '%d.%m.%Y %H:%M')}</code>"
        if getattr(group, "status_reason", None):
            st_txt += f"\n📝 Sabab: {esc(group.status_reason)}"
    else:
        st_txt = "🟢 Faol"
    text = (
        f"{format_group_settings(group)}\n"
        f"{adder_line}{link_line}"
        f"👤 Faol a’zolar (bazada): <b>{analytics['active_members']}</b>\n"
        f"📦 Jami droplar: <b>{analytics['total_drops']}</b> (Olingani: <b>{analytics['claimed_drops']}</b>)\n"
        f"🔌 Bot holati: <b>{st_txt}</b>"
    )
    await edit_or_answer(target, text, reply_markup=admin_group_settings_kb(owner_id, group, link=link))


@router.callback_query(
    AdminCb.filter(
        (F.section == "groups")
        & (
            F.action.in_(
                {"group_view", "toggle_drop", "toggle_auto", "drop_inc", "drop_dec", "drop_now", "drop_set", "msg_prompt", "top_users", "remove_prompt"}
            )
        )
    )
)
async def cb_admin_group_control(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    act = callback_data.action
    gid = callback_data.target_id

    if act == "drop_set":
        await state.set_state(AdminStates.group_drop_count)
        await state.update_data(group_id=gid, owner_tg_id=callback.from_user.id)
        await edit_or_answer(callback, "🔢 Kunlik drop sonini kiriting (0 dan 500 gacha):\n<i>Bekor: /cancel</i>")
        return

    if act == "msg_prompt":
        await state.set_state(AdminStates.group_msg_text)
        await state.update_data(group_id=gid, owner_tg_id=callback.from_user.id)
        await edit_or_answer(callback, "✉️ Guruhga yuboriladigan xabar matnini (yoki rasm/video) yuboring:\n<i>Bekor: /cancel</i>")
        return

    if act == "remove_prompt":
        await state.set_state(AdminStates.group_remove_reason)
        await state.update_data(group_id=gid, owner_tg_id=callback.from_user.id)
        await edit_or_answer(
            callback,
            "❌ <b>GURUHNI O‘CHIRISH</b>\n\nGuruhga xayrlashuv xabari bilan birga yuboriladigan <b>sababni</b> yozing (bot guruhdan chiqib ketadi):\n<i>Bekor: /cancel</i>",
        )
        return

    if act == "top_users":
        async with get_session() as session:
            grp = await session.get(Group, gid)
            analytics = await AdminService.get_group_analytics(session, gid)
        lines = [f"🏆 <b>{esc(grp.title if grp else gid)} — ENG FAOL USERLAR</b>\n"]
        for idx, (u, cnt) in enumerate(analytics["top_users"], 1):
            lines.append(f"{idx}. {esc(u.display_name)} — <b>{cnt}</b> ta karta olgan")
        if not analytics["top_users"]:
            lines.append("📭 Hali bu guruhda kartalar olinmagan.")
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Guruhga qaytish", callback_data=AdminCb(owner_id=callback.from_user.id, section="groups", action="group_view", target_id=gid).pack())]]
        )
        await edit_or_answer(callback, "\n".join(lines), reply_markup=kb)
        return

    async with get_session() as session:
        group = await session.get(Group, gid)
        if group is None:
            await callback.answer("Guruh topilmadi.", show_alert=True)
            return

        if act == "toggle_drop":
            group.drop_enabled = not group.drop_enabled
        elif act == "toggle_auto":
            group.auto_drop_count = not group.auto_drop_count
        elif act == "drop_inc":
            group.auto_drop_count = False
            group.daily_drop_count = min(500, group.daily_drop_count + 1)
        elif act == "drop_dec":
            group.auto_drop_count = False
            group.daily_drop_count = max(0, group.daily_drop_count - 1)
        elif act == "drop_now":
            card, forced = await DropService.pick_card_for_group(session, group)
            drop = await DropService.create_active_drop(session, group, card, force=forced) if card else None
            if card is None or drop is None:
                await callback.answer("Mos karta yo‘q!", show_alert=True)
                return
            bot_username = await get_bot_username(callback.bot)
            active_event = await CardService.get_active_event(session)
            ad_footer = await CommunityService.get_rendered_ad(
                session, bot_username=bot_username, user=None, placement="group"
            )
            await session.commit()
            caption = format_drop_message(card, active_event=active_event, ad_footer=ad_footer)
            try:
                msg = await send_card_visual(
                    bot=callback.bot, chat_id=group.telegram_chat_id, image=card.image, caption=caption, reply_markup=None
                )
                async with get_session() as s2:
                    d2 = await s2.get(ActiveDrop, drop.id)
                    if d2:
                        d2.telegram_message_id = msg.message_id
                        await s2.commit()
                await callback.answer("⚡ Drop guruhga yuborildi!", show_alert=True)
            except Exception:
                await callback.answer("❌ Guruhga yuborib bo‘lmadi.", show_alert=True)
            return

        await session.commit()
    await _render_group_detail(callback, callback.from_user.id, gid)


@router.message(AdminStates.group_drop_count, NotCommandFilter())
async def fsm_group_drop_count(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or not (0 <= int(raw) <= 500):
        await message.answer("⚠️ 0 dan 500 gacha son kiriting.")
        return
    gid = int(data["group_id"])
    await state.clear()
    async with get_session() as session:
        grp = await session.get(Group, gid)
        if grp:
            grp.auto_drop_count = False
            grp.daily_drop_count = int(raw)
            await session.commit()
    await message.answer(f"✅ Kunlik drop soni <b>{raw}</b> ta qilib saqlandi!")
    await _render_group_detail(message, message.from_user.id, gid)


@router.message(AdminStates.group_msg_text, NotCommandFilter())
async def fsm_group_msg_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    gid = int(data["group_id"])
    await state.clear()
    async with get_session() as session:
        grp = await session.get(Group, gid)
    if grp is None:
        await message.answer("❌ Guruh topilmadi.")
        return
    try:
        await message.bot.copy_message(chat_id=grp.telegram_chat_id, from_chat_id=message.chat.id, message_id=message.message_id)
        await message.answer("✅ Xabar guruhga yuborildi!")
    except Exception as exc:
        await message.answer(f"❌ Yuborib bo‘lmadi: {esc(str(exc))}")
    await _render_group_detail(message, message.from_user.id, gid)


@router.message(AdminStates.group_remove_reason, NotCommandFilter())
async def fsm_group_remove_reason(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    reason = (message.text or "Admin qarori").strip()
    gid = int(data["group_id"])
    await state.clear()
    async with get_session() as session:
        grp = await session.get(Group, gid)
        if grp is None:
            await message.answer("❌ Guruh topilmadi.")
            return
        chat_id = grp.telegram_chat_id
        title = grp.title
        grp.is_active = False
        grp.drop_enabled = False
        await AdminService.log_action(session, message.from_user.id, "remove_group", "group", str(gid), {"reason": reason})
        await session.commit()
    try:
        await message.bot.send_message(
            chat_id,
            f"👋 <b>Bot guruhdan chiqarildi.</b>\n📌 Sabab: {esc(reason)}",
        )
    except Exception:
        pass
    try:
        await message.bot.leave_chat(chat_id)
    except Exception:
        pass
    await message.answer(
        f"✅ Guruh o‘chirildi va bot tark etdi: <b>{esc(title)}</b>",
        reply_markup=admin_v2_home_kb(message.from_user.id, resolve_admin_perms(message.from_user, None)),
    )
