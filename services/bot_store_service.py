"""Bot Do'koni xizmati: userlar sotgan va admin qo'shgan kartalarni ko'rish va sotib olish."""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.redis_client import get_lock_manager
from models.base import utcnow
from models.card import Card
from models.user import User
from models.user_card import UserCard
from models.v2_features import BotStoreListing, CardHistory
from services.card_level_service import CardLevelService
from services.gameplay_service import GameplayService
from services.settings_service import SettingsService
from services.user_service import InsufficientCoinsError, UserService


@dataclass
class BotStoreBuyResult:
    success: bool
    message: str
    card: Optional[Card] = None
    price_paid: int = 0
    is_duplicate: bool = False
    level_before: int = 1
    level_after: int = 1
    maxed: bool = False
    refund: int = 0


class BotStoreService:
    @staticmethod
    async def list_active_listings(
        session: AsyncSession,
        page: int = 1,
        page_size: int = 5,
    ) -> Tuple[List[Tuple[BotStoreListing, Card]], int]:
        cond = [BotStoreListing.status == "active", Card.is_deleted.is_(False)]
        total = int(
            (
                await session.execute(
                    select(func.count(BotStoreListing.id))
                    .join(Card, Card.id == BotStoreListing.card_id)
                    .where(*cond)
                )
            ).scalar_one()
            or 0
        )
        rows = (
            await session.execute(
                select(BotStoreListing, Card)
                .join(Card, Card.id == BotStoreListing.card_id)
                .where(*cond)
                .order_by(BotStoreListing.id.desc())
                .offset((max(1, page) - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return [(r[0], r[1]) for r in rows], total

    @staticmethod
    async def get_listing_detail(
        session: AsyncSession, listing_id: int
    ) -> Optional[Tuple[BotStoreListing, Card, Optional[User]]]:
        row = (
            await session.execute(
                select(BotStoreListing, Card)
                .join(Card, Card.id == BotStoreListing.card_id)
                .where(BotStoreListing.id == listing_id)
            )
        ).first()
        if row is None:
            return None
        listing, card = row
        seller = await session.get(User, listing.seller_user_id) if listing.seller_user_id else None
        return listing, card, seller

    @staticmethod
    async def buy_listing(
        session: AsyncSession,
        listing_id: int,
        buyer_id: int,
    ) -> BotStoreBuyResult:
        cfg = await SettingsService.get_bot_store_config(session)
        if not cfg.get("enabled", True) or not cfg.get("buy_enabled", True):
            return BotStoreBuyResult(False, "🏛 Bot Do‘konidan sotib olish hozircha vaqtincha o‘chirilgan.")

        lock_mgr = get_lock_manager()
        async with lock_mgr.lock(f"botstore:{listing_id}", ttl_ms=5000) as acquired:
            if not acquired:
                return BotStoreBuyResult(False, "Band, iltimos qayta urining.")

            listing = await session.get(BotStoreListing, listing_id)
            if listing is None or listing.status != "active":
                return BotStoreBuyResult(False, "Bu karta allaqachon sotilgan yoki olib tashlangan.")

            card = await session.get(Card, listing.card_id)
            if card is None or card.is_deleted:
                return BotStoreBuyResult(False, "Karta topilmadi.")

            price = int(listing.price_coins)
            now = utcnow()
            upd = await session.execute(
                update(BotStoreListing)
                .where(BotStoreListing.id == listing_id, BotStoreListing.status == "active")
                .values(status="sold", buyer_user_id=buyer_id, sold_at=now, updated_at=now)
            )
            if upd.rowcount != 1:
                return BotStoreBuyResult(False, "Bu karta allaqachon sotib olingan.")

            try:
                await UserService.debit_coins(
                    session=session,
                    user_id=buyer_id,
                    amount=price,
                    tx_type="bot_store_buy",
                    description=f"Bot Do'konidan olindi: {card.character_name}",
                    reference_id=f"botstore:{listing.id}",
                )
            except InsufficientCoinsError:
                raise

            dup = None
            new_uc_id: Optional[int] = None
            if await UserService.owns_card(session, buyer_id, card.id):
                dup = await CardLevelService.apply_duplicate(session, buyer_id, card)
                if dup is not None:
                    new_uc_id = dup.user_card_id
            else:
                uc = UserCard(
                    user_id=buyer_id,
                    card_id=card.id,
                    level=max(1, int(listing.card_level or 1)),
                    acquired_via="bot_store",
                    card_state="OWNED",
                    acquired_price_coins=price,
                )
                session.add(uc)
                await session.flush()
                uc.serial_number = 10000 + uc.id
                new_uc_id = uc.id

            session.add(
                CardHistory(
                    card_id=card.id,
                    user_card_id=new_uc_id,
                    event_type="bot_store_buy",
                    from_user_id=listing.seller_user_id,
                    to_user_id=buyer_id,
                    price=price,
                    details={"listing_id": listing.id, "source": listing.source},
                )
            )
            await UserService.recompute_card_counts(session, buyer_id, add_xp=15)
            await GameplayService.add_season_points(session, buyer_id, points=10, xp=15)
            await session.flush()

            return BotStoreBuyResult(
                True,
                "🏛 Karta Bot Do‘konidan muvaffaqiyatli sotib olindi!",
                card=card,
                price_paid=price,
                is_duplicate=dup is not None,
                level_before=dup.level_before if dup else max(1, int(listing.card_level or 1)),
                level_after=dup.level_after if dup else max(1, int(listing.card_level or 1)),
                maxed=bool(dup and dup.maxed),
                refund=dup.refund if dup else 0,
            )

    @staticmethod
    async def admin_add_listing(
        session: AsyncSession,
        card_id: int,
        price_coins: int,
        card_level: int = 1,
    ) -> Tuple[bool, str, Optional[BotStoreListing]]:
        card = await session.get(Card, card_id)
        if card is None or card.is_deleted:
            return False, "Karta topilmadi.", None
        if int(price_coins) <= 0:
            return False, "Narx musbat bo‘lishi kerak.", None
        listing = BotStoreListing(
            card_id=card.id,
            card_level=max(1, int(card_level)),
            price_coins=int(price_coins),
            source="admin",
            seller_user_id=None,
            status="active",
        )
        session.add(listing)
        await session.flush()
        from services.auction_wishlist_service import AuctionWishlistService

        await AuctionWishlistService.notify_wishlist_watchers(
            session,
            card.id,
            "bot_store",
            f"🏛 {card.character_name} Bot Do‘koniga {int(price_coins):,} 🪙 narxda qo‘yildi!",
        )
        return True, "✅ Karta Bot Do‘koniga qo‘shildi!", listing

    @staticmethod
    async def admin_remove_listing(session: AsyncSession, listing_id: int) -> bool:
        listing = await session.get(BotStoreListing, listing_id)
        if listing is None or listing.status != "active":
            return False
        listing.status = "removed"
        await session.flush()
        return True
