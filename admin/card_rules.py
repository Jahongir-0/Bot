"""🎯 Drop qoidalari va karta darajasi sozlamalari (admin)."""
from __future__ import annotations

from typing import Optional

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from admin.filters import IsAdminFilter, has_admin_perm
from database.session import get_session
from services.card_level_service import DROP_MULT_PRESETS, CardLevelService
from services.gameplay_service import GameplayService
from utils.callback_data import AdminCb, AdmV2Cb
from utils.formatting import esc, header
from utils.tg_helpers import NotCommandFilter, edit_or_answer

router = Router(name="admin_card_rules")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())

SEC = "crules"


class AdminCardRulesStates(StatesGroup):
    waiting_val = State()


# kalit: (nom, variantlar, format)
CFG_FIELDS = {
    "max_level": ("📈 Maksimal daraja", [5, 10, 15, 20], lambda v: f"{int(v)}"),
    "coin_cost_factor": ("🪙 Ko‘tarish narxi omili", [0.25, 0.5, 1.0, 2.0], lambda v: f"x{float(v):g}"),
    "dup_refund_percent": ("🔁 Max darajada dublikat qaytimi", [0, 25, 50, 75, 100], lambda v: f"{int(v)}%"),
    "bot_sell_level_bonus_pct": ("🤖 Botga sotish: lvl bonusi (%)", [0, 5, 10, 15, 20, 25], lambda v: f"+{int(v)}% / lvl"),
    "stars_per_level": ("⭐ Stars narxi (daraja boshiga)", [10, 20, 50, 100], lambda v: f"{int(v)} ⭐"),
    "stars_max_level": ("⭐ Stars bilan maksimal daraja", [3, 5, 7, 10], lambda v: f"{int(v)}"),
    "power_step": ("💪 Daraja boshiga kuch o‘sishi", [0.1, 0.15, 0.2, 0.3], lambda v: f"+{int(float(v) * 100)}%"),
}


def _next(options: list, current) -> object:
    for i, o in enumerate(options):
        if abs(float(o) - float(current)) < 1e-9:
            return options[(i + 1) % len(options)]
    return options[0]


def _pct(m: float) -> str:
    return "🚫 tushmaydi" if m <= 0 else f"{m * 100:g}%"


