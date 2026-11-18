"""⚡ TEZKOR KARTA QO'SHISH

Bir asardan ko'p karta qo'shishning eng tez yo'li:
  1) Turini tanlang (Anime, Manga, ...)  2) Asar nomini yozing  3) Rankni tanlang
  4) Rasmlarni yuboring. Rasm izohiga (caption) personaj ismini yozsangiz karta darhol qo'shiladi.
     Izoh yo'q bo'lsa, bot "Bu kim?" deb so'raydi, ismni yozasiz, tamom.

Rasm Telegram'ga qayta yuklanmaydi: file_id ishlatiladi, shuning uchun juda tez va Railway'da yo'qolmaydi.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from admin.filters import IsAdminFilter, has_admin_perm
from database.session import get_session
from models.card import Card
from services.card_service import CardService
from services.card_zip_import import rank_table, resolve_rank
from services.community_service import CommunityService
from services.settings_service import SettingsService
from utils.callback_data import AdmV2Cb
from utils.constants import MEDIA_TYPES, get_rarity_meta
from utils.formatting import esc
from utils.tg_helpers import NotCommandFilter, edit_or_answer

logger = logging.getLogger(__name__)

router = Router(name="admin_quick_add")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())

SEC = "qadd"
_LOCKS: Dict[int, asyncio.Lock] = {}


class QuickAddStates(StatesGroup):
    media_name = State()  # asar nomini kutish
    photos = State()      # rasmlarni kutish
    name = State()        # rasm uchun personaj ismini kutish


def _lock(user_id: int) -> asyncio.Lock:
    return _LOCKS.setdefault(user_id, asyncio.Lock())


def _cb(owner_id: int, act: str, ext: str = "") -> str:
    return AdmV2Cb(owner_id=owner_id, sec=SEC, act=act, ext=ext).pack()


def _btn(owner_id: int, text: str, act: str, ext: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=_cb(owner_id, act, ext))


def _type_kb(owner_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for i, m in enumerate(MEDIA_TYPES):
        b.add(_btn(owner_id, m, "type", str(i)))
    b.adjust(3)
    b.row(InlineKeyboardButton(text="⬅️ Orqaga", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="add_menu").pack()))
    return b.as_markup()


def _rank_kb(owner_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for rid, name in rank_table():
        b.add(_btn(owner_id, f"{get_rarity_meta(name).emoji} {rid} · {name}", "rank", str(rid)))
    b.adjust(2)
    return b.as_markup()


def _work_kb(owner_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn(owner_id, "🎚 Rankni almashtirish", "rank_pick"), _btn(owner_id, "🔄 Boshqa asar", "new"))
    b.row(_btn(owner_id, "🏁 Tugatish", "done"))
    return b.as_markup()


def _skip_kb(owner_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(owner_id, "⏭ Bu rasmni o‘tkazib yuborish", "skip")]])


async def _perm_ok(callback: CallbackQuery, admin_perms: Optional[set]) -> bool:
    if not has_admin_perm(callback.from_user, admin_perms, "cards"):
        await callback.answer("⛔ Bu bo‘limga ruxsatingiz yo‘q.", show_alert=True)
        return False
    return True


# ---------------------------------------------------------------- status
def _status_text(data: dict) -> str:
    rarity = data.get("q_rank", "Common")
    meta = get_rarity_meta(rarity)
    recent: List[str] = data.get("q_recent", [])[-8:]
    lines = [
        "⚡ <b>TEZKOR QO‘SHISH</b>",
        "",
        f"📺 Asar: <b>{esc(data.get('q_media', '—'))}</b> ({esc(data.get('q_type', '—'))})",
        f"⭐ Rank: <b>{meta.emoji} {esc(rarity)}</b>",
        f"✅ Qo‘shildi: <b>{data.get('q_added', 0)}</b>   ♻️ Takroriy: <b>{data.get('q_dups', 0)}</b>",
    ]
    if recent:
        lines += ["", "<b>Oxirgilari:</b>"] + [f"• {esc(x)}" for x in recent]
    lines += ["", "🖼 Keyingi rasmni yuboring (izohga personaj ismini yozing)."]
    return "\n".join(lines)


async def _render_status(message: Message, state: FSMContext, owner_id: int) -> None:
    data = await state.get_data()
    text = _status_text(data)
    kb = _work_kb(owner_id)
    sid = data.get("q_status_id")
    if sid:
        try:
            await message.bot.edit_message_text(text, chat_id=message.chat.id, message_id=sid, reply_markup=kb)
            return
        except Exception:
            pass
    sent = await message.answer(text, reply_markup=kb)
    await state.update_data(q_status_id=sent.message_id)


# ---------------------------------------------------------------- start / setup
@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "start")))
@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "new")))
async def cb_start(callback: CallbackQuery, state: FSMContext, admin_perms: Optional[set] = None) -> None:
    if not await _perm_ok(callback, admin_perms):
        return
    await state.clear()
    await edit_or_answer(
        callback,
        "⚡ <b>TEZKOR QO‘SHISH</b>\n\n1/3 — Bu kartalar qaysi turdagi asardan?",
        reply_markup=_type_kb(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "type")))
async def cb_type(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: Optional[set] = None) -> None:
    if not await _perm_ok(callback, admin_perms):
        return
    try:
        mtype = MEDIA_TYPES[int(callback_data.ext)]
    except (ValueError, IndexError):
        await callback.answer("Tur topilmadi", show_alert=True)
        return
    await state.set_state(QuickAddStates.media_name)
    await state.update_data(q_type=mtype, q_added=0, q_dups=0, q_recent=[], q_queue=[], q_status_id=None)
    await edit_or_answer(
        callback,
        f"⚡ 2/3 — <b>{esc(mtype)}</b> nomini yozing.\nMasalan: <i>Naruto</i>, <i>Solo Leveling</i>\n\n<i>Bekor qilish: /cancel</i>",
    )
    await callback.answer()


@router.message(QuickAddStates.media_name, F.text, NotCommandFilter())
async def msg_media_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not (1 <= len(name) <= 120):
        await message.answer("⚠️ Nom 1 dan 120 gacha belgi bo‘lsin.")
        return
    await state.update_data(q_media=name)
    await message.answer("⚡ 3/3 — Bu rasmlar qaysi rank bo‘lsin?", reply_markup=_rank_kb(message.from_user.id))


@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "rank_pick")))
async def cb_rank_pick(callback: CallbackQuery, admin_perms: Optional[set] = None) -> None:
    if not await _perm_ok(callback, admin_perms):
        return
    await edit_or_answer(callback, "🎚 Yangi rasmlar uchun rankni tanlang:", reply_markup=_rank_kb(callback.from_user.id))
    await callback.answer()


@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "rank")))
async def cb_rank(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: Optional[set] = None) -> None:
    if not await _perm_ok(callback, admin_perms):
        return
    rarity = resolve_rank(callback_data.ext)
    data = await state.get_data()
    if rarity is None or not data.get("q_media"):
        await callback.answer("Avval asar nomini kiriting (⚡ Tezkor qo‘shishni qaytadan boshlang).", show_alert=True)
        return
    await state.update_data(q_rank=rarity, q_status_id=callback.message.message_id)
    if await state.get_state() != QuickAddStates.name.state:
        await state.set_state(QuickAddStates.photos)
    data = await state.get_data()
    await edit_or_answer(callback, _status_text(data), reply_markup=_work_kb(callback.from_user.id))
    await callback.answer(f"Rank: {rarity}")


# ---------------------------------------------------------------- core: bitta kartani qo'shish
async def _add_one(data: dict, file_id: str, character: str, admin_tg_id: int) -> str:
    """'ok' | 'dup' | xato matni"""
    rarity = data["q_rank"]
    media = data["q_media"]
    try:
        async with get_session() as session:
            dup = (
                await session.execute(
                    select(Card.id).where(
                        func.lower(Card.character_name) == character.lower(),
                        func.lower(Card.anime_name) == media.lower(),
                        Card.rarity == rarity,
                        Card.is_deleted.is_(False),
                    )
                )
            ).first()
            if dup is not None:
                return "dup"
            rcfg = await SettingsService.get_rarity_config(session)
            card = await CardService.create_card(
                session,
                character_name=character,
                anime_name=media,
                image=file_id,
                rarity=rarity,
                base_value=rcfg.get(rarity, {}).get("value", 1000),
                drop_limit=rcfg.get(rarity, {}).get("drops", 1),
                media_type=data["q_type"],
            )
            card.image_file_id = file_id
            await CommunityService.log_audit(
                session, admin_tg_id, "card_quick_add", "card", str(card.id),
                details={"name": character, "media": media, "rank": rarity},
            )
            await session.commit()
        return "ok"
    except Exception as exc:
        logger.exception("Tezkor qo'shishda xato")
        return f"xato: {str(exc)[:80]}"


async def _process_queue(message: Message, state: FSMContext, admin_tg_id: int) -> None:
    """Navbatdagi rasmlarni qo'shadi. Ismi yo'q rasmga kelsa, ism so'raydi."""
    while True:
        data = await state.get_data()
        queue: List[list] = data.get("q_queue", [])
        if not queue:
            await state.set_state(QuickAddStates.photos)
            await _render_status(message, state, admin_tg_id)
            return
        file_id, cname = queue[0]
        if not cname:
            if await state.get_state() != QuickAddStates.name.state:
                await state.set_state(QuickAddStates.name)
                waiting = len(queue)
                extra = f" (navbatda yana {waiting - 1} ta rasm bor)" if waiting > 1 else ""
                await message.answer_photo(
                    file_id,
                    caption=f"❓ <b>Bu kim?</b> Personaj ismini yozing{extra}.",
                    reply_markup=_skip_kb(admin_tg_id),
                )
            return
        result = await _add_one(data, file_id, cname, admin_tg_id)
        recent = list(data.get("q_recent", []))
        if result == "ok":
            recent.append(cname)
            await state.update_data(q_added=data.get("q_added", 0) + 1, q_recent=recent[-8:])
        elif result == "dup":
            await state.update_data(q_dups=data.get("q_dups", 0) + 1)
            recent.append(f"{cname} (takroriy, o‘tkazildi)")
            await state.update_data(q_recent=recent[-8:])
        else:
            recent.append(f"{cname} ❌ {result}")
            await state.update_data(q_recent=recent[-8:])
        await state.update_data(q_queue=queue[1:])


