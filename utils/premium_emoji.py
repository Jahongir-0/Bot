"""Premium (custom) emoji qo'llab-quvvatlash.

Admin /premium_emoji buyrug'i orqali premium emojilarni yuboradi, bot ularning ID sini saqlaydi.
Shundan keyin botning chiqish matnlaridagi oddiy emojilar avtomatik premium emojiga almashtiriladi.
Premium emoji ko'rsatilmasa (ruxsat yo'q yoki foydalanuvchida Premium yo'q) oddiy emoji ko'rinadi.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, Optional

logger = logging.getLogger(__name__)

KEY = "premium_emoji_map"
_MAP: Dict[str, str] = {}
_PATTERN: Optional[re.Pattern] = None
_LOADED = False
_TAG = re.compile(r"(<[^>]*>)")


def _norm(ch: str) -> str:
    return ch.replace("\ufe0f", "").strip()


def _rebuild() -> None:
    global _PATTERN
    if not _MAP:
        _PATTERN = None
        return
    keys = sorted(_MAP.keys(), key=len, reverse=True)
    _PATTERN = re.compile("(" + "|".join(re.escape(k) for k in keys) + ")\ufe0f?")


async def ensure_loaded() -> None:
    global _LOADED, _MAP
    if _LOADED:
        return
    _LOADED = True
    try:
        from database.session import get_session
        from services.settings_service import SettingsService

        async with get_session() as session:
            raw = await SettingsService.get_value(session, KEY, {})
        if isinstance(raw, dict):
            _MAP = {_norm(k): str(v) for k, v in raw.items() if k and v}
            _rebuild()
    except Exception:
        logger.exception("Premium emoji xaritasi yuklanmadi")


async def save_map(new_pairs: Dict[str, str], replace: bool = False) -> int:
    """Saqlaydi va jami nechta emoji bog'langanini qaytaradi."""
    global _MAP, _LOADED
    from database.session import get_session
    from services.settings_service import SettingsService

    async with get_session() as session:
        current = {} if replace else dict(await SettingsService.get_value(session, KEY, {}) or {})
        for k, v in new_pairs.items():
            current[_norm(k)] = v
        await SettingsService.set_value(session, KEY, current)
        await session.commit()
    _MAP = {_norm(k): str(v) for k, v in current.items() if k and v}
    _LOADED = True
    _rebuild()
    return len(_MAP)


def current_map() -> Dict[str, str]:
    return dict(_MAP)


def extract_pairs(text: str, entities) -> Dict[str, str]:
    """Xabardagi custom_emoji entity lardan {oddiy emoji: custom_emoji_id} juftliklarini oladi."""
    pairs: Dict[str, str] = {}
    raw = (text or "").encode("utf-16-le")
    for ent in entities or []:
        if getattr(ent, "type", "") != "custom_emoji" or not getattr(ent, "custom_emoji_id", None):
            continue
        ch = raw[ent.offset * 2 : (ent.offset + ent.length) * 2].decode("utf-16-le", errors="ignore")
        ch = _norm(ch)
        if ch:
            pairs[ch] = ent.custom_emoji_id
    return pairs


def apply(text: str) -> str:
    """HTML matndagi oddiy emojilarni <tg-emoji> bilan almashtiradi (code/pre ichidan tashqari)."""
    if not text or _PATTERN is None:
        return text
    out = []
    skip = 0
    for part in _TAG.split(text):
        if part.startswith("<") and part.endswith(">"):
            low = part.lower()
            if low.startswith(("<code", "<pre")):
                skip += 1
            elif low.startswith(("</code", "</pre")):
                skip = max(0, skip - 1)
            out.append(part)
        elif skip:
            out.append(part)
        else:
            out.append(_PATTERN.sub(lambda m: f'<tg-emoji emoji-id="{_MAP[m.group(1)]}">{m.group(1)}</tg-emoji>', part))
    return "".join(out)
