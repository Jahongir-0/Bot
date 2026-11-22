import asyncio
import io
import json
import logging
import re
import zipfile
import zlib
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramMigrateToChat, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select, update

from admin.filters import IsAdminFilter, has_admin_perm, resolve_admin_perms
from config.settings import get_settings
from database.session import get_session
from keyboards.v2 import (
    ADMIN_HUB_CATEGORIES,
    admin_card_give_users_kb,
    admin_rank_detail_kb,
    admin_ranks_list_kb,
    admin_rbac_menu_kb,
    admin_rbac_perms_kb,
    admin_rbac_pick_user_kb,
    admin_undropped_cards_kb,
    admin_v2_add_choice_kb,
    admin_v2_card_detail_kb,
    admin_v2_cards_action_list_kb,
    admin_v2_cards_menu_kb,
    admin_v2_edit_fields_kb,
    admin_v2_home_kb,
    admin_v2_hub_kb,
    admin_v2_media_titles_kb,
    admin_v2_title_cards_kb,
    admin_v2_view_cards_kb,
)
from models.admin import AdminAction
from models.base import ensure_utc, utcnow
from utils.timeutils import fmt_tashkent, to_tashkent
from models.card import Card
from models.group import Group
from models.schedule import ActiveDrop
from models.user import User
from models.user_card import UserCard
from models.v2_features import (
    AdCampaign,
    AdminRole,
    BackupRecord,
    Quiz,
    SecurityIncident,
    SupportTicket,
)
from services.admin_service import AdminService
from services.auction_wishlist_service import AuctionWishlistService
from services.card_level_service import CardLevelService
from services.card_service import CardService
from services.community_service import CommunityService
from services.drop_service import DropService
from services.event_service import EventService
from services.gameplay_service import GameplayService
from services.security_backup_service import MAX_ZIP_BYTES, SecurityBackupService
from services.settings_service import SettingsService
from services.user_service import UserService
from utils.callback_data import AdmV2Cb, AdminCb
from utils.constants import ADMIN_PERMISSION_SECTIONS, MEDIA_TYPES, PAGE_SIZE, get_rarity_meta, sync_runtime_ranks
from utils.formatting import esc, fmt_coins, format_card_about, format_card_block, format_drop_message
from utils.image_check import validate_image_url
from utils.tg_helpers import safe_delete, NotCommandFilter, edit_or_answer, get_bot_username, is_super_admin, send_card_message, send_card_visual

logger = logging.getLogger(__name__)

router = Router(name="admin_v2")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


class AdminV2States(StatesGroup):
    # Cards
    waiting_zip_cards = State()
    waiting_card_search = State()
    waiting_card_edit_val = State()
    waiting_card_give_user_search = State()

    # Admins RBAC
    waiting_admin_search = State()
    waiting_admin_role_name = State()

    # Channels
    waiting_channel_link = State()
    waiting_channel_title = State()
    waiting_channel_days = State()
    waiting_channel_subs = State()

    # Ranks
    waiting_rank_field = State()
    waiting_rank_add_name = State()
    waiting_rank_add_emoji = State()
    waiting_rank_add_prob = State()
    waiting_rank_add_drops = State()

    # Quiz
    waiting_quiz_title = State()
    waiting_quiz_coins = State()
    waiting_quiz_q_text = State()
    waiting_quiz_q_opts = State()
    waiting_quiz_sched_dt = State()
    waiting_quiz_clone_coins = State()

    # Games
    waiting_games_field = State()

    # Auction
    waiting_auc_price = State()
    waiting_auc_hours = State()
    waiting_auc_step = State()

    # Season
    waiting_season_name = State()
    waiting_season_days = State()
    waiting_season_prize = State()

    # Reklama
    waiting_ad_content = State()
    waiting_ad_edit_text = State()

    # Broadcast / Message
    waiting_msg_sched_dt = State()
    waiting_msg_content = State()

    # Memory / Backup
    waiting_backup_date = State()
    waiting_backup_restore = State()

    # Support & Security
    waiting_support_reply = State()
    waiting_sec_target = State()
    waiting_sec_reason = State()


def _back_home_btn(owner_id: int) -> InlineKeyboardButton:
    return InlineKeyboardButton(text="⬅️ Admin Panel", callback_data=AdminCb(owner_id=owner_id, section="home").pack())


async def _deny_if_no_perm(callback: CallbackQuery, admin_perms: set | None, sec: str) -> bool:
    if not has_admin_perm(callback.from_user, admin_perms, sec):
        await callback.answer("⛔ Bu bo‘limga ruxsatingiz yo‘q.", show_alert=True)
        return True
    return False


def _cancel_kb(owner_id: int, sec: str, act: str = "v") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Bekor", callback_data=AdmV2Cb(owner_id=owner_id, sec=sec, act=act).pack())]]
    )


def _local_tz() -> ZoneInfo:
    from utils.timeutils import TASHKENT

    return TASHKENT


def _parse_local_dt(raw: str) -> Optional[datetime]:
    cleaned = " ".join((raw or "").strip().split())
    for fmt in ("%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M", "%d.%m.%y %H:%M"):
        try:
            dt_local = datetime.strptime(cleaned, fmt).replace(tzinfo=_local_tz())
            return dt_local.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


# ===========================================================================
# 0. ADMIN HUB CATEGORY ROUTER (sec="hub")
# ===========================================================================
@router.callback_query(AdmV2Cb.filter(F.sec == "hub"))
async def cb_v2_hub(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    await state.clear()
    perms = resolve_admin_perms(callback.from_user, admin_perms)
    cat_key = callback_data.ext or "cards_evt"
    cat_info = next(((title, desc, secs) for k, title, desc, secs in ADMIN_HUB_CATEGORIES if k == cat_key), None)
    if cat_info is None or not any(s in perms for s in cat_info[2]):
        await callback.answer("⛔ Bu toifaga ruxsatingiz yo‘q.", show_alert=True)
        return
    title, desc, _secs = cat_info
    await edit_or_answer(
        callback,
        f"<b>{esc(title)}</b>\n━━━━━━━━━━━━━━━\n{esc(desc)}\n\n👇 Kerakli bo‘limni tanlang:",
        reply_markup=admin_v2_hub_kb(callback.from_user.id, cat_key, perms),
    )


# ===========================================================================
# 1. CARDS V2 (Add choice, ZIP, View Tabs, Media Titles, Edit, Delete, Search, Restore)
# ===========================================================================
@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "v")))
async def cb_v2_cards_menu(callback: CallbackQuery, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    await state.clear()
    await edit_or_answer(
        callback,
        "🎴 <b>KARTALAR BO‘LIMI</b>\n━━━━━━━━━━━━━━━\nKerakli amalni tanlang:",
        reply_markup=admin_v2_cards_menu_kb(callback.from_user.id),
    )


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "add_menu")))
async def cb_v2_add_choice(callback: CallbackQuery, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    await state.clear()
    await edit_or_answer(
        callback,
        "➕ <b>KARTA QO‘SHISH</b>\n━━━━━━━━━━━━━━━\nKartalarni qanday usulda qo‘shasiz?",
        reply_markup=admin_v2_add_choice_kb(callback.from_user.id),
    )


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "zip")))
async def cb_v2_zip_prompt(callback: CallbackQuery, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    from services.card_zip_import import rank_table

    async with get_session() as session:
        sync_runtime_ranks(await GameplayService.get_rank_definitions(session))
    await state.set_state(AdminV2States.waiting_zip_cards)
    await state.update_data(owner_tg_id=callback.from_user.id)
    ranks = "\n".join(f"   <b>{rid}</b> = {esc(name)}" for rid, name in rank_table())
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📥 Namuna ZIP olish", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="cards", act="zip_sample").pack()))
    kb.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="cards", act="v").pack()))
    await edit_or_answer(
        callback,
        "📦 <b>ZIP ORQALI KARTA QO‘SHISH</b>\n\n"
        "ZIP ichida <code>cards.json</code> (yoki <code>cards.csv</code>) va rasmlar bo‘lsin.\n\n"
        "<b>Majburiy maydonlar</b> (bittasi yo‘q bo‘lsa karta qo‘shilmaydi):\n"
        "• <code>image</code> — rasm fayl nomi\n"
        "• <code>character_name</code> — personaj ismi\n"
        "• <code>media_type</code> — Anime, Manga, Manhwa, Manhua, Comic, Cartoon, Movie, TV Series, Other\n"
        "• <code>media_name</code> — asar nomi\n"
        "• <code>rank_id</code> — rank raqami:\n"
        f"{ranks}\n\n"
        "ℹ️ Rank nomini o‘zgartirsangiz ham raqami o‘zgarmaydi.\n"
        "📥 Namuna ZIP ichida sun’iy intellekt uchun tayyor ko‘rsatma ham bor.\n\n"
        "Endi <b>.zip</b> faylni yuboring:",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "zip_sample")))
async def cb_v2_zip_sample(callback: CallbackQuery, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    from aiogram.types import BufferedInputFile

    from services.card_zip_sample import build_sample_zip

    async with get_session() as session:
        sync_runtime_ranks(await GameplayService.get_rank_definitions(session))
    await callback.message.answer_document(
        BufferedInputFile(build_sample_zip(), filename="namuna_kartalar.zip"),
        caption="📥 Namuna ZIP. Ichida: cards.json, images/ va PROMPT_FOR_AI.txt (AI ga berish uchun).",
    )
    await callback.answer()


@router.message(AdminV2States.waiting_zip_cards, F.document)
async def fsm_v2_zip_upload(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    doc = message.document
    if not doc or not (doc.file_name or "").lower().endswith(".zip"):
        await message.answer("⚠️ Faqat <code>.zip</code> fayl yuboring.")
        return
    if (doc.file_size or 0) > MAX_ZIP_BYTES:
        await message.answer("⚠️ ZIP hajmi 50 MB dan oshmasligi kerak.")
        return
    await state.clear()
    wait_msg = await message.answer("⏳ ZIP tekshirilmoqda...")

    buf = io.BytesIO()
    await message.bot.download(doc, destination=buf)

    async def _progress(text: str) -> None:
        await wait_msg.edit_text(text)

    try:
        async with get_session() as session:
            rep = await SecurityBackupService.import_cards_from_zip(
                session, buf.getvalue(), message.from_user.id, message.bot, _progress
            )
            await session.commit()
    except Exception as exc:  # kutilmagan xato
        logger.exception("ZIP import crashed")
        await wait_msg.edit_text(f"❌ ZIP import to‘xtadi: <code>{esc(str(exc)[:200])}</code>", reply_markup=admin_v2_cards_menu_kb(message.from_user.id))
        return

    if rep.fatal:
        await wait_msg.edit_text(f"❌ <b>QO‘SHILMADI</b>\n\nSabab: {esc(rep.fatal)}", reply_markup=admin_v2_cards_menu_kb(message.from_user.id))
        return

    head = "✅ <b>KARTALAR QO‘SHILDI!</b>" if rep.added and not rep.failed else (
        "⚠️ <b>QISMAN QO‘SHILDI</b>" if rep.added else "❌ <b>HECH QANDAY KARTA QO‘SHILMADI</b>"
    )
    lines = [
        head, "",
        f"📁 Fayllar: <b>{rep.total_files}</b>  |  🖼 Rasmlar: <b>{rep.total_images}</b>",
        f"🎴 Ro‘yxatdagi kartalar: <b>{rep.total_cards}</b>",
        f"✅ Qo‘shildi: <b>{rep.added}</b>",
        f"♻️ Takroriy (o‘tkazildi): <b>{rep.duplicates}</b>",
        f"❌ Qo‘shilmadi: <b>{rep.failed}</b>",
    ]
    if rep.added_by_rank:
        lines.append("\n<b>Rank bo‘yicha:</b> " + ", ".join(f"{esc(k)} — {v}" for k, v in rep.added_by_rank.items()))
    if rep.added_by_media:
        lines.append("<b>Tur bo‘yicha:</b> " + ", ".join(f"{esc(k)} — {v}" for k, v in rep.added_by_media.items()))
    if rep.failed_files:
        lines.append("\n<b>Qo‘shilmaganlar va sababi:</b>")
        for item in rep.failed_files[:10]:
            lines.append(f"• {esc(item)}")
        if len(rep.failed_files) > 10:
            lines.append(f"… va yana {len(rep.failed_files) - 10} ta (to‘liq ro‘yxat fayl ichida)")
    await wait_msg.edit_text("\n".join(lines), reply_markup=admin_v2_cards_menu_kb(message.from_user.id))
    if len(rep.failed_files) > 10:
        from aiogram.types import BufferedInputFile

        await message.answer_document(
            BufferedInputFile("\n".join(rep.failed_files).encode("utf-8"), filename="qoshilmaganlar.txt"),
            caption="❌ Qo‘shilmagan kartalarning to‘liq ro‘yxati",
        )


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "view_tabs")))
async def cb_v2_view_cards(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    tab = callback_data.ext or "all"
    page = max(1, callback_data.p)
    async with get_session() as session:
        cards, total = await AdminService.get_v2_cards_by_tab(session, tab=tab, page=page, page_size=PAGE_SIZE)
    if not cards and total > 0 and page > 1:
        page = 1
        async with get_session() as session:
            cards, total = await AdminService.get_v2_cards_by_tab(session, tab=tab, page=1, page_size=PAGE_SIZE)
    tab_titles = {
        "sent_group": "GURUHGA TUSHGANLAR (oxirgi yuborilganlar yuqorida)",
        "not_sent": "HALI TUSHMAGANLAR (guruhga tashlanmagan)",
        "anime": "ANIME KARTALARI",
        "manga": "MANGA / MANHWA / MANHUA",
        "movie": "KINO / SERIAL",
        "cartoon": "MULTFILM / KOMIKS",
        "event": "EVENT KARTALARI",
        "other": "BOSHQA KARTALAR",
        "deleted": "O‘CHIRILGAN KARTALAR",
        "all": "BARCHA KARTALAR",
    }
    txt = f"👁 <b>{tab_titles.get(tab, tab.upper())}</b> (Jami: <b>{total}</b> ta | 20/sahifa)"
    if not cards:
        txt += "\n\n📭 Bu bo‘limda kartalar yo‘q."
    await edit_or_answer(
        callback,
        txt,
        reply_markup=admin_v2_view_cards_kb(callback.from_user.id, cards, tab, page, total, page_size=PAGE_SIZE),
    )


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "titles")))
async def cb_v2_media_titles(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    mgroup = callback_data.ext or "anime"
    page = max(1, callback_data.p)
    async with get_session() as session:
        titles, total = await AdminService.get_v2_media_titles(session, media_group=mgroup, page=page, page_size=PAGE_SIZE)
    txt = f"📁 <b>{esc(mgroup.upper())} NOMLARI</b> (Karta soni bo‘yicha | Jami: <b>{total}</b> ta)\n\nIchiga kirish uchun nomni tanlang:"
    if not titles:
        txt = f"📁 <b>{esc(mgroup.upper())} NOMLARI</b>\n\n📭 Hozircha kartalar yo‘q."
    await edit_or_answer(
        callback,
        txt,
        reply_markup=admin_v2_media_titles_kb(callback.from_user.id, titles, mgroup, page, total, page_size=PAGE_SIZE),
    )


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "mtitle")))
async def cb_v2_media_title_cards(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    mgroup = callback_data.ext or "anime"
    page = max(1, callback_data.p)
    async with get_session() as session:
        title, cards, total = await AdminService.get_v2_cards_by_media_title(
            session, media_group=mgroup, rep_card_id=callback_data.tid, page=page, page_size=PAGE_SIZE
        )
    txt = f"🎬 <b>{esc(title or mgroup.upper())}</b> (Jami: <b>{total}</b> ta karta)\n\nTahrirlash yoki ko‘rish uchun kartani tanlang:"
    if not cards:
        txt += "\n\n📭 Topilmadi."
    await edit_or_answer(
        callback,
        txt,
        reply_markup=admin_v2_title_cards_kb(callback.from_user.id, cards, callback_data.tid, mgroup, page, total, page_size=PAGE_SIZE),
    )


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act.in_({"edit_list", "del_list"}))))
async def cb_v2_cards_action_list(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    mode = callback_data.act
    page = max(1, callback_data.p)
    async with get_session() as session:
        cards, total = await AdminService.get_v2_cards_by_tab(session, tab="all", page=page, page_size=PAGE_SIZE)
    hdr = "✏️ <b>KARTA TAHRIRLASH</b>" if mode == "edit_list" else "🗑 <b>KARTA O‘CHIRISH</b>"
    txt = f"{hdr} (Jami: <b>{total}</b> ta | 20/sahifa)\n\nKerakli kartani tanlang yoki qidiring:"
    if not cards:
        txt += "\n\n📭 Kartalar yo‘q."
    await edit_or_answer(
        callback,
        txt,
        reply_markup=admin_v2_cards_action_list_kb(callback.from_user.id, cards, mode, page, total, page_size=PAGE_SIZE),
    )


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "del_one")))
async def cb_v2_delete_one_confirm(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    async with get_session() as session:
        card = await session.get(Card, callback_data.tid)
    title = f"{card.character_name} ({card.card_code})" if card else f"#{callback_data.tid}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Arxivlab o‘chirish (userlarda qolsin)",
                    callback_data=AdminCb(owner_id=callback.from_user.id, section="cards", action="delete_exec", target_id=callback_data.tid, extra="archive").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🪙 To‘liq o‘chirish + Coin kompensatsiya",
                    callback_data=AdminCb(owner_id=callback.from_user.id, section="cards", action="delete_exec", target_id=callback_data.tid, extra="refund").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ ORQAGA",
                    callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="cards", act="del_list", p=max(1, callback_data.p)).pack(),
                )
            ],
        ]
    )
    await edit_or_answer(
        callback,
        f"🗑 <b>KARTANI O‘CHIRISH: {esc(title)}</b>\n\nMavjud egalardagi nusxalar bilan qanday yo‘l tutamiz?",
        reply_markup=kb,
    )


