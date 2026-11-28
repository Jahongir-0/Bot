"""Limitli karta eventlari va bonus eventlar."""

import asyncio
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from admin.filters import IsAdminFilter
from admin.handlers.common import AdminStates, _AdminRbacCallbackMiddleware
from app.event_announcer import announce_event
from database.session import get_session
from keyboards.admin import admin_home_kb
from keyboards.event import (
    admin_evt_candidates_kb,
    admin_evt_confirm_kb,
    admin_evt_detail_kb,
    admin_evt_duration_kb,
    admin_evt_menu_kb,
    admin_evt_price_type_kb,
    admin_evt_stars_kb,
)
from models.event import Event
from services.admin_service import AdminService
from services.card_service import CardService
from services.community_service import CommunityService
from services.event_service import EventService
from services.gameplay_service import GameplayService
from services.settings_service import SettingsService
from utils.callback_data import AdminCb
from utils.constants import EVENT_DURATION_HOURS, get_rarity_meta
from utils.formatting import esc
from utils.tg_helpers import show_card_with_prompt, NotCommandFilter, edit_or_answer
from utils.timeutils import to_tashkent

logger = logging.getLogger(__name__)
router = Router(name="admin_events")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())
router.callback_query.middleware(_AdminRbacCallbackMiddleware())


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


_TARGET_LABELS = {
    "groups": "🏰 Faqat guruhlarga",
    "users": "👤 Faqat userlarga",
    "all": "🌍 Guruh va userlarga",
    "none": "🔇 E’lonsiz (jim yaratish)",
}


def _evt_summary(data: dict) -> str:
    prices = []
    if data.get("price_coins"):
        prices.append(f"🪙 <b>{int(data['price_coins']):,}</b> coin")
    if data.get("price_stars"):
        prices.append(f"⭐ <b>{int(data['price_stars']):,}</b> Stars")
    hours = int(data.get("hours", EVENT_DURATION_HOURS))
    meta = get_rarity_meta(data.get("rarity", "Basic"))
    tgt = str(data.get("announce_target", "all"))
    return (
        "🎁 <b>EVENTNI TASDIQLASH</b>\n\n"
        f"🎴 Karta: <b>{esc(data.get('card_name', ''))}</b> ({meta.emoji} {esc(meta.name)})\n"
        f"📺 Media: {esc(data.get('anime_name', ''))}\n"
        f"📦 Nusxa soni: <b>{data.get('copies')}</b> ta\n"
        f"💳 Narx: {'  yoki  '.join(prices)}\n"
        f"⏳ Davomiyligi: <b>{hours} soat</b>\n"
        f"📢 E’lon manzili: <b>{_TARGET_LABELS.get(tgt, _TARGET_LABELS['all'])}</b>"
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
        await show_card_with_prompt(
            callback,
            card,
            "📦 Necha nusxa sotiladi? Sonni kiriting:",
            title="🎁 <b>YANGI EVENT</b>",
        )
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
        tgt = str(data.get("announce_target", "all"))
        await edit_or_answer(callback, _evt_summary(data), reply_markup=admin_evt_confirm_kb(admin_id, target=tgt))
        return

    if action == "etgt":
        tgt = callback_data.extra if callback_data.extra in _TARGET_LABELS else "all"
        await state.update_data(announce_target=tgt)
        data = await state.get_data()
        if "card_id" not in data or "copies" not in data:
            await callback.answer("Jarayon eskirgan.", show_alert=True)
            return
        await edit_or_answer(callback, _evt_summary(data), reply_markup=admin_evt_confirm_kb(admin_id, target=tgt))
        return

    if action == "go":
        data = await state.get_data()
        if not all(k in data for k in ("card_id", "copies")):
            await callback.answer("Jarayon eskirgan.", show_alert=True)
            return
        hours = int(data.get("hours", EVENT_DURATION_HOURS))
        target = str(data.get("announce_target", "all"))
        if target not in _TARGET_LABELS:
            target = "all"
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
        if target != "none":
            task = asyncio.create_task(announce_event(callback.bot, res.event.id, notify_admin_id=admin_id, target=target))
            _BG_TASKS.add(task)
            task.add_done_callback(_BG_TASKS.discard)
            note = f"📢 E’lon ({_TARGET_LABELS[target]}) yuborilmoqda..."
        else:
            note = "🔇 Event e’lonsiz ishga tushirildi."
        await callback.answer("✅ Event boshlandi!", show_alert=True)
        await edit_or_answer(
            callback,
            f"✅ <b>Event #{res.event.id} boshlandi!</b>\n{note}",
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
    tgt = str(data.get("announce_target", "all"))
    await message.answer(_evt_summary(data), reply_markup=admin_evt_confirm_kb(message.from_user.id, target=tgt))
