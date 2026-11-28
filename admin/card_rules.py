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
    "merge_count": ("🔮 Birlashtirish: kerakli karta soni", [2, 3, 4, 5, 8, 10], lambda v: f"{int(v)} ta"),
    "merge_daily_limit": ("🔮 Kunlik birlashtirish limiti", [0, 1, 2, 3, 5, 10], lambda v: "Cheklovsiz" if int(v) == 0 else f"{int(v)}/kun"),
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


_VIEW: dict = {}  # admin -> oxirgi ochilgan ko'rinish (drop | level | merge), tahrirdan keyin shunga qaytiladi

LEVEL_KEYS = ("max_level", "coin_cost_factor", "dup_refund_percent", "bot_sell_level_bonus_pct", "stars_per_level", "stars_max_level", "power_step")
MERGE_KEYS = ("merge_count", "merge_daily_limit", "merge_cost_4", "merge_cost_5")
VIEW_TITLES = {
    "drop": "🎲 RANK BO‘YICHA TUSHISH",
    "level": "📈 DARAJA VA NARX SOZLAMALARI",
    "merge": "🔮 BIRLASHTIRISH SOZLAMALARI",
}
VIEW_PARENT = {"drop": "drop", "level": "card_set", "merge": "card_set"}


async def _render(target, owner: int, view: Optional[str] = None) -> None:
    view = view or _VIEW.get(owner) or "level"
    _VIEW[owner] = view
    async with get_session() as session:
        ranks = await GameplayService.get_rank_definitions(session)
        mults = await CardLevelService.get_drop_mults(session)
        cfg = await CardLevelService.get_config(session)

    lines = [header(f"<b>{VIEW_TITLES[view]}</b>")]
    b = InlineKeyboardBuilder()
    if view == "drop":
        lines.append("Guruhga <b>tasodifiy</b> tushishda har bir rankning ehtimol ko‘paytiruvchisi:\n")
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
        lines += ["", "<i>Tugmani bossangiz qiymat almashadi: 🚫 tushmaydi → 5% → 15% → 50% → 100%.</i>"]
    elif view == "level":
        lines.append("Karta darajasi, ko‘tarish narxi va botga sotish:\n")
        for key in LEVEL_KEYS:
            label, options, fmt = CFG_FIELDS[key]
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
        )
        b.row(InlineKeyboardButton(text="✏️ Botga sotish Lvl %", callback_data=AdmV2Cb(owner_id=owner, sec=SEC, act="ed", ext="bot_sell_level_bonus_pct").pack()))
    else:  # merge
        on = bool(cfg.get("merge_enabled", True))
        lines.append(f"Holat: {'🟢 YOQILGAN' if on else '🔴 O‘CHIQ'}\n")
        for key in MERGE_KEYS:
            label, options, fmt = CFG_FIELDS[key]
            lines.append(f"{label}: <b>{fmt(cfg[key])}</b>")
            b.row(
                InlineKeyboardButton(
                    text=f"{label}: {fmt(cfg[key])}"[:60],
                    callback_data=AdmV2Cb(owner_id=owner, sec=SEC, act="cfg", ext=key).pack(),
                )
            )
        b.row(
            InlineKeyboardButton(
                text=f"🔮 Birlashtirish: {'🟢 YOQILGAN' if on else '🔴 O‘CHIQ'}",
                callback_data=AdmV2Cb(owner_id=owner, sec=SEC, act="mtog").pack(),
            )
        )
        pairs = cfg.get("merge_pairs") or {}
        ranks_by_ord = {int(r.get("order", 0)): r for r in ranks}
        lines.append("\n<b>Qaysi rank juftliklari birlashtiriladi:</b> (tugma bilan yoqish/o‘chirish)")
        pair_btns = []
        for ord_num in sorted(ranks_by_ord):
            if ord_num + 1 in ranks_by_ord:
                r_from, r_to = ranks_by_ord[ord_num], ranks_by_ord[ord_num + 1]
                pon = bool(pairs.get(str(ord_num), True))
                pair_btns.append(
                    InlineKeyboardButton(
                        text=f"{'✅' if pon else '🚫'} {r_from.get('emoji', '')}{ord_num}→{r_to.get('emoji', '')}{ord_num + 1}",
                        callback_data=AdmV2Cb(owner_id=owner, sec=SEC, act="mpair", tid=ord_num).pack(),
                    )
                )
        for i in range(0, len(pair_btns), 3):
            b.row(*pair_btns[i : i + 3])
    b.row(
        InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=owner, sec="hub", act="v", ext=VIEW_PARENT[view]).pack()),
        InlineKeyboardButton(text="🛡 Admin Panel", callback_data=AdminCb(owner_id=owner, section="home").pack()),
    )
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


async def _perm(callback: CallbackQuery, admin_perms: Optional[set]) -> bool:
    if not any(has_admin_perm(callback.from_user, admin_perms, sec) for sec in ("ranks", "cards", "settings")):
        await callback.answer("⛔ Bu bo‘limga ruxsatingiz yo‘q.", show_alert=True)
        return False
    return True


@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "v")))
async def cb_rules_home(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: Optional[set] = None) -> None:
    await state.clear()
    if await _perm(callback, admin_perms):
        view = callback_data.ext if callback_data.ext in VIEW_TITLES else None
        await _render(callback, callback.from_user.id, view)


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


@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "mtog")))
async def cb_rules_merge_toggle(callback: CallbackQuery, admin_perms: Optional[set] = None) -> None:
    if not await _perm(callback, admin_perms):
        return
    async with get_session() as session:
        cfg = await CardLevelService.get_config(session)
        await CardLevelService.set_config_value(session, "merge_enabled", not bool(cfg.get("merge_enabled", True)))
        await session.commit()
    await _render(callback, callback.from_user.id)


@router.callback_query(AdmV2Cb.filter((F.sec == SEC) & (F.act == "mpair")))
async def cb_rules_merge_pair(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: Optional[set] = None) -> None:
    if not await _perm(callback, admin_perms):
        return
    async with get_session() as session:
        cfg = await CardLevelService.get_config(session)
        pairs = dict(cfg.get("merge_pairs") or {})
        key = str(callback_data.tid)
        pairs[key] = not bool(pairs.get(key, True))
        await CardLevelService.set_config_value(session, "merge_pairs", pairs)
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
    await _render(message, message.from_user.id)
