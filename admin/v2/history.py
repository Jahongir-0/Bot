"""Admin: TARIX VA HISOBOT — kim nima qildi, kim nima sotdi/sotib oldi, komissiya, foydalanuvchi tarixi, jonli xabarlar."""
from __future__ import annotations

import logging
import math
from typing import List

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from admin.filters import IsAdminFilter
from admin.v2.common import _back_home_btn, _deny_if_no_perm
from database.session import get_session
from models.user import User
from services.history_service import (
    PERIODS,
    TX_CATEGORIES,
    AdminFeedService,
    HistoryService,
    period_label,
    period_since,
)
from utils.callback_data import AdmV2Cb
from utils.formatting import esc, fmt_coins
from utils.tg_helpers import NotCommandFilter, edit_or_answer
from utils.timeutils import fmt_tashkent

logger = logging.getLogger(__name__)
router = Router(name="admin_v2_history")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())

SEC = "hist"
PER_PAGE_TX = 12


class HistoryStates(StatesGroup):
    waiting_user = State()


def _cb(owner: int, act: str, **kw) -> str:
    return AdmV2Cb(owner_id=owner, sec=SEC, act=act, **kw).pack()


def _period_row(b: InlineKeyboardBuilder, owner: int, act: str, active: str, extra: str = "", tid: int = 0) -> None:
    b.row(
        *[
            InlineKeyboardButton(
                text=("✅ " if key == active else "") + label,
                callback_data=_cb(owner, act, ext=f"{key}-{extra}" if extra else key, tid=tid),
            )
            for key, label in PERIODS
        ]
    )


def _nav_row(b: InlineKeyboardBuilder, owner: int, act: str, ext: str, page: int, total: int, per_page: int, tid: int = 0) -> None:
    pages = max(1, math.ceil(total / per_page))
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=_cb(owner, act, ext=ext, p=page - 1, tid=tid)))
    nav.append(InlineKeyboardButton(text=f"{page}/{pages}", callback_data=_cb(owner, act, ext=ext, p=page, tid=tid)))
    if page < pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=_cb(owner, act, ext=ext, p=page + 1, tid=tid)))
    b.row(*nav)


def _short_cat(key: str, label: str) -> str:
    return "Hammasi" if key == "all" else label.split(" ", 1)[-1][:12]


def _menu_btn(b: InlineKeyboardBuilder, owner: int) -> None:
    b.row(
        InlineKeyboardButton(text="⬅️ Tarix menyusi", callback_data=_cb(owner, "v")),
        _back_home_btn(owner),
    )


# ---------------------------------------------------------------------------
# Asosiy menyu
# ---------------------------------------------------------------------------
@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "v")))
async def cb_hist_menu(callback: CallbackQuery, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "history"):
        return
    await state.clear()
    o = callback.from_user.id
    async with get_session() as session:
        feed = await AdminFeedService.get_cfg(session)
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="📊 Umumiy hisobot", callback_data=_cb(o, "rep", ext="d7")),
        InlineKeyboardButton(text="🧾 Barcha harakatlar", callback_data=_cb(o, "tx", ext="d7-all")),
    )
    b.row(
        InlineKeyboardButton(text="🛒 Bozor savdolari", callback_data=_cb(o, "mk", ext="d7")),
        InlineKeyboardButton(text="💸 Komissiya", callback_data=_cb(o, "comm", ext="d7")),
    )
    b.row(
        InlineKeyboardButton(text="🏆 Top foydalanuvchilar", callback_data=_cb(o, "top", ext="d7-seller")),
        InlineKeyboardButton(text="🎴 Olingan kartalar", callback_data=_cb(o, "claims", ext="d7")),
    )
    b.row(InlineKeyboardButton(text="👤 Foydalanuvchi tarixi (qidirish)", callback_data=_cb(o, "usr_s")))
    b.row(
        InlineKeyboardButton(
            text=f"🔔 Jonli xabarlar: {'✅ yoqilgan' if feed.get('enabled') else '❌ o‘chiq'}",
            callback_data=_cb(o, "feed"),
        )
    )
    b.row(
        InlineKeyboardButton(text="📋 Admin harakatlari jurnali", callback_data=AdmV2Cb(owner_id=o, sec="logs", act="v").pack()),
        InlineKeyboardButton(text="📈 Faollik", callback_data=AdmV2Cb(owner_id=o, sec="report", act="v").pack()),
    )
    b.row(_back_home_btn(o))
    await edit_or_answer(
        callback,
        "📜 <b>TARIX VA HISOBOT</b>\n━━━━━━━━━━━━━━━\n"
        "Foydalanuvchilar nima qilgani, kim nima sotgani va sotib olgani, komissiya daromadi va hisobotlar.\n\n"
        "👇 Kerakli bo‘limni tanlang:",
        reply_markup=b.as_markup(),
    )


