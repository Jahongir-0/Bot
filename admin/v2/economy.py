"""Admin Bot Do'koni, komissiya sozlamalari va karta takliflari (inbox)."""

import logging
from typing import Optional

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from admin.filters import IsAdminFilter, has_admin_perm
from admin.v2.common import AdminV2States, _back_home_btn, _cancel_kb, _deny_if_no_perm
from database.session import get_session
from models.card import Card
from models.user import User
from services.admin_service import AdminService
from services.auction_wishlist_service import AuctionWishlistService
from services.bot_store_service import BotStoreService
from services.community_service import CommunityService
from services.settings_service import SettingsService
from utils.callback_data import AdmV2Cb, AdminCb, BotStoreCb, SuggestCb
from utils.constants import PAGE_SIZE, get_rarity_meta
from utils.formatting import esc
from utils.tg_helpers import show_card_with_prompt, NotCommandFilter, edit_or_answer

logger = logging.getLogger(__name__)
router = Router(name="admin_v2_economy")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


# ===========================================================================
# 1. BOT DO'KONI ADMIN PANELI (AdmV2Cb sec="econ" + BotStoreCb adm*)
# ===========================================================================
async def _show_bot_store_home(target, owner_id: int) -> None:
    async with get_session() as session:
        cfg = await SettingsService.get_bot_store_config(session)
        _, total = await BotStoreService.list_active_listings(session, page=1, page_size=1)

    lines = [
        "🏛 <b>BOT DO‘KONI BOSHQARUVI</b>\n━━━━━━━━━━━━━━━",
        f"• Do‘kon holati: <b>{'🟢 Yoqilgan' if cfg['enabled'] else '🔴 O‘chirilgan'}</b>",
        f"• Botga sotish: <b>{'🟢 Yoqilgan' if cfg['sell_enabled'] else '🔴 O‘chirilgan'}</b>",
        f"• Botdan olish: <b>{'🟢 Yoqilgan' if cfg['buy_enabled'] else '🔴 O‘chirilgan'}</b>",
        f"• Qayta sotib olish chegirmasi: <b>{int(cfg['sell_discount_pct'])}%</b> (userdan olinganda)",
        f"• Faol katalog kartalari: <b>{total}</b> ta",
    ]
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text=("🔴 Do‘konni o‘chirish" if cfg["enabled"] else "🟢 Do‘konni yoqish"),
            callback_data=AdmV2Cb(owner_id=owner_id, sec="econ", act="bs_tog", ext="enabled").pack(),
        ),
        InlineKeyboardButton(
            text=("🔴 Sotish: ON" if cfg["sell_enabled"] else "🟢 Sotish: OFF"),
            callback_data=AdmV2Cb(owner_id=owner_id, sec="econ", act="bs_tog", ext="sell_enabled").pack(),
        ),
    )
    b.row(
        InlineKeyboardButton(
            text=("🔴 Olish: ON" if cfg["buy_enabled"] else "🟢 Olish: OFF"),
            callback_data=AdmV2Cb(owner_id=owner_id, sec="econ", act="bs_tog", ext="buy_enabled").pack(),
        ),
        InlineKeyboardButton(
            text=f"🏷 Chegirma ({cfg['sell_discount_pct']}%)",
            callback_data=BotStoreCb(owner_id=owner_id, act="disc").pack(),
        ),
    )
    b.row(
        InlineKeyboardButton(
            text="📋 Faol kartalar (o‘chirish)",
            callback_data=BotStoreCb(owner_id=owner_id, act="adm", p=1).pack(),
        ),
        InlineKeyboardButton(
            text="➕ Karta qo‘shish",
            callback_data=BotStoreCb(owner_id=owner_id, act="adm_add", p=1).pack(),
        ),
    )
    b.row(
        InlineKeyboardButton(text="💸 Komissiyalar", callback_data=AdmV2Cb(owner_id=owner_id, sec="econ", act="comm").pack()),
        InlineKeyboardButton(text="⚙️ Sozlamalar", callback_data=AdminCb(owner_id=owner_id, section="settings", action="view").pack()),
    )
    b.row(
        InlineKeyboardButton(text="📜 Do‘kon tarixi (xarid/sotuv)", callback_data=AdmV2Cb(owner_id=owner_id, sec="hist", act="tx", ext="d30-store").pack()),
    )
    b.row(
        InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=owner_id, sec="hub", act="v", ext="cards_evt").pack()),
        _back_home_btn(owner_id),
    )
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


