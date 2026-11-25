"""Reklama: matn, joylashuv, muddat va klik statistikasi."""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from admin.filters import IsAdminFilter
from admin.v2.common import AdminV2States, _back_home_btn, _cancel_kb, _deny_if_no_perm, _local_tz
from database.session import get_session
from models.base import ensure_utc, utcnow
from models.v2_features import AdCampaign
from services.community_service import CommunityService
from utils.callback_data import AdmV2Cb
from utils.formatting import esc
from utils.tg_helpers import NotCommandFilter, edit_or_answer

logger = logging.getLogger(__name__)
router = Router(name="admin_v2_reklama")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


# ===========================================================================
# 10. REKLAMA (sec="reklama", Qisqa matn + Joy cheklovi + Muddat + Klik)
# ===========================================================================
def _ad_status_badge(ad: AdCampaign) -> str:
    if CommunityService.is_ad_expired(ad):
        return "⌛ Muddati tugagan"
    return "🟢 Faol" if ad.is_active else "⏸ Nofaol"


def _ad_remaining_text(ad: AdCampaign) -> str:
    if ad.expires_at is None:
        return "♾ To o‘chirilmaguncha"
    now = utcnow()
    exp = ensure_utc(ad.expires_at)
    secs = int((exp - now).total_seconds())
    if secs <= 0:
        return "⌛ Muddati tugagan"
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins = max(1, rem // 60)
    if days > 0:
        left = f"{days} kun {hours} soat"
    elif hours > 0:
        left = f"{hours} soat {mins} daq."
    else:
        left = f"{mins} daqiqa"
    exp_local = exp.astimezone(_local_tz())
    return f"{left} ({exp_local:%d.%m %H:%M} gacha)"


def _ad_placement_kb(owner_id: int, occupied: dict, act: str, tid: int = 0) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for key in ("group", "private", "all"):
        label = CommunityService.placement_label(key)
        if key == "all":
            busy = bool(occupied)
        else:
            busy = key in occupied
        tag = "🔒 Band" if busy else "✅ Bo‘sh"
        b.row(
            InlineKeyboardButton(
                text=f"{label} — {tag}",
                callback_data=AdmV2Cb(owner_id=owner_id, sec="reklama", act=act, tid=tid, ext=key).pack(),
            )
        )
    back_act = "det" if tid else "v"
    b.row(InlineKeyboardButton(text="❌ Bekor", callback_data=AdmV2Cb(owner_id=owner_id, sec="reklama", act=back_act, tid=tid).pack()))
    return b.as_markup()


def _ad_duration_kb(owner_id: int, act: str, tid: int = 0) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for hrs, lbl in CommunityService.AD_DURATION_OPTIONS:
        ext_val = str(hrs) if act == "setdur" else ""
        cb_tid = tid if act == "setdur" else hrs
        b.add(
            InlineKeyboardButton(
                text=f"⏳ {lbl}" if hrs > 0 else lbl,
                callback_data=AdmV2Cb(owner_id=owner_id, sec="reklama", act=act, tid=cb_tid, ext=ext_val).pack(),
            )
        )
    b.adjust(2)
    back_act = "det" if tid and act == "setdur" else "v"
    b.row(InlineKeyboardButton(text="❌ Bekor", callback_data=AdmV2Cb(owner_id=owner_id, sec="reklama", act=back_act, tid=tid if back_act == "det" else 0).pack()))
    return b.as_markup()


async def _show_reklama_home(target, owner_id: int) -> None:
    async with get_session() as session:
        ads = await CommunityService.list_ads(session)
        occupied = await CommunityService.get_occupied_ad_slots(session)
        await session.commit()

    grp_holder = f"🟢 Faol (#{occupied['group'].id})" if "group" in occupied else "⚪ Bo‘sh"
    pm_holder = f"🟢 Faol (#{occupied['private'].id})" if "private" in occupied else "⚪ Bo‘sh"
    lines = [
        "📣 <b>REKLAMA KAMPANIYALARI</b>",
        "━━━━━━━━━━━━━━━",
        f"👥 Guruh (drop tagi): <b>{grp_holder}</b>",
        f"📱 User panel: <b>{pm_holder}</b>",
        "",
    ]
    b = InlineKeyboardBuilder()
    all_full = "group" in occupied and "private" in occupied
    add_lbl = "➕ Reklama qo‘shish" if not all_full else "🔒 Joylar band (Reklama qo‘shish)"
    b.row(InlineKeyboardButton(text=add_lbl, callback_data=AdmV2Cb(owner_id=owner_id, sec="reklama", act="add").pack()))
    for a in ads[:12]:
        icon = "⌛" if CommunityService.is_ad_expired(a) else ("🟢" if a.is_active else "⏸")
        click_part = f" · 🔗 {a.clicks_count}" if (a.button_url or a.clicks_count > 0) else ""
        lines.append(
            f"{icon} <b>#{a.id}</b> {esc(a.title[:36])} — "
            f"{esc(CommunityService.placement_label(a.placement))} · 👁 {a.views_count}{click_part}"
        )
        b.row(
            InlineKeyboardButton(
                text=f"{icon} #{a.id} {a.title[:26]}",
                callback_data=AdmV2Cb(owner_id=owner_id, sec="reklama", act="det", tid=a.id).pack(),
            )
        )
    if not ads:
        lines.append("📭 Hozircha reklamalar yo‘q.")
    else:
        lines.append("\n👇 Boshqarish yoki tahrirlash uchun reklamani tanlang:")
    b.row(_back_home_btn(owner_id))
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


async def _show_reklama_detail(target, owner_id: int, ad_id: int) -> None:
    async with get_session() as session:
        await CommunityService.expire_due_ads(session)
        ad = await session.get(AdCampaign, ad_id)
        await session.commit()
    if ad is None:
        await _show_reklama_home(target, owner_id)
        return

    lines = [
        f"📣 <b>REKLAMA #{ad.id}</b>",
        "━━━━━━━━━━━━━━━",
        f"📝 <b>Matn:</b>\n{esc(ad.text)}",
        "",
        f"📌 Holat: <b>{_ad_status_badge(ad)}</b>",
        f"📍 Joyi: <b>{esc(CommunityService.placement_label(ad.placement))}</b>",
        f"⏳ Muddat: <b>{_ad_remaining_text(ad)}</b>",
        f"👁 Ko‘rishlar: <b>{ad.views_count:,}</b> ta",
    ]
    if ad.button_url or ad.clicks_count > 0:
        lines.append(f"🔗 Havola/user bosishlari: <b>{ad.clicks_count:,}</b> marta")
        if ad.button_url:
            lines.append(f"🌐 Manzil: <code>{esc(ad.button_url)}</code>")

    b = InlineKeyboardBuilder()
    tog_txt = "⏸ Faolsizlantirish" if ad.is_active else "▶️ Faollashtirish"
    b.row(
        InlineKeyboardButton(text=tog_txt, callback_data=AdmV2Cb(owner_id=owner_id, sec="reklama", act="tog", tid=ad.id).pack()),
        InlineKeyboardButton(text="✏️ Matn", callback_data=AdmV2Cb(owner_id=owner_id, sec="reklama", act="edtxt", tid=ad.id).pack()),
    )
    b.row(
        InlineKeyboardButton(text="📍 Joyi", callback_data=AdmV2Cb(owner_id=owner_id, sec="reklama", act="edplc", tid=ad.id).pack()),
        InlineKeyboardButton(text="⏳ Vaqti", callback_data=AdmV2Cb(owner_id=owner_id, sec="reklama", act="eddur", tid=ad.id).pack()),
    )
    b.row(
        InlineKeyboardButton(text="🗑 O‘chirish", callback_data=AdmV2Cb(owner_id=owner_id, sec="reklama", act="del", tid=ad.id).pack()),
        InlineKeyboardButton(text="⬅️ Orqaga", callback_data=AdmV2Cb(owner_id=owner_id, sec="reklama", act="v").pack()),
    )
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


@router.callback_query(AdmV2Cb.filter(F.sec == "reklama"))
async def cb_v2_reklama(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "reklama"):
        return
    act = callback_data.act
    owner_id = callback.from_user.id

    if act == "add":
        async with get_session() as session:
            occupied = await CommunityService.get_occupied_ad_slots(session)
            await session.commit()
        if "group" in occupied and "private" in occupied:
            ids = ", ".join(f"#{a.id}" for a in sorted({ad.id: ad for ad in occupied.values()}.values(), key=lambda x: x.id))
            await callback.answer(
                f"⛔ Barcha reklama joylari band ({ids}). Avval faol reklamani o‘chiring, faolsizlantiring yoki joyini o‘zgartiring!",
                show_alert=True,
            )
            return
        await state.set_state(AdminV2States.waiting_ad_content)
        await state.update_data(owner_tg_id=owner_id, ad_edit_id=None)
        await edit_or_answer(
            callback,
            "📣 <b>YANGI REKLAMA (1/3)</b>\n\n"
            f"Faqat qisqa <b>matn</b> yuboring (ko‘pi bilan <b>{CommunityService.AD_MAX_LEN}</b> belgi, rasm va fayllarsiz).\n\n"
            "💡 <i>Matn ichiga kanal linki (https://t.me/... yoki @username) yozsangiz, unga nechta odam kirganini ham sanaymiz.</i>",
            reply_markup=_cancel_kb(owner_id, "reklama", "v"),
        )
        return

    if act == "place":
        chosen = callback_data.ext or "all"
        async with get_session() as session:
            ok_plc, err_plc = await CommunityService.can_use_placement(session, chosen)
            await session.commit()
        if not ok_plc:
            await callback.answer(err_plc[:190], show_alert=True)
            return
        await state.update_data(ad_place=chosen)
        await edit_or_answer(
            callback,
            f"⏳ <b>YANGI REKLAMA (3/3)</b>\n\nJoy: <b>{esc(CommunityService.placement_label(chosen))}</b>\nReklama qancha vaqt ko‘rinsin?",
            reply_markup=_ad_duration_kb(owner_id, act="dur"),
        )
        return

    if act == "dur":
        data = await state.get_data()
        txt = (data.get("ad_text") or "").strip()
        place = data.get("ad_place") or "all"
        if not txt:
            await state.clear()
            await callback.answer("⚠️ Reklama matni topilmadi, qaytadan urining.", show_alert=True)
            await _show_reklama_home(callback, owner_id)
            return
        async with get_session() as session:
            ok, msg, ad = await CommunityService.create_ad(
                session,
                text=txt,
                admin_tg_id=owner_id,
                placement=place,
                duration_hours=int(callback_data.tid),
            )
            if ok:
                await session.commit()
            else:
                await session.rollback()
        await state.clear()
        await callback.answer(msg[:190], show_alert=True)
        if ok and ad is not None:
            await _show_reklama_detail(callback, owner_id, ad.id)
        else:
            await _show_reklama_home(callback, owner_id)
        return

    if act == "det":
        await state.clear()
        await _show_reklama_detail(callback, owner_id, callback_data.tid)
        return

    if act == "tog":
        async with get_session() as session:
            ok, msg, _ad = await CommunityService.toggle_ad(session, callback_data.tid)
            if ok:
                await session.commit()
            else:
                await session.rollback()
        await callback.answer(msg[:190], show_alert=True)
        await _show_reklama_detail(callback, owner_id, callback_data.tid)
        return

    if act == "edtxt":
        await state.set_state(AdminV2States.waiting_ad_edit_text)
        await state.update_data(owner_tg_id=owner_id, ad_edit_id=callback_data.tid)
        await edit_or_answer(
            callback,
            f"✏️ <b>REKLAMA #{callback_data.tid} MATNINI TAHRIRLASH</b>\n\n"
            f"Yangi qisqa matnni yuboring (ko‘pi bilan {CommunityService.AD_MAX_LEN} belgi, rasmsiz):",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="❌ Bekor", callback_data=AdmV2Cb(owner_id=owner_id, sec="reklama", act="det", tid=callback_data.tid).pack())]]
            ),
        )
        return

    if act == "edplc":
        async with get_session() as session:
            occupied = await CommunityService.get_occupied_ad_slots(session, exclude_ad_id=callback_data.tid)
            await session.commit()
        await edit_or_answer(
            callback,
            f"📍 <b>REKLAMA #{callback_data.tid} JOYINI O‘ZGARTIRISH</b>\n\nYangi joyni tanlang:",
            reply_markup=_ad_placement_kb(owner_id, occupied, act="setplc", tid=callback_data.tid),
        )
        return

    if act == "setplc":
        async with get_session() as session:
            ok, msg, _ad = await CommunityService.update_ad(
                session, callback_data.tid, placement=callback_data.ext or "all"
            )
            if ok:
                await session.commit()
            else:
                await session.rollback()
        await callback.answer(msg[:190], show_alert=True)
        await _show_reklama_detail(callback, owner_id, callback_data.tid)
        return

    if act == "eddur":
        await edit_or_answer(
            callback,
            f"⏳ <b>REKLAMA #{callback_data.tid} VAQTINI O‘ZGARTIRISH</b>\n\nHozirdan boshlab qancha vaqt ko‘rinsin?",
            reply_markup=_ad_duration_kb(owner_id, act="setdur", tid=callback_data.tid),
        )
        return

    if act == "setdur":
        hrs = int(callback_data.ext) if (callback_data.ext or "").isdigit() else 0
        async with get_session() as session:
            ok, msg, _ad = await CommunityService.update_ad(
                session, callback_data.tid, duration_hours=hrs
            )
            if ok:
                await session.commit()
            else:
                await session.rollback()
        await callback.answer(msg[:190], show_alert=True)
        await _show_reklama_detail(callback, owner_id, callback_data.tid)
        return

    if act == "del":
        async with get_session() as session:
            await CommunityService.delete_ad(session, callback_data.tid)
            await session.commit()
        await state.clear()
        await callback.answer("🗑 Reklama o‘chirildi.", show_alert=True)
        await _show_reklama_home(callback, owner_id)
        return

    await state.clear()
    await _show_reklama_home(callback, owner_id)


