"""Majburiy obuna kanallari, guruhlar va havolalar."""

import logging
import zlib
from datetime import timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from admin.filters import IsAdminFilter
from admin.v2.common import AdminV2States, _back_home_btn, _cancel_kb, _deny_if_no_perm
from database.session import get_session
from models.base import utcnow
from services.community_service import CommunityService
from utils.callback_data import AdmV2Cb
from utils.formatting import esc
from utils.tg_helpers import NotCommandFilter, edit_or_answer
from utils.timeutils import fmt_tashkent

logger = logging.getLogger(__name__)
router = Router(name="admin_v2_channels")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


# ===========================================================================
# 4. FORCED CHANNELS & GROUPS & LINKS (sec="channels")
# ===========================================================================
async def _render_channels_menu(target, owner_id: int) -> None:
    async with get_session() as session:
        channels = await CommunityService.list_channels(session)
    lines = ["📢 <b>MAJBURIY OBUNA KANALLARI / GURUHLAR / LINKLAR</b>\n"]
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="➕ Kanal / Guruh / Link qo‘shish", callback_data=AdmV2Cb(owner_id=owner_id, sec="channels", act="add").pack()))
    mode_lbl = {"daily": "📅 Kunlik", "subscribers": "👥 Obunachi", "vip": "💎 VIP"}
    for ch in channels:
        st = "🟢" if ch.is_active else "🔴"
        lim = f" · Limit: {ch.target_subscribers}" if ch.target_subscribers else ""
        exp = f" · {fmt_tashkent(ch.expires_at, '%d.%m %H:%M')} gacha" if ch.expires_at else ""
        lines.append(f"{st} <b>{esc(ch.title)}</b> [{mode_lbl.get(ch.sub_mode, ch.sub_mode)}]{lim}{exp}")
        b.row(
            InlineKeyboardButton(text=f"{st} {ch.title[:24]}", callback_data=AdmV2Cb(owner_id=owner_id, sec="channels", act="tog", tid=ch.id).pack()),
            InlineKeyboardButton(text="🗑", callback_data=AdmV2Cb(owner_id=owner_id, sec="channels", act="del", tid=ch.id).pack()),
        )
    if not channels:
        lines.append("📭 Majburiy obunalar yo‘q.")
    b.row(_back_home_btn(owner_id))
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


def _channel_mode_kb(owner_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="📅 Kunlik (vaqtinchalik)", callback_data=AdmV2Cb(owner_id=owner_id, sec="channels", act="mode", ext="daily").pack()))
    b.row(InlineKeyboardButton(text="👥 Obunachi soni (limitga qadar)", callback_data=AdmV2Cb(owner_id=owner_id, sec="channels", act="mode", ext="subscribers").pack()))
    b.row(InlineKeyboardButton(text="💎 VIP / Doimiy (cheksiz)", callback_data=AdmV2Cb(owner_id=owner_id, sec="channels", act="mode", ext="vip").pack()))
    b.row(InlineKeyboardButton(text="❌ Bekor", callback_data=AdmV2Cb(owner_id=owner_id, sec="channels", act="v").pack()))
    return b.as_markup()