async def _show_bot_store_listings(target, owner_id: int, page: int = 1) -> None:
    page = max(1, page)
    async with get_session() as session:
        items, total = await BotStoreService.list_active_listings(session, page=page, page_size=8)
    lines = [f"🏛 <b>BOT DO‘KONI — FAOL KARTALAR</b> (Jami: <b>{total}</b> ta)\n"]
    b = InlineKeyboardBuilder()
    for lst, card in items:
        meta = get_rarity_meta(card.rarity)
        src = "👤 User" if lst.source == "user_sell" else "🛡 Admin"
        lines.append(f"• <b>#{lst.id}</b> {meta.emoji} {esc(card.character_name)} (Lvl {lst.card_level}) — <b>{lst.price_coins:,}</b> 🪙 ({src})")
        b.row(
            InlineKeyboardButton(
                text=f"🗑 Olib tashlash #{lst.id} ({card.character_name[:18]})"[:60],
                callback_data=BotStoreCb(owner_id=owner_id, act="adm_del", lid=lst.id, p=page).pack(),
            )
        )
    if not items:
        lines.append("📭 Hozircha Bot Do‘konida faol kartalar yo‘q.")
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=BotStoreCb(owner_id=owner_id, act="adm", p=page - 1).pack()))
    if page * 8 < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=BotStoreCb(owner_id=owner_id, act="adm", p=page + 1).pack()))
    if nav:
        b.row(*nav)
    b.row(
        InlineKeyboardButton(text="➕ Karta qo‘shish", callback_data=BotStoreCb(owner_id=owner_id, act="adm_add", p=1).pack()),
        InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=owner_id, sec="econ", act="bs").pack()),
    )
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


@router.callback_query(BotStoreCb.filter(F.act.in_({"adm", "adm_add", "adm_pick", "adm_del", "adm_lvl1", "disc"})))
async def cb_admin_bot_store(
    callback: CallbackQuery,
    callback_data: BotStoreCb,
    state: FSMContext,
    admin_perms: set | None = None,
) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "settings"):
        return
    owner_id = callback.from_user.id
    act = callback_data.act

    if act == "adm":
        await state.clear()
        await _show_bot_store_listings(callback, owner_id, page=callback_data.p)
        return

    if act == "adm_del":
        async with get_session() as session:
            ok = await BotStoreService.admin_remove_listing(session, callback_data.lid)
            if ok:
                await CommunityService.log_audit(session, owner_id, "bot_store_remove", "bot_store", str(callback_data.lid))
                await session.commit()
        await callback.answer("🗑 Do‘kondan olib tashlandi!" if ok else "Topilmadi.", show_alert=True)
        await _show_bot_store_listings(callback, owner_id, page=callback_data.p)
        return

    if act == "adm_add":
        page = max(1, callback_data.p)
        async with get_session() as session:
            cards, total = await AdminService.get_v2_cards_by_tab(session, tab="all", page=page, page_size=10)
        b = InlineKeyboardBuilder()
        for c in cards:
            meta = get_rarity_meta(c.rarity)
            b.row(
                InlineKeyboardButton(
                    text=f"{meta.emoji} {c.character_name[:24]} · {c.anime_name[:16]}"[:60],
                    callback_data=BotStoreCb(owner_id=owner_id, act="adm_pick", lid=c.id, p=page).pack(),
                )
            )
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=BotStoreCb(owner_id=owner_id, act="adm_add", p=page - 1).pack()))
        if page * 10 < total:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=BotStoreCb(owner_id=owner_id, act="adm_add", p=page + 1).pack()))
        if nav:
            b.row(*nav)
        b.row(InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=owner_id, sec="econ", act="bs").pack()))
        await edit_or_answer(
            callback,
            f"➕ <b>BOT DO‘KONIGA KARTA QO‘SHISH</b> (Jami: {total} ta)\nKatalogdan kartani tanlang:",
            reply_markup=b.as_markup(),
        )
        return

    if act == "adm_pick":
        async with get_session() as session:
            card = await session.get(Card, callback_data.lid)
        if card is None or card.is_deleted:
            await callback.answer("Karta topilmadi.", show_alert=True)
            return
        await state.set_state(AdminV2States.waiting_bs_price)
        await state.update_data(owner_tg_id=owner_id, bs_card_id=card.id, bs_card_name=card.character_name)
        meta = get_rarity_meta(card.rarity)
        sug_price = int(card.base_value or meta.default_base_value)
        await show_card_with_prompt(
            callback,
            card,
            f"💡 Tavsiya narx: <code>{sug_price}</code> 🪙\n\nSotish narxini (coin) yozing:\n<i>Bekor: /cancel</i>",
            reply_markup=_cancel_kb(owner_id, "econ", "bs"),
            title="🏛 <b>BOT DO‘KONIGA QO‘SHISH</b>",
        )
        return

    if act == "adm_lvl1":
        data = await state.get_data()
        card_id = int(data.get("bs_card_id") or 0)
        price = int(data.get("bs_price") or 0)
        await state.clear()
        if card_id <= 0 or price <= 0:
            await callback.answer("Jarayon eskirgan.", show_alert=True)
            return
        async with get_session() as session:
            ok, msg, lst = await BotStoreService.admin_add_listing(session, card_id, price, card_level=1)
            if ok and lst is not None:
                await CommunityService.log_audit(session, owner_id, "bot_store_add", "bot_store", str(lst.id), new_value={"card_id": card_id, "price": price, "lvl": 1})
                await session.commit()
        await callback.answer(msg, show_alert=True)
        await _show_bot_store_home(callback, owner_id)
        return

    if act == "disc":
        await state.set_state(AdminV2States.waiting_bs_disc)
        await state.update_data(owner_tg_id=owner_id)
        await edit_or_answer(
            callback,
            "🏷 <b>BOT DO‘KONI CHEGIRMASI</b>\n\nUser kartasini botga sotganda necha % arzon (0–80) narxda qayta sotuvga chiqsin?\nMasalan: <code>15</code>\n<i>Bekor: /cancel</i>",
            reply_markup=_cancel_kb(owner_id, "econ", "bs"),
        )