# ---------------------------------------------------------------------------
# Umumiy hisobot
# ---------------------------------------------------------------------------
@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "rep")))
async def cb_hist_report(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "history"):
        return
    period = (callback_data.ext or "d7").split("-")[0]
    async with get_session() as session:
        r = await HistoryService.overview(session, period_since(period))
    via_names = {"drop": "guruhda topildi", "market": "bozordan", "auction": "auksiondan", "transfer": "sovg‘a/transfer", "pack": "paketdan", "group_reward": "guruh mukofoti", "admin": "admin bergan", "event": "eventdan", "store": "do‘kondan"}
    via = ", ".join(f"{via_names.get(k, k)} {v}" for k, v in sorted(r["cards_by_via"].items(), key=lambda x: -x[1])[:6]) or "—"
    fee_lines = ", ".join(f"{k}: {fmt_coins(v)}" for k, v in r["commission_by_type"].items()) or "—"
    txt = (
        f"📊 <b>HISOBOT — {esc(period_label(period))}</b>\n━━━━━━━━━━━━━━━\n"
        f"👥 <b>Foydalanuvchilar</b>\n"
        f"• Jami: <b>{r['total_users']:,}</b> · yangi: <b>{r['new_users']:,}</b> · faol: <b>{r['active_users']:,}</b>\n\n"
        f"🪙 <b>Coin aylanmasi</b> ({r['tx_count']:,} ta harakat)\n"
        f"• Kirim: <b>+{fmt_coins(r['coins_in'])}</b> · Chiqim: <b>−{fmt_coins(r['coins_out'])}</b>\n\n"
        f"🛒 <b>Savdo</b>\n"
        f"• Bozor sotuvlari: <b>{r['market_count']:,}</b> ta · hajmi <b>{fmt_coins(r['market_volume'])}</b> 🪙\n"
        f"• Auksion sotuvlari: <b>{r['auction_sales']:,}</b> ta\n"
        f"• Bot do‘konidan xarid: <b>{r['store_buys']:,}</b> ta ({fmt_coins(r['store_spent'])} 🪙)\n"
        f"• ⭐ Stars xaridlari: <b>{r['stars_count']:,}</b> ta (+{fmt_coins(r['stars_coins'])} 🪙)\n\n"
        f"💸 <b>Komissiya daromadi: {fmt_coins(r['commission_total'])} 🪙</b>\n"
        f"<i>{esc(fee_lines)}</i>\n\n"
        f"🎴 <b>Kartalar qanday olingan:</b> {esc(via)}\n"
        f"🏰 Yangi guruhlar: <b>{r['groups_new']}</b> · qo‘shish mukofoti berilgan: <b>{r['groups_rewarded']}</b>\n"
        f"💬 Yangi murojaatlar: <b>{r['tickets']}</b>"
    )
    o = callback.from_user.id
    b = InlineKeyboardBuilder()
    _period_row(b, o, "rep", period)
    b.row(
        InlineKeyboardButton(text="🧾 Harakatlar", callback_data=_cb(o, "tx", ext=f"{period}-all")),
        InlineKeyboardButton(text="🛒 Savdolar", callback_data=_cb(o, "mk", ext=period)),
    )
    _menu_btn(b, o)
    await edit_or_answer(callback, txt, reply_markup=b.as_markup())


