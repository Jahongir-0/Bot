import asyncio
import logging
import re
from typing import Optional
from zoneinfo import ZoneInfo

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from admin.filters import IsAdminFilter, has_admin_perm, resolve_admin_perms
from app.event_announcer import announce_event
from config.settings import get_settings
from database.session import get_session
from keyboards.admin import (
    admin_groups_list_kb,
    admin_group_settings_kb,
    admin_home_kb,
    admin_pick_card_for_user_kb,
    admin_pick_group_for_card_kb,
    admin_schedule_detail_kb,
    admin_settings_kb,
    admin_stars_back_kb,
    admin_stars_confirm_kb,
    admin_stars_kb,
    admin_user_cards_remove_kb,
    admin_user_detail_kb,
    admin_users_list_kb,
    admin_users_menu_kb,
)
from keyboards.event import (
    admin_evt_candidates_kb,
    admin_evt_confirm_kb,
    admin_evt_detail_kb,
    admin_evt_duration_kb,
    admin_evt_menu_kb,
    admin_evt_price_type_kb,
    admin_evt_stars_kb,
)
from keyboards.v2 import admin_v2_cards_menu_kb, admin_v2_home_kb
from models.base import ensure_utc
from utils.timeutils import fmt_tashkent, to_tashkent
from models.card import Card
from models.event import Event
from models.group import Group, GroupMember
from models.schedule import ActiveDrop
from models.user import User
from models.v2_features import SecurityIncident, SupportTicket
from services.admin_service import AdminService
from services.auction_wishlist_service import AuctionWishlistService
from services.card_service import CardService
from services.community_service import CommunityService
from services.drop_service import DropService
from services.event_service import EventService
from services.gameplay_service import GameplayService
from services.ranking_service import RankingService
from services.security_backup_service import SecurityBackupService
from services.settings_service import SettingsService
from utils.callback_data import AdmV2Cb, AdminCb, NavCb
from utils.constants import EVENT_DURATION_HOURS, MEDIA_TYPES, PAGE_SIZE, RARITY_REGISTRY, RankingMode, get_rarity_meta
from utils.formatting import (
    esc,
    fmt_coins,
    format_card_block,
    format_drop_message,
    format_group_settings,
)
from utils.image_check import ERR_FORMAT, validate_image_url
from utils.tg_helpers import NotCommandFilter, edit_or_answer, get_bot_username, send_card_message, send_card_visual

logger = logging.getLogger(__name__)

_LEGACY_SECTION_PERM_MAP = {
    "home": None,
    "cards": "cards",
    "priority": "settings",
    "schedule": "groups",
    "groups": "groups",
    "users": "users",
    "ban": "users",
    "evt": "events",
    "events": "events",
    "stars": "stars",
    "stats": "stats",
    "settings": "settings",
}


class _AdminRbacCallbackMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: CallbackQuery, data: dict):
        if event.data and event.data.startswith("adm:"):
            try:
                cb = AdminCb.unpack(event.data)
                req = _LEGACY_SECTION_PERM_MAP.get(cb.section)
                if req and not has_admin_perm(event.from_user, data.get("admin_perms"), req):
                    await event.answer("⛔ Bu bo‘limga ruxsatingiz yo‘q.", show_alert=True)
                    return None
            except Exception:
                pass
        return await handler(event, data)


router = Router(name="admin")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())
router.callback_query.middleware(_AdminRbacCallbackMiddleware())


class AdminStates(StatesGroup):
    # Add Card (6 steps, 0 pipes)
    add_card_character = State()
    add_card_media_type = State()
    add_card_anime = State()
    add_card_meta = State()
    add_card_rarity = State()
    add_card_image = State()

    # Edit Card numeric/image
    edit_card_image = State()
    edit_card_value = State()
    edit_card_limit = State()

    # Users
    user_search = State()
    user_coin_delta = State()
    user_dm_text = State()
    user_temp_ban_hours = State()
    user_temp_ban_reason = State()

    # Groups & Schedule
    slot_priority_card = State()
    group_drop_count = State()
    group_msg_text = State()
    group_remove_reason = State()

    # Settings
    edit_ref_inviter = State()
    edit_ref_invitee = State()
    edit_group_reward = State()
    edit_group_cfg_field = State()
    edit_daily_field = State()
    edit_drop_rule_field = State()
    edit_welcome_text = State()
    edit_vip_price = State()
    edit_pack_limit = State()

    # Stars
    stars_add = State()
    stars_edit = State()

    # Events
    evt_copies = State()
    evt_price_coins = State()
    evt_price_stars = State()
    evt_duration = State()

    # Bonus event
    bonus_name = State()
    bonus_hours = State()


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


# ---------------------------------------------------------------------------
# CARD MANAGEMENT (Add Card 6-step, Drop to group, Delete confirm/exec, Edits)
# ---------------------------------------------------------------------------
@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "menu")))
async def cb_admin_cards_menu(callback: CallbackQuery, state: FSMContext, admin_perms: set | None = None) -> None:
    if not has_admin_perm(callback.from_user, admin_perms, "cards"):
        await callback.answer("⛔ Bu bo‘limga ruxsatingiz yo‘q.", show_alert=True)
        return
    await state.clear()
    await edit_or_answer(
        callback,
        "🎴 <b>CARD BO‘LIMI</b>\n\nKerakli bo‘limni tanlang:",
        reply_markup=admin_v2_cards_menu_kb(callback.from_user.id),
    )


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "drop_pick_grp")))
async def cb_admin_card_pick_group(callback: CallbackQuery, callback_data: AdminCb) -> None:
    async with get_session() as session:
        card = await session.get(Card, callback_data.target_id)
        groups = list(
            (await session.execute(select(Group).where(Group.is_active.is_(True), Group.is_banned.is_(False)).order_by(Group.id.desc())))
            .scalars()
            .all()
        )
    if card is None:
        await callback.answer("Karta topilmadi.", show_alert=True)
        return
    if not groups:
        await edit_or_answer(
            callback,
            "❌ Hozircha faol guruhlar yo‘q. Avval botni guruhga qo‘shing.",
            reply_markup=admin_v2_cards_menu_kb(callback.from_user.id),
        )
        return
    await edit_or_answer(
        callback,
        f"📤 <b>GURUHGA TASHLASH</b>\n\n"
        f"🎴 Karta: <b>{esc(card.character_name)}</b> ({esc(card.card_code)})\n"
        f"👇 Qaysi guruhga tashlaymiz?",
        reply_markup=admin_pick_group_for_card_kb(callback.from_user.id, card.id, groups),
    )


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "drop_exec")))
async def cb_admin_card_drop_to_group(callback: CallbackQuery, callback_data: AdminCb) -> None:
    group_id = int(callback_data.extra) if callback_data.extra.isdigit() else 0
    async with get_session() as session:
        card = await session.get(Card, callback_data.target_id)
        group = await session.get(Group, group_id)
        if card is None or group is None:
            await callback.answer("Karta yoki guruh topilmadi.", show_alert=True)
            return
        if card.is_event_card:
            await callback.answer("🎯 Event kartasi guruhga tashlanmaydi.", show_alert=True)
            return
        drop = await DropService.create_active_drop(session, group, card, force=True)
        bot_username = await get_bot_username(callback.bot)
        active_event = await CardService.get_active_event(session)
        ad_footer = await CommunityService.get_rendered_ad(
            session, bot_username=bot_username, user=None, placement="group"
        )
        await AdminService.log_action(
            session, callback.from_user.id, "manual_card_drop", "group", str(group.id), {"card_id": card.id, "card_code": card.card_code}
        )
        await session.commit()

    caption = format_drop_message(card, active_event=active_event, ad_footer=ad_footer)
    try:
        sent_msg = await send_card_visual(
            bot=callback.bot, chat_id=group.telegram_chat_id, image=card.image, caption=caption, reply_markup=None
        )
        async with get_session() as s2:
            d2 = await s2.get(ActiveDrop, drop.id)
            if d2 is not None:
                d2.telegram_message_id = sent_msg.message_id
                await s2.commit()
    except Exception as exc:
        logger.warning("Failed dropping card %s to group %s: %s", card.id, group.telegram_chat_id, exc)
        await callback.answer("❌ Guruhga yuborib bo‘lmadi.", show_alert=True)
        return

    await callback.answer(f"✅ {card.character_name} → {group.title} guruhiga tashlandi!", show_alert=True)
    await edit_or_answer(
        callback,
        f"✅ <b>KARTA GURUHGA TASHLANDI!</b>\n\n🎴 Karta: <b>{esc(card.character_name)}</b>\n👥 Guruh: <b>{esc(group.title)}</b>",
        reply_markup=admin_v2_cards_menu_kb(callback.from_user.id),
    )


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "delete_confirm")))
async def cb_admin_card_delete_confirm(callback: CallbackQuery, callback_data: AdminCb) -> None:
    async with get_session() as session:
        card = await session.get(Card, callback_data.target_id)
    title = f"{card.character_name} ({card.card_code})" if card else f"#{callback_data.target_id}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Arxivlab o‘chirish (userlarda qolsin)",
                    callback_data=AdminCb(
                        owner_id=callback.from_user.id, section="cards", action="delete_exec", target_id=callback_data.target_id, extra="archive"
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🪙 To‘liq o‘chirish + Coin kompensatsiya",
                    callback_data=AdminCb(
                        owner_id=callback.from_user.id, section="cards", action="delete_exec", target_id=callback_data.target_id, extra="refund"
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ BEKOR",
                    callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="cards", act="del_list", p=1).pack(),
                )
            ],
        ]
    )
    await edit_or_answer(
        callback,
        f"🗑 <b>KARTANI O‘CHIRISH: {esc(title)}</b>\n\nMavjud egalardagi nusxalar bilan qanday yo‘l tutamiz?",
        reply_markup=kb,
    )


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "delete_exec")))
async def cb_admin_card_delete_exec(callback: CallbackQuery, callback_data: AdminCb) -> None:
    mode = callback_data.extra or "archive"
    async with get_session() as session:
        affected = await CardService.soft_delete_card(session, callback_data.target_id, migration_mode=mode)
        await AdminService.log_action(
            session, callback.from_user.id, "soft_delete_card", "card", str(callback_data.target_id), {"mode": mode, "affected_copies": affected}
        )
        await session.commit()
    await callback.answer(f"✅ Karta o‘chirildi ({mode}).", show_alert=True)
    await edit_or_answer(
        callback,
        f"✅ Karta o‘chirildi (rejim: <b>{esc(mode)}</b>).",
        reply_markup=admin_v2_cards_menu_kb(callback.from_user.id),
    )


