import asyncio
import logging
from typing import Optional
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramMigrateToChat
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, func, select

from admin.filters import IsAdminFilter
from config.settings import get_settings
from database.session import get_session
from keyboards.admin import (
    admin_card_detail_kb,
    admin_cards_list_kb,
    admin_cards_menu_kb,
    admin_group_settings_kb,
    admin_groups_list_kb,
    admin_home_kb,
    admin_pick_card_for_user_kb,
    admin_pick_group_for_card_kb,
    admin_schedule_detail_kb,
    admin_settings_kb,
    admin_user_cards_remove_kb,
    admin_user_detail_kb,
    admin_users_list_kb,
    admin_users_menu_kb,
)
from keyboards.main_menu import drop_claim_kb
from models.base import ensure_utc
from models.card import Card
from models.event import Event
from models.group import Group
from models.schedule import ActiveDrop, DropSchedule
from models.user import User
from services.admin_service import AdminService
from services.card_service import CardService
from services.drop_service import DropService
from services.settings_service import SettingsService
from services.user_service import UserService
from utils.callback_data import AdminCb, NavCb
from utils.constants import PAGE_SIZE, get_rarity_meta, list_rarities
from utils.formatting import (
    esc,
    fmt_coins,
    format_card_block,
    format_drop_message,
    format_group_settings,
)
from utils.tg_helpers import edit_or_answer, send_card_visual

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


def _admin_cancel_kb(owner_id: int, section: str = "home", action: str = "view") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Bekor qilish",
                    callback_data=AdminCb(owner_id=owner_id, section=section, action=action).pack(),
                )
            ]
        ]
    )


@router.message(Command("cancel"))
async def cmd_admin_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ Jarayon bekor qilindi.", reply_markup=admin_home_kb(message.from_user.id))


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
        card = await CardService.pick_random_card(session, consume_priority=True)
        if group is None or card is None:
            await message.answer("❌ Hozircha tashlash uchun karta mavjud emas.")
            return
        drop = await DropService.create_active_drop(session, group, card)
        active_event = await CardService.get_active_event(session)
        await session.commit()

    caption = format_drop_message(card, active_event=active_event)
    try:
        try:
            sent_msg = await send_card_visual(
                bot=message.bot,
                chat_id=group.telegram_chat_id,
                image=card.image,
                caption=caption,
                reply_markup=drop_claim_kb(drop.id),
            )
        except TelegramMigrateToChat as mig:
            async with get_session() as s_mig:
                g_mig = await s_mig.get(Group, group.id)
                d_mig = await s_mig.get(ActiveDrop, drop.id)
                if g_mig is not None:
                    g_mig.telegram_chat_id = mig.migrate_to_chat_id
                if d_mig is not None:
                    d_mig.telegram_chat_id = mig.migrate_to_chat_id
                await s_mig.commit()
            sent_msg = await send_card_visual(
                bot=message.bot,
                chat_id=mig.migrate_to_chat_id,
                image=card.image,
                caption=caption,
                reply_markup=drop_claim_kb(drop.id),
            )
    except Exception as exc:
        logger.warning("Admin /drop failed in group %s: %s", group.telegram_chat_id, exc)
        async with get_session() as s_err:
            await s_err.execute(delete(ActiveDrop).where(ActiveDrop.id == drop.id, ActiveDrop.is_claimed.is_(False)))
            await s_err.commit()
        await message.answer("❌ Guruhga karta yuborib bo‘lmadi (botga xabar yuborish ruxsati berilganini tekshiring).")
        return

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


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action.in_({"list", "del_list"}))))
async def cb_admin_cards_list(callback: CallbackQuery, callback_data: AdminCb) -> None:
    await _show_admin_cards(
        callback,
        filter_mode=callback_data.extra or "all",
        page=callback_data.page,
        mode=callback_data.action,
    )


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "detail")))
async def cb_admin_card_detail(callback: CallbackQuery, callback_data: AdminCb) -> None:
    async with get_session() as session:
        card = await session.get(Card, callback_data.target_id)
        if card is None:
            await callback.answer("Karta topilmadi.", show_alert=True)
            return
        owners_list, drops_sent = await AdminService.get_card_ownership_and_drops(session, card.id)

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
    )

    if callback.message and card.image and (card.image.startswith("http") or len(card.image) > 20):
        try:
            safe_cap = text if len(text) <= 1024 else text[:1020] + "..."
            await callback.message.answer_photo(photo=card.image, caption=safe_cap, reply_markup=kb)
            try:
                await callback.message.delete()
            except Exception:
                pass
            try:
                await callback.answer()
            except Exception:
                pass
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
        drop = await DropService.create_active_drop(session, group, card)
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
        try:
            sent_msg = await send_card_visual(
                bot=callback.bot,
                chat_id=group.telegram_chat_id,
                image=card.image,
                caption=caption,
                reply_markup=drop_claim_kb(drop.id),
            )
        except TelegramMigrateToChat as mig:
            async with get_session() as s_mig:
                g_mig = await s_mig.get(Group, group.id)
                d_mig = await s_mig.get(ActiveDrop, drop.id)
                if g_mig is not None:
                    g_mig.telegram_chat_id = mig.migrate_to_chat_id
                if d_mig is not None:
                    d_mig.telegram_chat_id = mig.migrate_to_chat_id
                await s_mig.commit()
            sent_msg = await send_card_visual(
                bot=callback.bot,
                chat_id=mig.migrate_to_chat_id,
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
        async with get_session() as s_err:
            await s_err.execute(delete(ActiveDrop).where(ActiveDrop.id == drop.id, ActiveDrop.is_claimed.is_(False)))
            await s_err.commit()
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


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "add_start")))
async def cb_admin_add_card_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.add_card_character)
    await edit_or_answer(
        callback,
        "➕ <b>YANGI KARTA QO‘SHISH</b>\n\n1/6: Character nomini yozing (masalan: <code>Gojo Satoru</code>):",
        reply_markup=_admin_cancel_kb(callback.from_user.id, "cards", "menu"),
    )


