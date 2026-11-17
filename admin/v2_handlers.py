import asyncio
import json
import logging
import zipfile
import io
from datetime import datetime, timezone
from typing import Optional

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select, update

from admin.filters import IsAdminFilter, has_admin_perm, resolve_admin_perms
from config.settings import get_settings
from database.session import get_session
from keyboards.v2 import (
    admin_v2_card_detail_kb,
    admin_v2_cards_menu_kb,
    admin_v2_edit_fields_kb,
    admin_v2_home_kb,
    admin_v2_view_cards_kb,
)
from models.base import ensure_utc, utcnow
from models.card import Card
from models.group import Group
from models.user import User
from models.user_card import UserCard
from models.v2_features import AdCampaign, ForcedChannel, Quiz, SecurityIncident, SupportTicket
from services.admin_service import AdminService
from services.auction_wishlist_service import AuctionWishlistService
from services.card_service import CardService
from services.community_service import CommunityService
from services.gameplay_service import GameplayService
from services.security_backup_service import MAX_ZIP_BYTES, SecurityBackupService
from services.settings_service import SettingsService
from services.user_service import UserService
from utils.callback_data import AdmV2Cb, AdminCb, NavCb
from utils.constants import ADMIN_PERMISSION_SECTIONS, MEDIA_TYPES, PAGE_SIZE, get_rarity_meta, list_rarities
from utils.formatting import esc, fmt_coins, format_card_about, format_card_block
from utils.image_check import validate_image_url
from utils.tg_helpers import NotCommandFilter, edit_or_answer, is_super_admin, send_card_message

logger = logging.getLogger(__name__)

router = Router(name="admin_v2")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


class AdminV2States(StatesGroup):
    waiting_zip_cards = State()
    waiting_card_search = State()
    waiting_card_edit_val = State()
    waiting_new_admin_id = State()
    waiting_channel_add = State()
    waiting_rank_input = State()
    waiting_quiz_create = State()
    waiting_quiz_question = State()
    waiting_games_cfg = State()
    waiting_auction_create = State()
    waiting_season_create = State()
    waiting_ad_create = State()
    waiting_backup_restore = State()
    waiting_support_reply = State()
    waiting_security_ban = State()
    waiting_scheduled_msg = State()


def _back_home_btn(owner_id: int) -> InlineKeyboardButton:
    return InlineKeyboardButton(text="⬅️ Admin Panel", callback_data=AdminCb(owner_id=owner_id, section="home").pack())


async def _deny_if_no_perm(callback: CallbackQuery, admin_perms: set | None, sec: str) -> bool:
    if not has_admin_perm(callback.from_user, admin_perms, sec):
        await callback.answer("⛔ Bu bo‘limga ruxsatingiz yo‘q.", show_alert=True)
        return True
    return False


