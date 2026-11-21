import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.card import Card
from models.group import Group, GroupMember
from models.schedule import ActiveDrop
from models.user import User
from models.user_card import UserCard
from services.settings_service import SettingsService
from utils.cache_scope import cache_scope
from utils.constants import (
    DEFAULT_RANK_DEFINITIONS,
    RankingMode,
    Rarity,
    get_rank_name_by_order,
    get_rarity_aliases,
    get_rarity_meta,
    get_runtime_ranks,
)

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


@dataclass
class GroupRankEntry:
    rank: int
    group: Group
    active_users: int
    claimed_cards: int
    member_count: int
    score: int


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
    def _score_parts(mode: str, ranks: Optional[List[Dict[str, Any]]] = None):
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
        rank_list = ranks or get_runtime_ranks() or list(DEFAULT_RANK_DEFINITIONS)
        whens = []
        for r in rank_list:
            name = str(r.get("name") or "").strip()
            if not name:
                continue
            rw = int(r.get("rank_weight") or get_rarity_meta(name).rank_weight or 1)
            whens.append((func.lower(per_rarity.c.rarity) == name.lower(), rw))
        weight = case(*whens, else_=1) if whens else 1
        weighted = (
            select(per_rarity.c.uid.label("uid"), func.sum(per_rarity.c.cnt * weight).label("w"))
            .group_by(per_rarity.c.uid)
            .subquery()
        )
        return func.coalesce(weighted.c.w, 0) * 10 + User.total_cards, weighted

    @staticmethod
    def _apply_scope(
        stmt,
        weighted,
        group_id: Optional[int],
        excluded_ids: Optional[List[int]] = None,
    ):
        if weighted is not None:
            stmt = stmt.outerjoin(weighted, weighted.c.uid == User.id)
        if group_id is not None:
            stmt = stmt.join(GroupMember, GroupMember.user_id == User.id).where(GroupMember.group_id == group_id)
        if excluded_ids:
            stmt = stmt.where(User.id.not_in(excluded_ids))
        return stmt.where(User.is_banned.is_(False))

    @staticmethod
    async def _live_ranks(session: AsyncSession) -> List[Dict[str, Any]]:
        raw = await SettingsService.get_value(session, "rank_definitions", DEFAULT_RANK_DEFINITIONS)
        items = list(raw) if isinstance(raw, list) and raw else get_runtime_ranks()
        items.sort(key=lambda x: int(x.get("order", 99)))
        return items

    @staticmethod
    async def _top(session: AsyncSession, limit: int, group_id: Optional[int]) -> List[RankEntry]:
        from services.admin_service import AdminService

        mode = await SettingsService.get_ranking_mode(session)
        ranks = await RankingService._live_ranks(session)
        excluded_ids = await AdminService.get_excluded_admin_user_ids(session)
        rank_sig = tuple((str(r.get("name")), int(r.get("rank_weight", 1))) for r in ranks)
        key = ("top", RankingService._scope(session), mode, group_id, limit, rank_sig, tuple(excluded_ids))
        cached = _cache_get(key)
        if cached is not None:
            return cached

        score_expr, weighted = RankingService._score_parts(mode, ranks)
        stmt = RankingService._apply_scope(
            select(User, score_expr.label("score")), weighted, group_id, excluded_ids=excluded_ids
        )
        stmt = stmt.order_by(
            score_expr.desc(), User.unique_cards.desc(), User.total_cards.desc(), User.id.asc()
        ).limit(limit)
        rows = (await session.execute(stmt)).all()
        if not rows:
            return []

        r4_name = next((str(r["name"]) for r in ranks if int(r.get("order", 0)) == 4), get_rank_name_by_order(4, Rarity.LEGENDARY.value))
        r5_name = next((str(r["name"]) for r in ranks if int(r.get("order", 0)) == 5), get_rank_name_by_order(5, Rarity.MYTHIC.value))
        r4_low, r5_low = r4_name.lower(), r5_name.lower()
        r4_aliases = set(get_rarity_aliases(r4_name) or [r4_low])
        r5_aliases = set(get_rarity_aliases(r5_name) or [r5_low])

        user_ids = [r[0].id for r in rows]
        rarity_rows = (
            await session.execute(
                select(UserCard.user_id, func.lower(Card.rarity), func.count(UserCard.id))
                .join(Card, Card.id == UserCard.card_id)
                .where(
                    UserCard.user_id.in_(user_ids),
                    func.lower(Card.rarity).in_(list(r4_aliases | r5_aliases)),
                )
                .group_by(UserCard.user_id, func.lower(Card.rarity))
            )
        ).all()
        by_user: Dict[int, Dict[str, int]] = {}
        for uid, rarity_low, cnt in rarity_rows:
            r_l = str(rarity_low)
            canonical_key = r4_low if r_l in r4_aliases else (r5_low if r_l in r5_aliases else r_l)
            by_user.setdefault(uid, {})[canonical_key] = by_user.get(uid, {}).get(canonical_key, 0) + int(cnt)

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
                    legendary_cards=r_map.get(r4_low, 0),
                    mythic_cards=r_map.get(r5_low, 0),
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
        excluded_ids: Optional[List[int]] = None,
    ) -> Optional[int]:
        if excluded_ids is None:
            from services.admin_service import AdminService

            excluded_ids = await AdminService.get_excluded_admin_user_ids(session)
        if user_id in excluded_ids:
            return 0
        ranks = await RankingService._live_ranks(session)
        score_expr, weighted = RankingService._score_parts(mode, ranks)

        my_stmt = select(score_expr).select_from(User).where(User.id == user_id)
        if weighted is not None:
            my_stmt = my_stmt.outerjoin(weighted, weighted.c.uid == User.id)
        my_score = (await session.execute(my_stmt)).scalar_one_or_none()
        if my_score is None:
            return None
        my_score = int(my_score)

        key = ("rank", RankingService._scope(session), mode, group_id, my_score, tuple(excluded_ids))
        cached = _cache_get(key)
        if cached is not None:
            return cached

        count_stmt = RankingService._apply_scope(
            select(func.count(User.id)).select_from(User), weighted, group_id, excluded_ids=excluded_ids
        )
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
        from services.admin_service import AdminService

        excluded_ids = await AdminService.get_excluded_admin_user_ids(session)
        if user_id in excluded_ids:
            return 0, None

        mode = await SettingsService.get_ranking_mode(session)
        global_rank = await RankingService._rank_of(session, mode, user_id, None, excluded_ids=excluded_ids)
        if global_rank is None:
            cnt_stmt = select(func.count(User.id))
            if excluded_ids:
                cnt_stmt = cnt_stmt.where(User.id.not_in(excluded_ids))
            total = (await session.execute(cnt_stmt)).scalar_one()
            global_rank = int(total or 0) + 1

        group_rank: Optional[int] = None
        if group_id is not None:
            is_member = (
                await session.execute(
                    select(GroupMember.id).where(GroupMember.group_id == group_id, GroupMember.user_id == user_id)
                )
            ).first()
            if is_member is not None:
                group_rank = await RankingService._rank_of(
                    session, mode, user_id, group_id, excluded_ids=excluded_ids
                )
        return global_rank, group_rank

    @staticmethod
    async def get_groups_top(session: AsyncSession, limit: int = 10) -> List[GroupRankEntry]:
        key = ("groups_top", RankingService._scope(session), limit)
        cached = _cache_get(key)
        if cached is not None:
            return cached

        users_sq = (
            select(GroupMember.group_id.label("gid"), func.count(GroupMember.id).label("u_cnt"))
            .group_by(GroupMember.group_id)
            .subquery()
        )
        claims_sq = (
            select(ActiveDrop.group_id.label("gid"), func.count(ActiveDrop.id).label("c_cnt"))
            .where(ActiveDrop.is_claimed.is_(True))
            .group_by(ActiveDrop.group_id)
            .subquery()
        )
        active_users_expr = func.coalesce(users_sq.c.u_cnt, 0)
        claimed_expr = func.coalesce(claims_sq.c.c_cnt, 0)
        score_expr = active_users_expr * 10 + claimed_expr * 5

        rows = (
            await session.execute(
                select(Group, active_users_expr.label("au"), claimed_expr.label("cc"), score_expr.label("sc"))
                .outerjoin(users_sq, users_sq.c.gid == Group.id)
                .outerjoin(claims_sq, claims_sq.c.gid == Group.id)
                .where(Group.is_active.is_(True))
                .order_by(
                    score_expr.desc(),
                    claimed_expr.desc(),
                    active_users_expr.desc(),
                    func.coalesce(Group.member_count, 0).desc(),
                    Group.id.asc(),
                )
                .limit(limit)
            )
        ).all()

        entries = [
            GroupRankEntry(
                rank=idx,
                group=grp,
                active_users=int(au or 0),
                claimed_cards=int(cc or 0),
                member_count=int(grp.member_count or 0),
                score=int(sc or 0),
            )
            for idx, (grp, au, cc, sc) in enumerate(rows, start=1)
        ]
        _cache_put(key, entries)
        return entries

    @staticmethod
    async def get_group_standing(session: AsyncSession, group_id: int) -> Optional[GroupRankEntry]:
        grp = await session.get(Group, group_id)
        if grp is None:
            return None
        au = int(
            (
                await session.execute(select(func.count(GroupMember.id)).where(GroupMember.group_id == group_id))
            ).scalar_one()
            or 0
        )
        cc = int(
            (
                await session.execute(
                    select(func.count(ActiveDrop.id)).where(
                        ActiveDrop.group_id == group_id, ActiveDrop.is_claimed.is_(True)
                    )
                )
            ).scalar_one()
            or 0
        )
        score = au * 10 + cc * 5

        users_sq = (
            select(GroupMember.group_id.label("gid"), func.count(GroupMember.id).label("u_cnt"))
            .group_by(GroupMember.group_id)
            .subquery()
        )
        claims_sq = (
            select(ActiveDrop.group_id.label("gid"), func.count(ActiveDrop.id).label("c_cnt"))
            .where(ActiveDrop.is_claimed.is_(True))
            .group_by(ActiveDrop.group_id)
            .subquery()
        )
        other_score = func.coalesce(users_sq.c.u_cnt, 0) * 10 + func.coalesce(claims_sq.c.c_cnt, 0) * 5
        better = int(
            (
                await session.execute(
                    select(func.count(Group.id))
                    .select_from(Group)
                    .outerjoin(users_sq, users_sq.c.gid == Group.id)
                    .outerjoin(claims_sq, claims_sq.c.gid == Group.id)
                    .where(Group.is_active.is_(True), other_score > score)
                )
            ).scalar_one()
            or 0
        )
        return GroupRankEntry(
            rank=better + 1,
            group=grp,
            active_users=au,
            claimed_cards=cc,
            member_count=int(grp.member_count or 0),
            score=score,
        )

    @staticmethod
    async def get_extended_top(
        session: AsyncSession,
        scope: str,
        period: str = "all",
        limit: int = 10,
    ) -> List[Tuple[User, int]]:
        from datetime import timedelta
        from models.base import utcnow
        from services.admin_service import AdminService

        excluded_ids = await AdminService.get_excluded_admin_user_ids(session)
        cutoff = None
        if period == "weekly":
            cutoff = utcnow() - timedelta(days=7)
        elif period == "monthly":
            cutoff = utcnow() - timedelta(days=30)

        if scope in ("rare", "mythic", "divine"):
            ranks = await RankingService._live_ranks(session)

            def _expand(names: List[str]) -> List[str]:
                out = set()
                for n in names:
                    out.update(get_rarity_aliases(n) or [n.lower()])
                return list(out)

            rarity_map = {
                "rare": _expand([str(r["name"]) for r in ranks if int(r.get("order", 0)) >= 2]),
                "mythic": _expand([str(r["name"]) for r in ranks if int(r.get("order", 0)) == 5] or ["Mythic"]),
                "divine": _expand([str(r["name"]) for r in ranks if int(r.get("order", 0)) == 6] or ["Divine"]),
            }
            target_rarities = rarity_map.get(scope, [])
            stmt = (
                select(User, func.count(UserCard.id).label("val"))
                .join(UserCard, UserCard.user_id == User.id)
                .join(Card, Card.id == UserCard.card_id)
                .where(User.is_banned.is_(False), func.lower(Card.rarity).in_(target_rarities))
            )
            if excluded_ids:
                stmt = stmt.where(User.id.not_in(excluded_ids))
            if cutoff is not None:
                stmt = stmt.where(UserCard.created_at >= cutoff)
            rows = (
                await session.execute(stmt.group_by(User.id).order_by(func.count(UserCard.id).desc()).limit(limit))
            ).all()
            return [(r[0], int(r[1])) for r in rows]

        col_map = {
            "coins": User.coins,
            "xp": User.xp,
            "level": User.level,
            "quiz": User.quiz_wins,
            "games": User.game_wins,
            "trades": User.trade_count,
            "auction": User.auction_wins,
            "refs": User.referral_count,
            "activity": User.xp,
        }
        col = col_map.get(scope, User.total_cards)
        stmt = select(User, col.label("val")).where(User.is_banned.is_(False))
        if excluded_ids:
            stmt = stmt.where(User.id.not_in(excluded_ids))
        if cutoff is not None:
            stmt = stmt.where(User.last_activity >= cutoff)
        rows = (await session.execute(stmt.order_by(col.desc(), User.id.asc()).limit(limit))).all()
        return [(r[0], int(r[1] or 0)) for r in rows]


