"""Foydalanuvchiga ko'rsatiladigan barcha vaqtlar Toshkent vaqti (UTC+5) bilan chiqadi."""
from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    from zoneinfo import ZoneInfo

    TASHKENT = ZoneInfo("Asia/Tashkent")
except Exception:  # tzdata o'rnatilmagan bo'lsa (masalan Windows) - qat'iy UTC+5
    TASHKENT = timezone(timedelta(hours=5), "Asia/Tashkent")


def to_tashkent(dt: Optional[datetime]) -> Optional[datetime]:
    """Naive (SQLite) yoki aware datetime ni Toshkent vaqtiga o'tkazadi."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TASHKENT)


def fmt_tashkent(dt: Optional[datetime], fmt: str = "%d.%m.%Y %H:%M") -> str:
    local = to_tashkent(dt)
    return local.strftime(fmt) if local is not None else "—"