# ---------------------------------------------------------------------------
# CARDS V2 (ZIP Import, View Tabs 20/page, Titles, Card Detail, Edit Fields, Search, Restore)
# ---------------------------------------------------------------------------
@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "v")))
async def cb_v2_cards_menu(callback: CallbackQuery, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    await state.clear()
    await edit_or_answer(
        callback,
        "🎴 <b>CARD BO‘LIMI V2</b>\n\nKerakli amalni tanlang:",
        reply_markup=admin_v2_cards_menu_kb(callback.from_user.id),
    )


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "zip")))
async def cb_v2_zip_prompt(callback: CallbackQuery, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    await state.set_state(AdminV2States.waiting_zip_cards)
    await state.update_data(owner_tg_id=callback.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Bekor", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="cards", act="v").pack())]])
    await edit_or_answer(
        callback,
        "📦 <b>ZIP CARD IMPORT</b>\n\n"
        "Botga <code>.zip</code> arxiv yuboring (max 50 MB).\n"
        "• Ichida <code>cards.json</code> yoki <code>cards.csv</code> + rasmlar bo‘lishi mumkin;\n"
        "• Yoki rasmlar nomini <code>Character - Anime - Rank.jpg</code> ko‘rinishida qo‘ying.\n"
        "<i>Bekor qilish: /cancel</i>",
        reply_markup=kb,
    )


@router.message(AdminV2States.waiting_zip_cards, F.document)
async def fsm_v2_zip_upload(message: Message, state: FSMContext) -> None:
    doc = message.document
    if not doc or not (doc.file_name or "").lower().endswith(".zip"):
        await message.answer("⚠️ Faqat <code>.zip</code> arxiv fayl yuboring.")
        return
    if (doc.file_size or 0) > MAX_ZIP_BYTES:
        await message.answer("⚠️ ZIP fayl hajmi 50 MB dan oshmasligi kerak.")
        return
    await state.clear()
    wait_msg = await message.answer("⏳ ZIP arxiv tekshirilmoqda va import qilinmoqda...")

    buf = io.BytesIO()
    await message.bot.download(doc, destination=buf)
    async with get_session() as session:
        rep = await SecurityBackupService.import_cards_from_zip(session, buf.getvalue(), message.from_user.id)
        await session.commit()

    lines = [
        "✅ <b>IMPORT COMPLETED</b>",
        f"Files: <b>{rep.total_files}</b>",
        f"Cards: <b>{rep.total_cards}</b>",
        f"Images: <b>{rep.total_images}</b>",
        f"Added: <b>{rep.added}</b>",
        f"Duplicate: <b>{rep.duplicates}</b>",
        f"Failed: <b>{rep.failed}</b>",
    ]
    if rep.failed_files:
        lines.append("\n<b>Failed files:</b>")
        for idx, item in enumerate(rep.failed_files[:15], start=1):
            lines.append(f"{idx}. {esc(item)}")
    await wait_msg.edit_text("\n".join(lines), reply_markup=admin_v2_cards_menu_kb(message.from_user.id))


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
    txt = f"👁 <b>VIEW CARDS — {esc(tab.upper())}</b> (Jami: <b>{total}</b> ta | 20/sahifa)"
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
        rows, total = await AdminService.get_v2_media_titles(session, media_group=mgroup, page=page, page_size=PAGE_SIZE)
    lines = [f"📊 <b>{esc(mgroup.upper())} NOMLARI</b> (Karta soni bo‘yicha | Jami: {total})\n"]
    for idx, (title, cnt) in enumerate(rows, start=(page - 1) * PAGE_SIZE + 1):
        lines.append(f"{idx}. <b>{esc(title)}</b> — {cnt}")
    if not rows:
        lines.append("📭 Topilmadi.")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ ORQAGA",
                    callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="cards", act="view_tabs", ext=mgroup, p=1).pack(),
                )
            ]
        ]
    )
    await edit_or_answer(callback, "\n".join(lines), reply_markup=kb)


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "card_det")))
async def cb_v2_card_detail(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    async with get_session() as session:
        card = await session.get(Card, callback_data.tid)
        if card is None:
            await callback.answer("Karta topilmadi.", show_alert=True)
            return
        owners_list, drops_sent = await AdminService.get_card_ownership_and_drops(session, card.id)
        latest_uc = (
            await session.execute(
                select(UserCard).where(UserCard.card_id == card.id).order_by(UserCard.id.desc()).limit(1)
            )
        ).scalar_one_or_none()
    owner_preview = ", ".join(f"{n} (×{q})" for n, q in owners_list[:4]) if owners_list else "Yo‘q"
    claimed_dt = latest_uc.acquired_at.strftime("%Y-%m-%d %H:%M") if latest_uc and latest_uc.acquired_at else "—"
    text = (
        f"{format_card_block(card, show_status=True, owner_name=owner_preview)}\n"
        f"📤 <b>Tashlangan:</b> {drops_sent} marta\n"
        f"🗓 <b>Oxirgi olingan sana:</b> <code>{claimed_dt}</code>\n"
        f"🆔 <b>Card DB ID:</b> <code>{card.id}</code>"
    )
    kb = admin_v2_card_detail_kb(
        callback.from_user.id,
        card,
        tab=callback_data.ext or "all",
        page=callback_data.p,
        is_super=is_super_admin(callback.from_user),
    )
    if callback.message and card.image and (card.image.startswith("http") or len(card.image) > 20):
        try:
            await callback.message.delete()
            await send_card_message(callback.bot, callback.message.chat.id, card, text, kb)
            await callback.answer()
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
            lines = [f"👤 <b>{esc(card.character_name)} EGALARI</b> (Drops: {drops})\n"]
            for name, qty in owners[:20]:
                lines.append(f"• {esc(name)} — <b>{qty}</b> ta")
            if not owners:
                lines.append("📭 Hali hech kimda yo‘q.")
            text = "\n".join(lines)
        elif act == "hist":
            hist = await AuctionWishlistService.get_card_history(session, card.id, limit=20)
            lines = [f"📜 <b>KARTA TARIXI: {esc(card.character_name)}</b>\n"]
            for h in hist:
                dt = ensure_utc(h.created_at).strftime("%d.%m %H:%M")
                lines.append(f"• <code>{dt}</code> — <b>{esc(h.event_type)}</b>" + (f" ({h.price:,} 🪙)" if h.price else ""))
            if not hist:
                lines.append("📭 Tarix yozuvlari yo‘q.")
            text = "\n".join(lines)
        else:  # restore
            if not is_super_admin(callback.from_user):
                await callback.answer("⛔ Faqat Super Admin tiklay oladi.", show_alert=True)
                return
            await CardService.restore_card(session, card.id)
            await CommunityService.log_audit(session, callback.from_user.id, "restore_card", "card", str(card.id))
            await session.commit()
            await callback.answer("♻️ Karta tiklandi!", show_alert=True)
            text = f"♻️ <b>{esc(card.character_name)}</b> muvaffaqiyatli tiklandi!"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Karta",
                    callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="cards", act="card_det", tid=callback_data.tid).pack(),
                )
            ]
        ]
    )
    await edit_or_answer(callback, text, reply_markup=kb)


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act.in_({"search", "edit_search"}))))
async def cb_v2_card_search_prompt(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    await state.set_state(AdminV2States.waiting_card_search)
    await state.update_data(owner_tg_id=callback.from_user.id, next_act="edit_pick" if callback_data.act == "edit_search" else "card_det")
    await edit_or_answer(
        callback,
        "🔍 <b>KARTA QIDIRISH</b>\n\nPersonaj ismi, anime/media nomi, <code>CRD-...</code> yoki DB ID yozing:\n<i>Bekor qilish: /cancel</i>",
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
                text=f"{meta.emoji} {c.character_name[:24]} ({c.card_code})"[:60],
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
        await edit_or_answer(callback, "🎬 Yangi Media Type tanlang:", reply_markup=b.as_markup())
        return
    if field == "rank":
        b = InlineKeyboardBuilder()
        for r in list_rarities():
            meta = get_rarity_meta(r)
            b.row(InlineKeyboardButton(text=f"{meta.emoji} {r}", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="cards", act="set_rank", tid=cid, ext=r).pack()))
        await edit_or_answer(callback, "⭐ Yangi Rank tanlang:", reply_markup=b.as_markup())
        return

    prompts = {
        "char": "👤 Yangi personaj ismini yozing:",
        "anime": "📺 Yangi media/anime nomini yozing:",
        "img": "🖼 Yangi rasmni foto sifatida yuboring yoki URL yozing:",
        "desc": "📝 Yangi tavsif (description) matnini yozing:",
        "alias": "🏷 Aliaslarni vergul bilan yozing (masalan: <code>Gojo, Satoru</code>):",
        "value": "💎 Yangi coin qiymatini kiriting (musbat son):",
    }
    await state.set_state(AdminV2States.waiting_card_edit_val)
    await state.update_data(owner_tg_id=callback.from_user.id, edit_card_id=cid, edit_field=field)
    await edit_or_answer(callback, prompts.get(field, "Yangi qiymatni yozing:") + "\n<i>Bekor: /cancel</i>")


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
            old = card.rarity
            card.rarity = callback_data.ext
            meta = get_rarity_meta(card.rarity)
            card.drop_weight = meta.drop_weight
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


# ---------------------------------------------------------------------------
# ADMIN RBAC MANAGEMENT (sec="admins")
# ---------------------------------------------------------------------------
@router.callback_query(AdmV2Cb.filter(F.sec == "admins"))
async def cb_v2_admins(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "admins"):
        return
    if callback_data.act == "add":
        if not is_super_admin(callback.from_user):
            await callback.answer("⛔ Faqat Super Admin yangi admin qo‘sha oladi.", show_alert=True)
            return
        await state.set_state(AdminV2States.waiting_new_admin_id)
        await state.update_data(owner_tg_id=callback.from_user.id)
        await edit_or_answer(
            callback,
            "👑 <b>YANGI ADMIN QO‘SHISH</b>\n\n"
            "Format: <code>TelegramID | RolNomi | ruxsat1,ruxsat2</code>\n"
            "Misol: <code>123456789 | Moderator | cards,support,report</code>\n"
            "<i>Yoki barchasi uchun: <code>123456789 | Co-Admin | all</code></i>\n"
            "Mavjud bo‘limlar: <code>" + ", ".join(k for k, _ in ADMIN_PERMISSION_SECTIONS) + "</code>",
        )
        return
    if callback_data.act == "del":
        if not is_super_admin(callback.from_user):
            await callback.answer("⛔ Faqat Super Admin o‘chira oladi.", show_alert=True)
            return
        async with get_session() as session:
            await CommunityService.remove_admin_role(session, callback_data.tid)
            await CommunityService.log_audit(session, callback.from_user.id, "remove_admin", "admin", str(callback_data.tid))
            await session.commit()
        await callback.answer("✅ Admin olib tashlandi.")

    async with get_session() as session:
        roles = await CommunityService.list_admin_roles(session)
    super_ids = sorted(get_settings().admin_ids)
    lines = ["👑 <b>ADMINLAR VA RUXSATLAR (RBAC)</b>\n", f"⭐ Super Admin IDs: <code>{', '.join(map(str, super_ids))}</code>\n"]
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="➕ Admin qo‘shish / yangilash", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="admins", act="add").pack()))
    for r in roles:
        if not r.is_active:
            continue
        p_txt = "BARCHA" if r.is_super_admin else ", ".join(r.permissions or [])
        lines.append(f"• <code>{r.telegram_id}</code> — <b>{esc(r.role_name)}</b> ({esc(p_txt)})")
        if is_super_admin(callback.from_user):
            b.row(
                InlineKeyboardButton(
                    text=f"🗑 O‘chirish: {r.telegram_id}",
                    callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="admins", act="del", tid=r.telegram_id).pack(),
                )
            )
    b.row(_back_home_btn(callback.from_user.id))
    await edit_or_answer(callback, "\n".join(lines), reply_markup=b.as_markup())


