"""Admin paneli bosh sahifasi, /admin buyruqlari va statistika."""

import logging
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from admin.filters import IsAdminFilter, has_admin_perm, resolve_admin_perms
from admin.handlers.common import _AdminRbacCallbackMiddleware
from database.session import get_session
from keyboards.v2 import admin_v2_home_kb
from models.card import Card
from models.group import Group
from models.schedule import ActiveDrop
from models.user import User
from models.v2_features import SecurityIncident, SupportTicket
from services.admin_service import AdminService
from services.card_service import CardService
from services.community_service import CommunityService
from services.drop_service import DropService
from services.gameplay_service import GameplayService
from services.settings_service import SettingsService
from utils.callback_data import AdminCb
from utils.formatting import format_drop_message
from utils.tg_helpers import edit_or_answer, get_bot_username, send_card_message

logger = logging.getLogger(__name__)
router = Router(name="admin_home")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())
router.callback_query.middleware(_AdminRbacCallbackMiddleware())


async def _build_admin_home_text() -> str:
    async with get_session() as session:
        u_cnt = int((await session.execute(select(func.count(User.id)))).scalar_one() or 0)
        g_cnt = int((await session.execute(select(func.count(Group.id)).where(Group.is_active.is_(True)))).scalar_one() or 0)
        c_cnt = int((await session.execute(select(func.count(Card.id)).where(Card.is_deleted.is_(False)))).scalar_one() or 0)
        open_sup = int((await session.execute(select(func.count(SupportTicket.id)).where(SupportTicket.status == "open"))).scalar_one() or 0)
        sec_cnt = int((await session.execute(select(func.count(SecurityIncident.id)).where(SecurityIncident.is_resolved.is_(False)))).scalar_one() or 0)
        gcfg = await GameplayService.get_game_config(session)
        maint = await SettingsService.get_value(session, "maintenance", {"enabled": False})
    pack_lim = int(gcfg.get("pack_daily_limit", 1))
    maint_txt = "🔴 Yoqilgan" if (isinstance(maint, dict) and maint.get("enabled")) else "🟢 O‘chiq"
    return (
        "🛡️ <b>ADMIN BOSHQARUV MARKAZI</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"👤 Userlar: <b>{u_cnt:,}</b>  ·  👥 Guruhlar: <b>{g_cnt:,}</b>  ·  🎴 Kartalar: <b>{c_cnt:,}</b>\n"
        f"🎁 Paket limiti: <b>{pack_lim}/kun</b>  ·  💬 Murojaat: <b>{open_sup}</b>  ·  🔐 Hodisa: <b>{sec_cnt}</b>\n"
        f"🛠 Texnik rejim: {maint_txt}\n\n"
        "👇 Kerakli boshqaruv toifasini tanlang:"
    )


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext, admin_perms: set | None = None) -> None:
    await state.clear()
    perms = resolve_admin_perms(message.from_user, admin_perms)
    await message.answer(
        await _build_admin_home_text(),
        reply_markup=admin_v2_home_kb(message.from_user.id, perms),
    )


@router.message(Command("drop"))
async def cmd_admin_instant_drop(message: Message, db_group: Optional[Group] = None, admin_perms: set | None = None) -> None:
    if not has_admin_perm(message.from_user, admin_perms, "cards") and not has_admin_perm(message.from_user, admin_perms, "groups"):
        return
    if db_group is None:
        await message.answer("⚠️ Bu buyruq guruh ichida ishlaydi.")
        return

    async with get_session() as session:
        group = await session.get(Group, db_group.id)
        card, forced = (None, False)
        drop = None
        if group is not None:
            card, forced = await DropService.pick_card_for_group(session, group)
        if card is not None:
            drop = await DropService.create_active_drop(session, group, card, force=forced)
        if group is None or card is None or drop is None:
            await message.answer("❌ Hozircha tashlash uchun mos karta mavjud emas.")
            return
        bot_username = await get_bot_username(message.bot)
        active_event = await CardService.get_active_event(session)
        ad_footer = await CommunityService.get_rendered_ad(
            session, bot_username=bot_username, user=None, placement="group"
        )
        await session.commit()

    caption = format_drop_message(card, active_event=active_event, ad_footer=ad_footer)
    sent_msg = await send_card_message(message.bot, group.telegram_chat_id, card, caption, reply_markup=None)
    async with get_session() as session:
        d_row = await session.get(ActiveDrop, drop.id)
        if d_row is not None:
            d_row.telegram_message_id = sent_msg.message_id
            await session.commit()


@router.callback_query(AdminCb.filter(F.section == "home"))
async def cb_admin_home(callback: CallbackQuery, state: FSMContext, admin_perms: set | None = None) -> None:
    await state.clear()
    perms = resolve_admin_perms(callback.from_user, admin_perms)
    await edit_or_answer(
        callback,
        await _build_admin_home_text(),
        reply_markup=admin_v2_home_kb(callback.from_user.id, perms),
    )


# ---------------------------------------------------------------------------
# STATISTICS
# ---------------------------------------------------------------------------
@router.callback_query(AdminCb.filter(F.section == "stats"))
async def cb_admin_stats(callback: CallbackQuery, admin_perms: set | None = None) -> None:
    if not has_admin_perm(callback.from_user, admin_perms, "stats"):
        await callback.answer("⛔ Bu bo‘limga ruxsatingiz yo‘q.", show_alert=True)
        return
    async with get_session() as session:
        st = await AdminService.get_global_stats(session)
        rep = await AdminService.get_v2_activity_report(session)
    perms = resolve_admin_perms(callback.from_user, admin_perms)
    text = (
        "📊 <b>BOT STATISTIKASI</b>\n\n"
        f"👤 Jami foydalanuvchilar: <b>{st['total_users']:,}</b>\n"
        f"🟢 24 soat faollar: <b>{rep['active_users_24h']:,}</b>\n"
        f"🚫 Ban qilinganlar: <b>{st['banned_users']:,}</b>\n"
        f"👥 Jami guruhlar: <b>{st['total_groups']:,}</b> (Faol: <b>{rep['active_groups']:,}</b>)\n"
        f"🎴 Katalogdagi kartalar: <b>{st['total_cards']:,}</b>\n"
        f"📦 Foydalanuvchilardagi nusxalar: <b>{st['total_copies']:,}</b>\n"
        f"🛒 Faol market e’lonlari: <b>{st['active_listings']:,}</b>\n"
        f"🪙 Iqtisodiyotdagi jami coinlar: <b>{st['total_coins']:,}</b>\n"
        f"🎮 Bugungi o‘yinlar: <b>{rep['games_today']}</b> · 🎁 Daily: <b>{rep['daily_claims_today']}</b>"
    )
    await edit_or_answer(callback, text, reply_markup=admin_v2_home_kb(callback.from_user.id, perms))