async def _build_v2_card_detail_text(session, card: Card) -> str:
    owners_list, drops_sent, latest_uc = [], 0, None
    try:
        owners_list, drops_sent = await AdminService.get_card_ownership_and_drops(session, card.id)
    except Exception:
        logger.exception("card_det: egalari/tashlanishlar o'qilmadi (card %s)", card.id)
        await session.rollback()
    try:
        latest_uc = (
            await session.execute(select(UserCard).where(UserCard.card_id == card.id).order_by(UserCard.id.desc()).limit(1))
        ).scalar_one_or_none()
    except Exception:
        logger.exception("card_det: oxirgi egasi o'qilmadi (card %s)", card.id)
        await session.rollback()
    max_lvl = 10
    try:
        cfg = await CardLevelService.get_config(session)
        max_lvl = max(1, int(cfg.get("max_level", 10)))
    except Exception:
        pass

    owner_preview = ", ".join(f"{n} (×{q})" for n, q in owners_list[:4]) if owners_list else "Yo‘q"
    acquired = getattr(latest_uc, "acquired_at", None) if latest_uc is not None else None
    claimed_dt = fmt_tashkent(acquired, "%Y-%m-%d %H:%M") if acquired else "—"
    preset_lvl = max(1, int(getattr(card, "preset_level", 1) or 1))
    preset_line = (
        f"\n⭐ <b>Boshlang‘ich daraja:</b> Lvl {preset_lvl}/{max_lvl} (Auksion / Event / Sovg‘a)"
        if card.dropped_count == 0 and not card.is_event_card and not card.is_deleted
        else ""
    )
    try:
        return (
            f"{format_card_block(card, show_status=True, owner_name=owner_preview)}\n"
            f"📤 <b>Tashlangan:</b> {drops_sent} marta\n"
            f"🗓 <b>Oxirgi olingan sana:</b> <code>{claimed_dt}</code>"
            f"{preset_line}\n"
            f"🆔 <b>Card DB ID:</b> <code>{card.id}</code>"
        )
    except Exception:
        logger.exception("card_det: matn tuzilmadi (card %s)", card.id)
        return f"🎴 <b>{esc(card.character_name)}</b>\n📺 {esc(card.anime_name)}{preset_line}\n🆔 Card DB ID: <code>{card.id}</code>"


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "card_det")))
async def cb_v2_card_detail(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    async with get_session() as session:
        card = await session.get(Card, callback_data.tid)
        if card is None:
            await callback.answer("Karta topilmadi.", show_alert=True)
            return
        text = await _build_v2_card_detail_text(session, card)
    kb = admin_v2_card_detail_kb(
        callback.from_user.id,
        card,
        tab=callback_data.ext or "all",
        page=callback_data.p,
        is_super=is_super_admin(callback.from_user),
    )
    if callback.message and card.image and (card.image.startswith("http") or len(card.image) > 20):
        try:
            await send_card_message(callback.bot, callback.message.chat.id, card, text, kb)
            await safe_delete(callback.bot, callback.message.chat.id, callback.message.message_id)
            await callback.answer()
            return
        except Exception:
            logger.exception("card_det: rasm yuborilmadi (card %s)", card.id)
    await edit_or_answer(callback, text, reply_markup=kb)


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "lvl_info")))
async def cb_v2_card_lvl_info(callback: CallbackQuery, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    await callback.answer(
        "⭐ Hali guruhga tashlanmagan kartaning boshlang‘ich darajasi: Auksion, Event yoki Userga berishda shu Lvl bilan beriladi.",
        show_alert=True,
    )


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act.in_({"lvl_inc", "lvl_dec"}))))
async def cb_v2_card_preset_level(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    delta = 1 if callback_data.act == "lvl_inc" else -1
    async with get_session() as session:
        card = await session.get(Card, callback_data.tid)
        if card is None:
            await callback.answer("Karta topilmadi.", show_alert=True)
            return
        if card.dropped_count != 0 or card.is_event_card or card.is_deleted:
            await callback.answer("⚠️ Faqat hali guruhga tashlanmagan kartada o‘zgartirish mumkin.", show_alert=True)
            return
        cfg = await CardLevelService.get_config(session)
        max_lvl = max(1, int(cfg.get("max_level", 10)))
        cur_lvl = max(1, int(getattr(card, "preset_level", 1) or 1))
        new_lvl = max(1, min(max_lvl, cur_lvl + delta))
        if new_lvl == cur_lvl:
            limit_msg = f"🔝 Maksimal daraja (Lvl {max_lvl})!" if delta > 0 else "🔻 Minimal daraja (Lvl 1)!"
            await callback.answer(limit_msg, show_alert=False)
            return
        card.preset_level = new_lvl
        await CommunityService.log_audit(
            session, callback.from_user.id, "set_card_preset_level", "card", str(card.id),
            old_value={"preset_level": cur_lvl}, new_value={"preset_level": new_lvl},
        )
        await session.commit()
        text = await _build_v2_card_detail_text(session, card)
    kb = admin_v2_card_detail_kb(
        callback.from_user.id,
        card,
        tab=callback_data.ext or "all",
        page=callback_data.p,
        is_super=is_super_admin(callback.from_user),
    )
    if callback.message and callback.message.photo:
        try:
            await callback.message.edit_caption(caption=text, reply_markup=kb)
            await callback.answer(f"⭐ Boshlang‘ich daraja: Lvl {new_lvl}")
            return
        except Exception:
            pass
    await edit_or_answer(callback, text, reply_markup=kb)
    try:
        await callback.answer(f"⭐ Boshlang‘ich daraja: Lvl {new_lvl}")
    except Exception:
        pass


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "evt_go")))
async def cb_v2_card_to_event(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if not has_admin_perm(callback.from_user, admin_perms, "events"):
        await callback.answer("⛔ Event bo‘limiga ruxsatingiz yo‘q.", show_alert=True)
        return
    async with get_session() as session:
        card = (
            await session.execute(
                select(Card).where(Card.id == callback_data.tid, *EventService._candidate_conditions())
            )
        ).scalar_one_or_none()
    if card is None:
        await callback.answer("⚠️ Bu karta eventga mos emas (allaqachon tashlangan, o‘chirilgan yoki kimdadir bor).", show_alert=True)
        return
    from admin.handlers import AdminStates

    preset_lvl = max(1, int(getattr(card, "preset_level", 1) or 1))
    await state.set_state(AdminStates.evt_copies)
    await state.update_data(
        card_id=card.id,
        card_name=card.character_name,
        anime_name=card.anime_name,
        rarity=card.rarity,
        preset_level=preset_lvl,
    )
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Bekor qilish", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="cards", act="card_det", tid=card.id).pack())]]
    )
    await edit_or_answer(
        callback,
        f"🎯 <b>YANGI EVENT</b>\n\n"
        f"🎴 Karta: <b>{esc(card.character_name)}</b>\n"
        f"⭐ Boshlang‘ich daraja: <b>Lvl {preset_lvl}</b>\n\n"
        f"📦 Necha nusxa sotiladi? Sonni kiriting:",
        reply_markup=cancel_kb,
    )


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "give_u")))
async def cb_v2_card_give_users_list(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    back_tab = callback_data.ext or "all"
    page = max(1, callback_data.p)
    async with get_session() as session:
        card = await session.get(Card, callback_data.tid)
        if card is None or card.is_deleted:
            await callback.answer("Karta topilmadi.", show_alert=True)
            return
        users, total = await AdminService.get_users_for_card_grant(
            session, callback.from_user.id, page=page, page_size=12
        )
    preset_lvl = max(1, int(getattr(card, "preset_level", 1) or 1))
    kb = admin_card_give_users_kb(
        callback.from_user.id,
        card.id,
        users,
        page=page,
        total=total,
        current_admin_tg_id=callback.from_user.id,
        back_tab=back_tab,
        page_size=12,
    )
    txt = (
        f"🎁 <b>KARTANI USERGA BERISH</b>\n\n"
        f"🎴 Karta: <b>{esc(card.character_name)}</b> (Lvl {preset_lvl})\n"
        f"👥 Faol userlar: <b>{total}</b> ta — kimga beramiz?"
    )
    await edit_or_answer(callback, txt, reply_markup=kb)


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "give_us")))
async def cb_v2_card_give_user_search(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    await state.set_state(AdminV2States.waiting_card_give_user_search)
    await state.update_data(
        owner_tg_id=callback.from_user.id,
        give_card_id=callback_data.tid,
        back_tab=callback_data.ext or "all",
    )
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="cards", act="give_u", tid=callback_data.tid, ext=callback_data.ext or "all", p=1).pack())]]
    )
    await edit_or_answer(
        callback,
        "🔍 <b>USER QIDIRISH</b>\n\nTelegram ID, ism yoki <code>@username</code> yozing:\n<i>Bekor: /cancel</i>",
        reply_markup=cancel_kb,
    )


