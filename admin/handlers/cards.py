"""Kartalarni boshqarish: qo‘shish, guruhga tashlash, o‘chirish."""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from admin.filters import IsAdminFilter, has_admin_perm
from admin.handlers.common import AdminStates, _AdminRbacCallbackMiddleware
from database.session import get_session
from keyboards.admin import admin_pick_group_for_card_kb
from keyboards.v2 import admin_v2_cards_menu_kb
from models.card import Card
from models.group import Group
from models.schedule import ActiveDrop
from services.admin_service import AdminService
from services.auction_wishlist_service import AuctionWishlistService
from services.card_service import CardService
from services.community_service import CommunityService
from services.drop_service import DropService
from services.gameplay_service import GameplayService
from services.settings_service import SettingsService
from utils.callback_data import AdmV2Cb, AdminCb
from utils.constants import MEDIA_TYPES
from utils.formatting import esc, format_card_block, format_drop_message
from utils.image_check import ERR_FORMAT, validate_image_url
from utils.tg_helpers import (
    NotCommandFilter,
    edit_or_answer,
    get_bot_username,
    send_card_message,
    send_card_visual,
)

logger = logging.getLogger(__name__)
router = Router(name="admin_cards")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())
router.callback_query.middleware(_AdminRbacCallbackMiddleware())


# ---------------------------------------------------------------------------
# CARD MANAGEMENT (Add Card 6-step, Drop to group, Delete confirm/exec, Edits)
# ---------------------------------------------------------------------------
@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "menu")))
async def cb_admin_cards_menu(callback: CallbackQuery, state: FSMContext, admin_perms: set | None = None) -> None:
    if not has_admin_perm(callback.from_user, admin_perms, "cards"):
        await callback.answer("⛔ Bu bo‘limga ruxsatingiz yo‘q.", show_alert=True)
        return
    await state.clear()
    await edit_or_answer(
        callback,
        "🎴 <b>CARD BO‘LIMI</b>\n\nKerakli bo‘limni tanlang:",
        reply_markup=admin_v2_cards_menu_kb(callback.from_user.id),
    )


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "drop_pick_grp")))
async def cb_admin_card_pick_group(callback: CallbackQuery, callback_data: AdminCb) -> None:
    async with get_session() as session:
        card = await session.get(Card, callback_data.target_id)
        groups = list(
            (await session.execute(select(Group).where(Group.is_active.is_(True), Group.is_banned.is_(False)).order_by(Group.id.desc())))
            .scalars()
            .all()
        )
    if card is None:
        await callback.answer("Karta topilmadi.", show_alert=True)
        return
    if not groups:
        await edit_or_answer(
            callback,
            "❌ Hozircha faol guruhlar yo‘q. Avval botni guruhga qo‘shing.",
            reply_markup=admin_v2_cards_menu_kb(callback.from_user.id),
        )
        return
    await edit_or_answer(
        callback,
        f"📤 <b>GURUHGA TASHLASH</b>\n\n"
        f"🎴 Karta: <b>{esc(card.character_name)}</b> ({esc(card.card_code)})\n"
        f"👇 Qaysi guruhga tashlaymiz?",
        reply_markup=admin_pick_group_for_card_kb(callback.from_user.id, card.id, groups),
    )


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "drop_exec")))
async def cb_admin_card_drop_to_group(callback: CallbackQuery, callback_data: AdminCb) -> None:
    group_id = int(callback_data.extra) if callback_data.extra.isdigit() else 0
    async with get_session() as session:
        card = await session.get(Card, callback_data.target_id)
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
        logger.warning("Failed dropping card %s to group %s: %s", card.id, group.telegram_chat_id, exc)
        await callback.answer("❌ Guruhga yuborib bo‘lmadi.", show_alert=True)
        return

    await callback.answer(f"✅ {card.character_name} → {group.title} guruhiga tashlandi!", show_alert=True)
    await edit_or_answer(
        callback,
        f"✅ <b>KARTA GURUHGA TASHLANDI!</b>\n\n🎴 Karta: <b>{esc(card.character_name)}</b>\n👥 Guruh: <b>{esc(group.title)}</b>",
        reply_markup=admin_v2_cards_menu_kb(callback.from_user.id),
    )


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "delete_confirm")))
async def cb_admin_card_delete_confirm(callback: CallbackQuery, callback_data: AdminCb) -> None:
    async with get_session() as session:
        card = await session.get(Card, callback_data.target_id)
    title = f"{card.character_name} ({card.card_code})" if card else f"#{callback_data.target_id}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Arxivlab o‘chirish (userlarda qolsin)",
                    callback_data=AdminCb(
                        owner_id=callback.from_user.id, section="cards", action="delete_exec", target_id=callback_data.target_id, extra="archive"
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🪙 To‘liq o‘chirish + Coin kompensatsiya",
                    callback_data=AdminCb(
                        owner_id=callback.from_user.id, section="cards", action="delete_exec", target_id=callback_data.target_id, extra="refund"
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ BEKOR",
                    callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="cards", act="del_list", p=1).pack(),
                )
            ],
        ]
    )
    await edit_or_answer(
        callback,
        f"🗑 <b>KARTANI O‘CHIRISH: {esc(title)}</b>\n\nMavjud egalardagi nusxalar bilan qanday yo‘l tutamiz?",
        reply_markup=kb,
    )


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "delete_exec")))
async def cb_admin_card_delete_exec(callback: CallbackQuery, callback_data: AdminCb) -> None:
    mode = callback_data.extra or "archive"
    async with get_session() as session:
        affected = await CardService.soft_delete_card(session, callback_data.target_id, migration_mode=mode)
        await AdminService.log_action(
            session, callback.from_user.id, "soft_delete_card", "card", str(callback_data.target_id), {"mode": mode, "affected_copies": affected}
        )
        await session.commit()
    await callback.answer(f"✅ Karta o‘chirildi ({mode}).", show_alert=True)
    await edit_or_answer(
        callback,
        f"✅ Karta o‘chirildi (rejim: <b>{esc(mode)}</b>).",
        reply_markup=admin_v2_cards_menu_kb(callback.from_user.id),
    )