# ---------------------------------------------------------------------------
# Barcha harakatlar (tranzaksiya jurnali)
# ---------------------------------------------------------------------------
@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "tx")))
async def cb_hist_tx(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "history"):
        return
    parts = (callback_data.ext or "d7-all").split("-")
    period = parts[0]
    category = parts[1] if len(parts) > 1 and parts[1] in TX_CATEGORIES else "all"
    page = max(1, callback_data.p)
    async with get_session() as session:
        rows, total = await HistoryService.tx_page(session, period_since(period), category, page, PER_PAGE_TX)
    lines = [
        f"🧾 <b>HARAKATLAR JURNALI</b> — {esc(period_label(period))} · {esc(TX_CATEGORIES[category][0])}\n"
        f"Jami: <b>{total:,}</b> ta | sahifa {page}\n━━━━━━━━━━━━━━━"
    ]
    for tx, user in rows:
        lines.append(HistoryService.format_tx_line(tx, user))
    if not rows:
        lines.append("📭 Hozircha harakat yo‘q.")
    o = callback.from_user.id
    b = InlineKeyboardBuilder()
    _period_row(b, o, "tx", period, extra=category)
    cats = list(TX_CATEGORIES.items())
    for i in range(0, len(cats), 3):
        b.row(
            *[
                InlineKeyboardButton(
                    text=("✅ " if key == category else "") + _short_cat(key, label),
                    callback_data=_cb(o, "tx", ext=f"{period}-{key}"),
                )
                for key, (label, _t) in cats[i : i + 3]
            ]
        )
    _nav_row(b, o, "tx", f"{period}-{category}", page, total, PER_PAGE_TX)
    _menu_btn(b, o)
    await edit_or_answer(callback, "\n".join(lines)[:4000], reply_markup=b.as_markup())


# ---------------------------------------------------------------------------
# Bozor savdolari: kim kimga nimani qanchaga sotdi
# ---------------------------------------------------------------------------
@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "mk")))
async def cb_hist_market(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "history"):
        return
    period = (callback_data.ext or "d7").split("-")[0]
    page = max(1, callback_data.p)
    per_page = 8
    async with get_session() as session:
        sales, total = await HistoryService.market_sales_page(session, period_since(period), page, per_page)
    lines = [
        f"🛒 <b>BOZOR SAVDOLARI</b> — {esc(period_label(period))}\nJami: <b>{total:,}</b> ta | sahifa {page}\n━━━━━━━━━━━━━━━"
    ]
    for s in sales:
        fee = f" · 💸 komissiya {fmt_coins(s['fee'])}" if s["fee"] else ""
        lines.append(
            f"<code>{fmt_tashkent(s['when'], '%d.%m %H:%M')}</code>\n"
            f"🎴 <b>{esc(s['card'])}</b> <code>{esc(s['code'])}</code>\n"
            f"💰 {esc(s['seller'])} ➜ 🛒 {esc(s['buyer'])} · <b>{fmt_coins(s['price'])}</b> 🪙{fee}\n"
        )
    if not sales:
        lines.append("📭 Bu davrda savdo bo‘lmagan.")
    o = callback.from_user.id
    b = InlineKeyboardBuilder()
    _period_row(b, o, "mk", period)
    _nav_row(b, o, "mk", period, page, total, per_page)
    _menu_btn(b, o)
    await edit_or_answer(callback, "\n".join(lines)[:4000], reply_markup=b.as_markup())


