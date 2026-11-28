"""Admin auksionlari: yaratish, tugatish, bekor qilish."""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from datetime import timedelta
from sqlalchemy import select

from admin.filters import IsAdminFilter
from admin.v2.common import AdminV2States, _back_home_btn, _cancel_kb, _deny_if_no_perm
from database.session import get_session
from keyboards.v2 import admin_undropped_cards_kb
from models.card import Card
from models.user import User
from models.v2_features import Auction, AuctionBid
from services.admin_service import AdminService
from services.auction_wishlist_service import AuctionWishlistService
from utils.callback_data import AdmV2Cb
from utils.constants import PAGE_SIZE
from utils.formatting import esc
from utils.tg_helpers import NotCommandFilter, edit_or_answer
from utils.timeutils import fmt_tashkent

logger = logging.getLogger(__name__)
router = Router(name="admin_v2_auction")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


# ===========================================================================
# 8. AUCTION (sec="auction", Pick undropped card + Step-by-step + Detail/Edit/End/Cancel)
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
            InlineKeyboardButton(
                text=f"🔨 #{auc.id} {c.character_name[:22]} — {auc.current_price:,} 🪙"[:60],
                callback_data=AdmV2Cb(owner_id=owner_id, sec="auction", act="det", tid=auc.id).pack(),
            )
        )
    if not items:
        lines.append("📭 Hozircha faol auksionlar yo‘q.")
    b.row(_back_home_btn(owner_id))
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


