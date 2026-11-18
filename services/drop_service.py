import logging
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy import and_, delete, exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.redis_client import get_lock_manager
from models.base import ensure_utc, utcnow
from models.card import Card
from models.group import Group
from models.schedule import ActiveDrop, DropSchedule
from models.user import User
from models.user_card import UserCard
from services.card_service import CardService
from services.settings_service import SettingsService
from services.user_service import UserService

logger = logging.getLogger(__name__)


@dataclass
class ClaimResult:
    success: bool
    reason: str
    card: Optional[Card] = None
    winner: Optional[User] = None
    reward_coins: int = 0
    drop: Optional[ActiveDrop] = None


class DropService:
    @staticmethod
    def _local_today() -> date:
        tz = ZoneInfo(get_settings().timezone)
        return datetime.now(tz).date()

    # ------------------------------------------------------------------
    # Guruh a'zolari soniga qarab kunlik karta soni
    # ------------------------------------------------------------------
    @staticmethod
    def compute_daily_drop_count(member_count: int, rules: Dict[str, Any]) -> int:
        """20 va kam a'zo = 2 ta, 30 = 3, 40 = 4, 100 = 10, 140 = 14, 1000 = 100 (a'zolar // 10)."""
        per = max(1, int(rules.get("members_per_card", 10)))
        lo = max(0, int(rules.get("min_per_day", 2)))
        hi = max(lo, int(rules.get("max_per_day", 200)))
        return max(lo, min(hi, int(member_count) // per))

    @staticmethod
    async def refresh_member_count(
        bot,
        session: AsyncSession,
        group: Group,
        max_age_hours: float = 6.0,
        force: bool = False,
    ) -> Optional[int]:
        """Telegram'dan guruh a'zolari sonini oladi (API'ni tez-tez urmaslik uchun keshlanadi)."""
        now = utcnow()
        updated = group.member_count_updated_at
        if (
            not force
            and group.member_count is not None
            and updated is not None
            and (now - ensure_utc(updated)) < timedelta(hours=max_age_hours)
        ):
            return group.member_count
        try:
            count = int(await bot.get_chat_member_count(group.telegram_chat_id))
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            logger.warning("get_chat_member_count failed for %s: %s", group.telegram_chat_id, exc)
            return group.member_count
        except Exception as exc:  # tarmoq xatolari
            logger.warning("get_chat_member_count error for %s: %s", group.telegram_chat_id, exc)
            return group.member_count
        group.member_count = count
        group.member_count_updated_at = now
        return count

    @staticmethod
    def generate_slot_times(
        day: date,
        tz: ZoneInfo,
        rules: Dict[str, Any],
        count: int,
        now_local: datetime,
    ) -> List[datetime]:
        """
        Kun bo'yi teng taqsimlangan, lekin har safar boshqacha tasodifiy vaqtlar.
        Oyna `count` ta teng bo'lakka bo'linadi va har bir karta o'z bo'lagi ichida (markazdan ±40%) tasodifiy
        vaqtda tushadi => ikki karta orasidagi masofa kamida bo'lakning ~20% i, hammasi bir joyga to'planib qolmaydi.
        """
        if count <= 0:
            return []
        start_h = max(0, min(23, int(rules.get("window_start_hour", 0))))
        end_h = max(start_h + 1, min(24, int(rules.get("window_end_hour", 24))))
        midnight = datetime.combine(day, time(0, 0), tzinfo=tz)
        day_start = midnight + timedelta(hours=start_h)
        day_end = midnight + timedelta(hours=end_h)

        start_dt = day_start
        if day == now_local.date() and now_local + timedelta(minutes=2) > day_start:
            start_dt = now_local + timedelta(minutes=2)
        if start_dt >= day_end:
            return []

        full_span = (day_end - day_start).total_seconds()
        span = (day_end - start_dt).total_seconds()
        if span < full_span:
            # Kun o'rtasida ishga tushgan bo'lsa: qolgan vaqtga mutanosib sondagi karta
            count = max(1, round(count * span / full_span)) if span / full_span > 0.05 else 0
        if count <= 0:
            return []

        seg = span / count
        times: List[datetime] = []
        for i in range(count):
            offset = (i + 0.5) * seg + random.uniform(-0.4, 0.4) * seg
            offset = max(5.0, min(span - 30.0, offset))
            times.append(start_dt + timedelta(seconds=offset))
        times.sort()
        return times

    @staticmethod
    async def has_schedule_for_day(session: AsyncSession, group_id: int, day: date) -> bool:
        return bool(
            (
                await session.execute(
                    select(
                        exists().where(DropSchedule.group_id == group_id, DropSchedule.schedule_date == day)
                    )
                )
            ).scalar()
        )

    @staticmethod
    async def get_groups_missing_schedule(session: AsyncSession, day: date) -> List[Group]:
        """Bugungi jadvali yo'q faol guruhlar (bitta so'rov bilan)."""
        res = await session.execute(
            select(Group).where(
                Group.is_active.is_(True),
                Group.drop_enabled.is_(True),
                ~exists().where(DropSchedule.group_id == Group.id, DropSchedule.schedule_date == day),
            )
        )
        return list(res.scalars().all())

    @staticmethod
    async def ensure_daily_schedule_for_group(
        session: AsyncSession,
        group: Group,
        target_date: Optional[date] = None,
        force_regenerate: bool = False,
        member_count: Optional[int] = None,
    ) -> List[DropSchedule]:
        tz = ZoneInfo(get_settings().timezone)
        day = target_date or datetime.now(tz).date()

        if force_regenerate:
            await session.execute(
                delete(DropSchedule).where(
                    DropSchedule.group_id == group.id,
                    DropSchedule.schedule_date == day,
                    DropSchedule.status == "pending",
                )
            )
            await session.flush()

        existing = (
            await session.execute(
                select(DropSchedule)
                .where(DropSchedule.group_id == group.id, DropSchedule.schedule_date == day)
                .order_by(DropSchedule.scheduled_at.asc())
            )
        ).scalars().all()

        if existing and not force_regenerate:
            return list(existing)

        rules = await SettingsService.get_drop_rules(session)
        if member_count is not None:
            group.member_count = int(member_count)
            group.member_count_updated_at = utcnow()

        if group.auto_drop_count and group.member_count is not None:
            count = DropService.compute_daily_drop_count(group.member_count, rules)
            group.daily_drop_count = count  # admin panelida ko'rinadigan joriy kunlik son
        else:
            count = max(0, int(group.daily_drop_count))

        if force_regenerate:
            # Bugun allaqachon tashlangan kartalar sonini jami kunlik sondan ayiramiz
            already = (
                await session.execute(
                    select(DropSchedule.id).where(
                        DropSchedule.group_id == group.id,
                        DropSchedule.schedule_date == day,
                        DropSchedule.status == "sent",
                    )
                )
            ).all()
            count = max(0, count - len(already))

        now_local = datetime.now(tz)
        slot_times = DropService.generate_slot_times(day, tz, rules, count, now_local)
        schedules: List[DropSchedule] = []
        for local_dt in slot_times:
            item = DropSchedule(
                group_id=group.id,
                schedule_date=day,
                scheduled_at=local_dt.astimezone(timezone.utc),
                card_id=None,  # karta drop vaqtida tanlanadi (limit/cooldown qoidalari bilan)
                is_priority_override=False,
                status="pending",
            )
            session.add(item)
            schedules.append(item)
        if not schedules:
            # Kun oxirida yoki 0 drop: jadval "yaratilgan" deb belgilansin, scheduler har 30 soniyada qayta urinmasin
            session.add(
                DropSchedule(
                    group_id=group.id,
                    schedule_date=day,
                    scheduled_at=utcnow(),
                    card_id=None,
                    is_priority_override=False,
                    status="skipped",
                )
            )
        await session.flush()
        return schedules

    @staticmethod
    async def get_today_schedule(session: AsyncSession, group_id: int) -> List[DropSchedule]:
        day = DropService._local_today()
        res = await session.execute(
            select(DropSchedule)
            .where(DropSchedule.group_id == group_id, DropSchedule.schedule_date == day)
            .order_by(DropSchedule.scheduled_at.asc())
        )
        return list(res.scalars().all())

    @staticmethod
    async def replace_schedule_card(
        session: AsyncSession,
        schedule_id: int,
        card_id: int,
    ) -> Optional[DropSchedule]:
        sched = await session.get(DropSchedule, schedule_id)
        if sched is None or sched.status != "pending":
            return None
        sched.card_id = card_id
        sched.is_priority_override = True
        await session.flush()
        return sched

    @staticmethod
    def normalize_guess(text: str) -> str:
        import re
        cleaned = re.sub(r"[^\w\s]", " ", (text or "").casefold())
        return re.sub(r"\s+", " ", cleaned).strip()

    @staticmethod
    def matches_character_guess(card: Card, guess_text: str) -> bool:
        """Personaj ismini qulay tekshiradi:
        - to'liq ism (so'z tartibi farqi yo'q: "Hayato Suo" = "Suo Hayato"),
        - ismning bitta so'zi (kamida 3 harf): "Hayato" yoki "Suo",
        - alias lar,
        - kichik yozuv xatosi (masalan "Hayatto Suo").
        """
        from difflib import SequenceMatcher

        norm_guess = DropService.normalize_guess(guess_text)
        if not norm_guess or len(norm_guess) < 2:
            return False
        guess_tokens = norm_guess.split()
        compact_guess = norm_guess.replace(" ", "")
        candidates = [card.character_name] + list(getattr(card, "aliases", None) or [])
        for cand in candidates:
            norm_cand = DropService.normalize_guess(str(cand))
            if not norm_cand:
                continue
            cand_tokens = norm_cand.split()
            compact_cand = norm_cand.replace(" ", "")
            if norm_guess == norm_cand or compact_guess == compact_cand:
                return True
            if sorted(guess_tokens) == sorted(cand_tokens):
                return True
            if len(guess_tokens) == 1 and len(compact_guess) >= 3 and compact_guess in cand_tokens:
                return True
            if len(compact_guess) >= 5 and SequenceMatcher(None, compact_guess, compact_cand).ratio() >= 0.88:
                return True
        return False

    @staticmethod
    async def get_recent_unclaimed_drops(
        session: AsyncSession, telegram_chat_id: int, limit: int = 10
    ) -> List[Tuple[ActiveDrop, Card]]:
        rows = (
            await session.execute(
                select(ActiveDrop, Card)
                .join(Card, Card.id == ActiveDrop.card_id)
                .where(
                    ActiveDrop.telegram_chat_id == telegram_chat_id,
                    ActiveDrop.is_claimed.is_(False),
                )
                .order_by(ActiveDrop.id.desc())
                .limit(limit)
            )
        ).all()
        return [(r[0], r[1]) for r in rows]

    @staticmethod
    async def calculate_drop_reward(session: AsyncSession, card: Card, group: Group) -> int:
        # V2 qoidasi: drop orqali karta olganda coin berilmaydi
        return 0

    @staticmethod
    async def create_active_drop(
        session: AsyncSession,
        group: Group,
        card: Card,
        schedule_id: Optional[int] = None,
        force: bool = False,
    ) -> Optional[ActiveDrop]:
        conds = [Card.id == card.id]
        if not force:
            conds.append(Card.dropped_count < Card.drop_limit)
        upd = await session.execute(
            update(Card).where(*conds).values(dropped_count=Card.dropped_count + 1)
        )
        if upd.rowcount != 1:
            return None
        card.dropped_count = int(card.dropped_count or 0) + 1

        reward = await DropService.calculate_drop_reward(session, card, group)
        drop = ActiveDrop(
            group_id=group.id,
            telegram_chat_id=group.telegram_chat_id,
            card_id=card.id,
            schedule_id=schedule_id,
            is_claimed=False,
            reward_coins=reward,
        )
        session.add(drop)
        await session.flush()
        return drop

    @staticmethod
    async def pick_card_for_group(
        session: AsyncSession,
        group: Group,
        preferred_card_id: Optional[int] = None,
    ) -> Tuple[Optional[Card], bool]:
        priority_id = await SettingsService.get_next_priority_card_id(session)
        if priority_id is not None:
            await SettingsService.set_next_priority_card_id(session, None)
            p_card = await session.get(Card, priority_id)
            if (
                p_card is not None
                and p_card.is_available
                and not p_card.is_deleted
                and not p_card.is_event_card
                and p_card.drop_limit > 0
            ):
                return p_card, True

        if preferred_card_id is not None:
            cand = await session.get(Card, preferred_card_id)
            if (
                cand is not None
                and cand.is_available
                and not cand.is_deleted
                and not cand.is_event_card
                and cand.drop_limit > 0
            ):
                return cand, True

        rules = await SettingsService.get_drop_rules(session)
        now = utcnow()
        base = and_(
            Card.is_available.is_(True),
            Card.is_deleted.is_(False),
            Card.is_event_card.is_(False),
            Card.drop_weight > 0,
            Card.dropped_count < Card.drop_limit,
        )
        same_group_recent = select(ActiveDrop.card_id).where(
            ActiveDrop.group_id == group.id,
            ActiveDrop.created_at >= now - timedelta(days=int(rules["same_group_cooldown_days"])),
        )
        any_group_recent = select(ActiveDrop.card_id).where(
            ActiveDrop.created_at >= now - timedelta(hours=float(rules["any_group_cooldown_hours"]))
        )
        same_group_day = select(ActiveDrop.card_id).where(
            ActiveDrop.group_id == group.id,
            ActiveDrop.created_at >= now - timedelta(hours=24),
        )
        tiers = [
            and_(base, Card.id.not_in(same_group_recent), Card.id.not_in(any_group_recent)),
            and_(base, Card.id.not_in(same_group_day)),
        ]

        active_event = await CardService.get_active_event(session)
        for cond in tiers:
            cards = list((await session.execute(select(Card).where(cond))).scalars().all())
            if not cards:
                continue
            weights: List[float] = []
            for card in cards:
                w = float(card.drop_weight)
                if (
                    active_event is not None
                    and active_event.boosted_rarity
                    and card.rarity.lower() == active_event.boosted_rarity.lower()
                ):
                    w *= max(1.0, active_event.rarity_weight_multiplier)
                weights.append(max(0.1, w))
            return random.choices(cards, weights=weights, k=1)[0], False
        return None, False

    @staticmethod
    async def claim_drop(
        session: AsyncSession,
        drop_id: int,
        claimer_user_id: int,
    ) -> ClaimResult:
        lock_mgr = get_lock_manager()
        async with lock_mgr.lock(f"drop_claim:{drop_id}", ttl_ms=5000) as acquired:
            if not acquired:
                return ClaimResult(success=False, reason="locked")

            drop = await session.get(ActiveDrop, drop_id)
            if drop is None:
                return ClaimResult(success=False, reason="not_found")
            if drop.is_claimed:
                return ClaimResult(success=False, reason="already_claimed")

            card = await session.get(Card, drop.card_id)
            winner = await session.get(User, claimer_user_id)
            if card is None or winner is None:
                return ClaimResult(success=False, reason="not_found")

            if await UserService.owns_card(session, winner.id, card.id):
                return ClaimResult(success=False, reason="already_owned", card=card)

            now = utcnow()
            upd = await session.execute(
                update(ActiveDrop)
                .where(ActiveDrop.id == drop_id, ActiveDrop.is_claimed.is_(False))
                .values(
                    is_claimed=True,
                    claimed_by_user_id=claimer_user_id,
                    claimed_at=now,
                    updated_at=now,
                )
            )
            if upd.rowcount != 1:
                return ClaimResult(success=False, reason="already_claimed")

            try:
                uc = UserCard(
                    user_id=winner.id,
                    card_id=card.id,
                    acquired_via="drop",
                    acquired_group_id=drop.group_id,
                    card_state="OWNED",
                )
                session.add(uc)
                await session.flush()
                uc.serial_number = 10000 + uc.id
            except IntegrityError:
                await session.rollback()
                return ClaimResult(success=False, reason="already_owned", card=card)

            if int(drop.reward_coins or 0) > 0:
                await UserService.credit_coins(
                    session=session,
                    user_id=winner.id,
                    amount=drop.reward_coins,
                    tx_type="card_drop_reward",
                    description=f"Card drop claimed: {card.character_name}",
                    reference_id=f"drop:{drop.id}",
                )

            stats = dict(winner.stats or {})
            stats["claims"] = int(stats.get("claims", 0)) + 1
            winner.stats = stats

            await UserService.recompute_card_counts(session, winner.id, add_xp=20)

            from services.auction_wishlist_service import AuctionWishlistService
            from services.gameplay_service import GameplayService
            from services.security_backup_service import SecurityBackupService

            await AuctionWishlistService.record_history(
                session, card.id, "claimed", uc.id, to_user_id=winner.id, group_id=drop.group_id
            )
            await GameplayService.add_season_points(session, winner.id, points=10, xp=20, card_claimed=True)
            await GameplayService.sync_user_achievements(session, winner)
            await SecurityBackupService.check_claim_speed(session, winner, drop.created_at, drop.group_id)
            await session.flush()

            return ClaimResult(
                success=True,
                reason="claimed",
                card=card,
                winner=winner,
                reward_coins=drop.reward_coins,
                drop=drop,
            )

