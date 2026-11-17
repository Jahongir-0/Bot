import asyncio
import logging
import re
from typing import Optional
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from admin.filters import IsAdminFilter
from config.settings import get_settings
from database.session import get_session
from keyboards.admin import (
    admin_anime_characters_kb,
    admin_anime_sections_kb,
    admin_card_detail_kb,
    admin_cards_list_kb,
    admin_character_cards_kb,
    admin_cards_menu_kb,
    admin_group_settings_kb,
    admin_groups_list_kb,
    admin_home_kb,
    admin_pick_card_for_user_kb,
    admin_pick_group_for_card_kb,
    admin_schedule_detail_kb,
    admin_settings_kb,
    admin_stars_back_kb,
    admin_stars_confirm_kb,
    admin_stars_kb,
    admin_user_cards_remove_kb,
    admin_user_detail_kb,
    admin_users_list_kb,
    admin_users_menu_kb,
)
from app.event_announcer import announce_event
from keyboards.event import (
    admin_evt_candidates_kb,
    admin_evt_confirm_kb,
    admin_evt_detail_kb,
    admin_evt_menu_kb,
    admin_evt_price_type_kb,
    admin_evt_stars_kb,
)
from keyboards.main_menu import drop_claim_kb
from models.card import Card
from models.event import Event
from models.group import Group
from models.schedule import ActiveDrop
from models.user import User
from services.admin_service import AdminService
from services.event_service import EventService
from services.card_service import CardService
from services.drop_service import DropService
from services.ranking_service import RankingService
from services.settings_service import SettingsService
from services.user_service import UserService
from utils.callback_data import AdminCb, NavCb
from utils.constants import PAGE_SIZE, RankingMode, get_rarity_meta, list_rarities
from utils.formatting import (
    esc,
    fmt_coins,
    format_card_block,
    format_drop_message,
    format_group_settings,
)
from utils.image_check import ERR_FORMAT, validate_image_url
from utils.tg_helpers import NotCommandFilter, edit_or_answer, send_card_message, send_card_visual

logger = logging.getLogger(__name__)

router = Router(name="admin")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


class AdminStates(StatesGroup):
    add_card_character = State()
    add_card_anime = State()
    add_card_image = State()
    add_card_rarity = State()
    add_card_value = State()
    add_card_weight = State()

    edit_card_image = State()
    edit_card_value = State()

    user_search = State()
    user_coin_delta = State()

    slot_priority_card = State()
    edit_rarity_reward = State()
    event_create = State()
    broadcast_text = State()

    stars_add = State()
    stars_edit = State()

    evt_copies = State()
    evt_price_coins = State()
    edit_card_limit = State()
    edit_rank_value = State()
    edit_rank_drops = State()


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "🛡️ <b>ADMIN PANEL</b>\n\nBoshqaruv bo‘limini tanlang:",
        reply_markup=admin_home_kb(message.from_user.id),
    )


@router.message(Command("drop"))
async def cmd_admin_instant_drop(message: Message, db_group: Optional[Group] = None) -> None:
    """Admin-only instant card drop inside the current group."""
    if db_group is None:
        await message.answer("⚠️ Bu buyruq guruh ichida ishlaydi.")
        return

    async with get_session() as session:
        group = await session.get(Group, db_group.id)
        card, forced = (None, False)
        drop = None
        if group is not None:
            card, forced = await DropService.pick_card_for_group(session, group)
        if card is not None:
            drop = await DropService.create_active_drop(session, group, card, force=forced)
        if group is None or card is None or drop is None:
            await message.answer("❌ Hozircha tashlash uchun mos karta mavjud emas (limit tugagan yoki kartalar yo‘q).")
            return
        active_event = await CardService.get_active_event(session)
        await session.commit()

    caption = format_drop_message(card, active_event=active_event)
    sent_msg = await send_card_message(message.bot, group.telegram_chat_id, card, caption, drop_claim_kb(drop.id))
    async with get_session() as session:
        d_row = await session.get(ActiveDrop, drop.id)
        if d_row is not None:
            d_row.telegram_message_id = sent_msg.message_id
            await session.commit()


@router.callback_query(AdminCb.filter(F.section == "home"))
async def cb_admin_home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_or_answer(
        callback,
        "🛡️ <b>ADMIN PANEL</b>\n\nBoshqaruv bo‘limini tanlang:",
        reply_markup=admin_home_kb(callback.from_user.id),
    )


# ---------------------------------------------------------------------------
# STATISTICS
# ---------------------------------------------------------------------------
@router.callback_query(AdminCb.filter(F.section == "stats"))
async def cb_admin_stats(callback: CallbackQuery) -> None:
    async with get_session() as session:
        st = await AdminService.get_global_stats(session)
    text = (
        "📊 <b>BOT STATISTIKASI</b>\n\n"
        f"👤 Jami foydalanuvchilar: <b>{st['total_users']:,}</b>\n"
        f"🚫 Ban qilinganlar: <b>{st['banned_users']:,}</b>\n"
        f"👥 Ro‘yxatdan o‘tgan guruhlar: <b>{st['total_groups']:,}</b>\n"
        f"🎴 Katalogdagi kartalar: <b>{st['total_cards']:,}</b>\n"
        f"📦 Foydalanuvchilardagi jami nusxalar: <b>{st['total_copies']:,}</b>\n"
        f"🛒 Faol market e'lonlari: <b>{st['active_listings']:,}</b>\n"
        f"🪙 Iqtisodiyotdagi jami coinlar: <b>{st['total_coins']:,}</b>"
    )
    await edit_or_answer(callback, text, reply_markup=admin_home_kb(callback.from_user.id))


# ---------------------------------------------------------------------------
# CARD MANAGEMENT (Menu, Add, Inline Delete List, Inline Card List + Drop)
# ---------------------------------------------------------------------------
@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "menu")))
async def cb_admin_cards_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_or_answer(
        callback,
        "🎴 <b>CARD BO‘LIMI</b>\n\n"
        "• <b>➕ Karta qo‘shish</b> — yangi anime karta yaratish\n"
        "• <b>🗑 Karta o‘chirish</b> — ro‘yxatdan tanlab o‘chirish\n"
        "• <b>📋 Kartalar ro‘yxati</b> — karta rasmi, egasi va guruhga tashlash",
        reply_markup=admin_cards_menu_kb(callback.from_user.id),
    )


async def _show_admin_cards(callback: CallbackQuery, filter_mode: str, page: int, mode: str = "list") -> None:
    async with get_session() as session:
        cards, total = await AdminService.filter_cards(
            session, filter_mode=filter_mode, page=page, page_size=PAGE_SIZE
        )
    if mode == "del_list":
        text = f"🗑 <b>KARTA O‘CHIRISH</b>\n\nO‘chirmoqchi bo‘lgan kartangizni tanlang (Jami: {total}):"
    else:
        text = f"📋 <b>KARTALAR RO‘YXATI</b> (Filtr: {esc(filter_mode)} | Jami: {total})\n\nBatafsil ko‘rish va guruhga tashlash uchun kartani bosing:"

    await edit_or_answer(
        callback,
        text,
        reply_markup=admin_cards_list_kb(
            owner_id=callback.from_user.id,
            cards=cards,
            filter_mode=filter_mode,
            page=page,
            total=total,
            page_size=PAGE_SIZE,
            mode=mode,
        ),
    )


ADMIN_GROUP_PAGE = 6
_FILTER_MODES = {"all", "available", "disabled"}


def _parse_extra(extra: str, count: int):
    """'available-2-3' -> ('available', 2, 3). Noto'g'ri qiymatlar xavfsiz standartga tushadi."""
    parts = (extra or "").split("-")
    mode = parts[0] if parts and parts[0] in _FILTER_MODES else "all"
    nums = []
    for p in parts[1 : count + 1]:
        nums.append(int(p) if p.isdigit() and int(p) > 0 else 1)
    while len(nums) < count:
        nums.append(1)
    return (mode, *nums)