async def _show_auction_detail(target, owner_id: int, auction_id: int) -> None:
    async with get_session() as session:
        auc = await session.get(Auction, auction_id)
        if auc is None:
            await edit_or_answer(target, "❌ Auksion topilmadi.", reply_markup=InlineKeyboardBuilder().row(_back_home_btn(owner_id)).as_markup())
            return
        card = await session.get(Card, auc.card_id)
        seller = await session.get(User, auc.seller_user_id) if auc.seller_user_id else None
        bid_rows = (
            await session.execute(
                select(AuctionBid, User)
                .outerjoin(User, User.id == AuctionBid.user_id)
                .where(AuctionBid.auction_id == auc.id)
                .order_by(AuctionBid.id.desc())
                .limit(6)
            )
        ).all()
    c_name = card.character_name if card else f"Karta #{auc.card_id}"
    seller_lbl = esc(seller.display_name) if seller else "🛡 Admin"
    lines = [
        f"🔨 <b>AUKSION #{auc.id}</b> ({esc(auc.status)})\n━━━━━━━━━━━━━━━",
        f"🎴 Karta: <b>{esc(c_name)}</b>",
        f"👤 Sotuvchi: <b>{seller_lbl}</b>",
        f"💵 Boshlang‘ich narx: <b>{int(auc.start_price):,}</b> 🪙",
        f"💰 Joriy narx: <b>{int(auc.current_price):,}</b> 🪙",
        f"📈 Minimal qadam: <b>+{int(auc.min_step):,}</b> 🪙",
        f"👥 Stavkalar soni: <b>{int(auc.bid_count)}</b> ta",
        f"⏳ Tugash vaqti: <code>{fmt_tashkent(auc.ends_at, '%d.%m.%Y %H:%M')}</code>",
    ]
    if bid_rows:
        lines.append("\n<b>Oxirgi stavkalar:</b>")
        for bid, bu in bid_rows:
            who = esc(bu.display_name) if bu else f"User #{bid.user_id}"
            lines.append(f"• {who}: <b>{int(bid.amount):,}</b> 🪙 (<code>{fmt_tashkent(bid.created_at, '%d.%m %H:%M')}</code>)")

    b = InlineKeyboardBuilder()
    if auc.status == "active":
        b.row(
            InlineKeyboardButton(
                text=("🔒 Boshlang‘ich narx" if auc.bid_count > 0 else "✏️ Boshlang‘ich narx"),
                callback_data=AdmV2Cb(owner_id=owner_id, sec="auction", act="ed_f", tid=auc.id, ext="start").pack(),
            ),
            InlineKeyboardButton(
                text="✏️ Min qadam",
                callback_data=AdmV2Cb(owner_id=owner_id, sec="auction", act="ed_f", tid=auc.id, ext="step").pack(),
            ),
        )
        b.row(
            InlineKeyboardButton(text="⏳ +6 soat", callback_data=AdmV2Cb(owner_id=owner_id, sec="auction", act="ext_h", tid=auc.id, p=6).pack()),
            InlineKeyboardButton(text="⏳ +24 soat", callback_data=AdmV2Cb(owner_id=owner_id, sec="auction", act="ext_h", tid=auc.id, p=24).pack()),
        )
        b.row(
            InlineKeyboardButton(text="🏁 Yakunlash", callback_data=AdmV2Cb(owner_id=owner_id, sec="auction", act="fin", tid=auc.id).pack()),
            InlineKeyboardButton(text="❌ Bekor qilish", callback_data=AdmV2Cb(owner_id=owner_id, sec="auction", act="can", tid=auc.id).pack()),
        )
    b.row(
        InlineKeyboardButton(text="⬅️ Auksionlar", callback_data=AdmV2Cb(owner_id=owner_id, sec="auction", act="v").pack()),
        _back_home_btn(owner_id),
    )
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

    if act == "det":
        await state.clear()
        await _show_auction_detail(callback, owner_id, callback_data.tid)
        return

    if act == "ext_h":
        hrs = max(1, min(168, int(callback_data.p or 6)))
        async with get_session() as session:
            auc = await session.get(Auction, callback_data.tid)
            if auc is None or auc.status != "active":
                await callback.answer("❌ Faol auksion topilmadi.", show_alert=True)
                return
            auc.ends_at = auc.ends_at + timedelta(hours=hrs)
            auc.ending_notified = False
            await session.commit()
        await callback.answer(f"⏳ Auksion muddati +{hrs} soatga uzaytirildi!", show_alert=True)
        await _show_auction_detail(callback, owner_id, callback_data.tid)
        return

    if act == "ed_f":
        field = callback_data.ext
        async with get_session() as session:
            auc = await session.get(Auction, callback_data.tid)
            if auc is None or auc.status != "active":
                await callback.answer("❌ Faol auksion topilmadi.", show_alert=True)
                return
            if field == "start" and int(auc.bid_count or 0) > 0:
                await callback.answer("⚠️ Stavka qo‘yilgandan keyin boshlang‘ich narxni o‘zgartirib bo‘lmaydi.", show_alert=True)
                return
        await state.set_state(AdminV2States.waiting_auc_edit_val)
        await state.update_data(owner_tg_id=owner_id, auc_id=callback_data.tid, auc_edit_field=field)
        prompt = "💵 Yangi <b>boshlang‘ich narx</b>ni (coin) yozing:" if field == "start" else "📈 Yangi <b>minimal qadam</b>ni (kamida 10 coin) yozing:"
        await edit_or_answer(callback, f"✏️ <b>AUKSION #{callback_data.tid}</b>\n\n{prompt}\n<i>Bekor: /cancel</i>", reply_markup=_cancel_kb(owner_id, "auction", "v"))
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


@router.message(AdminV2States.waiting_auc_edit_val, NotCommandFilter())
async def fsm_v2_auc_edit_val(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    field = str(data.get("auc_edit_field") or "")
    auc_id = int(data.get("auc_id") or 0)
    if not raw.isdigit():
        await message.answer("⚠️ Musbat butun son kiriting.")
        return
    val = int(raw)
    if field == "step" and val < 10:
        await message.answer("⚠️ Minimal qadam kamida 10 coin bo‘lishi kerak.")
        return
    if field == "start" and val <= 0:
        await message.answer("⚠️ Boshlang‘ich narx musbat son bo‘lishi kerak.")
        return
    await state.clear()
    async with get_session() as session:
        auc = await session.get(Auction, auc_id)
        if auc is None or auc.status != "active":
            await message.answer("❌ Auksion topilmadi yoki faol emas.")
            return
        if field == "start":
            if int(auc.bid_count or 0) > 0:
                await message.answer("⚠️ Stavka qo‘yilgan auksionning boshlang‘ich narxini o‘zgartirib bo‘lmaydi.")
                return
            auc.start_price = val
            auc.current_price = val
        elif field == "step":
            auc.min_step = val
        await session.commit()
    await message.answer("✅ Auksion yangilandi!")
    await _show_auction_detail(message, message.from_user.id, auc_id)


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