@router.message(AdminV2States.waiting_new_admin_id, NotCommandFilter())
async def fsm_v2_new_admin(message: Message, state: FSMContext) -> None:
    if not is_super_admin(message.from_user):
        return
    parts = [p.strip() for p in (message.text or "").split("|")]
    if len(parts) < 2 or not parts[0].lstrip("-").isdigit():
        await message.answer("⚠️ Format: <code>TelegramID | RolNomi | cards,support</code>")
        return
    tg_id = int(parts[0])
    role_name = parts[1]
    raw_perms = parts[2] if len(parts) > 2 else "cards,support"
    all_keys = [k for k, _ in ADMIN_PERMISSION_SECTIONS]
    is_sup = raw_perms.lower() == "all"
    perms = all_keys if is_sup else [p.strip() for p in raw_perms.split(",") if p.strip() in all_keys]
    await state.clear()
    async with get_session() as session:
        await CommunityService.upsert_admin_role(session, tg_id, role_name, perms, message.from_user.id, is_super=is_sup)
        await CommunityService.log_audit(session, message.from_user.id, "upsert_admin", "admin", str(tg_id), new_value={"role": role_name, "perms": perms})
        await session.commit()
    await message.answer(f"✅ Admin <code>{tg_id}</code> ({esc(role_name)}) saqlandi!", reply_markup=admin_v2_home_kb(message.from_user.id, resolve_admin_perms(message.from_user, None)))


# ---------------------------------------------------------------------------
# FORCED CHANNELS (sec="channels")
# ---------------------------------------------------------------------------
@router.callback_query(AdmV2Cb.filter(F.sec == "channels"))
async def cb_v2_channels(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "channels"):
        return
    if callback_data.act == "add":
        await state.set_state(AdminV2States.waiting_channel_add)
        await state.update_data(owner_tg_id=callback.from_user.id)
        await edit_or_answer(
            callback,
            "📢 <b>KANAL QO‘SHISH</b>\n\n"
            "Format: <code>ChatID | Nomi | @username_yoki_link | TargetLimit(ixtiyoriy)</code>\n"
            "Misol: <code>-1001234567890 | Anime News | @animenews | 1000</code>",
        )
        return
    async with get_session() as session:
        if callback_data.act == "tog":
            await CommunityService.toggle_channel(session, callback_data.tid)
            await session.commit()
        elif callback_data.act == "del":
            await CommunityService.delete_channel(session, callback_data.tid)
            await session.commit()
        channels = await CommunityService.list_channels(session)
    lines = ["📢 <b>MAJBURIY OBUNA KANALLARI</b>\n"]
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="➕ Kanal qo‘shish", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="channels", act="add").pack()))
    for ch in channels:
        st = "🟢" if ch.is_active else "🔴"
        lim = f" · Limit: {ch.target_subscribers}" if ch.target_subscribers else ""
        lines.append(f"{st} <b>{esc(ch.title)}</b> (<code>{ch.chat_id}</code>){lim}")
        b.row(
            InlineKeyboardButton(text=f"{st} {ch.title[:22]}", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="channels", act="tog", tid=ch.id).pack()),
            InlineKeyboardButton(text="🗑", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="channels", act="del", tid=ch.id).pack()),
        )
    if not channels:
        lines.append("📭 Majburiy kanallar yo‘q.")
    b.row(_back_home_btn(callback.from_user.id))
    await edit_or_answer(callback, "\n".join(lines), reply_markup=b.as_markup())


