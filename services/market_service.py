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
class BotSellQuote:
    user_card_id: int
    card: Card
    level: int
    base_rank_value: int
    bonus_pct: int
    bonus_coins: int
    total_price: int
    is_favorite: bool = False
    is_showcased: bool = False


@dataclass
class MarketActionResult:
    success: bool
    message: str
    listing: Optional[CardListing] = None
    card: Optional[Card] = None
    seller: Optional[User] = None
    price_paid: int = 0
    is_duplicate: bool = False
    level_before: int = 0
    level_after: int = 0
    maxed: bool = False
    refund: int = 0


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
            price_paid = int(listing.price)
            seller_id = listing.seller_id
            card_id = listing.card_id
            uc_id = listing.user_card_id
            listing_id_val = listing.id

            try:
                await UserService.debit_coins(
                    session=session,
                    user_id=buyer_id,
                    amount=price_paid,
                    tx_type="market_buy",
                    description=f"Bought {card.character_name} on market",
                    reference_id=f"listing:{listing_id_val}",
                )
            except InsufficientCoinsError:
                raise

            await UserService.credit_coins(
                session=session,
                user_id=seller_id,
                amount=price_paid,
                tx_type="market_sell",
                description=f"Sold {card.character_name} on market",
                reference_id=f"listing:{listing_id_val}",
            )

            # Refund any pending offers on this user_card BEFORE deleting uc (FK CASCADE)
            pending_offers = (
                await session.execute(
                    select(Offer).where(
                        Offer.user_card_id == uc_id,
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

            dup = None
            if await UserService.owns_card(session, buyer_id, card_id):
                from services.card_level_service import CardLevelService

                dup = await CardLevelService.apply_duplicate(session, buyer_id, card)
                uc = await session.get(UserCard, uc_id)
                if uc is not None:
                    await session.delete(uc)
                    await session.flush()
            else:
                await session.execute(
                    update(UserCard)
                    .where(UserCard.id == uc_id)
                    .values(
                        user_id=buyer_id,
                        is_locked=False,
                        is_favorite=False,
                        is_showcased=False,
                        card_state="OWNED",
                        acquired_via="market",
                        updated_at=now,
                    )
                )

            await UserService.recompute_card_counts(session, seller_id)
            await UserService.recompute_card_counts(session, buyer_id, add_xp=15)
            await session.flush()

            return MarketActionResult(
                True,
                "Karta muvaffaqiyatli sotib olindi!",
                listing=None if dup is not None else listing,
                card=card,
                seller=seller,
                price_paid=price_paid,
                is_duplicate=dup is not None,
                level_before=dup.level_before if dup else 1,
                level_after=dup.level_after if dup else 1,
                maxed=bool(dup and dup.maxed),
                refund=dup.refund if dup else 0,
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

    # ------------------------------------------------------------------
    # Bot Market (kartani botga sotish: rank narxi + level bonusi)
    # ------------------------------------------------------------------
    @staticmethod
    async def get_bot_sell_quote(
        session: AsyncSession,
        user_id: int,
        user_card_id: int,
    ) -> Tuple[bool, str, Optional[BotSellQuote]]:
        from services.card_level_service import CardLevelService
        from services.settings_service import SettingsService

        uc = await session.get(UserCard, user_card_id)
        if uc is None or uc.user_id != user_id:
            return False, "Karta topilmadi yoki sizga tegishli emas.", None
        if uc.is_locked:
            return False, "Karta hozir market/auksion/almashuvda band.", None
        card = await session.get(Card, uc.card_id)
        if card is None:
            return False, "Karta ma’lumoti topilmadi.", None

        rcfg = await SettingsService.get_rarity_config(session)
        lvl_cfg = await CardLevelService.get_config(session)
        level = int(getattr(uc, "level", 1) or 1)
        rank_val = CardLevelService.rank_value_for_card(card, rcfg)
        total, base, bonus = CardLevelService.bot_sell_price(rank_val, level, lvl_cfg)
        bonus_pct = max(0, int(lvl_cfg.get("bot_sell_level_bonus_pct", 10)))
        return True, "ok", BotSellQuote(
            user_card_id=uc.id,
            card=card,
            level=level,
            base_rank_value=base,
            bonus_pct=bonus_pct,
            bonus_coins=bonus,
            total_price=total,
            is_favorite=bool(uc.is_favorite),
            is_showcased=bool(getattr(uc, "is_showcased", False)),
        )

    @staticmethod
    async def get_bot_sellable_cards(
        session: AsyncSession,
        user_id: int,
        page: int = 1,
        page_size: int = 8,
    ) -> Tuple[List[BotSellQuote], int]:
        from services.card_level_service import CardLevelService
        from services.settings_service import SettingsService

        cond = [UserCard.user_id == user_id, UserCard.is_locked.is_(False)]
        total = int((await session.execute(select(func.count(UserCard.id)).where(*cond))).scalar_one() or 0)
        offset = (max(1, page) - 1) * page_size
        rows = (
            await session.execute(
                select(UserCard, Card)
                .join(Card, Card.id == UserCard.card_id)
                .where(*cond)
                .order_by(UserCard.is_favorite.asc(), UserCard.level.asc(), UserCard.id.desc())
                .offset(offset)
                .limit(page_size)
            )
        ).all()
        if not rows:
            return [], total

        rcfg = await SettingsService.get_rarity_config(session)
        lvl_cfg = await CardLevelService.get_config(session)
        bonus_pct = max(0, int(lvl_cfg.get("bot_sell_level_bonus_pct", 10)))
        quotes: List[BotSellQuote] = []
        for uc, card in rows:
            lv = int(getattr(uc, "level", 1) or 1)
            rank_val = CardLevelService.rank_value_for_card(card, rcfg)
            total_p, base_p, bonus_p = CardLevelService.bot_sell_price(rank_val, lv, lvl_cfg)
            quotes.append(
                BotSellQuote(
                    user_card_id=uc.id,
                    card=card,
                    level=lv,
                    base_rank_value=base_p,
                    bonus_pct=bonus_pct,
                    bonus_coins=bonus_p,
                    total_price=total_p,
                    is_favorite=bool(uc.is_favorite),
                    is_showcased=bool(getattr(uc, "is_showcased", False)),
                )
            )
        return quotes, total

    @staticmethod
    async def sell_to_bot(
        session: AsyncSession,
        user_id: int,
        user_card_id: int,
    ) -> MarketActionResult:
        from models.v2_features import CardHistory
        from services.card_level_service import CardLevelService
        from services.settings_service import SettingsService

        lock_mgr = get_lock_manager()
        async with lock_mgr.lock(f"uc:{user_card_id}", ttl_ms=5000) as acquired:
            if not acquired:
                return MarketActionResult(False, "Karta hozir band, qayta urining.")

            now = utcnow()
            upd = await session.execute(
                update(UserCard)
                .where(
                    UserCard.id == user_card_id,
                    UserCard.user_id == user_id,
                    UserCard.is_locked.is_(False),
                )
                .values(is_locked=True, updated_at=now)
            )
            if upd.rowcount != 1:
                return MarketActionResult(
                    False,
                    "Karta topilmadi, sizga tegishli emas yoki allaqachon sotuvda/band.",
                )

            uc = await session.get(UserCard, user_card_id)
            card = await session.get(Card, uc.card_id) if uc else None
            if uc is None or card is None:
                return MarketActionResult(False, "Karta ma’lumoti topilmadi.")

            rcfg = await SettingsService.get_rarity_config(session)
            lvl_cfg = await CardLevelService.get_config(session)
            level = int(getattr(uc, "level", 1) or 1)
            rank_val = CardLevelService.rank_value_for_card(card, rcfg)
            total_price, base_val, bonus_val = CardLevelService.bot_sell_price(rank_val, level, lvl_cfg)

            # Refund any pending offers on this user_card BEFORE deleting uc (FK CASCADE)
            pending_offers = (
                await session.execute(
                    select(Offer).where(
                        Offer.user_card_id == uc.id,
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
                    description="Card sold to bot - offer refunded",
                    reference_id=f"offer:{off.id}",
                )

            session.add(
                CardHistory(
                    card_id=card.id,
                    user_card_id=None,
                    event_type="sold_to_bot",
                    from_user_id=user_id,
                    to_user_id=None,
                    price=total_price,
                    details={"level": level, "rank": card.rarity, "base": base_val, "bonus": bonus_val},
                )
            )

            # Oddiy (event bo'lmagan) karta bot zaxirasiga qaytadi
            if not card.is_event_card and int(card.dropped_count or 0) > 0:
                card.dropped_count = max(0, int(card.dropped_count) - 1)
                if card.card_state == "OWNED":
                    card.card_state = "AVAILABLE"

            await session.delete(uc)
            await session.flush()

            await UserService.credit_coins(
                session=session,
                user_id=user_id,
                amount=total_price,
                tx_type="bot_market_sell",
                description=f"Sold {card.character_name} (Lv.{level}) to bot",
                reference_id=f"botsell:{user_card_id}",
            )
            await UserService.recompute_card_counts(session, user_id)
            await session.flush()
            return MarketActionResult(
                True,
                f"🤖 Karta botga sotildi! +{total_price:,} 🪙",
                card=card,
                price_paid=total_price,
                level_before=level,
                level_after=level,
            )
