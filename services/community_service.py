import html
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import func, or_, select, update
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
        return dict(raw) if isinstance(raw, dict) else dict(DEFAULT_VIP_CONFIG)

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
    # Reklama / Ad Campaigns (matnli, joyga bog'liq cheklov + vaqt + klik)
    # ------------------------------------------------------------------
    AD_MAX_LEN = 160
    AD_MAX_LINES = 3
    VALID_AD_PLACEMENTS = ("group", "private", "all")
    AD_PLACEMENT_LABELS = {
        "group": "👥 Guruh (drop tagiga)",
        "private": "📱 User panelga",
        "all": "🌍 Hamma joyga",
    }
    AD_DURATION_OPTIONS = (
        (12, "12 soat"),
        (24, "24 soat"),
        (48, "48 soat"),
        (72, "72 soat"),
        (168, "7 kun"),
        (0, "♾ To o‘chirilmaguncha"),
    )
    _AD_LINK_RE = re.compile(
        r"(https?://[^\s<>()]+|(?:(?<=\s)|^)t\.me/[A-Za-z0-9_+/]+(?:\?[A-Za-z0-9_=&-]+)?|(?:(?<=[\s(\[])|^)@[A-Za-z][A-Za-z0-9_]{3,31}\b)"
    )

    @staticmethod
    def extract_ad_link(text: str) -> Tuple[Optional[str], Optional[str]]:
        """Matn ichidan birinchi link (https://..., t.me/...) yoki @username ni ajratadi."""
        m = CommunityService._AD_LINK_RE.search(text or "")
        if not m:
            return None, None
        raw = m.group(1).rstrip(".,!?;:)")
        if not raw:
            return None, None
        if raw.startswith("@"):
            url = f"https://t.me/{raw[1:]}"
        elif raw.lower().startswith("t.me/"):
            url = f"https://{raw}"
        else:
            url = raw
        return raw[:64], url[:255]

    @staticmethod
    def validate_ad_text(raw: str) -> Tuple[bool, str, str]:
        cleaned = "\n".join(line.strip() for line in (raw or "").strip().splitlines() if line.strip())
        if not cleaned:
            return False, "⚠️ Reklama matni bo‘sh bo‘lishi mumkin emas.", ""
        if len(cleaned) > CommunityService.AD_MAX_LEN:
            return (
                False,
                f"⚠️ Reklama matni juda uzun ({len(cleaned)}/{CommunityService.AD_MAX_LEN} belgi). Qisqaroq yozing.",
                "",
            )
        if cleaned.count("\n") >= CommunityService.AD_MAX_LINES:
            return (
                False,
                f"⚠️ Reklama ko‘pi bilan {CommunityService.AD_MAX_LINES} qatordan iborat bo‘lishi kerak.",
                "",
            )
        return True, "ok", cleaned

    @staticmethod
    def is_ad_expired(ad: AdCampaign, now: Optional[datetime] = None) -> bool:
        now = now or utcnow()
        return bool(ad.expires_at is not None and ensure_utc(ad.expires_at) <= now)

    @staticmethod
    def placement_label(placement: str) -> str:
        return CommunityService.AD_PLACEMENT_LABELS.get(placement, placement)

    @staticmethod
    async def expire_due_ads(session: AsyncSession) -> int:
        now = utcnow()
        res = await session.execute(
            update(AdCampaign)
            .where(
                AdCampaign.is_active.is_(True),
                AdCampaign.expires_at.is_not(None),
                AdCampaign.expires_at <= now,
            )
            .values(is_active=False)
        )
        return int(res.rowcount or 0)

    @staticmethod
    async def list_ads(session: AsyncSession) -> List[AdCampaign]:
        await CommunityService.expire_due_ads(session)
        return list((await session.execute(select(AdCampaign).order_by(AdCampaign.id.desc()))).scalars().all())

    @staticmethod
    async def get_occupied_ad_slots(
        session: AsyncSession, exclude_ad_id: Optional[int] = None
    ) -> Dict[str, AdCampaign]:
        await CommunityService.expire_due_ads(session)
        now = utcnow()
        conds = [
            AdCampaign.is_active.is_(True),
            or_(AdCampaign.expires_at.is_(None), AdCampaign.expires_at > now),
        ]
        if exclude_ad_id is not None:
            conds.append(AdCampaign.id != exclude_ad_id)
        active = list((await session.execute(select(AdCampaign).where(*conds).order_by(AdCampaign.id.asc()))).scalars().all())
        occupied: Dict[str, AdCampaign] = {}
        for ad in active:
            if ad.placement in ("all", "group"):
                occupied.setdefault("group", ad)
            if ad.placement in ("all", "private"):
                occupied.setdefault("private", ad)
        return occupied

    @staticmethod
    async def can_use_placement(
        session: AsyncSession, placement: str, exclude_ad_id: Optional[int] = None
    ) -> Tuple[bool, str]:
        if placement not in CommunityService.VALID_AD_PLACEMENTS:
            return False, "⚠️ Noto‘g‘ri reklama joyi tanlandi."
        occupied = await CommunityService.get_occupied_ad_slots(session, exclude_ad_id=exclude_ad_id)
        if placement == "all":
            if "group" in occupied or "private" in occupied:
                blockers = sorted({ad.id for ad in occupied.values()})
                ids_txt = ", ".join(f"#{i}" for i in blockers)
                return (
                    False,
                    f"⛔ «Hamma joyga» uchun barcha joylar bo‘sh bo‘lishi kerak. Hozir {ids_txt} reklama faol — avval uni o‘chiring, faolsizlantiring yoki joyini o‘zgartiring.",
                )
            return True, "ok"
        if placement in occupied:
            b_ad = occupied[placement]
            pl_lbl = CommunityService.placement_label(placement)
            return (
                False,
                f"⛔ «{pl_lbl}» joyi hozir #{b_ad.id} reklama bilan band ({CommunityService.placement_label(b_ad.placement)}). Avval uni o‘chiring, faolsizlantiring yoki joyini o‘zgartiring.",
            )
        return True, "ok"

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
        text: str,
        admin_tg_id: int,
        placement: str = "all",
        duration_hours: Optional[int] = None,
        title: Optional[str] = None,
        frequency_mins: int = 0,
        button_text: Optional[str] = None,
        button_url: Optional[str] = None,
        media_file_id: Optional[str] = None,
    ) -> Tuple[bool, str, Optional[AdCampaign]]:
        ok_txt, err_txt, cleaned = CommunityService.validate_ad_text(text)
        if not ok_txt:
            return False, err_txt, None
        ok_plc, err_plc = await CommunityService.can_use_placement(session, placement)
        if not ok_plc:
            return False, err_plc, None

        raw_tok, target_url = CommunityService.extract_ad_link(cleaned)
        if button_url:
            target_url = button_url[:255]
        if button_text:
            raw_tok = button_text[:64]

        expires_at = (
            utcnow() + timedelta(hours=int(duration_hours))
            if duration_hours is not None and int(duration_hours) > 0
            else None
        )
        ad_title = (title or cleaned.splitlines()[0])[:60]
        ad = AdCampaign(
            title=ad_title[:128],
            text=cleaned,
            media_file_id=None,
            media_type="text",
            button_text=raw_tok[:64] if raw_tok else None,
            button_url=target_url[:255] if target_url else None,
            placement=placement,
            frequency_mins=max(0, int(frequency_mins)),
            is_active=True,
            views_count=0,
            clicks_count=0,
            expires_at=expires_at,
            created_by_tg_id=admin_tg_id,
        )
        session.add(ad)
        await session.flush()
        return True, "✅ Reklama faollashtirildi!", ad

    @staticmethod
    async def update_ad(
        session: AsyncSession,
        ad_id: int,
        *,
        text: Optional[str] = None,
        placement: Optional[str] = None,
        duration_hours: Optional[int] = None,
    ) -> Tuple[bool, str, Optional[AdCampaign]]:
        ad = await session.get(AdCampaign, ad_id)
        if ad is None:
            return False, "Reklama topilmadi.", None

        if text is not None:
            ok_txt, err_txt, cleaned = CommunityService.validate_ad_text(text)
            if not ok_txt:
                return False, err_txt, ad
            raw_tok, target_url = CommunityService.extract_ad_link(cleaned)
            ad.text = cleaned
            ad.title = cleaned.splitlines()[0][:128]
            ad.button_text = raw_tok
            ad.button_url = target_url
            ad.media_file_id = None
            ad.media_type = "text"

        if placement is not None and placement != ad.placement:
            if ad.is_active and not CommunityService.is_ad_expired(ad):
                ok_plc, err_plc = await CommunityService.can_use_placement(
                    session, placement, exclude_ad_id=ad.id
                )
                if not ok_plc:
                    return False, err_plc, ad
            elif placement not in CommunityService.VALID_AD_PLACEMENTS:
                return False, "⚠️ Noto‘g‘ri reklama joyi.", ad
            ad.placement = placement

        if duration_hours is not None:
            if int(duration_hours) <= 0:
                ad.expires_at = None
            else:
                ad.expires_at = utcnow() + timedelta(hours=int(duration_hours))
            if not ad.is_active:
                ok_plc, _ = await CommunityService.can_use_placement(
                    session, ad.placement, exclude_ad_id=ad.id
                )
                if ok_plc:
                    ad.is_active = True

        await session.flush()
        return True, "✅ Reklama yangilandi!", ad

    @staticmethod
    async def toggle_ad(session: AsyncSession, ad_id: int) -> Tuple[bool, str, Optional[AdCampaign]]:
        ad = await session.get(AdCampaign, ad_id)
        if ad is None:
            return False, "Reklama topilmadi.", None
        if ad.is_active:
            ad.is_active = False
            await session.flush()
            return True, "⏸ Reklama faolsizlantirildi.", ad
        if CommunityService.is_ad_expired(ad):
            return (
                False,
                "⌛ Bu reklamaning muddati tugagan. Qayta yoqish uchun avval «⏳ Vaqtini o‘zgartirish» orqali yangi muddat tanlang.",
                ad,
            )
        ok_plc, err_plc = await CommunityService.can_use_placement(session, ad.placement, exclude_ad_id=ad.id)
        if not ok_plc:
            return False, err_plc, ad
        ad.is_active = True
        await session.flush()
        return True, "▶️ Reklama qayta faollashtirildi!", ad

    @staticmethod
    async def pick_due_ad(
        session: AsyncSession, user: Optional[User] = None, placement: str = "private"
    ) -> Optional[AdCampaign]:
        if placement == "private" and user is not None and await CommunityService.has_vip_benefit(session, user, "no_ads"):
            return None
        await CommunityService.expire_due_ads(session)
        now = utcnow()
        ads = list(
            (
                await session.execute(
                    select(AdCampaign)
                    .where(
                        AdCampaign.is_active.is_(True),
                        AdCampaign.placement.in_(["all", placement]),
                        or_(AdCampaign.expires_at.is_(None), AdCampaign.expires_at > now),
                    )
                    .order_by(AdCampaign.views_count.asc(), AdCampaign.id.asc())
                )
            )
            .scalars()
            .all()
        )
        if not ads:
            return None
        ad = next((a for a in ads if a.placement == placement), ads[0])
        await session.execute(
            update(AdCampaign)
            .where(AdCampaign.id == ad.id)
            .values(views_count=AdCampaign.views_count + 1, last_shown_at=now)
        )
        ad.views_count = int(ad.views_count or 0) + 1
        ad.last_shown_at = now
        await session.flush()
        return ad

    @staticmethod
    async def record_ad_click(session: AsyncSession, ad_id: int) -> Optional[AdCampaign]:
        ad = await session.get(AdCampaign, ad_id)
        if ad is None:
            return None
        await session.execute(
            update(AdCampaign)
            .where(AdCampaign.id == ad_id)
            .values(clicks_count=AdCampaign.clicks_count + 1)
        )
        ad.clicks_count = int(ad.clicks_count or 0) + 1
        await session.flush()
        return ad

    @staticmethod
    def format_ad_footer(ad: AdCampaign, bot_username: Optional[str] = None) -> str:
        safe_txt = html.escape(ad.text or "")
        if ad.button_url and ad.button_text:
            tok_esc = html.escape(ad.button_text)
            href = (
                f"https://t.me/{bot_username}?start=ad_{ad.id}"
                if bot_username
                else ad.button_url
            )
            link_tag = f'<a href="{html.escape(href, quote=True)}">{tok_esc}</a>'
            if tok_esc in safe_txt:
                safe_txt = safe_txt.replace(tok_esc, link_tag, 1)
            else:
                safe_txt = f"{safe_txt} — {link_tag}"
        return f"📢 {safe_txt}"

    @staticmethod
    async def get_rendered_ad(
        session: AsyncSession,
        bot_username: Optional[str] = None,
        user: Optional[User] = None,
        placement: str = "private",
    ) -> Optional[str]:
        ad = await CommunityService.pick_due_ad(session, user=user, placement=placement)
        if ad is None:
            return None
        return CommunityService.format_ad_footer(ad, bot_username=bot_username)

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
