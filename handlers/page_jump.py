"""Universal sahifaga sakrash ({page}/{total_pages}) handlerlari."""

import logging
from typing import Optional, Set

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from admin.filters import has_admin_perm
from admin.handlers.events import _render_evt_pick
from admin.handlers.groups import _render_admin_groups_list
from admin.handlers.users import _render_admin_give_card_pick, _show_admin_users_list
from admin.v2.auction import _render_auction_pick_card
from admin.v2.cards import (
    _render_v2_action_list,
    _render_v2_cards_tab,
    _render_v2_give_users,
    _render_v2_media_titles,
    _render_v2_title_cards,
)
from admin.v2.economy import (
    _show_bot_store_add_pick,
    _show_bot_store_listings,
    _show_suggestions_inbox,
)
from admin.v2.groups_drop import _render_group_pick_card
from admin.v2.rbac import _show_rbac_pick_user
from admin.v2.support import _render_v2_logs
from database.session import get_session
from handlers.auction import _render_auction_new_pick, _show_auctions
from handlers.bot_store import _show_buy_list, _show_sell_list
from handlers.collection import (
    _render_anime_list,
    _render_anime_open,
    _render_char_list,
    _render_my_search_results,
    _render_v2_collection_list,
)
from handlers.market import _show_bot_sell_list, _show_market
from handlers.offer import _render_offer_card_pick
from handlers.search import _render_results
from handlers.trade import _show_sender_cards_for_trade
from handlers.wishlist import _render_wishlist_top, _show_wishlist
from models.user import User
from utils.callback_data import PageJumpCb
from utils.tg_helpers import (
    NotCommandFilter,
    is_admin_user,
    is_super_admin,
    safe_delete,
)

logger = logging.getLogger(__name__)
router = Router(name="page_jump")


class PageJumpStates(StatesGroup):
    waiting_page = State()


_ADMIN_KEY_PERMS: dict[str, str] = {
    "b_adm": "settings",
    "b_add": "settings",
    "sg_a": "cards",
    "av_tab": "cards",
    "av_tit": "cards",
    "av_mt": "cards",
    "av_al": "cards",
    "av_gu": "cards",
    "av_ap": "admins",
    "av_lg": "logs",
    "ad_ul": "users",
    "ad_gp": "users",
    "ad_ev": "events",
}


def _required_admin_perm(key: str, ext: str) -> Optional[str]:
    if key == "av_up":
        return "auction" if ext == "auction" else "groups"
    if key == "ad_gl":
        sec = ext.split("|", 1)[0] if ext else "groups"
        return "schedule" if sec == "schedule" else "groups"
    return _ADMIN_KEY_PERMS.get(key)


@router.callback_query(PageJumpCb.filter(F.key == "can"))
async def cb_page_jump_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(None)
    await state.update_data(
        pj_owner_id=None,
        pj_key=None,
        pj_total=None,
        pj_tid=None,
        pj_ext=None,
        pj_list_msg_id=None,
        pj_prompt_id=None,
    )
    if callback.message:
        await safe_delete(callback.bot, callback.message.chat.id, callback.message.message_id)
    await callback.answer("Bekor qilindi.")


