"""Kartalar: qo‘shish (qo‘lda va ZIP), ko‘rish, tahrirlash, o‘chirish, qidirish, tiklash."""

import io
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from admin.filters import IsAdminFilter, has_admin_perm, resolve_admin_perms
from admin.v2.common import AdminV2States, _cancel_kb, _deny_if_no_perm
from database.session import get_session
from keyboards.v2 import (
    admin_card_give_users_kb,
    admin_v2_add_choice_kb,
    admin_v2_card_detail_kb,
    admin_v2_cards_action_list_kb,
    admin_v2_cards_menu_kb,
    admin_v2_edit_fields_kb,
    admin_v2_media_titles_kb,
    admin_v2_title_cards_kb,
    admin_v2_view_cards_kb,
)
from models.card import Card
from models.user import User
from models.user_card import UserCard
from services.admin_service import AdminService
from services.auction_wishlist_service import AuctionWishlistService
from services.card_level_service import CardLevelService
from services.card_service import CardService
from services.community_service import CommunityService
from services.event_service import EventService
from services.gameplay_service import GameplayService
from services.security_backup_service import SecurityBackupService
from utils import admin_nav
from utils.callback_data import AdmV2Cb, AdminCb
from utils.constants import MEDIA_TYPES, PAGE_SIZE, get_rarity_meta, sync_runtime_ranks
from utils.formatting import esc, format_card_about, format_card_block
from utils.image_check import validate_image_url
from utils.tg_helpers import NotCommandFilter, edit_or_answer, is_super_admin, safe_delete, send_card_message
from utils.timeutils import fmt_tashkent

logger = logging.getLogger(__name__)
router = Router(name="admin_v2_cards")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


