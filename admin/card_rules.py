"""🎯 Drop qoidalari va karta darajasi sozlamalari (admin)."""
from __future__ import annotations

from typing import Optional

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from admin.filters import IsAdminFilter, has_admin_perm
from database.session import get_session
from services.card_level_service import DROP_MULT_PRESETS, CardLevelService
from services.gameplay_service import GameplayService
from utils.callback_data import AdminCb, AdmV2Cb
from utils.formatting import esc, header
from utils.tg_helpers import edit_or_answer

router = Router(name="admin_card_rules")
router.callback_query.filter(IsAdminFilter())

SEC = "crules"

# kalit: (nom, variantlar, format)
CFG_FIELDS = {
    "max_level": ("📈 Maksimal daraja", [5, 10, 15, 20], lambda v: f"{int(v)}"),
    "coin_cost_factor": ("🪙 Ko‘tarish narxi omili", [0.25, 0.5, 1.0, 2.0], lambda v: f"x{float(v):g}"),
    "dup_refund_percent": ("🔁 Max darajada dublikat qaytimi", [0, 25, 50, 75, 100], lambda v: f"{int(v)}%"),
    "stars_per_level": ("⭐ Stars narxi (daraja boshiga)", [10, 20, 50, 100], lambda v: f"{int(v)} ⭐"),
    "stars_max_level": ("⭐ Stars bilan maksimal daraja", [3, 5, 7, 10], lambda v: f"{int(v)}"),
    "merge_cost_4": ("🔮 Birlashtirish: 4→5 rank (karta soni)", [0, 3, 5, 8, 10], lambda v: "o‘chiq" if int(v) == 0 else f"{int(v)} ta"),
    "merge_cost_5": ("🔮 Birlashtirish: 5→6 rank (karta soni)", [0, 5, 10, 20], lambda v: "o‘chiq" if int(v) == 0 else f"{int(v)} ta"),
    "power_step": ("💪 Daraja boshiga kuch o‘sishi", [0.1, 0.15, 0.2, 0.3], lambda v: f"+{int(float(v) * 100)}%"),
}


def _next(options: list, current) -> object:
    for i, o in enumerate(options):
        if abs(float(o) - float(current)) < 1e-9:
            return options[(i + 1) % len(options)]
    return options[0]


def _pct(m: float) -> str:
    return "🚫 tushmaydi" if m <= 0 else f"{m * 100:g}%"


async def _render(callback: CallbackQuery) -> None:
    owner = callback.from_user.id
    async with get_session() as session:
        ranks = await GameplayService.get_rank_definitions(session)
        mults = await CardLevelService.get_drop_mults(session)
        cfg = await CardLevelService.get_config(session)

    lines = [
        header("🎯 <b>DROP VA DARAJA SOZLAMALARI</b>"),
        "<b>Guruhga tasodifiy tushish ehtimoli</b> (rank bo‘yicha ko‘paytiruvchi):",
    ]
    b = InlineKeyboardBuilder()
    for r in ranks:
        order = int(r.get("order", 0))
        m = CardLevelService.mult_for(mults, order)
        lines.append(f"{r.get('emoji', '⭐')} {esc(r['name'])} — {_pct(m)}")
        b.row(
            InlineKeyboardButton(
                text=f"{r.get('emoji', '⭐')} {r['name']}: {_pct(m)}"[:60],
                callback_data=AdmV2Cb(owner_id=owner, sec=SEC, act="mult", tid=order).pack(),
            )
        )
    lines += ["", "<i>Tugmani bossangiz qiymat almashadi: 🚫 → 5% → 15% → 50% → 100%.</i>", "", "<b>Karta darajasi</b>"]
    for key, (label, options, fmt) in CFG_FIELDS.items():
        lines.append(f"{label}: <b>{fmt(cfg[key])}</b>")
        b.row(
            InlineKeyboardButton(
                text=f"{label}: {fmt(cfg[key])}"[:60],
                callback_data=AdmV2Cb(owner_id=owner, sec=SEC, act="cfg", ext=key).pack(),
            )
        )
    b.row(
        InlineKeyboardButton(text="⬅️ Ranklar", callback_data=AdmV2Cb(owner_id=owner, sec="ranks", act="v").pack()),
        InlineKeyboardButton(text="🛡 Admin Panel", callback_data=AdminCb(owner_id=owner, section="home").pack()),
    )
    await edit_or_answer(callback, "\n".join(lines), reply_markup=b.as_markup())


async def _perm(callback: CallbackQuery, admin_perms: Optional[set]) -> bool:
    if not has_admin_perm(callback.from_user, admin_perms, "ranks"):
        await callback.answer("⛔ Bu bo‘limga ruxsatingiz yo‘q.", show_alert=True)
        return False
    return True


@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "v")))
async def cb_rules_home(callback: CallbackQuery, admin_perms: Optional[set] = None) -> None:
    if await _perm(callback, admin_perms):
        await _render(callback)


@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "mult")))
async def cb_rules_mult(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: Optional[set] = None) -> None:
    if not await _perm(callback, admin_perms):
        return
    async with get_session() as session:
        mults = await CardLevelService.get_drop_mults(session)
        cur = CardLevelService.mult_for(mults, callback_data.tid)
        await CardLevelService.set_drop_mult(session, callback_data.tid, _next(DROP_MULT_PRESETS, cur))
        await session.commit()
    await _render(callback)


@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "cfg")))
async def cb_rules_cfg(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: Optional[set] = None) -> None:
    if not await _perm(callback, admin_perms):
        return
    field = CFG_FIELDS.get(callback_data.ext)
    if field is None:
        await callback.answer("Noma’lum sozlama", show_alert=True)
        return
    async with get_session() as session:
        cfg = await CardLevelService.get_config(session)
        new_val = _next(field[1], cfg[callback_data.ext])
        await CardLevelService.set_config_value(session, callback_data.ext, new_val)
        await session.commit()
    await _render(callback)