@router.message(AdminV2States.waiting_channel_add, NotCommandFilter())
async def fsm_v2_channel_add(message: Message, state: FSMContext) -> None:
    parts = [p.strip() for p in (message.text or "").split("|")]
    if len(parts) < 3 or not parts[0].lstrip("-").isdigit():
        await message.answer("⚠️ Format: <code>-1001234567890 | Nomi | @username</code>")
        return
    chat_id = int(parts[0])
    title = parts[1]
    ref = parts[2]
    uname = ref.lstrip("@") if not ref.startswith("http") else None
    link = ref if ref.startswith("http") else f"https://t.me/{ref.lstrip('@')}"
    target = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else None
    await state.clear()
    async with get_session() as session:
        await CommunityService.add_channel(session, chat_id, title, uname, link, message.from_user.id, target)
        await session.commit()
    await message.answer(f"✅ Kanal qo‘shildi: <b>{esc(title)}</b>", reply_markup=admin_v2_home_kb(message.from_user.id, resolve_admin_perms(message.from_user, None)))


# ---------------------------------------------------------------------------
# RANKS MANAGER (sec="ranks")
# ---------------------------------------------------------------------------
@router.callback_query(AdmV2Cb.filter(F.sec == "ranks"))
async def cb_v2_ranks(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "ranks"):
        return
    if callback_data.act == "edit":
        await state.set_state(AdminV2States.waiting_rank_input)
        await state.update_data(owner_tg_id=callback.from_user.id)
        await edit_or_answer(
            callback,
            "⭐ <b>RANK TAHRIRLASH / QO‘SHISH</b>\n\n"
            "Ehtimolliklar yig‘indisi <b>100%</b> bo‘lishi shart!\n"
            "Format (har qatorda bittadan yoki bitta rank):\n"
            "<code>edit | Common | 50 | 1000 | 3</code>\n"
            "Yoki yangi rank qo‘shish: <code>add | Celestial | 🌟 | 2 | 50000 | 1</code>",
        )
        return
    async with get_session() as session:
        ranks = await GameplayService.get_rank_definitions(session)
    lines = ["⭐ <b>RANKLAR TIZIMI (V2)</b>\n"]
    total_p = 0
    for r in ranks:
        total_p += int(r.get("drop_probability", 0))
        lines.append(
            f"{r.get('emoji', '⭐')} <b>{esc(r['name'])}</b> — {r.get('drop_probability')}% · "
            f"💰 {int(r.get('base_value', 0)):,} · 🔁 {r.get('drop_limit')} ta"
        )
    lines.append(f"\nJami ehtimollik: <b>{total_p}%</b>")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Rank tahrirlash / qo‘shish", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="ranks", act="edit").pack())],
            [_back_home_btn(callback.from_user.id)],
        ]
    )
    await edit_or_answer(callback, "\n".join(lines), reply_markup=kb)


@router.message(AdminV2States.waiting_rank_input, NotCommandFilter())
async def fsm_v2_rank_input(message: Message, state: FSMContext) -> None:
    parts = [p.strip() for p in (message.text or "").split("|")]
    cmd = parts[0].lower() if parts else ""
    try:
        async with get_session() as session:
            if cmd == "edit" and len(parts) >= 5:
                await GameplayService.update_rank_field(session, parts[1], int(parts[2]), int(parts[3]), int(parts[4]))
            elif cmd == "add" and len(parts) >= 6:
                await GameplayService.add_rank(session, parts[1], parts[2], int(parts[3]), int(parts[4]), int(parts[5]))
            else:
                await message.answer("⚠️ Noto‘g‘ri format. Misol: <code>edit | Common | 50 | 1000 | 3</code>")
                return
            await session.commit()
    except ValueError as exc:
        await message.answer(f"⚠️ {esc(str(exc))}")
        return
    await state.clear()
    await message.answer("✅ Rank sozlamalari saqlandi!", reply_markup=admin_v2_home_kb(message.from_user.id, resolve_admin_perms(message.from_user, None)))