@router.message(AdminStates.add_card_character, F.text, ~F.text.startswith("/"))
async def fsm_add_card_char(message: Message, state: FSMContext) -> None:
    await state.update_data(character_name=(message.text or "").strip())
    await state.set_state(AdminStates.add_card_anime)
    await message.answer("2/6: Anime nomini yozing (masalan: <code>Jujutsu Kaisen</code>):")


@router.message(AdminStates.add_card_anime, F.text, ~F.text.startswith("/"))
async def fsm_add_card_anime(message: Message, state: FSMContext) -> None:
    await state.update_data(anime_name=(message.text or "").strip())
    await state.set_state(AdminStates.add_card_image)
    await message.answer("3/6: Karta rasmini yuboring (Telegram rasm sifatida yoki HTTPS URL yozing):")


@router.message(AdminStates.add_card_image, F.photo | (F.text & ~F.text.startswith("/")))
async def fsm_add_card_image(message: Message, state: FSMContext) -> None:
    img = ""
    if message.photo:
        img = message.photo[-1].file_id
    elif message.text:
        img = message.text.strip()
    if not img:
        await message.answer("⚠️ Rasm yuboring yoki URL kiriting.")
        return
    await state.update_data(image=img)
    await state.set_state(AdminStates.add_card_rarity)
    await message.answer(f"4/6: Rarity kiriting ({', '.join(list_rarities())}):")