@router.message(AdminV2States.waiting_card_give_user_search, NotCommandFilter())
async def fsm_v2_card_give_user_search(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    q = (message.text or "").strip()
    card_id = int(data.get("give_card_id") or 0)
    back_tab = str(data.get("back_tab") or "all")
    await state.clear()
    async with get_session() as session:
        card = await session.get(Card, card_id)
        users, total = await AdminService.get_users_for_card_grant(
            session, message.from_user.id, page=1, page_size=12, query=q
        )
    if card is None:
        await message.answer("❌ Karta topilmadi.")
        return
    preset_lvl = max(1, int(getattr(card, "preset_level", 1) or 1))
    kb = admin_card_give_users_kb(
        message.from_user.id,
        card.id,
        users,
        page=1,
        total=total,
        current_admin_tg_id=message.from_user.id,
        back_tab=back_tab,
        page_size=12,
        query=q,
    )
    if not users:
        await message.answer(f"📭 <code>{esc(q)}</code> bo‘yicha foydalanuvchi topilmadi.", reply_markup=kb)
        return
    await message.answer(
        f"🔍 <b>Qidiruv natijasi ({total} ta)</b>\n🎴 Karta: <b>{esc(card.character_name)}</b> (Lvl {preset_lvl}):",
        reply_markup=kb,
    )


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "give_go")))
async def cb_v2_card_give_exec(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    target_uid = int(callback_data.ext) if (callback_data.ext or "").isdigit() else 0
    if target_uid <= 0:
        await callback.answer("❌ Noto‘g‘ri foydalanuvchi.", show_alert=True)
        return
    async with get_session() as session:
        card = await AdminService.give_card_to_user(session, callback.from_user.id, target_uid, callback_data.tid)
        u = await session.get(User, target_uid) if card else None
        if card is None or u is None:
            await session.rollback()
            await callback.answer("❌ Karta yoki foydalanuvchi topilmadi.", show_alert=True)
            return
        preset_lvl = max(1, int(getattr(card, "preset_level", 1) or 1))
        await session.commit()
        text = await _build_v2_card_detail_text(session, card)

    lvl_note = f" (Lvl {preset_lvl})" if preset_lvl > 1 else ""
    if u.telegram_id != callback.from_user.id:
        try:
            await callback.bot.send_message(
                u.telegram_id,
                f"🎁 Admin sizga <b>{esc(card.character_name)}</b>{lvl_note} ({esc(get_rarity_meta(card.rarity).name)}) kartasini sovg‘a qildi!",
            )
        except Exception:
            pass
    await callback.answer(f"✅ {card.character_name}{lvl_note} → {u.display_name} ga berildi!", show_alert=True)
    kb = admin_v2_card_detail_kb(
        callback.from_user.id,
        card,
        tab="all",
        page=1,
        is_super=is_super_admin(callback.from_user),
    )
    if callback.message and card.image and (card.image.startswith("http") or len(card.image) > 20):
        try:
            await send_card_message(callback.bot, callback.message.chat.id, card, text, kb)
            await safe_delete(callback.bot, callback.message.chat.id, callback.message.message_id)
            return
        except Exception:
            pass
    await edit_or_answer(callback, text, reply_markup=kb)


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act.in_({"about", "owners", "hist", "restore"}))))
async def cb_v2_card_sub_actions(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    act = callback_data.act
    async with get_session() as session:
        card = await session.get(Card, callback_data.tid)
        if card is None:
            await callback.answer("Karta topilmadi.", show_alert=True)
            return
        if act == "about":
            text = format_card_about(card)
        elif act == "owners":
            owners, drops = await AdminService.get_card_ownership_and_drops(session, card.id)
            lines = [f"👤 <b>{esc(card.character_name)} EGALARI</b> (Tashlangan: {drops})\n"]
            for name, qty in owners[:20]:
                lines.append(f"• {esc(name)} — <b>{qty}</b> ta")
            if not owners:
                lines.append("📭 Hali hech kimda yo‘q.")
            text = "\n".join(lines)
        elif act == "hist":
            hist = await AuctionWishlistService.get_card_history(session, card.id, limit=20)
            lines = [f"📜 <b>KARTA TARIXI: {esc(card.character_name)}</b>\n"]
            for h in hist:
                dt = fmt_tashkent(h.created_at, "%d.%m %H:%M")
                lines.append(f"• <code>{dt}</code> — <b>{esc(h.event_type)}</b>" + (f" ({h.price:,} 🪙)" if h.price else ""))
            if not hist:
                lines.append("📭 Tarix yozuvlari yo‘q.")
            text = "\n".join(lines)
        else:
            if not is_super_admin(callback.from_user):
                await callback.answer("⛔ Faqat Super Admin tiklay oladi.", show_alert=True)
                return
            await CardService.restore_card(session, card.id)
            await CommunityService.log_audit(session, callback.from_user.id, "restore_card", "card", str(card.id))
            await session.commit()
            await callback.answer("♻️ Karta tiklandi!", show_alert=True)
            text = f"♻️ <b>{esc(card.character_name)}</b> muvaffaqiyatli tiklandi!"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Karta", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="cards", act="card_det", tid=callback_data.tid).pack())]]
    )
    await edit_or_answer(callback, text, reply_markup=kb)


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act.in_({"search", "edit_search", "del_search"}))))
async def cb_v2_card_search_prompt(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    next_map = {"edit_search": "edit_pick", "del_search": "del_one", "search": "card_det"}
    await state.set_state(AdminV2States.waiting_card_search)
    await state.update_data(owner_tg_id=callback.from_user.id, next_act=next_map.get(callback_data.act, "card_det"))
    await edit_or_answer(
        callback,
        "🔍 <b>KARTA QIDIRISH</b>\n\nPersonaj ismi, anime/media nomi, <code>CRD-...</code> yoki DB ID yozing:\n<i>Bekor qilish: /cancel</i>",
        reply_markup=_cancel_kb(callback.from_user.id, "cards", "v"),
    )


@router.message(AdminV2States.waiting_card_search, NotCommandFilter())
async def fsm_v2_card_search(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    q = (message.text or "").strip()
    next_act = data.get("next_act", "card_det")
    await state.clear()
    async with get_session() as session:
        cards, total = await AdminService.get_v2_cards_by_tab(session, tab="all", query=q, page=1, page_size=20)
    if not cards:
        await message.answer(f"📭 <code>{esc(q)}</code> bo‘yicha karta topilmadi.", reply_markup=admin_v2_cards_menu_kb(message.from_user.id))
        return
    b = InlineKeyboardBuilder()
    for c in cards:
        meta = get_rarity_meta(c.rarity)
        b.row(
            InlineKeyboardButton(
                text=f"{meta.emoji} {c.character_name[:22]} · {c.anime_name[:16]} ({c.card_code})"[:60],
                callback_data=AdmV2Cb(owner_id=message.from_user.id, sec="cards", act=next_act, tid=c.id).pack(),
            )
        )
    b.row(InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=message.from_user.id, sec="cards", act="v").pack()))
    await message.answer(f"🔍 Topildi: <b>{total}</b> ta karta (20 tagacha ko‘rsatilmoqda):", reply_markup=b.as_markup())


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "edit_pick")))
async def cb_v2_edit_fields(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    await edit_or_answer(
        callback,
        f"✏️ <b>KARTA #{callback_data.tid} TAHRIRLASH</b>\n\nQaysi maydonni o‘zgartirasiz?",
        reply_markup=admin_v2_edit_fields_kb(callback.from_user.id, callback_data.tid),
    )


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "edit_f")))
async def cb_v2_edit_field_start(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    field = callback_data.ext
    cid = callback_data.tid
    if field == "mtype":
        b = InlineKeyboardBuilder()
        for i in range(0, len(MEDIA_TYPES), 3):
            b.row(*[InlineKeyboardButton(text=m, callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="cards", act="set_mtype", tid=cid, ext=m).pack()) for m in MEDIA_TYPES[i : i + 3]])
        b.row(InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="cards", act="edit_pick", tid=cid).pack()))
        await edit_or_answer(callback, "🎬 Yangi Media Type tanlang:", reply_markup=b.as_markup())
        return
    if field == "rank":
        async with get_session() as session:
            ranks = await GameplayService.get_rank_definitions(session)
        b = InlineKeyboardBuilder()
        for idx, r in enumerate(ranks):
            b.row(
                InlineKeyboardButton(
                    text=f"{r.get('emoji', '⭐')} {r['name']} ({r.get('drop_limit', 1)} drop)",
                    callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="cards", act="set_rank", tid=cid, ext=str(idx)).pack(),
                )
            )
        b.row(InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="cards", act="edit_pick", tid=cid).pack()))
        await edit_or_answer(callback, "⭐ Yangi Rank tanlang:", reply_markup=b.as_markup())
        return

    prompts = {
        "char": "👤 Yangi personaj ismini yozing:",
        "anime": "📺 Yangi media/anime nomini yozing:",
        "img": "🖼 Yangi rasmni foto sifatida yuboring yoki URL yozing:",
        "desc": "📝 Yangi tavsif (description) matnini yozing:",
        "alias": "🏷 Aliaslarni vergul bilan yozing (masalan: <code>Gojo, Satoru</code>):",
        "limit": "🔁 Guruhga nechta tushish sonini (drop limit) yozing (masalan: <code>3</code>):",
        "value": "💎 Yangi coin qiymatini kiriting (musbat son):",
    }
    await state.set_state(AdminV2States.waiting_card_edit_val)
    await state.update_data(owner_tg_id=callback.from_user.id, edit_card_id=cid, edit_field=field)
    await edit_or_answer(
        callback,
        prompts.get(field, "Yangi qiymatni yozing:") + "\n<i>Bekor: /cancel</i>",
        reply_markup=_cancel_kb(callback.from_user.id, "cards", "v"),
    )


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act.in_({"set_mtype", "set_rank"}))))
async def cb_v2_edit_inline_field(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    async with get_session() as session:
        card = await session.get(Card, callback_data.tid)
        if card is None:
            await callback.answer("Karta topilmadi.", show_alert=True)
            return
        if callback_data.act == "set_mtype":
            old = card.media_type
            card.media_type = callback_data.ext
            await CommunityService.log_audit(session, callback.from_user.id, "edit_card", "card", str(card.id), {"media_type": old}, {"media_type": card.media_type})
        else:
            ranks = await GameplayService.get_rank_definitions(session)
            r_obj = ranks[int(callback_data.ext)] if callback_data.ext.isdigit() and 0 <= int(callback_data.ext) < len(ranks) else None
            new_rank = str(r_obj["name"]) if r_obj else callback_data.ext
            old = card.rarity
            card.rarity = new_rank
            if r_obj:
                card.drop_limit = int(r_obj.get("drop_limit", card.drop_limit))
                card.drop_weight = int(r_obj.get("drop_probability", card.drop_weight))
            else:
                card.drop_weight = get_rarity_meta(new_rank).drop_weight
            await CommunityService.log_audit(session, callback.from_user.id, "edit_card", "card", str(card.id), {"rarity": old}, {"rarity": card.rarity})
        await session.commit()
    await callback.answer("✅ Yangilandi!")
    await edit_or_answer(callback, f"✅ Karta #{callback_data.tid} yangilandi!", reply_markup=admin_v2_edit_fields_kb(callback.from_user.id, callback_data.tid))


@router.message(AdminV2States.waiting_card_edit_val, NotCommandFilter())
async def fsm_v2_edit_card_val(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    cid = int(data["edit_card_id"])
    field = data["edit_field"]
    async with get_session() as session:
        card = await session.get(Card, cid)
        if card is None:
            await state.clear()
            await message.answer("❌ Karta topilmadi.")
            return
        if field == "img":
            if message.photo:
                fid = message.photo[-1].file_id
                card.image = fid
                card.image_file_id = fid
            elif message.text:
                ok, err = await validate_image_url(message.text)
                if not ok:
                    await message.answer(err)
                    return
                card.image = message.text.strip()[:512]
                card.image_file_id = None
            else:
                await message.answer("⚠️ Foto yoki to‘g‘ri URL yuboring.")
                return
        elif field == "char":
            card.character_name = " ".join((message.text or "").split())[:128]
        elif field == "anime":
            card.anime_name = " ".join((message.text or "").split())[:128]
        elif field == "desc":
            meta = dict(card.metadata_json or {})
            meta["description"] = (message.text or "").strip()
            card.metadata_json = meta
        elif field == "alias":
            card.aliases = [a.strip() for a in (message.text or "").split(",") if a.strip()]
        elif field == "limit":
            raw = (message.text or "").strip()
            if not raw.isdigit() or int(raw) < 1:
                await message.answer("⚠️ Kamida 1 butun son kiriting.")
                return
            card.drop_limit = int(raw)
        elif field == "value":
            raw = (message.text or "").strip()
            if not raw.isdigit():
                await message.answer("⚠️ Musbat butun son kiriting.")
                return
            card.base_value = int(raw)
        await CommunityService.log_audit(session, message.from_user.id, "edit_card", "card", str(card.id), new_value={"field": field})
        await session.commit()
    await state.clear()
    await message.answer("✅ Karta maydoni yangilandi!", reply_markup=admin_v2_edit_fields_kb(message.from_user.id, cid))


# ===========================================================================
# 2. GROUPS UNDROPPED DROP PICKER (sec="groups", act="pick_card" / "drop_sel")
# ===========================================================================
@router.callback_query(AdmV2Cb.filter((F.sec == "groups") & (F.act == "pick_card")))
async def cb_v2_group_pick_card(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "groups"):
        return
    gid = callback_data.tid
    page = max(1, callback_data.p)
    async with get_session() as session:
        cards, total = await AdminService.get_undropped_cards_paginated(session, page=page, page_size=PAGE_SIZE)
    back_cb = AdminCb(owner_id=callback.from_user.id, section="groups", action="group_view", target_id=gid).pack()
    txt = (
        f"🎴 <b>GURUHGA TASHLASH UCHUN KARTA TANLANG</b>\nHali tashlanmagan kartalar (<b>{total}</b> ta):"
        if cards
        else "📭 Hozircha hali guruhga tashlanmagan bo‘sh kartalar yo‘q."
    )
    await edit_or_answer(
        callback,
        txt,
        reply_markup=admin_undropped_cards_kb(
            callback.from_user.id, cards, "groups", "drop_sel", page, total, back_cb, target_ctx_id=gid, page_size=PAGE_SIZE
        ),
    )


@router.callback_query(AdmV2Cb.filter((F.sec == "groups") & (F.act == "drop_sel")))
async def cb_v2_group_drop_selected(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "groups"):
        return
    card_id = callback_data.tid
    group_id = int(callback_data.ext) if callback_data.ext.isdigit() else 0
    async with get_session() as session:
        card = await session.get(Card, card_id)
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
        logger.warning("Failed dropping selected card %s to group %s: %s", card.id, group.telegram_chat_id, exc)
        await callback.answer("❌ Guruhga yuborib bo‘lmadi.", show_alert=True)
        return

    await callback.answer(f"✅ {card.character_name} → {group.title} guruhiga tashlandi!", show_alert=True)
    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Guruhga qaytish", callback_data=AdminCb(owner_id=callback.from_user.id, section="groups", action="group_view", target_id=group.id).pack())]]
    )
    await edit_or_answer(
        callback,
        f"✅ <b>DROP YUBORILDI!</b>\n\n🎴 Karta: <b>{esc(card.character_name)}</b>\n👥 Guruh: <b>{esc(group.title)}</b>",
        reply_markup=back_kb,
    )


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


# ===========================================================================
# 4. FORCED CHANNELS & GROUPS & LINKS (sec="channels")
# ===========================================================================
async def _render_channels_menu(target, owner_id: int) -> None:
    async with get_session() as session:
        channels = await CommunityService.list_channels(session)
    lines = ["📢 <b>MAJBURIY OBUNA KANALLARI / GURUHLAR / LINKLAR</b>\n"]
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="➕ Kanal / Guruh / Link qo‘shish", callback_data=AdmV2Cb(owner_id=owner_id, sec="channels", act="add").pack()))
    mode_lbl = {"daily": "📅 Kunlik", "subscribers": "👥 Obunachi", "vip": "💎 VIP"}
    for ch in channels:
        st = "🟢" if ch.is_active else "🔴"
        lim = f" · Limit: {ch.target_subscribers}" if ch.target_subscribers else ""
        exp = f" · {fmt_tashkent(ch.expires_at, '%d.%m %H:%M')} gacha" if ch.expires_at else ""
        lines.append(f"{st} <b>{esc(ch.title)}</b> [{mode_lbl.get(ch.sub_mode, ch.sub_mode)}]{lim}{exp}")
        b.row(
            InlineKeyboardButton(text=f"{st} {ch.title[:24]}", callback_data=AdmV2Cb(owner_id=owner_id, sec="channels", act="tog", tid=ch.id).pack()),
            InlineKeyboardButton(text="🗑", callback_data=AdmV2Cb(owner_id=owner_id, sec="channels", act="del", tid=ch.id).pack()),
        )
    if not channels:
        lines.append("📭 Majburiy obunalar yo‘q.")
    b.row(_back_home_btn(owner_id))
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


def _channel_mode_kb(owner_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="📅 Kunlik (vaqtinchalik)", callback_data=AdmV2Cb(owner_id=owner_id, sec="channels", act="mode", ext="daily").pack()))
    b.row(InlineKeyboardButton(text="👥 Obunachi soni (limitga qadar)", callback_data=AdmV2Cb(owner_id=owner_id, sec="channels", act="mode", ext="subscribers").pack()))
    b.row(InlineKeyboardButton(text="💎 VIP / Doimiy (cheksiz)", callback_data=AdmV2Cb(owner_id=owner_id, sec="channels", act="mode", ext="vip").pack()))
    b.row(InlineKeyboardButton(text="❌ Bekor", callback_data=AdmV2Cb(owner_id=owner_id, sec="channels", act="v").pack()))
    return b.as_markup()