async def _show_admin_anime_sections(callback: CallbackQuery, filter_mode: str, page: int) -> None:
    async with get_session() as session:
        items, total = await AdminService.get_anime_sections(
            session, filter_mode=filter_mode, page=page, page_size=ADMIN_GROUP_PAGE
        )
        if not items and total > 0:
            page = 1
            items, total = await AdminService.get_anime_sections(
                session, filter_mode=filter_mode, page=1, page_size=ADMIN_GROUP_PAGE
            )
    if total == 0:
        text = "📋 <b>KARTALAR RO‘YXATI</b>\n\n📭 Bu filtr bo‘yicha kartalar topilmadi."
    else:
        lines = [f"📋 <b>KARTALAR — ANIME BO‘LIMLARI</b> (Filtr: {esc(filter_mode)} | Jami: {total} ta anime)\n"]
        for it in items:
            lines.append(
                f"📺 <b>{esc(it['name'])}</b> — {it['characters']} personaj, {it['cards']} karta, {it['copies']} nusxa"
            )
        lines.append("\nBo‘limni ochish uchun anime nomini bosing:")
        text = "\n".join(lines)
    await edit_or_answer(
        callback,
        text,
        reply_markup=admin_anime_sections_kb(
            callback.from_user.id, items, filter_mode, page, total, ADMIN_GROUP_PAGE
        ),
    )


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action.in_({"list", "del_list"}))))
async def cb_admin_cards_list(callback: CallbackQuery, callback_data: AdminCb) -> None:
    if callback_data.action == "list":
        mode = (callback_data.extra or "all").split("-")[0]
        await _show_admin_anime_sections(
            callback,
            filter_mode=mode if mode in _FILTER_MODES else "all",
            page=callback_data.page,
        )
        return
    await _show_admin_cards(
        callback,
        filter_mode=callback_data.extra or "all",
        page=callback_data.page,
        mode=callback_data.action,
    )


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "anime_open")))
async def cb_admin_anime_open(callback: CallbackQuery, callback_data: AdminCb) -> None:
    filter_mode, anime_page = _parse_extra(callback_data.extra, 1)
    page = max(1, callback_data.page)
    async with get_session() as session:
        info = await AdminService.get_anime_characters(
            session, callback_data.target_id, filter_mode=filter_mode, page=page, page_size=ADMIN_GROUP_PAGE
        )
        if info is not None and not info[1]:
            page = 1
            info = await AdminService.get_anime_characters(
                session, callback_data.target_id, filter_mode=filter_mode, page=1, page_size=ADMIN_GROUP_PAGE
            )
    if info is None:
        await callback.answer("Bu anime bo‘limida kartalar qolmagan.", show_alert=True)
        await _show_admin_anime_sections(callback, filter_mode, anime_page)
        return

    anime_name, items, total = info
    lines = [f"📺 <b>{esc(anime_name)}</b> — personajlar (Jami: {total})\n"]
    for it in items:
        lines.append(
            f"🎴 <b>{esc(it['name'])}</b> — {it['cards']} karta, {it['copies']} nusxa, {it['owners']} ega"
        )
    await edit_or_answer(
        callback,
        "\n".join(lines),
        reply_markup=admin_anime_characters_kb(
            callback.from_user.id, callback_data.target_id, items, filter_mode, anime_page, page, total, ADMIN_GROUP_PAGE
        ),
    )


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "char_open")))
async def cb_admin_char_open(callback: CallbackQuery, callback_data: AdminCb) -> None:
    filter_mode, char_page, anime_page = _parse_extra(callback_data.extra, 2)
    async with get_session() as session:
        info = await AdminService.get_character_cards(session, callback_data.target_id, filter_mode=filter_mode)
    if info is None:
        await callback.answer("Bu personaj kartalari topilmadi.", show_alert=True)
        await _show_admin_anime_sections(callback, filter_mode, anime_page)
        return

    character_name, anime_name, cards, anime_rep_id = info
    total_copies = sum(c[1] for c in cards)
    lines = [
        f"🎴 <b>{esc(character_name)}</b>",
        f"📺 {esc(anime_name)}",
        "",
        f"📦 {len(cards)} xil karta · jami {total_copies} nusxa\n",
    ]
    for card, copies, owners in cards:
        meta = get_rarity_meta(card.rarity)
        status = "🟢" if card.is_available else "🔴"
        lines.append(
            f"{status} {meta.emoji} {esc(card.rarity)} · <code>{esc(card.card_code)}</code> — "
            f"{copies} nusxa, {owners} ega, 💰 {fmt_coins(card.base_value)}"
        )
    await edit_or_answer(
        callback,
        "\n".join(lines),
        reply_markup=admin_character_cards_kb(
            callback.from_user.id, cards, anime_rep_id, filter_mode, char_page, anime_page
        ),
    )


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "detail")))
async def cb_admin_card_detail(callback: CallbackQuery, callback_data: AdminCb) -> None:
    async with get_session() as session:
        card = await session.get(Card, callback_data.target_id)
        if card is None:
            await callback.answer("Karta topilmadi.", show_alert=True)
            return
        owners_list, drops_sent = await AdminService.get_card_ownership_and_drops(session, card.id)
        reps = await AdminService.get_card_group_reps(session, card.id)

    total_copies = sum(qty for _, qty in owners_list)
    if owners_list:
        owners_preview = ", ".join(f"{esc(name)} (×{qty})" for name, qty in owners_list[:6])
        if len(owners_list) > 6:
            owners_preview += f" va yana {len(owners_list) - 6} ta"
        status_block = (
            f"📤 <b>Holati:</b> Tashlangan ({drops_sent} marta) | Jami nusxalar: {total_copies}\n"
            f"👤 <b>Egalari:</b> {owners_preview}"
        )
        not_dropped = False
    elif drops_sent > 0:
        status_block = (
            f"📤 <b>Holati:</b> Guruhga tashlangan ({drops_sent} marta), hali hech kim olmagan\n"
            f"👤 <b>Egalari:</b> Yo‘q"
        )
        not_dropped = False
    else:
        status_block = (
            "⏳ <b>Holati:</b> Hali hech qaysi guruhga tashlanmagan!\n"
            "👤 <b>Egalari:</b> Yo‘q"
        )
        not_dropped = True

    text = f"{format_card_block(card, show_status=True)}\n\n{status_block}"
    kb = admin_card_detail_kb(
        callback.from_user.id,
        card,
        page=callback_data.page,
        not_dropped_yet=not_dropped,
        char_rep_id=reps[1] if reps else 0,
    )

    if callback.message and card.image and (card.image.startswith("http") or len(card.image) > 20):
        try:
            await callback.message.delete()
            await callback.message.answer_photo(photo=card.image, caption=text, reply_markup=kb)
            await callback.answer()
            return
        except Exception as exc:
            logger.debug("Admin card photo preview fallback: %s", exc)

    await edit_or_answer(callback, text, reply_markup=kb)


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "drop_pick_grp")))
async def cb_admin_card_pick_group(callback: CallbackQuery, callback_data: AdminCb) -> None:
    async with get_session() as session:
        card = await session.get(Card, callback_data.target_id)
        groups = list(
            (await session.execute(select(Group).where(Group.is_active.is_(True)).order_by(Group.id.desc())))
            .scalars()
            .all()
        )
    if card is None:
        await callback.answer("Karta topilmadi.", show_alert=True)
        return
    if not groups:
        await edit_or_answer(
            callback,
            "❌ Hozircha bot qo‘shilgan faol guruhlar yo‘q. Avval botni guruhga qo‘shing.",
            reply_markup=admin_cards_menu_kb(callback.from_user.id),
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
            await callback.answer("🎯 Event kartasi guruhga tashlanmaydi — u faqat event orqali olinadi.", show_alert=True)
            return
        drop = await DropService.create_active_drop(session, group, card, force=True)
        active_event = await CardService.get_active_event(session)
        await AdminService.log_action(
            session,
            callback.from_user.id,
            "manual_card_drop",
            "group",
            str(group.id),
            {"card_id": card.id, "card_code": card.card_code},
        )
        await session.commit()

    caption = format_drop_message(card, active_event=active_event)
    try:
        sent_msg = await send_card_visual(
            bot=callback.bot,
            chat_id=group.telegram_chat_id,
            image=card.image,
            caption=caption,
            reply_markup=drop_claim_kb(drop.id),
        )
        async with get_session() as s2:
            d2 = await s2.get(ActiveDrop, drop.id)
            if d2 is not None:
                d2.telegram_message_id = sent_msg.message_id
                await s2.commit()
    except Exception as exc:
        logger.warning("Failed dropping card %s to group %s: %s", card.id, group.telegram_chat_id, exc)
        await callback.answer("❌ Guruhga yuborib bo‘lmadi (bot guruhdan chiqarilgan bo‘lishi mumkin).", show_alert=True)
        return

    await callback.answer(f"✅ {card.character_name} → {group.title} guruhiga tashlandi!", show_alert=True)
    await edit_or_answer(
        callback,
        f"✅ <b>KARTA GURUHGA TASHLANDI!</b>\n\n"
        f"🎴 Karta: <b>{esc(card.character_name)}</b>\n"
        f"👥 Guruh: <b>{esc(group.title)}</b>",
        reply_markup=admin_cards_menu_kb(callback.from_user.id),
    )


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "toggle")))
async def cb_admin_card_toggle(callback: CallbackQuery, callback_data: AdminCb) -> None:
    async with get_session() as session:
        card = await session.get(Card, callback_data.target_id)
        if card is None:
            await callback.answer("Topilmadi.", show_alert=True)
            return
        card.is_available = not card.is_available
        await AdminService.log_action(
            session,
            callback.from_user.id,
            "toggle_card",
            "card",
            str(card.id),
            {"is_available": card.is_available},
        )
        await session.commit()
    await callback.answer("Holat o‘zgartirildi.")
    await cb_admin_card_detail(callback, callback_data)


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
                        owner_id=callback.from_user.id,
                        section="cards",
                        action="delete_exec",
                        target_id=callback_data.target_id,
                        extra="archive",
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🪙 To‘liq o‘chirish + Coin kompensatsiya",
                    callback_data=AdminCb(
                        owner_id=callback.from_user.id,
                        section="cards",
                        action="delete_exec",
                        target_id=callback_data.target_id,
                        extra="refund",
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ BEKOR",
                    callback_data=AdminCb(
                        owner_id=callback.from_user.id,
                        section="cards",
                        action="menu",
                    ).pack(),
                )
            ],
        ]
    )
    await edit_or_answer(
        callback,
        f"🗑 <b>KARTANI O‘CHIRISH: {esc(title)}</b>\n\n"
        "Mavjud egalardagi nusxalar bilan qanday yo‘l tutamiz?",
        reply_markup=kb,
    )


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "delete_exec")))
async def cb_admin_card_delete_exec(callback: CallbackQuery, callback_data: AdminCb) -> None:
    mode = callback_data.extra or "archive"
    async with get_session() as session:
        affected = await CardService.soft_delete_card(session, callback_data.target_id, migration_mode=mode)
        await AdminService.log_action(
            session,
            callback.from_user.id,
            "soft_delete_card",
            "card",
            str(callback_data.target_id),
            {"mode": mode, "affected_copies": affected},
        )
        await session.commit()
    await callback.answer(f"✅ Karta o‘chirildi ({mode}).", show_alert=True)
    await _show_admin_cards(callback, "all", 1, mode="del_list")


