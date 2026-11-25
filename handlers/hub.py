"""Bosh menyudagi markaz sahifalari: Savdo markazi va O‘yinlar markazi."""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database.session import get_session
from keyboards.main_menu import games_hub_kb, savdo_hub_kb
from services.gameplay_service import GameplayService
from utils.callback_data import NavCb
from utils.game_catalog import ADMIN_GAMES
from utils.tg_helpers import edit_or_answer

router = Router(name="hub")

SAVDO_TEXT = (
    "🛒 <b>SAVDO MARKAZI</b>\n"
    "━━━━━━━━━━━━━━━\n"
    "Kartalaringiz bilan nima qilmoqchisiz?\n\n"
    "🛒 <b>Market</b> — foydalanuvchilar bilan karta sotib oling yoki soting\n"
    "🏛 <b>Bot Do‘koni</b> — kartani botga soting yoki bot zaxirasidan sotib oling\n"
    "🔨 <b>Auksion</b> — eng yuqori stavka bergan karta egasi bo‘ladi\n"
    "🎯 <b>Event</b> — cheklangan maxsus kartalar\n"
    "🤝 <b>Almashish</b> — do‘stingiz bilan karta kartaga almashing\n"
    "🎁 <b>Sovg‘a berish</b> — do‘stga karta yoki coin yuboring\n\n"
    "💡 <i>O‘z kartangizni sotish/auksionga qo‘yish: 👤 Profilim → 🎴 Kartalarim.</i>"
)


def _games_flags(cfg: dict) -> tuple[bool, bool]:
    """(mini o‘yinlardan kamida bittasi yoqilganmi, paketlar yoqilganmi)"""
    has_games = any(bool(cfg.get(g["toggle"], True)) for g in ADMIN_GAMES if g["key"] != "pack")
    has_pack = bool(cfg.get("pack_enabled", True)) and int(cfg.get("pack_daily_limit", 1)) > 0
    return has_games, has_pack


def games_hub_text(has_games: bool, has_pack: bool) -> str:
    lines = [
        "🎮 <b>O‘YINLAR MARKAZI</b>",
        "━━━━━━━━━━━━━━━",
        "O‘ynang, coin va XP yutib oling:\n",
    ]
    if has_games:
        lines.append("🎲 <b>Mini o‘yinlar</b> — slot, dart, sandiq va boshqalar")
    lines += [
        "🧠 <b>Viktorina</b> — savollarga javob bering, mukofot oling",
        "🎯 <b>Eventlar</b> — cheklangan maxsus kartalar",
        "📋 <b>Vazifalar</b> — bajaring, mukofot oling",
    ]
    if has_pack:
        lines.append("🎁 <b>Paketlar</b> — coin evaziga tasodifiy karta oching")
    return "\n".join(lines)


@router.callback_query(NavCb.filter(F.target == "savdo"))
async def cb_savdo_hub(callback: CallbackQuery) -> None:
    await edit_or_answer(callback, SAVDO_TEXT, reply_markup=savdo_hub_kb(callback.from_user.id))
    await callback.answer()


@router.message(Command("savdo", "shop"))
async def cmd_savdo_hub(message: Message) -> None:
    await message.answer(SAVDO_TEXT, reply_markup=savdo_hub_kb(message.from_user.id))


@router.callback_query(NavCb.filter(F.target == "grp_play"))
async def cb_games_hub(callback: CallbackQuery) -> None:
    async with get_session() as session:
        cfg = await GameplayService.get_game_config(session)
    has_games, has_pack = _games_flags(cfg)
    await edit_or_answer(
        callback,
        games_hub_text(has_games, has_pack),
        reply_markup=games_hub_kb(callback.from_user.id, has_games, has_pack),
    )
    await callback.answer()