@router.message(AdminV2States.waiting_bs_price, NotCommandFilter())
async def fsm_v2_bs_price(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("⚠️ Musbat coin narxini kiriting.")
        return
    await state.update_data(bs_price=int(raw))
    await state.set_state(AdminV2States.waiting_bs_level)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Lvl 1 (standart)", callback_data=BotStoreCb(owner_id=message.from_user.id, act="adm_lvl1").pack())],
            [InlineKeyboardButton(text="❌ Bekor", callback_data=AdmV2Cb(owner_id=message.from_user.id, sec="econ", act="bs").pack())],
        ]
    )
    await message.answer("⭐ Karta darajasini (1–20) yozing yoki «Lvl 1» tugmasini bosing:", reply_markup=kb)


@router.message(AdminV2States.waiting_bs_level, NotCommandFilter())
async def fsm_v2_bs_level(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or not (1 <= int(raw) <= 50):
        await message.answer("⚠️ 1 dan 50 gacha daraja kiriting.")
        return
    card_id = int(data.get("bs_card_id") or 0)
    price = int(data.get("bs_price") or 0)
    lvl = int(raw)
    await state.clear()
    async with get_session() as session:
        ok, msg, lst = await BotStoreService.admin_add_listing(session, card_id, price, card_level=lvl)
        if ok and lst is not None:
            await CommunityService.log_audit(session, message.from_user.id, "bot_store_add", "bot_store", str(lst.id), new_value={"card_id": card_id, "price": price, "lvl": lvl})
            await session.commit()
    await message.answer(msg)
    await _show_bot_store_home(message, message.from_user.id)


@router.message(AdminV2States.waiting_bs_disc, NotCommandFilter())
async def fsm_v2_bs_disc(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace("%", "")
    if not raw.isdigit() or not (0 <= int(raw) <= 80):
        await message.answer("⚠️ 0 dan 80 gacha butun son kiriting.")
        return
    await state.clear()
    async with get_session() as session:
        await SettingsService.set_bot_store_field(session, "sell_discount_pct", int(raw))
        await CommunityService.log_audit(session, message.from_user.id, "bot_store_discount", "settings", new_value={"pct": int(raw)})
        await session.commit()
    await message.answer("✅ Chegirma yangilandi!")
    await _show_bot_store_home(message, message.from_user.id)


# ===========================================================================
# 2. KOMISSIYALAR BOSHQARUVI (AdmV2Cb sec="econ", act="comm*")
# ===========================================================================
async def _show_commissions_home(target, owner_id: int) -> None:
    async with get_session() as session:
        cfg = await SettingsService.get_commission_config(session)
    lines = [
        "💸 <b>KOMISSIYALAR BOSHQARUVI</b>\n━━━━━━━━━━━━━━━",
        "Har bir savdo turi uchun komissiyani yoqish/o‘chirish va foizini (0–90%) o‘zgartirish mumkin:\n",
    ]
    b = InlineKeyboardBuilder()
    for key, (label, _def_on, _def_pct) in SettingsService.COMMISSION_KEYS.items():
        entry = cfg.get(key) or {"enabled": False, "percent": 0}
        on = bool(entry.get("enabled"))
        pct = int(entry.get("percent", 0))
        lines.append(f"• {label}: <b>{'🟢 ' + str(pct) + '%' if on else '🔴 O‘chiq (' + str(pct) + '%)'}</b>")
        b.row(
            InlineKeyboardButton(
                text=f"{'🟢' if on else '🔴'} {label}",
                callback_data=AdmV2Cb(owner_id=owner_id, sec="econ", act="comm_tog", ext=key).pack(),
            ),
            InlineKeyboardButton(
                text=f"✏️ {pct}%",
                callback_data=AdmV2Cb(owner_id=owner_id, sec="econ", act="comm_ed", ext=key).pack(),
            ),
        )
    b.row(
        InlineKeyboardButton(text="🏛 Bot Do‘koni", callback_data=AdmV2Cb(owner_id=owner_id, sec="econ", act="bs").pack()),
        InlineKeyboardButton(text="⚙️ Sozlamalar", callback_data=AdminCb(owner_id=owner_id, section="settings", action="view").pack()),
    )
    b.row(_back_home_btn(owner_id))
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


@router.callback_query(AdmV2Cb.filter(F.sec == "econ"))
async def cb_admin_economy(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "settings"):
        return
    owner_id = callback.from_user.id
    act = callback_data.act

    if act == "bs":
        await state.clear()
        await _show_bot_store_home(callback, owner_id)
        return

    if act == "bs_tog":
        field = callback_data.ext
        async with get_session() as session:
            cfg = await SettingsService.get_bot_store_config(session)
            if field in ("enabled", "sell_enabled", "buy_enabled"):
                await SettingsService.set_bot_store_field(session, field, not bool(cfg[field]))
                await session.commit()
        await _show_bot_store_home(callback, owner_id)
        return

    if act == "comm":
        await state.clear()
        await _show_commissions_home(callback, owner_id)
        return

    if act == "comm_tog":
        key = callback_data.ext
        if key not in SettingsService.COMMISSION_KEYS:
            await callback.answer("Noma’lum tur.", show_alert=True)
            return
        async with get_session() as session:
            cfg = await SettingsService.get_commission_config(session)
            await SettingsService.set_commission_field(session, key, enabled=not bool(cfg[key]["enabled"]))
            await CommunityService.log_audit(session, owner_id, "commission_toggle", "settings", key)
            await session.commit()
        await _show_commissions_home(callback, owner_id)
        return

    if act == "comm_ed":
        key = callback_data.ext
        if key not in SettingsService.COMMISSION_KEYS:
            await callback.answer("Noma’lum tur.", show_alert=True)
            return
        label = SettingsService.COMMISSION_KEYS[key][0]
        await state.set_state(AdminV2States.waiting_comm_pct)
        await state.update_data(owner_tg_id=owner_id, comm_key=key)
        await edit_or_answer(
            callback,
            f"✏️ <b>{esc(label)}</b>\n\nYangi komissiya foizini kiriting (0 dan 90 gacha):\n<i>Bekor: /cancel</i>",
            reply_markup=_cancel_kb(owner_id, "econ", "comm"),
        )


@router.message(AdminV2States.waiting_comm_pct, NotCommandFilter())
async def fsm_v2_comm_pct(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace("%", "")
    if not raw.isdigit() or not (0 <= int(raw) <= 90):
        await message.answer("⚠️ 0 dan 90 gacha butun son kiriting.")
        return
    key = str(data.get("comm_key") or "")
    pct = int(raw)
    await state.clear()
    async with get_session() as session:
        await SettingsService.set_commission_field(session, key, enabled=(pct > 0), percent=pct)
        await CommunityService.log_audit(session, message.from_user.id, "commission_percent", "settings", key, new_value={"pct": pct})
        await session.commit()
    await message.answer("✅ Komissiya foizi yangilandi!")
    await _show_commissions_home(message, message.from_user.id)


# ===========================================================================
# 3. KARTA TAKLIFLARI INBOX (SuggestCb act in {"adm", "ok", "no"})
# ===========================================================================
async def _show_suggestions_inbox(target, owner_id: int, page: int = 1) -> None:
    page = max(1, page)
    async with get_session() as session:
        items, total = await AuctionWishlistService.list_suggestions(session, status="all", page=page, page_size=5)
    st_icons = {"pending": "🟡 Kutilmoqda", "accepted": "✅ Qabul qilindi", "rejected": "❌ Rad etildi"}
    lines = [f"💡 <b>KARTA TAKLIFLARI</b> (Jami: <b>{total}</b> ta)\n━━━━━━━━━━━━━━━"]
    b = InlineKeyboardBuilder()
    for sug, user in items:
        who = esc(user.display_name) if user else f"User #{sug.user_id}"
        lines.append(
            f"\n<b>#{sug.id}</b> · {st_icons.get(sug.status, sug.status)}\n"
            f"🎬 [{esc(sug.media_type)}] <b>{esc(sug.anime_name)}</b> — {esc(sug.character_name)}\n"
            f"👤 {who}" + (f"\n📝 {esc(sug.note)}" if sug.note else "")
        )
        if sug.status == "pending":
            b.row(
                InlineKeyboardButton(text=f"✅ Tasdiqlash #{sug.id}", callback_data=SuggestCb(owner_id=owner_id, act="ok", sid=sug.id, p=page).pack()),
                InlineKeyboardButton(text=f"❌ Rad etish #{sug.id}", callback_data=SuggestCb(owner_id=owner_id, act="no", sid=sug.id, p=page).pack()),
            )
    if not items:
        lines.append("\n📭 Hozircha karta takliflari yo‘q.")
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=SuggestCb(owner_id=owner_id, act="adm", p=page - 1).pack()))
    if page * 5 < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=SuggestCb(owner_id=owner_id, act="adm", p=page + 1).pack()))
    if nav:
        b.row(*nav)
    b.row(
        InlineKeyboardButton(text="🎴 Kartalar", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="v").pack()),
        _back_home_btn(owner_id),
    )
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