# ---------------------------------------------------------------------------
# Komissiya
# ---------------------------------------------------------------------------
@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "comm")))
async def cb_hist_commission(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "history"):
        return
    period = (callback_data.ext or "d7").split("-")[0]
    names = {
        "market_sell": "🛒 Bozor savdosi",
        "offer_accepted_sale": "🤝 Qabul qilingan takliflar",
        "paid_transfer_sell": "🎁 Pullik sovg‘alar",
        "auction_sale": "🔨 Auksion sotuvlari",
        "coin_gift_sent": "💸 Coin yuborish",
    }
    async with get_session() as session:
        total, by_type = await HistoryService.commission(session, period_since(period))
    lines = [f"💸 <b>KOMISSIYA DAROMADI</b> — {esc(period_label(period))}\n━━━━━━━━━━━━━━━", f"Jami: <b>{fmt_coins(total)}</b> 🪙\n"]
    for key, label in names.items():
        lines.append(f"{label}: <b>{fmt_coins(by_type.get(key, 0))}</b> 🪙")
    lines.append("\n<i>Komissiya foizlarini «Komissiyalar» bo‘limidan o‘zgartirasiz.</i>")
    o = callback.from_user.id
    b = InlineKeyboardBuilder()
    _period_row(b, o, "comm", period)
    b.row(InlineKeyboardButton(text="⚙️ Komissiya sozlamalari", callback_data=AdmV2Cb(owner_id=o, sec="econ", act="comm").pack()))
    _menu_btn(b, o)
    await edit_or_answer(callback, "\n".join(lines), reply_markup=b.as_markup())


# ---------------------------------------------------------------------------
# Top foydalanuvchilar
# ---------------------------------------------------------------------------
_TOP_KINDS = [("seller", "💰 Sotuvchilar"), ("buyer", "🛒 Xaridorlar"), ("spender", "📉 Eng ko‘p sarflagan"), ("earner", "📈 Eng ko‘p topgan")]


@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "top")))
async def cb_hist_top(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "history"):
        return
    parts = (callback_data.ext or "d7-seller").split("-")
    period = parts[0]
    kind = parts[1] if len(parts) > 1 and parts[1] in dict(_TOP_KINDS) else "seller"
    async with get_session() as session:
        rows = await HistoryService.top_users(session, period_since(period), kind, 10)
    lines = [f"🏆 <b>{esc(dict(_TOP_KINDS)[kind])}</b> — {esc(period_label(period))}\n━━━━━━━━━━━━━━━"]
    o = callback.from_user.id
    b = InlineKeyboardBuilder()
    for i, (u, cnt, s) in enumerate(rows, 1):
        lines.append(f"{i}. {esc(u.display_name)} — <b>{fmt_coins(s)}</b> 🪙 ({cnt} ta)")
        b.row(InlineKeyboardButton(text=f"{i}. {u.display_name[:24]} — tarixi", callback_data=_cb(o, "usr", tid=u.id, ext="all")))
    if not rows:
        lines.append("📭 Ma’lumot yo‘q.")
    _period_row(b, o, "top", period, extra=kind)
    b.row(*[InlineKeyboardButton(text=("✅ " if k == kind else "") + lbl, callback_data=_cb(o, "top", ext=f"{period}-{k}")) for k, lbl in _TOP_KINDS[:2]])
    b.row(*[InlineKeyboardButton(text=("✅ " if k == kind else "") + lbl, callback_data=_cb(o, "top", ext=f"{period}-{k}")) for k, lbl in _TOP_KINDS[2:]])
    _menu_btn(b, o)
    await edit_or_answer(callback, "\n".join(lines), reply_markup=b.as_markup())