# ---------------------------------------------------------------------------
# QUIZ & GAMES & AUCTION & SEASON & REKLAMA
# ---------------------------------------------------------------------------
@router.callback_query(AdmV2Cb.filter(F.sec == "quiz"))
async def cb_v2_quiz(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "quiz"):
        return
    if callback_data.act == "add":
        await state.set_state(AdminV2States.waiting_quiz_create)
        await edit_or_answer(callback, "🧠 Yangi quiz formati: <code>Nomi | CoinMukofot | XP</code>\nMisol: <code>Naruto Viktorinasi | 500 | 40</code>")
        return
    if callback_data.act == "add_q":
        await state.set_state(AdminV2States.waiting_quiz_question)
        await edit_or_answer(callback, "➕ Savol formati:\n<code>QuizID | Savol matni | Var1;Var2;Var3;Var4 | To‘g‘riRaqam(1-4)</code>")
        return
    async with get_session() as session:
        if callback_data.act == "tog":
            q = await session.get(Quiz, callback_data.tid)
            if q:
                q.is_active = not q.is_active
                q.status = "active" if q.is_active else "ended"
                await session.commit()
        quizzes = (await session.execute(select(Quiz).order_by(Quiz.id.desc()).limit(10))).scalars().all()
    lines = ["🧠 <b>QUIZ BOSHQARUVI</b>\n"]
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="➕ Yangi Quiz", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="quiz", act="add").pack()),
        InlineKeyboardButton(text="➕ Savol qo‘shish", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="quiz", act="add_q").pack()),
    )
    for q in quizzes:
        st = "🟢" if q.is_active else "⚪"
        lines.append(f"{st} <b>#{q.id} {esc(q.title)}</b> — {q.question_count} savol · {q.reward_coins:,} 🪙")
        b.row(InlineKeyboardButton(text=f"{st} #{q.id} {q.title[:22]}", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="quiz", act="tog", tid=q.id).pack()))
    b.row(_back_home_btn(callback.from_user.id))
    await edit_or_answer(callback, "\n".join(lines), reply_markup=b.as_markup())


@router.message(AdminV2States.waiting_quiz_create, NotCommandFilter())
async def fsm_v2_quiz_create(message: Message, state: FSMContext) -> None:
    parts = [p.strip() for p in (message.text or "").split("|")]
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("⚠️ Format: <code>Nomi | CoinMukofot | XP</code>")
        return
    xp = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 30
    await state.clear()
    async with get_session() as session:
        q = await GameplayService.create_quiz(session, parts[0], int(parts[1]), message.from_user.id, reward_xp=xp)
        await session.commit()
    await message.answer(f"✅ Quiz #{q.id} yaratildi! Endi savol qo‘shishingiz mumkin.", reply_markup=admin_v2_home_kb(message.from_user.id, resolve_admin_perms(message.from_user, None)))


@router.message(AdminV2States.waiting_quiz_question, NotCommandFilter())
async def fsm_v2_quiz_question(message: Message, state: FSMContext) -> None:
    parts = [p.strip() for p in (message.text or "").split("|")]
    if len(parts) < 4 or not parts[0].isdigit() or not parts[3].isdigit():
        await message.answer("⚠️ Format: <code>QuizID | Savol | V1;V2;V3;V4 | 1</code>")
        return
    opts = [o.strip() for o in parts[2].split(";") if o.strip()]
    if len(opts) < 2:
        await message.answer("⚠️ Kamida 2 ta variant yozing (`;`).")
        return
    await state.clear()
    async with get_session() as session:
        qq = await GameplayService.add_quiz_question(session, int(parts[0]), parts[1], opts, int(parts[3]) - 1)
        await session.commit()
    if qq is None:
        await message.answer("❌ Quiz ID topilmadi.")
    else:
        await message.answer("✅ Savol quizga qo‘shildi!", reply_markup=admin_v2_home_kb(message.from_user.id, resolve_admin_perms(message.from_user, None)))


@router.callback_query(AdmV2Cb.filter(F.sec == "games"))
async def cb_v2_games(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "games"):
        return
    if callback_data.act == "edit":
        await state.set_state(AdminV2States.waiting_games_cfg)
        await edit_or_answer(callback, "🎮 Format: <code>KunlikLimit | CooldownDaq | JackpotCoin</code>\nMisol: <code>3 | 30 | 300</code>")
        return
    async with get_session() as session:
        cfg = await GameplayService.get_game_config(session)
        if callback_data.act == "tog":
            cfg["dice_enabled"] = not cfg.get("dice_enabled", True)
            await SettingsService.set_value(session, "game_config", cfg)
            await session.commit()
    st = "🟢 Yoqilgan" if cfg.get("dice_enabled", True) else "🔴 O‘chirilgan"
    txt = (
        f"🎮 <b>O‘YINLAR SOZLAMASI</b>\n\nHolat: {st}\n"
        f"🎲 Kunlik limit: <b>{cfg.get('dice_daily_limit')}</b>\n"
        f"⏳ Cooldown: <b>{cfg.get('dice_cooldown_minutes')} daq.</b>\n"
        f"🎉 Jackpot: <b>{cfg.get('dice_jackpot_reward')} 🪙</b>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Yoqish/O‘chirish", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="games", act="tog").pack())],
            [InlineKeyboardButton(text="✏️ Limit/Mukofotni tahrirlash", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="games", act="edit").pack())],
            [_back_home_btn(callback.from_user.id)],
        ]
    )
    await edit_or_answer(callback, txt, reply_markup=kb)


@router.message(AdminV2States.waiting_games_cfg, NotCommandFilter())
async def fsm_v2_games_cfg(message: Message, state: FSMContext) -> None:
    parts = [p.strip() for p in (message.text or "").split("|")]
    if len(parts) < 3 or not all(p.isdigit() for p in parts[:3]):
        await message.answer("⚠️ Format: <code>3 | 30 | 300</code>")
        return
    await state.clear()
    async with get_session() as session:
        cfg = await GameplayService.get_game_config(session)
        cfg["dice_daily_limit"] = max(1, int(parts[0]))
        cfg["dice_cooldown_minutes"] = max(1, int(parts[1]))
        cfg["dice_jackpot_reward"] = max(10, int(parts[2]))
        await SettingsService.set_value(session, "game_config", cfg)
        await session.commit()
    await message.answer("✅ O‘yin sozlamalari saqlandi!", reply_markup=admin_v2_home_kb(message.from_user.id, resolve_admin_perms(message.from_user, None)))


