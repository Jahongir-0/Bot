from dataclasses import dataclass
from typing import List, Optional, Tuple

from sqlalchemy import func, select
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


class SearchService:
    @staticmethod
    async def search_global_cards(
        session: AsyncSession,
        mode: str,
        query: str = "",
        page: int = 1,
        page_size: int = 5,
    ) -> Tuple[List[GlobalCardSearchItem], int]:
        stmt = (
            select(UserCard, Card, User)
            .join(Card, Card.id == UserCard.card_id)
            .join(User, User.id == UserCard.user_id)
            .where(User.is_banned.is_(False))
        )
        count_stmt = (
            select(func.count(UserCard.id))
            .join(Card, Card.id == UserCard.card_id)
            .join(User, User.id == UserCard.user_id)
            .where(User.is_banned.is_(False))
        )

        q = query.strip()
        if mode == "character" and q:
            pattern = f"%{q}%"
            stmt = stmt.where(Card.character_name.ilike(pattern))
            count_stmt = count_stmt.where(Card.character_name.ilike(pattern))
        elif mode == "anime" and q:
            pattern = f"%{q}%"
            stmt = stmt.where(Card.anime_name.ilike(pattern))
            count_stmt = count_stmt.where(Card.anime_name.ilike(pattern))
        elif mode == "rarity" and q:
            stmt = stmt.where(func.lower(Card.rarity) == q.lower())
            count_stmt = count_stmt.where(func.lower(Card.rarity) == q.lower())
        elif mode == "card_id" and q:
            if q.isdigit():
                stmt = stmt.where((UserCard.id == int(q)) | (Card.id == int(q)))
                count_stmt = count_stmt.where((UserCard.id == int(q)) | (Card.id == int(q)))
            else:
                stmt = stmt.where(func.lower(Card.card_code) == q.lower())
                count_stmt = count_stmt.where(func.lower(Card.card_code) == q.lower())

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
