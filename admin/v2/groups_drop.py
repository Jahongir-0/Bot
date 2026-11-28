"""Guruhga hali tushmagan kartani tanlab tashlash."""

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from admin.filters import IsAdminFilter
from admin.v2.common import _deny_if_no_perm
from database.session import get_session
from keyboards.v2 import admin_undropped_cards_kb
from models.card import Card
from models.group import Group
from models.schedule import ActiveDrop
from services.admin_service import AdminService
from services.card_service import CardService
from services.community_service import CommunityService
from services.drop_service import DropService
from utils.callback_data import AdmV2Cb, AdminCb
from utils.constants import PAGE_SIZE
from utils.formatting import esc, format_drop_message
from utils.tg_helpers import edit_or_answer, get_bot_username, send_card_visual, show_card_with_prompt

logger = logging.getLogger(__name__)
router = Router(name="admin_v2_groups_drop")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


async def _render_group_pick_card(target, owner_id: int, gid: int, page: int = 1) -> None:
    page = max(1, page)
    async with get_session() as session:
        cards, total = await AdminService.get_undropped_cards_paginated(session, page=page, page_size=PAGE_SIZE)
    back_cb = AdminCb(owner_id=owner_id, section="groups", action="group_view", target_id=gid).pack()
    txt = (
        f"🎴 <b>GURUHGA TASHLASH UCHUN KARTA TANLANG</b>\nHali tashlanmagan kartalar (<b>{total}</b> ta):"
        if cards
        else "📭 Hozircha hali guruhga tashlanmagan bo‘sh kartalar yo‘q."
    )
    await edit_or_answer(
        target,
        txt,
        reply_markup=admin_undropped_cards_kb(
            owner_id, cards, "groups", "drop_sel", page, total, back_cb, target_ctx_id=gid, page_size=PAGE_SIZE
        ),
    )


# ===========================================================================
# 2. GROUPS UNDROPPED DROP PICKER (sec="groups", act="pick_card" / "drop_sel" / "drop_go")
# ===========================================================================
@router.callback_query(AdmV2Cb.filter((F.sec == "groups") & (F.act == "pick_card")))
async def cb_v2_group_pick_card(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "groups"):
        return
    await _render_group_pick_card(callback, callback.from_user.id, callback_data.tid, page=max(1, callback_data.p))


@router.callback_query(AdmV2Cb.filter((F.sec == "groups") & (F.act == "drop_sel")))
async def cb_v2_group_drop_preview(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "groups"):
        return
    card_id = callback_data.tid
    group_id = int(callback_data.ext) if callback_data.ext.isdigit() else 0
    async with get_session() as session:
        card = await session.get(Card, card_id)
        group = await session.get(Group, group_id)
    if card is None or group is None:
        await callback.answer("Karta yoki guruh topilmadi.", show_alert=True)
        return
    if card.is_event_card:
        await callback.answer("🎯 Event kartasi guruhga tashlanmaydi.", show_alert=True)
        return
    owner_id = callback.from_user.id
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Ha, shu guruhga tashlash",
                    callback_data=AdmV2Cb(owner_id=owner_id, sec="groups", act="drop_go", tid=card.id, ext=str(group.id)).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Boshqa karta",
                    callback_data=AdmV2Cb(owner_id=owner_id, sec="groups", act="pick_card", tid=group.id, p=max(1, callback_data.p)).pack(),
                )
            ],
        ]
    )
    await show_card_with_prompt(
        callback,
        card,
        f"👥 Guruh: <b>{esc(group.title)}</b>\n\nShu kartani guruhga tashlaysizmi?",
        reply_markup=kb,
        title="📤 <b>GURUHGA DROP YUBORISH</b>",
    )


@router.callback_query(AdmV2Cb.filter((F.sec == "groups") & (F.act == "drop_go")))
async def cb_v2_group_drop_selected(callback: CallbackQuery, callback_data: AdmV2Cb, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "groups"):
        return
    card_id = callback_data.tid
    group_id = int(callback_data.ext) if callback_data.ext.isdigit() else 0
    async with get_session() as session:
        card = await session.get(Card, card_id)
        group = await session.get(Group, group_id)
        if card is None or group is None:
            await callback.answer("Karta yoki guruh topilmadi.", show_alert=True)
            return
        if card.is_event_card:
            await callback.answer("🎯 Event kartasi guruhga tashlanmaydi.", show_alert=True)
            return
        drop = await DropService.create_active_drop(session, group, card, force=True)
        bot_username = await get_bot_username(callback.bot)
        active_event = await CardService.get_active_event(session)
        ad_footer = await CommunityService.get_rendered_ad(
            session, bot_username=bot_username, user=None, placement="group"
        )
        await AdminService.log_action(
            session, callback.from_user.id, "manual_card_drop", "group", str(group.id), {"card_id": card.id, "card_code": card.card_code}
        )
        await session.commit()

    caption = format_drop_message(card, active_event=active_event, ad_footer=ad_footer)
    try:
        sent_msg = await send_card_visual(
            bot=callback.bot, chat_id=group.telegram_chat_id, image=card.image, caption=caption, reply_markup=None
        )
        async with get_session() as s2:
            d2 = await s2.get(ActiveDrop, drop.id)
            if d2 is not None:
                d2.telegram_message_id = sent_msg.message_id
                await s2.commit()
    except Exception as exc:
        logger.warning("Failed dropping selected card %s to group %s: %s", card.id, group.telegram_chat_id, exc)
        await callback.answer("❌ Guruhga yuborib bo‘lmadi.", show_alert=True)
        return

    await callback.answer(f"✅ {card.character_name} → {group.title} guruhiga tashlandi!", show_alert=True)
    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Guruhga qaytish", callback_data=AdminCb(owner_id=callback.from_user.id, section="groups", action="group_view", target_id=group.id).pack())]]
    )
    await edit_or_answer(
        callback,
        f"✅ <b>DROP YUBORILDI!</b>\n\n🎴 Karta: <b>{esc(card.character_name)}</b>\n👥 Guruh: <b>{esc(group.title)}</b>",
        reply_markup=back_kb,
    )
