import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from models.admin import Setting
from utils.constants import DEFAULT_SETTINGS


STARS_MAX_PACKAGES = 8
STARS_MIN = 1
STARS_MAX = 100_000
COINS_MAX = 1_000_000_000


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
    def build_stars_label(stars: int, coins: int) -> str:
        return f"⭐ {int(stars):,} Stars → {int(coins):,} coins"

    @staticmethod
    async def get_stars_packages(session: AsyncSession) -> List[Dict[str, Any]]:
        raw = await SettingsService.get_value(
            session,
            "stars_packages",
            DEFAULT_SETTINGS["stars_packages"],
        )
        packages: List[Dict[str, Any]] = []
        for p in list(raw or []):
            try:
                stars, coins = int(p["stars"]), int(p["coins"])
                pid = str(p["id"])
            except (KeyError, TypeError, ValueError):
                continue
            # Label har doim raqamlardan qayta quriladi, shuning uchun tahrirdan keyin eskirmaydi
            packages.append(
                {
                    "id": pid,
                    "stars": stars,
                    "coins": coins,
                    "label": SettingsService.build_stars_label(stars, coins),
                }
            )
        return packages

    @staticmethod
    async def save_stars_package(
        session: AsyncSession,
        package_id: Optional[str],
        stars: int,
        coins: int,
    ) -> Dict[str, Any]:
        """Yangi paket qo'shadi (package_id=None) yoki mavjudini yangilaydi. ValueError qaytaradi."""
        stars, coins = int(stars), int(coins)
        if not (STARS_MIN <= stars <= STARS_MAX):
            raise ValueError(f"Stars miqdori {STARS_MIN} dan {STARS_MAX:,} gacha bo‘lishi kerak.")
        if not (1 <= coins <= COINS_MAX):
            raise ValueError(f"Coin miqdori 1 dan {COINS_MAX:,} gacha bo‘lishi kerak.")

        packages = await SettingsService.get_stars_packages(session)
        if package_id:
            target = next((p for p in packages if p["id"] == package_id), None)
            if target is None:
                raise ValueError("Paket topilmadi.")
            target["stars"], target["coins"] = stars, coins
            target["label"] = SettingsService.build_stars_label(stars, coins)
            saved = target
        else:
            if len(packages) >= STARS_MAX_PACKAGES:
                raise ValueError(f"Eng ko‘pi bilan {STARS_MAX_PACKAGES} ta paket bo‘lishi mumkin.")
            existing_ids = {p["id"] for p in packages}
            new_id = f"pk{uuid.uuid4().hex[:8]}"
            while new_id in existing_ids:
                new_id = f"pk{uuid.uuid4().hex[:8]}"
            saved = {
                "id": new_id,
                "stars": stars,
                "coins": coins,
                "label": SettingsService.build_stars_label(stars, coins),
            }
            packages.append(saved)
        await SettingsService.set_value(session, "stars_packages", packages)
        return saved

    @staticmethod
    async def delete_stars_package(session: AsyncSession, package_id: str) -> bool:
        packages = await SettingsService.get_stars_packages(session)
        kept = [p for p in packages if p["id"] != package_id]
        if len(kept) == len(packages):
            return False
        await SettingsService.set_value(session, "stars_packages", kept)
        return True

    @staticmethod
    async def get_next_priority_card_id(session: AsyncSession) -> Optional[int]:
        val = await SettingsService.get_value(session, "next_priority_card_id", None)
        return int(val) if val is not None else None

    @staticmethod
    async def set_next_priority_card_id(session: AsyncSession, card_id: Optional[int]) -> None:
        await SettingsService.set_value(session, "next_priority_card_id", card_id)
