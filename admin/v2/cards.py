"""Kartalar: qo‘shish (qo‘lda va ZIP), ko‘rish, tahrirlash, o‘chirish, qidirish, tiklash."""

import io
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from admin.filters import resolve_admin_perms, IsAdminFilter, has_admin_perm
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
    admin_nav.set_mode(callback.from_user.id, admin_nav.MODE_VIEW)
    await edit_or_answer(
        callback,
        "🎴 <b>KARTALAR MARKAZI</b>\n━━━━━━━━━━━━━━━\n"
        "Karta bilan bog‘liq hamma narsa shu yerda: boshqarish, drop va ranklar, bot do‘koni, auksion, eventlar, komissiya.",
        reply_markup=admin_v2_cards_menu_kb(callback.from_user.id, resolve_admin_perms(callback.from_user, admin_perms)),
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


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "batch")))
async def cb_v2_batch_prompt(callback: CallbackQuery, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    from services.card_zip_import import rank_table

    async with get_session() as session:
        sync_runtime_ranks(await GameplayService.get_rank_definitions(session))
    await state.set_state(AdminV2States.waiting_batch_text)
    await state.update_data(owner_tg_id=callback.from_user.id)
    ranks_hint = ", ".join(f"{rid}={name}" for rid, name in rank_table())
    await edit_or_answer(
        callback,
        "📋 <b>MATN / CSV / JSON ORQALI KARTA QO‘SHISH (ZIP-siz)</b>\n\n"
        "Quyidagi formatlardan birida matn yuboring yoki <code>.json</code> / <code>.csv</code> / <code>.txt</code> fayl tashlang:\n\n"
        "1️⃣ <b>Har qator (| bilan):</b>\n"
        "<code>Personaj | Media turi | Asar nomi | Rank | Rasm URL (yoki file_id)</code>\n"
        "<i>(yoki 4 ustun: Personaj | Asar nomi | Rank | Rasm URL — turi Anime bo‘ladi)</i>\n\n"
        "2️⃣ <b>JSON:</b> <code>[{\"character_name\": ..., \"media_type\": \"Anime\", \"media_name\": ..., \"rank_id\": 1, \"image\": \"https://...\"}]</code>\n"
        "3️⃣ <b>CSV:</b> sarlavhali yoki 5 ustunli CSV\n\n"
        "💡 <b>JSON da rasm URL yo‘q bo‘lsa</b> — «📄 JSON + rasmlar ketma-ket» usulidan foydalaning: "
        "JSON ni tashlaysiz, rasmlarni esa botga ketma-ket yuborasiz.\n\n"
        f"⭐ <b>Ranklar:</b> {esc(ranks_hint)}\n"
        "<i>Bekor qilish: /cancel</i>",
        reply_markup=_cancel_kb(callback.from_user.id, "cards", "add_menu"),
    )


def _parse_batch_records(raw_text: str) -> tuple[list[dict], str | None]:
    import csv
    import json

    txt = (raw_text or "").strip()
    if not txt:
        return [], "Bo‘sh matn yuborildi."

    if txt.startswith("[") or txt.startswith("{"):
        try:
            parsed = json.loads(txt)
            if isinstance(parsed, dict):
                parsed = parsed.get("cards") if isinstance(parsed.get("cards"), list) else [parsed]
            if isinstance(parsed, list):
                return [dict(r) for r in parsed if isinstance(r, dict)], None
        except Exception as exc:
            return [], f"JSON o‘qishda xato: {exc}"

    lines = [ln.strip() for ln in txt.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if not lines:
        return [], "Qatorlar topilmadi."

    if any("|" in ln for ln in lines):
        out: list[dict] = []
        for idx, ln in enumerate(lines, start=1):
            parts = [p.strip() for p in ln.split("|")]
            if len(parts) == 4:
                out.append({
                    "_line": idx,
                    "character_name": parts[0],
                    "media_type": "Anime",
                    "media_name": parts[1],
                    "rank_id": parts[2],
                    "image": parts[3],
                })
            elif len(parts) >= 5:
                out.append({
                    "_line": idx,
                    "character_name": parts[0],
                    "media_type": parts[1],
                    "media_name": parts[2],
                    "rank_id": parts[3],
                    "image": parts[4],
                    "description": parts[5] if len(parts) > 5 else "",
                })
            else:
                out.append({"_line": idx, "_err": f"{idx}-qator: kamida 4 ta '|' qismi bo‘lishi kerak."})
        return out, None

    reader = list(csv.reader(io.StringIO("\n".join(lines))))
    if not reader:
        return [], "CSV ma’lumoti topilmadi."
    first_low = [c.strip().lower() for c in reader[0]]
    has_header = any(k in first_low for k in ("character_name", "character", "personaj", "anime_name", "media_name", "image"))
    out = []
    if has_header:
        dict_reader = csv.DictReader(io.StringIO("\n".join(lines)))
        for idx, row in enumerate(dict_reader, start=2):
            rec = dict(row)
            rec["_line"] = idx
            out.append(rec)
    else:
        for idx, cols in enumerate(reader, start=1):
            cols = [c.strip() for c in cols]
            if len(cols) == 4:
                out.append({"_line": idx, "character_name": cols[0], "media_type": "Anime", "media_name": cols[1], "rank_id": cols[2], "image": cols[3]})
            elif len(cols) >= 5:
                out.append({"_line": idx, "character_name": cols[0], "media_type": cols[1], "media_name": cols[2], "rank_id": cols[3], "image": cols[4]})
            else:
                out.append({"_line": idx, "_err": f"{idx}-qator: kamida 4 ustun bo‘lishi kerak."})
    return out, None


@router.message(AdminV2States.waiting_batch_text)
async def fsm_v2_batch_input(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    if message.text and message.text.strip().startswith("/"):
        return

    raw_text = (message.text or "").strip()
    if message.document:
        fname = (message.document.file_name or "").lower()
        if not fname.endswith((".json", ".csv", ".txt")):
            await message.answer("⚠️ Faqat <code>.json</code>, <code>.csv</code> yoki <code>.txt</code> fayl yuboring (yoki matn tashlang).")
            return
        if (message.document.file_size or 0) > 2 * 1024 * 1024:
            await message.answer("⚠️ Matn fayl 2 MB dan kichik bo‘lishi kerak.")
            return
        buf = io.BytesIO()
        await message.bot.download(message.document, destination=buf)
        raw_text = buf.getvalue().decode("utf-8", errors="replace")

    records, parse_err = _parse_batch_records(raw_text)
    if parse_err:
        await message.answer(f"❌ {esc(parse_err)}")
        return

    from services.card_zip_import import _MEDIA_LOOKUP, _pick, resolve_rank

    async with get_session() as session:
        ranks = await GameplayService.get_rank_definitions(session)
        sync_runtime_ranks(ranks)
        rank_by_name = {str(r["name"]).lower(): r for r in ranks}
        # Takroriy = bir xil RASM (URL / file_id). Bir personajning turli rasmli kartalari ruxsat.
        existing_keys = {
            str(v).strip().lower()
            for row in (await session.execute(select(Card.image, Card.image_file_id).where(Card.is_deleted.is_(False)))).all()
            for v in row
            if v
        }

        added = 0
        dups = 0
        failed_reasons: list[str] = []
        for idx, rec in enumerate(records[:200], start=1):
            ln = rec.get("_line", idx)
            if rec.get("_err"):
                failed_reasons.append(str(rec["_err"]))
                continue
            char_name = str(_pick(rec, "character_name") or "").strip()
            anime_name = str(_pick(rec, "media_name") or "").strip()
            raw_mtype = str(_pick(rec, "media_type") or "Anime").strip()
            raw_rank = _pick(rec, "rank_id")
            img_ref = str(_pick(rec, "image") or "").strip()

            if not char_name or not anime_name or not img_ref:
                failed_reasons.append(f"#{ln}: personaj, asar nomi yoki rasm yo‘q.")
                continue
            mtype = _MEDIA_LOOKUP.get(raw_mtype.lower())
            if not mtype:
                failed_reasons.append(f"#{ln} ({char_name}): noma’lum media_type '{raw_mtype}'.")
                continue
            rarity = resolve_rank(raw_rank)
            if not rarity:
                failed_reasons.append(f"#{ln} ({char_name}): rank topilmadi '{raw_rank}'.")
                continue

            if img_ref.startswith(("http://", "https://")):
                ok_img, img_err = await validate_image_url(img_ref)
                if not ok_img:
                    failed_reasons.append(f"#{ln} ({char_name}): {img_err}")
                    continue
            elif len(img_ref) < 20 or " " in img_ref:
                failed_reasons.append(f"#{ln} ({char_name}): rasm URL (https://...) yoki Telegram file_id bo‘lishi kerak.")
                continue

            dup_key = img_ref.strip().lower()
            if dup_key in existing_keys:
                dups += 1
                continue

            r_obj = rank_by_name.get(rarity.lower())
            meta_r = get_rarity_meta(rarity)
            base_val = int(rec.get("base_value") or (r_obj.get("base_value") if r_obj else meta_r.default_base_value) or meta_r.default_base_value)
            drop_lim = int(rec.get("drop_limit") or (r_obj.get("drop_limit") if r_obj else meta_r.default_drop_limit) or meta_r.default_drop_limit)
            drop_w = int((r_obj.get("drop_probability") if r_obj else meta_r.default_drop_weight) or meta_r.default_drop_weight)
            desc = str(rec.get("description") or "").strip()
            meta_json = {"description": desc} if desc else {}
            card = await CardService.create_card(
                session,
                character_name=char_name,
                anime_name=anime_name,
                image=img_ref,
                rarity=rarity,
                base_value=base_val,
                drop_weight=drop_w,
                drop_limit=drop_lim,
                media_type=mtype,
                metadata_json=meta_json,
            )
            if not img_ref.startswith(("http://", "https://")):
                card.image_file_id = img_ref
            existing_keys.add(dup_key)
            added += 1

        if added:
            await CommunityService.log_audit(session, message.from_user.id, "batch_add_cards", "card", str(added))
        await session.commit()

    await state.clear()
    lines = [
        "📋 <b>OMMAVIY QO‘SHISH NATIJASI</b>",
        f"✅ Qo‘shildi: <b>{added}</b> ta",
        f"♻️ Takroriy (o‘tkazildi): <b>{dups}</b> ta",
        f"❌ Xato: <b>{len(failed_reasons)}</b> ta",
    ]
    if failed_reasons and any("rasm" in fr for fr in failed_reasons):
        lines.append("\n💡 Rasm URL siz JSON uchun: Karta qo‘shish → «📄 JSON + rasmlar ketma-ket».")
    if failed_reasons:
        lines.append("\n<b>Sabablar:</b>")
        for fr in failed_reasons[:8]:
            lines.append(f"• {esc(fr[:120])}")
    await message.answer("\n".join(lines), reply_markup=admin_v2_cards_menu_kb(message.from_user.id))


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


async def _render_cards_tab(callback: CallbackQuery, tab: str, page: int) -> None:
    owner = callback.from_user.id
    page = max(1, page)
    async with get_session() as session:
        cards, total = await AdminService.get_v2_cards_by_tab(session, tab=tab, page=page, page_size=PAGE_SIZE)
    if not cards and total > 0 and page > 1:
        page = 1
        async with get_session() as session:
            cards, total = await AdminService.get_v2_cards_by_tab(session, tab=tab, page=1, page_size=PAGE_SIZE)
    admin_nav.remember(owner, "view_tabs", ext=tab, p=page)
    mode = admin_nav.get_mode(owner)
    txt = (
        f"{admin_nav.MODE_TITLES[mode]}\n"
        f"📂 <b>{_TAB_TITLES.get(tab, tab.upper())}</b>\n"
        f"Jami: <b>{total}</b> ta | sahifa {page} | 20/sahifa"
    )
    if mode == admin_nav.MODE_EDIT:
        txt += "\n\n👇 Tahrirlash uchun kartani tanlang:"
    elif mode == admin_nav.MODE_DEL:
        txt += "\n\n👇 O‘chirish uchun kartani tanlang:"
    if not cards:
        txt += "\n\n📭 Bu bo‘limda kartalar yo‘q."
    await edit_or_answer(
        callback,
        txt,
        reply_markup=admin_v2_view_cards_kb(owner, cards, tab, page, total, page_size=PAGE_SIZE),
    )


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "view_tabs")))
async def cb_v2_view_cards(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    await _render_cards_tab(callback, callback_data.ext or "all", callback_data.p)


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "view_start")))
async def cb_v2_view_start(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    """Menyudan «Kartalarni ko‘rish» / «O‘chirilganlar» — ko‘rish rejimini boshlaydi."""
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    admin_nav.set_mode(callback.from_user.id, admin_nav.MODE_VIEW)
    await _render_cards_tab(callback, callback_data.ext or "sent_group", 1)


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "titles_start")))
async def cb_v2_titles_start(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    admin_nav.set_mode(callback.from_user.id, admin_nav.MODE_VIEW)
    await _render_media_titles(callback, callback_data.ext or "anime", 1)


async def _render_media_titles(callback: CallbackQuery, mgroup: str, page: int) -> None:
    owner = callback.from_user.id
    page = max(1, page)
    async with get_session() as session:
        titles, total = await AdminService.get_v2_media_titles(session, media_group=mgroup, page=page, page_size=PAGE_SIZE)
    admin_nav.remember(owner, "titles", ext=mgroup, p=page)
    mode = admin_nav.get_mode(owner)
    txt = (
        f"{admin_nav.MODE_TITLES[mode]}\n"
        f"📁 <b>{esc(mgroup.upper())} NOMLARI</b> (karta soni bo‘yicha | Jami: <b>{total}</b> ta)\n\n"
        "Ichiga kirish uchun nomni tanlang:"
    )
    if not titles:
        txt = f"{admin_nav.MODE_TITLES[mode]}\n📁 <b>{esc(mgroup.upper())} NOMLARI</b>\n\n📭 Hozircha kartalar yo‘q."
    await edit_or_answer(
        callback,
        txt,
        reply_markup=admin_v2_media_titles_kb(owner, titles, mgroup, page, total, page_size=PAGE_SIZE),
    )


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "titles")))
async def cb_v2_media_titles(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    await _render_media_titles(callback, callback_data.ext or "anime", callback_data.p)


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "mtitle")))
async def cb_v2_media_title_cards(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    owner = callback.from_user.id
    mgroup = callback_data.ext or "anime"
    page = max(1, callback_data.p)
    async with get_session() as session:
        title, cards, total = await AdminService.get_v2_cards_by_media_title(
            session, media_group=mgroup, rep_card_id=callback_data.tid, page=page, page_size=PAGE_SIZE
        )
    # Sarlavha ichidagi sahifa eslab qolinadi: kartadan orqaga qaytganda shu sarlavhada davom etamiz
    admin_nav.remember(owner, "mtitle", tid=callback_data.tid, ext=mgroup, p=page)
    mode = admin_nav.get_mode(owner)
    hint = {
        admin_nav.MODE_VIEW: "Ko‘rish uchun kartani tanlang:",
        admin_nav.MODE_EDIT: "Tahrirlash uchun kartani tanlang:",
        admin_nav.MODE_DEL: "O‘chirish uchun kartani tanlang:",
    }[mode]
    txt = f"{admin_nav.MODE_TITLES[mode]}\n🎬 <b>{esc(title or mgroup.upper())}</b> (Jami: <b>{total}</b> ta karta)\n\n{hint}"
    if not cards:
        txt += "\n\n📭 Topilmadi."
    await edit_or_answer(
        callback,
        txt,
        reply_markup=admin_v2_title_cards_kb(owner, cards, callback_data.tid, mgroup, page, total, page_size=PAGE_SIZE),
    )


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act.in_({"edit_list", "del_list"}))))
async def cb_v2_cards_action_list(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    """Tahrirlash / o‘chirish: «Kartalarni ko‘rish» dagi bir xil filtrli (tab, sarlavha) ro‘yxat ochiladi."""
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    mode = admin_nav.MODE_EDIT if callback_data.act == "edit_list" else admin_nav.MODE_DEL
    admin_nav.set_mode(callback.from_user.id, mode)
    await _render_cards_tab(callback, callback_data.ext or "all", callback_data.p)


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
                    text="⬅️ ORQAGA (ro‘yxat)",
                    callback_data=admin_nav.back_callback(callback.from_user.id),
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
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Bekor", callback_data=admin_nav.back_callback(callback.from_user.id))]]
        ),
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
    b.row(InlineKeyboardButton(text="⬅️ ORQAGA (ro‘yxat)", callback_data=admin_nav.back_callback(message.from_user.id)))
    await message.answer(f"🔍 Topildi: <b>{total}</b> ta karta (20 tagacha ko‘rsatilmoqda):", reply_markup=b.as_markup())


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "edit_pick")))
async def cb_v2_edit_fields(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    async with get_session() as session:
        card = await session.get(Card, callback_data.tid)
    kb = admin_v2_edit_fields_kb(callback.from_user.id, callback_data.tid)
    if card is not None and card.image and (card.image.startswith("http") or len(card.image) > 20) and callback.message:
        cap = (
            f"{format_card_block(card, show_status=True)}\n\n"
            f"✏️ <b>KARTA #{card.id} TAHRIRLASH</b>\nQaysi maydonni o‘zgartirasiz?"
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
        f"✏️ <b>KARTA #{callback_data.tid} TAHRIRLASH</b>\n\nQaysi maydonni o‘zgartirasiz?",
        reply_markup=kb,
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
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(
                text="❌ Bekor (kartaga qaytish)",
                callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="cards", act="edit_pick", tid=cid).pack(),
            )]]
        ),
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
