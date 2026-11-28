"""Admin paneli: bo‘limlar markazi (kategoriyalar)."""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from admin.filters import IsAdminFilter, resolve_admin_perms
from keyboards.panel import PANEL, panel_kb, panel_text
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
    """Admin panel menyulari (toifa va ichki menyular): keyboards/panel.py dagi PANEL asosida."""
    await state.clear()
    perms = resolve_admin_perms(callback.from_user, admin_perms)
    key = callback_data.ext or "cards_evt"
    if key not in PANEL:
        await callback.answer("Bo‘lim topilmadi.", show_alert=True)
        return
    allowed_perms = {perm for _t, _fn, perm in PANEL[key]["items"]}
    if not (allowed_perms & perms):
        await callback.answer("⛔ Bu bo‘limga ruxsatingiz yo‘q.", show_alert=True)
        return
    await edit_or_answer(callback, panel_text(key), reply_markup=panel_kb(callback.from_user.id, key, perms))
