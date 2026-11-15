from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.card import Card
from models.user_card import UserCard
from utils.constants import list_rarities


@dataclass
class CharacterStack:
    card: Card
    quantity: int
    available_count: int
    sample_user_card_id: int
    is_favorite: bool


class CollectionService:
    @staticmethod
    async def get_summary(
        session: AsyncSession,
        user_id: int,
    ) -> Tuple[List[Tuple[str, int]], Dict[str, int], List[Tuple[str, int]]]:
        anime_rows = (
            await session.execute(
                select(Card.anime_name, func.count(UserCard.id).label("cnt"))
                .join(Card, Card.id == UserCard.card_id)
                .where(UserCard.user_id == user_id)
                .group_by(Card.anime_name)
                .order_by(func.count(UserCard.id).desc())
            )
        ).all()

        rarity_rows = (
            await session.execute(
                select(Card.rarity, func.count(UserCard.id).label("cnt"))
                .join(Card, Card.id == UserCard.card_id)
                .where(UserCard.user_id == user_id)
                .group_by(Card.rarity)
            )
        ).all()

        rarity_map: Dict[str, int] = {r: 0 for r in list_rarities()}
        for rarity_name, cnt in rarity_rows:
            rarity_map[rarity_name] = int(cnt)

        # Favorites first, then highest duplicate stacks ("Gojo Satoru × 7")
        fav_rows = (
            await session.execute(
                select(
                    Card.character_name,
                    func.count(UserCard.id).label("cnt"),
                    func.max(UserCard.is_favorite).label("fav"),
                )
                .join(Card, Card.id == UserCard.card_id)
                .where(UserCard.user_id == user_id)
                .group_by(Card.id, Card.character_name)
                .order_by(func.max(UserCard.is_favorite).desc(), func.count(UserCard.id).desc())
                .limit(5)
            )
        ).all()

        favorites = [(r[0], int(r[1])) for r in fav_rows]
        anime_counts = [(r[0], int(r[1])) for r in anime_rows]
        return anime_counts, rarity_map, favorites

    @staticmethod
    async def get_character_stacks(
        session: AsyncSession,
        user_id: int,
        anime_filter: Optional[str] = None,
        rarity_filter: Optional[str] = None,
        page: int = 1,
        page_size: int = 6,
    ) -> Tuple[List[CharacterStack], int]:
        stmt = (
            select(
                Card,
                func.count(UserCard.id).label("qty"),
                func.min(UserCard.id).label("sample_id"),
            )
            .join(Card, Card.id == UserCard.card_id)
            .where(UserCard.user_id == user_id)
        )
        if anime_filter and anime_filter != "all":
            if anime_filter.startswith("id:"):
                stmt = stmt.where(Card.anime_id.startswith(anime_filter[3:]))
            else:
                stmt = stmt.where(Card.anime_name.ilike(f"{anime_filter}%"))
        if rarity_filter and rarity_filter != "all":
            stmt = stmt.where(Card.rarity == rarity_filter)

        stmt = stmt.group_by(Card.id).order_by(func.count(UserCard.id).desc(), Card.base_value.desc())

        rows = (await session.execute(stmt)).all()
        total = len(rows)
        start = (max(1, page) - 1) * page_size
        sliced = rows[start : start + page_size]

        stacks: List[CharacterStack] = []
        for card, qty, sample_id in sliced:
            unlocked_cnt = (
                await session.execute(
                    select(func.count(UserCard.id)).where(
                        UserCard.user_id == user_id,
                        UserCard.card_id == card.id,
                        UserCard.is_locked.is_(False),
                    )
                )
            ).scalar_one()
            fav_cnt = (
                await session.execute(
                    select(func.count(UserCard.id)).where(
                        UserCard.user_id == user_id,
                        UserCard.card_id == card.id,
                        UserCard.is_favorite.is_(True),
                    )
                )
            ).scalar_one()
            stacks.append(
                CharacterStack(
                    card=card,
                    quantity=int(qty),
                    available_count=int(unlocked_cnt or 0),
                    sample_user_card_id=int(sample_id),
                    is_favorite=bool(fav_cnt),
                )
            )

        return stacks, total

    @staticmethod
    async def get_user_anime_names(session: AsyncSession, user_id: int) -> List[Tuple[str, str]]:
        rows = (
            await session.execute(
                select(Card.anime_name, Card.anime_id)
                .join(Card, Card.id == UserCard.card_id)
                .where(UserCard.user_id == user_id)
                .group_by(Card.anime_name, Card.anime_id)
                .order_by(func.count(UserCard.id).desc())
            )
        ).all()
        return [(r[0], r[1]) for r in rows]

    @staticmethod
    async def toggle_favorite_for_card(
        session: AsyncSession,
        user_id: int,
        user_card_id: int,
    ) -> bool:
        uc = await session.get(UserCard, user_card_id)
        if uc is None or uc.user_id != user_id:
            return False
        new_state = not uc.is_favorite
        siblings = (
            await session.execute(
                select(UserCard).where(UserCard.user_id == user_id, UserCard.card_id == uc.card_id)
            )
        ).scalars().all()
        for item in siblings:
            item.is_favorite = new_state
        await session.flush()
        return new_state

    @staticmethod
    async def get_unlocked_copy(
        session: AsyncSession,
        user_id: int,
        card_id: int,
    ) -> Optional[UserCard]:
        res = await session.execute(
            select(UserCard)
            .where(
                UserCard.user_id == user_id,
                UserCard.card_id == card_id,
                UserCard.is_locked.is_(False),
            )
            .order_by(UserCard.id.asc())
            .limit(1)
        )
        return res.scalar_one_or_none()
