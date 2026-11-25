"""Telegram Stars tariflari."""

import logging
import re
from typing import Optional

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from admin.filters import IsAdminFilter
from admin.handlers.bot_settings import STARS_INPUT_HELP
from admin.handlers.common import AdminStates, _AdminRbacCallbackMiddleware
from database.session import get_session
from keyboards.admin import admin_stars_back_kb, admin_stars_confirm_kb, admin_stars_kb
from services.admin_service import AdminService
from services.settings_service import SettingsService
from utils.callback_data import AdminCb
from utils.formatting import esc
from utils.tg_helpers import NotCommandFilter, edit_or_answer

logger = logging.getLogger(__name__)
router = Router(name="admin_stars")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())
router.callback_query.middleware(_AdminRbacCallbackMiddleware())


def _parse_stars_coins(text: str):
    nums = re.findall(r"\d+", (text or "").replace(",", " "))
    if len(nums) != 2:
        return None
    return int(nums[0]), int(nums[1])


def _stars_list_text(packages) -> str:
    lines = ["⭐ <b>STARS TARIFLARI</b>\n"]
    for p in packages:
        lines.append(f"• <b>{p['stars']:,}</b> ⭐ → <b>{p['coins']:,}</b> 🪙")
    if not packages:
        lines.append("📭 Paketlar yo‘q.")
    return "\n".join(lines)


@router.callback_query(AdminCb.filter(F.section == "stars"))
async def cb_admin_stars(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    action = callback_data.action
    admin_id = callback.from_user.id
    if action == "add":
        await state.set_state(AdminStates.stars_add)
        await edit_or_answer(callback, f"➕ <b>YANGI STARS TARIF</b>\n\n{STARS_INPUT_HELP}", reply_markup=admin_stars_back_kb(admin_id))
        return
    if action == "edit":
        async with get_session() as session:
            packages = await SettingsService.get_stars_packages(session)
        pkg = next((p for p in packages if p["id"] == callback_data.extra), None)
        if pkg is None:
            await callback.answer("Paket topilmadi.", show_alert=True)
            return
        await state.set_state(AdminStates.stars_edit)
        await state.update_data(package_id=pkg["id"])
        await edit_or_answer(
            callback,
            f"✏️ <b>TARIFNI TAHRIRLASH</b>\nHozirgi: <b>{pkg['stars']:,}</b> ⭐ → <b>{pkg['coins']:,}</b> 🪙\n\n{STARS_INPUT_HELP}",
            reply_markup=admin_stars_back_kb(admin_id),
        )
        return
    if action == "del":
        await state.clear()
        await edit_or_answer(callback, "🗑 Ushbu tarifni o‘chirishni tasdiqlaysizmi?", reply_markup=admin_stars_confirm_kb(admin_id, callback_data.extra))
        return

    await state.clear()
    async with get_session() as session:
        if action == "del_ok":
            if await SettingsService.delete_stars_package(session, callback_data.extra):
                await AdminService.log_action(session, admin_id, "stars_package_delete", "settings", callback_data.extra)
                await session.commit()
        packages = await SettingsService.get_stars_packages(session)
    await edit_or_answer(callback, _stars_list_text(packages), reply_markup=admin_stars_kb(admin_id, packages))


async def _save_stars_from_message(message: Message, state: FSMContext, package_id: Optional[str]) -> None:
    parsed = _parse_stars_coins(message.text or "")
    if parsed is None:
        await message.answer(f"⚠️ Format noto‘g‘ri.\n{STARS_INPUT_HELP}")
        return
    stars, coins = parsed
    try:
        async with get_session() as session:
            pkg = await SettingsService.save_stars_package(session, package_id, stars, coins)
            await session.commit()
            packages = await SettingsService.get_stars_packages(session)
    except ValueError as exc:
        await message.answer(f"⚠️ {esc(str(exc))}")
        return
    await state.clear()
    await message.answer(f"✅ Saqlandi: <b>{pkg['stars']:,}</b> ⭐ → <b>{pkg['coins']:,}</b> 🪙", reply_markup=admin_stars_kb(message.from_user.id, packages))


@router.message(AdminStates.stars_add, NotCommandFilter())
async def fsm_stars_add(message: Message, state: FSMContext) -> None:
    await _save_stars_from_message(message, state, None)


@router.message(AdminStates.stars_edit, NotCommandFilter())
async def fsm_stars_edit(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    package_id = data.get("package_id")
    if not package_id:
        await state.clear()
        await message.answer("⚠️ Tarif topilmadi.")
        return
    await _save_stars_from_message(message, state, str(package_id))
