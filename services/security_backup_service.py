import csv
import io
import json
import logging
import os
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.admin import Ban, Setting
from models.base import ensure_utc, utcnow
from models.card import Card
from models.group import Group
from models.market import PendingTransfer
from models.user import User
from models.user_card import UserCard
from models.v2_features import BackupRecord, SecurityIncident
from services.card_service import CardService
from services.community_service import CommunityService
from services.settings_service import SettingsService
from utils.constants import DEFAULT_ANTICHEAT_CONFIG, MEDIA_TYPES, RARITY_REGISTRY

logger = logging.getLogger(__name__)

_BOT_ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = _BOT_ROOT / "backups"
MEDIA_CARDS_DIR = _BOT_ROOT / "media_cards"
MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_ZIP_ENTRIES = 2000


@dataclass
class ZipImportReport:
    total_files: int = 0
    total_cards: int = 0
    total_images: int = 0
    added: int = 0
    duplicates: int = 0
    failed: int = 0
    failed_files: List[str] = field(default_factory=list)


class SecurityBackupService:
    # ------------------------------------------------------------------
    # Anti-Cheat & Security Signals (real DB/Telegram signals only)
    # ------------------------------------------------------------------
    @staticmethod
    async def record_signal(
        session: AsyncSession,
        user: Optional[User],
        incident_type: str,
        severity: str,
        score_delta: int,
        description: str,
        evidence: Optional[dict] = None,
        group_id: Optional[int] = None,
    ) -> SecurityIncident:
        cfg = await SettingsService.get_value(session, "anticheat_config", DEFAULT_ANTICHEAT_CONFIG)
        action = "logged"
        if user is not None and isinstance(cfg, dict) and cfg.get("enabled", True):
            user.suspicion_score = max(0, int(user.suspicion_score or 0) + int(score_delta))
            if user.suspicion_score >= int(cfg.get("ban_threshold", 90)):
                user.is_frozen = True
                action = "frozen"
            elif user.suspicion_score >= int(cfg.get("freeze_threshold", 60)):
                user.is_frozen = True
                action = "frozen"
            elif user.suspicion_score >= int(cfg.get("warn_threshold", 30)):
                action = "warned"

        inc = SecurityIncident(
            user_id=user.id if user else None,
            group_id=group_id,
            incident_type=incident_type[:48],
            severity=severity[:16],
            score_delta=int(score_delta),
            description=description,
            evidence=evidence or {},
            action_taken=action,
        )
        session.add(inc)
        await session.flush()
        return inc

    @staticmethod
    async def check_claim_speed(
        session: AsyncSession, user: User, drop_created_at: datetime, group_id: int
    ) -> None:
        elapsed = (utcnow() - ensure_utc(drop_created_at)).total_seconds()
        cfg = await SettingsService.get_value(session, "anticheat_config", DEFAULT_ANTICHEAT_CONFIG)
        min_sec = float((cfg or {}).get("min_claim_seconds", 1.2))
        if elapsed < min_sec:
            await SecurityBackupService.record_signal(
                session,
                user,
                "fast_claim_anomaly",
                "medium",
                15,
                f"Drop {elapsed:.2f}s ichida olindi (limit: {min_sec}s)",
                {"elapsed_sec": round(elapsed, 2)},
                group_id=group_id,
            )

    @staticmethod
    async def check_mutual_transfer_loop(
        session: AsyncSession, from_user: User, to_user_id: int
    ) -> None:
        since = utcnow() - timedelta(hours=24)
        cnt = int(
            (
                await session.execute(
                    select(func.count(PendingTransfer.id)).where(
                        PendingTransfer.created_at >= since,
                        (
                            (PendingTransfer.from_user_id == from_user.id)
                            & (PendingTransfer.to_user_id == to_user_id)
                        )
                        | (
                            (PendingTransfer.from_user_id == to_user_id)
                            & (PendingTransfer.to_user_id == from_user.id)
                        ),
                    )
                )
            ).scalar_one()
            or 0
        )
        cfg = await SettingsService.get_value(session, "anticheat_config", DEFAULT_ANTICHEAT_CONFIG)
        limit = int((cfg or {}).get("max_mutual_transfers_24h", 5))
        if cnt >= limit:
            await SecurityBackupService.record_signal(
                session,
                from_user,
                "transfer_loop",
                "high",
                25,
                f"24 soatda bir xil juftlikda {cnt} ta transfer aniqlandi.",
                {"partner_id": to_user_id, "count_24h": cnt},
            )

    @staticmethod
    async def list_incidents(session: AsyncSession, unresolved_only: bool = True, limit: int = 20) -> List[SecurityIncident]:
        stmt = select(SecurityIncident)
        if unresolved_only:
            stmt = stmt.where(SecurityIncident.is_resolved.is_(False))
        return list((await session.execute(stmt.order_by(SecurityIncident.id.desc()).limit(limit))).scalars().all())

    # ------------------------------------------------------------------
    # User & Group Temp/Perm Ban
    # ------------------------------------------------------------------
    @staticmethod
    async def ban_user(
        session: AsyncSession,
        admin_tg_id: int,
        user: User,
        reason: str,
        hours: Optional[int] = None,
    ) -> None:
        now = utcnow()
        user.is_banned = True
        user.ban_reason = reason[:500]
        user.ban_until = (now + timedelta(hours=hours)) if hours else None
        session.add(
            Ban(
                user_id=user.id,
                banned_by_telegram_id=admin_tg_id,
                reason=reason,
                is_active=True,
                is_permanent=(hours is None),
                ban_until=user.ban_until,
            )
        )
        await CommunityService.log_audit(
            session, admin_tg_id, "ban_user", "user", str(user.id), details={"reason": reason, "hours": hours}
        )
        await session.flush()

    @staticmethod
    async def ban_group(
        session: AsyncSession,
        admin_tg_id: int,
        group: Group,
        reason: str,
        hours: Optional[int] = None,
    ) -> None:
        now = utcnow()
        group.is_banned = True
        group.drop_enabled = False
        group.ban_reason = reason[:500]
        group.ban_until = (now + timedelta(hours=hours)) if hours else None
        session.add(
            Ban(
                group_id=group.id,
                banned_by_telegram_id=admin_tg_id,
                reason=reason,
                is_active=True,
                is_permanent=(hours is None),
                ban_until=group.ban_until,
            )
        )
        await CommunityService.log_audit(
            session, admin_tg_id, "ban_group", "group", str(group.id), details={"reason": reason, "hours": hours}
        )
        await session.flush()

    @staticmethod
    async def unban_group(session: AsyncSession, admin_tg_id: int, group: Group) -> None:
        group.is_banned = False
        group.ban_until = None
        group.ban_reason = None
        await session.execute(
            update(Ban)
            .where(Ban.group_id == group.id, Ban.is_active.is_(True))
            .values(is_active=False, unbanned_at=utcnow())
        )
        await CommunityService.log_audit(session, admin_tg_id, "unban_group", "group", str(group.id))
        await session.flush()

    @staticmethod
    async def expire_temp_bans(session: AsyncSession) -> int:
        now = utcnow()
        u_res = await session.execute(
            update(User)
            .where(User.is_banned.is_(True), User.ban_until.is_not(None), User.ban_until <= now)
            .values(is_banned=False, ban_until=None, ban_reason=None)
        )
        g_res = await session.execute(
            update(Group)
            .where(Group.is_banned.is_(True), Group.ban_until.is_not(None), Group.ban_until <= now)
            .values(is_banned=False, ban_until=None, ban_reason=None)
        )
        await session.execute(
            update(Ban)
            .where(Ban.is_active.is_(True), Ban.ban_until.is_not(None), Ban.ban_until <= now)
            .values(is_active=False, unbanned_at=now)
        )
        await session.flush()
        return int((u_res.rowcount or 0) + (g_res.rowcount or 0))

    # ------------------------------------------------------------------
    # ZIP Card Importer (xavfsiz: ZipSlip & size limits)
    # ------------------------------------------------------------------
    @staticmethod
    def _is_safe_zip_member(name: str) -> bool:
        if not name or name.startswith("/") or "\\" in name:
            return False
        parts = Path(name).parts
        return ".." not in parts

    @staticmethod
    async def import_cards_from_zip(
        session: AsyncSession, zip_bytes: bytes, admin_tg_id: int
    ) -> ZipImportReport:
        report = ZipImportReport()
        if len(zip_bytes) > MAX_ZIP_BYTES:
            report.failed = 1
            report.failed_files.append("ZIP hajmi 50MB dan katta.")
            return report

        try:
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        except zipfile.BadZipFile:
            report.failed = 1
            report.failed_files.append("Yaroqsiz ZIP arxiv.")
            return report

        with zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            if len(infos) > MAX_ZIP_ENTRIES:
                report.failed = 1
                report.failed_files.append(f"Fayllar soni juda ko‘p ({len(infos)} > {MAX_ZIP_ENTRIES}).")
                return report

            total_uncomp = sum(i.file_size for i in infos)
            if total_uncomp > MAX_ZIP_BYTES * 2:
                report.failed = 1
                report.failed_files.append("ZIP ichidagi fayllar hajmi xavfsiz limitdan oshib ketdi.")
                return report

            report.total_files = len(infos)
            img_exts = {".jpg", ".jpeg", ".png", ".webp"}
            MEDIA_CARDS_DIR.mkdir(parents=True, exist_ok=True)
            extracted_images: Dict[str, str] = {}
            manifest_records: List[Dict[str, Any]] = []

            for info in infos:
                if not SecurityBackupService._is_safe_zip_member(info.filename):
                    report.failed += 1
                    report.failed_files.append(f"{info.filename} (xavfli yo‘l)")
                    continue
                fname = Path(info.filename).name
                ext = Path(fname).suffix.lower()
                data = zf.read(info.filename)

                if ext in img_exts:
                    report.total_images += 1
                    safe_out = MEDIA_CARDS_DIR / fname
                    safe_out.write_bytes(data)
                    extracted_images[fname.lower()] = str(safe_out)
                    extracted_images[info.filename.lower()] = str(safe_out)
                elif ext == ".json":
                    try:
                        parsed = json.loads(data.decode("utf-8"))
                        if isinstance(parsed, list):
                            manifest_records.extend(p for p in parsed if isinstance(p, dict))
                    except Exception as exc:
                        report.failed += 1
                        report.failed_files.append(f"{fname}: JSON xato ({exc})")
                elif ext == ".csv":
                    try:
                        reader = csv.DictReader(io.StringIO(data.decode("utf-8")))
                        manifest_records.extend(dict(r) for r in reader)
                    except Exception as exc:
                        report.failed += 1
                        report.failed_files.append(f"{fname}: CSV xato ({exc})")

            # Agar JSON/CSV bo'lmasa, rasm fayl nomlaridan: "Character - Anime - Rank.jpg"
            if not manifest_records and extracted_images:
                seen_paths = set()
                for _, fpath in extracted_images.items():
                    if fpath in seen_paths:
                        continue
                    seen_paths.add(fpath)
                    stem = Path(fpath).stem
                    parts = [p.strip() for p in stem.split(" - ")]
                    char_name = parts[0] if parts else stem
                    anime_name = parts[1] if len(parts) > 1 else "Anime"
                    rank_name = parts[2] if len(parts) > 2 else "Common"
                    manifest_records.append(
                        {
                            "character_name": char_name,
                            "anime_name": anime_name,
                            "rarity": rank_name,
                            "image": fpath,
                        }
                    )

            rcfg = await SettingsService.get_rarity_config(session)
            report.total_cards = len(manifest_records)

            for idx, rec in enumerate(manifest_records, start=1):
                char_name = str(rec.get("character_name") or rec.get("character") or "").strip()
                anime_name = str(rec.get("anime_name") or rec.get("anime") or rec.get("media") or "").strip()
                rarity = str(rec.get("rarity") or rec.get("rank") or "Common").strip().title()
                mtype = str(rec.get("media_type") or "Anime").strip()
                if mtype not in MEDIA_TYPES:
                    mtype = "Anime"
                if rarity not in RARITY_REGISTRY:
                    rarity = "Common"
                img_ref = str(rec.get("image") or rec.get("image_url") or "").strip()
                if img_ref and img_ref.lower() in extracted_images:
                    img_ref = extracted_images[img_ref.lower()]

                if not char_name or not anime_name or not img_ref:
                    report.failed += 1
                    report.failed_files.append(f"Row #{idx}: character/anime/image yetishmaydi")
                    continue

                dup = (
                    await session.execute(
                        select(Card.id).where(
                            func.lower(Card.character_name) == char_name.lower(),
                            func.lower(Card.anime_name) == anime_name.lower(),
                            func.lower(Card.rarity) == rarity.lower(),
                            Card.is_deleted.is_(False),
                        )
                    )
                ).first()
                if dup is not None:
                    report.duplicates += 1
                    continue

                base_val = int(rec.get("base_value") or rcfg.get(rarity, {}).get("value", 1000))
                drop_lim = int(rec.get("drop_limit") or rcfg.get(rarity, {}).get("drops", 1))
                card = await CardService.create_card(
                    session,
                    character_name=char_name,
                    anime_name=anime_name,
                    image=img_ref,
                    rarity=rarity,
                    base_value=base_val,
                    drop_limit=drop_lim,
                )
                card.media_type = mtype
                if isinstance(rec.get("aliases"), list):
                    card.aliases = [str(a) for a in rec["aliases"] if a]
                report.added += 1

        await CommunityService.log_audit(
            session,
            admin_tg_id,
            "zip_import",
            "cards",
            details={
                "cards": report.total_cards,
                "added": report.added,
                "duplicates": report.duplicates,
                "failed": report.failed,
            },
        )
        await session.flush()
        return report

    # ------------------------------------------------------------------
    # Full ZIP Backup & Safe Restore (Memory)
    # ------------------------------------------------------------------
    @staticmethod
    async def create_full_backup_zip(
        session: AsyncSession, backup_type: str = "manual", admin_tg_id: Optional[int] = None
    ) -> BackupRecord:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = utcnow().strftime("%Y%m%d_%H%M%S")
        fname = f"anime_bot_backup_{backup_type}_{ts}.zip"
        fpath = BACKUP_DIR / fname

        def _serialize_rows(rows) -> List[Dict[str, Any]]:
            out = []
            for r in rows:
                d = {}
                for col in r.__table__.columns:
                    val = getattr(r, col.name)
                    if hasattr(val, "isoformat"):
                        val = val.isoformat()
                    d[col.name] = val
                out.append(d)
            return out

        users = (await session.execute(select(User))).scalars().all()
        cards = (await session.execute(select(Card))).scalars().all()
        user_cards = (await session.execute(select(UserCard))).scalars().all()
        groups = (await session.execute(select(Group))).scalars().all()
        settings_rows = (await session.execute(select(Setting))).scalars().all()

        payload = {
            "version": "2.0",
            "created_at": utcnow().isoformat(),
            "users": _serialize_rows(users),
            "cards": _serialize_rows(cards),
            "user_cards": _serialize_rows(user_cards),
            "groups": _serialize_rows(groups),
            "settings": _serialize_rows(settings_rows),
        }
        summary = {k: len(v) for k, v in payload.items() if isinstance(v, list)}

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("database_backup.json", json.dumps(payload, ensure_ascii=False, indent=2))
        raw_bytes = buf.getvalue()
        fpath.write_bytes(raw_bytes)

        rec = BackupRecord(
            filename=fname,
            file_size_bytes=len(raw_bytes),
            backup_type=backup_type,
            tables_summary=summary,
            created_by_tg_id=admin_tg_id,
            storage_path=str(fpath),
        )
        session.add(rec)
        await session.flush()
        return rec

    @staticmethod
    async def list_backups(session: AsyncSession, limit: int = 10) -> List[BackupRecord]:
        return list((await session.execute(select(BackupRecord).order_by(BackupRecord.id.desc()).limit(limit))).scalars().all())

    @staticmethod
    async def cleanup_old_backups(session: AsyncSession, retention_days: int = 14) -> int:
        cutoff = utcnow() - timedelta(days=max(1, int(retention_days)))
        old = list((await session.execute(select(BackupRecord).where(BackupRecord.created_at < cutoff))).scalars().all())
        removed = 0
        for rec in old:
            try:
                if rec.storage_path and os.path.exists(rec.storage_path):
                    os.remove(rec.storage_path)
            except OSError:
                pass
            await session.delete(rec)
            removed += 1
        await session.flush()
        return removed
