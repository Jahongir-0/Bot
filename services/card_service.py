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
from utils.constants import get_rarity_meta


def normalize_name(value: str) -> str:
    """Ortiqcha bo'shliqlarni olib tashlaydi: '  Jujutsu   Kaisen ' -> 'Jujutsu Kaisen'."""
    return re.sub(r"\s+", " ", (value or "").strip())


def _slugify(value: str, prefix: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    if cleaned:
        return f"{prefix}_{cleaned[:40]}"
    # Lotin bo'lmagan nomlar (yapon, kirill) uchun barqaror hash — hammasi bitta 'item' bo'lib qolmasin
    digest = hashlib.sha1(value.casefold().encode("utf-8")).hexdigest()[:10]
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
        drop_weight: Optional[int] = None,
        rank: int = 100,
        drop_limit: Optional[int] = None,
        media_type: str = "Anime",
        aliases: Optional[List[str]] = None,
        metadata_json: Optional[dict] = None,
        battle_stats: Optional[dict] = None,
    ) -> Card:
        meta = get_rarity_meta(rarity)
        code = await CardService.generate_next_card_code(session)
        character_name = normalize_name(character_name)[:128]
        anime_name = normalize_name(anime_name)[:128]

        anime_id = _slugify(anime_name, "anm")
        existing_anime = (
            await session.execute(
                select(Card.anime_name, Card.anime_id)
                .where(func.lower(func.trim(Card.anime_name)) == anime_name.lower())
                .order_by(Card.id.asc())
                .limit(1)
            )
        ).first()
        if existing_anime is not None:
            anime_name, anime_id = existing_anime[0], existing_anime[1]

        character_id = _slugify(character_name, "chr")
        existing_char = (
            await session.execute(
                select(Card.character_name, Card.character_id)
                .where(
                    func.lower(func.trim(Card.anime_name)) == anime_name.lower(),
                    func.lower(func.trim(Card.character_name)) == character_name.lower(),
                )
                .order_by(Card.id.asc())
                .limit(1)
            )
        ).first()
        if existing_char is not None:
            character_name, character_id = existing_char[0], existing_char[1]

        card = Card(
            card_code=code,
            character_name=character_name,
            anime_name=anime_name,
            anime_id=anime_id,
            character_id=character_id,
            image=image.strip()[:512],
            rarity=rarity,
            rank=rank,
            base_value=max(0, int(base_value)),
            drop_weight=max(0, int(drop_weight if drop_weight is not None else meta.default_drop_weight)),
            drop_limit=max(1, int(drop_limit if drop_limit is not None else meta.default_drop_limit)),
            dropped_count=0,
            is_available=True,
            is_deleted=False,
            media_type=media_type or "Anime",
            aliases=aliases or [],
            card_state="AVAILABLE",
            metadata_json=metadata_json or {},
            battle_stats=battle_stats or {},
        )
        session.add(card)
        await session.flush()
        return card

    @staticmethod
    async def restore_card(session: AsyncSession, card_id: int) -> Optional[Card]:
        card = await session.get(Card, card_id)
        if card is None:
            return None
        card.is_deleted = False
        card.is_available = True
        card.archived_note = None
        card.card_state = "EVENT" if card.is_event_card else "AVAILABLE"
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
                Card.is_event_card.is_(False),
                Card.drop_weight > 0,
                Card.dropped_count < Card.drop_limit,
            )
        )
        cards = list(res.scalars().all())
        from services.card_level_service import CardLevelService

        mults = await CardLevelService.get_drop_mults(session)
        cards = [c for c in cards if CardLevelService.rarity_mult(mults, c.rarity) > 0]
        if not cards:
            return None

        active_event = await CardService.get_active_event(session)
        random.shuffle(cards)

        def _weight(card: Card) -> float:
            w = float(card.drop_weight) * CardLevelService.rarity_mult(mults, card.rarity)
            if (
                active_event is not None
                and active_event.boosted_rarity
                and card.rarity.lower() == active_event.boosted_rarity.lower()
            ):
                w *= max(1.0, active_event.rarity_weight_multiplier)
            return max(0.01, w)

        by_anime = {}
        for card in cards:
            akey = card.anime_id or (card.anime_name or "").strip().casefold()
            by_anime.setdefault(akey, []).append(card)
        anime_groups = list(by_anime.values())
        random.shuffle(anime_groups)
        anime_weights = [
            max(0.01, sum(_weight(c) for c in grp) / len(grp))
            for grp in anime_groups
        ]
        chosen_group = random.choices(anime_groups, weights=anime_weights, k=1)[0]
        random.shuffle(chosen_group)
        return random.choices(chosen_group, weights=[_weight(c) for c in chosen_group], k=1)[0]

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
        await session.execute(
            delete(PendingTransfer).where(
                PendingTransfer.user_card_id.in_([c.id for c in copies]) if copies else False
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
