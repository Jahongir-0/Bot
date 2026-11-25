"""Zaxira (backup): yuklab olish, tiklash, tozalash, xotira."""

import io
import json
import logging
import re
import zipfile
from typing import Optional

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from admin.filters import IsAdminFilter
from admin.v2.common import AdminV2States, _back_home_btn, _cancel_kb, _deny_if_no_perm
from database.session import get_session
from models.base import ensure_utc
from models.card import Card
from models.v2_features import BackupRecord
from services.community_service import CommunityService
from services.security_backup_service import SecurityBackupService
from utils.callback_data import AdmV2Cb
from utils.formatting import esc
from utils.tg_helpers import NotCommandFilter, edit_or_answer, is_super_admin
from utils.timeutils import fmt_tashkent

logger = logging.getLogger(__name__)
router = Router(name="admin_v2_backup")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


# ===========================================================================
# 13. MEMORY / BACKUP (sec="memory", List + Download + Date Search + Clean + Restore)
# ===========================================================================
async def _show_memory_home(target, owner_id: int, date_filter: Optional[str] = None) -> None:
    async with get_session() as session:
        backups = await SecurityBackupService.list_backups(session, limit=20)
    if date_filter:
        backups = [b for b in backups if ensure_utc(b.created_at).strftime("%Y-%m-%d") == date_filter]
    lines = [f"💾 <b>XOTIRA VA BACKUP TIZIMI</b>" + (f" (Sana: <code>{date_filter}</code>)" if date_filter else "") + "\n"]
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="💾 Hozir Backup (.zip)", callback_data=AdmV2Cb(owner_id=owner_id, sec="memory", act="backup_now").pack()),
        InlineKeyboardButton(text="📅 Sana bo‘yicha", callback_data=AdmV2Cb(owner_id=owner_id, sec="memory", act="date_q").pack()),
    )
    for b_rec in backups[:8]:
        dt_s = fmt_tashkent(b_rec.created_at, "%d.%m %H:%M")
        kb_size = max(1, b_rec.file_size_bytes // 1024)
        lines.append(f"• <code>{dt_s}</code> — {kb_size} KB ({b_rec.backup_type})")
        b.row(
            InlineKeyboardButton(
                text=f"⬇️ {dt_s} ({kb_size} KB, {b_rec.backup_type})",
                callback_data=AdmV2Cb(owner_id=owner_id, sec="memory", act="dl", tid=b_rec.id).pack(),
            )
        )
    if not backups:
        lines.append("📭 Backuplar topilmadi.")
    b.row(
        InlineKeyboardButton(text="🧹 Eskilarini tozalash", callback_data=AdmV2Cb(owner_id=owner_id, sec="memory", act="clean").pack()),
        InlineKeyboardButton(text="♻️ Restore (.zip)", callback_data=AdmV2Cb(owner_id=owner_id, sec="memory", act="restore").pack()),
    )
    b.row(_back_home_btn(owner_id))
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


@router.callback_query(AdmV2Cb.filter(F.sec == "memory"))
async def cb_v2_memory(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "memory"):
        return
    act = callback_data.act
    owner_id = callback.from_user.id

    if act == "backup_now":
        async with get_session() as session:
            rec = await SecurityBackupService.create_full_backup_zip(session, "manual", owner_id)
            await session.commit()
        try:
            await callback.bot.send_document(
                owner_id,
                FSInputFile(rec.storage_path),
                caption=f"💾 <b>To‘liq ZIP Backup</b>\n📦 {rec.filename} ({rec.file_size_bytes:,} bytes)",
            )
            await callback.answer("✅ Backup yaratildi va yuborildi!", show_alert=True)
        except Exception:
            await callback.answer(f"✅ Backup saqlandi: {rec.filename}", show_alert=True)
        await _show_memory_home(callback, owner_id)
        return

    if act == "dl":
        async with get_session() as session:
            rec = await session.get(BackupRecord, callback_data.tid)
        if rec is None:
            await callback.answer("Backup topilmadi.", show_alert=True)
            return
        try:
            await callback.bot.send_document(
                owner_id,
                FSInputFile(rec.storage_path),
                caption=f"💾 <b>{esc(rec.filename)}</b> ({rec.file_size_bytes:,} bytes)",
            )
            await callback.answer("📥 Fayl yuborildi!")
        except Exception as exc:
            await callback.answer(f"❌ Faylni yuborib bo‘lmadi: {str(exc)[:60]}", show_alert=True)
        return

    if act == "date_q":
        await state.set_state(AdminV2States.waiting_backup_date)
        await state.update_data(owner_tg_id=owner_id)
        await edit_or_answer(callback, "📅 Backup sanasini yozing (<code>YYYY-MM-DD</code>, masalan: <code>2026-11-17</code>):", reply_markup=_cancel_kb(owner_id, "memory", "v"))
        return

    if act == "clean":
        async with get_session() as session:
            n = await SecurityBackupService.cleanup_old_backups(session, 14)
            await session.commit()
        await callback.answer(f"🧹 {n} ta eski backup o‘chirildi.", show_alert=True)
        await _show_memory_home(callback, owner_id)
        return

    if act == "restore":
        if not is_super_admin(callback.from_user):
            await callback.answer("⛔ Faqat Super Admin tiklay oladi.", show_alert=True)
            return
        await state.set_state(AdminV2States.waiting_backup_restore)
        await state.update_data(owner_tg_id=owner_id)
        await edit_or_answer(
            callback,
            "♻️ <b>ZAXIRADAN TIKLASH</b>\n\nAvval joriy bazadan avtomatik zaxira olinadi.\n<code>.zip</code> zaxira faylini yuboring:",
            reply_markup=_cancel_kb(owner_id, "memory", "v"),
        )
        return

    await state.clear()
    await _show_memory_home(callback, owner_id)


@router.message(AdminV2States.waiting_backup_date, NotCommandFilter())
async def fsm_v2_backup_date(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        await message.answer("⚠️ Format: <code>YYYY-MM-DD</code>")
        return
    await state.clear()
    await _show_memory_home(message, message.from_user.id, date_filter=raw)


@router.message(AdminV2States.waiting_backup_restore, F.document)
async def fsm_v2_backup_restore(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id or not is_super_admin(message.from_user):
        return
    doc = message.document
    if not doc or not (doc.file_name or "").lower().endswith(".zip"):
        await message.answer("⚠️ Faqat backup .zip fayl yuboring.")
        return
    await state.clear()
    buf = io.BytesIO()
    await message.bot.download(doc, destination=buf)
    try:
        with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as zf:
            payload = json.loads(zf.read("database_backup.json").decode("utf-8"))
    except Exception as exc:
        await message.answer(f"❌ Backup fayli o‘qilmadi: {esc(str(exc))}")
        return

    async with get_session() as session:
        await SecurityBackupService.create_full_backup_zip(session, "pre_restore", message.from_user.id)
        restored_cards = 0
        for row in payload.get("cards", []):
            cid = row.get("id")
            if cid and await session.get(Card, cid) is None and row.get("card_code"):
                session.add(
                    Card(
                        character_name=row["character_name"],
                        anime_name=row["anime_name"],
                        anime_id=row.get("anime_id", "an_01"),
                        card_code=row["card_code"],
                        rarity=row["rarity"],
                        media_type=row.get("media_type", "Anime"),
                        image=row["image"],
                        base_value=int(row.get("base_value", 1000)),
                        drop_limit=int(row.get("drop_limit", 1)),
                    )
                )
                restored_cards += 1
        await CommunityService.log_audit(session, message.from_user.id, "backup_restore", "database", details={"restored_cards": restored_cards})
        await session.commit()
    await message.answer(f"✅ Pre-restore backup saqlandi va tiklash yakunlandi! Tiklangan yangi kartalar: <b>{restored_cards}</b>")
