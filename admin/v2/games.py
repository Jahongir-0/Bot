"""Mini o‘yinlar va paketlar sozlamalari (har bir o‘yin uchun alohida sahifa)."""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from admin.filters import IsAdminFilter
from admin.v2.common import AdminV2States, _back_home_btn, _deny_if_no_perm
from database.session import get_session
from services.gameplay_service import GameplayService
from services.settings_service import SettingsService
from utils.callback_data import AdmV2Cb
from utils.formatting import esc
from utils.game_catalog import ADMIN_GAMES, FIELD_META, TOGGLE_KEYS
from utils.tg_helpers import NotCommandFilter, edit_or_answer

logger = logging.getLogger(__name__)
router = Router(name="admin_v2_games")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


# ===========================================================================
# 7. GAMES & PACKS (sec="games", Zero-pipe per-field edit)
# ===========================================================================
def _gm_on(cfg: dict, toggle: str) -> bool:
    return bool(cfg.get(toggle, True))


async def _show_games_home(target, owner_id: int) -> None:
    """O‘yinlar ro‘yxati: har bir o‘yin tugmasi o‘zining sozlamalar sahifasini ochadi."""
    async with get_session() as session:
        cfg = await GameplayService.get_game_config(session)
    lines = [
        "🎮 <b>O‘YINLAR BOSHQARUVI</b>",
        "━━━━━━━━━━━━━━━",
        "O‘yinni tanlang — yoqish/o‘chirish, kunlik limit va yutuqlarni o‘zgartirasiz.\n",
    ]
    b = InlineKeyboardBuilder()
    buttons = []
    for g in ADMIN_GAMES:
        on = _gm_on(cfg, g["toggle"])
        limit_key = g["fields"][0][0]
        lim = int(cfg.get(limit_key, 0))
        if g["key"] == "pack" and lim <= 0:
            on = False
        mark = "🟢" if on else "🔴"
        lines.append(f"{mark} {g['emoji']} <b>{g['name']}</b> — kuniga {lim} ta")
        buttons.append(
            InlineKeyboardButton(
                text=f"{mark} {g['emoji']} {g['name']}",
                callback_data=AdmV2Cb(owner_id=owner_id, sec="games", act="g", ext=g["key"]).pack(),
            )
        )
    b.add(*buttons)
    b.adjust(2)
    b.row(_back_home_btn(owner_id))
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


def _game_by_key(key: str):
    return next((g for g in ADMIN_GAMES if g["key"] == key), None)


async def _show_game_page(target, owner_id: int, gkey: str) -> None:
    g = _game_by_key(gkey)
    if g is None:
        await _show_games_home(target, owner_id)
        return
    async with get_session() as session:
        cfg = await GameplayService.get_game_config(session)
    on = _gm_on(cfg, g["toggle"])
    lines = [
        f"{g['emoji']} <b>{g['name'].upper()}</b>",
        "━━━━━━━━━━━━━━━",
        f"<i>{g['hint']}</i>\n",
        f"Holat: <b>{'🟢 Yoqilgan' if on else '🔴 O‘chirilgan'}</b>",
    ]
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text="🔴 O‘chirish" if on else "🟢 Yoqish",
            callback_data=AdmV2Cb(owner_id=owner_id, sec="games", act="tog", ext=g["toggle"]).pack(),
        )
    )
    for field, label, _zero, _mn, _mx in g["fields"]:
        val = cfg.get(field, 0)
        lines.append(f"• {label}: <b>{int(val):,}</b>")
        b.row(
            InlineKeyboardButton(
                text=f"✏️ {label}: {int(val):,}"[:60],
                callback_data=AdmV2Cb(owner_id=owner_id, sec="games", act="ed", ext=field).pack(),
            )
        )
    b.row(InlineKeyboardButton(text="⬅️ Barcha o‘yinlar", callback_data=AdmV2Cb(owner_id=owner_id, sec="games", act="v").pack()))
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


@router.callback_query(AdmV2Cb.filter(F.sec == "games"))
async def cb_v2_games(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "games"):
        return
    owner_id = callback.from_user.id
    act = callback_data.act
    if act == "g":
        await state.clear()
        await _show_game_page(callback, owner_id, callback_data.ext)
        return
    if act == "ed":
        field = callback_data.ext
        meta = FIELD_META.get(field)
        if meta is None:
            await callback.answer("Noma’lum sozlama", show_alert=True)
            return
        await state.set_state(AdminV2States.waiting_games_field)
        await state.update_data(owner_tg_id=owner_id, g_field=field)
        lo = 0 if meta["zero"] else max(1, meta["min"])
        back = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Bekor", callback_data=AdmV2Cb(owner_id=owner_id, sec="games", act="g", ext=meta["game"]).pack())]]
        )
        await edit_or_answer(
            callback,
            f"🎮 <b>{esc(meta['label'])}</b>\n\nYangi qiymatni kiriting ({lo:,} dan {meta['max']:,} gacha butun son):",
            reply_markup=back,
        )
        return
    if act == "tog":
        tog_key = callback_data.ext if callback_data.ext in TOGGLE_KEYS else None
        if tog_key is None:
            await callback.answer("Noma’lum sozlama", show_alert=True)
            return
        async with get_session() as session:
            cfg = await GameplayService.get_game_config(session)
            cfg[tog_key] = not bool(cfg.get(tog_key, True))
            await SettingsService.set_value(session, "game_config", cfg)
            await session.commit()
        await callback.answer("✅ Yangilandi!")
        game = next((g for g in ADMIN_GAMES if g["toggle"] == tog_key), None)
        await state.clear()
        await _show_game_page(callback, owner_id, game["key"] if game else "")
        return
    await state.clear()
    await _show_games_home(callback, owner_id)


@router.message(AdminV2States.waiting_games_field, NotCommandFilter())
async def fsm_v2_games_field(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    field = data["g_field"]
    meta = FIELD_META.get(field)
    if meta is None:
        await state.clear()
        return
    lo = 0 if meta["zero"] else max(1, meta["min"])
    if not raw.isdigit() or not (lo <= int(raw) <= meta["max"]):
        await message.answer(f"⚠️ {lo:,} dan {meta['max']:,} gacha butun son kiriting.")
        return
    val = int(raw)
    async with get_session() as session:
        cfg = await GameplayService.get_game_config(session)
        if field == "lucky_min_reward" and val > int(cfg.get("lucky_max_reward", val)):
            await message.answer("⚠️ Minimal yutuq maksimaldan katta bo‘lmasligi kerak.")
            return
        if field == "lucky_max_reward" and val < int(cfg.get("lucky_min_reward", val)):
            await message.answer("⚠️ Maksimal yutuq minimaldan kichik bo‘lmasligi kerak.")
            return
        cfg[field] = val
        if field == "pack_daily_limit":
            cfg["pack_enabled"] = val > 0
        await SettingsService.set_value(session, "game_config", cfg)
        await session.commit()
    await state.clear()
    await _show_game_page(message, message.from_user.id, meta["game"])