async def _extract_image(message: Message):
    if message.photo:
        file_id = message.photo[-1].file_id
        return file_id, file_id, None
    if message.document is not None:
        return None, None, "⚠️ Rasmni <b>fayl</b> emas, oddiy <b>foto</b> sifatida yuboring (yoki to‘g‘ri havola yozing)."
    if message.text:
        ok, err = await validate_image_url(message.text)
        if ok:
            return message.text.strip(), None, None
        return None, None, err
    return None, None, ERR_FORMAT


def _clean_name(raw: str) -> str:
    return " ".join((raw or "").split())


def _media_type_pick_kb(owner_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i in range(0, len(MEDIA_TYPES), 3):
        builder.row(
            *[
                InlineKeyboardButton(
                    text=mt,
                    callback_data=AdminCb(owner_id=owner_id, section="cards", action="add_mtype", extra=mt).pack(),
                )
                for mt in MEDIA_TYPES[i : i + 3]
            ]
        )
    builder.row(
        InlineKeyboardButton(text="❌ BEKOR", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="v").pack())
    )
    return builder.as_markup()


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "add_start")))
async def cb_admin_add_card_start(callback: CallbackQuery, state: FSMContext, admin_perms: set | None = None) -> None:
    if not has_admin_perm(callback.from_user, admin_perms, "cards"):
        await callback.answer("⛔ Ruxsatingiz yo‘q.", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminStates.add_card_character)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Bekor", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="cards", act="v").pack())]]
    )
    await edit_or_answer(
        callback,
        "➕ <b>YANGI KARTA QO‘SHISH</b>\n\n"
        "1/6: <b>Personaj ismini</b> yozing (masalan: <code>Gojo Satoru</code>):",
        reply_markup=kb,
    )


@router.message(AdminStates.add_card_character, NotCommandFilter())
async def fsm_add_card_char(message: Message, state: FSMContext) -> None:
    name = _clean_name(message.text or "")
    if not name or len(name) > 128:
        await message.answer("⚠️ Personaj ismini matn bilan yozing (1–128 belgi).")
        return
    await state.update_data(character_name=name)
    await state.set_state(AdminStates.add_card_media_type)
    await message.answer(
        "2/6: <b>Media turini</b> tanlang:",
        reply_markup=_media_type_pick_kb(message.from_user.id),
    )


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "add_mtype")))
async def cb_admin_add_card_mtype(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    mtype = callback_data.extra if callback_data.extra in MEDIA_TYPES else "Anime"
    await state.update_data(media_type=mtype)
    await state.set_state(AdminStates.add_card_anime)
    await edit_or_answer(
        callback,
        f"✅ Media turi: <b>{esc(mtype)}</b>\n\n"
        f"3/6: <b>{esc(mtype)} nomini</b> yozing (masalan: <code>Jujutsu Kaisen</code>):",
    )


@router.message(AdminStates.add_card_anime, NotCommandFilter())
async def fsm_add_card_anime(message: Message, state: FSMContext) -> None:
    anime = _clean_name(message.text or "")
    if not anime or len(anime) > 128:
        await message.answer("⚠️ Media nomini matn bilan yozing (1–128 belgi).")
        return
    await state.update_data(anime_name=anime)
    await state.set_state(AdminStates.add_card_meta)
    skip_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏭ O‘tkazib yuborish",
                    callback_data=AdminCb(owner_id=message.from_user.id, section="cards", action="add_meta_skip").pack(),
                )
            ]
        ]
    )
    await message.answer(
        "4/6: <b>Personaj haqida ma’lumot yozing</b> (ixtiyoriy):\n"
        "<i>Qisqacha tavsif yozing yoki O‘tkazib yuborish tugmasini bosing.</i>",
        reply_markup=skip_kb,
    )


async def _proceed_to_rarity_pick(target, state: FSMContext, owner_id: int) -> None:
    data = await state.get_data()
    anime = data.get("anime_name", "")
    char_name = data.get("character_name", "")
    await state.set_state(AdminStates.add_card_rarity)
    async with get_session() as session:
        ranks = await GameplayService.get_rank_definitions(session)
        cfg = await SettingsService.get_rarity_config(session)
        anime_exists = (
            await session.execute(
                select(Card.id).where(func.lower(func.trim(Card.anime_name)) == anime.lower(), Card.is_deleted.is_(False)).limit(1)
            )
        ).first()
        char_exists = (
            await session.execute(
                select(Card.id)
                .where(
                    func.lower(func.trim(Card.anime_name)) == anime.lower(),
                    func.lower(func.trim(Card.character_name)) == char_name.lower(),
                    Card.is_deleted.is_(False),
                )
                .limit(1)
            )
        ).first()
    hint = ""
    if char_exists:
        hint = "\nℹ️ Bu personaj shu bo‘limda allaqachon bor — yangi variant qo‘shiladi.\n"
    elif anime_exists:
        hint = "\nℹ️ Bu media bo‘limi mavjud — personaj shu bo‘limga qo‘shiladi.\n"

    builder = InlineKeyboardBuilder()
    for idx, r in enumerate(ranks):
        r_name = str(r["name"])
        r_emoji = str(r.get("emoji", "⭐"))
        r_drops = int(cfg.get(r_name, {}).get("drops", r.get("drop_limit", 1)))
        builder.row(
            InlineKeyboardButton(
                text=f"{r_emoji} {r_name} · {r.get('drop_probability', 0)}% · {r_drops} drop",
                callback_data=AdminCb(owner_id=owner_id, section="cards", action="add_rarity", extra=str(idx)).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(text="❌ BEKOR", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="v").pack())
    )
    text = (
        f"✅ {esc(char_name)} · {esc(anime)} ({esc(data.get('media_type', 'Anime'))})\n{hint}\n"
        "5/6: <b>Rankni</b> tanlang:"
    )
    if isinstance(target, CallbackQuery):
        await edit_or_answer(target, text, reply_markup=builder.as_markup())
    else:
        await target.answer(text, reply_markup=builder.as_markup())


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "add_meta_skip")))
async def cb_admin_add_meta_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(metadata_json={}, aliases=[])
    await _proceed_to_rarity_pick(callback, state, callback.from_user.id)