@router.callback_query(AdmV2Cb.filter(F.sec == "channels"))
async def cb_v2_channels(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "channels"):
        return
    act = callback_data.act
    owner_id = callback.from_user.id

    if act == "add":
        await state.set_state(AdminV2States.waiting_channel_link)
        await state.update_data(owner_tg_id=owner_id)
        await edit_or_answer(
            callback,
            "📢 <b>KANAL / GURUH / HAVOLA QO‘SHISH</b>\n\n"
            "Quyidagilardan birini yuboring:\n"
            "• Kanal yoki guruh <code>@username</code> (masalan <code>@anime_uz</code>)\n"
            "• Link (<code>https://t.me/...</code> yoki Instagram/YouTube havolasi)\n"
            "• Chat ID (<code>-100...</code>) yoki kanaldan biror postni forward qiling!\n\n"
            "<i>Telegram kanallar uchun bot o‘sha kanalda admin bo‘lishi kerak.</i>",
            reply_markup=_cancel_kb(owner_id, "channels", "v"),
        )
        return

    if act == "mode":
        mode = callback_data.ext or "vip"
        await state.update_data(ch_mode=mode)
        if mode == "daily":
            await state.set_state(AdminV2States.waiting_channel_days)
            b = InlineKeyboardBuilder()
            for d in (1, 3, 7, 14, 30):
                b.add(InlineKeyboardButton(text=f"{d} kun", callback_data=AdmV2Cb(owner_id=owner_id, sec="channels", act="days", tid=d).pack()))
            b.adjust(3)
            b.row(InlineKeyboardButton(text="❌ Bekor", callback_data=AdmV2Cb(owner_id=owner_id, sec="channels", act="v").pack()))
            await edit_or_answer(callback, "📅 Necha kun majburiy tursin? Tugmadan tanlang yoki kun sonini yozing:", reply_markup=b.as_markup())
            return
        if mode == "subscribers":
            await state.set_state(AdminV2States.waiting_channel_subs)
            b = InlineKeyboardBuilder()
            for cnt in (500, 1000, 2000, 5000):
                b.add(InlineKeyboardButton(text=f"{cnt:,}", callback_data=AdmV2Cb(owner_id=owner_id, sec="channels", act="subs", tid=cnt).pack()))
            b.adjust(2)
            b.row(InlineKeyboardButton(text="❌ Bekor", callback_data=AdmV2Cb(owner_id=owner_id, sec="channels", act="v").pack()))
            await edit_or_answer(callback, "👥 Necha obunachiga yetguncha tursin? Tugmadan tanlang yoki son yozing:", reply_markup=b.as_markup())
            return

        # VIP (doimiy)
        data = await state.get_data()
        await state.clear()
        async with get_session() as session:
            await CommunityService.add_channel(
                session,
                chat_id=int(data["ch_id"]),
                title=data["ch_title"],
                username=data.get("ch_uname"),
                invite_link=data.get("ch_link"),
                added_by_tg_id=owner_id,
                channel_type=data.get("ch_type", "telegram"),
                sub_mode="vip",
            )
            await session.commit()
        await callback.answer("✅ VIP/Doimiy kanal qo‘shildi!", show_alert=True)
        await _render_channels_menu(callback, owner_id)
        return

    if act == "days":
        data = await state.get_data()
        await state.clear()
        exp = utcnow() + timedelta(days=max(1, callback_data.tid))
        async with get_session() as session:
            await CommunityService.add_channel(
                session,
                chat_id=int(data["ch_id"]),
                title=data["ch_title"],
                username=data.get("ch_uname"),
                invite_link=data.get("ch_link"),
                added_by_tg_id=owner_id,
                channel_type=data.get("ch_type", "telegram"),
                sub_mode="daily",
                expires_at=exp,
            )
            await session.commit()
        await callback.answer(f"✅ {callback_data.tid} kunlik kanal qo‘shildi!", show_alert=True)
        await _render_channels_menu(callback, owner_id)
        return

    if act == "subs":
        data = await state.get_data()
        await state.clear()
        async with get_session() as session:
            await CommunityService.add_channel(
                session,
                chat_id=int(data["ch_id"]),
                title=data["ch_title"],
                username=data.get("ch_uname"),
                invite_link=data.get("ch_link"),
                added_by_tg_id=owner_id,
                target_subscribers=max(1, callback_data.tid),
                channel_type=data.get("ch_type", "telegram"),
                sub_mode="subscribers",
            )
            await session.commit()
        await callback.answer(f"✅ {callback_data.tid:,} obunachigacha qo‘shildi!", show_alert=True)
        await _render_channels_menu(callback, owner_id)
        return

    async with get_session() as session:
        if act == "tog":
            await CommunityService.toggle_channel(session, callback_data.tid)
            await session.commit()
        elif act == "del":
            await CommunityService.delete_channel(session, callback_data.tid)
            await session.commit()
    await state.clear()
    await _render_channels_menu(callback, owner_id)


@router.message(AdminV2States.waiting_channel_link, NotCommandFilter())
async def fsm_v2_channel_link(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return

    # 1. Forward qilingan xabar bo'lsa
    f_chat = getattr(message, "forward_from_chat", None)
    if f_chat is not None:
        uname = getattr(f_chat, "username", None)
        ch_type = "group" if getattr(f_chat, "type", "") in ("group", "supergroup") else "telegram"
        link = f"https://t.me/{uname}" if uname else None
        await state.update_data(ch_id=f_chat.id, ch_title=f_chat.title or "Kanal", ch_uname=uname, ch_link=link, ch_type=ch_type)
        await state.set_state(None)
        await message.answer(
            f"✅ Topildi: <b>{esc(f_chat.title or 'Kanal')}</b> (<code>{f_chat.id}</code>)\n\nEndi obuna rejimini tanlang:",
            reply_markup=_channel_mode_kb(message.from_user.id),
        )
        return

    raw = (message.text or "").strip()
    if not raw:
        await message.answer("⚠️ @username, link yoki Chat ID yuboring.")
        return

    # 2. Tashqi link (Instagram/YouTube/Sayt)
    if raw.startswith("http") and "t.me/" not in raw.lower() and "telegram.me/" not in raw.lower():
        pseudo_id = -abs(zlib.crc32(raw.encode()) + 10_000_000_000)
        await state.update_data(ch_id=pseudo_id, ch_uname=None, ch_link=raw[:250], ch_type="external")
        await state.set_state(AdminV2States.waiting_channel_title)
        await message.answer("🔗 Tashqi havola aniqlandi! Tugmada ko‘rinadigan <b>nomini</b> yozing (masalan: <code>Instagram</code>):")
        return

    # 3. Telegram link/username/ID
    ref = raw
    if "t.me/" in raw or "telegram.me/" in raw:
        tail = raw.rstrip("/").split("/")[-1]
        if tail.startswith("+") or tail.startswith("joinchat"):
            pseudo_id = -abs(zlib.crc32(raw.encode()) + 10_000_000_000)
            await state.update_data(ch_id=pseudo_id, ch_uname=None, ch_link=raw[:250], ch_type="external")
            await state.set_state(AdminV2States.waiting_channel_title)
            await message.answer(
                "🔐 Yopiq taklif linki! Agar bot obunani ham tekshirishini istasangiz, kanal postini forward qiling yoki nomini yozing:"
            )
            return
        ref = "@" + tail.lstrip("@")

    chat_lookup = int(ref) if ref.lstrip("-").isdigit() else (ref if ref.startswith("@") else "@" + ref)
    try:
        chat = await message.bot.get_chat(chat_lookup)
        uname = getattr(chat, "username", None)
        link = f"https://t.me/{uname}" if uname else getattr(chat, "invite_link", None)
        ch_type = "group" if getattr(chat, "type", "") in ("group", "supergroup") else "telegram"
        try:
            me = await message.bot.get_me()
            member = await message.bot.get_chat_member(chat.id, me.id)
            if getattr(member, "status", "") not in ("administrator", "creator"):
                await message.answer("⚠️ Eslatma: Bot bu kanalda admin emas — obuna tekshiruvi ishlashi uchun botni admin qiling.")
        except Exception:
            pass
        await state.update_data(ch_id=chat.id, ch_title=chat.title or str(chat.id), ch_uname=uname, ch_link=link, ch_type=ch_type)
        await state.set_state(None)
        await message.answer(
            f"✅ <b>{esc(chat.title or str(chat.id))}</b> aniqlandi!\n\nEndi obuna rejimini tanlang:",
            reply_markup=_channel_mode_kb(message.from_user.id),
        )
    except Exception as exc:
        await message.answer(
            f"❌ Kanal topilmadi yoki botda ruxsat yo‘q: <code>{esc(str(exc)[:100])}</code>\n"
            "Botni kanalga admin qiling yoki kanaldan post forward qilib tashlang."
        )


@router.message(AdminV2States.waiting_channel_title, NotCommandFilter())
async def fsm_v2_channel_title(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    title = (message.text or "Link").strip()[:200]
    await state.update_data(ch_title=title)
    await state.set_state(None)
    await message.answer(f"🏷 Nomi: <b>{esc(title)}</b>\n\nEndi rejimni tanlang:", reply_markup=_channel_mode_kb(message.from_user.id))


@router.message(AdminV2States.waiting_channel_days, NotCommandFilter())
async def fsm_v2_channel_days(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) < 1:
        await message.answer("⚠️ Kamida 1 kun yozing.")
        return
    days = int(raw)
    await state.clear()
    async with get_session() as session:
        await CommunityService.add_channel(
            session,
            chat_id=int(data["ch_id"]),
            title=data["ch_title"],
            username=data.get("ch_uname"),
            invite_link=data.get("ch_link"),
            added_by_tg_id=message.from_user.id,
            channel_type=data.get("ch_type", "telegram"),
            sub_mode="daily",
            expires_at=utcnow() + timedelta(days=days),
        )
        await session.commit()
    await message.answer(f"✅ Kanal <b>{days} kun</b>ga qo‘shildi!")
    await _render_channels_menu(message, message.from_user.id)


@router.message(AdminV2States.waiting_channel_subs, NotCommandFilter())
async def fsm_v2_channel_subs(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "")
    if not raw.isdigit() or int(raw) < 1:
        await message.answer("⚠️ Musbat obunachi sonini yozing.")
        return
    subs = int(raw)
    await state.clear()
    async with get_session() as session:
        await CommunityService.add_channel(
            session,
            chat_id=int(data["ch_id"]),
            title=data["ch_title"],
            username=data.get("ch_uname"),
            invite_link=data.get("ch_link"),
            added_by_tg_id=message.from_user.id,
            target_subscribers=subs,
            channel_type=data.get("ch_type", "telegram"),
            sub_mode="subscribers",
        )
        await session.commit()
    await message.answer(f"✅ Kanal <b>{subs:,}</b> obunachigacha qo‘shildi!")
    await _render_channels_menu(message, message.from_user.id)


# ===========================================================================
# 5. RANKS MANAGER (sec="ranks", Zero-pipe step-by-step + Normalize)
# ===========================================================================
async def _show_ranks_home(target, owner_id: int) -> None:
    async with get_session() as session:
        ranks = await GameplayService.get_rank_definitions(session)
    total_p = sum(int(r.get("drop_probability", 0)) for r in ranks)
    warn = " ✅" if total_p == 100 else " ⚠️ (100% bo‘lishi kerak!)"
    txt = f"⭐ <b>RANKLAR TIZIMI</b>\nJami ehtimollik: <b>{total_p}%</b>{warn}\n\nTahrirlash uchun rank ustiga bosing:"
    await edit_or_answer(target, txt, reply_markup=admin_ranks_list_kb(owner_id, ranks, total_p))


async def _show_rank_detail(target, owner_id: int, rank_idx: int) -> None:
    async with get_session() as session:
        ranks = await GameplayService.get_rank_definitions(session)
    if not (0 <= rank_idx < len(ranks)):
        await _show_ranks_home(target, owner_id)
        return
    r = ranks[rank_idx]
    val = int(r.get("base_value", 0) or 0)
    txt = (
        f"{r.get('emoji', '⭐')} <b>RANK: {esc(r['name'])}</b>\n\n"
        f"• Emoji: {r.get('emoji', '⭐')}\n"
        f"• Tushish ehtimoli: <b>{r.get('drop_probability', 0)}%</b>\n"
        f"• Bir marta yaratilganda guruhga tushish soni: <b>{r.get('drop_limit', 1)} ta</b>\n"
        f"• Karta narxi (botga sotish & daraja asosi): <b>{val:,} 🪙</b>"
    )
    await edit_or_answer(target, txt, reply_markup=admin_rank_detail_kb(owner_id, rank_idx))


@router.callback_query(AdmV2Cb.filter(F.sec == "ranks"))
async def cb_v2_ranks(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "ranks"):
        return
    act = callback_data.act
    owner_id = callback.from_user.id

    if act == "v":
        await state.clear()
        await _show_ranks_home(callback, owner_id)
        return

    if act == "det":
        await state.clear()
        await _show_rank_detail(callback, owner_id, callback_data.tid)
        return

    if act == "norm":
        async with get_session() as session:
            await GameplayService.normalize_rank_probabilities(session)
            await session.commit()
        await callback.answer("⚖️ Ehtimolliklar 100% ga moslandi!", show_alert=True)
        await _show_ranks_home(callback, owner_id)
        return

    if act == "ef":
        prompts = {
            "name": "✏️ Yangi rank nomini yozing:",
            "emoji": "😀 Yangi emoji yuboring (masalan: 🌟):",
            "prob": "📊 Yangi drop ehtimolligini yozing (0–100):",
            "drops": "🔁 Guruhga nechta tushish sonini (drop limit) yozing:",
            "value": "💎 Yangi karta narxini (coin) kiriting (masalan: <code>10000</code>):",
        }
        await state.set_state(AdminV2States.waiting_rank_field)
        await state.update_data(owner_tg_id=owner_id, rank_idx=callback_data.tid, rank_field=callback_data.ext)
        await edit_or_answer(callback, prompts.get(callback_data.ext, "Yangi qiymat:"), reply_markup=_cancel_kb(owner_id, "ranks", "v"))
        return

    if act == "del":
        async with get_session() as session:
            ranks = await GameplayService.get_rank_definitions(session)
            if len(ranks) <= 1 or not (0 <= callback_data.tid < len(ranks)):
                await callback.answer("⚠️ Kamida 1 ta rank qolishi shart.", show_alert=True)
                return
            await GameplayService.delete_rank(session, str(ranks[callback_data.tid]["name"]))
            await session.commit()
        await callback.answer("🗑 Rank o‘chirildi!", show_alert=True)
        await _show_ranks_home(callback, owner_id)
        return

    if act == "add":
        await state.set_state(AdminV2States.waiting_rank_add_name)
        await state.update_data(owner_tg_id=owner_id)
        await edit_or_answer(callback, "➕ <b>YANGI RANK (1/4)</b>\n\nRank nomini yozing (masalan: <code>Celestial</code>):", reply_markup=_cancel_kb(owner_id, "ranks", "v"))


@router.message(AdminV2States.waiting_rank_field, NotCommandFilter())
async def fsm_v2_rank_field(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    idx = int(data["rank_idx"])
    field = data["rank_field"]
    raw = (message.text or "").strip()
    try:
        async with get_session() as session:
            ranks = await GameplayService.get_rank_definitions(session)
            if not (0 <= idx < len(ranks)):
                await state.clear()
                await message.answer("❌ Rank topilmadi.")
                return
            r_name = str(ranks[idx]["name"])
            if field == "name":
                await GameplayService.update_single_rank_attr(session, r_name, new_name=raw)
            elif field == "emoji":
                await GameplayService.update_single_rank_attr(session, r_name, emoji=raw)
            elif field == "prob":
                if not raw.isdigit():
                    await message.answer("⚠️ 0 dan 100 gacha son kiriting.")
                    return
                await GameplayService.update_single_rank_attr(session, r_name, drop_probability=int(raw))
            elif field == "drops":
                if not raw.isdigit() or int(raw) < 1:
                    await message.answer("⚠️ Kamida 1 butun son kiriting.")
                    return
                await GameplayService.update_single_rank_attr(session, r_name, drop_limit=int(raw))
            elif field == "value":
                cleaned = raw.replace(",", "").replace(" ", "")
                if not cleaned.isdigit() or not (1 <= int(cleaned) <= 100_000_000):
                    await message.answer("⚠️ 1 dan 100,000,000 gacha butun coin narxi kiriting.")
                    return
                await GameplayService.update_single_rank_attr(session, r_name, base_value=int(cleaned))
            await session.commit()
    except ValueError as exc:
        await message.answer(f"⚠️ {esc(str(exc))}")
        return
    await state.clear()
    await _show_rank_detail(message, message.from_user.id, idx)


@router.message(AdminV2States.waiting_rank_add_name, NotCommandFilter())
async def fsm_v2_rank_add_name(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id:
        return
    name = (message.text or "").strip()[:32]
    if not name:
        await message.answer("⚠️ Rank nomini yozing.")
        return
    await state.update_data(new_rank_name=name)
    await state.set_state(AdminV2States.waiting_rank_add_emoji)
    await message.answer("2/4: Rank <b>emojisi</b>ni yuboring (masalan: 🌟):")


@router.message(AdminV2States.waiting_rank_add_emoji, NotCommandFilter())
async def fsm_v2_rank_add_emoji(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id:
        return
    emoji = (message.text or "⭐").strip()[:8]
    await state.update_data(new_rank_emoji=emoji)
    await state.set_state(AdminV2States.waiting_rank_add_prob)
    await message.answer("3/4: Tushish ehtimolini (%) yozing (masalan: <code>5</code>):")


@router.message(AdminV2States.waiting_rank_add_prob, NotCommandFilter())
async def fsm_v2_rank_add_prob(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("⚠️ Son kiriting.")
        return
    await state.update_data(new_rank_prob=int(raw))
    await state.set_state(AdminV2States.waiting_rank_add_drops)
    await message.answer("4/4: Guruhga necha marta tashlanish sonini (drop limit) yozing (masalan: <code>1</code>):")


@router.message(AdminV2States.waiting_rank_add_drops, NotCommandFilter())
async def fsm_v2_rank_add_drops(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) < 1:
        await message.answer("⚠️ Kamida 1 son kiriting.")
        return
    await state.clear()
    try:
        async with get_session() as session:
            await GameplayService.add_rank_flexible(
                session,
                name=data["new_rank_name"],
                emoji=data["new_rank_emoji"],
                drop_probability=int(data["new_rank_prob"]),
                drop_limit=int(raw),
            )
            await session.commit()
    except ValueError as exc:
        await message.answer(f"⚠️ {esc(str(exc))}")
        return
    await _show_ranks_home(message, message.from_user.id)


# ===========================================================================
# 6. QUIZ MANAGER (Zero-pipe Builder + Reuse/Clone + Schedule + Group/All Target)
# ===========================================================================
async def _dispatch_quiz_announcement(bot, session, quiz: Quiz) -> None:
    qs_res = await GameplayService.get_quiz_with_questions(session, quiz.id)
    first_q = qs_res[1][0] if qs_res and qs_res[1] else None
    text = (
        f"🧠 <b>YANGI VIKTORINA BOSHLANDI: {esc(quiz.title)}</b>\n\n"
        f"🎁 Har bir to‘g‘ri javob uchun: <b>{quiz.reward_coins:,} 🪙</b>\n"
        + (f"❓ <b>1-savol:</b> {esc(first_q.question_text)}\n\n" if first_q else "")
        + f"👇 Ishtirok etish uchun /quiz yozing (yoki ushbu xabarga javob yozing)!\n<code>Quiz #{quiz.id}</code>"
    )
    chat_ids: List[int] = []
    if quiz.target_scope == "group" and quiz.target_group_id:
        grp = await session.get(Group, quiz.target_group_id)
        if grp and grp.is_active:
            chat_ids.append(grp.telegram_chat_id)
    else:
        g_ids = (await session.execute(select(Group.telegram_chat_id).where(Group.is_active.is_(True), Group.is_banned.is_(False)))).scalars().all()
        u_ids = (await session.execute(select(User.telegram_id).where(User.is_banned.is_(False)))).scalars().all()
        chat_ids.extend(int(x) for x in g_ids)
        chat_ids.extend(int(x) for x in u_ids)

    sem = asyncio.Semaphore(6)

    async def _send(cid: int) -> None:
        async with sem:
            try:
                await bot.send_message(cid, text)
            except TelegramRetryAfter as exc:
                await asyncio.sleep(float(exc.retry_after) + 0.5)
            except Exception:
                pass

    if chat_ids:
        await asyncio.gather(*[_send(c) for c in chat_ids])


async def _show_quiz_home(target, owner_id: int) -> None:
    async with get_session() as session:
        quizzes = await GameplayService.list_all_quizzes(session, limit=15)
    lines = ["🧠 <b>QUIZ BOSHQARUVI</b>\n"]
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="➕ Yangi Quiz yaratish", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="new").pack()))
    for q in quizzes:
        st = "⏰" if q.status == "scheduled" else ("🟢" if q.is_active else "⚪")
        lines.append(f"{st} <b>#{q.id} {esc(q.title)}</b> — {q.question_count} savol · {q.reward_coins:,} 🪙")
        b.row(InlineKeyboardButton(text=f"{st} #{q.id} {q.title[:26]}", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="det", tid=q.id).pack()))
    if not quizzes:
        lines.append("📭 Hozircha quizlar yo‘q.")
    b.row(_back_home_btn(owner_id))
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


@router.callback_query(AdmV2Cb.filter(F.sec == "quiz"))
async def cb_v2_quiz(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "quiz"):
        return
    act = callback_data.act
    owner_id = callback.from_user.id

    if act == "v":
        await state.clear()
        await _show_quiz_home(callback, owner_id)
        return

    if act == "new":
        await state.set_state(AdminV2States.waiting_quiz_title)
        await state.update_data(owner_tg_id=owner_id, quiz_questions=[])
        await edit_or_answer(callback, "🧠 <b>YANGI QUIZ (1/4)</b>\n\nQuiz nomini yozing (masalan: <code>Naruto Viktorinasi</code>):", reply_markup=_cancel_kb(owner_id, "quiz", "v"))
        return

    if act == "coins_p":
        await state.update_data(quiz_coins=callback_data.tid)
        await state.set_state(AdminV2States.waiting_quiz_q_text)
        await edit_or_answer(callback, f"🪙 Mukofot: <b>{callback_data.tid:,}</b> coin.\n\nEndi <b>1-savol matnini</b> yozing:", reply_markup=_cancel_kb(owner_id, "quiz", "v"))
        return

    if act == "q_ans":
        data = await state.get_data()
        qs = list(data.get("quiz_questions") or [])
        opts = list(data.get("cur_q_opts") or [])
        q_text = data.get("cur_q_text", "")
        cidx = max(0, min(len(opts) - 1, callback_data.tid))
        qs.append({"q": q_text, "opts": opts, "cidx": cidx, "acc": [opts[cidx]] if opts else []})
        await state.update_data(quiz_questions=qs)
        await state.set_state(None)
        b = InlineKeyboardBuilder()
        b.row(
            InlineKeyboardButton(text="➕ Yana savol qo‘shish", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="q_more").pack()),
            InlineKeyboardButton(text="✅ Savollar tayyor", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="q_done").pack()),
        )
        await edit_or_answer(callback, f"✅ Savol #{len(qs)} saqlandi!\nYana savol qo‘shasizmi?", reply_markup=b.as_markup())
        return

    if act == "q_more":
        data = await state.get_data()
        await state.set_state(AdminV2States.waiting_quiz_q_text)
        await edit_or_answer(callback, f"❓ <b>{len(data.get('quiz_questions', [])) + 1}-savol matnini</b> yozing:", reply_markup=_cancel_kb(owner_id, "quiz", "v"))
        return

    if act == "q_done":
        b = InlineKeyboardBuilder()
        b.row(InlineKeyboardButton(text="🌍 Barcha guruh & userlarga", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="scope_all").pack()))
        b.row(InlineKeyboardButton(text="👥 Aniq bitta guruhga", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="scope_grp").pack()))
        await edit_or_answer(callback, "📢 <b>QUIZ QAYERGA YUBORILSIN?</b>", reply_markup=b.as_markup())
        return

    if act == "scope_all":
        await state.update_data(quiz_scope="all", quiz_grp_id=None)
        b = InlineKeyboardBuilder()
        b.row(
            InlineKeyboardButton(text="⚡ Hozir boshlash", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="when_now").pack()),
            InlineKeyboardButton(text="⏰ Vaqtga belgilash", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="when_sched").pack()),
        )
        await edit_or_answer(callback, "⏰ <b>QACHON BOSHLANSIN?</b>", reply_markup=b.as_markup())
        return

    if act == "scope_grp":
        async with get_session() as session:
            groups = list((await session.execute(select(Group).where(Group.is_active.is_(True)).order_by(Group.id.desc()).limit(20))).scalars().all())
        b = InlineKeyboardBuilder()
        for g in groups:
            b.row(InlineKeyboardButton(text=f"👥 {g.title[:36]}", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="set_grp", tid=g.id).pack()))
        b.row(InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="q_done").pack()))
        await edit_or_answer(callback, "👥 Qaysi guruhni tanlaysiz?", reply_markup=b.as_markup())
        return

    if act == "set_grp":
        await state.update_data(quiz_scope="group", quiz_grp_id=callback_data.tid)
        b = InlineKeyboardBuilder()
        b.row(
            InlineKeyboardButton(text="⚡ Hozir boshlash", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="when_now").pack()),
            InlineKeyboardButton(text="⏰ Vaqtga belgilash", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="when_sched").pack()),
        )
        await edit_or_answer(callback, "⏰ <b>QACHON BOSHLANSIN?</b>", reply_markup=b.as_markup())
        return

    if act == "when_sched":
        await state.set_state(AdminV2States.waiting_quiz_sched_dt)
        await edit_or_answer(callback, "⏰ Boshlanish vaqtini yozing (<code>2026-12-01 18:00</code> yoki <code>01.12.2026 18:00</code>):")
        return

    if act == "when_now":
        data = await state.get_data()
        await state.clear()
        async with get_session() as session:
            q = await GameplayService.create_quiz(
                session,
                title=data.get("quiz_title", "Quiz"),
                reward_coins=int(data.get("quiz_coins", 300)),
                admin_tg_id=owner_id,
                target_scope=data.get("quiz_scope", "all"),
                target_group_id=data.get("quiz_grp_id"),
                status="active",
            )
            for item in data.get("quiz_questions") or []:
                await GameplayService.add_quiz_question(
                    session, q.id, item["q"], item["opts"], item["cidx"], accepted_answers=item.get("acc")
                )
            await session.commit()
            await _dispatch_quiz_announcement(callback.bot, session, q)
        await callback.answer("🎉 Quiz yaratildi va yuborildi!", show_alert=True)
        await _show_quiz_home(callback, owner_id)
        return

    if act == "det":
        async with get_session() as session:
            res = await GameplayService.get_quiz_with_questions(session, callback_data.tid)
        if res is None:
            await callback.answer("Quiz topilmadi.", show_alert=True)
            return
        q, qs = res
        lines = [
            f"🧠 <b>QUIZ #{q.id}: {esc(q.title)}</b>\n",
            f"Holat: <b>{esc(q.status)}</b> ({'🟢 Faol' if q.is_active else '🔴 Yopiq'})",
            f"🪙 Mukofot: <b>{q.reward_coins:,}</b> coin · ⚡ {q.reward_xp} XP",
            f"❓ Savollar: <b>{len(qs)}</b> ta",
        ]
        for idx, qq in enumerate(qs[:10], 1):
            lines.append(f"{idx}. {esc(qq.question_text[:60])}")
        b = InlineKeyboardBuilder()
        b.row(
            InlineKeyboardButton(text="🔄 Yoqish/O‘chirish", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="tog", tid=q.id).pack()),
            InlineKeyboardButton(text="📋 Qayta o‘tkazish", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="clone", tid=q.id).pack()),
        )
        b.row(
            InlineKeyboardButton(text="🗑 O‘chirish", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="del", tid=q.id).pack()),
            InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="v").pack()),
        )
        await edit_or_answer(callback, "\n".join(lines), reply_markup=b.as_markup())
        return

    if act == "clone":
        await state.set_state(AdminV2States.waiting_quiz_clone_coins)
        await state.update_data(owner_tg_id=owner_id, clone_qid=callback_data.tid)
        b = InlineKeyboardBuilder()
        b.row(InlineKeyboardButton(text="⏭ Eski narx bilan boshlash", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="clone_go", tid=callback_data.tid).pack()))
        await edit_or_answer(callback, "📋 <b>QUIZNI QAYTA O‘TKAZISH</b>\n\nYangi coin mukofotini yozing yoki eski narx bilan boshlang:", reply_markup=b.as_markup())
        return

    if act == "clone_go":
        await state.clear()
        async with get_session() as session:
            nq = await GameplayService.clone_quiz(session, callback_data.tid, owner_id)
            if nq:
                await session.commit()
                await _dispatch_quiz_announcement(callback.bot, session, nq)
        await callback.answer("🎉 Quiz qayta boshlandi!", show_alert=True)
        await _show_quiz_home(callback, owner_id)
        return

    async with get_session() as session:
        q = await session.get(Quiz, callback_data.tid)
        if q:
            if act == "tog":
                q.is_active = not q.is_active
                q.status = "active" if q.is_active else "ended"
            elif act == "del":
                await session.delete(q)
            await session.commit()
    await _show_quiz_home(callback, owner_id)


