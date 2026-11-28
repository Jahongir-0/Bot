"""Admin paneli: bo‘limlar markazi (kategoriyalar)."""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from admin.filters import IsAdminFilter, resolve_admin_perms
from keyboards.v2 import ADMIN_HUB_CATEGORIES, admin_v2_hub_kb
from utils.callback_data import AdmV2Cb
from utils.formatting import esc
from utils.tg_helpers import edit_or_answer

logger = logging.getLogger(__name__)
router = Router(name="admin_v2_hub")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


# ===========================================================================
# 0. ADMIN HUB CATEGORY ROUTER (sec="hub")
# ===========================================================================
@router.callback_query(AdmV2Cb.filter(F.sec == "hub"))
async def cb_v2_hub(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    await state.clear()
    perms = resolve_admin_perms(callback.from_user, admin_perms)
    cat_key = callback_data.ext or "cards_evt"
    cat_info = next(((title, desc, secs) for k, title, desc, secs in ADMIN_HUB_CATEGORIES if k == cat_key), None)
    if cat_info is None or not any(s in perms for s in cat_info[2]):
        await callback.answer("⛔ Bu toifaga ruxsatingiz yo‘q.", show_alert=True)
        return
    title, desc, _secs = cat_info
    await edit_or_answer(
        callback,
        f"<b>{esc(title)}</b>\n━━━━━━━━━━━━━━━\n{esc(desc)}\n\n👇 Kerakli bo‘limni tanlang:",
        reply_markup=admin_v2_hub_kb(callback.from_user.id, cat_key, perms),
    )