@router.message(AdminStates.add_card_meta, NotCommandFilter())
async def fsm_add_card_meta(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    meta_dict = {"description": raw} if raw else {}
    await state.update_data(metadata_json=meta_dict, aliases=[])
    await _proceed_to_rarity_pick(message, state, message.from_user.id)


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "add_rarity")))
async def cb_admin_add_card_rarity(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    data = await state.get_data()
    if "character_name" not in data or "anime_name" not in data:
        await callback.answer("Jarayon eskirgan. Qaytadan boshlang.", show_alert=True)
        return
    async with get_session() as session:
        ranks = await GameplayService.get_rank_definitions(session)
    rank_obj = None
    if callback_data.extra.isdigit() and 0 <= int(callback_data.extra) < len(ranks):
        rank_obj = ranks[int(callback_data.extra)]
    else:
        rank_obj = next((r for r in ranks if str(r["name"]) == callback_data.extra), None)
    if rank_obj is None:
        await callback.answer("Rank topilmadi.", show_alert=True)
        return

    rarity = str(rank_obj["name"])
    await state.update_data(
        rarity=rarity,
        rank_base_value=int(rank_obj.get("base_value", 1000)),
        rank_drop_limit=int(rank_obj.get("drop_limit", 1)),
        rank_emoji=str(rank_obj.get("emoji", "⭐")),
    )
    await state.set_state(AdminStates.add_card_image)
    await edit_or_answer(
        callback,
        f"✅ Rank: {rank_obj.get('emoji', '⭐')} <b>{esc(rarity)}</b> · 🔁 Tashlash soni: <b>{rank_obj.get('drop_limit', 1)}</b> ta\n\n"
        "6/6: <b>Karta rasmini</b> yuboring:\n"
        "• Telegram’da <b>foto</b> sifatida, yoki\n"
        "• to‘g‘ri havola: <code>https://...jpg</code> / png / webp",
    )


@router.message(AdminStates.add_card_image, NotCommandFilter())
async def fsm_add_card_image(message: Message, state: FSMContext) -> None:
    image_ref, file_id, err = await _extract_image(message)
    if err:
        await message.answer(err)
        return
    data = await state.get_data()
    if not all(k in data for k in ("character_name", "anime_name", "rarity")):
        await state.clear()
        await message.answer("⚠️ Jarayon eskirgan.", reply_markup=admin_v2_cards_menu_kb(message.from_user.id))
        return
    await state.clear()

    async with get_session() as session:
        cfg_all = await SettingsService.get_rarity_config(session)
        r_cfg = cfg_all.get(data["rarity"], {})
        base_val = int(r_cfg.get("value", data.get("rank_base_value", 1000)))
        drop_lim = int(r_cfg.get("drops", data.get("rank_drop_limit", 1)))
        card = await CardService.create_card(
            session=session,
            character_name=data["character_name"],
            anime_name=data["anime_name"],
            image=image_ref,
            rarity=data["rarity"],
            base_value=base_val,
            drop_limit=drop_lim,
            media_type=data.get("media_type", "Anime"),
            aliases=data.get("aliases") or [],
            metadata_json=data.get("metadata_json") or {},
        )
        if file_id:
            card.image_file_id = file_id
        await AuctionWishlistService.record_history(
            session, card.id, "created", details={"admin_tg_id": message.from_user.id}
        )
        await AdminService.log_action(
            session,
            message.from_user.id,
            "create_card",
            "card",
            str(card.id),
            {"code": card.card_code, "character": card.character_name, "rarity": card.rarity, "media_type": card.media_type},
        )
        await session.commit()

    text = f"✅ <b>Yangi karta qo‘shildi!</b>\n\n{format_card_block(card, show_status=True)}"
    try:
        await send_card_message(message.bot, message.chat.id, card, text, admin_v2_cards_menu_kb(message.from_user.id))
    except Exception:
        await message.answer(text, reply_markup=admin_v2_cards_menu_kb(message.from_user.id))


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
    async with get_session() as session:
        total = int((await session.execute(select(func.count(Group.id)))).scalar_one() or 0)
        offset = (page - 1) * PAGE_SIZE
        groups = list(
            (await session.execute(select(Group).order_by(Group.is_active.desc(), Group.id.desc()).offset(offset).limit(PAGE_SIZE)))
            .scalars()
            .all()
        )
    title = "📅 <b>DROP JADVALI — GURUH TANLANG</b>" if callback_data.section == "schedule" else f"👥 <b>GURUHLAR ({total} ta)</b>"
    if not groups:
        await edit_or_answer(
            callback,
            f"{title}\n\n📭 Hozircha ro‘yxatga olingan guruhlar yo‘q.",
            reply_markup=admin_home_kb(callback.from_user.id),
        )
        return
    await edit_or_answer(
        callback,
        title,
        reply_markup=admin_groups_list_kb(callback.from_user.id, groups, section=callback_data.section, page=page, total=total, page_size=PAGE_SIZE),
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
    await message.answer(f"✅ Slot #{slot_id} ga <b>{esc(card.character_name)}</b> qo‘yildi!", reply_markup=admin_home_kb(message.from_user.id))


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
        await session.commit()
    link_line = "" if group.username else (f"🔗 Havola: {esc(link)}\n" if link else "🔗 Havola: yo‘q (yopiq guruh)\n")
    text = (
        f"{format_group_settings(group)}\n"
        f"{link_line}"
        f"👤 Faol a’zolar (bazada): <b>{analytics['active_members']}</b>\n"
        f"📦 Jami droplar: <b>{analytics['total_drops']}</b> (Olingani: <b>{analytics['claimed_drops']}</b>)\n"
        f"🔌 Bot holati: <b>{'🟢 Faol' if group.is_active else '🔴 Nofaol'}</b>"
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

    if user.is_banned:
        if user.ban_until:
            ban_str = f"⏱ Vaqtli ban ({fmt_tashkent(user.ban_until)} gacha (Toshkent vaqti))"
        else:
            ban_str = "🚫 Umrbod BANNED"
    else:
        ban_str = "🟢 Active"
    vip_str = f"💎 {esc(user.vip_tier)}" if CommunityService.is_vip_active(user) else "▫️ Oddiy"
    groups_str = ", ".join(esc(g) for g in groups[:5]) if groups else "—"
    text = (
        f"👤 <b>USER BOSHQARUVI</b>\n\n"
        f"🆔 Telegram ID: <code>{user.telegram_id}</code>\n"
        f"👤 Ism/Username: {esc(user.display_name)}\n"
        f"💎 Status: {vip_str}\n"
        f"🪙 Coins: <b>{fmt_coins(user.coins)}</b> (Band: {fmt_coins(user.locked_coins)})\n"
        f"⚡ Level: <b>{user.level}</b> ({user.xp} XP)\n"
        f"🎴 Cards: <b>{user.total_cards}</b> (Unique: <b>{user.unique_cards}</b>)\n"
        f"👥 Guruhlar: {groups_str}\n"
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


_RANKING_MODE_LABELS = {
    RankingMode.WEIGHTED.value: "⚖️ Rank vazni bo‘yicha",
    RankingMode.UNIQUE_FIRST.value: "✨ Noyob kartalar birinchi",
    RankingMode.TOTAL_CARDS.value: "🎴 Jami kartalar soni",
}
_DAILY_FIELD_LABELS = {
    "base_coins": "🪙 Asosiy kunlik coin",
    "streak_step": "🔥 Har kunlik seriya qo‘shimchasi (coin)",
    "max_streak_bonus": "🏆 Maksimal seriya bonusi (coin)",
}
_DROP_RULE_LABELS = {
    "members_per_card": "👥 Nechta a’zoga 1 ta karta (kamida 1)",
    "min_members": "🚫 Guruhda kamida nechta a’zo bo‘lsa karta tushsin (0 = cheklov yo‘q)",
    "min_per_day": "🔽 Kuniga eng kam tashlash soni (0+)",
    "max_per_day": "🔼 Kuniga eng ko‘p tashlash soni (1+)",
    "any_group_cooldown_hours": "⏳ Guruhlararo kutish vaqti (soat, 0+)",
    "same_group_cooldown_days": "📅 Bir xil karta guruhga qayta tushishi (kun, 1+)",
    "window_start_hour": "🌅 Boshlanish soati (0–23)",
    "window_end_hour": "🌃 Tugash soati (1–24)",
}


def _settings_cancel_kb(owner_id: int, action: str = "view") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text="❌ Bekor qilish",
            callback_data=AdminCb(owner_id=owner_id, section="settings", action=action).pack(),
        )
    )
    return b.as_markup()


async def _render_settings_home(target, owner_id: int) -> None:
    from utils.constants import get_rank_name_by_order

    async with get_session() as session:
        rank_mode = await SettingsService.get_ranking_mode(session)
        ref_cfg = await SettingsService.get_referral_config(session)
        grp_coins = await SettingsService.get_group_add_reward_coins(session)
        dcfg = await SettingsService.get_daily_config(session)
        gcfg = await GameplayService.get_game_config(session)
        maint = await SettingsService.get_value(session, "maintenance", {"enabled": False})

    maint_on = bool(isinstance(maint, dict) and maint.get("enabled"))
    pack_lim = max(0, int(gcfg.get("pack_daily_limit", 1)))
    r3_name = get_rank_name_by_order(3, "Epic")
    r3_meta = get_rarity_meta(r3_name)
    mode_lbl = _RANKING_MODE_LABELS.get(rank_mode, rank_mode)
    pack_txt = f"<b>{pack_lim}</b> marta/kun" if pack_lim > 0 else "🔴 O‘chirilgan (0)"
    lines = [
        "⚙️ <b>BOT SOZLAMALARI</b>\n━━━━━━━━━━━━━━━",
        f"🎁 <b>Paket limiti (/pack):</b> {pack_txt}",
        f"🎉 <b>Kunlik bonus:</b> {dcfg['base_coins']} 🪙 (+{dcfg['streak_step']}/kun, max +{dcfg['max_streak_bonus']})",
        f"🤝 <b>Referal:</b> {ref_cfg['inviter_coins']:,} 🪙 / {ref_cfg['invitee_coins']:,} 🪙",
        f"🏰 <b>Guruhga qo‘shish:</b> {grp_coins:,} 🪙 + {r3_meta.emoji} 1 {esc(r3_name)}",
        f"🏆 <b>Reyting tartibi:</b> {esc(mode_lbl)}",
        f"🛠 <b>Texnik rejim:</b> {'🔴 Yoqilgan' if maint_on else '🟢 O‘chirilgan'}",
    ]
    await edit_or_answer(
        target,
        "\n".join(lines),
        reply_markup=admin_settings_kb(owner_id, rank_mode, maint_on, pack_daily_limit=pack_lim),
    )


async def _render_daily_menu(target, owner_id: int) -> None:
    async with get_session() as session:
        dcfg = await SettingsService.get_daily_config(session)
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text=f"🪙 Asosiy coin: {dcfg['base_coins']}", callback_data=AdminCb(owner_id=owner_id, section="settings", action="daily_ed", extra="base_coins").pack()))
    b.row(InlineKeyboardButton(text=f"🔥 Streak qadam: +{dcfg['streak_step']}", callback_data=AdminCb(owner_id=owner_id, section="settings", action="daily_ed", extra="streak_step").pack()))
    b.row(InlineKeyboardButton(text=f"🏆 Max streak bonus: {dcfg['max_streak_bonus']}", callback_data=AdminCb(owner_id=owner_id, section="settings", action="daily_ed", extra="max_streak_bonus").pack()))
    b.row(InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdminCb(owner_id=owner_id, section="settings").pack()))
    await edit_or_answer(target, "🎁 <b>KUNLIK BONUS SOZLAMALARI</b>", reply_markup=b.as_markup())


