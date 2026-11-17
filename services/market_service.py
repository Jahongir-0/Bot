from dataclasses import dataclass
from typing import List, Optional, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.redis_client import get_lock_manager
from models.base import utcnow
from models.card import Card
from models.market import CardListing, Offer
from models.user import User
from models.user_card import UserCard
from services.user_service import InsufficientCoinsError, UserService
from utils.constants import MAX_CARD_PRICE, MIN_CARD_PRICE


@dataclass
class ListingView:
    listing: CardListing
    card: Card
    seller: User


@dataclass
class MarketActionResult:
    success: bool
    message: str
    listing: Optional[CardListing] = None
    card: Optional[Card] = None
    seller: Optional[User] = None


class MarketService:
    @staticmethod
    async def list_card(
        session: AsyncSession,
        seller_id: int,
        user_card_id: int,
        price: int,
    ) -> MarketActionResult:
        if price < MIN_CARD_PRICE or price > MAX_CARD_PRICE:
            return MarketActionResult(
                False,
                f"Narx {MIN_CARD_PRICE:,} dan {MAX_CARD_PRICE:,} coins gacha bo‘lishi kerak.",
            )

        lock_mgr = get_lock_manager()
        async with lock_mgr.lock(f"uc:{user_card_id}", ttl_ms=5000) as acquired:
            if not acquired:
                return MarketActionResult(False, "Karta hozir band, qayta urining.")

            upd = await session.execute(
                update(UserCard)
                .where(
                    UserCard.id == user_card_id,
                    UserCard.user_id == seller_id,
                    UserCard.is_locked.is_(False),
                )
                .values(is_locked=True, updated_at=utcnow())
            )
            if upd.rowcount != 1:
                return MarketActionResult(
                    False,
                    "Karta topilmadi, sizga tegishli emas yoki allaqachon sotuvda/band.",
                )

            uc = await session.get(UserCard, user_card_id)
            card = await session.get(Card, uc.card_id)
            listing = CardListing(
                user_card_id=uc.id,
                card_id=uc.card_id,
                seller_id=seller_id,
                price=price,
                status="active",
            )
            session.add(listing)
            await session.flush()
            return MarketActionResult(
                True,
                "Karta marketga muvaffaqiyatli qo‘yildi!",
                listing=listing,
                card=card,
            )

    @staticmethod
    async def cancel_listing(
        session: AsyncSession,
        listing_id: int,
        actor_user_id: int,
    ) -> MarketActionResult:
        lock_mgr = get_lock_manager()
        async with lock_mgr.lock(f"listing:{listing_id}", ttl_ms=5000) as acquired:
            if not acquired:
                return MarketActionResult(False, "Amal band, birozdan so‘ng urining.")

            upd = await session.execute(
                update(CardListing)
                .where(
                    CardListing.id == listing_id,
                    CardListing.seller_id == actor_user_id,
                    CardListing.status == "active",
                )
                .values(status="cancelled", updated_at=utcnow())
            )
            if upd.rowcount != 1:
                return MarketActionResult(False, "E'lon topilmadi yoki allaqachon yopilgan.")

            listing = await session.get(CardListing, listing_id)
            await session.execute(
                update(UserCard)
                .where(UserCard.id == listing.user_card_id)
                .values(is_locked=False, updated_at=utcnow())
            )
            await session.flush()
            return MarketActionResult(True, "Karta sotuvdan olindi.", listing=listing)

    @staticmethod
    async def buy_listing(
        session: AsyncSession,
        listing_id: int,
        buyer_id: int,
    ) -> MarketActionResult:
        lock_mgr = get_lock_manager()
        async with lock_mgr.lock(f"listing:{listing_id}", ttl_ms=5000) as acquired:
            if not acquired:
                return MarketActionResult(False, "Xarid jarayoni band, qayta urining.")

            listing = await session.get(CardListing, listing_id)
            if listing is None or listing.status != "active":
                return MarketActionResult(False, "Ushbu karta allaqachon sotilgan yoki olib tashlangan.")
            if listing.seller_id == buyer_id:
                return MarketActionResult(False, "O‘zingizning kartangizni o‘zingiz sotib ololmaysiz!")
            if await UserService.owns_card(session, buyer_id, listing.card_id):
                return MarketActionResult(False, "Bu kartadan sizda allaqachon bor — bir xil kartadan faqat bitta nusxa olish mumkin.")

            now = utcnow()
            upd = await session.execute(
                update(CardListing)
                .where(
                    CardListing.id == listing_id,
                    CardListing.status == "active",
                    CardListing.seller_id != buyer_id,
                )
                .values(status="sold", buyer_id=buyer_id, sold_at=now, updated_at=now)
            )
            if upd.rowcount != 1:
                return MarketActionResult(False, "Karta allaqachon sotib olingan.")

            card = await session.get(Card, listing.card_id)
            seller = await session.get(User, listing.seller_id)

            try:
                await UserService.debit_coins(
                    session=session,
                    user_id=buyer_id,
                    amount=listing.price,
                    tx_type="market_buy",
                    description=f"Bought {card.character_name} on market",
                    reference_id=f"listing:{listing.id}",
                )
            except InsufficientCoinsError:
                raise

            await UserService.credit_coins(
                session=session,
                user_id=listing.seller_id,
                amount=listing.price,
                tx_type="market_sell",
                description=f"Sold {card.character_name} on market",
                reference_id=f"listing:{listing.id}",
            )

            # Transfer ownership and unlock card
            await session.execute(
                update(UserCard)
                .where(UserCard.id == listing.user_card_id)
                .values(
                    user_id=buyer_id,
                    is_locked=False,
                    is_favorite=False,
                    acquired_via="market",
                    updated_at=now,
                )
            )

            # Refund any pending offers on this user_card
            pending_offers = (
                await session.execute(
                    select(Offer).where(
                        Offer.user_card_id == listing.user_card_id,
                        Offer.status == "pending",
                    )
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
                    description="Card sold on market - offer refunded",
                    reference_id=f"offer:{off.id}",
                )

            if seller is not None:
                s_stats = dict(seller.stats or {})
                s_stats["sales"] = int(s_stats.get("sales", 0)) + 1
                seller.stats = s_stats

            await UserService.recompute_card_counts(session, listing.seller_id)
            await UserService.recompute_card_counts(session, buyer_id, add_xp=15)
            await session.flush()

            return MarketActionResult(
                True,
                "Karta muvaffaqiyatli sotib olindi!",
                listing=listing,
                card=card,
                seller=seller,
            )

    @staticmethod
    async def get_active_listings(
        session: AsyncSession,
        page: int = 1,
        page_size: int = 5,
        seller_id: Optional[int] = None,
    ) -> Tuple[List[ListingView], int]:
        base_stmt = (
            select(CardListing, Card, User)
            .join(Card, Card.id == CardListing.card_id)
            .join(User, User.id == CardListing.seller_id)
            .where(CardListing.status == "active")
        )
        count_stmt = select(func.count(CardListing.id)).where(CardListing.status == "active")

        if seller_id is not None:
            base_stmt = base_stmt.where(CardListing.seller_id == seller_id)
            count_stmt = count_stmt.where(CardListing.seller_id == seller_id)

        total = int((await session.execute(count_stmt)).scalar_one() or 0)
        offset = (max(1, page) - 1) * page_size
        rows = (
            await session.execute(
                base_stmt.order_by(CardListing.created_at.desc()).offset(offset).limit(page_size)
            )
        ).all()
        return [ListingView(listing=r[0], card=r[1], seller=r[2]) for r in rows], total

    @staticmethod
    async def get_listing_detail(
        session: AsyncSession,
        listing_id: int,
    ) -> Optional[ListingView]:
        row = (
            await session.execute(
                select(CardListing, Card, User)
                .join(Card, Card.id == CardListing.card_id)
                .join(User, User.id == CardListing.seller_id)
                .where(CardListing.id == listing_id)
            )
        ).first()
        if row is None:
            return None
        return ListingView(listing=row[0], card=row[1], seller=row[2])