async def _extract_image(message: Message):
    if message.photo:
        file_id = message.photo[-1].file_id
        return file_id, file_id, None
    if message.document is not None:
        return None, None, "⚠️ Rasmni <b>fayl</b> emas, oddiy <b>foto</b> sifatida yuboring (yoki to‘g‘ri havola yozing)."
    if message.text:
        ok, err = await validate_image_url(message.text)
        if ok:
            return message.text.strip(), None, None
        return None, None, err
    return None, None, ERR_FORMAT


def _clean_name(raw: str) -> str:
    return " ".join((raw or "").split())


def _media_type_pick_kb(owner_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i in range(0, len(MEDIA_TYPES), 3):
        builder.row(
            *[
                InlineKeyboardButton(
                    text=mt,
                    callback_data=AdminCb(owner_id=owner_id, section="cards", action="add_mtype", extra=mt).pack(),
                )
                for mt in MEDIA_TYPES[i : i + 3]
            ]
        )
    builder.row(
        InlineKeyboardButton(text="❌ BEKOR", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="v").pack())
    )
    return builder.as_markup()


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "add_start")))
async def cb_admin_add_card_start(callback: CallbackQuery, state: FSMContext, admin_perms: set | None = None) -> None:
    if not has_admin_perm(callback.from_user, admin_perms, "cards"):
        await callback.answer("⛔ Ruxsatingiz yo‘q.", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminStates.add_card_character)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Bekor", callback_data=AdmV2Cb(owner_id=callback.from_user.id, sec="cards", act="v").pack())]]
    )
    await edit_or_answer(
        callback,
        "➕ <b>YANGI KARTA QO‘SHISH</b>\n\n"
        "1/6: <b>Personaj ismini</b> yozing (masalan: <code>Gojo Satoru</code>):",
        reply_markup=kb,
    )


