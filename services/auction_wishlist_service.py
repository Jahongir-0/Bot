import logging
from datetime import timedelta
from typing import List, Optional, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.redis_client import get_lock_manager
from models.base import ensure_utc, utcnow
from models.card import Card
from models.transaction import CoinTransaction
from models.user import User
from models.user_card import UserCard
from models.v2_features import (
    Auction,
    AuctionBid,
    CardHistory,
    CardTrade,
    WishlistItem,
)
from services.community_service import CommunityService
from services.gameplay_service import GameplayService
from services.user_service import InsufficientCoinsError, UserService

logger = logging.getLogger(__name__)


class AuctionWishlistService:
    # ------------------------------------------------------------------
    # Card History
    # ------------------------------------------------------------------
    @staticmethod
    async def record_history(
        session: AsyncSession,
        card_id: int,
        event_type: str,
        user_card_id: Optional[int] = None,
        from_user_id: Optional[int] = None,
        to_user_id: Optional[int] = None,
        group_id: Optional[int] = None,
        price: Optional[int] = None,
        details: Optional[dict] = None,
    ) -> CardHistory:
        ch = CardHistory(
            card_id=card_id,
            user_card_id=user_card_id,
            event_type=event_type[:32],
            from_user_id=from_user_id,
            to_user_id=to_user_id,
            group_id=group_id,
            price=price,
            details=details or {},
        )
        session.add(ch)
        await session.flush()
        return ch

    @staticmethod
    async def get_card_history(session: AsyncSession, card_id: int, limit: int = 20) -> List[CardHistory]:
        return list(
            (
                await session.execute(
                    select(CardHistory)
                    .where(CardHistory.card_id == card_id)
                    .order_by(CardHistory.id.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

    # ------------------------------------------------------------------
    # Auctions
    # ------------------------------------------------------------------
    @staticmethod
    async def create_auction(
        session: AsyncSession,
        card_id: int,
        start_price: int,
        hours: int,
        created_by_tg_id: int,
        min_step: int = 50,
        seller_user_id: Optional[int] = None,
        user_card_id: Optional[int] = None,
    ) -> Tuple[bool, str, Optional[Auction]]:
        if start_price <= 0 or hours <= 0:
            return False, "Narx va davomiylik musbat bo‘lishi kerak.", None
        card = await session.get(Card, card_id)
        if card is None or card.is_deleted:
            return False, "Karta topilmadi.", None

        if user_card_id is not None:
            uc = await session.get(UserCard, user_card_id)
            if uc is None or uc.user_id != seller_user_id or uc.is_locked:
                return False, "Bu karta nusxasi auksion uchun band yoki topilmadi.", None
            uc.is_locked = True
            uc.card_state = "AUCTION"
        else:
            # Admin auksioni: karta hali tashlanmagan/yopiq bo'lsa state AUCTION qilamiz
            card.card_state = "AUCTION"
            card.is_available = False

        now = utcnow()
        auc = Auction(
            card_id=card.id,
            user_card_id=user_card_id,
            seller_user_id=seller_user_id,
            created_by_tg_id=created_by_tg_id,
            start_price=int(start_price),
            current_price=int(start_price),
            min_step=max(10, int(min_step)),
            starts_at=now,
            ends_at=now + timedelta(hours=min(168, int(hours))),
            status="active",
        )
        session.add(auc)
        await session.flush()
        await AuctionWishlistService.notify_wishlist_watchers(
            session, card.id, "auction", f"🔨 {card.character_name} auksionga qo‘yildi! Boshlang‘ich narx: {start_price:,} 🪙"
        )
        return True, "Auksion boshlandi!", auc

    @staticmethod
    async def list_active_auctions(session: AsyncSession, page: int = 1, page_size: int = 10) -> Tuple[List[Tuple[Auction, Card]], int]:
        now = utcnow()
        cond = [Auction.status == "active", Auction.ends_at > now]
        total = int((await session.execute(select(func.count(Auction.id)).where(*cond))).scalar_one() or 0)
        rows = (
            await session.execute(
                select(Auction, Card)
                .join(Card, Card.id == Auction.card_id)
                .where(*cond)
                .order_by(Auction.ends_at.asc())
                .offset((max(1, page) - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return [(r[0], r[1]) for r in rows], total

    @staticmethod
    async def get_auction_detail(session: AsyncSession, auction_id: int) -> Optional[Tuple[Auction, Card, Optional[User]]]:
        row = (
            await session.execute(
                select(Auction, Card)
                .join(Card, Card.id == Auction.card_id)
                .where(Auction.id == auction_id)
            )
        ).first()
        if row is None:
            return None
        auc, card = row
        leader = await session.get(User, auc.highest_bidder_id) if auc.highest_bidder_id else None
        return auc, card, leader

    @staticmethod
    async def place_bid(
        session: AsyncSession, auction_id: int, bidder: User, amount: int
    ) -> Tuple[bool, str]:
        lock_mgr = get_lock_manager()
        async with lock_mgr.lock(f"auction_bid:{auction_id}", ttl_ms=5000) as acquired:
            if not acquired:
                return False, "Auksion yangilanmoqda, qayta urining."
            auc = await session.get(Auction, auction_id)
            if auc is None or auc.status != "active" or ensure_utc(auc.ends_at) <= utcnow():
                return False, "Auksion yakunlangan yoki topilmadi."
            if auc.seller_user_id and auc.seller_user_id == bidder.id:
                return False, "O‘zingiz qo‘ygan auksionga stavka bera olmaysiz."
            if auc.highest_bidder_id == bidder.id:
                return False, "Siz hozir allaqachon eng yuqori stavka egasisiz!"
            if await UserService.owns_card(session, bidder.id, auc.card_id):
                return False, "Bu kartadan sizda allaqachon bor (bir xil kartadan faqat 1 nusxa)."

            min_allowed = auc.start_price if auc.bid_count == 0 else (auc.current_price + auc.min_step)
            if int(amount) < int(min_allowed):
                return False, f"Eng kam stavka: {min_allowed:,} 🪙"

            # Balansni atomik tekshirish va band qilish
            upd_bidder = await session.execute(
                update(User)
                .where(User.id == bidder.id, User.coins >= int(amount))
                .values(
                    coins=User.coins - int(amount),
                    locked_coins=User.locked_coins + int(amount),
                    updated_at=utcnow(),
                )
            )
            if upd_bidder.rowcount != 1:
                return False, f"Coinlaringiz yetarli emas ({amount:,} 🪙 kerak)."

            prev_bidder_id = auc.highest_bidder_id
            prev_amount = auc.current_price

            # Oldingi liderga coinlarni qaytarish
            if prev_bidder_id is not None and auc.bid_count > 0:
                await session.execute(
                    update(User)
                    .where(User.id == prev_bidder_id)
                    .values(
                        coins=User.coins + prev_amount,
                        locked_coins=func.greatest(0, User.locked_coins - prev_amount),
                        updated_at=utcnow(),
                    )
                )
                await session.execute(
                    update(AuctionBid)
                    .where(AuctionBid.auction_id == auc.id, AuctionBid.user_id == prev_bidder_id, AuctionBid.is_refunded.is_(False))
                    .values(is_refunded=True)
                )
                await CommunityService.notify_user(
                    session,
                    prev_bidder_id,
                    "auction",
                    "🔨 Stavkangiz bosib o‘tildi!",
                    f"Auksion #{auc.id} da yangi stavka: {amount:,} 🪙. Sizning {prev_amount:,} 🪙 balansingizga qaytarildi.",
                )

            auc.current_price = int(amount)
            auc.highest_bidder_id = bidder.id
            auc.bid_count = int(auc.bid_count) + 1
            session.add(AuctionBid(auction_id=auc.id, user_id=bidder.id, amount=int(amount), is_refunded=False))
            await session.flush()
            return True, f"✅ Stavka qabul qilindi: {amount:,} 🪙!"

    @staticmethod
    async def _finalize_single_auction(session: AsyncSession, auc: Auction) -> None:
        card = await session.get(Card, auc.card_id)
        if auc.highest_bidder_id is None or auc.bid_count == 0:
            auc.status = "no_bids"
            auc.finalized = True
            if auc.user_card_id:
                uc = await session.get(UserCard, auc.user_card_id)
                if uc:
                    uc.is_locked = False
                    uc.card_state = "OWNED"
            elif card:
                card.card_state = "AVAILABLE"
            return

        winner_id = auc.highest_bidder_id
        price = int(auc.current_price)
        await session.execute(
            update(User)
            .where(User.id == winner_id)
            .values(
                locked_coins=func.greatest(0, User.locked_coins - price),
                auction_wins=User.auction_wins + 1,
                updated_at=utcnow(),
            )
        )
        winner = await session.get(User, winner_id)
        session.add(
            CoinTransaction(
                user_id=winner_id,
                amount=-price,
                balance_after=winner.coins if winner else 0,
                tx_type="auction_win",
                description=f"Auction #{auc.id} won",
                reference_id=f"auction:{auc.id}",
            )
        )

        if auc.seller_user_id:
            await UserService.credit_coins(
                session,
                auc.seller_user_id,
                price,
                "auction_sale",
                f"Auction #{auc.id} sold",
                reference_id=f"auction_sale:{auc.id}",
            )

        if auc.user_card_id:
            uc = await session.get(UserCard, auc.user_card_id)
            if uc:
                uc.user_id = winner_id
                uc.is_locked = False
                uc.card_state = "OWNED"
                uc.acquired_via = "auction"
            await UserService.recompute_card_counts(session, winner_id, add_xp=50)
            if auc.seller_user_id:
                await UserService.recompute_card_counts(session, auc.seller_user_id)
        else:
            try:
                async with session.begin_nested():
                    uc = UserCard(user_id=winner_id, card_id=auc.card_id, acquired_via="auction", card_state="OWNED")
                    session.add(uc)
                    await session.flush()
                    uc.serial_number = 10000 + uc.id
                await UserService.recompute_card_counts(session, winner_id, add_xp=50)
            except IntegrityError:
                pass
            if card:
                card.card_state = "OWNED"

        auc.status = "ended"
        auc.finalized = True
        await AuctionWishlistService.record_history(
            session,
            auc.card_id,
            "auction_won",
            from_user_id=auc.seller_user_id,
            to_user_id=winner_id,
            price=price,
            details={"auction_id": auc.id},
        )
        await CommunityService.notify_user(
            session,
            winner_id,
            "auction",
            "🏆 Auksionda g‘olib bo‘ldingiz!",
            f"Siz {card.character_name if card else 'karta'} ni {price:,} 🪙 ga yutib oldingiz!",
        )

    @staticmethod
    async def finalize_expired_auctions(session: AsyncSession) -> int:
        now = utcnow()
        due = list(
            (
                await session.execute(
                    select(Auction).where(Auction.status == "active", Auction.ends_at <= now, Auction.finalized.is_(False))
                )
            )
            .scalars()
            .all()
        )
        done = 0
        for auc in due:
            await AuctionWishlistService._finalize_single_auction(session, auc)
            done += 1
        await session.flush()
        return done

    @staticmethod
    async def admin_finalize_auction(session: AsyncSession, auction_id: int) -> Tuple[bool, str]:
        auc = await session.get(Auction, auction_id)
        if auc is None or auc.status != "active":
            return False, "Faol auksion topilmadi."
        await AuctionWishlistService._finalize_single_auction(session, auc)
        await session.flush()
        return True, "🏁 Auksion muddatidan oldin yakunlandi!"

    @staticmethod
    async def admin_cancel_auction(session: AsyncSession, auction_id: int) -> Tuple[bool, str]:
        auc = await session.get(Auction, auction_id)
        if auc is None or auc.status != "active":
            return False, "Faol auksion topilmadi."
        if auc.highest_bidder_id and auc.bid_count > 0:
            refund_amt = int(auc.current_price)
            await session.execute(
                update(User)
                .where(User.id == auc.highest_bidder_id)
                .values(
                    coins=User.coins + refund_amt,
                    locked_coins=func.greatest(0, User.locked_coins - refund_amt),
                    updated_at=utcnow(),
                )
            )
            await CommunityService.notify_user(
                session,
                auc.highest_bidder_id,
                "auction",
                "❌ Auksion bekor qilindi",
                f"Auksion #{auc.id} admin tomonidan bekor qilindi. {refund_amt:,} 🪙 balansingizga qaytarildi.",
            )
        if auc.user_card_id:
            uc = await session.get(UserCard, auc.user_card_id)
            if uc:
                uc.is_locked = False
                uc.card_state = "OWNED"
        else:
            card = await session.get(Card, auc.card_id)
            if card:
                card.card_state = "AVAILABLE"
                card.is_available = True
        auc.status = "cancelled"
        auc.finalized = True
        await session.flush()
        return True, "❌ Auksion bekor qilindi va stavkalar qaytarildi."

    # ------------------------------------------------------------------
    # Wishlist
    # ------------------------------------------------------------------
    @staticmethod
    async def toggle_wishlist(session: AsyncSession, user_id: int, card_id: int) -> Tuple[bool, str]:
        row = (
            await session.execute(
                select(WishlistItem).where(WishlistItem.user_id == user_id, WishlistItem.card_id == card_id)
            )
        ).scalar_one_or_none()
        if row is not None:
            await session.delete(row)
            await session.flush()
            return False, "❌ Karta Wishlistdan olib tashlandi."
        session.add(WishlistItem(user_id=user_id, card_id=card_id))
        await session.flush()
        return True, "💖 Karta Wishlistga qo‘shildi! Bozorga yoki auksionga chiqsa xabar beramiz."

    @staticmethod
    async def get_user_wishlist(session: AsyncSession, user_id: int) -> List[Card]:
        return list(
            (
                await session.execute(
                    select(Card)
                    .join(WishlistItem, WishlistItem.card_id == Card.id)
                    .where(WishlistItem.user_id == user_id, Card.is_deleted.is_(False))
                    .order_by(WishlistItem.id.desc())
                )
            )
            .scalars()
            .all()
        )

    @staticmethod
    async def get_most_wanted(session: AsyncSession, limit: int = 10) -> List[Tuple[Card, int]]:
        rows = (
            await session.execute(
                select(Card, func.count(WishlistItem.id).label("cnt"))
                .join(WishlistItem, WishlistItem.card_id == Card.id)
                .where(Card.is_deleted.is_(False))
                .group_by(Card.id)
                .order_by(func.count(WishlistItem.id).desc())
                .limit(limit)
            )
        ).all()
        return [(r[0], int(r[1])) for r in rows]

    @staticmethod
    async def notify_wishlist_watchers(
        session: AsyncSession, card_id: int, notif_type: str, body: str
    ) -> int:
        user_ids = list(
            (
                await session.execute(select(WishlistItem.user_id).where(WishlistItem.card_id == card_id))
            )
            .scalars()
            .all()
        )
        for uid in user_ids:
            await CommunityService.notify_user(session, uid, notif_type, "💖 Wishlist yangiligi!", body)
        return len(user_ids)

    # ------------------------------------------------------------------
    # Two-Sided Atomic Card-for-Card Trading
    # ------------------------------------------------------------------
    @staticmethod
    async def create_trade(
        session: AsyncSession, initiator_id: int, partner_id: int, initiator_uc_id: int
    ) -> Tuple[bool, str, Optional[CardTrade]]:
        if initiator_id == partner_id:
            return False, "O‘zingiz bilan trade qila olmaysiz.", None
        uc = await session.get(UserCard, initiator_uc_id)
        if uc is None or uc.user_id != initiator_id or uc.is_locked:
            return False, "Karta topilmadi yoki band.", None
        if await UserService.owns_card(session, partner_id, uc.card_id):
            return False, "Qabul qiluvchida bu karta allaqachon bor.", None
        uc.is_locked = True
        uc.card_state = "LOCKED"
        trade = CardTrade(
            initiator_user_id=initiator_id,
            partner_user_id=partner_id,
            initiator_uc_id=uc.id,
            status="pending_pick",
            expires_at=utcnow() + timedelta(hours=12),
        )
        session.add(trade)
        await session.flush()
        return True, "Almashuv taklifi yaratildi!", trade

    @staticmethod
    async def confirm_trade(
        session: AsyncSession, trade_id: int, partner_id: int, partner_uc_id: int
    ) -> Tuple[bool, str]:
        trade = await session.get(CardTrade, trade_id)
        if trade is None or trade.partner_user_id != partner_id or trade.status != "pending_pick":
            return False, "Almashuv topilmadi yoki yakunlangan."
        if ensure_utc(trade.expires_at) <= utcnow():
            return False, "Almashuv muddati tugagan."

        uc_init = await session.get(UserCard, trade.initiator_uc_id)
        uc_part = await session.get(UserCard, partner_uc_id)
        if uc_init is None or uc_part is None or uc_part.user_id != partner_id or uc_part.is_locked:
            return False, "Tanlangan karta band yoki mavjud emas."
        if uc_init.card_id == uc_part.card_id:
            return False, "Bir xil kartani almashtirib bo‘lmaydi."
        if await UserService.owns_card(session, trade.initiator_user_id, uc_part.card_id):
            return False, "Taklif qiluvchida sizning kartangiz allaqachon bor."
        if await UserService.owns_card(session, trade.partner_user_id, uc_init.card_id):
            return False, "Sizda bu karta allaqachon bor."

        # Atomik swap
        uc_init.user_id = trade.partner_user_id
        uc_init.is_locked = False
        uc_init.card_state = "OWNED"
        uc_init.acquired_via = "trade"

        uc_part.user_id = trade.initiator_user_id
        uc_part.is_locked = False
        uc_part.card_state = "OWNED"
        uc_part.acquired_via = "trade"

        trade.partner_uc_id = uc_part.id
        trade.status = "accepted"

        await session.execute(
            update(User)
            .where(User.id.in_([trade.initiator_user_id, trade.partner_user_id]))
            .values(trade_count=User.trade_count + 1)
        )
        await UserService.recompute_card_counts(session, trade.initiator_user_id, add_xp=25)
        await UserService.recompute_card_counts(session, trade.partner_user_id, add_xp=25)
        await GameplayService.add_season_points(session, trade.initiator_user_id, points=20, xp=25, trade_done=True)
        await GameplayService.add_season_points(session, trade.partner_user_id, points=20, xp=25, trade_done=True)
        await AuctionWishlistService.record_history(
            session, uc_init.card_id, "traded", uc_init.id, trade.initiator_user_id, trade.partner_user_id
        )
        await AuctionWishlistService.record_history(
            session, uc_part.card_id, "traded", uc_part.id, trade.partner_user_id, trade.initiator_user_id
        )
        await session.flush()
        return True, "🤝 Almashuv muvaffaqiyatli yakunlandi!"

    @staticmethod
    async def cancel_trade(
        session: AsyncSession, trade_id: int, initiator_id: int
    ) -> Tuple[bool, str]:
        trade = await session.get(CardTrade, trade_id)
        if trade is None or trade.initiator_user_id != initiator_id:
            return False, "Almashuv taklifi topilmadi."
        if trade.status != "pending_pick":
            return False, "Bu almashuv allaqachon yakunlangan yoki bekor qilingan."
        trade.status = "cancelled"
        uc = await session.get(UserCard, trade.initiator_uc_id)
        if uc and uc.is_locked:
            uc.is_locked = False
            uc.card_state = "OWNED"
        await session.flush()
        return True, "✅ Almashuv taklifi bekor qilindi va kartangiz qulfdan chiqarildi."

    @staticmethod
    async def expire_stale_trades(session: AsyncSession) -> int:
        now = utcnow()
        stale = list(
            (
                await session.execute(
                    select(CardTrade).where(
                        CardTrade.status == "pending_pick",
                        CardTrade.expires_at <= now,
                    )
                )
            )
            .scalars()
            .all()
        )
        count = 0
        for tr in stale:
            tr.status = "expired"
            uc = await session.get(UserCard, tr.initiator_uc_id)
            if uc and uc.is_locked:
                uc.is_locked = False
                uc.card_state = "OWNED"
            count += 1
        if count:
            await session.flush()
        return count
