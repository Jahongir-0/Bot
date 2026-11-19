import copy
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from models.admin import AdminAction
from models.base import ensure_utc, utcnow
from models.user import User
from models.v2_features import (
    AdCampaign,
    AdminRole,
    ForcedChannel,
    ScheduledMessage,
    SupportMessage,
    SupportTicket,
    UserNotification,
)
from services.settings_service import SettingsService
from services.user_service import InsufficientCoinsError, UserService
from utils.constants import ADMIN_PERMISSION_SECTIONS, DEFAULT_VIP_CONFIG

logger = logging.getLogger(__name__)


class CommunityService:
    # ------------------------------------------------------------------
    # RBAC Admin Roles
    # ------------------------------------------------------------------
    @staticmethod
    async def get_admin_role(session: AsyncSession, tg_id: int) -> Optional[AdminRole]:
        res = await session.execute(
            select(AdminRole).where(AdminRole.telegram_id == tg_id, AdminRole.is_active.is_(True))
        )
        return res.scalar_one_or_none()

    @staticmethod
    async def get_admin_permissions(
        session: AsyncSession, tg_id: int, username: Optional[str] = None
    ) -> Tuple[bool, Set[str]]:
        """Qaytaradi: (is_admin_or_super, allowed_sections)."""
        settings = get_settings()
        all_secs = {k for k, _ in ADMIN_PERMISSION_SECTIONS}
        if tg_id in settings.admin_ids:
            return True, all_secs
        if username and username.lstrip("@").lower() in settings.admin_usernames:
            return True, all_secs

        role = await CommunityService.get_admin_role(session, tg_id)
        if role is None:
            return False, set()
        if role.is_super_admin:
            return True, all_secs
        perms = {str(p) for p in (role.permissions or []) if str(p) in all_secs}
        return True, perms

    @staticmethod
    async def has_section_access(
        session: AsyncSession, tg_id: int, section: str, username: Optional[str] = None
    ) -> bool:
        is_adm, perms = await CommunityService.get_admin_permissions(session, tg_id, username)
        if not is_adm:
            return False
        if section in ("home", "back"):
            return True
        return section in perms

    @staticmethod
    async def list_admin_roles(session: AsyncSession) -> List[AdminRole]:
        rows = (
            await session.execute(select(AdminRole).order_by(AdminRole.is_super_admin.desc(), AdminRole.id.asc()))
        ).scalars().all()
        return list(rows)

    @staticmethod
    async def upsert_admin_role(
        session: AsyncSession,
        tg_id: int,
        role_name: str,
        permissions: List[str],
        added_by_tg_id: int,
        is_super: bool = False,
        username: Optional[str] = None,
    ) -> AdminRole:
        role = (await session.execute(select(AdminRole).where(AdminRole.telegram_id == tg_id))).scalar_one_or_none()
        if role is None:
            role = AdminRole(
                telegram_id=tg_id,
                username=username,
                role_name=role_name[:48],
                is_super_admin=is_super,
                permissions=permissions,
                added_by_tg_id=added_by_tg_id,
                is_active=True,
            )
            session.add(role)
        else:
            role.role_name = role_name[:48]
            role.is_super_admin = is_super
            role.permissions = permissions
            role.is_active = True
            if username:
                role.username = username
        await session.flush()
        return role

    @staticmethod
    async def remove_admin_role(session: AsyncSession, tg_id: int) -> bool:
        role = (await session.execute(select(AdminRole).where(AdminRole.telegram_id == tg_id))).scalar_one_or_none()
        if role is None:
            return False
        role.is_active = False
        await session.flush()
        return True

    # ------------------------------------------------------------------
    # Forced Subscription Channels
    # ------------------------------------------------------------------
    @staticmethod
    async def list_channels(session: AsyncSession, active_only: bool = False) -> List[ForcedChannel]:
        stmt = select(ForcedChannel).order_by(ForcedChannel.id.asc())
        if active_only:
            stmt = stmt.where(ForcedChannel.is_active.is_(True))
        return list((await session.execute(stmt)).scalars().all())

    @staticmethod
    async def add_channel(
        session: AsyncSession,
        chat_id: int,
        title: str,
        username: Optional[str],
        invite_link: Optional[str],
        added_by_tg_id: int,
        target_subscribers: Optional[int] = None,
        channel_type: str = "telegram",
        sub_mode: str = "vip",
        expires_at: Optional[datetime] = None,
    ) -> ForcedChannel:
        ch = (await session.execute(select(ForcedChannel).where(ForcedChannel.chat_id == chat_id))).scalar_one_or_none()
        if ch is None:
            ch = ForcedChannel(
                chat_id=chat_id,
                title=title[:255],
                username=username,
                invite_link=invite_link,
                channel_type=channel_type,
                sub_mode=sub_mode,
                expires_at=expires_at,
                is_active=True,
                target_subscribers=target_subscribers,
                added_by_tg_id=added_by_tg_id,
            )
            session.add(ch)
        else:
            ch.title = title[:255]
            ch.username = username
            ch.invite_link = invite_link
            ch.channel_type = channel_type
            ch.sub_mode = sub_mode
            ch.expires_at = expires_at
            ch.is_active = True
            ch.target_subscribers = target_subscribers
        await session.flush()
        return ch

    @staticmethod
    async def toggle_channel(session: AsyncSession, channel_id: int) -> Optional[ForcedChannel]:
        ch = await session.get(ForcedChannel, channel_id)
        if ch is None:
            return None
        ch.is_active = not ch.is_active
        await session.flush()
        return ch

    @staticmethod
    async def delete_channel(session: AsyncSession, channel_id: int) -> bool:
        ch = await session.get(ForcedChannel, channel_id)
        if ch is None:
            return False
        await session.delete(ch)
        await session.flush()
        return True

    @staticmethod
    async def check_user_subscriptions(
        bot,
        session: AsyncSession,
        user: Optional[User],
        tg_user_id: int,
    ) -> Tuple[bool, List[ForcedChannel]]:
        """Qaytaradi: (is_ok, missing_channels). VIP bypass va muddatli/obunachili kanallar tekshiriladi."""
        if user is not None and await CommunityService.has_vip_benefit(session, user, "bypass_forced_sub"):
            return True, []

        channels = await CommunityService.list_channels(session, active_only=True)
        if not channels:
            return True, []

        now = utcnow()
        missing: List[ForcedChannel] = []
        for ch in channels:
            if ch.sub_mode == "daily" and ch.expires_at and ensure_utc(ch.expires_at) <= now:
                ch.is_active = False
                continue
            if ch.sub_mode == "subscribers" and ch.target_subscribers and ch.current_subscribers >= ch.target_subscribers:
                ch.is_active = False
                continue
            if ch.channel_type == "external":
                # Tashqi havolalar (Instagram/YouTube) Telegram API bilan tekshirilmaydi
                continue
            try:
                member = await bot.get_chat_member(ch.chat_id, tg_user_id)
                if getattr(member, "status", "left") in ("left", "kicked"):
                    missing.append(ch)
            except Exception:
                continue
        await session.flush()
        return len(missing) == 0, missing

    # ------------------------------------------------------------------
    # VIP Subscription System
    # ------------------------------------------------------------------
    @staticmethod
    async def get_vip_config(session: AsyncSession) -> Dict[str, Any]:
        raw = await SettingsService.get_value(session, "vip_config", DEFAULT_VIP_CONFIG)
        return copy.deepcopy(raw) if isinstance(raw, dict) else copy.deepcopy(DEFAULT_VIP_CONFIG)

    @staticmethod
    def is_vip_active(user: User) -> bool:
        if not user.vip_tier or not user.vip_until:
            return False
        return ensure_utc(user.vip_until) > utcnow()

    @staticmethod
    async def has_vip_benefit(session: AsyncSession, user: User, benefit: str) -> bool:
        if not CommunityService.is_vip_active(user):
            return False
        cfg = await CommunityService.get_vip_config(session)
        tier = (cfg.get("tiers") or {}).get(user.vip_tier, {})
        return bool(tier.get(benefit, False))

    @staticmethod
    async def get_vip_daily_multiplier(session: AsyncSession, user: User) -> float:
        if not CommunityService.is_vip_active(user):
            return 1.0
        cfg = await CommunityService.get_vip_config(session)
        tier = (cfg.get("tiers") or {}).get(user.vip_tier, {})
        return float(tier.get("daily_bonus_mult", 1.0))

    @staticmethod
    async def grant_vip(session: AsyncSession, user: User, tier_key: str, days: int = 30) -> None:
        now = utcnow()
        base = ensure_utc(user.vip_until) if (user.vip_until and ensure_utc(user.vip_until) > now) else now
        user.vip_tier = tier_key
        user.vip_until = base + timedelta(days=max(1, int(days)))
        await session.flush()

    @staticmethod
    async def revoke_vip(session: AsyncSession, user: User) -> None:
        user.vip_tier = None
        user.vip_until = None
        await session.flush()

    @staticmethod
    async def buy_vip_with_coins(session: AsyncSession, user: User, tier_key: str) -> Tuple[bool, str]:
        cfg = await CommunityService.get_vip_config(session)
        tiers = cfg.get("tiers") or {}
        tier = tiers.get(tier_key)
        if not tier:
            return False, "VIP daraja topilmadi."
        price = int(tier.get("coins", 0))
        if price <= 0:
            return False, "Bu VIP daraja coin bilan sotilmaydi."
        try:
            await UserService.debit_coins(
                session,
                user.id,
                price,
                "vip_purchase",
                f"VIP {tier_key}",
                reference_id=f"vip:{user.id}:{tier_key}:{int(utcnow().timestamp())}",
            )
        except InsufficientCoinsError:
            return False, f"Coin yetarli emas ({price:,} 🪙 kerak)."
        await CommunityService.grant_vip(session, user, tier_key, int(tier.get("duration_days", 30)))
        return True, f"💎 {tier.get('name', tier_key)} faollashtirildi!"

    # ------------------------------------------------------------------
    # Reklama / Ad Campaigns
    # ------------------------------------------------------------------
    @staticmethod
    async def list_ads(session: AsyncSession) -> List[AdCampaign]:
        return list((await session.execute(select(AdCampaign).order_by(AdCampaign.id.desc()))).scalars().all())

    @staticmethod
    async def delete_ad(session: AsyncSession, ad_id: int) -> bool:
        ad = await session.get(AdCampaign, ad_id)
        if ad is None:
            return False
        await session.delete(ad)
        await session.flush()
        return True

    @staticmethod
    async def create_ad(
        session: AsyncSession,
        title: str,
        text: str,
        admin_tg_id: int,
        placement: str = "all",
        frequency_mins: int = 120,
        button_text: Optional[str] = None,
        button_url: Optional[str] = None,
        media_file_id: Optional[str] = None,
    ) -> AdCampaign:
        ad = AdCampaign(
            title=title[:128],
            text=text,
            media_file_id=media_file_id,
            media_type="photo" if media_file_id else "text",
            button_text=button_text[:64] if button_text else None,
            button_url=button_url[:255] if button_url else None,
            placement=placement,
            frequency_mins=max(1, int(frequency_mins)),
            is_active=True,
            created_by_tg_id=admin_tg_id,
        )
        session.add(ad)
        await session.flush()
        return ad

    @staticmethod
    async def pick_due_ad(
        session: AsyncSession, user: Optional[User], placement: str = "private"
    ) -> Optional[AdCampaign]:
        if user is not None and await CommunityService.has_vip_benefit(session, user, "no_ads"):
            return None
        now = utcnow()
        ads = (
            await session.execute(
                select(AdCampaign)
                .where(
                    AdCampaign.is_active.is_(True),
                    AdCampaign.placement.in_(["all", placement]),
                )
                .order_by(AdCampaign.views_count.asc(), AdCampaign.id.asc())
            )
        ).scalars().all()
        for ad in ads:
            if ad.last_shown_at is None or (now - ensure_utc(ad.last_shown_at)) >= timedelta(minutes=ad.frequency_mins):
                ad.last_shown_at = now
                ad.views_count += 1
                await session.flush()
                return ad
        return None

    # ------------------------------------------------------------------
    # Support Tickets
    # ------------------------------------------------------------------
    @staticmethod
    async def create_ticket(
        session: AsyncSession, user_id: int, subject: str, body: str, sender_tg_id: int, category: str = "support"
    ) -> SupportTicket:
        now = utcnow()
        t = SupportTicket(
            user_id=user_id,
            subject=subject[:200],
            category=category,
            status="open",
            last_message_at=now,
        )
        session.add(t)
        await session.flush()
        session.add(
            SupportMessage(
                ticket_id=t.id,
                sender_type="user",
                sender_tg_id=sender_tg_id,
                body=body,
            )
        )
        await session.flush()
        return t

    @staticmethod
    async def reply_ticket(
        session: AsyncSession, ticket_id: int, sender_type: str, sender_tg_id: int, body: str
    ) -> Optional[SupportTicket]:
        t = await session.get(SupportTicket, ticket_id)
        if t is None:
            return None
        t.status = "answered" if sender_type == "admin" else "open"
        t.last_message_at = utcnow()
        if sender_type == "admin":
            t.assigned_admin_tg_id = sender_tg_id
        session.add(
            SupportMessage(
                ticket_id=t.id,
                sender_type=sender_type,
                sender_tg_id=sender_tg_id,
                body=body,
            )
        )
        await session.flush()
        return t

    @staticmethod
    async def get_ticket_with_messages(
        session: AsyncSession, ticket_id: int
    ) -> Optional[Tuple[SupportTicket, List[SupportMessage], Optional[User]]]:
        t = await session.get(SupportTicket, ticket_id)
        if t is None:
            return None
        msgs = list(
            (
                await session.execute(
                    select(SupportMessage)
                    .where(SupportMessage.ticket_id == ticket_id)
                    .order_by(SupportMessage.id.asc())
                )
            )
            .scalars()
            .all()
        )
        u = await session.get(User, t.user_id)
        return t, msgs, u

    @staticmethod
    async def list_tickets(
        session: AsyncSession, user_id: Optional[int] = None, status: Optional[str] = None, limit: int = 20
    ) -> List[SupportTicket]:
        stmt = select(SupportTicket)
        if user_id is not None:
            stmt = stmt.where(SupportTicket.user_id == user_id)
        if status:
            stmt = stmt.where(SupportTicket.status == status)
        stmt = stmt.order_by(SupportTicket.last_message_at.desc()).limit(limit)
        return list((await session.execute(stmt)).scalars().all())

    # ------------------------------------------------------------------
    # Notification Center
    # ------------------------------------------------------------------
    @staticmethod
    async def notify_user(
        session: AsyncSession,
        user_id: int,
        notif_type: str,
        title: str,
        body: str,
        payload: Optional[dict] = None,
    ) -> UserNotification:
        n = UserNotification(
            user_id=user_id,
            notif_type=notif_type[:32],
            title=title[:160],
            body=body,
            is_read=False,
            payload=payload or {},
        )
        session.add(n)
        await session.flush()
        return n

    @staticmethod
    async def unread_count(session: AsyncSession, user_id: int) -> int:
        return int(
            (
                await session.execute(
                    select(func.count(UserNotification.id)).where(
                        UserNotification.user_id == user_id, UserNotification.is_read.is_(False)
                    )
                )
            ).scalar_one()
            or 0
        )

    @staticmethod
    async def list_notifications(
        session: AsyncSession, user_id: int, page: int = 1, page_size: int = 20
    ) -> Tuple[List[UserNotification], int]:
        total = int(
            (
                await session.execute(
                    select(func.count(UserNotification.id)).where(UserNotification.user_id == user_id)
                )
            ).scalar_one()
            or 0
        )
        rows = list(
            (
                await session.execute(
                    select(UserNotification)
                    .where(UserNotification.user_id == user_id)
                    .order_by(UserNotification.id.desc())
                    .offset((max(1, page) - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )
        return rows, total

    @staticmethod
    async def mark_all_read(session: AsyncSession, user_id: int) -> None:
        await session.execute(
            update(UserNotification)
            .where(UserNotification.user_id == user_id, UserNotification.is_read.is_(False))
            .values(is_read=True)
        )
        await session.flush()

    # ------------------------------------------------------------------
    # Audit Logs & Scheduled Messages
    # ------------------------------------------------------------------
    @staticmethod
    async def log_audit(
        session: AsyncSession,
        admin_tg_id: int,
        action_type: str,
        target_type: str,
        target_id: Optional[str] = None,
        old_value: Optional[dict] = None,
        new_value: Optional[dict] = None,
        details: Optional[dict] = None,
    ) -> None:
        session.add(
            AdminAction(
                admin_telegram_id=admin_tg_id,
                action_type=action_type,
                target_type=target_type,
                target_id=target_id,
                old_value=old_value or {},
                new_value=new_value or {},
                details=details or {},
            )
        )
        await session.flush()

    @staticmethod
    async def get_recent_logs(session: AsyncSession, limit: int = 25) -> List[AdminAction]:
        return list(
            (await session.execute(select(AdminAction).order_by(AdminAction.id.desc()).limit(limit))).scalars().all()
        )

    @staticmethod
    async def create_scheduled_message(
        session: AsyncSession,
        title: str,
        body: str,
        send_at: datetime,
        target_scope: str,
        admin_tg_id: int,
        media_file_id: Optional[str] = None,
        from_chat_id: Optional[int] = None,
        forward_message_id: Optional[int] = None,
    ) -> ScheduledMessage:
        sm = ScheduledMessage(
            title=title[:160],
            body=body,
            media_file_id=media_file_id,
            from_chat_id=from_chat_id,
            forward_message_id=forward_message_id,
            target_scope=target_scope,
            send_at=send_at,
            status="pending",
            created_by_tg_id=admin_tg_id,
        )
        session.add(sm)
        await session.flush()
        return sm

    @staticmethod
    async def get_due_scheduled_messages(session: AsyncSession) -> List[ScheduledMessage]:
        now = utcnow()
        return list(
            (
                await session.execute(
                    select(ScheduledMessage)
                    .where(ScheduledMessage.status == "pending", ScheduledMessage.send_at <= now)
                    .order_by(ScheduledMessage.send_at.asc())
                )
            )
            .scalars()
            .all()
        )
