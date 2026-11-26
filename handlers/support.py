"""Aloqa (support — matn va rasm/video/hujjat), ban apellyatsiyasi va majburiy obuna tekshiruvi."""

import logging
from typing import Optional, Tuple

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.session import get_session
from handlers.common import V2UserStates
from keyboards.main_menu import main_menu_kb
from keyboards.v2 import support_menu_kb, support_ticket_detail_kb
from models.user import User
from services.community_service import CommunityService
from utils.callback_data import SupCb
from utils.formatting import esc, header
from utils.tg_helpers import NotCommandFilter, edit_or_answer, is_admin_user

logger = logging.getLogger(__name__)
router = Router(name="support")


def _extract_support_payload(message: Message) -> Tuple[str, Optional[str], Optional[str]]:
    caption = (message.caption or "").strip()
    text = (message.text or "").strip()
    if message.photo:
        return caption or "[🖼 Rasm]", "photo", message.photo[-1].file_id
    if message.video:
        return caption or "[🎬 Video]", "video", message.video.file_id
    if message.document:
        return caption or f"[📎 Fayl: {message.document.file_name or 'hujjat'}]", "document", message.document.file_id
    return text, None, None


async def _notify_admins_support_msg(
    bot,
    session,
    ticket_id: int,
    subject: str,
    sender_user: User,
    sender_tg_id: int,
    body: str,
    media_type: Optional[str],
    media_file_id: Optional[str],
    is_new: bool,
) -> None:
    admin_ids = await CommunityService.list_admin_tg_ids(session, section="support")
    prefix = "📩 <b>YANGI MUROJAAT</b>" if is_new else "💬 <b>MUROJAATGA YANGI JAVOB</b>"
    note = (
        f"{prefix} <b>#{ticket_id}</b>\n"
        f"👤 {esc(sender_user.display_name)} (<code>{sender_tg_id}</code>)\n"
        f"📝 Mavzu: <b>{esc(subject)}</b>\n\n"
        f"{esc(body[:900])}\n\n"
        "↩️ <i>Javob berish uchun shu xabarga <b>REPLY</b> qiling.</i>"
    )
    for aid in admin_ids:
        try:
            if media_type == "photo" and media_file_id:
                await bot.send_photo(aid, media_file_id, caption=note[:1020])
            elif media_type == "video" and media_file_id:
                await bot.send_video(aid, media_file_id, caption=note[:1020])
            elif media_type == "document" and media_file_id:
                await bot.send_document(aid, media_file_id, caption=note[:1020])
            else:
                await bot.send_message(aid, note)
        except Exception:
            pass


# ------------------------------------------------------------------
# SUPPORT & BAN APPEAL (/support) + FORCED SUB CHECK
# ------------------------------------------------------------------
@router.message(Command("support", "report"))
async def cmd_support(message: Message, db_user: User) -> None:
    async with get_session() as session:
        tickets = await CommunityService.list_tickets(session, user_id=db_user.id)
    await message.answer(
        f"{header('💬 <b>ALOQA</b>')}\nSavol, taklif yoki shikoyatingizni (matn, rasm, video yoki fayl bilan) adminga yuboring.\n\n👇 Yangi murojaat yozing yoki oldingisini oching:",
        reply_markup=support_menu_kb(message.from_user.id, tickets),
    )


