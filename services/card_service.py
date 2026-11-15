import hashlib
import random
import re
from typing import List, Optional, Tuple

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.base import utcnow
from models.card import Card
from models.event import Event
from models.market import CardListing, Offer, PendingTransfer
from models.user_card import UserCard
from services.settings_service import SettingsService
from services.user_service import UserService


def _slugify(value: str, prefix: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    if cleaned:
        return f"{prefix}_{cleaned[:40]}"
    digest = hashlib.md5(value.encode("utf-8")).hexdigest()[:8]
    return f"{prefix}_{digest}"


class CardService:
    @staticmethod
    async def generate_next_card_code(session: AsyncSession) -> str:
        max_id = (await session.execute(select(func.max(Card.id)))).scalar_one_or_none() or 0
        return f"CRD-{max_id + 1:04d}"

    @staticmethod
    async def create_card(
        session: AsyncSession,
        character_name: str,
        anime_name: str,
        image: str,
        rarity: str,
        base_value: int,
        drop_weight: int = 20,
        rank: int = 100,
    ) -> Card:
        code = await CardService.generate_next_card_code(session)
        card = Card(
            card_code=code,
            character_name=character_name.strip()[:128],
            anime_name=anime_name.strip()[:128],
            anime_id=_slugify(anime_name, "anm"),
            character_id=_slugify(character_name, "chr"),
            image=image.strip()[:512],
            rarity=rarity,
            rank=rank,
            base_value=max(0, int(base_value)),
            drop_weight=max(0, int(drop_weight)),
            is_available=True,
            is_deleted=False,
        )
        session.add(card)
        await session.flush()
        return card

    @staticmethod
    async def get_active_event(session: AsyncSession) -> Optional[Event]:
        now = utcnow()
        res = await session.execute(
            select(Event)
            .where(
                Event.is_active.is_(True),
                Event.starts_at <= now,
                Event.ends_at >= now,
            )
            .order_by(Event.id.desc())
            .limit(1)
        )
        return res.scalar_one_or_none()

    @staticmethod
    async def pick_random_card(
        session: AsyncSession,
        consume_priority: bool = True,
    ) -> Optional[Card]:
        if consume_priority:
            priority_id = await SettingsService.get_next_priority_card_id(session)
            if priority_id is not None:
                p_card = await session.get(Card, priority_id)
                await SettingsService.set_next_priority_card_id(session, None)
                if p_card is not None and p_card.is_available and not p_card.is_deleted:
                    return p_card

        res = await session.execute(
            select(Card).where(
                Card.is_available.is_(True),
                Card.is_deleted.is_(False),
                Card.drop_weight > 0,
            )
        )
        cards = list(res.scalars().all())
        if not cards:
            return None

        active_event = await CardService.get_active_event(session)
        weights: List[float] = []
        for card in cards:
            w = float(card.drop_weight)
            if (
                active_event is not None
                and active_event.boosted_rarity
                and card.rarity.lower() == active_event.boosted_rarity.lower()
            ):
                w *= max(1.0, active_event.rarity_weight_multiplier)
            weights.append(max(0.1, w))

        return random.choices(cards, weights=weights, k=1)[0]

    @staticmethod
    async def get_card_stats(session: AsyncSession, card_id: int) -> Tuple[int, int]:
        """Returns (unique_owners_count, total_copies_count)."""
        owners = (
            await session.execute(
                select(func.count(func.distinct(UserCard.user_id))).where(UserCard.card_id == card_id)
            )
        ).scalar_one()
        copies = (
            await session.execute(
                select(func.count(UserCard.id)).where(UserCard.card_id == card_id)
            )
        ).scalar_one()
        return int(owners or 0), int(copies or 0)

    @staticmethod
    async def soft_delete_card(
        session: AsyncSession,
        card_id: int,
        migration_mode: str = "archive",
    ) -> int:
        """
        Safely soft-deletes a card.
        migration_mode:
          - 'archive': disables future drops, keeps existing user copies as legacy archived cards.
          - 'refund': cancels open listings/offers, removes user copies, and compensates owners with base_value coins.
        Returns number of affected user copies.
        """
        card = await session.get(Card, card_id)
        if card is None:
            return 0

        card.is_available = False
        card.is_deleted = True
        card.archived_note = f"Soft-deleted ({migration_mode})"

        if migration_mode != "refund":
            await session.flush()
            return 0

        # Cancel active market listings & refund/expire offers on this card
        uc_res = await session.execute(select(UserCard).where(UserCard.card_id == card_id))
        copies = list(uc_res.scalars().all())
        affected = len(copies)

        await session.execute(
            delete(CardListing).where(CardListing.card_id == card_id, CardListing.status == "active")
        )
        if copies:
            await session.execute(
                delete(PendingTransfer).where(
                    PendingTransfer.user_card_id.in_([c.id for c in copies])
                )
            )

        # Refund any pending escrowed offers on this card
        pending_offers = (
            await session.execute(
                select(Offer).where(Offer.card_id == card_id, Offer.status == "pending")
            )
        ).scalars().all()
        for off in pending_offers:
            off.status = "cancelled"
            await UserService.credit_coins(
                session=session,
                user_id=off.buyer_id,
                amount=off.amount,
                tx_type="offer_refund",
                description=f"Card {card.card_code} removed - offer refund",
                reference_id=f"offer:{off.id}",
            )

        owner_ids = set()
        for uc in copies:
            owner_ids.add(uc.user_id)
            if card.base_value > 0:
                await UserService.credit_coins(
                    session=session,
                    user_id=uc.user_id,
                    amount=card.base_value,
                    tx_type="card_migration_refund",
                    description=f"Archived card compensation: {card.character_name}",
                    reference_id=f"card_del:{card.id}:{uc.id}",
                )
            await session.delete(uc)

        await session.flush()
        for uid in owner_ids:
            await UserService.recompute_card_counts(session, uid)

        return affected