@router.callback_query(AdmV2Cb.filter(F.sec == "auction"))
async def cb_v2_auction(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "auction"):
        return
    if callback_data.act in ("new", "new_card"):
        await state.set_state(AdminV2States.waiting_auction_create)
        pre = f"{callback_data.tid} | " if callback_data.tid else "CardID | "
        await edit_or_answer(callback, f"🔨 <b>YANGI AUKSION</b>\n\nFormat: <code>{pre}BoshlangichNarx | Soat | MinQadam</code>\nMisol: <code>1 | 2000 | 24 | 100</code>")
        return
    async with get_session() as session:
        items, total = await AuctionWishlistService.list_active_auctions(session, page=1, page_size=15)
    lines = [f"🔨 <b>AUKSIONLAR BOSHQARUVI</b> ({total} ta faol)\n"]
    for auc, c in items:
        lines.append(f"• <b>#{auc.id}</b> {esc(c.character_name)} — {auc.current_price:,} 🪙 ({auc.bid_count} stavka)")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Yangi Auksion", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="auction", act="new").pack())],
            [_back_home_btn(callback.from_user.id)],
        ]
    )
    await edit_or_answer(callback, "\n".join(lines), reply_markup=kb)


@router.message(AdminV2States.waiting_auction_create, NotCommandFilter())
async def fsm_v2_auction_create(message: Message, state: FSMContext) -> None:
    parts = [p.strip() for p in (message.text or "").split("|")]
    if len(parts) < 3 or not all(p.isdigit() for p in parts[:3]):
        await message.answer("⚠️ Format: <code>CardID | Narx | Soat | Qadam</code>")
        return
    step = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 50
    await state.clear()
    async with get_session() as session:
        ok, msg, auc = await AuctionWishlistService.create_auction(
            session, int(parts[0]), int(parts[1]), int(parts[2]), message.from_user.id, min_step=step
        )
        if ok:
            await session.commit()
        else:
            await session.rollback()
    await message.answer(msg + (f" (ID: #{auc.id})" if auc else ""), reply_markup=admin_v2_home_kb(message.from_user.id, resolve_admin_perms(message.from_user, None)))


@router.callback_query(AdmV2Cb.filter(F.sec == "season"))
async def cb_v2_season(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "season"):
        return
    if callback_data.act == "new":
        await state.set_state(AdminV2States.waiting_season_create)
        await edit_or_answer(callback, "🏆 Yangi mavsum: <code>Nomi | Kunlar | Top1Mukofot</code>\nMisol: <code>Season 2: Eclipse | 30 | 15000</code>")
        return
    async with get_session() as session:
        s = await GameplayService.get_active_season(session)
        top = await GameplayService.get_season_leaderboard(session, s.id, 10) if s else []
    if s:
        lines = [f"🏆 <b>{esc(s.name)}</b> (#{s.season_number})\n⏳ {ensure_utc(s.ends_at):%d.%m.%Y %H:%M} UTC\n"]
        for i, (sp, u) in enumerate(top, 1):
            lines.append(f"{i}. {esc(u.display_name)} — {sp.points} ball")
    else:
        lines = ["🏆 Faol mavsum yo‘q."]
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Yangi Mavsum Boshlash", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="season", act="new").pack())],
            [_back_home_btn(callback.from_user.id)],
        ]
    )
    await edit_or_answer(callback, "\n".join(lines), reply_markup=kb)


@router.message(AdminV2States.waiting_season_create, NotCommandFilter())
async def fsm_v2_season_create(message: Message, state: FSMContext) -> None:
    parts = [p.strip() for p in (message.text or "").split("|")]
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("⚠️ Format: <code>Nomi | Kunlar | Top1Mukofot</code>")
        return
    prize = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 10000
    await state.clear()
    async with get_session() as session:
        s = await GameplayService.create_season(session, parts[0], int(parts[1]), prize)
        await session.commit()
    await message.answer(f"✅ Yangi mavsum #{s.season_number} ({esc(s.name)}) boshlandi!", reply_markup=admin_v2_home_kb(message.from_user.id, resolve_admin_perms(message.from_user, None)))


@router.callback_query(AdmV2Cb.filter(F.sec == "reklama"))
async def cb_v2_reklama(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "reklama"):
        return
    if callback_data.act == "add":
        await state.set_state(AdminV2States.waiting_ad_create)
        await edit_or_answer(callback, "📣 Reklama formati: <code>Sarlavha | DaqiqaOraliq | Placement(all/private) | Matn</code>")
        return
    async with get_session() as session:
        if callback_data.act == "tog":
            ad = await session.get(AdCampaign, callback_data.tid)
            if ad:
                ad.is_active = not ad.is_active
                await session.commit()
        ads = await CommunityService.list_ads(session)
    lines = ["📣 <b>REKLAMA KAMPANIYALARI</b>\n"]
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="➕ Reklama qo‘shish", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="reklama", act="add").pack()))
    for a in ads[:10]:
        st = "🟢" if a.is_active else "🔴"
        lines.append(f"{st} <b>{esc(a.title)}</b> — 👁 {a.views_count} ({a.placement})")
        b.row(InlineKeyboardButton(text=f"{st} {a.title[:22]}", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="reklama", act="tog", tid=a.id).pack()))
    b.row(_back_home_btn(callback.from_user.id))
    await edit_or_answer(callback, "\n".join(lines), reply_markup=b.as_markup())


@router.message(AdminV2States.waiting_ad_create, NotCommandFilter())
async def fsm_v2_ad_create(message: Message, state: FSMContext) -> None:
    parts = [p.strip() for p in (message.text or "").split("|", 3)]
    if len(parts) < 4 or not parts[1].isdigit():
        await message.answer("⚠️ Format: <code>Sarlavha | 120 | all | Reklama matni</code>")
        return
    await state.clear()
    async with get_session() as session:
        await CommunityService.create_ad(session, parts[0], parts[3], message.from_user.id, placement=parts[2], frequency_mins=int(parts[1]))
        await session.commit()
    await message.answer("✅ Reklama kampaniyasi qo‘shildi!", reply_markup=admin_v2_home_kb(message.from_user.id, resolve_admin_perms(message.from_user, None)))