@router.callback_query(PageJumpCb.filter())
async def cb_page_jump(
    callback: CallbackQuery,
    callback_data: PageJumpCb,
    state: FSMContext,
    is_bot_admin: bool = False,
    admin_perms: Optional[Set[str]] = None,
) -> None:
    total = max(1, int(callback_data.total or 1))
    if total <= 1:
        await callback.answer("Faqat 1 ta sahifa mavjud.", show_alert=False)
        return

    req_perm = _required_admin_perm(callback_data.key, callback_data.ext)
    if req_perm is not None:
        if not (is_bot_admin or is_admin_user(callback.from_user)) or not has_admin_perm(
            callback.from_user, admin_perms, req_perm
        ):
            await callback.answer("⛔ Bu bo‘limga ruxsatingiz yo‘q.", show_alert=True)
            return
        if callback_data.key == "av_ap" and not is_super_admin(callback.from_user):
            await callback.answer("⛔ Faqat Super Admin boshqara oladi.", show_alert=True)
            return

    old_data = await state.get_data()
    old_prompt_id = old_data.get("pj_prompt_id")
    if old_prompt_id and callback.message:
        await safe_delete(callback.bot, callback.message.chat.id, int(old_prompt_id))

    owner_id = callback.from_user.id
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Bekor qilish",
                    callback_data=PageJumpCb(owner_id=owner_id, key="can", total=0).pack(),
                )
            ]
        ]
    )
    prompt_text = (
        f"📄 <b>SAHIFAGA O‘TISH</b>\n\n"
        f"Nechanchi sahifaga o‘tmoqchisiz? (<b>1</b> dan <b>{total}</b> gacha son yozing):"
    )
    prompt_msg: Optional[Message] = None
    if callback.message:
        prompt_msg = await callback.message.answer(prompt_text, reply_markup=cancel_kb)

    await state.set_state(PageJumpStates.waiting_page)
    await state.update_data(
        pj_owner_id=owner_id,
        pj_key=callback_data.key,
        pj_total=total,
        pj_tid=int(callback_data.tid or 0),
        pj_ext=str(callback_data.ext or ""),
        pj_list_msg_id=(callback.message.message_id if callback.message else 0),
        pj_prompt_id=(prompt_msg.message_id if prompt_msg else 0),
    )
    await callback.answer()