@router.message(AdminV2States.waiting_quiz_title, NotCommandFilter())
async def fsm_v2_quiz_title(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id:
        return
    title = (message.text or "").strip()[:160]
    if not title:
        await message.answer("⚠️ Quiz nomini yozing.")
        return
    await state.update_data(quiz_title=title)
    await state.set_state(AdminV2States.waiting_quiz_coins)
    b = InlineKeyboardBuilder()
    for c in (200, 500, 1000, 2500):
        b.add(InlineKeyboardButton(text=f"🪙 {c:,}", callback_data=AdmV2Cb(owner_id=message.from_user.id, sec="quiz", act="coins_p", tid=c).pack()))
    b.adjust(2)
    await message.answer("2/4: To‘g‘ri javob uchun <b>coin mukofotini</b> tanlang yoki son yozing:", reply_markup=b.as_markup())


@router.message(AdminV2States.waiting_quiz_coins, NotCommandFilter())
async def fsm_v2_quiz_coins(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "")
    if not raw.isdigit():
        await message.answer("⚠️ Musbat son kiriting.")
        return
    await state.update_data(quiz_coins=int(raw))
    await state.set_state(AdminV2States.waiting_quiz_q_text)
    await message.answer("3/4: <b>1-savol matnini</b> yozing:")


@router.message(AdminV2States.waiting_quiz_q_text, NotCommandFilter())
async def fsm_v2_quiz_q_text(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id:
        return
    qtxt = (message.text or "").strip()
    if not qtxt:
        await message.answer("⚠️ Savol matnini yozing.")
        return
    await state.update_data(cur_q_text=qtxt)
    await state.set_state(AdminV2States.waiting_quiz_q_opts)
    await message.answer(
        "Javob variantlarini <b>vergul</b> yoki <b>yangi qator</b> bilan yozing (masalan: <code>Naruto, Sasuke, Sakura, Kakashi</code>).\n"
        "<i>Agar bitta to‘g‘ri javob yozsangiz, ochiq javobli savol bo‘ladi!</i>"
    )


@router.message(AdminV2States.waiting_quiz_q_opts, NotCommandFilter())
async def fsm_v2_quiz_q_opts(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip()
    opts = [x.strip() for x in re.split(r"[\n,;]+", raw) if x.strip()][:6]
    if not opts:
        await message.answer("⚠️ Kamida 1 ta javob yozing.")
        return
    if len(opts) == 1:
        qs = list(data.get("quiz_questions") or [])
        qs.append({"q": data.get("cur_q_text", ""), "opts": [opts[0]], "cidx": 0, "acc": [opts[0]]})
        await state.update_data(quiz_questions=qs)
        await state.set_state(None)
        b = InlineKeyboardBuilder()
        b.row(
            InlineKeyboardButton(text="➕ Yana savol qo‘shish", callback_data=AdmV2Cb(owner_id=message.from_user.id, sec="quiz", act="q_more").pack()),
            InlineKeyboardButton(text="✅ Savollar tayyor", callback_data=AdmV2Cb(owner_id=message.from_user.id, sec="quiz", act="q_done").pack()),
        )
        await message.answer(f"✅ Savol #{len(qs)} saqlandi (to‘g‘ri javob: <code>{esc(opts[0])}</code>)!", reply_markup=b.as_markup())
        return

    await state.update_data(cur_q_opts=opts)
    await state.set_state(None)
    b = InlineKeyboardBuilder()
    for idx, opt in enumerate(opts):
        b.row(InlineKeyboardButton(text=f"✅ {opt[:40]}", callback_data=AdmV2Cb(owner_id=message.from_user.id, sec="quiz", act="q_ans", tid=idx).pack()))
    await message.answer("👇 Qaysi variant <b>TO‘G‘RI JAVOB</b>?", reply_markup=b.as_markup())


@router.message(AdminV2States.waiting_quiz_sched_dt, NotCommandFilter())
async def fsm_v2_quiz_sched_dt(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    dt_utc = _parse_local_dt(message.text or "")
    if dt_utc is None:
        await message.answer("⚠️ Sana formati noto‘g‘ri. Misol: <code>2026-12-01 18:00</code>")
        return
    await state.clear()
    async with get_session() as session:
        q = await GameplayService.create_quiz(
            session,
            title=data.get("quiz_title", "Quiz"),
            reward_coins=int(data.get("quiz_coins", 300)),
            admin_tg_id=message.from_user.id,
            target_scope=data.get("quiz_scope", "all"),
            target_group_id=data.get("quiz_grp_id"),
            scheduled_at=dt_utc,
            status="scheduled",
        )
        for item in data.get("quiz_questions") or []:
            await GameplayService.add_quiz_question(session, q.id, item["q"], item["opts"], item["cidx"], accepted_answers=item.get("acc"))
        await session.commit()
    await message.answer(f"⏰ Quiz #{q.id} belgilangan vaqtga rejalashtirildi!")
    await _show_quiz_home(message, message.from_user.id)


@router.message(AdminV2States.waiting_quiz_clone_coins, NotCommandFilter())
async def fsm_v2_quiz_clone_coins(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "")
    if not raw.isdigit():
        await message.answer("⚠️ Son kiriting.")
        return
    qid = int(data["clone_qid"])
    await state.clear()
    async with get_session() as session:
        nq = await GameplayService.clone_quiz(session, qid, message.from_user.id, new_reward_coins=int(raw))
        if nq:
            await session.commit()
            await _dispatch_quiz_announcement(message.bot, session, nq)
    await message.answer("🎉 Quiz yangi mukofot bilan qayta boshlandi!")
    await _show_quiz_home(message, message.from_user.id)


# ===========================================================================
# 7. GAMES & PACKS (sec="games", Zero-pipe per-field edit)
# ===========================================================================
_GAME_FIELD_LABELS = {
    "dice_jackpot_reward": "🎲 Tosh yutug‘i (coin)",
    "dice_daily_limit": "🎲 Tosh kunlik limiti",
    "dice_cooldown_minutes": "🎲 Tosh kutish vaqti (daqiqa, 0=yo‘q)",
    "coinflip_daily_limit": "🪙 Tanga tashlash kunlik limiti",
    "lucky_daily_limit": "🃏 Omadli karta kunlik limiti",
    "lucky_min_reward": "🃏 Omadli karta minimal yutuq (coin)",
    "lucky_max_reward": "🃏 Omadli karta maksimal yutuq (coin)",
    "slots_daily_limit": "🎰 Slot 777 kunlik limiti",
    "mines_daily_limit": "💣 Xazina ovi kunlik limiti",
    "wheel_daily_limit": "🎡 Omad g‘ildiragi kunlik limiti",
    "wheel_ticket_cost": "🎡 Omad g‘ildiragi chipta narxi (0=bepul)",
    "pack_daily_limit": "🎁 Paket (/pack) kunlik limiti",
}

_GAME_TOGGLE_KEYS = (
    "dice_enabled",
    "coinflip_enabled",
    "lucky_enabled",
    "slots_enabled",
    "mines_enabled",
    "wheel_enabled",
    "pack_enabled",
)

_GAME_ZERO_ALLOWED = ("dice_cooldown_minutes", "pack_daily_limit", "wheel_ticket_cost")


async def _show_games_home(target, owner_id: int) -> None:
    async with get_session() as session:
        cfg = await GameplayService.get_game_config(session)
    d_on = "🟢" if cfg.get("dice_enabled", True) else "🔴"
    f_on = "🟢" if cfg.get("coinflip_enabled", True) else "🔴"
    l_on = "🟢" if cfg.get("lucky_enabled", True) else "🔴"
    s_on = "🟢" if cfg.get("slots_enabled", True) else "🔴"
    m_on = "🟢" if cfg.get("mines_enabled", True) else "🔴"
    w_on = "🟢" if cfg.get("wheel_enabled", True) else "🔴"
    pack_lim = max(0, int(cfg.get("pack_daily_limit", 1)))
    p_on = "🟢" if (cfg.get("pack_enabled", True) and pack_lim > 0) else "🔴"
    txt = (
        "🎮 <b>MINI O‘YINLAR VA PAKETLAR SOZLAMASI</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"{s_on} <b>Slot 777:</b> kuniga {cfg.get('slots_daily_limit', 5)} ta (10x/4x/1.5x)\n"
        f"{m_on} <b>Xazina ovi:</b> kuniga {cfg.get('mines_daily_limit', 5)} ta (4.0x gacha)\n"
        f"{w_on} <b>Omad g‘ildiragi:</b> kuniga {cfg.get('wheel_daily_limit', 3)} ta · chipta {cfg.get('wheel_ticket_cost', 100)} 🪙\n"
        f"{d_on} <b>Tosh o‘yini:</b> kuniga {cfg.get('dice_daily_limit', 3)} ta · "
        f"yutuq {cfg.get('dice_jackpot_reward', 300)} 🪙 · kutish {cfg.get('dice_cooldown_minutes', 0)} daq.\n"
        f"{f_on} <b>Tanga tashlash:</b> kuniga {cfg.get('coinflip_daily_limit', 5)} ta (2x garov)\n"
        f"{l_on} <b>Omadli karta:</b> kuniga {cfg.get('lucky_daily_limit', 3)} ta "
        f"({cfg.get('lucky_min_reward', 60)}–{cfg.get('lucky_max_reward', 350)} 🪙)\n"
        f"{p_on} <b>Karta paketlari (/pack):</b> kuniga <b>{pack_lim}</b> marta\n"
        "   <i>(Dublikat karta tushsa 50% coin keshbek qaytadi)</i>"
    )
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text=f"{s_on} Slot 777", callback_data=AdmV2Cb(owner_id=owner_id, sec="games", act="tog", ext="slots_enabled").pack()),
        InlineKeyboardButton(text=f"{m_on} Xazina", callback_data=AdmV2Cb(owner_id=owner_id, sec="games", act="tog", ext="mines_enabled").pack()),
        InlineKeyboardButton(text=f"{w_on} G‘ildirak", callback_data=AdmV2Cb(owner_id=owner_id, sec="games", act="tog", ext="wheel_enabled").pack()),
    )
    b.row(
        InlineKeyboardButton(text=f"{d_on} Tosh", callback_data=AdmV2Cb(owner_id=owner_id, sec="games", act="tog", ext="dice_enabled").pack()),
        InlineKeyboardButton(text=f"{f_on} Tanga", callback_data=AdmV2Cb(owner_id=owner_id, sec="games", act="tog", ext="coinflip_enabled").pack()),
        InlineKeyboardButton(text=f"{l_on} Omadli", callback_data=AdmV2Cb(owner_id=owner_id, sec="games", act="tog", ext="lucky_enabled").pack()),
    )
    b.row(
        InlineKeyboardButton(text="🎰 Slot limiti", callback_data=AdmV2Cb(owner_id=owner_id, sec="games", act="ed", ext="slots_daily_limit").pack()),
        InlineKeyboardButton(text="💣 Xazina limiti", callback_data=AdmV2Cb(owner_id=owner_id, sec="games", act="ed", ext="mines_daily_limit").pack()),
    )
    b.row(
        InlineKeyboardButton(text="🎡 G‘ildirak limiti", callback_data=AdmV2Cb(owner_id=owner_id, sec="games", act="ed", ext="wheel_daily_limit").pack()),
        InlineKeyboardButton(text="🎡 Chipta narxi", callback_data=AdmV2Cb(owner_id=owner_id, sec="games", act="ed", ext="wheel_ticket_cost").pack()),
    )
    b.row(
        InlineKeyboardButton(text=f"{p_on} Paket holati", callback_data=AdmV2Cb(owner_id=owner_id, sec="games", act="tog", ext="pack_enabled").pack()),
        InlineKeyboardButton(text=f"🎁 Paket limiti ({pack_lim}/kun)", callback_data=AdmV2Cb(owner_id=owner_id, sec="games", act="ed", ext="pack_daily_limit").pack()),
    )
    b.row(
        InlineKeyboardButton(text="🎲 Tosh yutug‘i", callback_data=AdmV2Cb(owner_id=owner_id, sec="games", act="ed", ext="dice_jackpot_reward").pack()),
        InlineKeyboardButton(text="🎲 Tosh limiti", callback_data=AdmV2Cb(owner_id=owner_id, sec="games", act="ed", ext="dice_daily_limit").pack()),
    )
    b.row(
        InlineKeyboardButton(text="🪙 Tanga limiti", callback_data=AdmV2Cb(owner_id=owner_id, sec="games", act="ed", ext="coinflip_daily_limit").pack()),
        InlineKeyboardButton(text="🃏 Omadli limiti", callback_data=AdmV2Cb(owner_id=owner_id, sec="games", act="ed", ext="lucky_daily_limit").pack()),
    )
    b.row(
        InlineKeyboardButton(text="🃏 Min coin", callback_data=AdmV2Cb(owner_id=owner_id, sec="games", act="ed", ext="lucky_min_reward").pack()),
        InlineKeyboardButton(text="🃏 Max coin", callback_data=AdmV2Cb(owner_id=owner_id, sec="games", act="ed", ext="lucky_max_reward").pack()),
    )
    b.row(InlineKeyboardButton(text="⏳ Tosh kutish vaqti (daq.)", callback_data=AdmV2Cb(owner_id=owner_id, sec="games", act="ed", ext="dice_cooldown_minutes").pack()))
    b.row(_back_home_btn(owner_id))
    await edit_or_answer(target, txt, reply_markup=b.as_markup())


@router.callback_query(AdmV2Cb.filter(F.sec == "games"))
async def cb_v2_games(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "games"):
        return
    owner_id = callback.from_user.id
    if callback_data.act == "ed":
        field = callback_data.ext or "dice_daily_limit"
        label = _GAME_FIELD_LABELS.get(field, field)
        await state.set_state(AdminV2States.waiting_games_field)
        await state.update_data(owner_tg_id=owner_id, g_field=field)
        min_note = "0 (o‘chirish/bepul) yoki musbat son" if field in _GAME_ZERO_ALLOWED else "kamida 1 butun son"
        await edit_or_answer(
            callback,
            f"🎮 <b>{esc(label)}</b>\n\nYangi qiymatni kiriting ({min_note}):",
            reply_markup=_cancel_kb(owner_id, "games", "v"),
        )
        return
    if callback_data.act == "tog":
        tog_key = callback_data.ext if callback_data.ext in _GAME_TOGGLE_KEYS else "dice_enabled"
        async with get_session() as session:
            cfg = await GameplayService.get_game_config(session)
            cfg[tog_key] = not bool(cfg.get(tog_key, True))
            await SettingsService.set_value(session, "game_config", cfg)
            await session.commit()
        await callback.answer("✅ Yangilandi!")
    await state.clear()
    await _show_games_home(callback, owner_id)


@router.message(AdminV2States.waiting_games_field, NotCommandFilter())
async def fsm_v2_games_field(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    field = data["g_field"]
    min_val = 0 if field in _GAME_ZERO_ALLOWED else 1
    if not raw.isdigit() or int(raw) < min_val or (field == "pack_daily_limit" and int(raw) > 100):
        note = "0–100 oralig‘ida" if field == "pack_daily_limit" else f"kamida {min_val}"
        await message.answer(f"⚠️ {note} butun son kiriting.")
        return
    await state.clear()
    async with get_session() as session:
        cfg = await GameplayService.get_game_config(session)
        cfg[field] = int(raw)
        if field == "pack_daily_limit":
            cfg["pack_enabled"] = int(raw) > 0
        await SettingsService.set_value(session, "game_config", cfg)
        await session.commit()
    await _show_games_home(message, message.from_user.id)


# ===========================================================================
# 8. AUCTION (sec="auction", Pick undropped card + Step-by-step + End/Cancel)
# ===========================================================================
async def _show_auctions_home(target, owner_id: int) -> None:
    async with get_session() as session:
        items, total = await AuctionWishlistService.list_active_auctions(session, page=1, page_size=15)
    lines = [f"🔨 <b>AUKSIONLAR BOSHQARUVI</b> ({total} ta faol)\n"]
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="➕ Yangi Auksion", callback_data=AdmV2Cb(owner_id=owner_id, sec="auction", act="pick_card", p=1).pack()))
    for auc, c in items:
        lines.append(f"• <b>#{auc.id}</b> {esc(c.character_name)} — {auc.current_price:,} 🪙 ({auc.bid_count} stavka)")
        b.row(
            InlineKeyboardButton(text=f"🏁 Yakunlash #{auc.id}", callback_data=AdmV2Cb(owner_id=owner_id, sec="auction", act="fin", tid=auc.id).pack()),
            InlineKeyboardButton(text=f"❌ Bekor #{auc.id}", callback_data=AdmV2Cb(owner_id=owner_id, sec="auction", act="can", tid=auc.id).pack()),
        )
    if not items:
        lines.append("📭 Hozircha faol auksionlar yo‘q.")
    b.row(_back_home_btn(owner_id))
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


@router.callback_query(AdmV2Cb.filter(F.sec == "auction"))
async def cb_v2_auction(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "auction"):
        return
    act = callback_data.act
    owner_id = callback.from_user.id

    if act == "v":
        await state.clear()
        await _show_auctions_home(callback, owner_id)
        return

    if act == "fin":
        async with get_session() as session:
            ok, msg = await AuctionWishlistService.admin_finalize_auction(session, callback_data.tid)
            if ok:
                await session.commit()
        await callback.answer(msg, show_alert=True)
        await _show_auctions_home(callback, owner_id)
        return

    if act == "can":
        async with get_session() as session:
            ok, msg = await AuctionWishlistService.admin_cancel_auction(session, callback_data.tid)
            if ok:
                await session.commit()
        await callback.answer(msg, show_alert=True)
        await _show_auctions_home(callback, owner_id)
        return

    if act == "pick_card":
        page = max(1, callback_data.p)
        async with get_session() as session:
            cards, total = await AdminService.get_undropped_cards_paginated(session, page=page, page_size=PAGE_SIZE)
        back_cb = AdmV2Cb(owner_id=owner_id, sec="auction", act="v").pack()
        txt = f"🔨 <b>AUKSION UCHUN KARTA TANLANG</b>\nHali tashlanmagan kartalar ({total} ta):" if cards else "📭 Auksion uchun bo‘sh kartalar yo‘q."
        await edit_or_answer(
            callback,
            txt,
            reply_markup=admin_undropped_cards_kb(owner_id, cards, "auction", "new_card", page, total, back_cb, page_size=PAGE_SIZE),
        )
        return

    if act == "new_card":
        await state.set_state(AdminV2States.waiting_auc_price)
        await state.update_data(owner_tg_id=owner_id, auc_card_id=callback_data.tid)
        await edit_or_answer(
            callback,
            f"🔨 <b>YANGI AUKSION (1/3)</b>\nKarta ID: <code>{callback_data.tid}</code>\n\nEng kam (boshlang‘ich) coin narxini yozing (masalan: <code>2000</code>):",
            reply_markup=_cancel_kb(owner_id, "auction", "v"),
        )
        return

    if act == "dur":
        await state.update_data(auc_hours=callback_data.tid)
        await state.set_state(AdminV2States.waiting_auc_step)
        b = InlineKeyboardBuilder()
        for st in (50, 100, 250, 500):
            b.add(InlineKeyboardButton(text=f"+{st}", callback_data=AdmV2Cb(owner_id=owner_id, sec="auction", act="step", tid=st).pack()))
        b.adjust(2)
        await edit_or_answer(callback, f"⏳ Davomiylik: <b>{callback_data.tid} soat</b>.\n\n3/3: Eng kam stavka oshish qadamini tanlang yoki yozing:", reply_markup=b.as_markup())
        return

    if act == "step":
        data = await state.get_data()
        await state.clear()
        async with get_session() as session:
            ok, msg, auc = await AuctionWishlistService.create_auction(
                session,
                card_id=int(data["auc_card_id"]),
                start_price=int(data["auc_price"]),
                hours=int(data["auc_hours"]),
                created_by_tg_id=owner_id,
                min_step=max(10, callback_data.tid),
            )
            if ok:
                await session.commit()
            else:
                await session.rollback()
        await callback.answer(msg, show_alert=True)
        await _show_auctions_home(callback, owner_id)


@router.message(AdminV2States.waiting_auc_price, NotCommandFilter())
async def fsm_v2_auc_price(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "")
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("⚠️ Musbat coin narxini yozing.")
        return
    await state.update_data(auc_price=int(raw))
    await state.set_state(AdminV2States.waiting_auc_hours)
    b = InlineKeyboardBuilder()
    for h in (6, 12, 24, 48, 72):
        b.add(InlineKeyboardButton(text=f"{h} soat", callback_data=AdmV2Cb(owner_id=message.from_user.id, sec="auction", act="dur", tid=h).pack()))
    b.adjust(3)
    await message.answer("2/3: Auksion qancha soat davom etsin? Tanlang yoki son yozing:", reply_markup=b.as_markup())


@router.message(AdminV2States.waiting_auc_hours, NotCommandFilter())
async def fsm_v2_auc_hours(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or not (1 <= int(raw) <= 168):
        await message.answer("⚠️ 1 dan 168 soatgacha yozing.")
        return
    await state.update_data(auc_hours=int(raw))
    await state.set_state(AdminV2States.waiting_auc_step)
    b = InlineKeyboardBuilder()
    for st in (50, 100, 250, 500):
        b.add(InlineKeyboardButton(text=f"+{st}", callback_data=AdmV2Cb(owner_id=message.from_user.id, sec="auction", act="step", tid=st).pack()))
    b.adjust(2)
    await message.answer("3/3: Eng kam stavka qadamini tanlang yoki yozing:", reply_markup=b.as_markup())


@router.message(AdminV2States.waiting_auc_step, NotCommandFilter())
async def fsm_v2_auc_step(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) < 10:
        await message.answer("⚠️ Kamida 10 coin yozing.")
        return
    await state.clear()
    async with get_session() as session:
        ok, msg, _ = await AuctionWishlistService.create_auction(
            session,
            card_id=int(data["auc_card_id"]),
            start_price=int(data["auc_price"]),
            hours=int(data["auc_hours"]),
            created_by_tg_id=message.from_user.id,
            min_step=int(raw),
        )
        if ok:
            await session.commit()
    await message.answer(msg)
    await _show_auctions_home(message, message.from_user.id)


# ===========================================================================
# 9. SEASON (sec="season", Create / Extend / End)
# ===========================================================================
async def _show_season_home(target, owner_id: int) -> None:
    async with get_session() as session:
        s = await GameplayService.get_active_season(session)
        top = await GameplayService.get_season_leaderboard(session, s.id, 10) if s else []
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="➕ Yangi Mavsum Boshlash", callback_data=AdmV2Cb(owner_id=owner_id, sec="season", act="new").pack()))
    if s:
        lines = [f"🏆 <b>{esc(s.name)}</b> (#{s.season_number})\n⏳ Tugash: <b>{fmt_tashkent(s.ends_at)}</b> (Toshkent vaqti)\n"]
        for i, (sp, u) in enumerate(top, 1):
            lines.append(f"{i}. {esc(u.display_name)} — {sp.points} ball")
        b.row(
            InlineKeyboardButton(text="⏳ Muddat uzaytirish", callback_data=AdmV2Cb(owner_id=owner_id, sec="season", act="ext_menu").pack()),
            InlineKeyboardButton(text="🏁 Yakunlash", callback_data=AdmV2Cb(owner_id=owner_id, sec="season", act="end_conf").pack()),
        )
    else:
        lines = ["🏆 Hozircha faol mavsum yo‘q."]
    b.row(_back_home_btn(owner_id))
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