@router.message(AdminStates.add_card_character, NotCommandFilter())
async def fsm_add_card_char(message: Message, state: FSMContext) -> None:
    name = _clean_name(message.text or "")
    if not name or len(name) > 128:
        await message.answer("⚠️ Personaj ismini matn bilan yozing (1–128 belgi).")
        return
    await state.update_data(character_name=name)
    await state.set_state(AdminStates.add_card_media_type)
    await message.answer(
        "2/6: <b>Media turini</b> tanlang:",
        reply_markup=_media_type_pick_kb(message.from_user.id),
    )


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "add_mtype")))
async def cb_admin_add_card_mtype(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    mtype = callback_data.extra if callback_data.extra in MEDIA_TYPES else "Anime"
    await state.update_data(media_type=mtype)
    await state.set_state(AdminStates.add_card_anime)
    await edit_or_answer(
        callback,
        f"✅ Media turi: <b>{esc(mtype)}</b>\n\n"
        f"3/6: <b>{esc(mtype)} nomini</b> yozing (masalan: <code>Jujutsu Kaisen</code>):",
    )


@router.message(AdminStates.add_card_anime, NotCommandFilter())
async def fsm_add_card_anime(message: Message, state: FSMContext) -> None:
    anime = _clean_name(message.text or "")
    if not anime or len(anime) > 128:
        await message.answer("⚠️ Media nomini matn bilan yozing (1–128 belgi).")
        return
    await state.update_data(anime_name=anime)
    await state.set_state(AdminStates.add_card_meta)
    skip_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏭ O‘tkazib yuborish",
                    callback_data=AdminCb(owner_id=message.from_user.id, section="cards", action="add_meta_skip").pack(),
                )
            ]
        ]
    )
    await message.answer(
        "4/6: <b>Personaj haqida ma’lumot yozing</b> (ixtiyoriy):\n"
        "<i>Qisqacha tavsif yozing yoki O‘tkazib yuborish tugmasini bosing.</i>",
        reply_markup=skip_kb,
    )


async def _proceed_to_rarity_pick(target, state: FSMContext, owner_id: int) -> None:
    data = await state.get_data()
    anime = data.get("anime_name", "")
    char_name = data.get("character_name", "")
    await state.set_state(AdminStates.add_card_rarity)
    async with get_session() as session:
        ranks = await GameplayService.get_rank_definitions(session)
        cfg = await SettingsService.get_rarity_config(session)
        anime_exists = (
            await session.execute(
                select(Card.id).where(func.lower(func.trim(Card.anime_name)) == anime.lower(), Card.is_deleted.is_(False)).limit(1)
            )
        ).first()
        char_exists = (
            await session.execute(
                select(Card.id)
                .where(
                    func.lower(func.trim(Card.anime_name)) == anime.lower(),
                    func.lower(func.trim(Card.character_name)) == char_name.lower(),
                    Card.is_deleted.is_(False),
                )
                .limit(1)
            )
        ).first()
    hint = ""
    if char_exists:
        hint = "\nℹ️ Bu personaj shu bo‘limda allaqachon bor — yangi variant qo‘shiladi.\n"
    elif anime_exists:
        hint = "\nℹ️ Bu media bo‘limi mavjud — personaj shu bo‘limga qo‘shiladi.\n"

    builder = InlineKeyboardBuilder()
    for idx, r in enumerate(ranks):
        r_name = str(r["name"])
        r_emoji = str(r.get("emoji", "⭐"))
        r_drops = int(cfg.get(r_name, {}).get("drops", r.get("drop_limit", 1)))
        builder.row(
            InlineKeyboardButton(
                text=f"{r_emoji} {r_name} · {r.get('drop_probability', 0)}% · {r_drops} drop",
                callback_data=AdminCb(owner_id=owner_id, section="cards", action="add_rarity", extra=str(idx)).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(text="❌ BEKOR", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="v").pack())
    )
    text = (
        f"✅ {esc(char_name)} · {esc(anime)} ({esc(data.get('media_type', 'Anime'))})\n{hint}\n"
        "5/6: <b>Rankni</b> tanlang:"
    )
    if isinstance(target, CallbackQuery):
        await edit_or_answer(target, text, reply_markup=builder.as_markup())
    else:
        await target.answer(text, reply_markup=builder.as_markup())


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "add_meta_skip")))
async def cb_admin_add_meta_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(metadata_json={}, aliases=[])
    await _proceed_to_rarity_pick(callback, state, callback.from_user.id)


