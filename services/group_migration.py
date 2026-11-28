"""Guruh supergruppaga aylanganda (Telegram chat ID o'zgaradi) bazani xavfsiz yangilash.

Eski kod `UPDATE groups SET telegram_chat_id=<yangi>` qilardi va yangi ID bilan qator allaqachon
mavjud bo'lsa `ix_groups_telegram_chat_id` unikal cheklovi buzilardi (IntegrityError).
"""
from __future__ import annotations

import logging
from typing import Iterable, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.group import Group

logger = logging.getLogger(__name__)

_ARCHIVED_BASE = -9_000_000_000_000


async def apply_group_migrations(session: AsyncSession, pairs: Iterable[Tuple[int, int]]) -> None:
    """pairs: [(eski_chat_id, yangi_chat_id), ...]"""
    for old_cid, new_cid in pairs:
        if old_cid == new_cid:
            continue
        old = (await session.execute(select(Group).where(Group.telegram_chat_id == old_cid))).scalar_one_or_none()
        if old is None:
            continue
        new = (await session.execute(select(Group).where(Group.telegram_chat_id == new_cid))).scalar_one_or_none()
        if new is None:
            old.telegram_chat_id = new_cid
        else:
            # Yangi ID bilan qator bor: eskisi arxivlanadi (tarixi saqlanadi), unikal cheklov buzilmaydi
            old.is_active = False
            old.bot_status = "left"
            old.status_reason = f"Supergruppaga ko‘chdi ({new_cid})"[:255]
            old.telegram_chat_id = _ARCHIVED_BASE - int(old.id)
            if not new.is_active:
                new.is_active = True
                new.bot_status = "active"
        await session.flush()
        logger.info("Guruh ko'chirildi: %s -> %s", old_cid, new_cid)