@router.callback_query(AdmV2Cb.filter(F.sec == "season"))
async def cb_v2_season(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "season"):
        return
    act = callback_data.act
    owner_id = callback.from_user.id

    if act == "new":
        await state.set_state(AdminV2States.waiting_season_name)
        await state.update_data(owner_tg_id=owner_id)
        await edit_or_answer(callback, "🏆 <b>YANGI MAVSUM (1/3)</b>\n\nMavsum nomini yozing (masalan: <code>Season 2: Eclipse</code>):", reply_markup=_cancel_kb(owner_id, "season", "v"))
        return

    if act == "days_p":
        await state.update_data(s_days=callback_data.tid)
        await state.set_state(AdminV2States.waiting_season_prize)
        await edit_or_answer(callback, f"📅 Davomiylik: <b>{callback_data.tid} kun</b>.\n\n3/3: 1-o‘rin g‘olibi uchun coin mukofotini yozing (masalan: <code>15000</code>):")
        return

    if act == "ext_menu":
        b = InlineKeyboardBuilder()
        for d in (7, 15, 30):
            b.add(InlineKeyboardButton(text=f"+{d} kun", callback_data=AdmV2Cb(owner_id=owner_id, sec="season", act="ext_go", tid=d).pack()))
        b.row(InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=owner_id, sec="season", act="v").pack()))
        await edit_or_answer(callback, "⏳ Mavsum muddatini necha kunga uzaytiramiz?", reply_markup=b.as_markup())
        return

    if act == "ext_go":
        async with get_session() as session:
            await GameplayService.extend_active_season(session, callback_data.tid)
            await session.commit()
        await callback.answer(f"✅ +{callback_data.tid} kun uzaytirildi!", show_alert=True)
        await _show_season_home(callback, owner_id)
        return

    if act == "end_conf":
        b = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Ha, hozir yakunlash (mukofotlarni berish)", callback_data=AdmV2Cb(owner_id=owner_id, sec="season", act="end_go").pack())],
                [InlineKeyboardButton(text="❌ Bekor", callback_data=AdmV2Cb(owner_id=owner_id, sec="season", act="v").pack())],
            ]
        )
        await edit_or_answer(callback, "🏁 Faol mavsumni hozir yakunlab, Top-3 g‘oliblarga coin tarqatamizmi?", reply_markup=b)
        return

    if act == "end_go":
        async with get_session() as session:
            await GameplayService.end_active_season_now(session)
            await session.commit()
        await callback.answer("🏁 Mavsum yakunlandi va g‘oliblar taqdirlandi!", show_alert=True)
    await state.clear()
    await _show_season_home(callback, owner_id)


