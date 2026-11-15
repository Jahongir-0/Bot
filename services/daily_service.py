from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.redis_client import get_lock_manager
from models.transaction import DailyReward
from models.user import User
from services.settings_service import SettingsService
from services.user_service import UserService


@dataclass
class DailyClaimResult:
    success: bool
    message: str
    coins_awarded: int = 0
    streak: int = 0


class DailyService:
    @staticmethod
    async def claim_daily(session: AsyncSession, user_id: int) -> DailyClaimResult:
        lock_mgr = get_lock_manager()
        async with lock_mgr.lock(f"daily:{user_id}", ttl_ms=5000) as acquired:
            if not acquired:
                return DailyClaimResult(False, "So‘rov bajarilmoqda, biroz kuting.")

            user = await session.get(User, user_id)
            if user is None:
                return DailyClaimResult(False, "Foydalanuvchi topilmadi.")

            tz = ZoneInfo(get_settings().timezone)
            today = datetime.now(tz).date()

            existing = (
                await session.execute(
                    select(DailyReward).where(
                        DailyReward.user_id == user_id,
                        DailyReward.reward_date == today,
                    )
                )
            ).scalar_one_or_none()

            if existing is not None or user.last_daily_date == today:
                return DailyClaimResult(
                    False,
                    "Bugungi daily bonusni allaqachon olgansiz! Ertaga qayta urinib ko‘ring.",
                    streak=user.daily_streak,
                )

            yesterday = today - timedelta(days=1)
            new_streak = (user.daily_streak + 1) if user.last_daily_date == yesterday else 1

            cfg = await SettingsService.get_daily_config(session)
            bonus = min(
                cfg["max_streak_bonus"],
                (new_streak - 1) * cfg["streak_step"],
            )
            total_coins = cfg["base_coins"] + bonus

            user.daily_streak = new_streak
            user.last_daily_date = today

            session.add(
                DailyReward(
                    user_id=user.id,
                    reward_date=today,
                    streak_day=new_streak,
                    coins_awarded=total_coins,
                )
            )
            await UserService.credit_coins(
                session=session,
                user_id=user.id,
                amount=total_coins,
                tx_type="daily_bonus",
                description=f"Daily bonus (Streak {new_streak})",
                reference_id=f"daily:{user.id}:{today.isoformat()}",
            )
            await session.flush()

            return DailyClaimResult(
                True,
                "Daily bonus berildi!",
                coins_awarded=total_coins,
                streak=new_streak,
            )