@router.message(AdminStates.add_card_meta, NotCommandFilter())
async def fsm_add_card_meta(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    meta_dict = {"description": raw} if raw else {}
    await state.update_data(metadata_json=meta_dict, aliases=[])
    await _proceed_to_rarity_pick(message, state, message.from_user.id)


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "add_rarity")))
async def cb_admin_add_card_rarity(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    data = await state.get_data()
    if "character_name" not in data or "anime_name" not in data:
        await callback.answer("Jarayon eskirgan. Qaytadan boshlang.", show_alert=True)
        return
    async with get_session() as session:
        ranks = await GameplayService.get_rank_definitions(session)
    rank_obj = None
    if callback_data.extra.isdigit() and 0 <= int(callback_data.extra) < len(ranks):
        rank_obj = ranks[int(callback_data.extra)]
    else:
        rank_obj = next((r for r in ranks if str(r["name"]) == callback_data.extra), None)
    if rank_obj is None:
        await callback.answer("Rank topilmadi.", show_alert=True)
        return

    rarity = str(rank_obj["name"])
    await state.update_data(
        rarity=rarity,
        rank_base_value=int(rank_obj.get("base_value", 1000)),
        rank_drop_limit=int(rank_obj.get("drop_limit", 1)),
        rank_emoji=str(rank_obj.get("emoji", "⭐")),
    )
    await state.set_state(AdminStates.add_card_image)
    await edit_or_answer(
        callback,
        f"✅ Rank: {rank_obj.get('emoji', '⭐')} <b>{esc(rarity)}</b> · 🔁 Tashlash soni: <b>{rank_obj.get('drop_limit', 1)}</b> ta\n\n"
        "6/6: <b>Karta rasmini</b> yuboring:\n"
        "• Telegram’da <b>foto</b> sifatida, yoki\n"
        "• to‘g‘ri havola: <code>https://...jpg</code> / png / webp",
    )


@router.message(AdminStates.add_card_image, NotCommandFilter())
async def fsm_add_card_image(message: Message, state: FSMContext) -> None:
    image_ref, file_id, err = await _extract_image(message)
    if err:
        await message.answer(err)
        return
    data = await state.get_data()
    if not all(k in data for k in ("character_name", "anime_name", "rarity")):
        await state.clear()
        await message.answer("⚠️ Jarayon eskirgan.", reply_markup=admin_v2_cards_menu_kb(message.from_user.id))
        return
    await state.clear()

    async with get_session() as session:
        cfg_all = await SettingsService.get_rarity_config(session)
        r_cfg = cfg_all.get(data["rarity"], {})
        base_val = int(r_cfg.get("value", data.get("rank_base_value", 1000)))
        drop_lim = int(r_cfg.get("drops", data.get("rank_drop_limit", 1)))
        card = await CardService.create_card(
            session=session,
            character_name=data["character_name"],
            anime_name=data["anime_name"],
            image=image_ref,
            rarity=data["rarity"],
            base_value=base_val,
            drop_limit=drop_lim,
            media_type=data.get("media_type", "Anime"),
            aliases=data.get("aliases") or [],
            metadata_json=data.get("metadata_json") or {},
        )
        if file_id:
            card.image_file_id = file_id
        await AuctionWishlistService.record_history(
            session, card.id, "created", details={"admin_tg_id": message.from_user.id}
        )
        await AdminService.log_action(
            session,
            message.from_user.id,
            "create_card",
            "card",
            str(card.id),
            {"code": card.card_code, "character": card.character_name, "rarity": card.rarity, "media_type": card.media_type},
        )
        await session.commit()

    text = f"✅ <b>Yangi karta qo‘shildi!</b>\n\n{format_card_block(card, show_status=True)}"
    try:
        await send_card_message(message.bot, message.chat.id, card, text, admin_v2_cards_menu_kb(message.from_user.id))
    except Exception:
        await message.answer(text, reply_markup=admin_v2_cards_menu_kb(message.from_user.id))