async def _extract_image(message: Message):
    """(image_ref, file_id, xato). Telegram foto yoki tekshirilgan URL qabul qilinadi, boshqasi rad etiladi."""
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


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "add_start")))
async def cb_admin_add_card_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AdminStates.add_card_character)
    await edit_or_answer(
        callback,
        "➕ <b>YANGI KARTA QO‘SHISH</b>\n\n1/4: <b>Personaj ismini</b> yozing "
        "(masalan: <code>Gojo Satoru</code>):\n<i>Bekor qilish: /cancel</i>",
    )


@router.message(AdminStates.add_card_character, NotCommandFilter())
async def fsm_add_card_char(message: Message, state: FSMContext) -> None:
    name = _clean_name(message.text or "")
    if not name or len(name) > 128:
        await message.answer("⚠️ Personaj ismini matn bilan yozing (1–128 belgi).")
        return
    await state.update_data(character_name=name)
    await state.set_state(AdminStates.add_card_anime)
    await message.answer("2/4: <b>Anime nomini</b> yozing (masalan: <code>Jujutsu Kaisen</code>):")


def _rarity_pick_kb(owner_id: int, cfg: dict) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for r in list_rarities():
        meta = get_rarity_meta(r)
        builder.row(
            InlineKeyboardButton(
                text=f"{meta.emoji} {r} · {cfg[r]['value']:,} coin · {cfg[r]['drops']} ta",
                callback_data=AdminCb(owner_id=owner_id, section="cards", action="add_rarity", extra=r).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="❌ BEKOR", callback_data=AdminCb(owner_id=owner_id, section="cards", action="menu").pack()
        )
    )
    return builder.as_markup()


@router.message(AdminStates.add_card_anime, NotCommandFilter())
async def fsm_add_card_anime(message: Message, state: FSMContext) -> None:
    anime = _clean_name(message.text or "")
    if not anime or len(anime) > 128:
        await message.answer("⚠️ Anime nomini matn bilan yozing (1–128 belgi).")
        return
    data = await state.get_data()
    await state.update_data(anime_name=anime)
    await state.set_state(AdminStates.add_card_rarity)

    async with get_session() as session:
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
                    func.lower(func.trim(Card.character_name)) == data["character_name"].lower(),
                    Card.is_deleted.is_(False),
                )
                .limit(1)
            )
        ).first()

    hint = ""
    if char_exists:
        hint = "\nℹ️ Bu personaj shu anime bo‘limida allaqachon bor — yangi karta uning ostiga qo‘shiladi.\n"
    elif anime_exists:
        hint = "\nℹ️ Bu anime bo‘limi mavjud — personaj shu bo‘lim ichiga qo‘shiladi.\n"
    await message.answer(
        f"✅ {esc(data['character_name'])} · {esc(anime)}\n{hint}\n"
        "3/4: <b>Rankni</b> tanlang — karta narxi va tashlash soni avtomatik qo‘yiladi:",
        reply_markup=_rarity_pick_kb(message.from_user.id, cfg),
    )


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "add_rarity")))
async def cb_admin_add_card_rarity(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    data = await state.get_data()
    rarity = callback_data.extra
    if "character_name" not in data or "anime_name" not in data or rarity not in list_rarities():
        await callback.answer("Jarayon eskirgan. Qaytadan boshlang.", show_alert=True)
        return
    async with get_session() as session:
        cfg = (await SettingsService.get_rarity_config(session))[rarity]
    meta = get_rarity_meta(rarity)
    await state.update_data(rarity=rarity)
    await state.set_state(AdminStates.add_card_image)
    await edit_or_answer(
        callback,
        f"✅ Rank: {meta.emoji} <b>{esc(rarity)}</b>\n"
        f"💰 Narx: <b>{cfg['value']:,}</b> coin · 🔁 Tashlash soni: <b>{cfg['drops']}</b> ta (avtomatik)\n\n"
        "4/4: <b>Karta rasmini</b> yuboring:\n"
        "• Telegram’da <b>foto</b> sifatida, yoki\n"
        "• to‘g‘ri havola: <code>https://...jpg</code> / png / webp\n"
        "<i>Noto‘g‘ri havola yoki boshqa format qabul qilinmaydi.</i>",
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
        await message.answer("⚠️ Jarayon eskirgan. Qaytadan boshlang.", reply_markup=admin_cards_menu_kb(message.from_user.id))
        return
    await state.clear()

    async with get_session() as session:
        cfg = (await SettingsService.get_rarity_config(session))[data["rarity"]]
        card = await CardService.create_card(
            session=session,
            character_name=data["character_name"],
            anime_name=data["anime_name"],
            image=image_ref,
            rarity=data["rarity"],
            base_value=cfg["value"],
            drop_limit=cfg["drops"],
        )
        if file_id:
            card.image_file_id = file_id
        await AdminService.log_action(
            session,
            message.from_user.id,
            "create_card",
            "card",
            str(card.id),
            {"code": card.card_code, "character": card.character_name, "rarity": card.rarity},
        )
        await session.commit()

    text = (
        f"✅ <b>Yangi karta qo‘shildi!</b>\n\n{format_card_block(card, show_status=True)}\n"
        f"🔁 Tashlash soni: <b>{card.drop_limit}</b> ta"
    )
    try:
        await send_card_message(message.bot, message.chat.id, card, text, admin_cards_menu_kb(message.from_user.id))
    except Exception:
        await message.answer(text, reply_markup=admin_cards_menu_kb(message.from_user.id))


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "edit_image")))
async def cb_edit_card_image(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    await state.set_state(AdminStates.edit_card_image)
    await state.update_data(edit_card_id=callback_data.target_id)
    await edit_or_answer(callback, "🖼 Yangi rasmni <b>foto</b> sifatida yuboring yoki to‘g‘ri havola (jpg/png/webp) yozing:")


@router.message(AdminStates.edit_card_image, NotCommandFilter())
async def fsm_edit_card_image(message: Message, state: FSMContext) -> None:
    image_ref, file_id, err = await _extract_image(message)
    if err:
        await message.answer(err)
        return
    data = await state.get_data()
    await state.clear()
    async with get_session() as session:
        card = await session.get(Card, int(data["edit_card_id"]))
        if card:
            card.image = image_ref[:512]
            card.image_file_id = file_id  # URL bo'lsa keshni tozalaymiz (yangi rasm yuborilganda qayta keshlanadi)
            await session.commit()
    await message.answer("✅ Karta rasmi yangilandi!", reply_markup=admin_cards_menu_kb(message.from_user.id))


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "edit_limit")))
async def cb_edit_card_limit(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    async with get_session() as session:
        card = await session.get(Card, callback_data.target_id)
    if card is None:
        await callback.answer("Karta topilmadi.", show_alert=True)
        return
    await state.set_state(AdminStates.edit_card_limit)
    await state.update_data(edit_card_id=card.id)
    await edit_or_answer(
        callback,
        f"🔁 <b>{esc(card.character_name)}</b>\nHozir: tashlangan <b>{card.dropped_count}</b> / limit <b>{card.drop_limit}</b>\n\n"
        "Yangi tashlash sonini (jami nusxa limiti) kiriting (1–1000):",
    )


@router.message(AdminStates.edit_card_limit, NotCommandFilter())
async def fsm_edit_card_limit(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or not (1 <= int(raw) <= 1000):
        await message.answer("⚠️ 1 dan 1000 gacha butun son kiriting.")
        return
    data = await state.get_data()
    await state.clear()
    async with get_session() as session:
        card = await session.get(Card, int(data["edit_card_id"]))
        if card:
            card.drop_limit = int(raw)
            await AdminService.log_action(
                session, message.from_user.id, "edit_card_limit", "card", str(card.id), {"limit": int(raw)}
            )
            await session.commit()
    await message.answer("✅ Tashlash soni yangilandi!", reply_markup=admin_cards_menu_kb(message.from_user.id))


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "edit_value")))
async def cb_edit_card_value(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    await state.set_state(AdminStates.edit_card_value)
    await state.update_data(edit_card_id=callback_data.target_id)
    await edit_or_answer(callback, "💰 Yangi base value (coins) raqamini kiriting:")


@router.message(AdminStates.edit_card_value, NotCommandFilter())
async def fsm_edit_card_value(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("⚠️ Faqat musbat raqam kiriting.")
        return
    data = await state.get_data()
    await state.clear()
    async with get_session() as session:
        card = await session.get(Card, int(data["edit_card_id"]))
        if card:
            card.base_value = int(raw)
            await session.commit()
    await message.answer("✅ Karta qiymati yangilandi!", reply_markup=admin_cards_menu_kb(message.from_user.id))


# ---------------------------------------------------------------------------
# PRIORITY CARD
# ---------------------------------------------------------------------------
@router.callback_query(AdminCb.filter(F.section == "priority"))
async def cb_admin_priority(callback: CallbackQuery, callback_data: AdminCb) -> None:
    async with get_session() as session:
        if callback_data.action == "set":
            await SettingsService.set_next_priority_card_id(session, callback_data.target_id)
            await AdminService.log_action(
                session, callback.from_user.id, "set_priority_card", "card", str(callback_data.target_id)
            )
            await session.commit()
            await callback.answer("⭐ Keyingi drop uchun priority karta belgilandi!", show_alert=True)
        elif callback_data.action == "clear":
            await SettingsService.set_next_priority_card_id(session, None)
            await session.commit()
            await callback.answer("Priority o‘chirildi.")

        pid = await SettingsService.get_next_priority_card_id(session)
        p_card = await session.get(Card, pid) if pid else None

    if p_card:
        text = (
            f"🎯 <b>NEXT PRIORITY CARD</b>\n\n"
            f"🎴 {esc(p_card.character_name)} ({esc(p_card.rarity)}) [<code>{esc(p_card.card_code)}</code>]\n"
            f"Keyingi dropda shu karta tushadi va priority avtomatik o‘chadi."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ Priorityni bekor qilish",
                        callback_data=AdminCb(
                            owner_id=callback.from_user.id, section="priority", action="clear"
                        ).pack(),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ ORQAGA",
                        callback_data=AdminCb(owner_id=callback.from_user.id, section="home").pack(),
                    )
                ],
            ]
        )
    else:
        text = (
            "🎯 <b>NEXT PRIORITY CARD</b>\n\n"
            "Hozircha priority karta tanlanmagan.\n"
            "Card Bo‘limi -> Kartalar ro‘yxatidan istalgan kartani ochib <b>[⭐ KEYINGI DROP QILISH]</b> ni bosing."
        )
        kb = admin_home_kb(callback.from_user.id)
    await edit_or_answer(callback, text, reply_markup=kb)


