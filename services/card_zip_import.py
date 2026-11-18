"""ZIP orqali kartalarni ommaviy qo'shish (v3).

ZIP ichida:
  cards.json  (yoki cards.csv)   - kartalar ro'yxati
  images/     (yoki istalgan papka) - rasmlar

MAJBURIY maydonlar (bittasi bo'lmasa karta QO'SHILMAYDI):
  image, character_name, media_type, media_name, rank_id

Rasmlar Telegram serveriga yuklanib, ularning file_id si bazaga yoziladi.
Shu sabab Railway qayta ishga tushsa ham rasmlar yo'qolmaydi.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import BufferedInputFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.card import Card
from services.card_service import CardService
from services.community_service import CommunityService
from services.settings_service import SettingsService
from utils.constants import MEDIA_TYPES, RARITY_REGISTRY

logger = logging.getLogger(__name__)

MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_ZIP_ENTRIES = 2000
MAX_IMAGE_BYTES = 15 * 1024 * 1024
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
UPLOAD_PAUSE_SEC = 0.35

# ---- maydon nomlari (sinonimlar) ----
FIELD_ALIASES: Dict[str, Tuple[str, ...]] = {
    "image": ("image", "image_file", "file", "photo", "rasm"),
    "character_name": ("character_name", "character", "name", "personaj"),
    "media_type": ("media_type", "type", "tur"),
    "media_name": ("media_name", "anime_name", "anime", "media", "title", "nom"),
    "rank_id": ("rank_id", "rank", "rarity", "rank_name"),
}
REQUIRED_LABELS = {
    "image": "image (rasm fayl nomi)",
    "character_name": "character_name (personaj ismi)",
    "media_type": "media_type (turi)",
    "media_name": "media_name (anime/manga/kino nomi)",
    "rank_id": "rank_id (rank raqami)",
}
OPTIONAL_META = ("description", "birthday", "age", "height", "weight", "personality", "history", "abilities")

_MEDIA_LOOKUP = {m.lower(): m for m in MEDIA_TYPES}
_MEDIA_LOOKUP.update({"tv": "TV Series", "series": "TV Series", "tvseries": "TV Series", "film": "Movie", "kino": "Movie", "comics": "Comic"})


def rank_table() -> List[Tuple[int, str]]:
    """[(rank_id, nom)] - rank_id = rankning tartib raqami (nomi o'zgarsa ham id o'zgarmaydi)."""
    return [(m.order, m.name) for m in sorted(RARITY_REGISTRY.values(), key=lambda x: x.order)]


def resolve_rank(value: Any) -> Optional[str]:
    """rank_id (1,2,3..) yoki rank nomi -> rarity nomi. Topilmasa None."""
    raw = str(value).strip() if value is not None else ""
    if not raw:
        return None
    if raw.isdigit():
        for rid, name in rank_table():
            if rid == int(raw):
                return name
        return None
    low = raw.lower()
    for _, name in rank_table():
        if name.lower() == low:
            return name
    return None


@dataclass
class ZipImportReport:
    total_files: int = 0
    total_cards: int = 0
    total_images: int = 0
    added: int = 0
    duplicates: int = 0
    failed: int = 0
    failed_files: List[str] = field(default_factory=list)  # eski kod bilan moslik
    added_by_rank: Dict[str, int] = field(default_factory=dict)
    added_by_media: Dict[str, int] = field(default_factory=dict)
    fatal: Optional[str] = None


@dataclass
class _Prepared:
    label: str
    character_name: str
    media_type: str
    media_name: str
    rarity: str
    image_bytes: bytes
    base_value: Optional[int]
    drop_limit: Optional[int]
    aliases: List[str]
    meta: Dict[str, Any]


def _safe_member(name: str) -> bool:
    if not name or name.startswith("/") or "\\" in name:
        return False
    return ".." not in PurePosixPath(name).parts


def _pick(rec: Dict[str, Any], key: str) -> Any:
    low = {str(k).strip().lower(): v for k, v in rec.items()}
    for alias in FIELD_ALIASES[key]:
        v = low.get(alias)
        if v is not None and str(v).strip() != "":
            return v
    return None


def _to_jpeg(raw: bytes) -> bytes:
    """Rasmni tekshiradi va Telegram uchun JPEG ga o'giradi (shaffoflik -> oq fon)."""
    from PIL import Image  # kech import: faqat kerak bo'lganda

    with Image.open(io.BytesIO(raw)) as probe:
        probe.verify()
    with Image.open(io.BytesIO(raw)) as im:
        im.load()
        if im.width < 50 or im.height < 50:
            raise ValueError("rasm juda kichik (kamida 50x50)")
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        im.thumbnail((2560, 2560))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=90, optimize=True)
        return out.getvalue()


