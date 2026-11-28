"""Guruh chatini toza saqlash.

Guruhda foydalanuvchi ma'lumot beruvchi buyruq (/daily, /top, /me, /cards va h.k.) yuborsa:
  - buyruq xabari bir necha soniyadan keyin o'chadi,
  - bot shu buyruq uchun yuborgan javoblar CLEAN_DELAY soniyadan keyin o'chadi.

Drop, viktorina, auksion, savdo, e'lon kabi MUHIM xabarlarga tegilmaydi (faqat ro'yxatdagi buyruqlar).
Botda "xabarlarni o'chirish" ruxsati bo'lmasa, hech narsa o'chmaydi (xato ham bermaydi).
Har qanday xatolik bo'lsa funksiya jim o'chadi va bot odatdagidek ishlayveradi.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any, Awaitable, Callable, Dict, List, Optional

from aiogram import BaseMiddleware
from aiogram.types import Message

from utils.tg_helpers import schedule_delete

logger = logging.getLogger(__name__)

CLEAN_DELAY = 120.0     # bot javobi necha soniyadan keyin o'chadi
USER_CMD_DELAY = 4.0    # foydalanuvchi buyrug'i necha soniyadan keyin o'chadi

CLEAN_COMMANDS = {
    "daily", "me", "profil", "profilim", "cards", "collection", "top", "help", "menu",
    "quests", "vazifalar", "search", "event", "events", "ref", "referral",
    "achievements", "season", "notif", "notifications", "vip", "stars", "wishlist",
}

_collector: ContextVar[Optional[List[int]]] = ContextVar("group_cleanup_collector", default=None)

try:
    from aiogram.client.session.middlewares.base import BaseRequestMiddleware
    from aiogram.methods import (
        SendAnimation, SendDice, SendDocument, SendMessage, SendPhoto, SendSticker, SendVideo,
    )

    _SEND_METHODS = (SendMessage, SendPhoto, SendDocument, SendSticker, SendDice, SendAnimation, SendVideo)

    class GroupCleanupRequestMiddleware(BaseRequestMiddleware):
        """Bot yuborgan xabarlar ID sini yig'adi (faqat buyruq ishlayotgan paytda)."""

        async def __call__(self, make_request, bot, method):
            result = await make_request(bot, method)
            try:
                bucket = _collector.get()
                if bucket is not None and isinstance(method, _SEND_METHODS):
                    mid = getattr(result, "message_id", None)
                    if mid:
                        bucket.append(mid)
            except Exception:  # pragma: no cover
                pass
            return result

    AVAILABLE = True
except Exception as exc:  # aiogram versiyasi boshqacha bo'lsa funksiya o'chadi
    logger.warning("Group cleanup o'chirildi (aiogram importi): %s", exc)
    GroupCleanupRequestMiddleware = None  # type: ignore
    AVAILABLE = False


def _command_name(text: str) -> str:
    first = (text or "").split(maxsplit=1)[0] if text else ""
    return first[1:].split("@", 1)[0].lower() if first.startswith("/") else ""


class GroupCleanupMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]], event: Message, data: Dict[str, Any]) -> Any:
        if (
            not isinstance(event, Message)
            or event.chat.type not in ("group", "supergroup")
            or _command_name(event.text or "") not in CLEAN_COMMANDS
        ):
            return await handler(event, data)

        bucket: List[int] = []
        token = _collector.set(bucket)
        try:
            return await handler(event, data)
        finally:
            _collector.reset(token)
            try:
                # Foydalanuvchi yuborgan /buyruq o'chadi, botning javobi guruhda saqlanib qoladi
                schedule_delete(event.bot, event.chat.id, [event.message_id], delay=USER_CMD_DELAY)
            except Exception:  # pragma: no cover
                pass