@router.message(AdminStates.add_card_rarity, F.text, ~F.text.startswith("/"))
async def fsm_add_card_rarity(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().capitalize()
    if raw not in list_rarities():
        await message.answer(f"⚠️ Noto‘g‘ri rarity. Mavjudlar: {', '.join(list_rarities())}")
        return
    await state.update_data(rarity=raw)
    await state.set_state(AdminStates.add_card_value)
    default_val = get_rarity_meta(raw).default_base_value
    await message.answer(f"5/6: Base value (coins) kiriting (masalan: <code>{default_val}</code>):")


@router.message(AdminStates.add_card_value, F.text, ~F.text.startswith("/"))
async def fsm_add_card_value(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("⚠️ Faqat raqam kiriting.")
        return
    await state.update_data(base_value=int(raw))
    await state.set_state(AdminStates.add_card_weight)
    await message.answer("6/6: Drop weight (tushish ehtimoli vazni, masalan: <code>15</code>) kiriting:")


@router.message(AdminStates.add_card_weight, F.text, ~F.text.startswith("/"))
async def fsm_add_card_weight(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("⚠️ Faqat raqam kiriting.")
        return
    data = await state.get_data()
    await state.clear()

    async with get_session() as session:
        card = await CardService.create_card(
            session=session,
            character_name=data["character_name"],
            anime_name=data["anime_name"],
            image=data["image"],
            rarity=data["rarity"],
            base_value=data["base_value"],
            drop_weight=int(raw),
        )
        await AdminService.log_action(
            session,
            message.from_user.id,
            "create_card",
            "card",
            str(card.id),
            {"code": card.card_code, "character": card.character_name},
        )
        await session.commit()

    await message.answer(
        f"✅ Yangi karta qo‘shildi!\n\n{format_card_block(card, show_status=True)}",
        reply_markup=admin_cards_menu_kb(message.from_user.id),
    )


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "edit_image")))
async def cb_edit_card_image(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    await state.set_state(AdminStates.edit_card_image)
    await state.update_data(edit_card_id=callback_data.target_id)
    await edit_or_answer(
        callback,
        "🖼 Yangi rasm yuboring yoki HTTPS URL kiriting:",
        reply_markup=_admin_cancel_kb(callback.from_user.id, "cards", "menu"),
    )


@router.message(AdminStates.edit_card_image, F.photo | (F.text & ~F.text.startswith("/")))
async def fsm_edit_card_image(message: Message, state: FSMContext) -> None:
    img = message.photo[-1].file_id if message.photo else (message.text or "").strip()
    data = await state.get_data()
    await state.clear()
    async with get_session() as session:
        card = await session.get(Card, int(data["edit_card_id"]))
        if card:
            card.image = img[:512]
            await session.commit()
    await message.answer("✅ Karta rasmi yangilandi!", reply_markup=admin_cards_menu_kb(message.from_user.id))


@router.callback_query(AdminCb.filter((F.section == "cards") & (F.action == "edit_value")))
async def cb_edit_card_value(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    await state.set_state(AdminStates.edit_card_value)
    await state.update_data(edit_card_id=callback_data.target_id)
    await edit_or_answer(
        callback,
        "💰 Yangi base value (coins) raqamini kiriting:",
        reply_markup=_admin_cancel_kb(callback.from_user.id, "cards", "menu"),
    )


@router.message(AdminStates.edit_card_value, F.text, ~F.text.startswith("/"))
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
async def cb_schedule_overview(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
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
            t_str = ensure_utc(sched.scheduled_at).astimezone(tz).strftime("%H:%M")
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
            local_t = ensure_utc(sc.scheduled_at).astimezone(tz).strftime("%H:%M")
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
        reply_markup=_admin_cancel_kb(callback.from_user.id, "schedule", "overview"),
    )


@router.message(AdminStates.slot_priority_card, F.text, ~F.text.startswith("/"))
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
        & (F.action.in_({"group_view", "toggle_drop", "drop_inc", "drop_dec", "drop_now"}))
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
            if group.drop_enabled:
                await DropService.ensure_daily_schedule_for_group(session, group, force_regenerate=True)
            else:
                await session.execute(
                    delete(DropSchedule).where(DropSchedule.group_id == group.id, DropSchedule.status == "pending")
                )
        elif callback_data.action == "drop_inc":
            group.daily_drop_count = min(100, group.daily_drop_count + 1)
            if group.drop_enabled:
                await DropService.ensure_daily_schedule_for_group(session, group, force_regenerate=True)
        elif callback_data.action == "drop_dec":
            group.daily_drop_count = max(0, group.daily_drop_count - 1)
            if group.drop_enabled:
                await DropService.ensure_daily_schedule_for_group(session, group, force_regenerate=True)
        elif callback_data.action == "drop_now":
            card = await CardService.pick_random_card(session, consume_priority=True)
            if card is None:
                await callback.answer("Mavjud karta yo‘q!", show_alert=True)
                return
            drop = await DropService.create_active_drop(session, group, card)
            active_event = await CardService.get_active_event(session)
            await session.commit()
            caption = format_drop_message(card, active_event=active_event)
            try:
                try:
                    msg = await send_card_visual(
                        bot=callback.bot,
                        chat_id=group.telegram_chat_id,
                        image=card.image,
                        caption=caption,
                        reply_markup=drop_claim_kb(drop.id),
                    )
                except TelegramMigrateToChat as mig:
                    async with get_session() as s_mig:
                        g_mig = await s_mig.get(Group, group.id)
                        d_mig = await s_mig.get(ActiveDrop, drop.id)
                        if g_mig is not None:
                            g_mig.telegram_chat_id = mig.migrate_to_chat_id
                        if d_mig is not None:
                            d_mig.telegram_chat_id = mig.migrate_to_chat_id
                        await s_mig.commit()
                    msg = await send_card_visual(
                        bot=callback.bot,
                        chat_id=mig.migrate_to_chat_id,
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
            except Exception as exc:
                logger.warning("Admin drop_now failed in group %s: %s", group.telegram_chat_id, exc)
                async with get_session() as s_err:
                    await s_err.execute(
                        delete(ActiveDrop).where(ActiveDrop.id == drop.id, ActiveDrop.is_claimed.is_(False))
                    )
                    await s_err.commit()
                await callback.answer("❌ Guruhga yuborib bo‘lmadi (bot guruhdan chiqarilgan bo‘lishi mumkin).", show_alert=True)
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
        f"🕒 Last activity: <code>{ensure_utc(user.last_activity).strftime('%Y-%m-%d %H:%M UTC')}</code>"
    )
    await edit_or_answer(target, text, reply_markup=admin_user_detail_kb(admin_tg_id, user))


@router.callback_query(AdminCb.filter((F.section == "users") & (F.action == "detail")))
async def cb_admin_user_detail(callback: CallbackQuery, callback_data: AdminCb) -> None:
    await _render_admin_user(callback, callback.from_user.id, callback_data.target_id)


@router.callback_query(AdminCb.filter((F.section == "users") & (F.action == "search_prompt")))
async def cb_admin_user_search(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.user_search)
    await edit_or_answer(
        callback,
        "👤 Foydalanuvchi Telegram ID yoki @username ni yozing:",
        reply_markup=_admin_cancel_kb(callback.from_user.id, "users", "menu"),
    )


@router.message(AdminStates.user_search, F.text, ~F.text.startswith("/"))
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
    await edit_or_answer(
        callback,
        "🪙 Coin miqdorini kiriting (musbat son):",
        reply_markup=_admin_cancel_kb(callback.from_user.id, "users", "menu"),
    )


@router.message(AdminStates.user_coin_delta, F.text, ~F.text.startswith("/"))
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
    async with get_session() as session:
        if callback_data.action == "set_rank":
            await state.clear()
            await SettingsService.set_ranking_mode(session, callback_data.extra)
            await session.commit()
        elif callback_data.action == "edit_reward":
            await state.set_state(AdminStates.edit_rarity_reward)
            await state.update_data(rarity=callback_data.extra)
            await edit_or_answer(
                callback,
                f"🪙 <b>{esc(callback_data.extra)}</b> karta tushganda beriladigan yangi coin miqdorini kiriting:",
                reply_markup=_admin_cancel_kb(callback.from_user.id, "settings", "view"),
            )
            return
        else:
            await state.clear()

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


@router.message(AdminStates.edit_rarity_reward, F.text, ~F.text.startswith("/"))
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
    if callback_data.action == "create_prompt":
        await state.set_state(AdminStates.event_create)
        await edit_or_answer(
            callback,
            "🎯 <b>YANGI EVENT YARATISH</b>\n\n"
            "Formatda yuboring:\n"
            "<code>Nomi | Soat | CoinMultiplier | BoostRarity | RarityMult</code>\n\n"
            "Misol:\n"
            "<code>GOJO EVENT | 24 | 2.0 | Legendary | 2.0</code>",
            reply_markup=_admin_cancel_kb(callback.from_user.id, "events", "list"),
        )
        return

    await state.clear()

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
            f"(gacha: <code>{ensure_utc(ev.ends_at).strftime('%Y-%m-%d %H:%M UTC')}</code>)"
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


@router.message(AdminStates.event_create, F.text, ~F.text.startswith("/"))
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
    await edit_or_answer(
        callback,
        "📢 Barcha faol guruhlar va foydalanuvchilarga yuboriladigan xabar matnini yozing:",
        reply_markup=_admin_cancel_kb(callback.from_user.id, "home", "view"),
    )


@router.message(AdminStates.broadcast_text, F.text, ~F.text.startswith("/"))
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
