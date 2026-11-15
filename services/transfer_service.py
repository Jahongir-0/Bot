from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.redis_client import get_lock_manager
from models.base import utcnow
from models.card import Card
from models.market import Offer, PendingTransfer
from models.user import User
from models.user_card import UserCard
from services.user_service import InsufficientCoinsError, UserService


@dataclass
class TransferResult:
    success: bool
    message: str
    transfer: Optional[PendingTransfer] = None
    card: Optional[Card] = None
    from_user: Optional[User] = None
    to_user: Optional[User] = None


class TransferService:
    @staticmethod
    async def create_pending_transfer(
        session: AsyncSession,
        from_user_id: int,
        to_user_id: int,
        user_card_id: int,
        price: int = 0,
    ) -> TransferResult:
        if from_user_id == to_user_id:
            return TransferResult(False, "O‘zingizga o‘zingiz karta bera olmaysiz.")
        if price < 0:
            return TransferResult(False, "Narx manfiy bo‘lishi mumkin emas.")

        lock_mgr = get_lock_manager()
        async with lock_mgr.lock(f"uc:{user_card_id}", ttl_ms=5000) as acquired:
            if not acquired:
                return TransferResult(False, "Karta hozir band, qayta urining.")

            upd = await session.execute(
                update(UserCard)
                .where(
                    UserCard.id == user_card_id,
                    UserCard.user_id == from_user_id,
                    UserCard.is_locked.is_(False),
                )
                .values(is_locked=True, updated_at=utcnow())
            )
            if upd.rowcount != 1:
                return TransferResult(
                    False,
                    "Karta topilmadi, sizga tegishli emas yoki hozir sotuvda/transferda.",
                )

            uc = await session.get(UserCard, user_card_id)
            card = await session.get(Card, uc.card_id)
            to_user = await session.get(User, to_user_id)
            from_user = await session.get(User, from_user_id)

            transfer = PendingTransfer(
                user_card_id=uc.id,
                from_user_id=from_user_id,
                to_user_id=to_user_id,
                price=price,
                status="pending",
                expires_at=utcnow() + timedelta(minutes=15),
            )
            session.add(transfer)
            await session.flush()
            return TransferResult(
                True,
                "Tasdiqlash so‘rovi yaratildi.",
                transfer=transfer,
                card=card,
                from_user=from_user,
                to_user=to_user,
            )

    @staticmethod
    async def confirm_transfer(
        session: AsyncSession,
        transfer_id: int,
        actor_user_id: int,
    ) -> TransferResult:
        lock_mgr = get_lock_manager()
        async with lock_mgr.lock(f"trf:{transfer_id}", ttl_ms=5000) as acquired:
            if not acquired:
                return TransferResult(False, "Transfer band, qayta urining.")

            trf = await session.get(PendingTransfer, transfer_id)
            if trf is None or trf.status != "pending":
                return TransferResult(False, "Transfer topilmadi yoki allaqachon yakunlangan.")

            # For paid transfers, recipient must confirm (pays coins); for free gifts, sender confirms
            expected_actor = trf.to_user_id if trf.price > 0 else trf.from_user_id
            if actor_user_id != expected_actor:
                return TransferResult(False, "Bu transferni tasdiqlash huquqingiz yo‘q.")

            now = utcnow()
            if trf.expires_at < now:
                trf.status = "expired"
                await session.execute(
                    update(UserCard)
                    .where(UserCard.id == trf.user_card_id)
                    .values(is_locked=False, updated_at=now)
                )
                await session.flush()
                return TransferResult(False, "Transfer muddati tugagan.")

            upd = await session.execute(
                update(PendingTransfer)
                .where(PendingTransfer.id == transfer_id, PendingTransfer.status == "pending")
                .values(status="completed", updated_at=now)
            )
            if upd.rowcount != 1:
                return TransferResult(False, "Transfer allaqachon bajarilgan.")

            uc = await session.get(UserCard, trf.user_card_id)
            card = await session.get(Card, uc.card_id)
            from_user = await session.get(User, trf.from_user_id)
            to_user = await session.get(User, trf.to_user_id)

            if trf.price > 0:
                try:
                    await UserService.debit_coins(
                        session=session,
                        user_id=trf.to_user_id,
                        amount=trf.price,
                        tx_type="paid_transfer_buy",
                        description=f"Paid transfer for {card.character_name}",
                        reference_id=f"trf:{trf.id}",
                    )
                except InsufficientCoinsError:
                    raise

                await UserService.credit_coins(
                    session=session,
                    user_id=trf.from_user_id,
                    amount=trf.price,
                    tx_type="paid_transfer_sell",
                    description=f"Paid transfer received for {card.character_name}",
                    reference_id=f"trf:{trf.id}",
                )

            await session.execute(
                update(UserCard)
                .where(UserCard.id == trf.user_card_id)
                .values(
                    user_id=trf.to_user_id,
                    is_locked=False,
                    is_favorite=False,
                    acquired_via="transfer",
                    updated_at=now,
                )
            )

            # Refund any open offers on this card instance
            pending_offers = (
                await session.execute(
                    select(Offer).where(Offer.user_card_id == trf.user_card_id, Offer.status == "pending")
                )
            ).scalars().all()
            for off in pending_offers:
                off.status = "cancelled"
                off.resolved_at = now
                await UserService.credit_coins(
                    session=session,
                    user_id=off.buyer_id,
                    amount=off.amount,
                    tx_type="offer_refund",
                    description="Card transferred - offer refunded",
                    reference_id=f"offer:{off.id}",
                )

            await UserService.recompute_card_counts(session, trf.from_user_id)
            await UserService.recompute_card_counts(session, trf.to_user_id, add_xp=10)
            await session.flush()

            return TransferResult(
                True,
                "Karta muvaffaqiyatli topshirildi!",
                transfer=trf,
                card=card,
                from_user=from_user,
                to_user=to_user,
            )

    @staticmethod
    async def cancel_transfer(
        session: AsyncSession,
        transfer_id: int,
        actor_user_id: int,
    ) -> TransferResult:
        trf = await session.get(PendingTransfer, transfer_id)
        if trf is None or trf.status != "pending":
            return TransferResult(False, "Transfer topilmadi yoki yakunlangan.")
        if actor_user_id not in (trf.from_user_id, trf.to_user_id):
            return TransferResult(False, "Bekor qilish huquqingiz yo‘q.")

        now = utcnow()
        upd = await session.execute(
            update(PendingTransfer)
            .where(PendingTransfer.id == transfer_id, PendingTransfer.status == "pending")
            .values(status="cancelled", updated_at=now)
        )
        if upd.rowcount == 1:
            await session.execute(
                update(UserCard)
                .where(UserCard.id == trf.user_card_id)
                .values(is_locked=False, updated_at=now)
            )
            await session.flush()
            return TransferResult(True, "Transfer bekor qilindi.", transfer=trf)
        return TransferResult(False, "Transfer allaqachon yopilgan.")