@router.callback_query(SuggestCb.filter(F.act.in_({"adm", "ok", "no"})))
async def cb_admin_suggestions(
    callback: CallbackQuery,
    callback_data: SuggestCb,
    admin_perms: set | None = None,
) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "cards"):
        return
    owner_id = callback.from_user.id
    act = callback_data.act
    if act == "adm":
        await _show_suggestions_inbox(callback, owner_id, page=callback_data.p)
        return

    new_status = "accepted" if act == "ok" else "rejected"
    target_tg_id: Optional[int] = None
    char_name = ""
    async with get_session() as session:
        sug = await AuctionWishlistService.resolve_suggestion(session, callback_data.sid, new_status)
        if sug is None:
            await callback.answer("Taklif topilmadi.", show_alert=True)
            return
        char_name = sug.character_name
        user = await session.get(User, sug.user_id)
        if user:
            target_tg_id = user.telegram_id
            msg_body = (
                f"🎉 Siz taklif qilgan karta (<b>{esc(sug.character_name)}</b> · {esc(sug.anime_name)}) admin tomonidan tasdiqlandi!"
                if new_status == "accepted"
                else f"ℹ️ Siz taklif qilgan karta (<b>{esc(sug.character_name)}</b> · {esc(sug.anime_name)}) hozircha rad etildi."
            )
            await CommunityService.notify_user(session, user.id, "suggestion", "💡 Karta taklifi javobi", msg_body)
        await CommunityService.log_audit(session, owner_id, f"suggestion_{new_status}", "suggestion", str(sug.id))
        await session.commit()

    if target_tg_id:
        try:
            note_txt = (
                f"🎉 Siz taklif qilgan <b>{esc(char_name)}</b> kartasi admin tomonidan <b>tasdiqlandi</b>!"
                if new_status == "accepted"
                else f"ℹ️ Siz taklif qilgan <b>{esc(char_name)}</b> kartasi admin tomonidan rad etildi."
            )
            await callback.bot.send_message(target_tg_id, note_txt)
        except Exception:
            pass

    await callback.answer("✅ Tasdiqlandi!" if new_status == "accepted" else "❌ Rad etildi!", show_alert=True)
    await _show_suggestions_inbox(callback, owner_id, page=callback_data.p)
