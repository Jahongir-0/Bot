import hashlib
import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject

from database.redis_client import get_lock_manager

logger = logging.getLogger(__name__)


class CallbackSecurityMiddleware(BaseMiddleware):
    """
    Protects against:
    1. Duplicate callback / double-tap replay.
    2. Unauthorized users clicking another user's inline buttons (owner_id verification).
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)

        raw_data = event.data or ""
        msg_id = event.message.message_id if event.message else 0
        user_id = event.from_user.id

        # Debounce identical callback replay within 0.8 seconds
        cb_hash = hashlib.sha1(raw_data.encode("utf-8")).hexdigest()[:12]
        dedupe_key = f"cb_replay:{user_id}:{msg_id}:{cb_hash}"
        if not await get_lock_manager().check_and_set_cooldown(dedupe_key, seconds=0.8):
            await event.answer("⏳ So‘rov bajarilmoqda...", show_alert=False)
            return None

        # Ownership enforcement for callbacks formatted with prefix:owner_id:...
        # `drop` is open to all group users; `trf` allows both sender (cancel) and recipient (confirm/cancel) checked by TransferService.
        parts = raw_data.split(":")
        public_prefixes = {"drop", "trf"}
        if len(parts) >= 2 and parts[0] not in public_prefixes and parts[1].isdigit():
            owner_id = int(parts[1])
            if owner_id != user_id:
                logger.warning(
                    "Blocked unauthorized callback click: actor=%s owner=%s data=%s",
                    user_id,
                    owner_id,
                    raw_data,
                )
                await event.answer(
                    "⛔ Bu tugma faqat egasi uchun! O‘z profilingizni /me orqali oching.",
                    show_alert=True,
                )
                return None

        return await handler(event, data)