def _parse_manifest(infos_data: Dict[str, bytes], report: ZipImportReport) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for name, data in infos_data.items():
        ext = PurePosixPath(name).suffix.lower()
        base = PurePosixPath(name).name
        if ext == ".json":
            try:
                parsed = json.loads(data.decode("utf-8-sig"))
            except Exception as exc:
                report.fatal = f"{base}: JSON formati xato ({exc})"
                return []
            if isinstance(parsed, dict):
                parsed = parsed.get("cards") or []
            if not isinstance(parsed, list):
                report.fatal = f"{base}: ro'yxat (list) bo'lishi kerak"
                return []
            records.extend(p for p in parsed if isinstance(p, dict))
        elif ext == ".csv":
            try:
                text = data.decode("utf-8-sig")
                dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t") if text.strip() else csv.excel
                records.extend(dict(r) for r in csv.DictReader(io.StringIO(text), dialect=dialect))
            except Exception as exc:
                report.fatal = f"{base}: CSV formati xato ({exc})"
                return []
    return records


async def _upload_photo(bot: Bot, chat_id: int, jpeg: bytes, name: str) -> str:
    """Rasmni admin chatiga yuborib file_id oladi, xabarni darhol o'chiradi."""
    for attempt in range(3):
        try:
            msg = await bot.send_photo(
                chat_id=chat_id,
                photo=BufferedInputFile(jpeg, filename=f"{name[:40] or 'card'}.jpg"),
                disable_notification=True,
            )
            file_id = msg.photo[-1].file_id
            try:
                await bot.delete_message(chat_id, msg.message_id)
            except Exception:
                pass
            return file_id
        except TelegramRetryAfter as exc:
            await asyncio.sleep(float(exc.retry_after) + 0.5)
    raise RuntimeError("Telegram rasmni qabul qilmadi (limit). Keyinroq urinib ko'ring.")