async def _render(target, owner: int) -> None:
    async with get_session() as session:
        ranks = await GameplayService.get_rank_definitions(session)
        mults = await CardLevelService.get_drop_mults(session)
        cfg = await CardLevelService.get_config(session)

    lines = [
        header("🎯 <b>DROP, NARX VA DARAJA SOZLAMALARI</b>"),
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
    lines += ["", "<i>Tugmani bossangiz qiymat almashadi: 🚫 → 5% → 15% → 50% → 100%.</i>", "", "<b>Karta darajasi va botga sotish narxlari</b>"]
    for key, (label, options, fmt) in CFG_FIELDS.items():
        lines.append(f"{label}: <b>{fmt(cfg[key])}</b>")
        b.row(
            InlineKeyboardButton(
                text=f"{label}: {fmt(cfg[key])}"[:60],
                callback_data=AdmV2Cb(owner_id=owner, sec=SEC, act="cfg", ext=key).pack(),
            )
        )
    b.row(
        InlineKeyboardButton(text="✏️ Lvl narxi omili", callback_data=AdmV2Cb(owner_id=owner, sec=SEC, act="ed", ext="coin_cost_factor").pack()),
        InlineKeyboardButton(text="✏️ Stars/lvl", callback_data=AdmV2Cb(owner_id=owner, sec=SEC, act="ed", ext="stars_per_level").pack()),
        InlineKeyboardButton(text="✏️ Bot Lvl %", callback_data=AdmV2Cb(owner_id=owner, sec=SEC, act="ed", ext="bot_sell_level_bonus_pct").pack()),
    )
    b.row(
        InlineKeyboardButton(text="🔮 Birlashtirish sozlamalari (barcha ranklar)", callback_data=AdmV2Cb(owner_id=owner, sec=SEC, act="mg_v").pack()),
    )
    b.row(
        InlineKeyboardButton(text="💎 Rank narxlarini o‘zgartirish", callback_data=AdmV2Cb(owner_id=owner, sec="ranks", act="v").pack()),
    )
    b.row(
        InlineKeyboardButton(text="⬅️ Ranklar", callback_data=AdmV2Cb(owner_id=owner, sec="ranks", act="v").pack()),
        InlineKeyboardButton(text="🛡 Admin Panel", callback_data=AdminCb(owner_id=owner, section="home").pack()),
    )
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


async def _perm(callback: CallbackQuery, admin_perms: Optional[set]) -> bool:
    if not has_admin_perm(callback.from_user, admin_perms, "ranks"):
        await callback.answer("⛔ Bu bo‘limga ruxsatingiz yo‘q.", show_alert=True)
        return False
    return True


MERGE_PRESETS = [0, 2, 3, 5, 8, 10, 15, 20]


def _merge_fmt(v: int) -> str:
    return "🚫 o‘chiq" if int(v) <= 0 else f"{int(v)} ta"


async def _render_merge(target, owner: int) -> None:
    async with get_session() as session:
        ranks = await GameplayService.get_rank_definitions(session)
        cfg = await CardLevelService.get_config(session)
    ranks = sorted(ranks, key=lambda r: int(r.get("order", 0)))
    lines = [
        header("🔮 <b>BIRLASHTIRISH SOZLAMALARI</b>"),
        "Foydalanuvchi bir xil rankdagi kartalarini topshirib, <b>keyingi rankdagi</b> karta oladi.",
        "Har bir rank uchun nechta karta kerakligini belgilang. <b>0 = o‘chiq</b> (o‘sha rankdan oshirib bo‘lmaydi).",
        "",
    ]
    b = InlineKeyboardBuilder()
    for idx, r in enumerate(ranks[:-1]):
        order = int(r.get("order", idx + 1))
        nxt = ranks[idx + 1]
        cost = int(cfg.get(f"merge_cost_{order}", 0) or 0)
        lines.append(f"{r.get('emoji', '⭐')} {esc(r['name'])} → {nxt.get('emoji', '⭐')} {esc(nxt['name'])}: <b>{_merge_fmt(cost)}</b>")
        b.row(
            InlineKeyboardButton(
                text=f"{r.get('emoji', '⭐')} {r['name']} → {nxt['name']}: {_merge_fmt(cost)}"[:50],
                callback_data=AdmV2Cb(owner_id=owner, sec=SEC, act="mg_c", tid=order).pack(),
            ),
            InlineKeyboardButton(text="✏️", callback_data=AdmV2Cb(owner_id=owner, sec=SEC, act="mg_ed", tid=order).pack()),
        )
    lines += ["", "<i>Tugmani bossangiz tayyor qiymatlar almashadi (0 → 2 → 3 → 5 → 8 → 10 → 15 → 20). Aniq son uchun ✏️ ni bosing.</i>"]
    b.row(InlineKeyboardButton(text="⬅️ Orqaga", callback_data=AdmV2Cb(owner_id=owner, sec=SEC, act="v").pack()))
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "mg_v")))
async def cb_merge_home(callback: CallbackQuery, state: FSMContext, admin_perms: Optional[set] = None) -> None:
    await state.clear()
    if await _perm(callback, admin_perms):
        await _render_merge(callback, callback.from_user.id)


@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "mg_c")))
async def cb_merge_cycle(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: Optional[set] = None) -> None:
    if not await _perm(callback, admin_perms):
        return
    key = f"merge_cost_{int(callback_data.tid)}"
    async with get_session() as session:
        cfg = await CardLevelService.get_config(session)
        cur = int(cfg.get(key, 0) or 0)
        nxt = next((p for p in MERGE_PRESETS if p > cur), MERGE_PRESETS[0])
        await CardLevelService.set_config_value(session, key, nxt)
        await session.commit()
    await _render_merge(callback, callback.from_user.id)