@router.message(AdminV2States.waiting_ad_content, NotCommandFilter())
async def fsm_v2_ad_content(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id:
        return
    if (
        message.photo
        or message.document
        or message.video
        or message.animation
        or message.sticker
        or message.audio
        or message.voice
        or message.video_note
        or not message.text
    ):
        await message.answer("⚠️ Reklama faqat qisqa <b>MATN</b> bo‘lishi kerak! Rasm, fayl yoki stiker yubormang.")
        return
    ok, err, cleaned = CommunityService.validate_ad_text(message.text)
    if not ok:
        await message.answer(err)
        return
    await state.update_data(ad_text=cleaned)
    await state.set_state(None)
    async with get_session() as session:
        occupied = await CommunityService.get_occupied_ad_slots(session)
        await session.commit()
    await message.answer(
        f"📍 <b>YANGI REKLAMA (2/3)</b>\n\nMatn saqlandi ({len(cleaned)}/{CommunityService.AD_MAX_LEN} belgi).\nReklama qayerlarda chiqsin?",
        reply_markup=_ad_placement_kb(message.from_user.id, occupied, act="place"),
    )


@router.message(AdminV2States.waiting_ad_edit_text, NotCommandFilter())
async def fsm_v2_ad_edit_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    if (
        message.photo
        or message.document
        or message.video
        or message.animation
        or message.sticker
        or message.audio
        or message.voice
        or message.video_note
        or not message.text
    ):
        await message.answer("⚠️ Reklama faqat qisqa <b>MATN</b> bo‘lishi kerak! Rasm, fayl yoki stiker yubormang.")
        return
    ad_id = int(data.get("ad_edit_id") or 0)
    async with get_session() as session:
        ok, msg, _ad = await CommunityService.update_ad(session, ad_id, text=message.text)
        if ok:
            await session.commit()
        else:
            await session.rollback()
    if not ok:
        await message.answer(msg)
        return
    await state.clear()
    await _show_reklama_detail(message, message.from_user.id, ad_id)
