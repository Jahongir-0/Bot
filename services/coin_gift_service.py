"""Foydalanuvchilar o'rtasida coin yuborish (sender'dan ayiriladi, qabul qiluvchiga qo'shiladi)."""
from datetime import datetime, timezone
from typing import Any, Dict, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.redis_client import get_lock_manager
from models.transaction import CoinTransaction
from models.user import User
from services.settings_service import SettingsService
from services.user_service import InsufficientCoinsError, UserService
from utils.timeutils import TASHKENT

TX_SENT = "coin_gift_sent"
TX_RECEIVED = "coin_gift_received"
CONFIG_KEY = "coin_gift_config"
DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "min_amount": 10,        # bir martalik minimal miqdor
    "daily_limit": 200_000,  # bir kunda yuborish mumkin bo'lgan jami coin (0 = cheklov yo'q)
    "fee_percent": 0,        # komissiya (%) — qabul qiluvchi shuncha kam oladi
}
INT_FIELDS = ("min_amount", "daily_limit", "fee_percent")


class CoinGiftService:
    @staticmethod
    async def get_config(session: AsyncSession) -> Dict[str, Any]:
        stored = await SettingsService.get_value(session, CONFIG_KEY, None)
        cfg = dict(DEFAULTS)
        if isinstance(stored, dict):
            cfg.update({k: v for k, v in stored.items() if k in DEFAULTS})
        return {
            "enabled": bool(cfg["enabled"]),
            "min_amount": max(1, int(cfg["min_amount"])),
            "daily_limit": max(0, int(cfg["daily_limit"])),
            "fee_percent": max(0, min(90, int(cfg["fee_percent"]))),
        }

    @staticmethod
    async def set_field(session: AsyncSession, field: str, value: Any) -> Dict[str, Any]:
        cfg = await CoinGiftService.get_config(session)
        if field == "enabled":
            cfg["enabled"] = bool(value)
        elif field in INT_FIELDS:
            val = int(value)
            if field == "fee_percent" and not (0 <= val <= 90):
                raise ValueError("Komissiya 0 dan 90 gacha bo‘lishi kerak.")
            if field == "min_amount" and val < 1:
                raise ValueError("Minimal miqdor kamida 1 bo‘lishi kerak.")
            if val < 0 or val > 1_000_000_000:
                raise ValueError("Qiymat noto‘g‘ri.")
            cfg[field] = val
        else:
            raise ValueError("Noma’lum sozlama.")
        await SettingsService.set_value(session, CONFIG_KEY, cfg)
        return cfg

    @staticmethod
    async def sent_today(session: AsyncSession, user_id: int) -> int:
        start_local = datetime.now(TASHKENT).replace(hour=0, minute=0, second=0, microsecond=0)
        start_utc = start_local.astimezone(timezone.utc)
        total = (
            await session.execute(
                select(func.coalesce(func.sum(func.abs(CoinTransaction.amount)), 0)).where(
                    CoinTransaction.user_id == user_id,
                    CoinTransaction.tx_type == TX_SENT,
                    CoinTransaction.created_at >= start_utc,
                )
            )
        ).scalar_one()
        return int(total or 0)

    @staticmethod
    async def send_coins(
        session: AsyncSession, from_user_id: int, to_user_id: int, amount: int
    ) -> Tuple[bool, str, int, int]:
        """Qaytaradi: (ok, xabar, qabul qiluvchi olgan miqdor, yuboruvchi yangi balansi)."""
        cfg = await CoinGiftService.get_config(session)
        if not cfg["enabled"]:
            return False, "Coin yuborish hozircha o‘chirilgan.", 0, 0
        if from_user_id == to_user_id:
            return False, "O‘zingizga coin yubora olmaysiz.", 0, 0
        amount = int(amount)
        if amount < cfg["min_amount"]:
            return False, f"Minimal miqdor: {cfg['min_amount']:,} 🪙", 0, 0

        lock_mgr = get_lock_manager()
        async with lock_mgr.lock(f"coin_gift:{from_user_id}", ttl_ms=5000) as acquired:
            if not acquired:
                return False, "So‘rov bajarilmoqda, biroz kuting.", 0, 0
            sender = await session.get(User, from_user_id)
            recipient = await session.get(User, to_user_id)
            if sender is None or recipient is None:
                return False, "Foydalanuvchi topilmadi.", 0, 0
            if recipient.is_banned:
                return False, "Bu foydalanuvchiga coin yuborib bo‘lmaydi.", 0, 0
            if cfg["daily_limit"] > 0:
                already = await CoinGiftService.sent_today(session, from_user_id)
                left = cfg["daily_limit"] - already
                if amount > left:
                    return False, f"Kunlik limit: {cfg['daily_limit']:,} 🪙. Bugun yana {max(0, left):,} 🪙 yubora olasiz.", 0, 0
            fee = amount * cfg["fee_percent"] // 100
            received = amount - fee
            if received <= 0:
                return False, "Miqdor juda kichik.", 0, 0
            try:
                new_balance = await UserService.debit_coins(
                    session,
                    from_user_id,
                    amount,
                    TX_SENT,
                    f"Coin yuborildi → {recipient.display_name}"[:255],
                    meta={"to_user_id": to_user_id, "fee": fee},
                )
            except InsufficientCoinsError:
                return False, "Coinlaringiz yetarli emas.", 0, 0
            await UserService.credit_coins(
                session,
                to_user_id,
                received,
                TX_RECEIVED,
                f"Coin olindi ← {sender.display_name}"[:255],
                meta={"from_user_id": from_user_id, "fee": fee},
            )
            await session.flush()
            return True, "OK", received, int(new_balance)
