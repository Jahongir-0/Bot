"""Aloqa (support), ban apellyatsiyasi va majburiy obuna tekshiruvi."""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.session import get_session
from handlers.common import V2UserStates
from keyboards.main_menu import back_home_kb, main_menu_kb
from keyboards.v2 import support_menu_kb
from models.user import User
from services.community_service import CommunityService
from utils.callback_data import SupCb
from utils.formatting import esc, header
from utils.tg_helpers import NotCommandFilter, edit_or_answer, is_admin_user

logger = logging.getLogger(__name__)
router = Router(name="support")


# ------------------------------------------------------------------
# SUPPORT & BAN APPEAL (/support) + FORCED SUB CHECK
# ------------------------------------------------------------------
@router.message(Command("support", "report"))
async def cmd_support(message: Message, db_user: User) -> None:
    async with get_session() as session:
        tickets = await CommunityService.list_tickets(session, user_id=db_user.id)
    await message.answer(
        f"{header('💬 <b>ALOQA</b>')}\nSavol, taklif yoki shikoyatingizni adminga yuboring. Javob shu yerda va xabar ko‘rinishida keladi.\n\n👇 Yangi murojaat yozing yoki oldingisini oching:",
        reply_markup=support_menu_kb(message.from_user.id, tickets),
    )


@router.callback_query(SupCb.filter(F.act == "v"))
async def cb_support_menu(callback: CallbackQuery, db_user: User) -> None:
    async with get_session() as session:
        tickets = await CommunityService.list_tickets(session, user_id=db_user.id)
    await edit_or_answer(
        callback,
        f"{header('💬 <b>ALOQA</b>')}\nSavol, taklif yoki shikoyatingizni adminga yuboring. Javob shu yerda va xabar ko‘rinishida keladi.\n\n👇 Yangi murojaat yozing yoki oldingisini oching:",
        reply_markup=support_menu_kb(callback.from_user.id, tickets),
    )


@router.callback_query(SupCb.filter(F.act == "new"))
async def cb_support_new(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(V2UserStates.waiting_support_subject)
    await state.update_data(owner_tg_id=callback.from_user.id)
    await callback.message.answer("📝 Murojaat mavzusini yozing (qisqacha):\n<i>Bekor qilish: /cancel</i>")
    await callback.answer()


@router.message(V2UserStates.waiting_support_subject, NotCommandFilter())
async def fsm_sup_subject(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    await state.update_data(sup_subject=(message.text or "Murojaat").strip()[:200])
    await state.set_state(V2UserStates.waiting_support_body)
    await message.answer("✍️ Endi murojaat matnini batafsil yozing:")


@router.message(V2UserStates.waiting_support_body, NotCommandFilter())
async def fsm_sup_body(message: Message, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    subject = data.get("sup_subject", "Murojaat")
    await state.clear()
    async with get_session() as session:
        t = await CommunityService.create_ticket(
            session, db_user.id, subject, (message.text or "").strip(), message.from_user.id
        )
        await session.commit()
    await message.answer(f"✅ Murojaatingiz qabul qilindi (<b>#{t.id}</b>).\nAdmin javob berishi bilan sizga xabar keladi.")


@router.callback_query(SupCb.filter(F.act == "view"))
async def cb_support_view(callback: CallbackQuery, callback_data: SupCb, db_user: User) -> None:
    async with get_session() as session:
        res = await CommunityService.get_ticket_with_messages(session, callback_data.tid)
    if res is None or res[0].user_id != db_user.id:
        await callback.answer("Murojaat topilmadi.", show_alert=True)
        return
    t, msgs, _ = res
    status_label = {"open": "🟡 Ko‘rib chiqilmoqda", "answered": "🟢 Javob berildi", "closed": "🔴 Yopilgan"}.get(t.status, t.status)
    lines = [header(f"💬 <b>Murojaat #{t.id}</b>"), f"📝 {esc(t.subject)}\n📌 Holat: <b>{status_label}</b>\n"]
    for m in msgs[-10:]:
        who = "👤 Siz" if m.sender_type == "user" else "🛡 Admin"
        lines.append(f"<b>{who}:</b> {esc(m.body)}")
    await edit_or_answer(callback, "\n".join(lines), reply_markup=back_home_kb(callback.from_user.id, "home"))


@router.callback_query(SupCb.filter(F.act == "check_sub"))
async def cb_check_sub(callback: CallbackQuery, db_user: User, is_bot_admin: bool = False) -> None:
    async with get_session() as session:
        ok, missing = await CommunityService.check_user_subscriptions(
            callback.bot, session, db_user, callback.from_user.id
        )
        await session.commit()
    if not ok and missing:
        await callback.answer("❌ Hali hamma kanallarga obuna bo‘lmadingiz!", show_alert=True)
        return
    await callback.answer("✅ Obuna tasdiqlandi! Xush kelibsiz!", show_alert=True)
    is_adm = is_bot_admin or is_admin_user(callback.from_user)
    await edit_or_answer(
        callback,
        "🏠 <b>BOSH MENU</b> — Obuna tasdiqlandi!",
        reply_markup=main_menu_kb(callback.from_user.id, is_admin=is_adm),
    )
