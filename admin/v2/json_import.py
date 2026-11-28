"""JSON (rasmsiz) + rasmlar ketma-ket: kartalarni tez ommaviy qo'shish.

Oqim:
1. Admin rasmsiz JSON fayl yuboradi (AI tuzib bergan).
2. Bot JSON dagi kartalarni TARTIB RAQAMI bilan ro'yxat qilib chiqaradi.
3. Admin rasmlarni shu tartibda yuboradi — 20 ta rasmni bir yo'la (albom) yoki bittalab.
   Bot rasmlarni yuborilish tartibi (message_id) bo'yicha 1, 2, 3... kartalarga joylaydi.
   • Rasm izohiga raqam yozilsa (masalan «7») — aynan shu raqamli kartaga joylanadi.
   • Fayl (document) ko'rinishida yuborilgan rasmlar ham qabul qilinadi.
4. Hamma kartaga rasm to'lganda (yoki «Tugatish» bosilganda) kartalar yaratiladi.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
from typing import Any, Dict, List, Optional

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select

from admin.filters import IsAdminFilter
from admin.v2.common import _deny_if_no_perm
from database.session import get_session
from keyboards.v2 import admin_v2_cards_menu_kb
from models.card import Card
from services.card_service import CardService
from services.community_service import CommunityService
from services.gameplay_service import GameplayService
from utils.callback_data import AdmV2Cb
from utils.constants import MEDIA_TYPES, get_rarity_meta, sync_runtime_ranks
from utils.formatting import esc
from utils.tg_helpers import NotCommandFilter, edit_or_answer

logger = logging.getLogger(__name__)
router = Router(name="admin_v2_json_import")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())

MAX_RECORDS = 300
DEBOUNCE_SEC = 1.8
OPTIONAL_META = ("description", "birthday", "age", "height", "weight", "personality", "history", "abilities")


class JsonImportStates(StatesGroup):
    waiting_json = State()
    waiting_photos = State()


# --------------------------------------------------------------------------
# AI uchun ko'rsatma (jonli rank jadvali bilan)
# --------------------------------------------------------------------------
def build_ai_prompt() -> str:
    from services.card_zip_import import rank_table

    table = rank_table()
    n = len(table)
    lines = []
    if n >= 3:
        bounds = [round(45 + (95 - 45) * i / (n - 2)) for i in range(n - 1)]
    else:
        bounds = [60][: max(0, n - 1)]
    for idx, (rid, name) in enumerate(table):
        if idx == 0:
            rng = f"0–{bounds[0] - 1} ball" if bounds else "hamma ball"
        elif idx == n - 1:
            rng = f"{bounds[-1]}+ ball"
        else:
            rng = f"{bounds[idx - 1]}–{bounds[idx] - 1} ball"
        lines.append(f"  {rid} = {name}  →  {rng}")
    rank_block = "\n".join(lines)
    media = ", ".join(MEDIA_TYPES)
    return f"""Sen anime/manga karta kolleksiyasi kuratorisan. Men sanga personaj rasmlarini yuboraman.
Internetda (Google) qidirib har bir personajni aniq top va karta ma'lumotlari bilan JSON fayl tuzib ber.

QOIDALAR
1. Rasmlarni men yuborgan TARTIBDA tahlil qil: 1-rasm = JSON dagi 1-karta, 2-rasm = 2-karta ... (tartib buzilmasin).
2. Personajni internetdan tekshirib aniqla. Ishonching komil bo'lmasa o'ylab topma: "character_name": "UNKNOWN" yoz va "note" ga sababini yoz.
3. Ism va asar nomi rasmiy inglizcha (lotin) yozuvida bo'lsin. Ixtiyoriy maydonlarni faqat ishonchli bo'lsa to'ldir.

HAR KARTA MAYDONLARI
character_name (majburiy) · media_type (majburiy: {media}) · media_name (majburiy, asar nomi) · rank_id (majburiy, son)
aliases (boshqa ismlari, ro'yxat) · description (1–2 gap, o'zbekcha) · birthday · age · height · weight · personality · abilities · history
score (umumiy ball) · rank_reason (rank sababi, 1 gap)

