from dataclasses import dataclass
from datetime import timedelta
from typing import List, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.redis_client import get_lock_manager
from models.base import utcnow
from models.card import Card
from models.market import Offer
from models.user import User
from models.user_card import UserCard
from services.settings_service import SettingsService
from services.user_service import InsufficientCoinsError, UserService
from utils.constants import MAX_CARD_PRICE, MIN_CARD_PRICE


@dataclass
class OfferResult:
    success: bool
    message: str
    offer: Optional[Offer] = None
    card: Optional[Card] = None
    owner: Optional[User] = None
    buyer: Optional[User] = None
    amount: int = 0
    is_duplicate: bool = False
    level_before: int = 0
    level_after: int = 0
    maxed: bool = False
    refund: int = 0


class OfferService:
    @staticmethod
    async def expire_stale_offers(session: AsyncSession) -> int:
        now = utcnow()
        stale = (
            await session.execute(
                select(Offer).where(Offer.status == "pending", Offer.expires_at < now)
            )
        ).scalars().all()
        count = 0
        for off in stale:
            upd = await session.execute(
                update(Offer)
                .where(Offer.id == off.id, Offer.status == "pending")
                .values(status="expired", resolved_at=now, updated_at=now)
            )
            if upd.rowcount == 1:
                await UserService.credit_coins(
                    session=session,
                    user_id=off.buyer_id,
                    amount=off.amount,
                    tx_type="offer_expired_refund",
                    description="Expired offer escrow refund",
                    reference_id=f"offer:{off.id}",
                )
                count += 1
        if count:
            await session.flush()
        return count

    @staticmethod
    async def create_offer(
        session: AsyncSession,
        buyer_id: int,
        user_card_id: int,
        amount: int,
    ) -> OfferResult:
        if amount < MIN_CARD_PRICE or amount > MAX_CARD_PRICE:
            return OfferResult(
                False,
                f"Taklif summasi {MIN_CARD_PRICE:,} dan {MAX_CARD_PRICE:,} coins gacha bo‘lishi kerak.",
            )

        await OfferService.expire_stale_offers(session)

        uc = await session.get(UserCard, user_card_id)
        if uc is None:
            return OfferResult(False, "Karta topilmadi.")
        if uc.user_id == buyer_id:
            return OfferResult(False, "O‘z kartangizga taklif yubora olmaysiz.")
        if uc.is_locked:
            return OfferResult(False, "Bu karta hozir marketda yoki transfer jarayonida.")

        # Prevent duplicate pending offer spam from same buyer on same card
        existing = (
            await session.execute(
                select(Offer).where(
                    Offer.user_card_id == user_card_id,
                    Offer.buyer_id == buyer_id,
                    Offer.status == "pending",
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return OfferResult(False, "Siz bu kartaga allaqachon faol taklif yuborgansiz.")

        card = await session.get(Card, uc.card_id)
        owner = await session.get(User, uc.user_id)
        buyer = await session.get(User, buyer_id)
        expiry_hours = await SettingsService.get_offer_expiry_hours(session)

        # Escrow buyer coins atomically to eliminate fake/spam offers
        try:
            await UserService.debit_coins(
                session=session,
                user_id=buyer_id,
                amount=amount,
                tx_type="offer_escrow",
                description=f"Escrowed offer for {card.character_name}",
                reference_id=f"uc:{uc.id}",
            )
        except InsufficientCoinsError:
            return OfferResult(False, "Taklif yuborish uchun hisobingizda coin yetarli emas.")

        offer = Offer(
            user_card_id=uc.id,
            card_id=uc.card_id,
            buyer_id=buyer_id,
            owner_id=uc.user_id,
            amount=amount,
            status="pending",
            expires_at=utcnow() + timedelta(hours=expiry_hours),
        )
        session.add(offer)
        await session.flush()

        return OfferResult(
            True,
            "Taklif egasiga yuborildi!",
            offer=offer,
            card=card,
            owner=owner,
            buyer=buyer,
            amount=amount,
        )

    @staticmethod
    async def accept_offer(
        session: AsyncSession,
        offer_id: int,
        actor_owner_id: int,
    ) -> OfferResult:
        lock_mgr = get_lock_manager()
        async with lock_mgr.lock(f"offer:{offer_id}", ttl_ms=5000) as acquired:
            if not acquired:
                return OfferResult(False, "Taklif qayta ishlanmoqda.")

            await OfferService.expire_stale_offers(session)

            offer = await session.get(Offer, offer_id)
            if offer is None or offer.status != "pending":
                return OfferResult(False, "Taklif topilmadi yoki muddati tugagan.")
            if offer.owner_id != actor_owner_id:
                return OfferResult(False, "Faqat karta egasi taklifni qabul qila oladi.")

            if offer.user_card_id is None:
                return OfferResult(False, "Taklif kartasi topilmadi yoki bekor qilingan.")
            uc = await session.get(UserCard, offer.user_card_id)
            if uc is None or uc.user_id != actor_owner_id or uc.is_locked:
                return OfferResult(False, "Karta hozir boshqa tranzaksiyada band yoki sizga tegishli emas.")

            now = utcnow()
            upd = await session.execute(
                update(Offer)
                .where(Offer.id == offer_id, Offer.status == "pending")
                .values(status="accepted", resolved_at=now, updated_at=now)
            )
            if upd.rowcount != 1:
                return OfferResult(False, "Taklif allaqachon yakunlangan.")

            card = await session.get(Card, offer.card_id)
            buyer = await session.get(User, offer.buyer_id)
            owner = await session.get(User, offer.owner_id)
            offer_id_val = offer.id
            offer_amount = int(offer.amount)
            offer_owner_id = offer.owner_id
            offer_buyer_id = offer.buyer_id
            uc_id = uc.id

            # Release escrowed coins to seller
            await UserService.credit_coins(
                session=session,
                user_id=offer_owner_id,
                amount=offer_amount,
                tx_type="offer_accepted_sale",
                description=f"Accepted offer for {card.character_name}",
                reference_id=f"offer:{offer_id_val}",
            )

            # Refund any competing pending offers BEFORE possible deletion of uc (FK CASCADE)
            competing = (
                await session.execute(
                    select(Offer).where(
                        Offer.user_card_id == uc_id,
                        Offer.status == "pending",
                        Offer.id != offer_id_val,
                    )
                )
            ).scalars().all()
            for comp in competing:
                comp.status = "cancelled"
                comp.resolved_at = now
                await UserService.credit_coins(
                    session=session,
                    user_id=comp.buyer_id,
                    amount=comp.amount,
                    tx_type="offer_refund",
                    description="Another offer accepted - escrow refunded",
                    reference_id=f"offer:{comp.id}",
                )

            dup = None
            if await UserService.owns_card(session, offer_buyer_id, uc.card_id):
                from services.card_level_service import CardLevelService

                dup = await CardLevelService.apply_duplicate(session, offer_buyer_id, card)
                await session.delete(uc)
                await session.flush()
            else:
                await session.execute(
                    update(UserCard)
                    .where(UserCard.id == uc_id)
                    .values(
                        user_id=offer_buyer_id,
                        is_favorite=False,
                        is_showcased=False,
                        card_state="OWNED",
                        acquired_via="offer",
                        updated_at=now,
                    )
                )

            await UserService.recompute_card_counts(session, offer_owner_id)
            await UserService.recompute_card_counts(session, offer_buyer_id, add_xp=15)
            await session.flush()

            return OfferResult(
                True,
                "Taklif qabul qilindi! Karta va coinlar o‘tkazildi.",
                offer=None if dup is not None else offer,
                card=card,
                owner=owner,
                buyer=buyer,
                amount=offer_amount,
                is_duplicate=dup is not None,
                level_before=dup.level_before if dup else 1,
                level_after=dup.level_after if dup else 1,
                maxed=bool(dup and dup.maxed),
                refund=dup.refund if dup else 0,
            )

    @staticmethod
    async def reject_offer(
        session: AsyncSession,
        offer_id: int,
        actor_user_id: int,
    ) -> OfferResult:
        lock_mgr = get_lock_manager()
        async with lock_mgr.lock(f"offer:{offer_id}", ttl_ms=5000) as acquired:
            if not acquired:
                return OfferResult(False, "Amal band.")

            offer = await session.get(Offer, offer_id)
            if offer is None or offer.status != "pending":
                return OfferResult(False, "Taklif topilmadi yoki yakunlangan.")
            if actor_user_id not in (offer.owner_id, offer.buyer_id):
                return OfferResult(False, "Huquqingiz yo‘q.")

            now = utcnow()
            new_status = "rejected" if actor_user_id == offer.owner_id else "cancelled"
            upd = await session.execute(
                update(Offer)
                .where(Offer.id == offer_id, Offer.status == "pending")
                .values(status=new_status, resolved_at=now, updated_at=now)
            )
            if upd.rowcount != 1:
                return OfferResult(False, "Taklif allaqachon yopilgan.")

            await UserService.credit_coins(
                session=session,
                user_id=offer.buyer_id,
                amount=offer.amount,
                tx_type="offer_refund",
                description="Offer rejected/cancelled - escrow refunded",
                reference_id=f"offer:{offer.id}",
            )
            card = await session.get(Card, offer.card_id)
            buyer = await session.get(User, offer.buyer_id)
            owner = await session.get(User, offer.owner_id)
            await session.flush()

            return OfferResult(
                True,
                "Taklif rad etildi va coinlar xaridorga qaytarildi.",
                offer=offer,
                card=card,
                owner=owner,
                buyer=buyer,
            )

    @staticmethod
    async def get_incoming_offers(
        session: AsyncSession,
        owner_id: int,
    ) -> List[Tuple[Offer, Card, User]]:
        await OfferService.expire_stale_offers(session)
        rows = (
            await session.execute(
                select(Offer, Card, User)
                .join(Card, Card.id == Offer.card_id)
                .join(User, User.id == Offer.buyer_id)
                .where(Offer.owner_id == owner_id, Offer.status == "pending")
                .order_by(Offer.created_at.desc())
            )
        ).all()
        return [(r[0], r[1], r[2]) for r in rows]