# ---------------------------------------------------------------------------
# Olingan kartalar
# ---------------------------------------------------------------------------
@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "claims")))
async def cb_hist_claims(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "history"):
        return
    period = (callback_data.ext or "d7").split("-")[0]
    page = max(1, callback_data.p)
    per_page = 10
    async with get_session() as session:
        rows, total = await HistoryService.claims_page(session, period_since(period), page, per_page)
    via_names = {"drop": "🎴 topdi", "market": "🛒 bozordan", "auction": "🔨 auksiondan", "transfer": "🎁 sovg‘a", "pack": "📦 paket", "group_reward": "🏰 mukofot", "admin": "🛡 admin", "event": "🎯 event", "store": "🏛 do‘kon"}
    lines = [f"🎴 <b>OLINGAN KARTALAR</b> — {esc(period_label(period))}\nJami: <b>{total:,}</b> ta | sahifa {page}\n━━━━━━━━━━━━━━━"]
    for uc, card, user in rows:
        lines.append(
            f"<code>{fmt_tashkent(uc.created_at, '%d.%m %H:%M')}</code> {esc(user.display_name)} — "
            f"<b>{esc(card.character_name)}</b> · {via_names.get(uc.acquired_via, esc(uc.acquired_via))}"
        )
    if not rows:
        lines.append("📭 Yo‘q.")
    o = callback.from_user.id
    b = InlineKeyboardBuilder()
    _period_row(b, o, "claims", period)
    _nav_row(b, o, "claims", period, page, total, per_page)
    _menu_btn(b, o)
    await edit_or_answer(callback, "\n".join(lines)[:4000], reply_markup=b.as_markup())


# ---------------------------------------------------------------------------
# Foydalanuvchi tarixi
# ---------------------------------------------------------------------------
@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "usr_s")))
async def cb_hist_user_search(callback: CallbackQuery, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "history"):
        return
    await state.set_state(HistoryStates.waiting_user)
    await state.update_data(owner_tg_id=callback.from_user.id)
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="❌ Bekor", callback_data=_cb(callback.from_user.id, "v")))
    await edit_or_answer(
        callback,
        "👤 <b>FOYDALANUVCHI TARIXI</b>\n\nTelegram ID, <code>@username</code> yoki ism yozing:\n<i>Bekor qilish: /cancel</i>",
        reply_markup=b.as_markup(),
    )


@router.message(HistoryStates.waiting_user, NotCommandFilter())
async def fsm_hist_user_search(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    q = (message.text or "").strip()
    await state.clear()
    o = message.from_user.id
    async with get_session() as session:
        users: List[User] = await HistoryService.find_users(session, q)
    b = InlineKeyboardBuilder()
    for u in users:
        b.row(InlineKeyboardButton(text=f"{u.display_name[:26]} · {u.telegram_id}", callback_data=_cb(o, "usr", tid=u.id, ext="all")))
    b.row(InlineKeyboardButton(text="⬅️ Tarix menyusi", callback_data=_cb(o, "v")))
    if not users:
        await message.answer(f"📭 <code>{esc(q)}</code> bo‘yicha foydalanuvchi topilmadi.", reply_markup=b.as_markup())
        return
    await message.answer(f"🔍 Topildi: <b>{len(users)}</b> ta. Tanlang:", reply_markup=b.as_markup())


@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "usr")))
async def cb_hist_user(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "history"):
        return
    period = (callback_data.ext or "all").split("-")[0]
    page = max(1, callback_data.p)
    async with get_session() as session:
        user = await session.get(User, callback_data.tid)
        if user is None:
            await callback.answer("Foydalanuvchi topilmadi.", show_alert=True)
            return
        summ = await HistoryService.user_summary(session, user)
        rows, total = await HistoryService.tx_page(session, period_since(period), "all", page, 8, user_id=user.id)
    lines = [
        f"👤 <b>{esc(user.display_name)}</b> · <code>{user.telegram_id}</code>\n━━━━━━━━━━━━━━━",
        f"🪙 Balans: <b>{fmt_coins(user.coins)}</b> · 🎴 Kartalar: <b>{user.total_cards}</b> (noyob {user.unique_cards})",
        f"🕒 Qo‘shilgan: {fmt_tashkent(user.created_at, '%d.%m.%Y')} · oxirgi faollik: {fmt_tashkent(user.last_activity, '%d.%m %H:%M')}",
        f"🚫 Ban: {'ha' if user.is_banned else 'yo‘q'}\n",
        f"💰 Sotgan: <b>{summ['sold_n']}</b> ta ({fmt_coins(summ['sold_sum'])} 🪙)",
        f"🛒 Sotib olgan: <b>{summ['bought_n']}</b> ta ({fmt_coins(summ['bought_sum'])} 🪙)",
        f"🏛 Do‘kon/Event xaridi: <b>{summ['store_n']}</b> ta ({fmt_coins(summ['store_sum'])} 🪙)",
        f"🎁 Mukofotlar: <b>{summ['rew_n']}</b> ta ({fmt_coins(summ['rew_sum'])} 🪙)",
        f"💸 To‘lagan komissiya: <b>{fmt_coins(summ['fee_paid'])}</b> 🪙",
        f"🏰 Guruhga qo‘shgan: <b>{summ['groups_added']}</b> · 💬 Murojaatlar: <b>{summ['tickets']}</b>",
        f"\n🧾 <b>Oxirgi harakatlar</b> ({esc(period_label(period))}, jami {total}):",
    ]
    for tx, _u in rows:
        lines.append(HistoryService.format_tx_line(tx, None, show_user=False))
    if not rows:
        lines.append("📭 Harakat yo‘q.")
    o = callback.from_user.id
    b = InlineKeyboardBuilder()
    _period_row(b, o, "usr", period, tid=user.id)
    _nav_row(b, o, "usr", period, page, total, 8, tid=user.id)
    b.row(InlineKeyboardButton(text="🔎 Boshqa foydalanuvchi", callback_data=_cb(o, "usr_s")))
    _menu_btn(b, o)
    await edit_or_answer(callback, "\n".join(lines)[:4000], reply_markup=b.as_markup())