# ---------------------------------------------------------------------------
# MEMORY / BACKUP & RESTORE (sec="memory")
# ---------------------------------------------------------------------------
@router.callback_query(AdmV2Cb.filter(F.sec == "memory"))
async def cb_v2_memory(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "memory"):
        return
    if callback_data.act == "backup_now":
        async with get_session() as session:
            rec = await SecurityBackupService.create_full_backup_zip(session, "manual", callback.from_user.id)
            await session.commit()
        try:
            await callback.bot.send_document(
                callback.from_user.id,
                FSInputFile(rec.storage_path),
                caption=f"💾 <b>To‘liq ZIP Backup</b>\n📦 {rec.filename} ({rec.file_size_bytes:,} bytes)",
            )
            await callback.answer("✅ Backup yaratildi va yuborildi!", show_alert=True)
        except Exception as exc:
            logger.warning("Failed sending backup document: %s", exc)
            await callback.answer(f"✅ Backup saqlandi: {rec.filename}", show_alert=True)
        return
    if callback_data.act == "clean":
        async with get_session() as session:
            n = await SecurityBackupService.cleanup_old_backups(session, 14)
            await session.commit()
        await callback.answer(f"🧹 {n} ta eski backup o‘chirildi.", show_alert=True)
    if callback_data.act == "restore":
        if not is_super_admin(callback.from_user):
            await callback.answer("⛔ Faqat Super Admin tiklay oladi.", show_alert=True)
            return
        await state.set_state(AdminV2States.waiting_backup_restore)
        await edit_or_answer(callback, "♻️ <b>BACKUP RESTORE</b>\n\nAvval joriy bazadan avtomatik pre-restore backup olinadi.\n<code>.zip</code> backup faylini yuboring (<i>/cancel</i>):")
        return

    async with get_session() as session:
        backups = await SecurityBackupService.list_backups(session, limit=8)
    lines = ["💾 <b>XOTIRA VA BACKUP TIZIMI</b>\n"]
    for b_rec in backups:
        lines.append(f"• <code>{esc(b_rec.filename)}</code> — {b_rec.file_size_bytes // 1024} KB ({b_rec.backup_type})")
    if not backups:
        lines.append("📭 Hozircha backuplar yo‘q.")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💾 Hozir Backup olish (.zip)", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="memory", act="backup_now").pack())],
            [
                InlineKeyboardButton(text="🧹 Eski backuplarni tozalash", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="memory", act="clean").pack()),
                InlineKeyboardButton(text="♻️ Restore (.zip)", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="memory", act="restore").pack()),
            ],
            [_back_home_btn(callback.from_user.id)],
        ]
    )
    await edit_or_answer(callback, "\n".join(lines), reply_markup=kb)


@router.message(AdminV2States.waiting_backup_restore, F.document)
async def fsm_v2_backup_restore(message: Message, state: FSMContext) -> None:
    if not is_super_admin(message.from_user):
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


# ---------------------------------------------------------------------------
# SUPPORT & REPORT & SECURITY & LOGS & SCHEDULED MESSAGE
# ---------------------------------------------------------------------------
@router.callback_query(AdmV2Cb.filter(F.sec.in_({"support", "report"})))
async def cb_v2_support(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    sec = callback_data.sec
    if await _deny_if_no_perm(callback, admin_perms, sec):
        return
    if callback_data.act == "reply":
        await state.set_state(AdminV2States.waiting_support_reply)
        await state.update_data(owner_tg_id=callback.from_user.id, ticket_id=callback_data.tid)
        await edit_or_answer(callback, f"✍️ Murojaat <b>#{callback_data.tid}</b> uchun javob matnini yozing:")
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
            lines = [f"💬 <b>Ticket #{t.id}</b> ({t.status}) — 👤 {esc(u.display_name if u else t.user_id)}\n<b>{esc(t.subject)}</b>\n"]
            for m in msgs[-10:]:
                lines.append(f"<b>{m.sender_type}:</b> {esc(m.body)}")
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✍️ Javob berish", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec=sec, act="reply", tid=t.id).pack()),
                        InlineKeyboardButton(text="🔒 Yopish", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec=sec, act="close", tid=t.id).pack()),
                    ],
                    [InlineKeyboardButton(text="⬅️ Ro‘yxat", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec=sec, act="v").pack())],
                ]
            )
            await edit_or_answer(callback, "\n".join(lines), reply_markup=kb)
            return
        tickets = await CommunityService.list_tickets(session, limit=15)
    b = InlineKeyboardBuilder()
    lines = ["💬 <b>SUPPORT VA SHIKOYATLAR</b>\n"]
    for t in tickets:
        ico = "🟡" if t.status == "open" else ("🟢" if t.status == "answered" else "🔴")
        lines.append(f"{ico} <b>#{t.id}</b> {esc(t.subject)}")
        b.row(InlineKeyboardButton(text=f"{ico} #{t.id} {t.subject[:28]}", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec=sec, act="det", tid=t.id).pack()))
    b.row(_back_home_btn(callback.from_user.id))
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
    await message.answer(f"✅ Javob yuborildi (Ticket #{tid})!")


