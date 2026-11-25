"""Admin auksionlari: yaratish, tugatish, bekor qilish."""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from admin.filters import IsAdminFilter
from admin.v2.common import AdminV2States, _back_home_btn, _cancel_kb, _deny_if_no_perm
from database.session import get_session
from keyboards.v2 import admin_undropped_cards_kb
from services.admin_service import AdminService
from services.auction_wishlist_service import AuctionWishlistService
from utils.callback_data import AdmV2Cb
from utils.constants import PAGE_SIZE
from utils.formatting import esc
from utils.tg_helpers import NotCommandFilter, edit_or_answer

logger = logging.getLogger(__name__)
router = Router(name="admin_v2_auction")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


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