@router.message(AdminV2States.waiting_season_name, NotCommandFilter())
async def fsm_v2_season_name(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id:
        return
    name = (message.text or "").strip()[:120]
    if not name:
        await message.answer("⚠️ Mavsum nomini yozing.")
        return
    await state.update_data(s_name=name)
    await state.set_state(AdminV2States.waiting_season_days)
    b = InlineKeyboardBuilder()
    for d in (14, 30, 60):
        b.add(InlineKeyboardButton(text=f"{d} kun", callback_data=AdmV2Cb(owner_id=message.from_user.id, sec="season", act="days_p", tid=d).pack()))
    await message.answer("2/3: Necha kun davom etsin? Tanlang yoki kun sonini yozing:", reply_markup=b.as_markup())


@router.message(AdminV2States.waiting_season_days, NotCommandFilter())
async def fsm_v2_season_days(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) < 1:
        await message.answer("⚠️ Kamida 1 kun yozing.")
        return
    await state.update_data(s_days=int(raw))
    await state.set_state(AdminV2States.waiting_season_prize)
    await message.answer("3/3: 1-o‘rin mukofoti (coin) ni yozing (masalan: <code>15000</code>):")


@router.message(AdminV2States.waiting_season_prize, NotCommandFilter())
async def fsm_v2_season_prize(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "")
    if not raw.isdigit():
        await message.answer("⚠️ Musbat son yozing.")
        return
    await state.clear()
    async with get_session() as session:
        await GameplayService.create_season(session, data["s_name"], int(data["s_days"]), int(raw))
        await session.commit()
    await _show_season_home(message, message.from_user.id)


# ===========================================================================
# 10. REKLAMA (sec="reklama", Qisqa matn + Joy cheklovi + Muddat + Klik)
# ===========================================================================
def _ad_status_badge(ad: AdCampaign) -> str:
    if CommunityService.is_ad_expired(ad):
        return "⌛ Muddati tugagan"
    return "🟢 Faol" if ad.is_active else "⏸ Nofaol"


def _ad_remaining_text(ad: AdCampaign) -> str:
    if ad.expires_at is None:
        return "♾ To o‘chirilmaguncha"
    now = utcnow()
    exp = ensure_utc(ad.expires_at)
    secs = int((exp - now).total_seconds())
    if secs <= 0:
        return "⌛ Muddati tugagan"
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins = max(1, rem // 60)
    if days > 0:
        left = f"{days} kun {hours} soat"
    elif hours > 0:
        left = f"{hours} soat {mins} daq."
    else:
        left = f"{mins} daqiqa"
    exp_local = exp.astimezone(_local_tz())
    return f"{left} ({exp_local:%d.%m %H:%M} gacha)"


def _ad_placement_kb(owner_id: int, occupied: dict, act: str, tid: int = 0) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for key in ("group", "private", "all"):
        label = CommunityService.placement_label(key)
        if key == "all":
            busy = bool(occupied)
        else:
            busy = key in occupied
        tag = "🔒 Band" if busy else "✅ Bo‘sh"
        b.row(
            InlineKeyboardButton(
                text=f"{label} — {tag}",
                callback_data=AdmV2Cb(owner_id=owner_id, sec="reklama", act=act, tid=tid, ext=key).pack(),
            )
        )
    back_act = "det" if tid else "v"
    b.row(InlineKeyboardButton(text="❌ Bekor", callback_data=AdmV2Cb(owner_id=owner_id, sec="reklama", act=back_act, tid=tid).pack()))
    return b.as_markup()


def _ad_duration_kb(owner_id: int, act: str, tid: int = 0) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for hrs, lbl in CommunityService.AD_DURATION_OPTIONS:
        ext_val = str(hrs) if act == "setdur" else ""
        cb_tid = tid if act == "setdur" else hrs
        b.add(
            InlineKeyboardButton(
                text=f"⏳ {lbl}" if hrs > 0 else lbl,
                callback_data=AdmV2Cb(owner_id=owner_id, sec="reklama", act=act, tid=cb_tid, ext=ext_val).pack(),
            )
        )
    b.adjust(2)
    back_act = "det" if tid and act == "setdur" else "v"
    b.row(InlineKeyboardButton(text="❌ Bekor", callback_data=AdmV2Cb(owner_id=owner_id, sec="reklama", act=back_act, tid=tid if back_act == "det" else 0).pack()))
    return b.as_markup()


async def _show_reklama_home(target, owner_id: int) -> None:
    async with get_session() as session:
        ads = await CommunityService.list_ads(session)
        occupied = await CommunityService.get_occupied_ad_slots(session)
        await session.commit()

    grp_holder = f"🟢 Faol (#{occupied['group'].id})" if "group" in occupied else "⚪ Bo‘sh"
    pm_holder = f"🟢 Faol (#{occupied['private'].id})" if "private" in occupied else "⚪ Bo‘sh"
    lines = [
        "📣 <b>REKLAMA KAMPANIYALARI</b>",
        "━━━━━━━━━━━━━━━",
        f"👥 Guruh (drop tagi): <b>{grp_holder}</b>",
        f"📱 User panel: <b>{pm_holder}</b>",
        "",
    ]
    b = InlineKeyboardBuilder()
    all_full = "group" in occupied and "private" in occupied
    add_lbl = "➕ Reklama qo‘shish" if not all_full else "🔒 Joylar band (Reklama qo‘shish)"
    b.row(InlineKeyboardButton(text=add_lbl, callback_data=AdmV2Cb(owner_id=owner_id, sec="reklama", act="add").pack()))
    for a in ads[:12]:
        icon = "⌛" if CommunityService.is_ad_expired(a) else ("🟢" if a.is_active else "⏸")
        click_part = f" · 🔗 {a.clicks_count}" if (a.button_url or a.clicks_count > 0) else ""
        lines.append(
            f"{icon} <b>#{a.id}</b> {esc(a.title[:36])} — "
            f"{esc(CommunityService.placement_label(a.placement))} · 👁 {a.views_count}{click_part}"
        )
        b.row(
            InlineKeyboardButton(
                text=f"{icon} #{a.id} {a.title[:26]}",
                callback_data=AdmV2Cb(owner_id=owner_id, sec="reklama", act="det", tid=a.id).pack(),
            )
        )
    if not ads:
        lines.append("📭 Hozircha reklamalar yo‘q.")
    else:
        lines.append("\n👇 Boshqarish yoki tahrirlash uchun reklamani tanlang:")
    b.row(_back_home_btn(owner_id))
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


async def _show_reklama_detail(target, owner_id: int, ad_id: int) -> None:
    async with get_session() as session:
        await CommunityService.expire_due_ads(session)
        ad = await session.get(AdCampaign, ad_id)
        await session.commit()
    if ad is None:
        await _show_reklama_home(target, owner_id)
        return

    lines = [
        f"📣 <b>REKLAMA #{ad.id}</b>",
        "━━━━━━━━━━━━━━━",
        f"📝 <b>Matn:</b>\n{esc(ad.text)}",
        "",
        f"📌 Holat: <b>{_ad_status_badge(ad)}</b>",
        f"📍 Joyi: <b>{esc(CommunityService.placement_label(ad.placement))}</b>",
        f"⏳ Muddat: <b>{_ad_remaining_text(ad)}</b>",
        f"👁 Ko‘rishlar: <b>{ad.views_count:,}</b> ta",
    ]
    if ad.button_url or ad.clicks_count > 0:
        lines.append(f"🔗 Havola/user bosishlari: <b>{ad.clicks_count:,}</b> marta")
        if ad.button_url:
            lines.append(f"🌐 Manzil: <code>{esc(ad.button_url)}</code>")

    b = InlineKeyboardBuilder()
    tog_txt = "⏸ Faolsizlantirish" if ad.is_active else "▶️ Faollashtirish"
    b.row(
        InlineKeyboardButton(text=tog_txt, callback_data=AdmV2Cb(owner_id=owner_id, sec="reklama", act="tog", tid=ad.id).pack()),
        InlineKeyboardButton(text="✏️ Matn", callback_data=AdmV2Cb(owner_id=owner_id, sec="reklama", act="edtxt", tid=ad.id).pack()),
    )
    b.row(
        InlineKeyboardButton(text="📍 Joyi", callback_data=AdmV2Cb(owner_id=owner_id, sec="reklama", act="edplc", tid=ad.id).pack()),
        InlineKeyboardButton(text="⏳ Vaqti", callback_data=AdmV2Cb(owner_id=owner_id, sec="reklama", act="eddur", tid=ad.id).pack()),
    )
    b.row(
        InlineKeyboardButton(text="🗑 O‘chirish", callback_data=AdmV2Cb(owner_id=owner_id, sec="reklama", act="del", tid=ad.id).pack()),
        InlineKeyboardButton(text="⬅️ Orqaga", callback_data=AdmV2Cb(owner_id=owner_id, sec="reklama", act="v").pack()),
    )
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


@router.callback_query(AdmV2Cb.filter(F.sec == "reklama"))
async def cb_v2_reklama(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "reklama"):
        return
    act = callback_data.act
    owner_id = callback.from_user.id

    if act == "add":
        async with get_session() as session:
            occupied = await CommunityService.get_occupied_ad_slots(session)
            await session.commit()
        if "group" in occupied and "private" in occupied:
            ids = ", ".join(f"#{a.id}" for a in sorted({ad.id: ad for ad in occupied.values()}.values(), key=lambda x: x.id))
            await callback.answer(
                f"⛔ Barcha reklama joylari band ({ids}). Avval faol reklamani o‘chiring, faolsizlantiring yoki joyini o‘zgartiring!",
                show_alert=True,
            )
            return
        await state.set_state(AdminV2States.waiting_ad_content)
        await state.update_data(owner_tg_id=owner_id, ad_edit_id=None)
        await edit_or_answer(
            callback,
            "📣 <b>YANGI REKLAMA (1/3)</b>\n\n"
            f"Faqat qisqa <b>matn</b> yuboring (ko‘pi bilan <b>{CommunityService.AD_MAX_LEN}</b> belgi, rasm va fayllarsiz).\n\n"
            "💡 <i>Matn ichiga kanal linki (https://t.me/... yoki @username) yozsangiz, unga nechta odam kirganini ham sanaymiz.</i>",
            reply_markup=_cancel_kb(owner_id, "reklama", "v"),
        )
        return

    if act == "place":
        chosen = callback_data.ext or "all"
        async with get_session() as session:
            ok_plc, err_plc = await CommunityService.can_use_placement(session, chosen)
            await session.commit()
        if not ok_plc:
            await callback.answer(err_plc[:190], show_alert=True)
            return
        await state.update_data(ad_place=chosen)
        await edit_or_answer(
            callback,
            f"⏳ <b>YANGI REKLAMA (3/3)</b>\n\nJoy: <b>{esc(CommunityService.placement_label(chosen))}</b>\nReklama qancha vaqt ko‘rinsin?",
            reply_markup=_ad_duration_kb(owner_id, act="dur"),
        )
        return

    if act == "dur":
        data = await state.get_data()
        txt = (data.get("ad_text") or "").strip()
        place = data.get("ad_place") or "all"
        if not txt:
            await state.clear()
            await callback.answer("⚠️ Reklama matni topilmadi, qaytadan urining.", show_alert=True)
            await _show_reklama_home(callback, owner_id)
            return
        async with get_session() as session:
            ok, msg, ad = await CommunityService.create_ad(
                session,
                text=txt,
                admin_tg_id=owner_id,
                placement=place,
                duration_hours=int(callback_data.tid),
            )
            if ok:
                await session.commit()
            else:
                await session.rollback()
        await state.clear()
        await callback.answer(msg[:190], show_alert=True)
        if ok and ad is not None:
            await _show_reklama_detail(callback, owner_id, ad.id)
        else:
            await _show_reklama_home(callback, owner_id)
        return

    if act == "det":
        await state.clear()
        await _show_reklama_detail(callback, owner_id, callback_data.tid)
        return

    if act == "tog":
        async with get_session() as session:
            ok, msg, _ad = await CommunityService.toggle_ad(session, callback_data.tid)
            if ok:
                await session.commit()
            else:
                await session.rollback()
        await callback.answer(msg[:190], show_alert=True)
        await _show_reklama_detail(callback, owner_id, callback_data.tid)
        return

    if act == "edtxt":
        await state.set_state(AdminV2States.waiting_ad_edit_text)
        await state.update_data(owner_tg_id=owner_id, ad_edit_id=callback_data.tid)
        await edit_or_answer(
            callback,
            f"✏️ <b>REKLAMA #{callback_data.tid} MATNINI TAHRIRLASH</b>\n\n"
            f"Yangi qisqa matnni yuboring (ko‘pi bilan {CommunityService.AD_MAX_LEN} belgi, rasmsiz):",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="❌ Bekor", callback_data=AdmV2Cb(owner_id=owner_id, sec="reklama", act="det", tid=callback_data.tid).pack())]]
            ),
        )
        return

    if act == "edplc":
        async with get_session() as session:
            occupied = await CommunityService.get_occupied_ad_slots(session, exclude_ad_id=callback_data.tid)
            await session.commit()
        await edit_or_answer(
            callback,
            f"📍 <b>REKLAMA #{callback_data.tid} JOYINI O‘ZGARTIRISH</b>\n\nYangi joyni tanlang:",
            reply_markup=_ad_placement_kb(owner_id, occupied, act="setplc", tid=callback_data.tid),
        )
        return

    if act == "setplc":
        async with get_session() as session:
            ok, msg, _ad = await CommunityService.update_ad(
                session, callback_data.tid, placement=callback_data.ext or "all"
            )
            if ok:
                await session.commit()
            else:
                await session.rollback()
        await callback.answer(msg[:190], show_alert=True)
        await _show_reklama_detail(callback, owner_id, callback_data.tid)
        return

    if act == "eddur":
        await edit_or_answer(
            callback,
            f"⏳ <b>REKLAMA #{callback_data.tid} VAQTINI O‘ZGARTIRISH</b>\n\nHozirdan boshlab qancha vaqt ko‘rinsin?",
            reply_markup=_ad_duration_kb(owner_id, act="setdur", tid=callback_data.tid),
        )
        return

    if act == "setdur":
        hrs = int(callback_data.ext) if (callback_data.ext or "").isdigit() else 0
        async with get_session() as session:
            ok, msg, _ad = await CommunityService.update_ad(
                session, callback_data.tid, duration_hours=hrs
            )
            if ok:
                await session.commit()
            else:
                await session.rollback()
        await callback.answer(msg[:190], show_alert=True)
        await _show_reklama_detail(callback, owner_id, callback_data.tid)
        return

    if act == "del":
        async with get_session() as session:
            await CommunityService.delete_ad(session, callback_data.tid)
            await session.commit()
        await state.clear()
        await callback.answer("🗑 Reklama o‘chirildi.", show_alert=True)
        await _show_reklama_home(callback, owner_id)
        return

    await state.clear()
    await _show_reklama_home(callback, owner_id)


