"""Admin v2 uchun umumiy holatlar (FSM) va yordamchi funksiyalar."""

from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from admin.filters import has_admin_perm
from utils.callback_data import AdmV2Cb, AdminCb

class AdminV2States(StatesGroup):
    # Cards
    waiting_zip_cards = State()
    waiting_card_search = State()
    waiting_card_edit_val = State()
    waiting_card_give_user_search = State()

    # Admins RBAC
    waiting_admin_search = State()
    waiting_admin_role_name = State()

    # Channels
    waiting_channel_link = State()
    waiting_channel_title = State()
    waiting_channel_days = State()
    waiting_channel_subs = State()

    # Ranks
    waiting_rank_field = State()
    waiting_rank_add_name = State()
    waiting_rank_add_emoji = State()
    waiting_rank_add_prob = State()
    waiting_rank_add_drops = State()

    # Quiz
    waiting_quiz_title = State()
    waiting_quiz_coins = State()
    waiting_quiz_q_text = State()
    waiting_quiz_q_opts = State()
    waiting_quiz_sched_dt = State()
    waiting_quiz_clone_coins = State()
    waiting_quiz_edit_title = State()
    waiting_quiz_edit_coins = State()
    waiting_quiz_edit_q_text = State()
    waiting_quiz_edit_q_opts = State()

    # Games
    waiting_games_field = State()

    # Auction
    waiting_auc_price = State()
    waiting_auc_hours = State()
    waiting_auc_step = State()
    waiting_auc_edit_val = State()

    # Bot Store & Commissions
    waiting_bs_price = State()
    waiting_bs_level = State()
    waiting_bs_disc = State()
    waiting_comm_pct = State()

    # Season
    waiting_season_name = State()
    waiting_season_days = State()
    waiting_season_prize = State()

    # Reklama
    waiting_ad_content = State()
    waiting_ad_edit_text = State()

    # Broadcast / Message
    waiting_msg_sched_dt = State()
    waiting_msg_content = State()

    # Memory / Backup
    waiting_backup_date = State()
    waiting_backup_restore = State()

    # Support & Security
    waiting_support_reply = State()
    waiting_sec_target = State()
    waiting_sec_reason = State()


def _back_home_btn(owner_id: int) -> InlineKeyboardButton:
    return InlineKeyboardButton(text="⬅️ Admin Panel", callback_data=AdminCb(owner_id=owner_id, section="home").pack())


async def _deny_if_no_perm(callback: CallbackQuery, admin_perms: set | None, sec: str) -> bool:
    if not has_admin_perm(callback.from_user, admin_perms, sec):
        await callback.answer("⛔ Bu bo‘limga ruxsatingiz yo‘q.", show_alert=True)
        return True
    return False


def _cancel_kb(owner_id: int, sec: str, act: str = "v") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Bekor", callback_data=AdmV2Cb(owner_id=owner_id, sec=sec, act=act).pack())]]
    )


def _local_tz() -> ZoneInfo:
    from utils.timeutils import TASHKENT

    return TASHKENT


def _parse_local_dt(raw: str) -> Optional[datetime]:
    cleaned = " ".join((raw or "").strip().split())
    for fmt in ("%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M", "%d.%m.%y %H:%M"):
        try:
            dt_local = datetime.strptime(cleaned, fmt).replace(tzinfo=_local_tz())
            return dt_local.astimezone(timezone.utc)
        except ValueError:
            continue
    return None