async def _render_drop_rules(target, owner_id: int) -> None:
    async with get_session() as session:
        rules = await SettingsService.get_drop_rules(session)
    b = InlineKeyboardBuilder()
    ann = "✅ Ha" if rules.get("announce_in_group", True) else "❌ Yo‘q"
    b.row(InlineKeyboardButton(text=f"📢 Guruhda e’lon: {ann}", callback_data=AdminCb(owner_id=owner_id, section="settings", action="dr_tog_ann").pack()))
    b.row(
        InlineKeyboardButton(text=f"👥 {rules['members_per_card']} a’zo/1 karta", callback_data=AdminCb(owner_id=owner_id, section="settings", action="dr_ed", extra="members_per_card").pack()),
        InlineKeyboardButton(text=f"🔽 Min/kun: {rules['min_per_day']}", callback_data=AdminCb(owner_id=owner_id, section="settings", action="dr_ed", extra="min_per_day").pack()),
    )
    b.row(
        InlineKeyboardButton(text=f"🔼 Max/kun: {rules['max_per_day']}", callback_data=AdminCb(owner_id=owner_id, section="settings", action="dr_ed", extra="max_per_day").pack()),
        InlineKeyboardButton(text=f"⏳ Cooldown: {rules['any_group_cooldown_hours']} soat", callback_data=AdminCb(owner_id=owner_id, section="settings", action="dr_ed", extra="any_group_cooldown_hours").pack()),
    )
    b.row(
        InlineKeyboardButton(text=f"📅 Qayta drop: {rules['same_group_cooldown_days']} kun", callback_data=AdminCb(owner_id=owner_id, section="settings", action="dr_ed", extra="same_group_cooldown_days").pack()),
        InlineKeyboardButton(text=f"🌅 Soat: {rules['window_start_hour']}:00", callback_data=AdminCb(owner_id=owner_id, section="settings", action="dr_ed", extra="window_start_hour").pack()),
    )
    b.row(
        InlineKeyboardButton(text=f"🌃 Tugash: {rules['window_end_hour']}:00", callback_data=AdminCb(owner_id=owner_id, section="settings", action="dr_ed", extra="window_end_hour").pack()),
        InlineKeyboardButton(text=f"🚫 Min a’zo: {rules['min_members']}", callback_data=AdminCb(owner_id=owner_id, section="settings", action="dr_ed", extra="min_members").pack()),
    )
    b.row(InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdminCb(owner_id=owner_id, section="settings").pack()))
    await edit_or_answer(target, f"📡 <b>DROP QOIDALARI</b>\n\n🚫 Guruhda <b>{rules['min_members']}</b> tadan kam haqiqiy a’zo bo‘lsa karta tushmaydi.\n\nO‘zgartirish uchun bosing:", reply_markup=b.as_markup())


async def _render_vip_menu(target, owner_id: int) -> None:
    async with get_session() as session:
        vcfg = await CommunityService.get_vip_config(session)
    tiers = vcfg.get("tiers") or {}
    lines = ["💎 <b>VIP OBUNA NARXLARI</b>\n"]
    b = InlineKeyboardBuilder()
    for key, t in tiers.items():
        lines.append(f"{t.get('emoji', '💎')} <b>{esc(t.get('name', key))}</b>: {int(t.get('coins', 0)):,} 🪙 | {int(t.get('stars', 0)):,} ⭐ ({t.get('duration_days', 30)} kun)")
        b.row(
            InlineKeyboardButton(text=f"🪙 {key} Coin", callback_data=AdminCb(owner_id=owner_id, section="settings", action="vip_ed", extra=f"{key}.coins").pack()),
            InlineKeyboardButton(text=f"⭐ {key} Stars", callback_data=AdminCb(owner_id=owner_id, section="settings", action="vip_ed", extra=f"{key}.stars").pack()),
        )
    b.row(InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdminCb(owner_id=owner_id, section="settings").pack()))
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