@router.message(AdminV2States.waiting_ad_content, NotCommandFilter())
async def fsm_v2_ad_content(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id:
        return
    if (
        message.photo
        or message.document
        or message.video
        or message.animation
        or message.sticker
        or message.audio
        or message.voice
        or message.video_note
        or not message.text
    ):
        await message.answer("⚠️ Reklama faqat qisqa <b>MATN</b> bo‘lishi kerak! Rasm, fayl yoki stiker yubormang.")
        return
    ok, err, cleaned = CommunityService.validate_ad_text(message.text)
    if not ok:
        await message.answer(err)
        return
    await state.update_data(ad_text=cleaned)
    await state.set_state(None)
    async with get_session() as session:
        occupied = await CommunityService.get_occupied_ad_slots(session)
        await session.commit()
    await message.answer(
        f"📍 <b>YANGI REKLAMA (2/3)</b>\n\nMatn saqlandi ({len(cleaned)}/{CommunityService.AD_MAX_LEN} belgi).\nReklama qayerlarda chiqsin?",
        reply_markup=_ad_placement_kb(message.from_user.id, occupied, act="place"),
    )


@router.message(AdminV2States.waiting_ad_edit_text, NotCommandFilter())
async def fsm_v2_ad_edit_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    if (
        message.photo
        or message.document
        or message.video
        or message.animation
        or message.sticker
        or message.audio
        or message.voice
        or message.video_note
        or not message.text
    ):
        await message.answer("⚠️ Reklama faqat qisqa <b>MATN</b> bo‘lishi kerak! Rasm, fayl yoki stiker yubormang.")
        return
    ad_id = int(data.get("ad_edit_id") or 0)
    async with get_session() as session:
        ok, msg, _ad = await CommunityService.update_ad(session, ad_id, text=message.text)
        if ok:
            await session.commit()
        else:
            await session.rollback()
    if not ok:
        await message.answer(msg)
        return
    await state.clear()
    await _show_reklama_detail(message, message.from_user.id, ad_id)


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
            for old_cid, new_cid in migrated:
                await session.execute(
                    update(Group).where(Group.telegram_chat_id == old_cid).values(telegram_chat_id=new_cid)
                )
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


# ===========================================================================
# 13. MEMORY / BACKUP (sec="memory", List + Download + Date Search + Clean + Restore)
# ===========================================================================
async def _show_memory_home(target, owner_id: int, date_filter: Optional[str] = None) -> None:
    async with get_session() as session:
        backups = await SecurityBackupService.list_backups(session, limit=20)
    if date_filter:
        backups = [b for b in backups if ensure_utc(b.created_at).strftime("%Y-%m-%d") == date_filter]
    lines = [f"💾 <b>XOTIRA VA BACKUP TIZIMI</b>" + (f" (Sana: <code>{date_filter}</code>)" if date_filter else "") + "\n"]
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="💾 Hozir Backup (.zip)", callback_data=AdmV2Cb(owner_id=owner_id, sec="memory", act="backup_now").pack()),
        InlineKeyboardButton(text="📅 Sana bo‘yicha", callback_data=AdmV2Cb(owner_id=owner_id, sec="memory", act="date_q").pack()),
    )
    for b_rec in backups[:8]:
        dt_s = fmt_tashkent(b_rec.created_at, "%d.%m %H:%M")
        kb_size = max(1, b_rec.file_size_bytes // 1024)
        lines.append(f"• <code>{dt_s}</code> — {kb_size} KB ({b_rec.backup_type})")
        b.row(
            InlineKeyboardButton(
                text=f"⬇️ {dt_s} ({kb_size} KB, {b_rec.backup_type})",
                callback_data=AdmV2Cb(owner_id=owner_id, sec="memory", act="dl", tid=b_rec.id).pack(),
            )
        )
    if not backups:
        lines.append("📭 Backuplar topilmadi.")
    b.row(
        InlineKeyboardButton(text="🧹 Eskilarini tozalash", callback_data=AdmV2Cb(owner_id=owner_id, sec="memory", act="clean").pack()),
        InlineKeyboardButton(text="♻️ Restore (.zip)", callback_data=AdmV2Cb(owner_id=owner_id, sec="memory", act="restore").pack()),
    )
    b.row(_back_home_btn(owner_id))
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


@router.callback_query(AdmV2Cb.filter(F.sec == "memory"))
async def cb_v2_memory(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "memory"):
        return
    act = callback_data.act
    owner_id = callback.from_user.id

    if act == "backup_now":
        async with get_session() as session:
            rec = await SecurityBackupService.create_full_backup_zip(session, "manual", owner_id)
            await session.commit()
        try:
            await callback.bot.send_document(
                owner_id,
                FSInputFile(rec.storage_path),
                caption=f"💾 <b>To‘liq ZIP Backup</b>\n📦 {rec.filename} ({rec.file_size_bytes:,} bytes)",
            )
            await callback.answer("✅ Backup yaratildi va yuborildi!", show_alert=True)
        except Exception:
            await callback.answer(f"✅ Backup saqlandi: {rec.filename}", show_alert=True)
        await _show_memory_home(callback, owner_id)
        return

    if act == "dl":
        async with get_session() as session:
            rec = await session.get(BackupRecord, callback_data.tid)
        if rec is None:
            await callback.answer("Backup topilmadi.", show_alert=True)
            return
        try:
            await callback.bot.send_document(
                owner_id,
                FSInputFile(rec.storage_path),
                caption=f"💾 <b>{esc(rec.filename)}</b> ({rec.file_size_bytes:,} bytes)",
            )
            await callback.answer("📥 Fayl yuborildi!")
        except Exception as exc:
            await callback.answer(f"❌ Faylni yuborib bo‘lmadi: {str(exc)[:60]}", show_alert=True)
        return

    if act == "date_q":
        await state.set_state(AdminV2States.waiting_backup_date)
        await state.update_data(owner_tg_id=owner_id)
        await edit_or_answer(callback, "📅 Backup sanasini yozing (<code>YYYY-MM-DD</code>, masalan: <code>2026-11-17</code>):", reply_markup=_cancel_kb(owner_id, "memory", "v"))
        return

    if act == "clean":
        async with get_session() as session:
            n = await SecurityBackupService.cleanup_old_backups(session, 14)
            await session.commit()
        await callback.answer(f"🧹 {n} ta eski backup o‘chirildi.", show_alert=True)
        await _show_memory_home(callback, owner_id)
        return

    if act == "restore":
        if not is_super_admin(callback.from_user):
            await callback.answer("⛔ Faqat Super Admin tiklay oladi.", show_alert=True)
            return
        await state.set_state(AdminV2States.waiting_backup_restore)
        await state.update_data(owner_tg_id=owner_id)
        await edit_or_answer(
            callback,
            "♻️ <b>ZAXIRADAN TIKLASH</b>\n\nAvval joriy bazadan avtomatik zaxira olinadi.\n<code>.zip</code> zaxira faylini yuboring:",
            reply_markup=_cancel_kb(owner_id, "memory", "v"),
        )
        return

    await state.clear()
    await _show_memory_home(callback, owner_id)


@router.message(AdminV2States.waiting_backup_date, NotCommandFilter())
async def fsm_v2_backup_date(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        await message.answer("⚠️ Format: <code>YYYY-MM-DD</code>")
        return
    await state.clear()
    await _show_memory_home(message, message.from_user.id, date_filter=raw)


@router.message(AdminV2States.waiting_backup_restore, F.document)
async def fsm_v2_backup_restore(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id or not is_super_admin(message.from_user):
        return
    doc = message.document
    if not doc or not (doc.file_name or "").lower().endswith(".zip"):
        await message.answer("⚠️ Faqat backup .zip fayl yuboring.")
        return
    await state.clear()
    buf = io.BytesIO()
    await message.bot.download(doc, destination=buf)
    try:
        with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
            payload = json.loads(zf.read("database_backup.json").decode("utf-8"))
    except Exception as exc:
        await message.answer(f"❌ Backup fayli o‘qilmadi: {esc(str(exc))}")
        return

    async with get_session() as session:
        await SecurityBackupService.create_full_backup_zip(session, "pre_restore", message.from_user.id)
        restored_cards = 0
        for row in payload.get("cards", []):
            cid = row.get("id")
            if cid and await session.get(Card, cid) is None and row.get("card_code"):
                session.add(
                    Card(
                        character_name=row["character_name"],
                        anime_name=row["anime_name"],
                        anime_id=row.get("anime_id", "an_01"),
                        card_code=row["card_code"],
                        rarity=row["rarity"],
                        media_type=row.get("media_type", "Anime"),
                        image=row["image"],
                        base_value=int(row.get("base_value", 1000)),
                        drop_limit=int(row.get("drop_limit", 1)),
                    )
                )
                restored_cards += 1
        await CommunityService.log_audit(session, message.from_user.id, "backup_restore", "database", details={"restored_cards": restored_cards})
        await session.commit()
    await message.answer(f"✅ Pre-restore backup saqlandi va tiklash yakunlandi! Tiklangan yangi kartalar: <b>{restored_cards}</b>")


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