# ---------------------------------------------------------------- rasm / ism qabul qilish
@router.message(QuickAddStates.photos, F.photo)
@router.message(QuickAddStates.name, F.photo)
async def msg_photo(message: Message, state: FSMContext) -> None:
    async with _lock(message.from_user.id):
        data = await state.get_data()
        if not data.get("q_media") or not data.get("q_rank"):
            await message.answer("⚠️ Avval rankni tanlang (yuqoridagi tugmalar).")
            return
        caption = (message.caption or "").strip()[:128] or None
        queue = list(data.get("q_queue", []))
        queue.append([message.photo[-1].file_id, caption])
        await state.update_data(q_queue=queue)
        await _process_queue(message, state, message.from_user.id)


@router.message(QuickAddStates.photos, F.document)
@router.message(QuickAddStates.name, F.document)
async def msg_document_hint(message: Message) -> None:
    await message.answer("⚠️ Rasmni <b>fayl</b> sifatida emas, oddiy <b>rasm</b> sifatida yuboring (siqilgan holda).")


@router.message(QuickAddStates.name, F.text, NotCommandFilter())
async def msg_name(message: Message, state: FSMContext) -> None:
    character = (message.text or "").strip()
    if not (1 <= len(character) <= 128):
        await message.answer("⚠️ Ism 1 dan 128 gacha belgi bo‘lsin.")
        return
    async with _lock(message.from_user.id):
        data = await state.get_data()
        queue = list(data.get("q_queue", []))
        if not queue:
            await state.set_state(QuickAddStates.photos)
            return
        queue[0][1] = character
        await state.update_data(q_queue=queue)
        await state.set_state(QuickAddStates.photos)  # keyingisi ismsiz bo'lsa, _process_queue qayta so'raydi
        await _process_queue(message, state, message.from_user.id)