# ---------------------------------------------------------------------------
# BOT SETTINGS (Clean V2 controls: Daily, Referral, Drop Rules, Welcome, VIP, Maint, Ranking)
# ---------------------------------------------------------------------------
@router.callback_query(AdminCb.filter(F.section == "settings"))
async def cb_admin_settings(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    act = callback_data.action
    owner_id = callback.from_user.id

    if act == "rewards_menu":
        await state.clear()
        await _render_rewards_menu(callback)
        return

    if act in ("edit_ref_inviter", "edit_ref_invitee", "edit_group_reward"):
        prompts = {
            "edit_ref_inviter": (AdminStates.edit_ref_inviter, "🤝 Taklif qiluvchi uchun <b>referal coin</b> miqdorini kiriting:"),
            "edit_ref_invitee": (AdminStates.edit_ref_invitee, "🆕 Yangi foydalanuvchi uchun <b>referal coin</b> miqdorini kiriting:"),
            "edit_group_reward": (AdminStates.edit_group_reward, "🏰 Guruhga qo‘shish uchun <b>coin mukofotini</b> kiriting:"),
        }
        st, txt = prompts[act]
        await state.set_state(st)
        await state.update_data(owner_tg_id=owner_id)
        await edit_or_answer(callback, txt, reply_markup=_settings_cancel_kb(owner_id, "rewards_menu"))
        return

    if act == "grp_tog":
        field = callback_data.extra
        async with get_session() as session:
            cur = await SettingsService.get_group_reward_config(session)
            if field not in ("enabled", "per_member", "give_epic_card"):
                await callback.answer("Noma’lum sozlama", show_alert=True)
                return
            await SettingsService.set_group_reward_field(session, field, not cur[field])
            await AdminService.log_action(session, owner_id, "edit_group_reward_cfg", "settings", field, {"value": not cur[field]})
            await session.commit()
        await callback.answer("✅ Yangilandi!")
        await state.clear()
        await _render_rewards_menu(callback)
        return

    if act == "grp_ed":
        field = callback_data.extra
        if field not in _GRP_CFG_LABELS:
            await callback.answer("Noma’lum sozlama", show_alert=True)
            return
        await state.set_state(AdminStates.edit_group_cfg_field)
        await state.update_data(owner_tg_id=owner_id, field=field)
        await edit_or_answer(
            callback,
            f"🏰 <b>{esc(_GRP_CFG_LABELS[field])}</b>\n\nYangi qiymatni kiriting (butun son, 0 ham mumkin):",
            reply_markup=_settings_cancel_kb(owner_id, "rewards_menu"),
        )
        return

    if act == "daily_menu":
        await state.clear()
        await _render_daily_menu(callback, owner_id)
        return

    if act == "daily_ed":
        field = callback_data.extra
        await state.set_state(AdminStates.edit_daily_field)
        await state.update_data(owner_tg_id=owner_id, field=field)
        label = _DAILY_FIELD_LABELS.get(field, field)
        await edit_or_answer(
            callback,
            f"🎁 <b>{esc(label)}</b>\n\nYangi qiymatni kiriting (butun son):",
            reply_markup=_settings_cancel_kb(owner_id, "daily_menu"),
        )
        return

    if act == "drop_rules":
        await state.clear()
        await _render_drop_rules(callback, owner_id)
        return

    if act == "dr_tog_ann":
        async with get_session() as session:
            rules = await SettingsService.get_drop_rules(session)
            rules["announce_in_group"] = not bool(rules.get("announce_in_group", True))
            await SettingsService.set_value(session, "drop_rules", rules)
            await session.commit()
        await callback.answer("✅ Yangilandi!")
        await state.clear()
        await _render_drop_rules(callback, owner_id)
        return

    if act == "dr_ed":
        field = callback_data.extra
        await state.set_state(AdminStates.edit_drop_rule_field)
        await state.update_data(owner_tg_id=owner_id, field=field)
        label = _DROP_RULE_LABELS.get(field, field)
        await edit_or_answer(
            callback,
            f"📡 <b>{esc(label)}</b>\n\nYangi qiymatni kiriting:",
            reply_markup=_settings_cancel_kb(owner_id, "drop_rules"),
        )
        return

    if act == "welcome_edit":
        await state.set_state(AdminStates.edit_welcome_text)
        await state.update_data(owner_tg_id=owner_id)
        async with get_session() as session:
            cur = str(await SettingsService.get_value(session, "welcome_text", "") or "")
        preview = esc(cur[:1500]) if cur else "<i>Bo‘sh</i>"
        await edit_or_answer(
            callback,
            f"👋 <b>KIRISH MATNI (/start)</b>\n\nHozirgi matn:\n<code>{preview}</code>\n\nYangi matnni yuboring:",
            reply_markup=_settings_cancel_kb(owner_id, "view"),
        )
        return

    if act == "vip_menu":
        await state.clear()
        await _render_vip_menu(callback, owner_id)
        return

    if act == "vip_ed":
        tier_metric = callback_data.extra or ""
        await state.set_state(AdminStates.edit_vip_price)
        await state.update_data(owner_tg_id=owner_id, vip_key=tier_metric)
        pretty = tier_metric.replace(".coins", " — Coin narxi").replace(".stars", " — Stars narxi")
        await edit_or_answer(
            callback,
            f"💎 <b>{esc(pretty)}</b>\n\nYangi narxni kiriting (faqat musbat son):",
            reply_markup=_settings_cancel_kb(owner_id, "vip_menu"),
        )
        return

    if act == "pack_limit":
        async with get_session() as session:
            gcfg = await GameplayService.get_game_config(session)
        cur_lim = max(0, int(gcfg.get("pack_daily_limit", 1)))
        await state.set_state(AdminStates.edit_pack_limit)
        await state.update_data(owner_tg_id=owner_id)
        await edit_or_answer(
            callback,
            "🎁 <b>KUNLIK PAKET LIMITI (/pack)</b>\n"
            "━━━━━━━━━━━━━━━\n"
            f"Hozirgi limit: <b>{cur_lim} marta/kun</b>\n\n"
            "Bir foydalanuvchi 1 kunda nechta paket ocha olsin?\n"
            "• <code>1</code> — standart (kuniga 1 marta)\n"
            "• <code>0</code> — paketlarni vaqtincha yopish\n"
            "• <code>1–100</code> — kunlik limit soni\n\n"
            "Yangi sonni kiriting:",
            reply_markup=_settings_cancel_kb(owner_id, "view"),
        )
        return

    await state.clear()
    async with get_session() as session:
        if act == "set_rank":
            if callback_data.extra in {m.value for m in RankingMode}:
                await SettingsService.set_ranking_mode(session, callback_data.extra)
                await AdminService.log_action(
                    session, owner_id, "set_ranking_mode", "settings", "ranking_mode", {"mode": callback_data.extra}
                )
                await session.commit()
                RankingService.invalidate_cache()
                await callback.answer("🏆 Reyting tartibi yangilandi!")
        elif act == "maint_tog":
            maint = await SettingsService.get_value(session, "maintenance", {"enabled": False})
            maint = dict(maint) if isinstance(maint, dict) else {"enabled": False}
            maint["enabled"] = not bool(maint.get("enabled", False))
            await SettingsService.set_value(session, "maintenance", maint)
            await session.commit()
            await callback.answer("🛠 Texnik rejim o‘zgartirildi!")

    await _render_settings_home(callback, owner_id)


@router.message(AdminStates.edit_pack_limit, NotCommandFilter())
async def fsm_edit_pack_limit(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit() or not (0 <= int(raw) <= 100):
        await message.answer("⚠️ 0 dan 100 gacha butun son kiriting.")
        return
    val = int(raw)
    await state.clear()
    async with get_session() as session:
        gcfg = await GameplayService.get_game_config(session)
        gcfg["pack_daily_limit"] = val
        gcfg["pack_enabled"] = val > 0
        await SettingsService.set_value(session, "game_config", gcfg)
        await AdminService.log_action(
            session, message.from_user.id, "set_pack_daily_limit", "settings", "pack_daily_limit", {"limit": val}
        )
        await session.commit()
    await message.answer(f"✅ Kunlik paket limiti <b>{val}/kun</b> qilib saqlandi!")
    await _render_settings_home(message, message.from_user.id)


@router.message(AdminStates.edit_daily_field, NotCommandFilter())
async def fsm_edit_daily_field(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    field = data["field"]
    min_val = 1 if field == "base_coins" else 0
    if not raw.isdigit() or int(raw) < min_val:
        await message.answer(f"⚠️ Kamida {min_val} butun son kiriting.")
        return
    await state.clear()
    async with get_session() as session:
        dcfg = await SettingsService.get_daily_config(session)
        dcfg[field] = int(raw)
        await SettingsService.set_value(session, "daily_bonus", dcfg)
        await session.commit()
    await message.answer("✅ Daily Bonus sozlamasi yangilandi!")
    await _render_daily_menu(message, message.from_user.id)


@router.message(AdminStates.edit_drop_rule_field, NotCommandFilter())
async def fsm_edit_drop_rule_field(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    field = data["field"]
    if not raw.isdigit():
        await message.answer("⚠️ Faqat butun son kiriting.")
        return
    val = int(raw)
    if field in ("min_per_day", "any_group_cooldown_hours", "min_members") and val < 0:
        await message.answer("⚠️ 0 yoki musbat son kiriting.")
        return
    if field in ("members_per_card", "max_per_day", "same_group_cooldown_days") and val < 1:
        await message.answer("⚠️ Kamida 1 son kiriting.")
        return
    if field == "window_start_hour" and not (0 <= val <= 23):
        await message.answer("⚠️ Boshlanish soati 0 dan 23 gacha bo‘lishi kerak.")
        return
    if field == "window_end_hour" and not (1 <= val <= 24):
        await message.answer("⚠️ Tugash soati 1 dan 24 gacha bo‘lishi kerak.")
        return
    await state.clear()
    async with get_session() as session:
        rules = await SettingsService.get_drop_rules(session)
        rules[field] = val
        await SettingsService.set_value(session, "drop_rules", rules)
        await session.commit()
    await message.answer(f"✅ Drop qoidasi saqlandi (<b>{val}</b>)!")
    await _render_drop_rules(message, message.from_user.id)


@router.message(AdminStates.edit_welcome_text, NotCommandFilter())
async def fsm_edit_welcome_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    txt = (message.text or "").strip()
    if not txt:
        await message.answer("⚠️ Matn kiriting.")
        return
    await state.clear()
    async with get_session() as session:
        await SettingsService.set_value(session, "welcome_text", txt)
        await session.commit()
    await message.answer("✅ Welcome matni saqlandi!")
    await _render_settings_home(message, message.from_user.id)


@router.message(AdminStates.edit_vip_price, NotCommandFilter())
async def fsm_edit_vip_price(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("⚠️ Musbat son kiriting.")
        return
    tier_key, metric = data["vip_key"].rsplit(".", 1)
    await state.clear()
    async with get_session() as session:
        vcfg = await CommunityService.get_vip_config(session)
        tiers = {k: dict(v) for k, v in (vcfg.get("tiers") or {}).items()}
        if tier_key in tiers and metric in ("coins", "stars"):
            tiers[tier_key][metric] = int(raw)
            vcfg["tiers"] = tiers
            await SettingsService.set_value(session, "vip_config", vcfg)
            await session.commit()
    await message.answer(f"✅ {esc(tier_key)} narxi = <b>{int(raw):,}</b> saqlandi!")
    await _render_vip_menu(message, message.from_user.id)


_GRP_CFG_LABELS = {
    "per_member_coins": "👥 Har bir a’zo uchun coin",
    "flat_coins": "🪙 Qat’iy mukofot (a’zo soniga bog‘liq bo‘lmasa)",
    "min_members": "🚫 Kamida nechta haqiqiy a’zo kerak",
}


async def _rewards_menu_text_and_kb(owner_id: int):
    from utils.constants import get_rank_name_by_order

    async with get_session() as session:
        ref_cfg = await SettingsService.get_referral_config(session)
        gcfg = await SettingsService.get_group_reward_config(session)
    r3_name = get_rank_name_by_order(3, "Epic")
    r3_meta = get_rarity_meta(r3_name)
    on = lambda v: "✅ Yoqilgan" if v else "❌ O‘chiq"  # noqa: E731
    if gcfg["per_member"]:
        amount_line = f"👥 Hisoblash: <b>a’zolar soni × {gcfg['per_member_coins']:,} 🪙</b> (masalan 100 a’zo = {100 * gcfg['per_member_coins']:,} 🪙)"
    else:
        amount_line = f"🪙 Hisoblash: qat’iy <b>{gcfg['flat_coins']:,} 🪙</b>"
    text = (
        "🤝 <b>REFERAL VA GURUH MUKOFOTLARI</b>\n\n"
        "<b>Referal (do‘st taklif qilish)</b>\n"
        f"👤 Taklif qilgan odamga: <b>{ref_cfg['inviter_coins']:,} 🪙</b>\n"
        f"🆕 Taklif qilingan do‘stga: <b>{ref_cfg['invitee_coins']:,} 🪙</b>\n\n"
        "<b>Botni guruhga qo‘shish mukofoti</b>\n"
        f"Holat: <b>{on(gcfg['enabled'])}</b>\n"
        f"{amount_line}\n"
        f"🚫 Kamida <b>{gcfg['min_members']}</b> ta haqiqiy a’zo bo‘lmasa mukofot berilmaydi (botlar hisobga olinmaydi)\n"
        f"{r3_meta.emoji} Qo‘shimcha {esc(r3_name)} karta: <b>{on(gcfg['give_epic_card'])}</b>"
    )
    cb = lambda action, extra="": AdminCb(owner_id=owner_id, section="settings", action=action, extra=extra).pack()  # noqa: E731
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="👤 Taklif qilgan coin", callback_data=cb("edit_ref_inviter")),
        InlineKeyboardButton(text="🆕 Do‘st coin", callback_data=cb("edit_ref_invitee")),
    )
    builder.row(InlineKeyboardButton(text=f"🏰 Guruh mukofoti: {on(gcfg['enabled'])}", callback_data=cb("grp_tog", "enabled")))
    builder.row(
        InlineKeyboardButton(
            text="🔀 A’zo soniga hisoblash: " + ("✅ Ha" if gcfg["per_member"] else "❌ Yo‘q"),
            callback_data=cb("grp_tog", "per_member"),
        )
    )
    builder.row(
        InlineKeyboardButton(text=f"👥 1 a’zo = {gcfg['per_member_coins']:,} 🪙", callback_data=cb("grp_ed", "per_member_coins")),
        InlineKeyboardButton(text=f"🪙 Qat’iy: {gcfg['flat_coins']:,}", callback_data=cb("grp_ed", "flat_coins")),
    )
    builder.row(
        InlineKeyboardButton(text=f"🚫 Min a’zo: {gcfg['min_members']}", callback_data=cb("grp_ed", "min_members")),
        InlineKeyboardButton(text=f"{r3_meta.emoji} Karta: {'✅' if gcfg['give_epic_card'] else '❌'}", callback_data=cb("grp_tog", "give_epic_card")),
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdminCb(owner_id=owner_id, section="settings", action="view").pack())
    )
    return text, builder.as_markup()


async def _render_rewards_menu(callback: CallbackQuery) -> None:
    text, kb = await _rewards_menu_text_and_kb(callback.from_user.id)
    await edit_or_answer(callback, text, reply_markup=kb)


async def _save_reward_coin_setting(message: Message, state: FSMContext, kind: str) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") is not None and data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit():
        await message.answer("⚠️ Faqat musbat butun son (yoki 0) kiriting.")
        return
    val = int(raw)
    try:
        async with get_session() as session:
            if kind == "inviter":
                await SettingsService.set_referral_config(session, inviter_coins=val)
            elif kind == "invitee":
                await SettingsService.set_referral_config(session, invitee_coins=val)
            else:
                await SettingsService.set_group_add_reward_coins(session, val)
            await AdminService.log_action(session, message.from_user.id, f"edit_{kind}_reward", "settings", kind, {"coins": val})
            await session.commit()
    except ValueError as exc:
        await message.answer(f"⚠️ {esc(str(exc))}")
        return
    await state.clear()
    text, kb = await _rewards_menu_text_and_kb(message.from_user.id)
    await message.answer("✅ Saqlandi!\n\n" + text, reply_markup=kb)


@router.message(AdminStates.edit_ref_inviter, NotCommandFilter())
async def fsm_edit_ref_inviter(message: Message, state: FSMContext) -> None:
    await _save_reward_coin_setting(message, state, "inviter")


@router.message(AdminStates.edit_ref_invitee, NotCommandFilter())
async def fsm_edit_ref_invitee(message: Message, state: FSMContext) -> None:
    await _save_reward_coin_setting(message, state, "invitee")


@router.message(AdminStates.edit_group_reward, NotCommandFilter())
async def fsm_edit_group_reward(message: Message, state: FSMContext) -> None:
    await _save_reward_coin_setting(message, state, "group")


@router.message(AdminStates.edit_group_cfg_field, NotCommandFilter())
async def fsm_edit_group_cfg_field(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") is not None and data.get("owner_tg_id") != message.from_user.id:
        return
    field = str(data.get("field", ""))
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit():
        await message.answer("⚠️ Faqat musbat butun son (yoki 0) kiriting.")
        return
    try:
        async with get_session() as session:
            await SettingsService.set_group_reward_field(session, field, int(raw))
            await AdminService.log_action(session, message.from_user.id, "edit_group_reward_cfg", "settings", field, {"value": int(raw)})
            await session.commit()
    except ValueError as exc:
        await message.answer(f"⚠️ {esc(str(exc))}")
        return
    await state.clear()
    text, kb = await _rewards_menu_text_and_kb(message.from_user.id)
    await message.answer("✅ Saqlandi!\n\n" + text, reply_markup=kb)


# ---------------------------------------------------------------------------
# TELEGRAM STARS TARIFLARI
# ---------------------------------------------------------------------------
STARS_INPUT_HELP = (
    "Ikkita raqamni bo‘sh joy bilan yozing: <code>Stars Coin</code>\n"
    "Misol: <code>250 30000</code> — 250 ⭐ evaziga 30,000 coin.\n"
    "<i>Bekor qilish: /cancel</i>"
)


def _parse_stars_coins(text: str):
    nums = re.findall(r"\d+", (text or "").replace(",", " "))
    if len(nums) != 2:
        return None
    return int(nums[0]), int(nums[1])


def _stars_list_text(packages) -> str:
    lines = ["⭐ <b>STARS TARIFLARI</b>\n"]
    for p in packages:
        lines.append(f"• <b>{p['stars']:,}</b> ⭐ → <b>{p['coins']:,}</b> 🪙")
    if not packages:
        lines.append("📭 Paketlar yo‘q.")
    return "\n".join(lines)


@router.callback_query(AdminCb.filter(F.section == "stars"))
async def cb_admin_stars(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    action = callback_data.action
    admin_id = callback.from_user.id
    if action == "add":
        await state.set_state(AdminStates.stars_add)
        await edit_or_answer(callback, f"➕ <b>YANGI STARS TARIF</b>\n\n{STARS_INPUT_HELP}", reply_markup=admin_stars_back_kb(admin_id))
        return
    if action == "edit":
        async with get_session() as session:
            packages = await SettingsService.get_stars_packages(session)
        pkg = next((p for p in packages if p["id"] == callback_data.extra), None)
        if pkg is None:
            await callback.answer("Paket topilmadi.", show_alert=True)
            return
        await state.set_state(AdminStates.stars_edit)
        await state.update_data(package_id=pkg["id"])
        await edit_or_answer(
            callback,
            f"✏️ <b>TARIFNI TAHRIRLASH</b>\nHozirgi: <b>{pkg['stars']:,}</b> ⭐ → <b>{pkg['coins']:,}</b> 🪙\n\n{STARS_INPUT_HELP}",
            reply_markup=admin_stars_back_kb(admin_id),
        )
        return
    if action == "del":
        await state.clear()
        await edit_or_answer(callback, "🗑 Ushbu tarifni o‘chirishni tasdiqlaysizmi?", reply_markup=admin_stars_confirm_kb(admin_id, callback_data.extra))
        return

    await state.clear()
    async with get_session() as session:
        if action == "del_ok":
            if await SettingsService.delete_stars_package(session, callback_data.extra):
                await AdminService.log_action(session, admin_id, "stars_package_delete", "settings", callback_data.extra)
                await session.commit()
        packages = await SettingsService.get_stars_packages(session)
    await edit_or_answer(callback, _stars_list_text(packages), reply_markup=admin_stars_kb(admin_id, packages))


async def _save_stars_from_message(message: Message, state: FSMContext, package_id: Optional[str]) -> None:
    parsed = _parse_stars_coins(message.text or "")
    if parsed is None:
        await message.answer(f"⚠️ Format noto‘g‘ri.\n{STARS_INPUT_HELP}")
        return
    stars, coins = parsed
    try:
        async with get_session() as session:
            pkg = await SettingsService.save_stars_package(session, package_id, stars, coins)
            await session.commit()
            packages = await SettingsService.get_stars_packages(session)
    except ValueError as exc:
        await message.answer(f"⚠️ {esc(str(exc))}")
        return
    await state.clear()
    await message.answer(f"✅ Saqlandi: <b>{pkg['stars']:,}</b> ⭐ → <b>{pkg['coins']:,}</b> 🪙", reply_markup=admin_stars_kb(message.from_user.id, packages))


@router.message(AdminStates.stars_add, NotCommandFilter())
async def fsm_stars_add(message: Message, state: FSMContext) -> None:
    await _save_stars_from_message(message, state, None)


@router.message(AdminStates.stars_edit, NotCommandFilter())
async def fsm_stars_edit(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    package_id = data.get("package_id")
    if not package_id:
        await state.clear()
        await message.answer("⚠️ Tarif topilmadi.")
        return
    await _save_stars_from_message(message, state, str(package_id))


# ---------------------------------------------------------------------------
# LIMITLI KARTA EVENTI
# ---------------------------------------------------------------------------
_BG_TASKS: set = set()
_EVT_STATUS = {"active": "🟢 Faol", "sold_out": "🔥 Sotilib bo‘ldi", "ended": "⌛ Tugagan", "cancelled": "⛔ To‘xtatilgan"}


def _local_dt(dt):
    return to_tashkent(dt)


# ----------------------------------------------------------------------
# BONUS EVENT: ma'lum vaqt davomida biror rank tushish ehtimolini oshirish
# ----------------------------------------------------------------------
async def _render_bonus_menu(callback: CallbackQuery) -> None:
    owner_id = callback.from_user.id
    async with get_session() as session:
        ev = await CardService.get_active_event(session)
    b = InlineKeyboardBuilder()
    if ev is not None:
        text = (
            "⚡ <b>BONUS EVENT</b>\n\n"
            f"🟢 Hozir faol: <b>{esc(ev.name)}</b>\n"
            f"🎯 {esc(ev.description)}\n"
            f"⏳ Tugash: <b>{_local_dt(ev.ends_at):%d.%m.%Y %H:%M}</b>"
        )
        b.row(InlineKeyboardButton(text="⛔ Hozir to‘xtatish", callback_data=AdminCb(owner_id=owner_id, section="events", action="bonus_stop", target_id=ev.id).pack()))
    else:
        text = (
            "⚡ <b>BONUS EVENT</b>\n\n"
            "Hozir faol bonus event yo‘q.\n"
            "Bonus event — belgilangan vaqt davomida tanlangan rank kartalari guruhlarga tez-tez tushishini ta’minlaydi."
        )
    b.row(InlineKeyboardButton(text="➕ Yangi bonus event", callback_data=AdminCb(owner_id=owner_id, section="events", action="bonus_new").pack()))
    b.row(InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdminCb(owner_id=owner_id, section="evt", action="menu").pack()))
    await edit_or_answer(callback, text, reply_markup=b.as_markup())


@router.callback_query(AdminCb.filter(F.section == "events"))
async def cb_admin_bonus_event(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    owner_id = callback.from_user.id
    action = callback_data.action

    if action == "bonus_menu":
        await state.clear()
        await _render_bonus_menu(callback)
        return

    if action == "bonus_stop":
        async with get_session() as session:
            ev = await session.get(Event, callback_data.target_id)
            if ev is not None:
                ev.is_active = False
                await CommunityService.log_audit(session, owner_id, "bonus_event_stop", "event", str(ev.id))
                await session.commit()
        await callback.answer("⛔ Bonus event to‘xtatildi", show_alert=True)
        await _render_bonus_menu(callback)
        return

    if action == "bonus_new":
        await state.set_state(AdminStates.bonus_name)
        await state.update_data(owner_tg_id=owner_id)
        await edit_or_answer(callback, "⚡ <b>YANGI BONUS EVENT</b>\n\n1/4 — Event nomini yozing (masalan: <i>Legend haftaligi</i>):\n<i>Bekor: /cancel</i>")
        return

    if action == "bonus_rar":
        # extra = rank nomi yoki "none"
        await state.update_data(bonus_rarity=None if callback_data.extra == "none" else callback_data.extra)
        b = InlineKeyboardBuilder()
        for m in (2, 3, 5, 10):
            b.add(InlineKeyboardButton(text=f"x{m}", callback_data=AdminCb(owner_id=owner_id, section="events", action="bonus_go", extra=str(m)).pack()))
        b.adjust(4)
        await edit_or_answer(callback, "⚡ 4/4 — Tushish ehtimoli necha barobar oshsin?", reply_markup=b.as_markup())
        return

    if action == "bonus_go":
        data = await state.get_data()
        name = data.get("bonus_name_val")
        hours = int(data.get("bonus_hours_val") or 24)
        rarity = data.get("bonus_rarity")
        try:
            mult = float(callback_data.extra or 2)
        except ValueError:
            mult = 2.0
        if not name:
            await callback.answer("Ma’lumot yo‘qoldi, qaytadan boshlang.", show_alert=True)
            return
        await state.clear()
        async with get_session() as session:
            await AdminService.create_event(
                session, owner_id, name, hours,
                boosted_rarity=rarity, rarity_weight_multiplier=mult if rarity else 1.0,
            )
            await session.commit()
        await callback.answer("✅ Bonus event boshlandi!", show_alert=True)
        await _render_bonus_menu(callback)
        return

    await callback.answer()


@router.message(AdminStates.bonus_name, NotCommandFilter())
async def fsm_bonus_name(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    name = (message.text or "").strip()
    if not (2 <= len(name) <= 100):
        await message.answer("⚠️ Nom 2 dan 100 gacha belgi bo‘lsin.")
        return
    await state.update_data(bonus_name_val=name)
    await state.set_state(AdminStates.bonus_hours)
    await message.answer("⚡ 2/4 — Necha soat davom etsin? (1 dan 720 gacha son)")


@router.message(AdminStates.bonus_hours, NotCommandFilter())
async def fsm_bonus_hours(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or not (1 <= int(raw) <= 720):
        await message.answer("⚠️ 1 dan 720 gacha butun son kiriting.")
        return
    await state.update_data(bonus_hours_val=int(raw))
    owner_id = message.from_user.id
    async with get_session() as session:
        ranks = await GameplayService.get_rank_definitions(session)
    b = InlineKeyboardBuilder()
    for r in ranks:
        rarity = str(r["name"])
        emoji = str(r.get("emoji") or get_rarity_meta(rarity).emoji)
        b.add(InlineKeyboardButton(text=f"{emoji} {rarity}", callback_data=AdminCb(owner_id=owner_id, section="events", action="bonus_rar", extra=rarity).pack()))
    b.adjust(2)
    b.row(InlineKeyboardButton(text="Rank tanlamaslik (faqat e’lon)", callback_data=AdminCb(owner_id=owner_id, section="events", action="bonus_rar", extra="none").pack()))
    await message.answer("⚡ 3/4 — Qaysi rank kartalari ko‘proq tushsin?", reply_markup=b.as_markup())



async def _render_evt_menu(callback: CallbackQuery) -> None:
    async with get_session() as session:
        await EventService.close_expired(session)
        await session.commit()
        events = await EventService.get_recent_events(session, limit=8)
    lines = ["🎁 <b>EVENTLAR — LIMITLI KARTALAR</b>\n"]
    if not events:
        lines.append("Hozircha eventlar yo‘q. ➕ tugmasi bilan yangi event boshlang.")
    for ev, card in events:
        lines.append(
            f"{_EVT_STATUS.get(ev.status, ev.status)} <b>#{ev.id} {esc(card.character_name)}</b> "
            f"— {ev.sold_count}/{ev.total_copies} · tugash: {_local_dt(ev.ends_at):%d.%m %H:%M}"
        )
    await edit_or_answer(callback, "\n".join(lines), reply_markup=admin_evt_menu_kb(callback.from_user.id, events))


def _evt_summary(data: dict) -> str:
    prices = []
    if data.get("price_coins"):
        prices.append(f"🪙 <b>{int(data['price_coins']):,}</b> coin")
    if data.get("price_stars"):
        prices.append(f"⭐ <b>{int(data['price_stars']):,}</b> Stars")
    hours = int(data.get("hours", EVENT_DURATION_HOURS))
    meta = get_rarity_meta(data.get("rarity", "Basic"))
    return (
        "🎁 <b>EVENTNI TASDIQLASH</b>\n\n"
        f"🎴 Karta: <b>{esc(data.get('card_name', ''))}</b> ({meta.emoji} {esc(meta.name)})\n"
        f"📺 Media: {esc(data.get('anime_name', ''))}\n"
        f"📦 Nusxa soni: <b>{data.get('copies')}</b> ta\n"
        f"💳 Narx: {'  yoki  '.join(prices)}\n"
        f"⏳ Davomiyligi: <b>{hours} soat</b>"
    )


async def _show_stars_choice(target, state: FSMContext, owner_id: int) -> None:
    async with get_session() as session:
        packages = await SettingsService.get_stars_packages(session)
    await state.set_state(AdminStates.evt_price_stars)
    text = "⭐ <b>Stars narxini belgilang</b>\n\nPastdan tanlang yoki son yozing:"
    kb = admin_evt_stars_kb(owner_id, packages)
    if isinstance(target, CallbackQuery):
        await edit_or_answer(target, text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


async def _ask_evt_duration(target, state: FSMContext, owner_id: int) -> None:
    await state.set_state(AdminStates.evt_duration)
    text = "⏳ <b>Event qancha vaqt faol bo‘lsin?</b> (tugmadan tanlang yoki soat sonini yozing):"
    kb = admin_evt_duration_kb(owner_id)
    if isinstance(target, CallbackQuery):
        await edit_or_answer(target, text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


@router.callback_query(AdminCb.filter(F.section == "evt"))
async def cb_admin_evt(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    action = callback_data.action
    admin_id = callback.from_user.id

    if action == "menu":
        await state.clear()
        await _render_evt_menu(callback)
        return

    if action == "pick":
        await state.clear()
        async with get_session() as session:
            cards, total = await EventService.list_candidates(session, page=callback_data.page, page_size=10)
        text = (
            f"🎁 <b>YANGI EVENT</b> (Mos kartalar: <b>{total}</b> ta)\nEventga qo‘yiladigan kartani tanlang:"
            if total > 0
            else "🎁 <b>YANGI EVENT</b>\n\n📭 Hali guruhga tashlanmagan bo‘sh karta yo‘q."
        )
        await edit_or_answer(callback, text, reply_markup=admin_evt_candidates_kb(admin_id, cards, callback_data.page, total, 10))
        return

    if action == "card":
        async with get_session() as session:
            cards_ok = await EventService.list_candidates(session, page=1, page_size=1000)
            card = next((c for c in cards_ok[0] if c.id == callback_data.target_id), None)
        if card is None:
            await callback.answer("Bu karta endi mos emas.", show_alert=True)
            return
        await state.set_state(AdminStates.evt_copies)
        await state.update_data(card_id=card.id, card_name=card.character_name, anime_name=card.anime_name, rarity=card.rarity)
        await edit_or_answer(callback, f"🎴 <b>{esc(card.character_name)}</b>\n\n📦 Necha nusxa sotiladi? Sonni kiriting:")
        return

    if action == "ptype":
        data = await state.get_data()
        if "card_id" not in data or "copies" not in data:
            await callback.answer("Jarayon eskirgan.", show_alert=True)
            return
        mode = callback_data.extra
        await state.update_data(mode=mode, price_coins=None, price_stars=None)
        if mode in ("coins", "both"):
            await state.set_state(AdminStates.evt_price_coins)
            await edit_or_answer(callback, "🪙 Karta narxini <b>coin</b>da kiriting:")
        else:
            await _show_stars_choice(callback, state, admin_id)
        return

    if action == "pstars":
        async with get_session() as session:
            packages = await SettingsService.get_stars_packages(session)
        pkg = next((p for p in packages if p["id"] == callback_data.extra), None)
        if pkg is None:
            await callback.answer("Tarif topilmadi.", show_alert=True)
            return
        await state.update_data(price_stars=int(pkg["stars"]))
        await _ask_evt_duration(callback, state, admin_id)
        return

    if action == "dur":
        if not callback_data.extra.isdigit() or not (1 <= int(callback_data.extra) <= 720):
            await callback.answer("Noto‘g‘ri vaqt.", show_alert=True)
            return
        await state.update_data(hours=int(callback_data.extra))
        await state.set_state(None)
        data = await state.get_data()
        if "card_id" not in data or "copies" not in data:
            await callback.answer("Jarayon eskirgan.", show_alert=True)
            return
        await edit_or_answer(callback, _evt_summary(data), reply_markup=admin_evt_confirm_kb(admin_id))
        return

    if action == "go":
        data = await state.get_data()
        if not all(k in data for k in ("card_id", "copies")):
            await callback.answer("Jarayon eskirgan.", show_alert=True)
            return
        hours = int(data.get("hours", EVENT_DURATION_HOURS))
        async with get_session() as session:
            res = await EventService.create_event(
                session=session,
                admin_telegram_id=admin_id,
                card_id=int(data["card_id"]),
                total_copies=int(data["copies"]),
                price_coins=data.get("price_coins"),
                price_stars=data.get("price_stars"),
                hours=hours,
            )
            if res.success:
                await AdminService.log_action(session, admin_id, "create_limited_event", "event", str(res.event.id))
                await session.commit()
            else:
                await session.rollback()
        if not res.success:
            await callback.answer(res.message, show_alert=True)
            return
        await state.clear()
        task = asyncio.create_task(announce_event(callback.bot, res.event.id, notify_admin_id=admin_id))
        _BG_TASKS.add(task)
        task.add_done_callback(_BG_TASKS.discard)
        await callback.answer("✅ Event boshlandi!", show_alert=True)
        await edit_or_answer(
            callback,
            f"✅ <b>Event #{res.event.id} boshlandi!</b>\n📢 E’lon barcha guruhlar va userlarga yuborilmoqda...",
            reply_markup=admin_evt_menu_kb(admin_id, []),
        )
        return

    if action in ("detail", "stop"):
        async with get_session() as session:
            if action == "stop":
                if await EventService.stop_event(session, callback_data.target_id):
                    await AdminService.log_action(session, admin_id, "stop_limited_event", "event", str(callback_data.target_id))
                await session.commit()
            pair = await EventService.get_event_with_card(session, callback_data.target_id)
        if pair is None:
            await callback.answer("Event topilmadi.", show_alert=True)
            return
        ev, card = pair
        if action == "stop":
            from app.event_announcer import finalize_ended_events

            task = asyncio.create_task(finalize_ended_events(callback.bot))
            _BG_TASKS.add(task)
            task.add_done_callback(_BG_TASKS.discard)
        prices = []
        if ev.price_coins:
            prices.append(f"🪙 {ev.price_coins:,}")
        if ev.price_stars:
            prices.append(f"⭐ {ev.price_stars:,}")
        await edit_or_answer(
            callback,
            f"🎁 <b>Event #{ev.id}</b> — {_EVT_STATUS.get(ev.status, ev.status)}\n\n"
            f"🎴 {esc(card.character_name)} ({esc(get_rarity_meta(card.rarity).name)}) · {esc(card.anime_name)}\n"
            f"📦 Sotilgan: <b>{ev.sold_count}/{ev.total_copies}</b>\n"
            f"💳 Narx: {'  /  '.join(prices)}\n"
            f"⏳ Tugash: {_local_dt(ev.ends_at):%d.%m %H:%M}",
            reply_markup=admin_evt_detail_kb(admin_id, ev),
        )


@router.message(AdminStates.evt_copies, NotCommandFilter())
async def fsm_evt_copies(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit() or not (1 <= int(raw) <= 100_000):
        await message.answer("⚠️ 1 dan 100000 gacha son kiriting.")
        return
    await state.update_data(copies=int(raw))
    await state.set_state(None)
    await message.answer("💳 Karta qanday to‘lov bilan sotiladi?", reply_markup=admin_evt_price_type_kb(message.from_user.id))


@router.message(AdminStates.evt_price_coins, NotCommandFilter())
async def fsm_evt_price_coins(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit() or not (1 <= int(raw) <= 1_000_000_000):
        await message.answer("⚠️ Narx musbat son bo‘lishi kerak.")
        return
    await state.update_data(price_coins=int(raw))
    await state.set_state(None)
    data = await state.get_data()
    if data.get("mode") == "both":
        await _show_stars_choice(message, state, message.from_user.id)
        return
    await _ask_evt_duration(message, state, message.from_user.id)


@router.message(AdminStates.evt_price_stars, NotCommandFilter())
async def fsm_evt_price_stars(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit() or not (1 <= int(raw) <= 100_000):
        await message.answer("⚠️ Stars narxi 1 dan 100,000 gacha son bo‘lishi kerak.")
        return
    await state.update_data(price_stars=int(raw))
    await _ask_evt_duration(message, state, message.from_user.id)


@router.message(AdminStates.evt_duration, NotCommandFilter())
async def fsm_evt_duration(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit() or not (1 <= int(raw) <= 720):
        await message.answer("⚠️ Davomiylik 1 dan 720 soatgacha butun son bo‘lishi kerak.")
        return
    await state.update_data(hours=int(raw))
    await state.set_state(None)
    data = await state.get_data()
    if "card_id" not in data or "copies" not in data:
        await message.answer("⚠️ Jarayon eskirgan.", reply_markup=admin_home_kb(message.from_user.id))
        return
    await message.answer(_evt_summary(data), reply_markup=admin_evt_confirm_kb(message.from_user.id))
