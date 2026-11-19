import difflib
from dataclasses import dataclass
from typing import List, Optional, Tuple

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.card import Card
from models.market import CardListing
from models.user import User
from models.user_card import UserCard


@dataclass
class GlobalCardSearchItem:
    user_card: UserCard
    card: Card
    owner: User
    active_listing: Optional[CardListing]


def _apply_card_filters(stmt, count_stmt, mode: str, query: str, include_user_card_id: bool = True):
    q = query.strip()
    if not q:
        return stmt, count_stmt

    pattern = f"%{q}%"
    if mode == "character":
        cond = Card.character_name.ilike(pattern)
    elif mode == "anime":
        cond = Card.anime_name.ilike(pattern)
    elif mode == "rarity":
        cond = func.lower(Card.rarity) == q.lower()
    elif mode == "card_id":
        if q.isdigit():
            cond = (UserCard.id == int(q)) | (Card.id == int(q)) if include_user_card_id else (Card.id == int(q))
        else:
            cond = func.lower(Card.card_code) == q.lower()
    elif mode == "any":
        conditions = [
            Card.character_name.ilike(pattern),
            Card.anime_name.ilike(pattern),
            func.lower(Card.card_code) == q.lower(),
            func.lower(Card.rarity) == q.lower(),
        ]
        if q.isdigit():
            conditions.append(Card.id == int(q))
            if include_user_card_id:
                conditions.append(UserCard.id == int(q))
        cond = or_(*conditions)
    else:
        return stmt, count_stmt

    return stmt.where(cond), count_stmt.where(cond)


class SearchService:
    @staticmethod
    async def search_global_cards(
        session: AsyncSession,
        mode: str,
        query: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[GlobalCardSearchItem], int]:
        stmt = (
            select(UserCard, Card, User)
            .join(Card, Card.id == UserCard.card_id)
            .join(User, User.id == UserCard.user_id)
            .where(User.is_banned.is_(False), Card.is_deleted.is_(False))
        )
        count_stmt = (
            select(func.count(UserCard.id))
            .join(Card, Card.id == UserCard.card_id)
            .join(User, User.id == UserCard.user_id)
            .where(User.is_banned.is_(False), Card.is_deleted.is_(False))
        )

        stmt, count_stmt = _apply_card_filters(stmt, count_stmt, mode, query, include_user_card_id=True)

        if mode == "random":
            total = int((await session.execute(count_stmt)).scalar_one() or 0)
            rows = (await session.execute(stmt.order_by(func.random()).limit(page_size))).all()
        else:
            total = int((await session.execute(count_stmt)).scalar_one() or 0)
            offset = (max(1, page) - 1) * page_size
            rows = (
                await session.execute(
                    stmt.order_by(Card.base_value.desc(), UserCard.id.desc())
                    .offset(offset)
                    .limit(page_size)
                )
            ).all()

        results: List[GlobalCardSearchItem] = []
        for uc, card, owner in rows:
            listing = (
                await session.execute(
                    select(CardListing)
                    .where(
                        CardListing.user_card_id == uc.id,
                        CardListing.status == "active",
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            results.append(
                GlobalCardSearchItem(
                    user_card=uc,
                    card=card,
                    owner=owner,
                    active_listing=listing,
                )
            )
        return results, total

    @staticmethod
    async def search_catalog_cards(
        session: AsyncSession,
        mode: str,
        query: str = "",
        page: int = 1,
        page_size: int = 20,
        media_type: Optional[str] = None,
        sort_by: str = "value_desc",
        include_unreleased: bool = False,
    ) -> Tuple[List[Card], int]:
        """Foydalanuvchilar uchun katalog qidiruvi (unreleased kartalarni yashiradi, typo va aliaslarni qo'llab-quvvatlaydi)."""
        has_owner = exists().where(UserCard.card_id == Card.id)
        base_conds = [Card.is_deleted.is_(False)]
        if not include_unreleased:
            base_conds.append((Card.dropped_count > 0) | Card.is_event_card.is_(True) | has_owner)
        if media_type and media_type != "all":
            if media_type == "Manga":
                base_conds.append(Card.media_type.in_(["Manga", "Manhwa", "Manhua"]))
            else:
                base_conds.append(Card.media_type == media_type)

        stmt = select(Card).where(*base_conds)
        count_stmt = select(func.count(Card.id)).where(*base_conds)

        stmt, count_stmt = _apply_card_filters(stmt, count_stmt, mode, query, include_user_card_id=False)

        total = int((await session.execute(count_stmt)).scalar_one() or 0)
        if total == 0 and query.strip() and mode in ("character", "any"):
            # Alias va yozuv xatosiga chidamli qidiruv
            all_released = list((await session.execute(select(Card).where(*base_conds))).scalars().all())
            q_low = query.strip().lower()
            matched: List[Card] = []
            for c in all_released:
                aliases = [str(a).lower() for a in (getattr(c, "aliases", None) or [])]
                if any(q_low in a for a in aliases):
                    matched.append(c)
            if not matched:
                names = [c.character_name for c in all_released]
                close = set(difflib.get_close_matches(query.strip(), names, n=10, cutoff=0.55))
                matched = [c for c in all_released if c.character_name in close]
            total = len(matched)
            offset = (max(1, page) - 1) * page_size
            return matched[offset : offset + page_size], total

        if mode == "random":
            cards = list((await session.execute(stmt.order_by(func.random()).limit(page_size))).scalars().all())
        else:
            order_col = (
                Card.character_name.asc()
                if sort_by == "alpha"
                else (Card.id.desc() if sort_by == "newest" else Card.base_value.desc())
            )
            offset = (max(1, page) - 1) * page_size
            cards = list(
                (await session.execute(stmt.order_by(order_col, Card.id.asc()).offset(offset).limit(page_size)))
                .scalars()
                .all()
            )
        return cards, total

    @staticmethod
    async def inspect_user_card(
        session: AsyncSession,
        user_card_id: int,
    ) -> Optional[GlobalCardSearchItem]:
        row = (
            await session.execute(
                select(UserCard, Card, User)
                .join(Card, Card.id == UserCard.card_id)
                .join(User, User.id == UserCard.user_id)
                .where(UserCard.id == user_card_id)
            )
        ).first()
        if row is None:
            return None
        uc, card, owner = row
        listing = (
            await session.execute(
                select(CardListing)
                .where(CardListing.user_card_id == uc.id, CardListing.status == "active")
                .limit(1)
            )
        ).scalar_one_or_none()
        return GlobalCardSearchItem(
            user_card=uc,
            card=card,
            owner=owner,
            active_listing=listing,
        )

    @staticmethod
    async def inspect_catalog_card(session: AsyncSession, card_id: int) -> Optional[Card]:
        card = await session.get(Card, card_id)
        if card is None or card.is_deleted:
            return None
        return card
