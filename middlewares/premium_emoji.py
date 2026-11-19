"""Chiqish xabarlaridagi emojilarni premium emojiga almashtiradi (xato bo'lsa oddiy matnga qaytadi)."""
from __future__ import annotations

import logging

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import (
    EditMessageCaption,
    EditMessageText,
    SendAnimation,
    SendDocument,
    SendMessage,
    SendPhoto,
    SendVideo,
)

from utils import premium_emoji as pe

logger = logging.getLogger(__name__)

_METHODS = (SendMessage, EditMessageText, SendPhoto, SendDocument, SendAnimation, SendVideo, EditMessageCaption)


class PremiumEmojiRequestMiddleware(BaseRequestMiddleware):
    async def __call__(self, make_request, bot, method):
        await pe.ensure_loaded()
        if not pe.current_map() or not isinstance(method, _METHODS):
            return await make_request(bot, method)

        pm = getattr(method, "parse_mode", None)
        if pm is None or (isinstance(pm, str) and pm.upper() != "HTML"):
            return await make_request(bot, method)

        originals = {}
        try:
            for field, ent_field in (("text", "entities"), ("caption", "caption_entities")):
                val = getattr(method, field, None)
                if isinstance(val, str) and not getattr(method, ent_field, None):
                    new = pe.apply(val)
                    if new != val:
                        originals[field] = val
                        setattr(method, field, new)
        except Exception:
            logger.exception("Premium emoji almashtirishda xato")
            for k, v in originals.items():
                setattr(method, k, v)
            return await make_request(bot, method)

        if not originals:
            return await make_request(bot, method)
        try:
            return await make_request(bot, method)
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                raise
            logger.warning("Premium emoji bilan yuborilmadi, oddiy emoji bilan qayta urinaman: %s", exc)
            for k, v in originals.items():
                setattr(method, k, v)
            return await make_request(bot, method)