@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "skip")))
async def cb_skip(callback: CallbackQuery, state: FSMContext, admin_perms: Optional[set] = None) -> None:
    if not await _perm_ok(callback, admin_perms):
        return
    async with _lock(callback.from_user.id):
        data = await state.get_data()
        queue = list(data.get("q_queue", []))
        if queue:
            queue = queue[1:]
        await state.update_data(q_queue=queue)
        await state.set_state(QuickAddStates.photos)
        await callback.answer("O‘tkazib yuborildi")
        try:
            await callback.message.delete()
        except Exception:
            pass
        await _process_queue(callback.message, state, callback.from_user.id)


@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "done")))
async def cb_done(callback: CallbackQuery, state: FSMContext, admin_perms: Optional[set] = None) -> None:
    if not await _perm_ok(callback, admin_perms):
        return
    data = await state.get_data()
    await state.clear()
    owner_id = callback.from_user.id
    b = InlineKeyboardBuilder()
    b.row(_btn(owner_id, "⚡ Yana qo‘shish", "start"))
    b.row(InlineKeyboardButton(text="🎴 Card bo‘limi", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="v").pack()))
    await edit_or_answer(
        callback,
        "🏁 <b>TUGADI</b>\n\n"
        f"📺 {esc(data.get('q_media', '—'))}\n"
        f"✅ Qo‘shildi: <b>{data.get('q_added', 0)}</b>\n"
        f"♻️ Takroriy: <b>{data.get('q_dups', 0)}</b>",
        reply_markup=b.as_markup(),
    )
    await callback.answer()