# ---------------------------------------------------------------------------
# DAILY DROP SCHEDULE (Global Overview + Per-Group Detail) & GROUP SETTINGS
# ---------------------------------------------------------------------------
@router.callback_query(AdminCb.filter((F.section == "schedule") & (F.action == "overview")))
async def cb_schedule_overview(callback: CallbackQuery) -> None:
    tz = ZoneInfo(get_settings().timezone)
    async with get_session() as session:
        groups = list((await session.execute(select(Group).order_by(Group.id.desc()))).scalars().all())
        for g in groups:
            if g.is_active and g.drop_enabled:
                await DropService.ensure_daily_schedule_for_group(session, g)
        await session.commit()
        upcoming = await AdminService.get_upcoming_schedules_all_groups(session, limit=15)

    lines = ["📅 <b>QAYSI GURUHGA QACHON KARTA YUBORILISHI</b>\n"]
    if not upcoming:
        lines.append("⏳ Hozircha rejalashtirilgan navbatdagi droplar yo‘q.")
    else:
        for sched, grp, card in upcoming:
            t_str = sched.scheduled_at.astimezone(tz).strftime("%H:%M")
            c_name = f"{card.character_name} ({card.rarity})" if card else "Random"
            mark = "⭐" if sched.is_priority_override else "⏰"
            lines.append(f"{mark} <code>{t_str}</code> — <b>{esc(grp.title)}</b> → {esc(c_name)}")

    if groups:
        lines.append("\n👇 Batafsil ko‘rish yoki jadvalni yangilash uchun guruhni tanlang:")
    await edit_or_answer(
        callback,
        "\n".join(lines),
        reply_markup=admin_groups_list_kb(callback.from_user.id, groups, section="schedule"),
    )


@router.callback_query(AdminCb.filter((F.section == "schedule") & (F.action == "groups")))
@router.callback_query(AdminCb.filter((F.section == "groups") & (F.action == "list")))
async def cb_admin_groups_list(callback: CallbackQuery, callback_data: AdminCb) -> None:
    async with get_session() as session:
        groups = list((await session.execute(select(Group).order_by(Group.id.desc()))).scalars().all())
    title = "📅 <b>DROP SCHEDULE — GURUH TANLANG</b>" if callback_data.section == "schedule" else "⚙️ <b>GURUH SOZLAMALARI</b>"
    if not groups:
        await edit_or_answer(
            callback,
            f"{title}\n\nHozircha ro‘yxatga olingan guruhlar yo‘q. Botni guruhga qo‘shing.",
            reply_markup=admin_home_kb(callback.from_user.id),
        )
        return
    await edit_or_answer(
        callback,
        title,
        reply_markup=admin_groups_list_kb(callback.from_user.id, groups, section=callback_data.section),
    )


async def _render_schedule_detail(callback: CallbackQuery, group_id: int) -> None:
    tz = ZoneInfo(get_settings().timezone)
    async with get_session() as session:
        group = await session.get(Group, group_id)
        if group is None:
            await callback.answer("Guruh topilmadi.", show_alert=True)
            return
        schedules = await DropService.ensure_daily_schedule_for_group(session, group)
        await session.commit()

        lines = [f"📅 <b>BUGUNGI JADVAL — {esc(group.title)}</b> ({group.daily_drop_count}/kun)\n"]
        for sc in schedules:
            local_t = sc.scheduled_at.astimezone(tz).strftime("%H:%M")
            card_label = "Random"
            if sc.card_id:
                c = await session.get(Card, sc.card_id)
                if c:
                    card_label = f"{c.character_name} ({c.rarity})"
            st_icon = "✅" if sc.status == "sent" else ("⭐" if sc.is_priority_override else "⏳")
            lines.append(f"{st_icon} <code>{local_t}</code> — {esc(card_label)} [#{sc.id}]")
        text = "\n".join(lines) if schedules else "Bugun uchun drop rejalashtirilmagan."

    await edit_or_answer(
        callback,
        text,
        reply_markup=admin_schedule_detail_kb(callback.from_user.id, group_id, schedules),
    )


@router.callback_query(AdminCb.filter((F.section == "schedule") & (F.action == "group_view")))
async def cb_schedule_group_view(callback: CallbackQuery, callback_data: AdminCb) -> None:
    await _render_schedule_detail(callback, callback_data.target_id)


@router.callback_query(AdminCb.filter((F.section == "schedule") & (F.action == "regen")))
async def cb_schedule_regen(callback: CallbackQuery, callback_data: AdminCb) -> None:
    async with get_session() as session:
        group = await session.get(Group, callback_data.target_id)
        if group:
            await DropService.ensure_daily_schedule_for_group(session, group, force_regenerate=True)
            await session.commit()
    await callback.answer("🔄 Jadval qayta yaratildi!")
    await _render_schedule_detail(callback, callback_data.target_id)