@router.callback_query(AdmV2Cb.filter(F.sec == "channels"))
async def cb_v2_channels(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "channels"):
        return
    act = callback_data.act
    owner_id = callback.from_user.id

    if act == "add":
        await state.set_state(AdminV2States.waiting_channel_link)
        await state.update_data(owner_tg_id=owner_id)
        await edit_or_answer(
            callback,
            "📢 <b>KANAL / GURUH / HAVOLA QO‘SHISH</b>\n\n"
            "Quyidagilardan birini yuboring:\n"
            "• Kanal yoki guruh <code>@username</code> (masalan <code>@anime_uz</code>)\n"
            "• Link (<code>https://t.me/...</code> yoki Instagram/YouTube havolasi)\n"
            "• Chat ID (<code>-100...</code>) yoki kanaldan biror postni forward qiling!\n\n"
            "<i>Telegram kanallar uchun bot o‘sha kanalda admin bo‘lishi kerak.</i>",
            reply_markup=_cancel_kb(owner_id, "channels", "v"),
        )
        return

    if act == "mode":
        mode = callback_data.ext or "vip"
        await state.update_data(ch_mode=mode)
        if mode == "daily":
            await state.set_state(AdminV2States.waiting_channel_days)
            b = InlineKeyboardBuilder()
            for d in (1, 3, 7, 14, 30):
                b.add(InlineKeyboardButton(text=f"{d} kun", callback_data=AdmV2Cb(owner_id=owner_id, sec="channels", act="days", tid=d).pack()))
            b.adjust(3)
            b.row(InlineKeyboardButton(text="❌ Bekor", callback_data=AdmV2Cb(owner_id=owner_id, sec="channels", act="v").pack()))
            await edit_or_answer(callback, "📅 Necha kun majburiy tursin? Tugmadan tanlang yoki kun sonini yozing:", reply_markup=b.as_markup())
            return
        if mode == "subscribers":
            await state.set_state(AdminV2States.waiting_channel_subs)
            b = InlineKeyboardBuilder()
            for cnt in (500, 1000, 2000, 5000):
                b.add(InlineKeyboardButton(text=f"{cnt:,}", callback_data=AdmV2Cb(owner_id=owner_id, sec="channels", act="subs", tid=cnt).pack()))
            b.adjust(2)
            b.row(InlineKeyboardButton(text="❌ Bekor", callback_data=AdmV2Cb(owner_id=owner_id, sec="channels", act="v").pack()))
            await edit_or_answer(callback, "👥 Necha obunachiga yetguncha tursin? Tugmadan tanlang yoki son yozing:", reply_markup=b.as_markup())
            return

        # VIP (doimiy)
        data = await state.get_data()
        await state.clear()
        async with get_session() as session:
            await CommunityService.add_channel(
                session,
                chat_id=int(data["ch_id"]),
                title=data["ch_title"],
                username=data.get("ch_uname"),
                invite_link=data.get("ch_link"),
                added_by_tg_id=owner_id,
                channel_type=data.get("ch_type", "telegram"),
                sub_mode="vip",
            )
            await session.commit()
        await callback.answer("✅ VIP/Doimiy kanal qo‘shildi!", show_alert=True)
        await _render_channels_menu(callback, owner_id)
        return

    if act == "days":
        data = await state.get_data()
        await state.clear()
        exp = utcnow() + timedelta(days=max(1, callback_data.tid))
        async with get_session() as session:
            await CommunityService.add_channel(
                session,
                chat_id=int(data["ch_id"]),
                title=data["ch_title"],
                username=data.get("ch_uname"),
                invite_link=data.get("ch_link"),
                added_by_tg_id=owner_id,
                channel_type=data.get("ch_type", "telegram"),
                sub_mode="daily",
                expires_at=exp,
            )
            await session.commit()
        await callback.answer(f"✅ {callback_data.tid} kunlik kanal qo‘shildi!", show_alert=True)
        await _render_channels_menu(callback, owner_id)
        return

    if act == "subs":
        data = await state.get_data()
        await state.clear()
        async with get_session() as session:
            await CommunityService.add_channel(
                session,
                chat_id=int(data["ch_id"]),
                title=data["ch_title"],
                username=data.get("ch_uname"),
                invite_link=data.get("ch_link"),
                added_by_tg_id=owner_id,
                target_subscribers=max(1, callback_data.tid),
                channel_type=data.get("ch_type", "telegram"),
                sub_mode="subscribers",
            )
            await session.commit()
        await callback.answer(f"✅ {callback_data.tid:,} obunachigacha qo‘shildi!", show_alert=True)
        await _render_channels_menu(callback, owner_id)
        return

    async with get_session() as session:
        if act == "tog":
            await CommunityService.toggle_channel(session, callback_data.tid)
            await session.commit()
        elif act == "del":
            await CommunityService.delete_channel(session, callback_data.tid)
            await session.commit()
    await state.clear()
    await _render_channels_menu(callback, owner_id)


