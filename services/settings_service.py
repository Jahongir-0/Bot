from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from models.admin import Setting
from utils.constants import DEFAULT_SETTINGS


class SettingsService:
    @staticmethod
    async def get_value(session: AsyncSession, key: str, default: Any = None) -> Any:
        row = await session.get(Setting, key)
        if row is None:
            return default if default is not None else DEFAULT_SETTINGS.get(key)
        return row.value.get("data", default)

    @staticmethod
    async def set_value(session: AsyncSession, key: str, value: Any) -> None:
        row = await session.get(Setting, key)
        if row is None:
            row = Setting(key=key, value={"data": value})
            session.add(row)
        else:
            row.value = {"data": value}
        await session.flush()

    @staticmethod
    async def get_rarity_rewards(session: AsyncSession) -> Dict[str, int]:
        rewards = await SettingsService.get_value(
            session,
            "rarity_rewards",
            DEFAULT_SETTINGS["rarity_rewards"],
        )
        return {str(k): int(v) for k, v in dict(rewards).items()}

    @staticmethod
    async def set_rarity_reward(session: AsyncSession, rarity: str, coins: int) -> None:
        rewards = await SettingsService.get_rarity_rewards(session)
        rewards[rarity] = max(0, int(coins))
        await SettingsService.set_value(session, "rarity_rewards", rewards)

    @staticmethod
    async def get_daily_config(session: AsyncSession) -> Dict[str, int]:
        cfg = await SettingsService.get_value(
            session,
            "daily_bonus",
            DEFAULT_SETTINGS["daily_bonus"],
        )
        return {
            "base_coins": int(cfg.get("base_coins", 150)),
            "streak_step": int(cfg.get("streak_step", 50)),
            "max_streak_bonus": int(cfg.get("max_streak_bonus", 1000)),
        }

    @staticmethod
    async def get_ranking_mode(session: AsyncSession) -> str:
        mode = await SettingsService.get_value(
            session,
            "ranking_mode",
            DEFAULT_SETTINGS["ranking_mode"],
        )
        return str(mode)

    @staticmethod
    async def set_ranking_mode(session: AsyncSession, mode: str) -> None:
        await SettingsService.set_value(session, "ranking_mode", mode)

    @staticmethod
    async def get_offer_expiry_hours(session: AsyncSession) -> int:
        val = await SettingsService.get_value(
            session,
            "offer_expiry_hours",
            DEFAULT_SETTINGS["offer_expiry_hours"],
        )
        return max(1, int(val))

    @staticmethod
    async def get_stars_packages(session: AsyncSession) -> List[Dict[str, Any]]:
        pkgs = await SettingsService.get_value(
            session,
            "stars_packages",
            DEFAULT_SETTINGS["stars_packages"],
        )
        return list(pkgs)

    @staticmethod
    async def get_next_priority_card_id(session: AsyncSession) -> Optional[int]:
        val = await SettingsService.get_value(session, "next_priority_card_id", None)
        return int(val) if val is not None else None

    @staticmethod
    async def set_next_priority_card_id(session: AsyncSession, card_id: Optional[int]) -> None:
        await SettingsService.set_value(session, "next_priority_card_id", card_id)