@router.callback_query(SupCb.filter(F.act == "v"))
async def cb_support_menu(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    await state.clear()
    async with get_session() as session:
        tickets = await CommunityService.list_tickets(session, user_id=db_user.id)
    await edit_or_answer(
        callback,
        f"{header('💬 <b>ALOQA</b>')}\nSavol, taklif yoki shikoyatingizni (matn, rasm, video yoki fayl bilan) adminga yuboring.\n\n👇 Yangi murojaat yozing yoki oldingisini oching:",
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
    await message.answer("✍️ Endi murojaatingizni yozing (rasm, video yoki fayl + izoh ham yuborishingiz mumkin):")


@router.message(V2UserStates.waiting_support_body, NotCommandFilter())
async def fsm_sup_body(message: Message, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    body, media_type, media_file_id = _extract_support_payload(message)
    if not body and not media_file_id:
        await message.answer("⚠️ Matn yoki rasm/video/hujjat yuboring.")
        return
    subject = data.get("sup_subject", "Murojaat")
    await state.clear()
    async with get_session() as session:
        t = await CommunityService.create_ticket(
            session,
            db_user.id,
            subject,
            body,
            message.from_user.id,
            media_type=media_type,
            media_file_id=media_file_id,
        )
        await _notify_admins_support_msg(
            message.bot, session, t.id, t.subject, db_user, message.from_user.id, body, media_type, media_file_id, is_new=True
        )
        await session.commit()
    await message.answer(f"✅ Murojaatingiz qabul qilindi (<b>#{t.id}</b>).\nAdmin javob berishi bilan sizga xabar keladi.")


@router.callback_query(SupCb.filter(F.act == "reply"))
async def cb_support_reply(callback: CallbackQuery, callback_data: SupCb, state: FSMContext, db_user: User) -> None:
    async with get_session() as session:
        res = await CommunityService.get_ticket_with_messages(session, callback_data.tid)
    if res is None or res[0].user_id != db_user.id:
        await callback.answer("Murojaat topilmadi.", show_alert=True)
        return
    if res[0].status == "closed":
        await callback.answer("Bu murojaat yopilgan.", show_alert=True)
        return
    await state.set_state(V2UserStates.waiting_support_reply)
    await state.update_data(owner_tg_id=callback.from_user.id, sup_tid=callback_data.tid)
    await callback.message.answer(
        f"✍️ Murojaat <b>#{callback_data.tid}</b> uchun xabar yoki rasm/video/hujjat yuboring:\n<i>Bekor qilish: /cancel</i>"
    )
    await callback.answer()


@router.message(V2UserStates.waiting_support_reply, NotCommandFilter())
async def fsm_sup_reply(message: Message, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    tid = int(data.get("sup_tid", 0))
    body, media_type, media_file_id = _extract_support_payload(message)
    if not body and not media_file_id:
        await message.answer("⚠️ Matn yoki rasm/video/hujjat yuboring.")
        return
    await state.clear()
    async with get_session() as session:
        t = await CommunityService.reply_ticket(
            session,
            tid,
            "user",
            message.from_user.id,
            body,
            media_type=media_type,
            media_file_id=media_file_id,
        )
        if t is None or t.user_id != db_user.id:
            await message.answer("❌ Murojaat topilmadi.")
            return
        await _notify_admins_support_msg(
            message.bot, session, t.id, t.subject, db_user, message.from_user.id, body, media_type, media_file_id, is_new=False
        )
        await session.commit()
    await message.answer(f"✅ Xabaringiz murojaat <b>#{tid}</b> ga qo‘shildi va adminga yuborildi.")


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
    media_items = []
    for m in msgs[-10:]:
        who = "👤 Siz" if m.sender_type == "user" else "🛡 Admin"
        tag = f" [{m.media_type}]" if m.media_type else ""
        lines.append(f"<b>{who}{tag}:</b> {esc(m.body)}")
        if m.media_type and m.media_file_id:
            media_items.append((who, m.media_type, m.media_file_id, m.body))
    await edit_or_answer(
        callback,
        "\n".join(lines),
        reply_markup=support_ticket_detail_kb(callback.from_user.id, t),
    )
    if callback.message and media_items:
        for who, mtype, fid, cap in media_items[-3:]:
            try:
                c_txt = f"<b>{who}:</b> {esc(cap[:200])}"
                if mtype == "photo":
                    await callback.message.answer_photo(fid, caption=c_txt)
                elif mtype == "video":
                    await callback.message.answer_video(fid, caption=c_txt)
                elif mtype == "document":
                    await callback.message.answer_document(fid, caption=c_txt)
            except Exception:
                pass


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
