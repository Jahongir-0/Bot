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

    # ZIP Card Importer -> services/card_zip_import.py (v3)
    @staticmethod
    async def import_cards_from_zip(session, zip_bytes: bytes, admin_tg_id: int, bot, progress=None):
        from services.card_zip_import import import_cards_from_zip as _imp

        return await _imp(session, zip_bytes, admin_tg_id, bot, progress)

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
