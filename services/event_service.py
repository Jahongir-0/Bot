import hashlib
import hmac
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import List, Optional, Tuple

from sqlalchemy import exists, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from models.base import ensure_utc, utcnow
from models.card import Card
from models.limited_event import EventPurchase, LimitedEvent
from models.user import User
from models.user_card import UserCard
from services.user_service import InsufficientCoinsError, UserService
from utils.constants import EVENT_DURATION_HOURS

logger = logging.getLogger(__name__)

MAX_EVENT_COPIES = 100_000
OWN_MSG = "Bu kartadan sizda allaqachon bor — bir xil kartadan faqat bitta nusxa olish mumkin."


@dataclass
class EventResult:
    success: bool
    message: str
    code: str = ""  # ok | owned | sold_out | ended | no_price | funds | invalid | duplicate | refund
    event: Optional[LimitedEvent] = None
    card: Optional[Card] = None


def _sign(order_uid: str, telegram_user_id: int, stars: int) -> str:
    secret = get_settings().stars_payment_secret.encode("utf-8")
    sig = hmac.new(secret, f"evt:{order_uid}:{telegram_user_id}:{stars}".encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    return f"evt:{order_uid}:{sig}"


def verify_payload(payload: str, telegram_user_id: int, stars: int) -> Optional[str]:
    parts = (payload or "").split(":")
    if len(parts) != 3 or parts[0] != "evt":
        return None
    if hmac.compare_digest(payload, _sign(parts[1], telegram_user_id, stars)):
        return parts[1]
    return None


def is_event_payload(payload: str) -> bool:
    return (payload or "").startswith("evt:")


class EventService:
    # ------------------------------------------------------------------
    # Admin: nomzod kartalar va event yaratish
    # ------------------------------------------------------------------
    @staticmethod
    def _candidate_conditions():
        has_owner = exists().where(UserCard.card_id == Card.id)
        return [
            Card.is_available.is_(True),
            Card.is_deleted.is_(False),
            Card.is_event_card.is_(False),
            Card.dropped_count == 0,
            Card.drop_limit > 0,
            ~has_owner,
        ]

    @staticmethod
    async def list_candidates(session: AsyncSession, page: int = 1, page_size: int = 6) -> Tuple[List[Card], int]:
        """Hali guruhga tashlanmagan va hech kimda yo'q kartalar."""
        conds = EventService._candidate_conditions()
        total = (await session.execute(select(func.count(Card.id)).where(*conds))).scalar_one()
        rows = (
            await session.execute(
                select(Card)
                .where(*conds)
                .order_by(Card.anime_name.asc(), Card.character_name.asc(), Card.id.asc())
                .offset((max(1, page) - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
        return list(rows), int(total or 0)

    @staticmethod
    async def create_event(
        session: AsyncSession,
        admin_telegram_id: int,
        card_id: int,
        total_copies: int,
        price_coins: Optional[int],
        price_stars: Optional[int],
        hours: int = EVENT_DURATION_HOURS,
    ) -> EventResult:
        if not (1 <= int(total_copies) <= MAX_EVENT_COPIES):
            return EventResult(False, f"Nusxa soni 1 dan {MAX_EVENT_COPIES:,} gacha bo‘lishi kerak.", "invalid")
        if not (1 <= int(hours) <= 720):
            return EventResult(False, "Event davomiyligi 1 dan 720 soatgacha (30 kun) bo‘lishi kerak.", "invalid")
        if not price_coins and not price_stars:
            return EventResult(False, "Kamida bitta narx (coin yoki Stars) belgilanishi kerak.", "no_price")

        card = (
            await session.execute(
                select(Card).where(Card.id == card_id, *EventService._candidate_conditions())
            )
        ).scalar_one_or_none()
        if card is None:
            return EventResult(False, "Bu karta endi event uchun yaroqsiz (tashlangan yoki kimdadir bor).", "invalid")

        now = utcnow()
        card.is_event_card = True
        card.limited_copies = int(total_copies)
        card.drop_limit = 0
        card.drop_weight = 0
        event = LimitedEvent(
            card_id=card.id,
            total_copies=int(total_copies),
            sold_count=0,
            price_coins=int(price_coins) if price_coins else None,
            price_stars=int(price_stars) if price_stars else None,
            starts_at=now,
            ends_at=now + timedelta(hours=int(hours)),
            status="active",
            created_by_tg_id=admin_telegram_id,
        )
        session.add(event)
        await session.flush()
        return EventResult(True, "Event yaratildi.", "ok", event=event, card=card)

    @staticmethod
    async def stop_event(session: AsyncSession, event_id: int) -> bool:
        res = await session.execute(
            update(LimitedEvent)
            .where(LimitedEvent.id == event_id, LimitedEvent.status == "active")
            .values(status="cancelled", updated_at=utcnow())
        )
        return res.rowcount == 1

    # ------------------------------------------------------------------
    # O'qish
    # ------------------------------------------------------------------
    @staticmethod
    async def get_active_events(session: AsyncSession) -> List[Tuple[LimitedEvent, Card]]:
        now = utcnow()
        rows = (
            await session.execute(
                select(LimitedEvent, Card)
                .join(Card, Card.id == LimitedEvent.card_id)
                .where(
                    LimitedEvent.status == "active",
                    LimitedEvent.ends_at > now,
                    LimitedEvent.sold_count < LimitedEvent.total_copies,
                )
                .order_by(LimitedEvent.ends_at.asc())
            )
        ).all()
        return [(r[0], r[1]) for r in rows]

    @staticmethod
    async def get_recent_events(session: AsyncSession, limit: int = 8) -> List[Tuple[LimitedEvent, Card]]:
        rows = (
            await session.execute(
                select(LimitedEvent, Card)
                .join(Card, Card.id == LimitedEvent.card_id)
                .order_by(LimitedEvent.id.desc())
                .limit(limit)
            )
        ).all()
        return [(r[0], r[1]) for r in rows]

    @staticmethod
    async def get_event_with_card(session: AsyncSession, event_id: int) -> Optional[Tuple[LimitedEvent, Card]]:
        row = (
            await session.execute(
                select(LimitedEvent, Card)
                .join(Card, Card.id == LimitedEvent.card_id)
                .where(LimitedEvent.id == event_id)
            )
        ).first()
        return (row[0], row[1]) if row else None

    # ------------------------------------------------------------------
    # Karta berish (umumiy atomik qism)
    # ------------------------------------------------------------------
    @staticmethod
    async def _allocate(session: AsyncSession, event: LimitedEvent, card: Card, user_id: int) -> EventResult:
        """Nusxani atomik band qiladi va userga beradi. Coin reward BERILMAYDI (event karta)."""
        if await UserService.owns_card(session, user_id, card.id):
            return EventResult(False, OWN_MSG, "owned", event=event, card=card)

        now = utcnow()
        upd = await session.execute(
            update(LimitedEvent)
            .where(
                LimitedEvent.id == event.id,
                LimitedEvent.status == "active",
                LimitedEvent.ends_at > now,
                LimitedEvent.sold_count < LimitedEvent.total_copies,
            )
            .values(sold_count=LimitedEvent.sold_count + 1, updated_at=now)
        )
        if upd.rowcount != 1:
            fresh = await session.get(LimitedEvent, event.id)
            sold_out = fresh is not None and fresh.sold_count >= fresh.total_copies
            return EventResult(
                False,
                "😔 Event kartalari sotilib bo‘ldi." if sold_out else "⌛ Event tugagan.",
                "sold_out" if sold_out else "ended",
                event=event,
                card=card,
            )

        try:
            uc = UserCard(user_id=user_id, card_id=card.id, acquired_via="event", card_state="OWNED")
            session.add(uc)
            await session.flush()
            uc.serial_number = 10000 + uc.id
        except IntegrityError:
            await session.rollback()
            return EventResult(False, OWN_MSG, "owned", event=event, card=card)

        await session.execute(
            update(LimitedEvent)
            .where(LimitedEvent.id == event.id, LimitedEvent.sold_count >= LimitedEvent.total_copies)
            .values(status="sold_out", updated_at=now)
        )
        await UserService.recompute_card_counts(session, user_id, add_xp=40)

        from services.auction_wishlist_service import AuctionWishlistService
        await AuctionWishlistService.record_history(
            session, card.id, "event_bought", uc.id, to_user_id=user_id, details={"event_id": event.id}
        )

        await session.flush()
        event.sold_count = int(event.sold_count) + 1
        return EventResult(True, "ok", "ok", event=event, card=card)

    # ------------------------------------------------------------------
    # Coin bilan sotib olish
    # ------------------------------------------------------------------
    @staticmethod
    async def buy_with_coins(session: AsyncSession, event_id: int, user: User) -> EventResult:
        pair = await EventService.get_event_with_card(session, event_id)
        if pair is None:
            return EventResult(False, "Event topilmadi.", "invalid")
        event, card = pair
        if not event.price_coins:
            return EventResult(False, "Bu event kartasi coin bilan sotilmaydi. ⭐ Stars bilan oling.", "no_price")

        if user.coins < int(event.price_coins):
            return EventResult(False, "❌ Coinlaringiz yetarli emas!", "funds", event=event, card=card)

        try:
            async with session.begin_nested():
                res = await EventService._allocate(session, event, card, user.id)
                if not res.success:
                    return res
                await UserService.debit_coins(
                    session=session,
                    user_id=user.id,
                    amount=int(event.price_coins),
                    tx_type="event_buy",
                    description=f"Event card: {card.character_name}",
                    reference_id=f"event:{event.id}",
                )
        except InsufficientCoinsError:
            return EventResult(False, "❌ Coinlaringiz yetarli emas!", "funds", event=event, card=card)

        session.add(
            EventPurchase(
                order_uid=uuid.uuid4().hex,
                event_id=event.id,
                user_id=user.id,
                method="coins",
                price=int(event.price_coins),
                status="paid",
            )
        )
        await session.flush()
        return res

    # ------------------------------------------------------------------
    # Stars bilan sotib olish
    # ------------------------------------------------------------------
    @staticmethod
    async def create_stars_order(
        session: AsyncSession, event_id: int, user: User
    ) -> Tuple[EventResult, Optional[str]]:
        pair = await EventService.get_event_with_card(session, event_id)
        if pair is None:
            return EventResult(False, "Event topilmadi.", "invalid"), None
        event, card = pair
        if not event.price_stars:
            return EventResult(False, "Bu event kartasi Stars bilan sotilmaydi.", "no_price"), None
        if event.status != "active" or ensure_utc(event.ends_at) <= utcnow():
            return EventResult(False, "⌛ Event tugagan.", "ended"), None
        if event.sold_count >= event.total_copies:
            return EventResult(False, "😔 Event kartalari sotilib bo‘ldi.", "sold_out"), None
        if await UserService.owns_card(session, user.id, card.id):
            return EventResult(False, OWN_MSG, "owned"), None

        order = EventPurchase(
            order_uid=uuid.uuid4().hex,
            event_id=event.id,
            user_id=user.id,
            method="stars",
            price=int(event.price_stars),
            status="pending",
        )
        session.add(order)
        await session.flush()
        payload = _sign(order.order_uid, user.telegram_id, int(event.price_stars))
        return EventResult(True, "ok", "ok", event=event, card=card), payload

    @staticmethod
    async def validate_pre_checkout(
        session: AsyncSession, payload: str, user: User, currency: str, total_amount: int
    ) -> Tuple[bool, str]:
        order_uid = verify_payload(payload, user.telegram_id, total_amount)
        if currency != "XTR" or order_uid is None:
            return False, "To‘lovni tasdiqlab bo‘lmadi."
        order = (
            await session.execute(select(EventPurchase).where(EventPurchase.order_uid == order_uid))
        ).scalar_one_or_none()
        if order is None or order.user_id != user.id or order.status != "pending" or order.price != total_amount:
            return False, "Buyurtma topilmadi yoki allaqachon ishlatilgan."
        pair = await EventService.get_event_with_card(session, order.event_id)
        if pair is None:
            return False, "Event topilmadi."
        event, card = pair
        if event.status != "active" or ensure_utc(event.ends_at) <= utcnow():
            return False, "Event tugagan."
        if event.sold_count >= event.total_copies:
            return False, "Event kartalari sotilib bo‘lgan."
        if await UserService.owns_card(session, user.id, card.id):
            return False, "Bu kartadan sizda allaqachon bor."
        return True, ""

    @staticmethod
    async def complete_stars_purchase(
        session: AsyncSession,
        payload: str,
        user: User,
        currency: str,
        total_amount: int,
        charge_id: str,
    ) -> EventResult:
        """code: ok | duplicate | refund (pul olingan, lekin karta berib bo'lmadi => Stars qaytarilishi kerak)."""
        dup = (
            await session.execute(
                select(EventPurchase.id).where(EventPurchase.telegram_payment_charge_id == charge_id)
            )
        ).first()
        if dup is not None:
            return EventResult(False, "Takroriy to‘lov xabari.", "duplicate")

        order_uid = verify_payload(payload, user.telegram_id, total_amount)
        if currency != "XTR" or order_uid is None:
            return EventResult(False, "To‘lov tasdiqlanmadi.", "refund")
        order = (
            await session.execute(select(EventPurchase).where(EventPurchase.order_uid == order_uid))
        ).scalar_one_or_none()
        if order is None or order.user_id != user.id or order.price != total_amount:
            return EventResult(False, "Buyurtma topilmadi.", "refund")
        if order.status != "pending":
            return EventResult(False, "Buyurtma allaqachon yopilgan.", "duplicate" if order.status == "paid" else "refund")

        pair = await EventService.get_event_with_card(session, order.event_id)
        if pair is None:
            return EventResult(False, "Event topilmadi.", "refund")
        event, card = pair

        res = await EventService._allocate(session, event, card, user.id)
        if not res.success:
            res.code = "refund"
            return res

        order.status = "paid"
        order.telegram_payment_charge_id = charge_id
        await session.flush()
        return res

    # ------------------------------------------------------------------
    # Tugash / yakunlash
    # ------------------------------------------------------------------
    @staticmethod
    async def close_expired(session: AsyncSession) -> int:
        res = await session.execute(
            update(LimitedEvent)
            .where(LimitedEvent.status == "active", LimitedEvent.ends_at <= utcnow())
            .values(status="ended", updated_at=utcnow())
        )
        return int(res.rowcount or 0)

    @staticmethod
    async def get_unfinalized(session: AsyncSession) -> List[LimitedEvent]:
        rows = (
            await session.execute(
                select(LimitedEvent).where(LimitedEvent.status != "active", LimitedEvent.finalized.is_(False))
            )
        ).scalars().all()
        return list(rows)
