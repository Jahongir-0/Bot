import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.card import Card
from models.group import GroupMember
from models.user import User
from models.user_card import UserCard
from services.settings_service import SettingsService
from utils.cache_scope import cache_scope
from utils.constants import RARITY_REGISTRY, RankingMode, Rarity

CACHE_TTL_SECONDS = 20.0
_CACHE: Dict[Tuple[Any, ...], Tuple[float, Any]] = {}


@dataclass
class RankEntry:
    rank: int
    user: User
    score: int
    total_cards: int
    unique_cards: int
    legendary_cards: int
    mythic_cards: int


def _cache_get(key: Tuple[Any, ...]) -> Any:
    hit = _CACHE.get(key)
    if hit is None:
        return None
    ts, value = hit
    if time.monotonic() - ts > CACHE_TTL_SECONDS:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_put(key: Tuple[Any, ...], value: Any) -> None:
    if len(_CACHE) > 2000:
        _CACHE.clear()
    _CACHE[key] = (time.monotonic(), value)


class RankingService:
    @staticmethod
    def invalidate_cache() -> None:
        _CACHE.clear()

    @staticmethod
    def _scope(session: AsyncSession) -> int:
        # Har bir DB ulanishi (test/production) uchun alohida kesh kaliti
        return cache_scope(session)

    @staticmethod
    def _score_parts(mode: str):
        """(score ifodasi, outer-join uchun subquery yoki None). Hammasi SQL'da hisoblanadi — Python'ga yuklanmaydi."""
        if mode == RankingMode.TOTAL_CARDS.value:
            return User.total_cards, None
        if mode == RankingMode.UNIQUE_FIRST.value:
            return User.unique_cards * 100 + User.total_cards, None

        per_rarity = (
            select(
                UserCard.user_id.label("uid"),
                Card.rarity.label("rarity"),
                func.count(func.distinct(UserCard.card_id)).label("cnt"),
            )
            .join(Card, Card.id == UserCard.card_id)
            .group_by(UserCard.user_id, Card.rarity)
            .subquery()
        )
        weight = case(
            *[(per_rarity.c.rarity == name, meta.rank_weight) for name, meta in RARITY_REGISTRY.items()],
            else_=1,
        )
        weighted = (
            select(per_rarity.c.uid.label("uid"), func.sum(per_rarity.c.cnt * weight).label("w"))
            .group_by(per_rarity.c.uid)
            .subquery()
        )
        return func.coalesce(weighted.c.w, 0) * 10 + User.total_cards, weighted

    @staticmethod
    def _apply_scope(stmt, weighted, group_id: Optional[int]):
        if weighted is not None:
            stmt = stmt.outerjoin(weighted, weighted.c.uid == User.id)
        if group_id is not None:
            stmt = stmt.join(GroupMember, GroupMember.user_id == User.id).where(GroupMember.group_id == group_id)
        return stmt.where(User.is_banned.is_(False))

    @staticmethod
    async def _top(session: AsyncSession, limit: int, group_id: Optional[int]) -> List[RankEntry]:
        mode = await SettingsService.get_ranking_mode(session)
        key = ("top", RankingService._scope(session), mode, group_id, limit)
        cached = _cache_get(key)
        if cached is not None:
            return cached

        score_expr, weighted = RankingService._score_parts(mode)
        stmt = RankingService._apply_scope(select(User, score_expr.label("score")), weighted, group_id)
        stmt = stmt.order_by(
            score_expr.desc(), User.unique_cards.desc(), User.total_cards.desc(), User.id.asc()
        ).limit(limit)
        rows = (await session.execute(stmt)).all()
        if not rows:
            return []

        user_ids = [r[0].id for r in rows]
        rarity_rows = (
            await session.execute(
                select(UserCard.user_id, Card.rarity, func.count(UserCard.id))
                .join(Card, Card.id == UserCard.card_id)
                .where(
                    UserCard.user_id.in_(user_ids),
                    Card.rarity.in_([Rarity.LEGENDARY.value, Rarity.MYTHIC.value]),
                )
                .group_by(UserCard.user_id, Card.rarity)
            )
        ).all()
        by_user: Dict[int, Dict[str, int]] = {}
        for uid, rarity, cnt in rarity_rows:
            by_user.setdefault(uid, {})[rarity] = int(cnt)

        entries: List[RankEntry] = []
        for idx, (user, score) in enumerate(rows, start=1):
            r_map = by_user.get(user.id, {})
            entries.append(
                RankEntry(
                    rank=idx,
                    user=user,
                    score=int(score or 0),
                    total_cards=user.total_cards,
                    unique_cards=user.unique_cards,
                    legendary_cards=r_map.get(Rarity.LEGENDARY.value, 0),
                    mythic_cards=r_map.get(Rarity.MYTHIC.value, 0),
                )
            )
        _cache_put(key, entries)
        return entries

    @staticmethod
    async def get_global_top(session: AsyncSession, limit: int = 10) -> List[RankEntry]:
        return await RankingService._top(session, limit, None)

    @staticmethod
    async def get_group_top(session: AsyncSession, group_id: int, limit: int = 10) -> List[RankEntry]:
        return await RankingService._top(session, limit, group_id)

    @staticmethod
    async def _rank_of(
        session: AsyncSession,
        mode: str,
        user_id: int,
        group_id: Optional[int],
    ) -> Optional[int]:
        score_expr, weighted = RankingService._score_parts(mode)

        my_stmt = select(score_expr).select_from(User).where(User.id == user_id)
        if weighted is not None:
            my_stmt = my_stmt.outerjoin(weighted, weighted.c.uid == User.id)
        my_score = (await session.execute(my_stmt)).scalar_one_or_none()
        if my_score is None:
            return None
        my_score = int(my_score)

        key = ("rank", RankingService._scope(session), mode, group_id, my_score)
        cached = _cache_get(key)
        if cached is not None:
            return cached

        count_stmt = RankingService._apply_scope(select(func.count(User.id)).select_from(User), weighted, group_id)
        better = (await session.execute(count_stmt.where(score_expr > my_score))).scalar_one()
        rank = int(better or 0) + 1
        _cache_put(key, rank)
        return rank

    @staticmethod
    async def get_user_ranks(
        session: AsyncSession,
        user_id: int,
        group_id: Optional[int] = None,
    ) -> tuple[int, Optional[int]]:
        mode = await SettingsService.get_ranking_mode(session)
        global_rank = await RankingService._rank_of(session, mode, user_id, None)
        if global_rank is None:
            total = (await session.execute(select(func.count(User.id)))).scalar_one()
            global_rank = int(total or 0) + 1

        group_rank: Optional[int] = None
        if group_id is not None:
            is_member = (
                await session.execute(
                    select(GroupMember.id).where(GroupMember.group_id == group_id, GroupMember.user_id == user_id)
                )
            ).first()
            if is_member is not None:
                group_rank = await RankingService._rank_of(session, mode, user_id, group_id)
        return global_rank, group_rank