async def import_cards_from_zip(
    session: AsyncSession,
    zip_bytes: bytes,
    admin_tg_id: int,
    bot: Bot,
    progress: Optional[Callable[[str], Awaitable[None]]] = None,
) -> ZipImportReport:
    report = ZipImportReport()

    if len(zip_bytes) > MAX_ZIP_BYTES:
        report.fatal = "ZIP hajmi 50 MB dan katta."
        return report
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        report.fatal = "Bu yaroqsiz ZIP fayl (ochilmadi)."
        return report

    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir() and not PurePosixPath(i.filename).name.startswith(("._", "."))]
        infos = [i for i in infos if "__MACOSX" not in i.filename]
        if len(infos) > MAX_ZIP_ENTRIES:
            report.fatal = f"Fayllar soni juda ko'p ({len(infos)} > {MAX_ZIP_ENTRIES})."
            return report
        if sum(i.file_size for i in infos) > MAX_ZIP_BYTES * 2:
            report.fatal = "ZIP ichidagi fayllar hajmi xavfsiz limitdan oshib ketdi."
            return report

        report.total_files = len(infos)
        manifests: Dict[str, bytes] = {}
        images: Dict[str, bytes] = {}  # kalit: kichik harfli yo'l va fayl nomi
        for info in infos:
            if not _safe_member(info.filename):
                report.failed += 1
                report.failed_files.append(f"{info.filename} — xavfli fayl yo'li, o'tkazib yuborildi")
                continue
            ext = PurePosixPath(info.filename).suffix.lower()
            if ext in IMG_EXTS:
                if info.file_size > MAX_IMAGE_BYTES:
                    report.failed += 1
                    report.failed_files.append(f"{info.filename} — rasm 15 MB dan katta")
                    continue
                data = zf.read(info)
                report.total_images += 1
                images[info.filename.lower()] = data
                images.setdefault(PurePosixPath(info.filename).name.lower(), data)
            elif ext in (".json", ".csv"):
                manifests[info.filename] = zf.read(info)

    if not manifests:
        report.fatal = "ZIP ichida cards.json yoki cards.csv topilmadi."
        return report

    records = _parse_manifest(manifests, report)
    if report.fatal:
        return report
    if not records:
        report.fatal = "cards.json ichida bitta ham karta yo'q."
        return report

    report.total_cards = len(records)
    rcfg = await SettingsService.get_rarity_config(session)

    # ---------------- 1-bosqich: HAMMASINI tekshirish (hech narsa yozilmaydi) ----------------
    prepared: List[_Prepared] = []
    seen_keys: set = set()
    for idx, rec in enumerate(records, start=1):
        who = str(_pick(rec, "character_name") or "").strip()
        label = f"#{idx} {who}" if who else f"#{idx}"

        missing = [REQUIRED_LABELS[k] for k in REQUIRED_LABELS if _pick(rec, k) is None]
        if missing:
            report.failed += 1
            report.failed_files.append(f"{label} — yetishmaydi: {', '.join(missing)}")
            continue

        rarity = resolve_rank(_pick(rec, "rank_id"))
        if rarity is None:
            ids = ", ".join(f"{i}={n}" for i, n in rank_table())
            report.failed += 1
            report.failed_files.append(f"{label} — rank_id noto'g'ri ({_pick(rec, 'rank_id')}). Mumkin: {ids}")
            continue

        mt_raw = str(_pick(rec, "media_type")).strip()
        mtype = _MEDIA_LOOKUP.get(mt_raw.lower().replace(" ", "")) or _MEDIA_LOOKUP.get(mt_raw.lower())
        if mtype is None:
            report.failed += 1
            report.failed_files.append(f"{label} — media_type noto'g'ri ({mt_raw}). Mumkin: {', '.join(MEDIA_TYPES)}")
            continue

        img_ref = str(_pick(rec, "image")).strip().replace("\\", "/").lstrip("./")
        raw = images.get(img_ref.lower()) or images.get(PurePosixPath(img_ref).name.lower())
        if raw is None:
            report.failed += 1
            report.failed_files.append(f"{label} — rasm ZIP ichida topilmadi: {img_ref}")
            continue
        try:
            jpeg = _to_jpeg(raw)
        except Exception as exc:
            report.failed += 1
            report.failed_files.append(f"{label} — rasm buzilgan yoki rasm emas ({exc})")
            continue

        media_name = str(_pick(rec, "media_name")).strip()
        key = (who.lower(), media_name.lower(), rarity)
        if key in seen_keys:
            report.duplicates += 1
            continue
        seen_keys.add(key)

        def _int(v: Any) -> Optional[int]:
            try:
                n = int(str(v).replace(" ", "").replace(",", ""))
                return n if n > 0 else None
            except (TypeError, ValueError):
                return None

        aliases_raw = rec.get("aliases")
        if isinstance(aliases_raw, str):
            aliases_raw = [a.strip() for a in aliases_raw.replace("|", ",").split(",")]
        aliases = [str(a).strip() for a in (aliases_raw or []) if str(a).strip()]
        low = {str(k).strip().lower(): v for k, v in rec.items()}
        meta = {k: str(low[k]).strip() for k in OPTIONAL_META if low.get(k) not in (None, "")}

        prepared.append(
            _Prepared(
                label=label, character_name=who, media_type=mtype, media_name=media_name, rarity=rarity,
                image_bytes=jpeg, base_value=_int(low.get("base_value")), drop_limit=_int(low.get("drop_limit")),
                aliases=aliases, meta=meta,
            )
        )

    # ---------------- 2-bosqich: yuklash va bazaga yozish ----------------
    total = len(prepared)
    for n, item in enumerate(prepared, start=1):
        try:
            dup = (
                await session.execute(
                    select(Card.id).where(
                        func.lower(Card.character_name) == item.character_name.lower(),
                        func.lower(Card.anime_name) == item.media_name.lower(),
                        Card.rarity == item.rarity,
                        Card.is_deleted.is_(False),
                    )
                )
            ).first()
            if dup is not None:
                report.duplicates += 1
                continue

            file_id = await _upload_photo(bot, admin_tg_id, item.image_bytes, item.character_name)
            async with session.begin_nested():
                card = await CardService.create_card(
                    session,
                    character_name=item.character_name,
                    anime_name=item.media_name,
                    image=file_id,
                    rarity=item.rarity,
                    base_value=item.base_value or rcfg.get(item.rarity, {}).get("value", 1000),
                    drop_limit=item.drop_limit or rcfg.get(item.rarity, {}).get("drops", 1),
                    media_type=item.media_type,
                    aliases=item.aliases,
                    metadata_json=item.meta,
                )
                card.image_file_id = file_id
            report.added += 1
            report.added_by_rank[item.rarity] = report.added_by_rank.get(item.rarity, 0) + 1
            report.added_by_media[item.media_type] = report.added_by_media.get(item.media_type, 0) + 1
        except Exception as exc:
            logger.warning("ZIP import: %s qo'shilmadi: %s", item.label, exc)
            report.failed += 1
            report.failed_files.append(f"{item.label} — {str(exc)[:120]}")

        if progress and (n % 10 == 0 or n == total):
            try:
                await progress(f"⏳ Yuklanmoqda: {n}/{total} (qo'shildi: {report.added})")
            except Exception:
                pass
        await asyncio.sleep(UPLOAD_PAUSE_SEC)

    await CommunityService.log_audit(
        session, admin_tg_id, "zip_import", "cards",
        details={"cards": report.total_cards, "added": report.added, "duplicates": report.duplicates, "failed": report.failed},
    )
    await session.flush()
    return report
