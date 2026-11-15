import logging
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.redis_client import get_lock_manager
from models.base import utcnow
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

    @staticmethod
    async def ensure_daily_schedule_for_group(
        session: AsyncSession,
        group: Group,
        target_date: Optional[date] = None,
        force_regenerate: bool = False,
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
                .where(
                    DropSchedule.group_id == group.id,
                    DropSchedule.schedule_date == day,
                )
                .order_by(DropSchedule.scheduled_at.asc())
            )
        ).scalars().all()

        if existing and not force_regenerate:
            return list(existing)

        count = max(0, group.daily_drop_count)
        if count == 0:
            return []

        now_local = datetime.now(tz)
        start_dt = datetime.combine(day, time(9, 0), tzinfo=tz)
        end_dt = datetime.combine(day, time(23, 30), tzinfo=tz)
        if day == now_local.date() and now_local > start_dt:
            start_dt = now_local + timedelta(minutes=2)

        if start_dt >= end_dt:
            return []

        total_seconds = int((end_dt - start_dt).total_seconds())
        step = max(60, total_seconds // max(1, count))
        schedules: List[DropSchedule] = []

        for idx in range(count):
            window_start = idx * step
            window_end = min(total_seconds, (idx + 1) * step - 10)
            offset = random.randint(window_start, max(window_start, window_end))
            sched_local = start_dt + timedelta(seconds=offset)
            sched_utc = sched_local.astimezone(timezone.utc)

            # Pre-assign ~40% of schedule slots with specific preview cards, rest Random
            preview_card_id = None
            if random.random() < 0.4:
                card = await CardService.pick_random_card(session, consume_priority=False)
                if card is not None:
                    preview_card_id = card.id

            item = DropSchedule(
                group_id=group.id,
                schedule_date=day,
                scheduled_at=sched_utc,
                card_id=preview_card_id,
                is_priority_override=False,
                status="pending",
            )
            session.add(item)
            schedules.append(item)

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
    async def calculate_drop_reward(session: AsyncSession, card: Card, group: Group) -> int:
        rewards = await SettingsService.get_rarity_rewards(session)
        base_reward = int(rewards.get(card.rarity, 20))
        multiplier = float(group.coin_multiplier or 1.0)
        active_event = await CardService.get_active_event(session)
        if active_event is not None:
            multiplier *= float(active_event.coin_multiplier or 1.0)
        return max(1, int(round(base_reward * multiplier)))

    @staticmethod
    async def create_active_drop(
        session: AsyncSession,
        group: Group,
        card: Card,
        schedule_id: Optional[int] = None,
    ) -> ActiveDrop:
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
    async def claim_drop(
        session: AsyncSession,
        drop_id: int,
        claimer_user_id: int,
    ) -> ClaimResult:
        """
        Race-condition-free atomic claim:
        1. Distributed lock per drop_id prevents simultaneous click contention.
        2. Conditional SQL UPDATE ensures only ONE claimer ever succeeds even across processes.
        """
        lock_mgr = get_lock_manager()
        async with lock_mgr.lock(f"drop_claim:{drop_id}", ttl_ms=5000) as acquired:
            if not acquired:
                return ClaimResult(success=False, reason="locked")

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

            drop = await session.get(ActiveDrop, drop_id)
            card = await session.get(Card, drop.card_id)
            winner = await session.get(User, claimer_user_id)

            if drop is None or card is None or winner is None:
                return ClaimResult(success=False, reason="not_found")

            session.add(
                UserCard(
                    user_id=winner.id,
                    card_id=card.id,
                    acquired_via="drop",
                    acquired_group_id=drop.group_id,
                )
            )
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

            await UserService.recompute_card_counts(session, winner.id, add_xp=25)
            await session.flush()

            return ClaimResult(
                success=True,
                reason="claimed",
                card=card,
                winner=winner,
                reward_coins=drop.reward_coins,
                drop=drop,
            )