@router.callback_query(AdminCb.filter((F.section == "schedule") & (F.action == "slot_prio")))
async def cb_slot_priority_prompt(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    await state.set_state(AdminStates.slot_priority_card)
    await state.update_data(slot_id=callback_data.target_id)
    await edit_or_answer(
        callback,
        f"⭐ Slot #{callback_data.target_id} ga qo‘ymoqchi bo‘lgan Card ID (masalan <code>CRD-0001</code>) yozing:",
    )


@router.message(AdminStates.slot_priority_card, NotCommandFilter())
async def fsm_slot_priority_card(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    slot_id = int(data["slot_id"])
    ref = (message.text or "").strip()
    await state.clear()

    async with get_session() as session:
        card = (
            await session.execute(select(Card).where(func.lower(Card.card_code) == ref.lower()))
        ).scalar_one_or_none()
        if card is None and ref.isdigit():
            card = await session.get(Card, int(ref))
        if card is None:
            await message.answer("❌ Karta topilmadi.", reply_markup=admin_home_kb(message.from_user.id))
            return
        sched = await DropService.replace_schedule_card(session, slot_id, card.id)
        await session.commit()

    if sched is None:
        await message.answer("❌ Slot topilmadi yoki allaqachon yuborilgan.")
        return
    await message.answer(
        f"✅ Slot #{slot_id} ga <b>{esc(card.character_name)}</b> qo‘yildi!",
        reply_markup=admin_home_kb(message.from_user.id),
    )


@router.callback_query(
    AdminCb.filter(
        (F.section == "groups")
        & (F.action.in_({"group_view", "toggle_drop", "toggle_auto", "drop_inc", "drop_dec", "drop_now"}))
    )
)
async def cb_admin_group_control(callback: CallbackQuery, callback_data: AdminCb) -> None:
    async with get_session() as session:
        group = await session.get(Group, callback_data.target_id)
        if group is None:
            await callback.answer("Guruh topilmadi.", show_alert=True)
            return

        if callback_data.action == "toggle_drop":
            group.drop_enabled = not group.drop_enabled
        elif callback_data.action == "toggle_auto":
            group.auto_drop_count = not group.auto_drop_count
        elif callback_data.action == "drop_inc":
            group.auto_drop_count = False  # qo'lda sozlandi
            group.daily_drop_count = min(500, group.daily_drop_count + 1)
        elif callback_data.action == "drop_dec":
            group.auto_drop_count = False
            group.daily_drop_count = max(0, group.daily_drop_count - 1)
        elif callback_data.action == "drop_now":
            card, forced = await DropService.pick_card_for_group(session, group)
            drop = None
            if card is not None:
                drop = await DropService.create_active_drop(session, group, card, force=forced)
            if card is None or drop is None:
                await callback.answer("Mos karta yo‘q (limit tugagan yoki kartalar yo‘q)!", show_alert=True)
                return
            active_event = await CardService.get_active_event(session)
            await session.commit()
            caption = format_drop_message(card, active_event=active_event)
            msg = await send_card_visual(
                bot=callback.bot,
                chat_id=group.telegram_chat_id,
                image=card.image,
                caption=caption,
                reply_markup=drop_claim_kb(drop.id),
            )
            async with get_session() as s2:
                d2 = await s2.get(ActiveDrop, drop.id)
                if d2:
                    d2.telegram_message_id = msg.message_id
                    await s2.commit()
            await callback.answer("⚡ Drop guruhga yuborildi!", show_alert=True)
            return

        await session.commit()
        text = format_group_settings(group)
        kb = admin_group_settings_kb(callback.from_user.id, group)

    await edit_or_answer(callback, text, reply_markup=kb)


# ---------------------------------------------------------------------------
# USER MANAGEMENT (Menu, Ban/Unban inline lists, All Users list, Coin/Card +/-)
# ---------------------------------------------------------------------------
@router.callback_query(AdminCb.filter((F.section == "users") & (F.action == "menu")))
async def cb_admin_users_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_or_answer(
        callback,
        "👤 <b>USERLAR BO‘LIMI</b>\n\n"
        "• <b>🚫 Ban qilish</b> — faol userlardan tanlab ban qilish\n"
        "• <b>🔓 Bandan chiqarish</b> — banlangan userlardan tanlab ochish\n"
        "• <b>👥 Userlar ro‘yxati</b> — coin/card berish va olish\n"
        "• <b>🔍 User qidirish</b> — ID yoki @username bilan qidirish",
        reply_markup=admin_users_menu_kb(callback.from_user.id),
    )


async def _show_admin_users_list(callback: CallbackQuery, filter_mode: str, page: int) -> None:
    async with get_session() as session:
        users, total = await AdminService.get_users_paginated(
            session, filter_mode=filter_mode, page=page, page_size=6
        )
    titles = {
        "active": f"🚫 <b>BAN QILISH</b> (Faol userlar: {total})\n\nBan qilmoqchi bo‘lgan userni tanlang:",
        "banned": f"🔓 <b>BANDAN CHIQARISH</b> (Banlanganlar: {total})\n\nBandan chiqarish uchun userni tanlang:",
        "all": f"👥 <b>USERLAR RO‘YXATI</b> (Jami: {total})\n\nBoshqarish uchun userni tanlang:",
    }
    text = titles.get(filter_mode, titles["all"])
    if not users:
        text += "\n\n📭 Ro‘yxat bo‘sh."
    await edit_or_answer(
        callback,
        text,
        reply_markup=admin_users_list_kb(
            owner_id=callback.from_user.id,
            users=users,
            filter_mode=filter_mode,
            page=page,
            total=total,
            page_size=6,
        ),
    )


@router.callback_query(AdminCb.filter((F.section == "users") & (F.action == "list")))
async def cb_admin_users_list(callback: CallbackQuery, callback_data: AdminCb) -> None:
    await _show_admin_users_list(callback, callback_data.extra or "all", callback_data.page)


@router.callback_query(AdminCb.filter((F.section == "users") & (F.action.in_({"quick_ban", "quick_unban"}))))
async def cb_admin_quick_ban(callback: CallbackQuery, callback_data: AdminCb) -> None:
    ban = callback_data.action == "quick_ban"
    async with get_session() as session:
        await AdminService.set_global_ban(session, callback.from_user.id, callback_data.target_id, ban=ban)
        await session.commit()
    await callback.answer("🚫 Global ban qo‘yildi!" if ban else "🔓 Bandan chiqarildi!", show_alert=True)
    await _show_admin_users_list(callback, callback_data.extra or ("active" if ban else "banned"), callback_data.page)


async def _render_admin_user(target, admin_tg_id: int, target_user_id: int) -> None:
    async with get_session() as session:
        user = await session.get(User, target_user_id)
        if user is None:
            await edit_or_answer(target, "❌ Foydalanuvchi topilmadi.", reply_markup=admin_users_menu_kb(admin_tg_id))
            return
        groups = await AdminService.get_user_groups(session, user.id)

    ban_str = "🚫 BANNED" if user.is_banned else "🟢 Active"
    groups_str = ", ".join(esc(g) for g in groups[:5]) if groups else "—"
    text = (
        f"👤 <b>USER CONTROL</b>\n\n"
        f"🆔 Telegram ID: <code>{user.telegram_id}</code>\n"
        f"👤 Username: {esc(user.display_name)}\n"
        f"🪙 Coins: <b>{fmt_coins(user.coins)}</b>\n"
        f"🎴 Total cards: <b>{user.total_cards}</b>\n"
        f"✨ Unique cards: <b>{user.unique_cards}</b>\n"
        f"🔥 Daily streak: <b>{user.daily_streak}</b>\n"
        f"👥 Groups: {groups_str}\n"
        f"📌 Ban status: <b>{ban_str}</b>\n"
        f"🕒 Last activity: <code>{user.last_activity.strftime('%Y-%m-%d %H:%M UTC')}</code>"
    )
    await edit_or_answer(target, text, reply_markup=admin_user_detail_kb(admin_tg_id, user))


@router.callback_query(AdminCb.filter((F.section == "users") & (F.action == "detail")))
async def cb_admin_user_detail(callback: CallbackQuery, callback_data: AdminCb) -> None:
    await _render_admin_user(callback, callback.from_user.id, callback_data.target_id)


@router.callback_query(AdminCb.filter((F.section == "users") & (F.action == "search_prompt")))
async def cb_admin_user_search(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.user_search)
    await edit_or_answer(callback, "👤 Foydalanuvchi Telegram ID yoki @username ni yozing:")


@router.message(AdminStates.user_search, NotCommandFilter())
async def fsm_admin_user_search(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with get_session() as session:
        user = await UserService.find_user(session, message.text or "")
    if user is None:
        await message.answer("❌ Foydalanuvchi topilmadi.", reply_markup=admin_users_menu_kb(message.from_user.id))
        return
    await _render_admin_user(message, message.from_user.id, user.id)


@router.callback_query(AdminCb.filter((F.section == "users") & (F.action.in_({"add_coins", "sub_coins"}))))
async def cb_admin_coin_prompt(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    await state.set_state(AdminStates.user_coin_delta)
    await state.update_data(target_user_id=callback_data.target_id, sign=1 if callback_data.action == "add_coins" else -1)
    await edit_or_answer(callback, "🪙 Coin miqdorini kiriting (musbat son):")


@router.message(AdminStates.user_coin_delta, NotCommandFilter())
async def fsm_admin_coin_delta(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("⚠️ Musbat butun son kiriting.")
        return
    data = await state.get_data()
    await state.clear()
    delta = int(raw) * int(data["sign"])
    uid = int(data["target_user_id"])

    async with get_session() as session:
        ok, msg_text, _ = await AdminService.adjust_user_coins(session, message.from_user.id, uid, delta)
        if ok:
            await session.commit()
        else:
            await session.rollback()

    await message.answer(msg_text)
    await _render_admin_user(message, message.from_user.id, uid)


@router.callback_query(AdminCb.filter((F.section == "users") & (F.action == "give_pick")))
async def cb_admin_give_card_pick(callback: CallbackQuery, callback_data: AdminCb) -> None:
    async with get_session() as session:
        cards, total = await AdminService.filter_cards(
            session, filter_mode="all", page=callback_data.page, page_size=6
        )
    await edit_or_answer(
        callback,
        "➕ <b>FOYDALANUVCHIGA KARTA BERISH</b>\n\nBeriladigan kartani tanlang:",
        reply_markup=admin_pick_card_for_user_kb(
            callback.from_user.id, callback_data.target_id, cards, callback_data.page, total, page_size=6
        ),
    )


@router.callback_query(AdminCb.filter((F.section == "users") & (F.action == "give_exec")))
async def cb_admin_give_card_exec(callback: CallbackQuery, callback_data: AdminCb) -> None:
    card_id = int(callback_data.extra) if callback_data.extra.isdigit() else 0
    async with get_session() as session:
        card = await AdminService.give_card_to_user(
            session, callback.from_user.id, callback_data.target_id, card_id
        )
        await session.commit()
    if card:
        await callback.answer(f"✅ {card.character_name} berildi!", show_alert=True)
    else:
        await callback.answer("❌ Berilmadi: karta topilmadi yoki userda bu karta allaqachon bor.", show_alert=True)
    await _render_admin_user(callback, callback.from_user.id, callback_data.target_id)


@router.callback_query(AdminCb.filter((F.section == "users") & (F.action == "rem_pick")))
async def cb_admin_rem_card_pick(callback: CallbackQuery, callback_data: AdminCb) -> None:
    async with get_session() as session:
        items = await AdminService.get_user_cards_for_admin(session, callback_data.target_id)
    text = "➖ <b>FOYDALANUVCHIDAN KARTA OLISH</b>\n\nOlib qo‘yiladigan kartani tanlang:" if items else "📭 Bu foydalanuvchida bo‘sh karta yo‘q."
    await edit_or_answer(
        callback,
        text,
        reply_markup=admin_user_cards_remove_kb(callback.from_user.id, callback_data.target_id, items),
    )


@router.callback_query(AdminCb.filter((F.section == "users") & (F.action == "rem_exec")))
async def cb_admin_rem_card_exec(callback: CallbackQuery, callback_data: AdminCb) -> None:
    card_id = int(callback_data.extra) if callback_data.extra.isdigit() else 0
    async with get_session() as session:
        ok = await AdminService.remove_card_from_user(
            session, callback.from_user.id, callback_data.target_id, card_id
        )
        if ok:
            await session.commit()
        else:
            await session.rollback()
    await callback.answer("✅ Karta olib tashlandi!" if ok else "❌ Bo‘sh nusxa topilmadi.", show_alert=True)
    await _render_admin_user(callback, callback.from_user.id, callback_data.target_id)


@router.callback_query(AdminCb.filter(F.section == "ban"))
async def cb_admin_ban_toggle(callback: CallbackQuery, callback_data: AdminCb) -> None:
    ban = callback_data.action == "ban"
    async with get_session() as session:
        await AdminService.set_global_ban(
            session, callback.from_user.id, callback_data.target_id, ban=ban
        )
        await session.commit()
    await callback.answer("🚫 Global ban qo‘yildi!" if ban else "🔓 Ban olib tashlandi.", show_alert=True)
    await _render_admin_user(callback, callback.from_user.id, callback_data.target_id)


# ---------------------------------------------------------------------------
# BOT SETTINGS, EVENTS & BROADCAST
# ---------------------------------------------------------------------------
@router.callback_query(AdminCb.filter(F.section == "settings"))
async def cb_admin_settings(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    if callback_data.action == "rank_menu":
        await state.clear()
        await _render_rank_menu(callback)
        return
    if callback_data.action in ("rank_value", "rank_drops"):
        if callback_data.extra not in list_rarities():
            await callback.answer("Noma’lum rank.", show_alert=True)
            return
        is_value = callback_data.action == "rank_value"
        await state.set_state(AdminStates.edit_rank_value if is_value else AdminStates.edit_rank_drops)
        await state.update_data(rarity=callback_data.extra)
        await edit_or_answer(
            callback,
            (
                f"💰 <b>{esc(callback_data.extra)}</b> ranki uchun yangi <b>karta narxini</b> (coin) kiriting:"
                if is_value
                else f"🔁 <b>{esc(callback_data.extra)}</b> ranki uchun yangi <b>tashlash sonini</b> (1–1000) kiriting:"
            )
            + "\n<i>Bu qiymat keyin qo‘shiladigan kartalarga qo‘llanadi. Bekor qilish: /cancel</i>",
        )
        return
    async with get_session() as session:
        if callback_data.action == "set_rank":
            if callback_data.extra in {m.value for m in RankingMode}:
                await SettingsService.set_ranking_mode(session, callback_data.extra)
                await session.commit()
                RankingService.invalidate_cache()
        elif callback_data.action == "edit_reward":
            await state.set_state(AdminStates.edit_rarity_reward)
            await state.update_data(rarity=callback_data.extra)
            await edit_or_answer(
                callback,
                f"🪙 <b>{esc(callback_data.extra)}</b> karta tushganda beriladigan yangi coin miqdorini kiriting:",
            )
            return

        rewards = await SettingsService.get_rarity_rewards(session)
        rank_mode = await SettingsService.get_ranking_mode(session)

    lines = ["🔧 <b>BOT SOZLAMALARI</b>\n\n🪙 <b>Rarity Coin Rewards:</b>"]
    for r, val in rewards.items():
        lines.append(f"• {r}: <b>{val:,} 🪙</b>")
    lines.append(f"\n🏆 <b>Ranking Mode:</b> <code>{esc(rank_mode)}</code>")
    await edit_or_answer(
        callback,
        "\n".join(lines),
        reply_markup=admin_settings_kb(callback.from_user.id, rank_mode),
    )


async def _rank_menu_text_and_kb(owner_id: int):
    async with get_session() as session:
        cfg = await SettingsService.get_rarity_config(session)
    lines = [
        "🎖 <b>RANK SOZLAMALARI</b>\n",
        "Karta qo‘shilganda narx va tashlash soni rank bo‘yicha <b>avtomatik</b> qo‘yiladi:\n",
    ]
    builder = InlineKeyboardBuilder()
    for r in list_rarities():
        meta = get_rarity_meta(r)
        lines.append(f"{meta.emoji} <b>{esc(r)}</b> — {cfg[r]['value']:,} coin · {cfg[r]['drops']} ta tashlanadi")
        builder.row(
            InlineKeyboardButton(
                text=f"💰 {r} narxi",
                callback_data=AdminCb(owner_id=owner_id, section="settings", action="rank_value", extra=r).pack(),
            ),
            InlineKeyboardButton(
                text=f"🔁 {r} tashlash",
                callback_data=AdminCb(owner_id=owner_id, section="settings", action="rank_drops", extra=r).pack(),
            ),
        )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA", callback_data=AdminCb(owner_id=owner_id, section="settings").pack()
        )
    )
    return "\n".join(lines), builder.as_markup()


async def _render_rank_menu(callback: CallbackQuery) -> None:
    text, kb = await _rank_menu_text_and_kb(callback.from_user.id)
    await edit_or_answer(callback, text, reply_markup=kb)


async def _save_rank_value(message: Message, state: FSMContext, field: str) -> None:
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit():
        await message.answer("⚠️ Faqat musbat butun son kiriting.")
        return
    data = await state.get_data()
    rarity = data.get("rarity")
    try:
        async with get_session() as session:
            await SettingsService.set_rarity_config(
                session, rarity, **({"value": int(raw)} if field == "value" else {"drops": int(raw)})
            )
            await AdminService.log_action(
                session, message.from_user.id, "rank_config", "settings", str(rarity), {field: int(raw)}
            )
            await session.commit()
    except ValueError as exc:
        await message.answer(f"⚠️ {esc(str(exc))}")
        return
    await state.clear()
    text, kb = await _rank_menu_text_and_kb(message.from_user.id)
    await message.answer("✅ Saqlandi!\n\n" + text, reply_markup=kb)


@router.message(AdminStates.edit_rank_value, NotCommandFilter())
async def fsm_edit_rank_value(message: Message, state: FSMContext) -> None:
    await _save_rank_value(message, state, "value")


@router.message(AdminStates.edit_rank_drops, NotCommandFilter())
async def fsm_edit_rank_drops(message: Message, state: FSMContext) -> None:
    await _save_rank_value(message, state, "drops")


@router.message(AdminStates.edit_rarity_reward, NotCommandFilter())
async def fsm_edit_rarity_reward(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("⚠️ Faqat raqam kiriting.")
        return
    data = await state.get_data()
    rarity = data["rarity"]
    await state.clear()
    async with get_session() as session:
        await SettingsService.set_rarity_reward(session, rarity, int(raw))
        await session.commit()
    await message.answer(
        f"✅ {rarity} reward {int(raw):,} coins ga o‘zgartirildi!",
        reply_markup=admin_home_kb(message.from_user.id),
    )


@router.callback_query(AdminCb.filter(F.section == "events"))
async def cb_admin_events(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    if callback_data.action in ("view", ""):
        await state.clear()
        await _render_evt_menu(callback)
        return

    if callback_data.action == "create_prompt":
        await state.set_state(AdminStates.event_create)
        await edit_or_answer(
            callback,
            "🎯 <b>YANGI EVENT YARATISH</b>\n\n"
            "Formatda yuboring:\n"
            "<code>Nomi | Soat | CoinMultiplier | BoostRarity | RarityMult</code>\n\n"
            "Misol:\n"
            "<code>GOJO EVENT | 24 | 2.0 | Legendary | 2.0</code>",
        )
        return

    if callback_data.action == "stop":
        async with get_session() as session:
            ev = await session.get(Event, callback_data.target_id)
            if ev is not None:
                ev.is_active = False
                await AdminService.log_action(
                    session, callback.from_user.id, "stop_event", "event", str(ev.id)
                )
                await session.commit()
        await callback.answer("⏹ Event to‘xtatildi.")

    async with get_session() as session:
        events = list((await session.execute(select(Event).order_by(Event.id.desc()).limit(5))).scalars().all())
    lines = ["🎯 <b>EVENTS</b>\n"]
    if not events:
        lines.append("Hozircha eventlar yo‘q.")
    action_rows = [
        [
            InlineKeyboardButton(
                text="➕ YANGI EVENT YARATISH",
                callback_data=AdminCb(
                    owner_id=callback.from_user.id, section="events", action="create_prompt"
                ).pack(),
            )
        ]
    ]
    for ev in events:
        status_icon = "🟢" if ev.is_active else "⚪"
        lines.append(
            f"{status_icon} <b>{esc(ev.name)}</b> — {esc(ev.description)} "
            f"(gacha: <code>{ev.ends_at.strftime('%Y-%m-%d %H:%M UTC')}</code>)"
        )
        if ev.is_active:
            action_rows.append(
                [
                    InlineKeyboardButton(
                        text=f"⏹ To‘xtatish: {ev.name[:24]}",
                        callback_data=AdminCb(
                            owner_id=callback.from_user.id,
                            section="events",
                            action="stop",
                            target_id=ev.id,
                        ).pack(),
                    )
                ]
            )
    action_rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ ORQAGA",
                callback_data=AdminCb(owner_id=callback.from_user.id, section="home").pack(),
            )
        ]
    )
    await edit_or_answer(
        callback,
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=action_rows),
    )


@router.message(AdminStates.event_create, NotCommandFilter())
async def fsm_event_create(message: Message, state: FSMContext) -> None:
    parts = [p.strip() for p in (message.text or "").split("|")]
    if len(parts) < 3:
        await message.answer("⚠️ Format noto‘g‘ri. Misol: <code>GOJO EVENT | 24 | 2.0 | Legendary | 2.0</code>")
        return
    name = parts[0]
    try:
        hours = int(parts[1])
        coin_mult = float(parts[2])
        boosted_rarity = parts[3] if len(parts) > 3 and parts[3] in list_rarities() else None
        rarity_mult = float(parts[4]) if len(parts) > 4 else 1.0
    except ValueError:
        await message.answer("⚠️ Soat va multiplikatorlar son bo‘lishi kerak. Misol: <code>GOJO EVENT | 24 | 2.0 | Legendary | 2.0</code>")
        return

    await state.clear()
    async with get_session() as session:
        ev = await AdminService.create_event(
            session=session,
            admin_telegram_id=message.from_user.id,
            name=name,
            duration_hours=hours,
            coin_multiplier=coin_mult,
            boosted_rarity=boosted_rarity,
            rarity_weight_multiplier=rarity_mult,
        )
        await session.commit()

    await message.answer(
        f"✅ Event yaratildi: <b>{esc(ev.name)}</b> ({hours} soat)",
        reply_markup=admin_home_kb(message.from_user.id),
    )


@router.callback_query(AdminCb.filter(F.section == "broadcast"))
async def cb_admin_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.broadcast_text)
    await edit_or_answer(callback, "📢 Barcha faol guruhlar va foydalanuvchilarga yuboriladigan xabar matnini yozing:")


@router.message(AdminStates.broadcast_text, NotCommandFilter())
async def fsm_admin_broadcast(message: Message, state: FSMContext) -> None:
    text = message.text or ""
    await state.clear()
    if not text:
        return

    async with get_session() as session:
        group_ids = list((await session.execute(select(Group.telegram_chat_id).where(Group.is_active.is_(True)))).scalars().all())
        user_ids = list((await session.execute(select(User.telegram_id).where(User.is_banned.is_(False)))).scalars().all())

    sent = 0
    for chat_id in set(group_ids + user_ids):
        try:
            await message.bot.send_message(chat_id=chat_id, text=f"📢 <b>E'LON</b>\n\n{esc(text)}")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            continue

    await message.answer(
        f"✅ Broadcast yakunlandi. Yuborildi: <b>{sent}</b> ta chatga.",
        reply_markup=admin_home_kb(message.from_user.id),
    )


# ---------------------------------------------------------------------------
# TELEGRAM STARS TARIFLARI (nechta Stars = nechta coin) — admin paneldan boshqariladi
# ---------------------------------------------------------------------------
STARS_INPUT_HELP = (
    "Ikkita raqamni bo‘sh joy bilan yozing: <code>Stars Coin</code>\n"
    "Misol: <code>250 30000</code> — 250 ⭐ evaziga 30,000 coin.\n"
    "<i>Bekor qilish: /cancel</i>"
)


def _parse_stars_coins(text: str):
    nums = re.findall(r"\d+", (text or "").replace(",", " "))
    if len(nums) != 2:
        return None
    return int(nums[0]), int(nums[1])


def _stars_list_text(packages) -> str:
    lines = [
        "⭐ <b>STARS TARIFLARI</b>\n",
        "Foydalanuvchilar /stars orqali shu paketlarni ko‘radi va sotib oladi.\n",
    ]
    for p in packages:
        lines.append(f"• <b>{p['stars']:,}</b> ⭐ → <b>{p['coins']:,}</b> 🪙")
    if not packages:
        lines.append("📭 Paketlar yo‘q. ➕ tugmasi bilan yangi tarif qo‘shing.")
    lines.append("\n✏️ — tahrirlash, 🗑 — o‘chirish. Narx o‘zgarishi yangi buyurtmalarga darhol qo‘llanadi.")
    return "\n".join(lines)


@router.callback_query(AdminCb.filter(F.section == "stars"))
async def cb_admin_stars(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    action = callback_data.action
    admin_id = callback.from_user.id

    if action == "add":
        await state.set_state(AdminStates.stars_add)
        await edit_or_answer(
            callback,
            f"➕ <b>YANGI STARS TARIF</b>\n\n{STARS_INPUT_HELP}",
            reply_markup=admin_stars_back_kb(admin_id),
        )
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
            f"✏️ <b>TARIFNI TAHRIRLASH</b>\n\nHozirgi: <b>{pkg['stars']:,}</b> ⭐ → <b>{pkg['coins']:,}</b> 🪙\n\n"
            f"Yangi qiymatni yozing.\n{STARS_INPUT_HELP}",
            reply_markup=admin_stars_back_kb(admin_id),
        )
        return

    if action == "del":
        await state.clear()
        await edit_or_answer(
            callback,
            "🗑 Ushbu tarifni o‘chirishni tasdiqlaysizmi?\n<i>Allaqachon yaratilgan buyurtmalarga ta’sir qilmaydi.</i>",
            reply_markup=admin_stars_confirm_kb(admin_id, callback_data.extra),
        )
        return

    await state.clear()
    async with get_session() as session:
        if action == "del_ok":
            if await SettingsService.delete_stars_package(session, callback_data.extra):
                await AdminService.log_action(
                    session, admin_id, "stars_package_delete", "settings", callback_data.extra
                )
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
            await AdminService.log_action(
                session,
                message.from_user.id,
                "stars_package_edit" if package_id else "stars_package_add",
                "settings",
                pkg["id"],
                {"stars": stars, "coins": coins},
            )
            await session.commit()
            packages = await SettingsService.get_stars_packages(session)
    except ValueError as exc:
        await message.answer(f"⚠️ {esc(str(exc))}")
        return

    await state.clear()
    await message.answer(
        f"✅ Saqlandi: <b>{pkg['stars']:,}</b> ⭐ → <b>{pkg['coins']:,}</b> 🪙\n\n{_stars_list_text(packages)}",
        reply_markup=admin_stars_kb(message.from_user.id, packages),
    )


@router.message(AdminStates.stars_add, NotCommandFilter())
async def fsm_stars_add(message: Message, state: FSMContext) -> None:
    await _save_stars_from_message(message, state, None)


@router.message(AdminStates.stars_edit, NotCommandFilter())
async def fsm_stars_edit(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    package_id = data.get("package_id")
    if not package_id:
        await state.clear()
        await message.answer("⚠️ Tarif topilmadi, qaytadan urinib ko‘ring.")
        return
    await _save_stars_from_message(message, state, str(package_id))


# ---------------------------------------------------------------------------
# LIMITLI KARTA EVENTI: hali tashlanmagan karta -> nusxa soni -> narx (coin/Stars) -> e'lon
# ---------------------------------------------------------------------------
_BG_TASKS: set = set()
_EVT_STATUS = {"active": "🟢 Faol", "sold_out": "🔥 Sotilib bo‘ldi", "ended": "⌛ Tugagan", "cancelled": "⛔ To‘xtatilgan"}


def _local_dt(dt):
    from models.base import ensure_utc

    return ensure_utc(dt).astimezone(ZoneInfo(get_settings().timezone))


async def _render_evt_menu(callback: CallbackQuery) -> None:
    async with get_session() as session:
        await EventService.close_expired(session)
        await session.commit()
        events = await EventService.get_recent_events(session, limit=8)
    lines = ["🎯 <b>EVENTLAR — LIMITLI KARTALAR</b>\n"]
    if not events:
        lines.append("Hozircha eventlar yo‘q. ➕ tugmasi bilan yangi event boshlang.")
    for ev, card in events:
        lines.append(
            f"{_EVT_STATUS.get(ev.status, ev.status)} <b>#{ev.id} {esc(card.character_name)}</b> "
            f"— {ev.sold_count}/{ev.total_copies} · tugash: {_local_dt(ev.ends_at):%d.%m %H:%M}"
        )
    await edit_or_answer(callback, "\n".join(lines), reply_markup=admin_evt_menu_kb(callback.from_user.id, events))


def _evt_summary(data: dict) -> str:
    prices = []
    if data.get("price_coins"):
        prices.append(f"🪙 <b>{int(data['price_coins']):,}</b> coin")
    if data.get("price_stars"):
        prices.append(f"⭐ <b>{int(data['price_stars']):,}</b> Stars")
    meta = get_rarity_meta(data.get("rarity", "Common"))
    return (
        "🎯 <b>EVENTNI TASDIQLASH</b>\n\n"
        f"🎴 Karta: <b>{esc(data.get('card_name', ''))}</b> ({meta.emoji} {esc(data.get('rarity', ''))})\n"
        f"📺 Anime: {esc(data.get('anime_name', ''))}\n"
        f"📦 Nusxa soni: <b>{data.get('copies')}</b> ta\n"
        f"💳 Narx: {'  yoki  '.join(prices)}\n"
        "⏳ Davomiyligi: <b>24 soat</b>\n\n"
        "Karta guruhlarga <b>tashlanmaydi</b>, faqat event orqali olinadi. "
        "Event boshlanganda barcha faol guruhlarga e’lon yuboriladi."
    )


async def _show_stars_choice(target, state: FSMContext, owner_id: int) -> None:
    async with get_session() as session:
        packages = await SettingsService.get_stars_packages(session)
    if not packages:
        await edit_or_answer(
            target,
            "⚠️ Stars tariflari yo‘q. Avval Admin → ⭐ STARS TARIFLAR bo‘limida tarif qo‘shing.",
        ) if isinstance(target, CallbackQuery) else await target.answer(
            "⚠️ Stars tariflari yo‘q. Avval Admin → ⭐ STARS TARIFLAR bo‘limida tarif qo‘shing."
        )
        return
    text = (
        "⭐ <b>Stars narxini tanlang</b>\n\nTarifdagi Stars miqdori karta narxi bo‘ladi "
        "(foydalanuvchi shuncha Stars to‘laydi va <b>coin emas, kartaning o‘zini</b> oladi):"
    )
    kb = admin_evt_stars_kb(owner_id, packages)
    if isinstance(target, CallbackQuery):
        await edit_or_answer(target, text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


@router.callback_query(AdminCb.filter(F.section == "evt"))
async def cb_admin_evt(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    action = callback_data.action
    admin_id = callback.from_user.id

    if action == "menu":
        await state.clear()
        await _render_evt_menu(callback)
        return

    if action == "pick":
        await state.clear()
        async with get_session() as session:
            cards, total = await EventService.list_candidates(session, page=callback_data.page, page_size=6)
        if total == 0:
            text = (
                "🎯 <b>YANGI EVENT</b>\n\n📭 Mos karta yo‘q. Event kartasi hali guruhga tashlanmagan va "
                "hech kimda bo‘lmagan bo‘lishi kerak. Avval yangi karta qo‘shing."
            )
        else:
            text = (
                f"🎯 <b>YANGI EVENT</b>\n\nGuruhga hali tashlanmagan va hech kimda yo‘q kartalar (<b>{total}</b> ta). "
                "Eventga qo‘yiladigan kartani tanlang:"
            )
        await edit_or_answer(
            callback, text, reply_markup=admin_evt_candidates_kb(admin_id, cards, callback_data.page, total, 6)
        )
        return

    if action == "card":
        async with get_session() as session:
            cards_ok = await EventService.list_candidates(session, page=1, page_size=1000)
            card = next((c for c in cards_ok[0] if c.id == callback_data.target_id), None)
        if card is None:
            await callback.answer("Bu karta endi mos emas (tashlangan yoki kimdadir bor).", show_alert=True)
            return
        await state.set_state(AdminStates.evt_copies)
        await state.update_data(
            card_id=card.id, card_name=card.character_name, anime_name=card.anime_name, rarity=card.rarity
        )
        await edit_or_answer(
            callback,
            f"🎴 <b>{esc(card.character_name)}</b> ({esc(card.rarity)})\n\n"
            "📦 Necha nusxa sotiladi? Sonni kiriting (masalan: <code>10</code>):\n<i>Bekor qilish: /cancel</i>",
        )
        return

    if action == "ptype":
        data = await state.get_data()
        if "card_id" not in data or "copies" not in data:
            await callback.answer("Jarayon eskirgan. Qaytadan boshlang.", show_alert=True)
            return
        mode = callback_data.extra
        await state.update_data(mode=mode, price_coins=None, price_stars=None)
        if mode in ("coins", "both"):
            await state.set_state(AdminStates.evt_price_coins)
            await edit_or_answer(
                callback, "🪙 Karta narxini <b>coin</b>da kiriting (masalan: <code>5000</code>):\n<i>Bekor qilish: /cancel</i>"
            )
        else:
            await _show_stars_choice(callback, state, admin_id)
        return

    if action == "pstars":
        async with get_session() as session:
            packages = await SettingsService.get_stars_packages(session)
        pkg = next((p for p in packages if p["id"] == callback_data.extra), None)
        if pkg is None:
            await callback.answer("Tarif topilmadi.", show_alert=True)
            return
        await state.update_data(price_stars=int(pkg["stars"]))
        data = await state.get_data()
        await edit_or_answer(callback, _evt_summary(data), reply_markup=admin_evt_confirm_kb(admin_id))
        return

    if action == "go":
        data = await state.get_data()
        if not all(k in data for k in ("card_id", "copies")):
            await callback.answer("Jarayon eskirgan. Qaytadan boshlang.", show_alert=True)
            return
        async with get_session() as session:
            res = await EventService.create_event(
                session=session,
                admin_telegram_id=admin_id,
                card_id=int(data["card_id"]),
                total_copies=int(data["copies"]),
                price_coins=data.get("price_coins"),
                price_stars=data.get("price_stars"),
            )
            if res.success:
                await AdminService.log_action(
                    session, admin_id, "create_limited_event", "event", str(res.event.id),
                    {"card_id": data["card_id"], "copies": data["copies"]},
                )
                await session.commit()
            else:
                await session.rollback()
        if not res.success:
            await callback.answer(res.message, show_alert=True)
            return
        await state.clear()
        task = asyncio.create_task(announce_event(callback.bot, res.event.id, notify_admin_id=admin_id))
        _BG_TASKS.add(task)
        task.add_done_callback(_BG_TASKS.discard)
        await callback.answer("✅ Event boshlandi!", show_alert=True)
        await edit_or_answer(
            callback,
            f"✅ <b>Event #{res.event.id} boshlandi!</b>\n\n🎴 {esc(res.card.character_name)} · "
            f"{res.event.total_copies} nusxa · 24 soat\n📢 E’lon barcha faol guruhlarga yuborilmoqda...",
            reply_markup=admin_evt_menu_kb(admin_id, []),
        )
        return

    if action in ("detail", "stop"):
        async with get_session() as session:
            if action == "stop":
                if await EventService.stop_event(session, callback_data.target_id):
                    await AdminService.log_action(session, admin_id, "stop_limited_event", "event", str(callback_data.target_id))
                await session.commit()
            pair = await EventService.get_event_with_card(session, callback_data.target_id)
        if pair is None:
            await callback.answer("Event topilmadi.", show_alert=True)
            return
        ev, card = pair
        if action == "stop":
            from app.event_announcer import finalize_ended_events

            task = asyncio.create_task(finalize_ended_events(callback.bot))
            _BG_TASKS.add(task)
            task.add_done_callback(_BG_TASKS.discard)
        prices = []
        if ev.price_coins:
            prices.append(f"🪙 {ev.price_coins:,}")
        if ev.price_stars:
            prices.append(f"⭐ {ev.price_stars:,}")
        await edit_or_answer(
            callback,
            f"🎯 <b>Event #{ev.id}</b> — {_EVT_STATUS.get(ev.status, ev.status)}\n\n"
            f"🎴 {esc(card.character_name)} ({esc(card.rarity)}) · {esc(card.anime_name)}\n"
            f"📦 Sotilgan: <b>{ev.sold_count}/{ev.total_copies}</b>\n"
            f"💳 Narx: {'  /  '.join(prices)}\n"
            f"⏳ Boshlangan: {_local_dt(ev.starts_at):%d.%m %H:%M} · Tugash: {_local_dt(ev.ends_at):%d.%m %H:%M}",
            reply_markup=admin_evt_detail_kb(admin_id, ev),
        )
        return


@router.message(AdminStates.evt_copies, NotCommandFilter())
async def fsm_evt_copies(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit() or not (1 <= int(raw) <= 100_000):
        await message.answer("⚠️ 1 dan 100000 gacha butun son kiriting.")
        return
    await state.update_data(copies=int(raw))
    await state.set_state(None)
    await message.answer(
        "💳 Karta qanday to‘lov bilan sotiladi?", reply_markup=admin_evt_price_type_kb(message.from_user.id)
    )


@router.message(AdminStates.evt_price_coins, NotCommandFilter())
async def fsm_evt_price_coins(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit() or not (1 <= int(raw) <= 1_000_000_000):
        await message.answer("⚠️ Narx musbat butun son bo‘lishi kerak.")
        return
    await state.update_data(price_coins=int(raw))
    await state.set_state(None)
    data = await state.get_data()
    if data.get("mode") == "both":
        await _show_stars_choice(message, state, message.from_user.id)
        return
    await message.answer(_evt_summary(data), reply_markup=admin_evt_confirm_kb(message.from_user.id))