@router.message(PageJumpStates.waiting_page, NotCommandFilter())
async def fsm_page_jump_input(
    message: Message,
    state: FSMContext,
    db_user: User,
    is_bot_admin: bool = False,
    admin_perms: Optional[Set[str]] = None,
) -> None:
    data = await state.get_data()
    if data.get("pj_owner_id") != message.from_user.id:
        return

    total = max(1, int(data.get("pj_total") or 1))
    raw = (message.text or "").strip().replace(" ", "")
    if not raw.isdigit():
        await message.answer(f"⚠️ 1 dan {total} gacha butun son kiriting.")
        return

    page = int(raw)
    if page < 1 or page > total:
        await message.answer(f"⚠️ Sahifa raqami <b>1</b> dan <b>{total}</b> gacha bo‘lishi kerak.")
        return

    key = str(data.get("pj_key") or "")
    tid = int(data.get("pj_tid") or 0)
    ext = str(data.get("pj_ext") or "")
    prompt_id = int(data.get("pj_prompt_id") or 0)
    list_msg_id = int(data.get("pj_list_msg_id") or 0)

    req_perm = _required_admin_perm(key, ext)
    if req_perm is not None:
        if not (is_bot_admin or is_admin_user(message.from_user)) or not has_admin_perm(
            message.from_user, admin_perms, req_perm
        ):
            await state.set_state(None)
            await message.answer("⛔ Bu bo‘limga ruxsatingiz yo‘q.")
            return
        if key == "av_ap" and not is_super_admin(message.from_user):
            await state.set_state(None)
            await message.answer("⛔ Faqat Super Admin boshqara oladi.")
            return

    await state.set_state(None)
    await state.update_data(
        pj_owner_id=None,
        pj_key=None,
        pj_total=None,
        pj_tid=None,
        pj_ext=None,
        pj_list_msg_id=None,
        pj_prompt_id=None,
    )

    chat_id = message.chat.id
    if prompt_id:
        await safe_delete(message.bot, chat_id, prompt_id)
    if list_msg_id and list_msg_id != message.message_id:
        await safe_delete(message.bot, chat_id, list_msg_id)
    await safe_delete(message.bot, chat_id, message.message_id)

    owner_id = message.from_user.id

    # --- User renderers ---
    if key == "c_v2":
        parts = ext.split("|", 1)
        ftab = parts[0] or "all"
        sort_by = parts[1] if len(parts) > 1 and parts[1] else "newest"
        await _render_v2_collection_list(message, owner_id, db_user, ftab, sort_by, page)
        return
    if key == "c_an":
        await _render_anime_list(message, owner_id, db_user, page)
        return
    if key == "c_ch":
        anime_page = int(ext) if ext.isdigit() and int(ext) > 0 else 1
        await _render_anime_open(message, owner_id, db_user, tid, anime_page, page)
        return
    if key == "c_rl":
        await _render_char_list(message, owner_id, db_user, ext or "all", page)
        return
    if key == "c_ms":
        q = str(data.get("my_search_query") or "").strip()
        if not q:
            await message.answer("⚠️ Qidiruv matni eskirgan, qayta qidiring.")
            return
        await _render_my_search_results(message, owner_id, db_user.id, q, page)
        return
    if key in ("m_all", "m_my"):
        await _show_market(message, owner_id, db_user, page=page, my_only=(key == "m_my"))
        return
    if key == "m_bot":
        await _show_bot_sell_list(message, owner_id, db_user, page=page)
        return
    if key == "s_res":
        parts = ext.split("|", 1)
        mode = parts[0] or "character"
        q_short = parts[1] if len(parts) > 1 else ""
        query = q_short
        if mode not in ("random", "rarity"):
            saved_q = str(data.get("last_search_query") or "")
            if mode == "unowned":
                query = query or saved_q
            elif saved_q and (not query or saved_q.startswith(query)):
                query = saved_q
        await _render_results(message, owner_id, mode, query, page)
        return
    if key == "o_pk":
        await _render_offer_card_pick(message, owner_id, db_user, tid, page)
        return
    if key == "a_lst":
        my_only = ext == "mine"
        owner_filter = "users" if ext == "f_usr" else ("admin" if ext == "f_adm" else "all")
        await _show_auctions(
            message,
            owner_id,
            page=page,
            my_only=my_only,
            db_user=db_user,
            owner_filter=owner_filter,
        )
        return
    if key == "a_new":
        await _render_auction_new_pick(message, owner_id, db_user, page=page)
        return
    if key == "w_my":
        await _show_wishlist(message, owner_id, db_user, page=page)
        return
    if key == "w_top":
        await _render_wishlist_top(message, owner_id, page=page)
        return
    if key == "b_buy":
        await _show_buy_list(message, page=page, owner_id=owner_id)
        return
    if key == "b_sel":
        await _show_sell_list(message, db_user, page=page, owner_id=owner_id)
        return
    if key == "t_cd":
        async with get_session() as session:
            partner = await session.get(User, tid)
        if partner is None or partner.id == db_user.id or partner.is_banned:
            await message.answer("❌ Foydalanuvchi topilmadi.")
            return
        await _show_sender_cards_for_trade(message, owner_id, db_user.id, partner, page=page)
        return

    # --- Admin renderers ---
    if key == "b_adm":
        await _show_bot_store_listings(message, owner_id, page=page)
        return
    if key == "b_add":
        await _show_bot_store_add_pick(message, owner_id, page=page)
        return
    if key == "sg_a":
        await _show_suggestions_inbox(message, owner_id, page=page)
        return
    if key == "av_tab":
        await _render_v2_cards_tab(message, owner_id, tab=ext or "all", page=page)
        return
    if key == "av_tit":
        await _render_v2_media_titles(message, owner_id, mgroup=ext or "anime", page=page)
        return
    if key == "av_mt":
        await _render_v2_title_cards(message, owner_id, rep_card_id=tid, mgroup=ext or "anime", page=page)
        return
    if key == "av_al":
        await _render_v2_action_list(message, owner_id, mode=ext or "edit_list", page=page)
        return
    if key == "av_gu":
        await _render_v2_give_users(message, owner_id, card_id=tid, back_tab=ext or "all", page=page)
        return
    if key == "av_ap":
        await _show_rbac_pick_user(message, owner_id, page=page)
        return
    if key == "av_up":
        if ext == "auction":
            await _render_auction_pick_card(message, owner_id, page=page)
        else:
            await _render_group_pick_card(message, owner_id, tid, page=page)
        return
    if key == "av_lg":
        await _render_v2_logs(message, owner_id, page=page)
        return
    if key == "ad_ul":
        await _show_admin_users_list(message, ext or "all", page, owner_id=owner_id)
        return
    if key == "ad_gp":
        await _render_admin_give_card_pick(message, owner_id, tid, page=page)
        return
    if key == "ad_gl":
        parts = ext.split("|", 1)
        sec = parts[0] or "groups"
        fmode = parts[1] if len(parts) > 1 and parts[1] else ("active" if sec == "groups" else "all")
        await _render_admin_groups_list(message, owner_id, sec, fmode, page=page)
        return
    if key == "ad_ev":
        await _render_evt_pick(message, owner_id, page=page)
        return

    logger.warning("Unknown page_jump key=%r", key)