@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "mg_ed")))
async def cb_merge_edit_prompt(
    callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: Optional[set] = None
) -> None:
    if not await _perm(callback, admin_perms):
        return
    key = f"merge_cost_{int(callback_data.tid)}"
    await state.set_state(AdminCardRulesStates.waiting_val)
    await state.update_data(owner_tg_id=callback.from_user.id, cr_field=key)
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Bekor", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec=SEC, act="mg_v").pack())]]
    )
    await edit_or_answer(
        callback,
        "🔮 Nechta karta topshirilsa keyingi rankdagi karta berilsin? Butun son kiriting (<code>0</code> = o‘chirish):",
        reply_markup=cancel_kb,
    )


@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "v")))
async def cb_rules_home(callback: CallbackQuery, state: FSMContext, admin_perms: Optional[set] = None) -> None:
    await state.clear()
    if await _perm(callback, admin_perms):
        await _render(callback, callback.from_user.id)


@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "mult")))
async def cb_rules_mult(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: Optional[set] = None) -> None:
    if not await _perm(callback, admin_perms):
        return
    async with get_session() as session:
        mults = await CardLevelService.get_drop_mults(session)
        cur = CardLevelService.mult_for(mults, callback_data.tid)
        await CardLevelService.set_drop_mult(session, callback_data.tid, _next(DROP_MULT_PRESETS, cur))
        await session.commit()
    await _render(callback, callback.from_user.id)


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
    await _render(callback, callback.from_user.id)


@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "ed")))
async def cb_rules_edit_prompt(
    callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: Optional[set] = None
) -> None:
    if not await _perm(callback, admin_perms):
        return
    key = callback_data.ext
    prompts = {
        "coin_cost_factor": "🪙 Daraja ko‘tarish narxi omilini kiriting (masalan: <code>0.5</code>, <code>1.0</code>, <code>1.5</code>):",
        "stars_per_level": "⭐ Har bir daraja uchun Stars narxini kiriting (masalan: <code>20</code>):",
        "bot_sell_level_bonus_pct": "🤖 Botga sotishda har daraja (+1 lvl) uchun bonus foizini kiriting (0–100, masalan: <code>10</code>):",
    }
    if key not in prompts:
        await callback.answer("Noma’lum maydon", show_alert=True)
        return
    await state.set_state(AdminCardRulesStates.waiting_val)
    await state.update_data(owner_tg_id=callback.from_user.id, cr_field=key)
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Bekor", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec=SEC, act="v").pack())]]
    )
    await edit_or_answer(callback, prompts[key], reply_markup=cancel_kb)


@router.message(AdminCardRulesStates.waiting_val, NotCommandFilter())
async def fsm_rules_val(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    key = data.get("cr_field", "")
    raw = (message.text or "").strip().replace(",", ".").replace(" ", "")
    try:
        if key == "coin_cost_factor":
            val = float(raw)
            if not (0.05 <= val <= 20.0):
                raise ValueError("0.05 dan 20.0 gacha qiymat kiriting.")
        elif key == "stars_per_level":
            val = int(float(raw))
            if not (1 <= val <= 10000):
                raise ValueError("1 dan 10,000 gacha butun son kiriting.")
        elif key == "bot_sell_level_bonus_pct":
            val = int(float(raw))
            if not (0 <= val <= 100):
                raise ValueError("0 dan 100 gacha foiz kiriting.")
        elif key.startswith("merge_cost_"):
            val = int(float(raw))
            if not (0 <= val <= 1000):
                raise ValueError("0 dan 1000 gacha butun son kiriting (0 = o‘chirish).")
        else:
            await state.clear()
            return
    except ValueError as exc:
        await message.answer(f"⚠️ {esc(str(exc))}")
        return
    await state.clear()
    async with get_session() as session:
        await CardLevelService.set_config_value(session, key, val)
        await session.commit()
    if key.startswith("merge_cost_"):
        await _render_merge(message, message.from_user.id)
    else:
        await _render(message, message.from_user.id)
