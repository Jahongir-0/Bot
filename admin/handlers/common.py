"""Admin paneli uchun umumiy holatlar (FSM), middleware va doimiylar."""

from aiogram import BaseMiddleware
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery

from admin.filters import has_admin_perm
from utils.callback_data import AdminCb

_LEGACY_SECTION_PERM_MAP = {
    "home": None,
    "cards": "cards",
    "priority": "settings",
    "schedule": "groups",
    "groups": "groups",
    "users": "users",
    "ban": "users",
    "evt": "events",
    "events": "events",
    "stars": "stars",
    "stats": "stats",
    "settings": "settings",
}


class _AdminRbacCallbackMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: CallbackQuery, data: dict):
        if event.data and event.data.startswith("adm:"):
            try:
                cb = AdminCb.unpack(event.data)
                req = _LEGACY_SECTION_PERM_MAP.get(cb.section)
                if req and not has_admin_perm(event.from_user, data.get("admin_perms"), req):
                    await event.answer("⛔ Bu bo‘limga ruxsatingiz yo‘q.", show_alert=True)
                    return None
            except Exception:
                pass
        return await handler(event, data)


class AdminStates(StatesGroup):
    # Add Card (6 steps, 0 pipes)
    add_card_character = State()
    add_card_media_type = State()
    add_card_anime = State()
    add_card_meta = State()
    add_card_rarity = State()
    add_card_image = State()

    # Edit Card numeric/image
    edit_card_image = State()
    edit_card_value = State()
    edit_card_limit = State()

    # Users
    user_search = State()
    user_coin_delta = State()
    user_dm_text = State()
    user_temp_ban_hours = State()
    user_temp_ban_reason = State()

    # Groups & Schedule
    slot_priority_card = State()
    group_drop_count = State()
    group_msg_text = State()
    group_remove_reason = State()

    # Settings
    edit_ref_inviter = State()
    edit_ref_invitee = State()
    edit_group_cfg_field = State()
    edit_coin_gift_field = State()
    edit_daily_field = State()
    edit_drop_rule_field = State()
    edit_welcome_text = State()
    edit_vip_price = State()
    edit_pack_limit = State()

    # Stars
    stars_add = State()
    stars_edit = State()

    # Events
    evt_copies = State()
    evt_price_coins = State()
    evt_price_stars = State()
    evt_duration = State()

    # Bonus event
    bonus_name = State()
    bonus_hours = State()