@router.message(AdminV2States.waiting_channel_link, NotCommandFilter())
async def fsm_v2_channel_link(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return

    # 1. Forward qilingan xabar bo'lsa
    f_chat = getattr(message, "forward_from_chat", None)
    if f_chat is not None:
        uname = getattr(f_chat, "username", None)
        ch_type = "group" if getattr(f_chat, "type", "") in ("group", "supergroup") else "telegram"
        link = f"https://t.me/{uname}" if uname else None
        await state.update_data(ch_id=f_chat.id, ch_title=f_chat.title or "Kanal", ch_uname=uname, ch_link=link, ch_type=ch_type)
        await state.set_state(None)
        await message.answer(
            f"✅ Topildi: <b>{esc(f_chat.title or 'Kanal')}</b> (<code>{f_chat.id}</code>)\n\nEndi obuna rejimini tanlang:",
            reply_markup=_channel_mode_kb(message.from_user.id),
        )
        return

    raw = (message.text or "").strip()
    if not raw:
        await message.answer("⚠️ @username, link yoki Chat ID yuboring.")
        return

    # 2. Tashqi link (Instagram/YouTube/Sayt)
    if raw.startswith("http") and "t.me/" not in raw.lower() and "telegram.me/" not in raw.lower():
        pseudo_id = -abs(zlib.crc32(raw.encode()) + 10_000_000_000)
        await state.update_data(ch_id=pseudo_id, ch_uname=None, ch_link=raw[:250], ch_type="external")
        await state.set_state(AdminV2States.waiting_channel_title)
        await message.answer("🔗 Tashqi havola aniqlandi! Tugmada ko‘rinadigan <b>nomini</b> yozing (masalan: <code>Instagram</code>):")
        return

    # 3. Telegram link/username/ID
    ref = raw
    if "t.me/" in raw or "telegram.me/" in raw:
        tail = raw.rstrip("/").split("/")[-1]
        if tail.startswith("+") or tail.startswith("joinchat"):
            pseudo_id = -abs(zlib.crc32(raw.encode()) + 10_000_000_000)
            await state.update_data(ch_id=pseudo_id, ch_uname=None, ch_link=raw[:250], ch_type="external")
            await state.set_state(AdminV2States.waiting_channel_title)
            await message.answer(
                "🔐 Yopiq taklif linki! Agar bot obunani ham tekshirishini istasangiz, kanal postini forward qiling yoki nomini yozing:"
            )
            return
        ref = "@" + tail.lstrip("@")

    chat_lookup = int(ref) if ref.lstrip("-").isdigit() else (ref if ref.startswith("@") else "@" + ref)
    try:
        chat = await message.bot.get_chat(chat_lookup)
        uname = getattr(chat, "username", None)
        link = f"https://t.me/{uname}" if uname else getattr(chat, "invite_link", None)
        ch_type = "group" if getattr(chat, "type", "") in ("group", "supergroup") else "telegram"
        try:
            me = await message.bot.get_me()
            member = await message.bot.get_chat_member(chat.id, me.id)
            if getattr(member, "status", "") not in ("administrator", "creator"):
                await message.answer("⚠️ Eslatma: Bot bu kanalda admin emas — obuna tekshiruvi ishlashi uchun botni admin qiling.")
        except Exception:
            pass
        await state.update_data(ch_id=chat.id, ch_title=chat.title or str(chat.id), ch_uname=uname, ch_link=link, ch_type=ch_type)
        await state.set_state(None)
        await message.answer(
            f"✅ <b>{esc(chat.title or str(chat.id))}</b> aniqlandi!\n\nEndi obuna rejimini tanlang:",
            reply_markup=_channel_mode_kb(message.from_user.id),
        )
    except Exception as exc:
        await message.answer(
            f"❌ Kanal topilmadi yoki botda ruxsat yo‘q: <code>{esc(str(exc)[:100])}</code>\n"
            "Botni kanalga admin qiling yoki kanaldan post forward qilib tashlang."
        )


@router.message(AdminV2States.waiting_channel_title, NotCommandFilter())
async def fsm_v2_channel_title(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    title = (message.text or "Link").strip()[:200]
    await state.update_data(ch_title=title)
    await state.set_state(None)
    await message.answer(f"🏷 Nomi: <b>{esc(title)}</b>\n\nEndi rejimni tanlang:", reply_markup=_channel_mode_kb(message.from_user.id))


@router.message(AdminV2States.waiting_channel_days, NotCommandFilter())
async def fsm_v2_channel_days(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) < 1:
        await message.answer("⚠️ Kamida 1 kun yozing.")
        return
    days = int(raw)
    await state.clear()
    async with get_session() as session:
        await CommunityService.add_channel(
            session,
            chat_id=int(data["ch_id"]),
            title=data["ch_title"],
            username=data.get("ch_uname"),
            invite_link=data.get("ch_link"),
            added_by_tg_id=message.from_user.id,
            channel_type=data.get("ch_type", "telegram"),
            sub_mode="daily",
            expires_at=utcnow() + timedelta(days=days),
        )
        await session.commit()
    await message.answer(f"✅ Kanal <b>{days} kun</b>ga qo‘shildi!")
    await _render_channels_menu(message, message.from_user.id)


@router.message(AdminV2States.waiting_channel_subs, NotCommandFilter())
async def fsm_v2_channel_subs(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "")
    if not raw.isdigit() or int(raw) < 1:
        await message.answer("⚠️ Musbat obunachi sonini yozing.")
        return
    subs = int(raw)
    await state.clear()
    async with get_session() as session:
        await CommunityService.add_channel(
            session,
            chat_id=int(data["ch_id"]),
            title=data["ch_title"],
            username=data.get("ch_uname"),
            invite_link=data.get("ch_link"),
            added_by_tg_id=message.from_user.id,
            target_subscribers=subs,
            channel_type=data.get("ch_type", "telegram"),
            sub_mode="subscribers",
        )
        await session.commit()
    await message.answer(f"✅ Kanal <b>{subs:,}</b> obunachigacha qo‘shildi!")
    await _render_channels_menu(message, message.from_user.id)
