from dataclasses import dataclass
from typing import Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.card import Card
from models.group import GroupMember
from models.user import User
from models.user_card import UserCard
from services.settings_service import SettingsService
from utils.constants import RARITY_REGISTRY, RankingMode, Rarity


@dataclass
class RankEntry:
    rank: int
    user: User
    score: int
    total_cards: int
    unique_cards: int
    legendary_cards: int
    mythic_cards: int


class RankingService:
    @staticmethod
    async def _compute_entries(
        session: AsyncSession,
        user_ids: Optional[List[int]] = None,
    ) -> List[RankEntry]:
        mode = await SettingsService.get_ranking_mode(session)

        user_stmt = select(User).where(User.is_banned.is_(False))
        if user_ids is not None:
            if not user_ids:
                return []
            user_stmt = user_stmt.where(User.id.in_(user_ids))

        users = list((await session.execute(user_stmt)).scalars().all())
        if not users:
            return []

        uid_list = [u.id for u in users]
        # Aggregate per-user rarity counts
        rarity_rows = (
            await session.execute(
                select(UserCard.user_id, Card.rarity, func.count(UserCard.id))
                .join(Card, Card.id == UserCard.card_id)
                .where(UserCard.user_id.in_(uid_list))
                .group_by(UserCard.user_id, Card.rarity)
            )
        ).all()

        rarity_by_user: Dict[int, Dict[str, int]] = {}
        for uid, rarity, cnt in rarity_rows:
            rarity_by_user.setdefault(uid, {})[rarity] = int(cnt)

        # Unique cards per rarity to discourage pure common duplicate farming in weighted mode
        unique_rarity_rows = (
            await session.execute(
                select(UserCard.user_id, Card.rarity, func.count(func.distinct(UserCard.card_id)))
                .join(Card, Card.id == UserCard.card_id)
                .where(UserCard.user_id.in_(uid_list))
                .group_by(UserCard.user_id, Card.rarity)
            )
        ).all()
        unique_rarity_by_user: Dict[int, Dict[str, int]] = {}
        for uid, rarity, cnt in unique_rarity_rows:
            unique_rarity_by_user.setdefault(uid, {})[rarity] = int(cnt)

        entries: List[RankEntry] = []
        for user in users:
            r_map = rarity_by_user.get(user.id, {})
            ur_map = unique_rarity_by_user.get(user.id, {})
            leg_cnt = r_map.get(Rarity.LEGENDARY.value, 0)
            myt_cnt = r_map.get(Rarity.MYTHIC.value, 0)

            if mode == RankingMode.TOTAL_CARDS.value:
                score = user.total_cards
            elif mode == RankingMode.UNIQUE_FIRST.value:
                score = user.unique_cards * 100 + user.total_cards
            else:
                # Anti-duplicate-abuse weighted score: rewards unique cards + high rarity heavily
                weighted_unique = sum(
                    cnt * RARITY_REGISTRY[r].rank_weight
                    for r, cnt in ur_map.items()
                    if r in RARITY_REGISTRY
                )
                score = weighted_unique * 10 + user.total_cards

            entries.append(
                RankEntry(
                    rank=0,
                    user=user,
                    score=score,
                    total_cards=user.total_cards,
                    unique_cards=user.unique_cards,
                    legendary_cards=leg_cnt,
                    mythic_cards=myt_cnt,
                )
            )

        entries.sort(
            key=lambda e: (e.score, e.unique_cards, e.mythic_cards, e.legendary_cards, e.total_cards),
            reverse=True,
        )
        for idx, entry in enumerate(entries, start=1):
            entry.rank = idx
        return entries

    @staticmethod
    async def get_global_top(session: AsyncSession, limit: int = 10) -> List[RankEntry]:
        entries = await RankingService._compute_entries(session)
        return entries[:limit]

    @staticmethod
    async def get_group_top(
        session: AsyncSession,
        group_id: int,
        limit: int = 10,
    ) -> List[RankEntry]:
        member_ids = list(
            (
                await session.execute(
                    select(GroupMember.user_id).where(GroupMember.group_id == group_id)
                )
            )
            .scalars()
            .all()
        )
        entries = await RankingService._compute_entries(session, user_ids=member_ids)
        return entries[:limit]

    @staticmethod
    async def get_user_ranks(
        session: AsyncSession,
        user_id: int,
        group_id: Optional[int] = None,
    ) -> tuple[int, Optional[int]]:
        global_entries = await RankingService._compute_entries(session)
        global_rank = next((e.rank for e in global_entries if e.user.id == user_id), len(global_entries) + 1)

        group_rank: Optional[int] = None
        if group_id is not None:
            group_entries = await RankingService.get_group_top(session, group_id, limit=10_000)
            group_rank = next((e.rank for e in group_entries if e.user.id == user_id), None)

        return global_rank, group_rank
