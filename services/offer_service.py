from dataclasses import dataclass
from datetime import timedelta
from typing import List, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.redis_client import get_lock_manager
from models.base import utcnow
from models.card import Card
from models.market import CardListing, Offer
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
    offered_card: Optional[Card] = None
    offered_user_card: Optional[UserCard] = None
    owner: Optional[User] = None
    buyer: Optional[User] = None
    amount: int = 0
    fee_coins: int = 0
    fee_pct: int = 0
    offer_type: str = "coins"
    is_duplicate: bool = False
    level_before: int = 0
    level_after: int = 0
    maxed: bool = False
    refund: int = 0


class OfferService:
    @staticmethod
    async def _refund_or_unlock_offer(
        session: AsyncSession,
        off: Offer,
        tx_type: str,
        description: str,
    ) -> None:
        now = utcnow()
        if getattr(off, "offer_type", "coins") == "card" and getattr(off, "offered_user_card_id", None):
            await session.execute(
                update(UserCard)
                .where(UserCard.id == off.offered_user_card_id)
                .values(is_locked=False, card_state="OWNED", updated_at=now)
            )
        elif int(off.amount or 0) > 0:
            await UserService.credit_coins(
                session=session,
                user_id=off.buyer_id,
                amount=int(off.amount),
                tx_type=tx_type,
                description=description,
                reference_id=f"offer:{off.id}",
            )

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
                await OfferService._refund_or_unlock_offer(
                    session, off, "offer_expired_refund", "Expired offer escrow refund"
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

        # Faol market e'lonida turgan kartaga ham taklif yuborishga ruxsat beramiz (boshqa holda band bo'lsa ruxsat yo'q)
        if uc.is_locked:
            active_listing = (
                await session.execute(
                    select(CardListing.id).where(
                        CardListing.user_card_id == uc.id, CardListing.status == "active"
                    )
                )
            ).first()
            if active_listing is None:
                return OfferResult(False, "Bu karta hozir transfer yoki auksion jarayonida band.")

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
            offer_type="coins",
            offered_user_card_id=None,
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
            offer_type="coins",
        )

    @staticmethod
    async def create_card_offer(
        session: AsyncSession,
        buyer_id: int,
        target_user_card_id: int,
        offered_user_card_id: int,
    ) -> OfferResult:
        if target_user_card_id == offered_user_card_id:
            return OfferResult(False, "Bir xil kartani taklif qilib bo‘lmaydi.")

        await OfferService.expire_stale_offers(session)

        target_uc = await session.get(UserCard, target_user_card_id)
        if target_uc is None:
            return OfferResult(False, "Nishon karta topilmadi.")
        if target_uc.user_id == buyer_id:
            return OfferResult(False, "O‘z kartangizga taklif yubora olmaysiz.")
        if target_uc.is_locked:
            active_listing = (
                await session.execute(
                    select(CardListing.id).where(
                        CardListing.user_card_id == target_uc.id, CardListing.status == "active"
                    )
                )
            ).first()
            if active_listing is None:
                return OfferResult(False, "Bu karta hozir transfer yoki auksion jarayonida band.")

        existing = (
            await session.execute(
                select(Offer).where(
                    Offer.user_card_id == target_user_card_id,
                    Offer.buyer_id == buyer_id,
                    Offer.status == "pending",
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return OfferResult(False, "Siz bu kartaga allaqachon faol taklif yuborgansiz.")

        upd_off = await session.execute(
            update(UserCard)
            .where(
                UserCard.id == offered_user_card_id,
                UserCard.user_id == buyer_id,
                UserCard.is_locked.is_(False),
            )
            .values(is_locked=True, card_state="OFFER", updated_at=utcnow())
        )
        if upd_off.rowcount != 1:
            return OfferResult(False, "Taklif qilinadigan kartangiz topilmadi yoki hozir band.")

        offered_uc = await session.get(UserCard, offered_user_card_id)
        card = await session.get(Card, target_uc.card_id)
        offered_card = await session.get(Card, offered_uc.card_id)
        owner = await session.get(User, target_uc.user_id)
        buyer = await session.get(User, buyer_id)
        expiry_hours = await SettingsService.get_offer_expiry_hours(session)

        offer = Offer(
            user_card_id=target_uc.id,
            card_id=target_uc.card_id,
            buyer_id=buyer_id,
            owner_id=target_uc.user_id,
            amount=0,
            offer_type="card",
            offered_user_card_id=offered_uc.id,
            status="pending",
            expires_at=utcnow() + timedelta(hours=expiry_hours),
        )
        session.add(offer)
        await session.flush()

        return OfferResult(
            True,
            "Karta taklifi egasiga yuborildi!",
            offer=offer,
            card=card,
            offered_card=offered_card,
            offered_user_card=offered_uc,
            owner=owner,
            buyer=buyer,
            amount=0,
            offer_type="card",
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

            uc = await session.get(UserCard, offer.user_card_id)
            if uc is None or uc.user_id != actor_owner_id:
                return OfferResult(False, "Karta topilmadi yoki sizga tegishli emas.")

            # Agar karta marketda turgan bo'lsa, taklif qabul qilinganda listingni yopamiz
            active_listing = None
            if uc.is_locked:
                active_listing = (
                    await session.execute(
                        select(CardListing).where(
                            CardListing.user_card_id == uc.id, CardListing.status == "active"
                        )
                    )
                ).scalar_one_or_none()
                if active_listing is None:
                    return OfferResult(False, "Karta hozir boshqa tranzaksiyada band.")

            now = utcnow()
            offer_type = getattr(offer, "offer_type", "coins") or "coins"
            offered_uc = (
                await session.get(UserCard, offer.offered_user_card_id)
                if offer_type == "card" and getattr(offer, "offered_user_card_id", None)
                else None
            )
            if offer_type == "card":
                if offered_uc is None or offered_uc.user_id != offer.buyer_id:
                    return OfferResult(False, "Taklif qilingan karta endi mavjud emas.")

            upd = await session.execute(
                update(Offer)
                .where(Offer.id == offer_id, Offer.status == "pending")
                .values(status="accepted", resolved_at=now, updated_at=now)
            )
            if upd.rowcount != 1:
                return OfferResult(False, "Taklif allaqachon yakunlangan.")

            if active_listing is not None:
                active_listing.status = "sold"
                active_listing.buyer_id = offer.buyer_id
                active_listing.sold_at = now
                active_listing.updated_at = now

            card = await session.get(Card, offer.card_id)
            offered_card = await session.get(Card, offered_uc.card_id) if offered_uc else None
            buyer = await session.get(User, offer.buyer_id)
            owner = await session.get(User, offer.owner_id)
            offer_id_val = offer.id
            offer_amount = int(offer.amount or 0)
            offer_owner_id = offer.owner_id
            offer_buyer_id = offer.buyer_id
            uc_id = uc.id

            fee_coins, fee_pct = 0, 0
            if offer_type == "coins" and offer_amount > 0:
                comm_cfg = await SettingsService.get_commission_config(session)
                fee_coins, fee_pct = SettingsService.compute_commission(offer_amount, comm_cfg, "market_offer")
                seller_payout = max(1, offer_amount - fee_coins)
                await UserService.credit_coins(
                    session=session,
                    user_id=offer_owner_id,
                    amount=seller_payout,
                    tx_type="offer_accepted_sale",
                    description=f"Accepted offer for {card.character_name}"
                    + (f" (-{fee_pct}% komissiya)" if fee_coins else ""),
                    reference_id=f"offer:{offer_id_val}",
                    meta={"commission": fee_coins, "pct": fee_pct},
                )

            # Refund/unlock competing pending offers BEFORE possible deletion of uc (FK CASCADE)
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
                await OfferService._refund_or_unlock_offer(
                    session, comp, "offer_refund", "Another offer accepted - escrow refunded"
                )

            from services.card_level_service import CardLevelService

            # 1) Offered card -> original owner (if card-for-card offer)
            if offer_type == "card" and offered_uc is not None and offered_card is not None:
                if await UserService.owns_card(session, offer_owner_id, offered_uc.card_id):
                    await CardLevelService.apply_duplicate(session, offer_owner_id, offered_card)
                    await session.delete(offered_uc)
                    await session.flush()
                else:
                    await session.execute(
                        update(UserCard)
                        .where(UserCard.id == offered_uc.id)
                        .values(
                            user_id=offer_owner_id,
                            is_locked=False,
                            is_favorite=False,
                            is_showcased=False,
                            card_state="OWNED",
                            acquired_via="offer_trade",
                            acquired_price_coins=0,
                            updated_at=now,
                        )
                    )

            # 2) Target card -> buyer
            dup = None
            if await UserService.owns_card(session, offer_buyer_id, uc.card_id):
                dup = await CardLevelService.apply_duplicate(session, offer_buyer_id, card)
                await session.delete(uc)
                await session.flush()
            else:
                await session.execute(
                    update(UserCard)
                    .where(UserCard.id == uc_id)
                    .values(
                        user_id=offer_buyer_id,
                        is_locked=False,
                        is_favorite=False,
                        is_showcased=False,
                        card_state="OWNED",
                        acquired_via="offer",
                        acquired_price_coins=offer_amount if offer_type == "coins" else 0,
                        updated_at=now,
                    )
                )

            await UserService.recompute_card_counts(session, offer_owner_id)
            await UserService.recompute_card_counts(session, offer_buyer_id, add_xp=15)
            await session.flush()

            return OfferResult(
                True,
                "Taklif qabul qilindi! Karta almashinuvi bajarildi.",
                offer=None if dup is not None else offer,
                card=card,
                offered_card=offered_card,
                owner=owner,
                buyer=buyer,
                amount=offer_amount,
                fee_coins=fee_coins,
                fee_pct=fee_pct,
                offer_type=offer_type,
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

            await OfferService._refund_or_unlock_offer(
                session, offer, "offer_refund", "Offer rejected/cancelled - escrow refunded"
            )
            card = await session.get(Card, offer.card_id)
            buyer = await session.get(User, offer.buyer_id)
            owner = await session.get(User, offer.owner_id)
            offered_card = None
            if getattr(offer, "offer_type", "coins") == "card" and getattr(offer, "offered_user_card_id", None):
                ouc = await session.get(UserCard, offer.offered_user_card_id)
                if ouc:
                    offered_card = await session.get(Card, ouc.card_id)
            await session.flush()

            msg = (
                "Taklif bekor qilindi va band qilingan qiymat qaytarildi."
                if new_status == "cancelled"
                else "Taklif rad etildi va band qilingan qiymat xaridorga qaytarildi."
            )
            return OfferResult(
                True,
                msg,
                offer=offer,
                card=card,
                offered_card=offered_card,
                owner=owner,
                buyer=buyer,
                offer_type=getattr(offer, "offer_type", "coins") or "coins",
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

    @staticmethod
    async def get_outgoing_offers(
        session: AsyncSession,
        buyer_id: int,
    ) -> List[Tuple[Offer, Card, User]]:
        await OfferService.expire_stale_offers(session)
        rows = (
            await session.execute(
                select(Offer, Card, User)
                .join(Card, Card.id == Offer.card_id)
                .join(User, User.id == Offer.owner_id)
                .where(Offer.buyer_id == buyer_id, Offer.status == "pending")
                .order_by(Offer.created_at.desc())
            )
        ).all()
        return [(r[0], r[1], r[2]) for r in rows]