# ---------------------------------------------------------------------------
# Jonli xabarlar sozlamasi
# ---------------------------------------------------------------------------
@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "feed")))
async def cb_hist_feed(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "history"):
        return
    ext = callback_data.ext or ""
    async with get_session() as session:
        cfg = await AdminFeedService.get_cfg(session)
        changed = False
        if ext == "toggle":
            cfg["enabled"] = not bool(cfg.get("enabled"))
            cfg["last_tx_id"] = 0  # yoqilganda eski tarix yuborilmaydi
            cfg["last_sent"] = None
            changed = True
        elif ext == "rew":
            cfg["skip_rewards"] = not bool(cfg.get("skip_rewards", True))
            changed = True
        elif ext.startswith("i") and ext[1:].isdigit():
            cfg["interval_min"] = max(5, int(ext[1:]))
            changed = True
        if changed:
            await AdminFeedService.save_cfg(session, cfg)
            await session.commit()
    o = callback.from_user.id
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text=("🔕 O‘chirish" if cfg.get("enabled") else "🔔 Yoqish"), callback_data=_cb(o, "feed", ext="toggle")))
    b.row(
        *[
            InlineKeyboardButton(
                text=("✅ " if int(cfg.get("interval_min", 15)) == m else "") + f"{m} daq",
                callback_data=_cb(o, "feed", ext=f"i{m}"),
            )
            for m in (5, 15, 30, 60)
        ]
    )
    b.row(
        InlineKeyboardButton(
            text=("🎁 Mukofotlar yashirilgan" if cfg.get("skip_rewards", True) else "🎁 Mukofotlar ko‘rsatiladi"),
            callback_data=_cb(o, "feed", ext="rew"),
        )
    )
    _menu_btn(b, o)
    await edit_or_answer(
        callback,
        "🔔 <b>JONLI XABARLAR</b>\n━━━━━━━━━━━━━━━\n"
        f"Holat: <b>{'✅ yoqilgan' if cfg.get('enabled') else '❌ o‘chiq'}</b>\n"
        f"Interval: har <b>{int(cfg.get('interval_min', 15))}</b> daqiqada\n\n"
        "Yoqilsa, bot adminlarga foydalanuvchilarning yangi harakatlarini (kim nima sotdi, sotib oldi, "
        "kim qancha komissiya to‘ladi va h.k.) jamlab yuborib turadi.",
        reply_markup=b.as_markup(),
    )