@router.callback_query(AdmV2Cb.filter(F.sec == "security"))
async def cb_v2_security(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "security"):
        return
    if callback_data.act == "ban_prompt":
        await state.set_state(AdminV2States.waiting_security_ban)
        await edit_or_answer(
            callback,
            "🔐 <b>TEMP / PERM BAN</b>\n\n"
            "Format: <code>user/group | TG_ID_yoki_ChatID | Soat(0=umrbod) | Sabab</code>\n"
            "Misol: <code>user | 123456789 | 24 | Auto-clicker</code>",
        )
        return
    async with get_session() as session:
        maint = await SettingsService.get_value(session, "maintenance", {"enabled": False})
        if callback_data.act == "maint":
            maint["enabled"] = not bool(maint.get("enabled", False))
            await SettingsService.set_value(session, "maintenance", maint)
            await CommunityService.log_audit(session, callback.from_user.id, "toggle_maintenance", "settings", new_value=maint)
            await session.commit()
        elif callback_data.act == "res":
            inc = await session.get(SecurityIncident, callback_data.tid)
            if inc:
                inc.is_resolved = True
                await session.commit()
        incidents = await SecurityBackupService.list_incidents(session, unresolved_only=True, limit=10)
    m_txt = "🔴 YOQILGAN" if maint.get("enabled") else "🟢 O‘chirilgan"
    lines = [f"🔐 <b>XAVFSIZLIK VA ANTI-CHEAT</b>\n\n🛠 Texnik rejim: {m_txt}\n\n🚨 <b>Faol hodisalar ({len(incidents)}):</b>"]
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="🛠 Texnik rejim (+/-)", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="security", act="maint").pack()),
        InlineKeyboardButton(text="🚫 Temp/Perm Ban", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="security", act="ban_prompt").pack()),
    )
    for inc in incidents:
        lines.append(f"• #{inc.id} [{inc.severity}] {esc(inc.description[:70])}")
        b.row(InlineKeyboardButton(text=f"✅ Yopish #{inc.id}", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="security", act="res", tid=inc.id).pack()))
    b.row(_back_home_btn(callback.from_user.id))
    await edit_or_answer(callback, "\n".join(lines), reply_markup=b.as_markup())


@router.message(AdminV2States.waiting_security_ban, NotCommandFilter())
async def fsm_v2_security_ban(message: Message, state: FSMContext) -> None:
    parts = [p.strip() for p in (message.text or "").split("|", 3)]
    if len(parts) < 4 or not parts[1].lstrip("-").isdigit() or not parts[2].isdigit():
        await message.answer("⚠️ Format: <code>user | 123456789 | 24 | Sabab</code>")
        return
    kind, target_id, hrs, reason = parts[0].lower(), int(parts[1]), int(parts[2]), parts[3]
    hours = hrs if hrs > 0 else None
    await state.clear()
    async with get_session() as session:
        if kind == "group":
            grp = (await session.execute(select(Group).where(Group.telegram_chat_id == target_id))).scalar_one_or_none()
            if grp is None:
                await message.answer("❌ Guruh topilmadi.")
                return
            await SecurityBackupService.ban_group(session, message.from_user.id, grp, reason, hours=hours)
        else:
            u = await UserService.find_user(session, str(target_id))
            if u is None:
                await message.answer("❌ Foydalanuvchi topilmadi.")
                return
            await SecurityBackupService.ban_user(session, message.from_user.id, u, reason, hours=hours)
        await session.commit()
    await message.answer("✅ Ban qo‘llandi!", reply_markup=admin_v2_home_kb(message.from_user.id, resolve_admin_perms(message.from_user, None)))


@router.callback_query(AdmV2Cb.filter(F.sec == "logs"))
async def cb_v2_logs(callback: CallbackQuery, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "logs"):
        return
    async with get_session() as session:
        logs = await CommunityService.get_recent_logs(session, limit=20)
    lines = ["📋 <b>AUDIT LOGS (OXIRGI 20 TA)</b>\n"]
    for lg in logs:
        dt = ensure_utc(lg.created_at).strftime("%d.%m %H:%M")
        lines.append(f"• <code>{dt}</code> | <code>{lg.admin_telegram_id}</code> | <b>{esc(lg.action_type)}</b> → {esc(lg.target_type)}:{esc(lg.target_id or '')}")
    if not logs:
        lines.append("📭 Loglar bo‘sh.")
    kb = InlineKeyboardMarkup(inline_keyboard=[[_back_home_btn(callback.from_user.id)]])
    await edit_or_answer(callback, "\n".join(lines), reply_markup=kb)


@router.callback_query(AdmV2Cb.filter(F.sec == "message"))
async def cb_v2_message_menu(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "message"):
        return
    if callback_data.act == "sched":
        await state.set_state(AdminV2States.waiting_scheduled_msg)
        await edit_or_answer(callback, "⏰ <b>REJALI XABAR</b>\n\nFormat: <code>YYYY-MM-DD HH:MM | all/users/groups | Sarlavha | Matn</code> (UTC)")
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Hozir barchaga yuborish", callback_data=AdminCb(owner_id=callback.from_user.id, section="broadcast", action="prompt").pack())],
            [InlineKeyboardButton(text="⏰ Vaqtga belgilash (Scheduled)", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="message", act="sched").pack())],
            [_back_home_btn(callback.from_user.id)],
        ]
    )
    await edit_or_answer(callback, "📨 <b>XABARLAR VA BROADCAST</b>", reply_markup=kb)


@router.message(AdminV2States.waiting_scheduled_msg, NotCommandFilter())
async def fsm_v2_scheduled_msg(message: Message, state: FSMContext) -> None:
    parts = [p.strip() for p in (message.text or "").split("|", 3)]
    if len(parts) < 4:
        await message.answer("⚠️ Format: <code>2026-12-01 14:00 | all | Sarlavha | Matn</code>")
        return
    try:
        dt = datetime.strptime(parts[0], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        await message.answer("⚠️ Sana formati: <code>YYYY-MM-DD HH:MM</code>")
        return
    await state.clear()
    async with get_session() as session:
        await CommunityService.create_scheduled_message(session, parts[2], parts[3], dt, parts[1], message.from_user.id)
        await session.commit()
    await message.answer(f"✅ Xabar <code>{parts[0]} UTC</code> ga rejalashtirildi!", reply_markup=admin_v2_home_kb(message.from_user.id, resolve_admin_perms(message.from_user, None)))
