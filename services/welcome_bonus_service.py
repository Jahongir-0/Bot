"""Yangi qo'shilgan foydalanuvchilarga beriladigan boshlang'ich bonus (admin paneldan o'zgartiriladi).

Eslatma: bonus faqat YANGI foydalanuvchilarga beriladi. Avvaldan bor foydalanuvchilarning coiniga tegilmaydi.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from services.settings_service import SettingsService

TX_NEW = "welcome_bonus"
SETTING_KEY = "welcome_bonus_coins"
DEFAULT_WELCOME_BONUS = 2_000
MAX_WELCOME_BONUS = 1_000_000_000


class WelcomeBonusService:
    @staticmethod
    async def get_amount(session: AsyncSession) -> int:
        raw = await SettingsService.get_value(session, SETTING_KEY, DEFAULT_WELCOME_BONUS)
        try:
            return max(0, min(int(raw), MAX_WELCOME_BONUS))
        except (TypeError, ValueError):
            return DEFAULT_WELCOME_BONUS

    @staticmethod
    async def set_amount(session: AsyncSession, amount: int) -> int:
        amount = int(amount)
        if not (0 <= amount <= MAX_WELCOME_BONUS):
            raise ValueError(f"Qiymat 0 dan {MAX_WELCOME_BONUS:,} gacha bo‘lishi kerak.")
        await SettingsService.set_value(session, SETTING_KEY, amount)
        return amount


def new_user_text(amount: int) -> str:
    """Yangi foydalanuvchiga birinchi /start da ko'rsatiladigan xabar."""
    return (
        "🎁 <b>Xush kelibsiz!</b>\n"
        f"Sizga boshlang‘ich bonus sifatida <b>{int(amount):,} 🪙</b> berildi!\n\n"
    )