# ===========================================================================
# 1. CARDS V2 (Add choice, ZIP, View Tabs, Media Titles, Edit, Delete, Search, Restore)
# ===========================================================================
@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "v")))
async def cb_v2_cards_menu(callback: CallbackQuery, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    await state.clear()
    perms = resolve_admin_perms(callback.from_user, admin_perms)
    await edit_or_answer(
        callback,
        "🎴 <b>KARTALAR MARKAZI</b>\n━━━━━━━━━━━━━━━\nKerakli bo‘limni tanlang:",
        reply_markup=admin_v2_cards_menu_kb(callback.from_user.id, perms),
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
        "⚠️ <b>Hajm chegarasi: 20 MB.</b> Telegram botlarga shundan katta fayl bermaydi. "
        "Katta bo‘lsa ZIP ni bir necha qismga bo‘lib (masalan, har bir rank alohida) ketma-ket yuboring — "
        "bot hammasini birin-ketin qabul qiladi.\n\n"
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


TG_DOWNLOAD_LIMIT = 20 * 1024 * 1024  # Telegram Bot API: bot yuklab ola oladigan eng katta fayl


def _zip_next_kb(owner_id: int) -> InlineKeyboardMarkup:
    """ZIP qabul qilingandan keyin: yana ZIP yuborish mumkin yoki tugatish."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Tugatish", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="v").pack())]
        ]
    )


_ZIP_TOO_BIG_TEXT = (
    "⚠️ <b>Fayl juda katta.</b> Telegram botlarga <b>20 MB</b> dan katta fayl yuklab olishga ruxsat bermaydi.\n\n"
    "Nima qilish kerak:\n"
    "1️⃣ ZIP ni bir nechta kichik qismga bo‘ling (masalan, har bir rank yoki har 30–50 karta alohida ZIP)\n"
    "2️⃣ Rasmlarni kichraytiring (PNG o‘rniga JPG/WEBP)\n"
    "3️⃣ Qismlarni ketma-ket shu yerga yuboring — bot har birini qabul qiladi\n\n"
    "Tayyor bo‘lgach «Tugatish» tugmasini bosing."
)


@router.message(AdminV2States.waiting_zip_cards, F.document)
async def fsm_v2_zip_upload(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    owner_id = message.from_user.id
    next_kb = _zip_next_kb(owner_id)
    doc = message.document
    if not doc or not (doc.file_name or "").lower().endswith(".zip"):
        await message.answer("⚠️ Faqat <code>.zip</code> fayl yuboring.", reply_markup=next_kb)
        return
    if (doc.file_size or 0) >= TG_DOWNLOAD_LIMIT:
        # Holat saqlanadi: admin keyingi (kichik) ZIP ni darhol yuborishi mumkin
        await message.answer(_ZIP_TOO_BIG_TEXT, reply_markup=next_kb)
        return
    wait_msg = await message.answer("⏳ ZIP yuklanmoqda va tekshirilmoqda...")

    buf = io.BytesIO()
    try:
        await message.bot.download(doc, destination=buf)
    except TelegramBadRequest as exc:
        too_big = "too big" in str(exc).lower()
        await wait_msg.edit_text(
            _ZIP_TOO_BIG_TEXT if too_big else f"❌ Faylni yuklab bo‘lmadi: <code>{esc(str(exc)[:150])}</code>",
            reply_markup=next_kb,
        )
        return

    async def _progress(text: str) -> None:
        await wait_msg.edit_text(text)

    try:
        async with get_session() as session:
            rep = await SecurityBackupService.import_cards_from_zip(
                session, buf.getvalue(), owner_id, message.bot, _progress
            )
            await session.commit()
    except Exception as exc:  # kutilmagan xato
        logger.exception("ZIP import crashed")
        await wait_msg.edit_text(f"❌ ZIP import to‘xtadi: <code>{esc(str(exc)[:200])}</code>", reply_markup=next_kb)
        return

    if rep.fatal:
        await wait_msg.edit_text(f"❌ <b>QO‘SHILMADI</b>\n\nSabab: {esc(rep.fatal)}", reply_markup=next_kb)
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
    lines.append("\n📤 Yana ZIP bo‘lsa, shu yerga yuboring. Tugagan bo‘lsa «Tugatish» ni bosing.")
    await wait_msg.edit_text("\n".join(lines), reply_markup=next_kb)
    if len(rep.failed_files) > 10:
        from aiogram.types import BufferedInputFile

        await message.answer_document(
            BufferedInputFile("\n".join(rep.failed_files).encode("utf-8"), filename="qoshilmaganlar.txt"),
            caption="❌ Qo‘shilmagan kartalarning to‘liq ro‘yxati",
        )


_TAB_TITLES = {
    "sent_group": "GURUHGA TUSHGANLAR (oxirgi yuborilganlar yuqorida)",
    "not_sent": "HALI TUSHMAGANLAR (guruhga tashlanmagan)",
    "owned": "FOYDALANUVCHILARDA BOR KARTALAR",
    "in_auction": "AUKSIONDA TURGAN KARTALAR",
    "anime": "ANIME KARTALARI",
    "manga": "MANGA / MANHWA / MANHUA",
    "movie": "KINO / SERIAL",
    "cartoon": "MULTFILM / KOMIKS",
    "event": "EVENT KARTALARI",
    "other": "BOSHQA KARTALAR",
    "deleted": "O‘CHIRILGAN KARTALAR",
    "all": "BARCHA KARTALAR",
}


async def _render_v2_cards_tab(target, owner_id: int, tab: str = "all", page: int = 1) -> None:
    page = max(1, page)
    async with get_session() as session:
        cards, total = await AdminService.get_v2_cards_by_tab(session, tab=tab, page=page, page_size=PAGE_SIZE)
    if not cards and total > 0 and page > 1:
        page = 1
        async with get_session() as session:
            cards, total = await AdminService.get_v2_cards_by_tab(session, tab=tab, page=1, page_size=PAGE_SIZE)
    admin_nav.remember(owner_id, "view_tabs", ext=tab, p=page)
    mode = admin_nav.MODE_VIEW if tab == "deleted" else admin_nav.get_mode(owner_id)
    mode_tag = "" if mode == admin_nav.MODE_VIEW else f"{admin_nav.MODE_TITLES.get(mode, '')} · "
    txt = f"👁 <b>{mode_tag}{_TAB_TITLES.get(tab, tab.upper())}</b> (Jami: <b>{total}</b> ta | 20/sahifa)"
    if not cards:
        txt += "\n\n📭 Bu bo‘limda kartalar yo‘q."
    await edit_or_answer(
        target,
        txt,
        reply_markup=admin_v2_view_cards_kb(owner_id, cards, tab, page, total, page_size=PAGE_SIZE),
    )


async def _render_v2_media_titles(target, owner_id: int, mgroup: str = "anime", page: int = 1) -> None:
    page = max(1, page)
    async with get_session() as session:
        titles, total = await AdminService.get_v2_media_titles(session, media_group=mgroup, page=page, page_size=PAGE_SIZE)
    admin_nav.remember(owner_id, "titles", ext=mgroup, p=page)
    txt = f"📁 <b>{esc(mgroup.upper())} NOMLARI</b> (Karta soni bo‘yicha | Jami: <b>{total}</b> ta)\n\nIchiga kirish uchun nomni tanlang:"
    if not titles:
        txt = f"📁 <b>{esc(mgroup.upper())} NOMLARI</b>\n\n📭 Hozircha kartalar yo‘q."
    await edit_or_answer(
        target,
        txt,
        reply_markup=admin_v2_media_titles_kb(owner_id, titles, mgroup, page, total, page_size=PAGE_SIZE),
    )


async def _render_v2_title_cards(target, owner_id: int, rep_card_id: int, mgroup: str = "anime", page: int = 1) -> None:
    page = max(1, page)
    async with get_session() as session:
        title, cards, total = await AdminService.get_v2_cards_by_media_title(
            session, media_group=mgroup, rep_card_id=rep_card_id, page=page, page_size=PAGE_SIZE
        )
    admin_nav.remember(owner_id, "mtitle", tid=rep_card_id, ext=mgroup, p=page)
    txt = f"🎬 <b>{esc(title or mgroup.upper())}</b> (Jami: <b>{total}</b> ta karta)\n\nTahrirlash yoki ko‘rish uchun kartani tanlang:"
    if not cards:
        txt += "\n\n📭 Topilmadi."
    await edit_or_answer(
        target,
        txt,
        reply_markup=admin_v2_title_cards_kb(owner_id, cards, rep_card_id, mgroup, page, total, page_size=PAGE_SIZE),
    )


async def _render_v2_action_list(target, owner_id: int, mode: str, page: int = 1) -> None:
    page = max(1, page)
    expected = admin_nav.MODE_EDIT if mode == "edit_list" else admin_nav.MODE_DEL
    if admin_nav.get_mode(owner_id) != expected:
        admin_nav.set_mode(owner_id, expected)
    admin_nav.remember(owner_id, mode, p=page)
    async with get_session() as session:
        cards, total = await AdminService.get_v2_cards_by_tab(session, tab="all", page=page, page_size=PAGE_SIZE)
    hdr = "✏️ <b>KARTA TAHRIRLASH</b>" if mode == "edit_list" else "🗑 <b>KARTA O‘CHIRISH</b>"
    txt = f"{hdr} (Jami: <b>{total}</b> ta | 20/sahifa)\n\nKerakli kartani tanlang yoki qidiring:"
    if not cards:
        txt += "\n\n📭 Kartalar yo‘q."
    await edit_or_answer(
        target,
        txt,
        reply_markup=admin_v2_cards_action_list_kb(owner_id, cards, mode, page, total, page_size=PAGE_SIZE),
    )


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "view_start")))
async def cb_v2_view_start(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    admin_nav.set_mode(callback.from_user.id, admin_nav.MODE_VIEW)
    await _render_v2_cards_tab(callback, callback.from_user.id, tab=callback_data.ext or "sent_group", page=max(1, callback_data.p))


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "view_tabs")))
async def cb_v2_view_cards(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    await _render_v2_cards_tab(callback, callback.from_user.id, tab=callback_data.ext or "all", page=max(1, callback_data.p))


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act.in_({"titles", "titles_v"}))))
async def cb_v2_media_titles(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    if callback_data.act == "titles_v":
        admin_nav.set_mode(callback.from_user.id, admin_nav.MODE_VIEW)
    await _render_v2_media_titles(callback, callback.from_user.id, mgroup=callback_data.ext or "anime", page=max(1, callback_data.p))


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "mtitle")))
async def cb_v2_media_title_cards(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    await _render_v2_title_cards(
        callback, callback.from_user.id, rep_card_id=callback_data.tid, mgroup=callback_data.ext or "anime", page=max(1, callback_data.p)
    )


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act.in_({"edit_list", "del_list"}))))
async def cb_v2_cards_action_list(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    await _render_v2_action_list(callback, callback.from_user.id, mode=callback_data.act, page=max(1, callback_data.p))


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
                    callback_data=admin_nav.back_callback(
                        callback.from_user.id,
                        fallback=AdmV2Cb(owner_id=callback.from_user.id, sec="cards", act="del_list", p=max(1, callback_data.p)).pack(),
                    ),
                )
            ],
        ]
    )
    if card is not None and card.image and (card.image.startswith("http") or len(card.image) > 20) and callback.message:
        cap = (
            f"{format_card_block(card, show_status=True)}\n\n"
            f"🗑 <b>KARTANI O‘CHIRISH</b>\nMavjud egalardagi nusxalar bilan qanday yo‘l tutamiz?"
        )[:1000]
        try:
            await send_card_message(callback.bot, callback.message.chat.id, card, cap, kb)
            await safe_delete(callback.bot, callback.message.chat.id, callback.message.message_id)
            await callback.answer()
            return
        except Exception:
            pass
    await edit_or_answer(
        callback,
        f"🗑 <b>KARTANI O‘CHIRISH: {esc(title)}</b>\n\nMavjud egalardagi nusxalar bilan qanday yo‘l tutamiz?",
        reply_markup=kb,
    )


async def _build_v2_card_detail_text(session, card: Card) -> str:
    owners_list, drops_sent, latest_uc = [], 0, None
    analytics: dict = {}
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
    try:
        analytics = await AdminService.get_card_admin_analytics(session, card.id)
    except Exception:
        analytics = {}
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
    an_lines = []
    wish_n = int(analytics.get("wishlist", 0))
    if wish_n > 0:
        an_lines.append(f"💖 <b>Istaklarda:</b> {wish_n} kishi")
    m_act = int(analytics.get("market_active", 0))
    if m_act > 0:
        an_lines.append(f"🛒 <b>Bozorda:</b> {m_act} ta ({analytics.get('market_min', 0):,}–{analytics.get('market_max', 0):,} 🪙)")
    s_cnt = int(analytics.get("sold_count", 0))
    if s_cnt > 0:
        an_lines.append(f"📈 <b>Sotilgan:</b> {s_cnt} ta (o‘rtacha {analytics.get('avg_sold', 0):,} 🪙)")
    if int(analytics.get("active_auction", 0)) > 0:
        an_lines.append(f"🔨 <b>Faol auksion:</b> {int(analytics['active_auction'])} ta")
    an_block = ("\n" + "\n".join(an_lines)) if an_lines else ""
    try:
        text = (
            f"{format_card_block(card, show_status=True, owner_name=owner_preview)}\n"
            f"📤 <b>Tashlangan:</b> {drops_sent} marta\n"
            f"🗓 <b>Oxirgi olingan sana:</b> <code>{claimed_dt}</code>"
            f"{preset_line}{an_block}\n"
            f"🆔 <b>Card DB ID:</b> <code>{card.id}</code>"
        )
        return text[:1000]
    except Exception:
        logger.exception("card_det: matn tuzilmadi (card %s)", card.id)
        return f"🎴 <b>{esc(card.character_name)}</b>\n📺 {esc(card.anime_name)}{preset_line}\n🆔 Card DB ID: <code>{card.id}</code>"


async def open_admin_card_detail(
    target: CallbackQuery | Message,
    owner_id: int,
    card_id: int,
    tab: str = "all",
    page: int = 1,
) -> None:
    async with get_session() as session:
        card = await session.get(Card, card_id)
        if card is None:
            if isinstance(target, CallbackQuery):
                await target.answer("Karta topilmadi.", show_alert=True)
            else:
                await target.answer("❌ Karta topilmadi.")
            return
        text = await _build_v2_card_detail_text(session, card)
    kb = admin_v2_card_detail_kb(
        owner_id,
        card,
        tab=tab or "all",
        page=max(1, page),
        is_super=is_super_admin(target.from_user),
    )
    msg_chat_id = target.message.chat.id if isinstance(target, CallbackQuery) and target.message else (
        target.chat.id if isinstance(target, Message) else None
    )
    if msg_chat_id is not None and card.image and (card.image.startswith("http") or len(card.image) > 20):
        try:
            await send_card_message(target.bot, msg_chat_id, card, text, kb)
            if isinstance(target, CallbackQuery) and target.message:
                await safe_delete(target.bot, target.message.chat.id, target.message.message_id)
                await target.answer()
            return
        except Exception:
            logger.exception("card_det: rasm yuborilmadi (card %s)", card.id)
    await edit_or_answer(target, text, reply_markup=kb)


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "card_det")))
async def cb_v2_card_detail(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    await open_admin_card_detail(
        callback,
        callback.from_user.id,
        callback_data.tid,
        tab=callback_data.ext or "all",
        page=callback_data.p,
    )


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


async def _render_v2_give_users(target, owner_id: int, card_id: int, back_tab: str = "all", page: int = 1) -> None:
    page = max(1, page)
    async with get_session() as session:
        card = await session.get(Card, card_id)
        if card is None or card.is_deleted:
            if isinstance(target, CallbackQuery):
                await target.answer("Karta topilmadi.", show_alert=True)
            else:
                await target.answer("❌ Karta topilmadi.")
            return
        users, total = await AdminService.get_users_for_card_grant(
            session, owner_id, page=page, page_size=12
        )
    preset_lvl = max(1, int(getattr(card, "preset_level", 1) or 1))
    kb = admin_card_give_users_kb(
        owner_id,
        card.id,
        users,
        page=page,
        total=total,
        current_admin_tg_id=owner_id,
        back_tab=back_tab,
        page_size=12,
    )
    txt = (
        f"🎁 <b>KARTANI USERGA BERISH</b>\n\n"
        f"🎴 Karta: <b>{esc(card.character_name)}</b> (Lvl {preset_lvl})\n"
        f"👥 Faol userlar: <b>{total}</b> ta — kimga beramiz?"
    )
    await edit_or_answer(target, txt, reply_markup=kb)


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "give_u")))
async def cb_v2_card_give_users_list(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    await _render_v2_give_users(
        callback, callback.from_user.id, callback_data.tid, back_tab=callback_data.ext or "all", page=max(1, callback_data.p)
    )


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
            eff_meta, eff_aliases = await CardService.get_effective_about(session, card)
            text = format_card_about(card, meta_override=eff_meta, aliases_override=eff_aliases)
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
    b.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=admin_nav.back_callback(
                message.from_user.id, fallback=AdmV2Cb(owner_id=message.from_user.id, sec="cards", act="v").pack()
            ),
        )
    )
    await message.answer(f"🔍 Topildi: <b>{total}</b> ta karta (20 tagacha ko‘rsatilmoqda):", reply_markup=b.as_markup())


async def open_admin_card_edit(
    target: CallbackQuery | Message,
    owner_id: int,
    card_id: int,
) -> None:
    async with get_session() as session:
        card = await session.get(Card, card_id)
    if card is None and isinstance(target, Message):
        await target.answer("❌ Karta topilmadi.")
        return
    kb = admin_v2_edit_fields_kb(owner_id, card_id)
    msg_chat_id = target.message.chat.id if isinstance(target, CallbackQuery) and target.message else (
        target.chat.id if isinstance(target, Message) else None
    )
    if card is not None and card.image and (card.image.startswith("http") or len(card.image) > 20) and msg_chat_id is not None:
        cap = (
            f"{format_card_block(card, show_status=True)}\n\n"
            f"✏️ <b>KARTA #{card.id} TAHRIRLASH</b>\nQaysi maydonni o‘zgartirasiz?"
        )[:1000]
        try:
            await send_card_message(target.bot, msg_chat_id, card, cap, kb)
            if isinstance(target, CallbackQuery) and target.message:
                await safe_delete(target.bot, target.message.chat.id, target.message.message_id)
                await target.answer()
            return
        except Exception:
            pass
    await edit_or_answer(
        target,
        f"✏️ <b>KARTA #{card_id} TAHRIRLASH</b>\n\nQaysi maydonni o‘zgartirasiz?",
        reply_markup=kb,
    )


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "edit_pick")))
async def cb_v2_edit_fields(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    await open_admin_card_edit(callback, callback.from_user.id, callback_data.tid)


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
    kb = admin_v2_edit_fields_kb(callback.from_user.id, callback_data.tid)
    if card.image and (card.image.startswith("http") or len(card.image) > 20) and callback.message:
        cap = (
            f"{format_card_block(card, show_status=True)}\n\n"
            f"✅ <b>Karta #{card.id} yangilandi!</b>"
        )[:1000]
        try:
            await send_card_message(callback.bot, callback.message.chat.id, card, cap, kb)
            await safe_delete(callback.bot, callback.message.chat.id, callback.message.message_id)
            return
        except Exception:
            pass
    await edit_or_answer(callback, f"✅ Karta #{callback_data.tid} yangilandi!", reply_markup=kb)


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
            canonical = await CardService.get_canonical_character_card(session, card)
            desc_txt = (message.text or "").strip()
            c_meta = dict(canonical.metadata_json or {})
            if desc_txt:
                c_meta["description"] = desc_txt
            else:
                c_meta.pop("description", None)
            canonical.metadata_json = c_meta
            if canonical.id != card.id and isinstance(card.metadata_json, dict) and "description" in card.metadata_json:
                sub_meta = dict(card.metadata_json)
                sub_meta.pop("description", None)
                card.metadata_json = sub_meta
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
