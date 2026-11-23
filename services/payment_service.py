import hashlib
import hmac
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from models.transaction import StarPayment
from services.settings_service import SettingsService
from services.user_service import UserService


class PaymentService:
    @staticmethod
    def _sign_payload(order_uid: str, telegram_user_id: int, stars: int) -> str:
        secret = get_settings().stars_payment_secret.encode("utf-8")
        msg = f"{order_uid}:{telegram_user_id}:{stars}".encode("utf-8")
        sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()[:16]
        return f"stars:{order_uid}:{sig}"

    @staticmethod
    def verify_payload(payload: str, telegram_user_id: int, stars: int) -> Optional[str]:
        parts = payload.split(":")
        if len(parts) != 3 or parts[0] != "stars":
            return None
        order_uid, sig = parts[1], parts[2]
        expected = PaymentService._sign_payload(order_uid, telegram_user_id, stars)
        if hmac.compare_digest(payload, expected):
            return order_uid
        return None

    @staticmethod
    async def is_charge_processed(session: AsyncSession, telegram_payment_charge_id: str) -> bool:
        """Shu Telegram to'lovi allaqachon coin sifatida hisobga olinganmi (takroriy xabar himoyasi)."""
        row = (
            await session.execute(
                select(StarPayment.id).where(
                    StarPayment.telegram_payment_charge_id == telegram_payment_charge_id
                )
            )
        ).first()
        return row is not None

    @staticmethod
    async def create_star_order(
        session: AsyncSession,
        user_id: int,
        telegram_user_id: int,
        package_id: str,
    ) -> Optional[tuple[StarPayment, str, str]]:
        packages = await SettingsService.get_stars_packages(session)
        pkg = next((p for p in packages if p.get("id") == package_id), None)
        if pkg is None:
            return None

        stars = int(pkg["stars"])
        coins = int(pkg["coins"])
        order_uid = uuid.uuid4().hex[:16]
        payload = PaymentService._sign_payload(order_uid, telegram_user_id, stars)

        order = StarPayment(
            order_uid=order_uid,
            user_id=user_id,
            package_id=package_id,
            stars_amount=stars,
            coins_amount=coins,
            status="pending",
        )
        session.add(order)
        await session.flush()
        return order, payload, str(pkg.get("label", f"{coins:,} coins"))

    @staticmethod
    async def verify_pre_checkout(
        session: AsyncSession,
        payload: str,
        telegram_user_id: int,
        currency: str,
        total_amount: int,
    ) -> bool:
        if currency != "XTR":
            return False
        order_uid = PaymentService.verify_payload(payload, telegram_user_id, total_amount)
        if order_uid is None:
            return False

        order = (
            await session.execute(select(StarPayment).where(StarPayment.order_uid == order_uid))
        ).scalar_one_or_none()
        if order is None or order.status != "pending":
            return False
        return order.stars_amount == total_amount

    @staticmethod
    async def complete_star_payment(
        session: AsyncSession,
        payload: str,
        telegram_user_id: int,
        currency: str,
        total_amount: int,
        telegram_payment_charge_id: str,
        provider_payment_charge_id: str,
    ) -> Optional[StarPayment]:
        if currency != "XTR":
            return None
        order_uid = PaymentService.verify_payload(payload, telegram_user_id, total_amount)
        if order_uid is None:
            return None

        # Idempotency check against duplicate Telegram charge IDs
        dup = (
            await session.execute(
                select(StarPayment).where(
                    StarPayment.telegram_payment_charge_id == telegram_payment_charge_id
                )
            )
        ).scalar_one_or_none()
        if dup is not None:
            return None

        # Lock the order row so two concurrent successful_payment updates cannot both
        # observe `pending` and credit the same order twice. PostgreSQL (Railway)
        # enforces this at the database transaction level.
        order = (
            await session.execute(
                select(StarPayment)
                .where(StarPayment.order_uid == order_uid)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if order is None or order.status != "pending" or order.stars_amount != total_amount:
            return None

        order.status = "paid"
        order.telegram_payment_charge_id = telegram_payment_charge_id
        order.provider_payment_charge_id = provider_payment_charge_id

        await UserService.credit_coins(
            session=session,
            user_id=order.user_id,
            amount=order.coins_amount,
            tx_type="telegram_stars_purchase",
            description=f"Purchased {order.coins_amount:,} coins for {order.stars_amount} Stars",
            reference_id=telegram_payment_charge_id,
        )
        await session.flush()
        return order