RANK (rank_id) — quyidagilarni birgalikda baholab 100 ballik umumiy ball (score) chiqar:
• Rasm grafikasi va sifati (aniqlik, chizilish, ranglar) — 25
• Dizayn, poza, personajning ko'rinishi — 20
• Fon va kompozitsiyaning yoqimliligi — 15
• Personajning mashhurligi va sevilishi — 20
• Asar (anime/manga) mashhurligi — 15
• Odamlarga yoqishi va noyobligi (kolleksiya qiymati) — 5
Ballga qarab rank ber (yuqori ranklarni kam, pastkilarni ko'p ber):
{rank_block}

JAVOB: faqat .json fayl (yoki bitta ```json bloki), boshqa matn yo'q:
{{"cards": [{{"character_name": "Naruto Uzumaki", "media_type": "Anime", "media_name": "Naruto", "rank_id": 3, "aliases": ["Naruto"], "description": "...", "score": 78, "rank_reason": "..."}}]}}
"""


# --------------------------------------------------------------------------
# yordamchilar
# --------------------------------------------------------------------------
_BUF: Dict[int, List[tuple]] = {}
_TASKS: Dict[int, "asyncio.Task"] = {}
_LOCKS: Dict[int, asyncio.Lock] = {}


def _lock(uid: int) -> asyncio.Lock:
    lk = _LOCKS.get(uid)
    if lk is None:
        lk = _LOCKS[uid] = asyncio.Lock()
    return lk


def _cb(owner_id: int, act: str) -> str:
    return AdmV2Cb(owner_id=owner_id, sec="cards", act=act).pack()


def _photos_kb(owner_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="↩️ Oxirgi rasmni bekor", callback_data=_cb(owner_id, "jq_undo")),
                InlineKeyboardButton(text="⏭ Hozirgisini o‘tkazish", callback_data=_cb(owner_id, "jq_skip")),
            ],
            [
                InlineKeyboardButton(text="📋 Holat / ro‘yxat", callback_data=_cb(owner_id, "jq_list")),
                InlineKeyboardButton(text="✅ Tugatish va saqlash", callback_data=_cb(owner_id, "jq_done")),
            ],
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data=_cb(owner_id, "jq_cancel"))],
        ]
    )


def _chunks(lines: List[str], limit: int = 3500) -> List[str]:
    out: List[str] = []
    cur = ""
    for ln in lines:
        if len(cur) + len(ln) + 1 > limit:
            out.append(cur)
            cur = ""
        cur += ln + "\n"
    if cur.strip():
        out.append(cur)
    return out


def _numbered_lines(records: List[dict], slots: List[Optional[str]], skipped: set) -> List[str]:
    lines = []
    for i, r in enumerate(records):
        if slots[i]:
            mark = "✅"
        elif i in skipped:
            mark = "⏭"
        else:
            mark = "⬜"
        lines.append(f"{mark} <b>{i + 1}.</b> {esc(r['character_name'])} — {esc(r['media_name'])} · {esc(r['rarity'])}")
    return lines


def _next_free(slots: List[Optional[str]], skipped: set) -> Optional[int]:
    for i, v in enumerate(slots):
        if not v and i not in skipped:
            return i
    return None


def _to_list(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return [str(a).strip() for a in raw if str(a).strip()]
    if isinstance(raw, str):
        return [a.strip() for a in raw.split(",") if a.strip()]
    return []


def _extract_json_payload(raw_text: str) -> Any:
    """AI qaytargan JSON ni bardoshli o'qiydi: ```json bloki, oldi-orqasidagi matn, ortiqcha vergullar."""
    import re

    txt = (raw_text or "").strip().lstrip("\ufeff")
    fence = re.search(r"```(?:json)?\s*(.*?)```", txt, re.S | re.I)
    if fence:
        txt = fence.group(1).strip()
    try:
        return json.loads(txt)
    except Exception:
        pass
    starts = [i for i in (txt.find("{"), txt.find("[")) if i != -1]
    ends = [i for i in (txt.rfind("}"), txt.rfind("]")) if i != -1]
    if starts and ends and max(ends) > min(starts):
        txt = txt[min(starts) : max(ends) + 1]
    txt = re.sub(r",\s*([}\]])", r"\1", txt)  # ortiqcha vergullar
    txt = txt.replace("“", '"').replace("”", '"')
    return json.loads(txt)


# --------------------------------------------------------------------------
# 1. Boshlash
# --------------------------------------------------------------------------
@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "jsonq")))
async def cb_jsonq_start(callback: CallbackQuery, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    await state.clear()
    await state.set_state(JsonImportStates.waiting_json)
    await state.update_data(owner_tg_id=callback.from_user.id)
    owner = callback.from_user.id
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🤖 AI uchun ko‘rsatma (.txt)", callback_data=_cb(owner, "jq_prompt"))],
            [InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=_cb(owner, "add_menu"))],
        ]
    )
    await edit_or_answer(
        callback,
        "📄 <b>JSON + RASMLAR (ketma-ket)</b>\n━━━━━━━━━━━━━━━\n"
        "1️⃣ Rasmsiz <b>.json</b> faylni yuboring (yoki JSON matnini tashlang).\n"
        "2️⃣ Bot kartalarni tartib raqami bilan ro‘yxat qiladi.\n"
        "3️⃣ Siz rasmlarni shu tartibda yuborasiz — 20 tasini bir yo‘la belgilab yuborsangiz ham bo‘ladi.\n\n"
        "🤖 JSON ni AI ga tuzdirish uchun pastdagi ko‘rsatmani oling.\n<i>Bekor qilish: /cancel</i>",
        reply_markup=kb,
    )


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "jq_prompt")))
async def cb_jq_prompt(callback: CallbackQuery, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    async with get_session() as session:
        sync_runtime_ranks(await GameplayService.get_rank_definitions(session))
    text = build_ai_prompt()
    if callback.message:
        await callback.message.answer_document(
            BufferedInputFile(text.encode("utf-8"), filename="AI_korsatma.txt"),
            caption="🤖 Shu matnni AI ga bering, so‘ng rasmlarni yuboring. Rank jadvali botdagi joriy ranklardan olingan.",
        )
    await callback.answer()


# --------------------------------------------------------------------------
# 2. JSON qabul qilish
# --------------------------------------------------------------------------
@router.message(JsonImportStates.waiting_json, NotCommandFilter())
async def fsm_jsonq_input(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip()
    if message.document:
        fname = (message.document.file_name or "").lower()
        if not fname.endswith((".json", ".txt")):
            await message.answer("⚠️ Faqat <code>.json</code> fayl (yoki JSON matni) yuboring.")
            return
        if (message.document.file_size or 0) > 3 * 1024 * 1024:
            await message.answer("⚠️ Fayl 3 MB dan kichik bo‘lishi kerak.")
            return
        buf = io.BytesIO()
        await message.bot.download(message.document, destination=buf)
        raw = buf.getvalue().decode("utf-8-sig", errors="replace")
    if not raw:
        await message.answer("⚠️ JSON fayl yoki matn yuboring.")
        return
    try:
        parsed = _extract_json_payload(raw)
    except Exception as exc:
        await message.answer(f"❌ JSON o‘qilmadi: <code>{esc(str(exc)[:200])}</code>\nAI ga «faqat to‘g‘ri JSON ber» deb ayting.")
        return
    if isinstance(parsed, dict):
        parsed = parsed.get("cards") if isinstance(parsed.get("cards"), list) else [parsed]
    if not isinstance(parsed, list) or not parsed:
        await message.answer("❌ JSON ichida kartalar ro‘yxati topilmadi.")
        return

    from services.card_zip_import import _MEDIA_LOOKUP, _pick, resolve_rank

    async with get_session() as session:
        ranks = await GameplayService.get_rank_definitions(session)
        sync_runtime_ranks(ranks)

    records: List[dict] = []
    problems: List[str] = []
    for idx, rec in enumerate(parsed[:MAX_RECORDS], start=1):
        if not isinstance(rec, dict):
            problems.append(f"#{idx}: obyekt emas")
            continue
        name = " ".join(str(_pick(rec, "character_name") or "").split())
        media = " ".join(str(_pick(rec, "media_name") or "").split())
        raw_type = str(_pick(rec, "media_type") or "Anime").strip()
        mtype = _MEDIA_LOOKUP.get(raw_type.lower())
        rarity = resolve_rank(_pick(rec, "rank_id"))
        if not name or name.upper() == "UNKNOWN":
            problems.append(f"#{idx}: personaj aniqlanmagan ({esc(str(rec.get('note', ''))[:60])})")
            continue
        if not media:
            problems.append(f"#{idx} {name}: media_name yo‘q")
            continue
        if not mtype:
            problems.append(f"#{idx} {name}: noma’lum media_type «{raw_type}»")
            continue
        if not rarity:
            problems.append(f"#{idx} {name}: rank_id noto‘g‘ri «{_pick(rec, 'rank_id')}»")
            continue
        meta = {k: str(rec.get(k)).strip() for k in OPTIONAL_META if rec.get(k) not in (None, "")}
        records.append(
            {
                "character_name": name,
                "media_name": media,
                "media_type": mtype,
                "rarity": rarity,
                "aliases": _to_list(rec.get("aliases")),
                "meta": meta,
                "base_value": int(rec["base_value"]) if str(rec.get("base_value", "")).isdigit() else None,
                "drop_limit": int(rec["drop_limit"]) if str(rec.get("drop_limit", "")).isdigit() else None,
            }
        )

    if not records:
        await state.clear()
        extra = ("\n" + "\n".join(f"• {p}" for p in problems[:10])) if problems else ""
        await message.answer(
            f"❌ Qo‘shiladigan karta topilmadi.{extra}",
            reply_markup=admin_v2_cards_menu_kb(message.from_user.id),
        )
        return

    await state.set_state(JsonImportStates.waiting_photos)
    await state.update_data(
        owner_tg_id=message.from_user.id,
        records=records,
        slots=[None] * len(records),
        uids=[None] * len(records),
        skipped=[],
    )
    head = (
        f"📋 <b>JSON qabul qilindi:</b> {len(records)} ta karta\n"
        f"❌ Xato (o‘tkazib yuborildi): {len(problems)}\n"
        "ℹ️ Bir personajning bir xil rankdagi bir nechta karta (turli rasm) ham qo‘shiladi — takroriy faqat BIR XIL RASM bo‘lsa aniqlanadi."
    )
    if problems:
        head += "\n⚠️ Bu kartalar ro‘yxatda YO‘Q (ularning rasmini yubormang):\n" + "\n".join(f"• {p}" for p in problems[:8])
    await message.answer(head)
    for chunk in _chunks(_numbered_lines(records, [None] * len(records), set())):
        await message.answer(chunk)
    await message.answer(
        f"🖼 <b>Endi rasmlarni shu tartibda yuboring</b> (1 → {len(records)}).\n\n"
        "• Bir yo‘la ko‘p rasm belgilab yuborishingiz mumkin (albom) — bot ularni yuborilgan ketma-ketlikda "
        "1, 2, 3… kartalarga joylaydi.\n"
        "• Bittalab ham yuborsangiz bo‘ladi — bot navbatdagi kartaga qo‘yadi.\n"
        "• Rasm izohiga <b>raqam</b> yozsangiz (masalan <code>7</code>), u aynan 7-kartaga joylanadi.\n"
        "• Rasmni <b>fayl</b> ko‘rinishida yuborsangiz ham qabul qilinadi.\n\n"
        f"👉 Birinchi navbat: <b>1. {esc(records[0]['character_name'])}</b>",
        reply_markup=_photos_kb(message.from_user.id),
    )


# --------------------------------------------------------------------------
# 3. Rasmlarni qabul qilish (debounce bilan, tartib = message_id)
# --------------------------------------------------------------------------
def _is_image_doc(message: Message) -> bool:
    d = message.document
    return bool(d and ((d.mime_type or "").startswith("image/") or (d.file_name or "").lower().endswith((".jpg", ".jpeg", ".png", ".webp"))))


@router.message(JsonImportStates.waiting_photos, F.photo | F.document)
async def fsm_jsonq_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    uid = message.from_user.id
    if data.get("owner_tg_id") != uid:
        return
    if message.photo:
        item = (message.message_id, "photo", (message.photo[-1].file_id, message.photo[-1].file_unique_id), message.caption)
    elif _is_image_doc(message):
        item = (message.message_id, "doc", message.document, message.caption)
    else:
        await message.answer("⚠️ Rasm (foto yoki rasm fayli) yuboring.")
        return
    _BUF.setdefault(uid, []).append(item)
    old = _TASKS.get(uid)
    if old and not old.done():
        old.cancel()
    _TASKS[uid] = asyncio.create_task(_flush_later(message, state))


@router.message(JsonImportStates.waiting_photos, NotCommandFilter())
async def fsm_jsonq_text_hint(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    await message.answer(
        "🖼 Hozir <b>rasm</b> yuborish kerak (foto yoki rasm fayli). Holatni ko‘rish uchun «📋 Holat / ro‘yxat» ni bosing.",
        reply_markup=_photos_kb(message.from_user.id),
    )


async def _flush_later(message: Message, state: FSMContext) -> None:
    try:
        await asyncio.sleep(DEBOUNCE_SEC)
    except asyncio.CancelledError:
        return
    try:
        await _flush(message, state)
    except Exception as exc:
        logger.exception("json_import flush xatosi")
        try:
            await message.answer(
                f"❌ <b>Xatolik:</b> <code>{esc(type(exc).__name__)}: {esc(str(exc)[:300])}</code>\n"
                "Rasmlar saqlanib turibdi. «✅ Tugatish va saqlash» ni qayta bosib ko‘ring.",
                reply_markup=_photos_kb(message.from_user.id),
            )
        except Exception:
            pass


async def _doc_to_photo_id(bot, chat_id: int, document):
    """Rasm faylini foto sifatida yuklaydi. (file_id, unikal belgi) qaytaradi."""
    import hashlib

    from services.card_zip_import import _to_jpeg, _upload_photo

    buf = io.BytesIO()
    await bot.download(document, destination=buf)
    jpeg = await asyncio.to_thread(_to_jpeg, buf.getvalue())
    fid = await _upload_photo(bot, chat_id, jpeg, (document.file_name or "card")[:40])
    return fid, "h" + hashlib.sha256(jpeg).hexdigest()[:40]


async def _flush(message: Message, state: FSMContext) -> None:
    uid = message.from_user.id
    async with _lock(uid):
        items = _BUF.pop(uid, [])
        if not items:
            return
        if await state.get_state() != JsonImportStates.waiting_photos.state:
            return
        items.sort(key=lambda x: x[0])
        data = await state.get_data()
        records: List[dict] = data["records"]
        slots: List[Optional[str]] = list(data["slots"])
        uids: List[Optional[str]] = list(data.get("uids") or [None] * len(slots))
        skipped = set(data.get("skipped", []))
        placed: List[int] = []
        warns: List[str] = []
        for _mid, kind, ref, caption in items:
            try:
                fid, src_uid = ref if kind == "photo" else await _doc_to_photo_id(message.bot, message.chat.id, ref)
            except Exception as exc:
                warns.append(f"rasm o‘qilmadi: {str(exc)[:60]}")
                continue
            cap = (caption or "").strip()
            idx: Optional[int] = None
            if cap.isdigit() and 1 <= int(cap) <= len(records):
                idx = int(cap) - 1
                skipped.discard(idx)
            else:
                idx = _next_free(slots, skipped)
            if idx is None:
                warns.append("ortiqcha rasm (hamma kartaga rasm bor)")
                continue
            slots[idx] = fid
            uids[idx] = src_uid
            placed.append(idx)
        await state.update_data(slots=slots, uids=uids, skipped=sorted(skipped))

        lines = []
        for i in placed[:25]:
            lines.append(f"✅ {i + 1}. {esc(records[i]['character_name'])}")
        if len(placed) > 25:
            lines.append(f"… va yana {len(placed) - 25} ta")
        txt = f"🖼 <b>{len(placed)} ta rasm joylandi</b>\n" + "\n".join(lines)
        if warns:
            txt += "\n\n⚠️ " + "; ".join(sorted(set(warns)))
        nxt = _next_free(slots, skipped)
        filled = sum(1 for v in slots if v)
        txt += f"\n\n📊 To‘ldi: <b>{filled}/{len(records)}</b>"
        if nxt is None:
            txt += "\n🎉 Hamma kartaga rasm joylandi — saqlayapman…"
            await message.answer(txt)
            await _finish(message, state)
            return
        txt += f"\n👉 Navbatdagi: <b>{nxt + 1}. {esc(records[nxt]['character_name'])}</b>"
        await message.answer(txt, reply_markup=_photos_kb(uid))


# --------------------------------------------------------------------------
# 4. Tugmalar: bekor qilish, o'tkazish, ro'yxat, tugatish
# --------------------------------------------------------------------------
async def _owner_ok(callback: CallbackQuery, state: FSMContext) -> Optional[dict]:
    data = await state.get_data()
    if data.get("owner_tg_id") != callback.from_user.id or "records" not in data:
        await callback.answer("Sessiya tugagan. Qaytadan boshlang.", show_alert=True)
        return None
    return data


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "jq_undo")))
async def cb_jq_undo(callback: CallbackQuery, state: FSMContext) -> None:
    data = await _owner_ok(callback, state)
    if data is None:
        return
    async with _lock(callback.from_user.id):
        slots = list(data["slots"])
        uids = list(data.get("uids") or [None] * len(slots))
        filled = [i for i, v in enumerate(slots) if v]
        if not filled:
            await callback.answer("Bekor qilinadigan rasm yo‘q.", show_alert=True)
            return
        last = filled[-1]
        slots[last] = None
        uids[last] = None
        await state.update_data(slots=slots, uids=uids)
    await callback.answer(f"↩️ {last + 1}-karta rasmi bekor qilindi.", show_alert=True)


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "jq_skip")))
async def cb_jq_skip(callback: CallbackQuery, state: FSMContext) -> None:
    data = await _owner_ok(callback, state)
    if data is None:
        return
    async with _lock(callback.from_user.id):
        slots = list(data["slots"])
        skipped = set(data.get("skipped", []))
        nxt = _next_free(slots, skipped)
        if nxt is None:
            await callback.answer("O‘tkaziladigan karta qolmadi.", show_alert=True)
            return
        skipped.add(nxt)
        await state.update_data(skipped=sorted(skipped))
        nxt2 = _next_free(slots, skipped)
    records = data["records"]
    msg = f"⏭ {nxt + 1}-karta o‘tkazildi (rasmsiz qo‘shilmaydi)."
    if nxt2 is not None:
        msg += f"\n👉 Navbatdagi: {nxt2 + 1}. {records[nxt2]['character_name']}"
    await callback.answer(msg[:190], show_alert=True)


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "jq_list")))
async def cb_jq_list(callback: CallbackQuery, state: FSMContext) -> None:
    data = await _owner_ok(callback, state)
    if data is None:
        return
    records, slots, skipped = data["records"], data["slots"], set(data.get("skipped", []))
    filled = sum(1 for v in slots if v)
    await callback.answer()
    if not callback.message:
        return
    head = f"📋 <b>Holat:</b> {filled}/{len(records)} rasm joylangan\n✅ rasm bor · ⬜ kutilmoqda · ⏭ o‘tkazilgan"
    chunks = _chunks(_numbered_lines(records, slots, skipped))
    for i, ch in enumerate(chunks):
        await callback.message.answer((head + "\n\n" if i == 0 else "") + ch, reply_markup=_photos_kb(callback.from_user.id) if i == len(chunks) - 1 else None)


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "jq_cancel")))
async def cb_jq_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    _BUF.pop(callback.from_user.id, None)
    await state.clear()
    await callback.answer("Bekor qilindi.")
    await edit_or_answer(callback, "❌ JSON import bekor qilindi.", reply_markup=admin_v2_cards_menu_kb(callback.from_user.id))


@router.callback_query(AdmV2Cb.filter((F.sec == "cards") & (F.act == "jq_done")))
async def cb_jq_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await _owner_ok(callback, state)
    if data is None:
        return
    await callback.answer()
    if callback.message:
        await _finish(callback.message, state, user_id=callback.from_user.id)


# --------------------------------------------------------------------------
# 5. Saqlash
# --------------------------------------------------------------------------
async def _finish(message: Message, state: FSMContext, user_id: Optional[int] = None) -> None:
    uid_tg = user_id or message.from_user.id
    data = await state.get_data()
    records: List[dict] = data.get("records") or []
    slots: List[Optional[str]] = data.get("slots") or []
    if not records:
        await state.clear()
        return
    uids = data.get("uids") or [None] * len(records)
    added: List[str] = []
    missing: List[str] = []
    dup_imgs: List[str] = []
    failed: List[str] = []
    progress = await message.answer(f"⏳ Saqlanmoqda… (0/{len(records)})")
    try:
        async with get_session() as session:
            ranks = await GameplayService.get_rank_definitions(session)
            sync_runtime_ranks(ranks)
            rank_by_name = {str(r["name"]).lower(): r for r in ranks}
            # Takroriy = BIR XIL RASM (nom/rank emas): bir personajning turli rasmli kartalari ruxsat
            known_uids = set()
            for m in (await session.execute(select(Card.metadata_json).where(Card.is_deleted.is_(False)))).scalars().all():
                if isinstance(m, dict) and m.get("src_uid"):
                    known_uids.add(str(m["src_uid"]))
            for i, rec in enumerate(records):
                fid = slots[i] if i < len(slots) else None
                if not fid:
                    missing.append(f"{i + 1}. {rec['character_name']}")
                    continue
                src_uid = uids[i] if i < len(uids) else None
                if src_uid and src_uid in known_uids:
                    dup_imgs.append(f"{i + 1}. {rec['character_name']}")
                    continue
                try:
                    async with session.begin_nested():
                        rarity = rec["rarity"]
                        r_obj = rank_by_name.get(rarity.lower())
                        meta_r = get_rarity_meta(rarity)
                        base_val = int(rec.get("base_value") or (r_obj.get("base_value") if r_obj else 0) or meta_r.default_base_value)
                        drop_lim = int(rec.get("drop_limit") or (r_obj.get("drop_limit") if r_obj else 0) or meta_r.default_drop_limit)
                        drop_w = int((r_obj.get("drop_probability") if r_obj else 0) or meta_r.default_drop_weight)
                        card = await CardService.create_card(
                            session,
                            character_name=rec["character_name"],
                            anime_name=rec["media_name"],
                            image=fid,
                            rarity=rarity,
                            base_value=base_val,
                            drop_weight=drop_w,
                            drop_limit=drop_lim,
                            media_type=rec["media_type"],
                            aliases=rec.get("aliases") or None,
                            metadata_json={**(rec.get("meta") or {}), **({"src_uid": src_uid} if src_uid else {})},
                        )
                        card.image_file_id = fid
                    if src_uid:
                        known_uids.add(src_uid)
                    added.append(f"{rec['character_name']} ({rarity})")
                except Exception as exc:
                    logger.exception("json_import: karta saqlanmadi #%s", i + 1)
                    failed.append(f"{i + 1}. {rec['character_name']} — {type(exc).__name__}: {str(exc)[:80]}")
            if added:
                try:
                    await CommunityService.log_audit(
                        session, uid_tg, "json_import_cards", "card", str(len(added)), details={"names": added[:30]}
                    )
                except Exception:
                    logger.exception("json_import: audit yozilmadi")
            await session.commit()
    except Exception as exc:
        logger.exception("json_import: saqlash xatosi")
        await message.answer(
            f"❌ <b>Saqlashda xatolik:</b> <code>{esc(type(exc).__name__)}: {esc(str(exc)[:300])}</code>\n"
            "Rasmlar yo‘qolmadi — «✅ Tugatish va saqlash» ni qayta bosib ko‘ring.",
            reply_markup=_photos_kb(uid_tg),
        )
        return
    try:
        await progress.delete()
    except Exception:
        pass
    _BUF.pop(uid_tg, None)
    await state.clear()

    lines = [
        "✅ <b>JSON IMPORT YAKUNLANDI</b>" if added else "⚠️ <b>Hech narsa qo‘shilmadi</b>",
        f"➕ Qo‘shildi: <b>{len(added)}</b> ta",
        f"⏭ Rasmsiz (qo‘shilmadi): <b>{len(missing)}</b> ta",
        f"♻️ Takroriy rasm (o‘tkazildi): <b>{len(dup_imgs)}</b> ta",
        f"❌ Xato: <b>{len(failed)}</b> ta",
    ]
    for title, items in (("Rasmsiz qolganlar", missing), ("Bu rasmlar botda allaqachon bor", dup_imgs), ("Xatolar", failed)):
        if items:
            lines.append(f"\n<b>{title}:</b>")
            lines.extend(f"• {esc(m)}" for m in items[:15])
            if len(items) > 15:
                lines.append(f"… va yana {len(items) - 15} ta")
    if added:
        lines.append("\n👁 Yangi kartalar <b>«Tushmaganlar»</b> bo‘limida turadi (hali guruhga tushmagan).")
    await message.answer("\n".join(lines))
    # Karta qo'shish menyusi qayta chiqadi — keyingisini darrov qo'shish mumkin
    from keyboards.v2 import admin_v2_add_choice_kb

    kb = admin_v2_add_choice_kb(uid_tg)
    if added:
        kb.inline_keyboard.insert(
            0,
            [InlineKeyboardButton(text="👁 Yangi qo‘shilganlarni ko‘rish", callback_data=AdmV2Cb(owner_id=uid_tg, sec="cards", act="view_start", ext="not_sent", p=1).pack())],
        )
    await message.answer("➕ <b>KARTA QO‘SHISH</b>\n──────────────\nKartalarni qanday usulda qo‘shasiz?", reply_markup=kb)
