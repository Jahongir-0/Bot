"""Karta rasmini tekshirish: noto'g'ri URL yoki rasm bo'lmagan format qabul qilinmaydi."""
import ipaddress
import logging
from typing import Optional, Tuple
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger(__name__)

ALLOWED_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
ALLOWED_EXT = (".jpg", ".jpeg", ".png", ".webp")
MAX_BYTES = 5 * 1024 * 1024  # Telegram URL orqali rasm uchun 5 MB limit
TIMEOUT = aiohttp.ClientTimeout(total=8)

ERR_FORMAT = (
    "⚠️ Rasm noto‘g‘ri. Rasmni Telegram’da <b>foto</b> sifatida yuboring yoki to‘g‘ri "
    "<code>https://...jpg/png/webp</code> havola yozing."
)


def _host_is_private(host: str) -> bool:
    if host in ("localhost",) or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except ValueError:
        return False


async def validate_image_url(url: str) -> Tuple[bool, Optional[str]]:
    """(ok, xato_matni). URL haqiqatan ham jpg/png/webp rasmga olib borishini tarmoq orqali tekshiradi."""
    url = (url or "").strip()
    if len(url) > 500 or " " in url:
        return False, ERR_FORMAT
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or not parsed.hostname:
        return False, ERR_FORMAT
    if _host_is_private(parsed.hostname):
        return False, "⚠️ Bu manzildan rasm olib bo‘lmaydi (ichki tarmoq)."

    headers = {"User-Agent": "Mozilla/5.0 (AnimeCardBot image check)", "Range": "bytes=0-2047"}
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as http:
            async with http.get(url, headers=headers, allow_redirects=True) as resp:
                if resp.status not in (200, 206):
                    return False, f"⚠️ Havola ochilmadi (HTTP {resp.status}). To‘g‘ri rasm havolasini yuboring."
                ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype not in ALLOWED_TYPES:
                    return False, (
                        f"⚠️ Bu havola rasm emas yoki format qo‘llab-quvvatlanmaydi ({ctype or 'noma’lum'}). "
                        "Faqat JPG, PNG yoki WEBP."
                    )
                total = resp.headers.get("Content-Range", "").split("/")[-1] or resp.headers.get("Content-Length", "")
                if total.isdigit() and int(total) > MAX_BYTES:
                    return False, "⚠️ Rasm hajmi 5 MB dan katta. Kichikroq rasm yuboring."
                head = await resp.content.read(16)
    except (aiohttp.ClientError, TimeoutError) as exc:
        logger.info("Image URL check failed: %s", exc)
        return False, "⚠️ Havolaga ulanib bo‘lmadi. To‘g‘ri rasm havolasini yuboring."
    except Exception as exc:  # pragma: no cover
        logger.warning("Image URL unexpected error: %s", exc)
        return False, ERR_FORMAT

    signatures = (b"\xff\xd8\xff", b"\x89PNG", b"RIFF")
    if not any(head.startswith(sig) for sig in signatures):
        return False, "⚠️ Havola rasm faylini bermadi (fayl mazmuni rasm emas)."
    return True, None
