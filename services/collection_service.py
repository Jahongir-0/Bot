from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.card import Card
from models.user_card import UserCard
from utils.constants import get_rarity_aliases, get_rarity_meta, list_rarities
from utils.grouping import anime_key, character_key, get_anime_key_of, get_character_key_of, get_group_reps


@dataclass
class CharacterStack:
    card: Card
    quantity: int
    available_count: int
    sample_user_card_id: int
    is_favorite: bool


@dataclass
class AnimeSection:
    rep_card_id: int
    anime_name: str
    characters: int
    copies: int


@dataclass
class CharacterSection:
    rep_card_id: int
    character_name: str
    variants: int
    copies: int
    is_favorite: bool
    has_event_card: bool = False


@dataclass
class CardVariant:
    card: Card
    quantity: int
    sample_user_card_id: int
    available_count: int


class CollectionService:
    @staticmethod
    async def get_anime_sections(
        session: AsyncSession,
        user_id: int,
        page: int = 1,
        page_size: int = 6,
    ) -> Tuple[List[AnimeSection], int]:
        """Foydalanuvchi kartalarini anime bo'yicha guruhlaydi (bir xil nomli anime = bitta bo'lim)."""
        rows = (
            await session.execute(
                select(
                    func.min(Card.id),
                    func.min(Card.anime_name),
                    func.count(func.distinct(character_key())),
                    func.count(UserCard.id),
                )
                .join(Card, Card.id == UserCard.card_id)
                .where(UserCard.user_id == user_id)
                .group_by(anime_key())
                .order_by(func.count(UserCard.id).desc(), func.min(Card.anime_name).asc())
            )
        ).all()
        total = len(rows)
        start = (max(1, page) - 1) * page_size
        sections = [
            AnimeSection(int(r[0]), str(r[1]), int(r[2]), int(r[3])) for r in rows[start : start + page_size]
        ]
        return sections, total

    @staticmethod
    async def get_anime_characters(
        session: AsyncSession,
        user_id: int,
        anime_rep_card_id: int,
        page: int = 1,
        page_size: int = 6,
    ) -> Optional[Tuple[str, List[CharacterSection], int]]:
        """Bitta anime ichidagi personajlar (bir xil personajning hamma kartalari birlashtirilgan)."""
        a_key = await get_anime_key_of(session, anime_rep_card_id)
        if a_key is None:
            return None
        rows = (
            await session.execute(
                select(
                    func.min(Card.id),
                    func.min(Card.character_name),
                    func.min(Card.anime_name),
                    func.count(func.distinct(Card.id)),
                    func.count(UserCard.id),
                    func.max(cast(UserCard.is_favorite, Integer)),
                    func.max(cast(Card.is_event_card, Integer)),
                )
                .join(Card, Card.id == UserCard.card_id)
                .where(UserCard.user_id == user_id, anime_key() == a_key)
                .group_by(character_key())
                .order_by(func.count(UserCard.id).desc(), func.min(Card.character_name).asc())
            )
        ).all()
        if not rows:
            return None
        anime_name = str(rows[0][2])
        total = len(rows)
        start = (max(1, page) - 1) * page_size
        chars = [
            CharacterSection(int(r[0]), str(r[1]), int(r[3]), int(r[4]), bool(r[5]), bool(r[6]))
            for r in rows[start : start + page_size]
        ]
        return anime_name, chars, total

    @staticmethod
    async def get_character_variants(
        session: AsyncSession,
        user_id: int,
        char_rep_card_id: int,
    ) -> Optional[Tuple[str, str, List[CardVariant], int]]:
        """Bitta personajning foydalanuvchidagi barcha kartalari. Qaytaradi: (personaj, anime, variantlar, anime_rep_id)."""
        a_key = await get_anime_key_of(session, char_rep_card_id)
        c_key = await get_character_key_of(session, char_rep_card_id)
        if a_key is None or c_key is None:
            return None
        rows = (
            await session.execute(
                select(Card, func.count(UserCard.id), func.min(UserCard.id))
                .join(UserCard, UserCard.card_id == Card.id)
                .where(UserCard.user_id == user_id, anime_key() == a_key, character_key() == c_key)
                .group_by(Card.id)
                .order_by(Card.base_value.desc(), Card.id.asc())
            )
        ).all()
        if not rows:
            return None
        variants: List[CardVariant] = []
        for card, qty, sample_id in rows:
            unlocked = (
                await session.execute(
                    select(func.count(UserCard.id)).where(
                        UserCard.user_id == user_id,
                        UserCard.card_id == card.id,
                        UserCard.is_locked.is_(False),
                    )
                )
            ).scalar_one()
            variants.append(CardVariant(card, int(qty), int(sample_id), int(unlocked or 0)))
        reps = await get_group_reps(session, char_rep_card_id)
        anime_rep = reps[0] if reps else char_rep_card_id
        first = variants[0].card
        return first.character_name, first.anime_name, variants, anime_rep

    @staticmethod
    async def find_cards_by_name(
        session: AsyncSession,
        user_id: int,
        query: str,
        limit: int = 8,
    ) -> List[Tuple[UserCard, Card]]:
        """Foydalanuvchining shu nomdagi (personaj yoki anime) kartalari — /give uchun."""
        clean = (query or "").strip().replace("%", "").replace("_", "")
        if not clean:
            return []
        like = f"%{clean}%"
        rows = (
            await session.execute(
                select(UserCard, Card)
                .join(Card, Card.id == UserCard.card_id)
                .where(
                    UserCard.user_id == user_id,
                    (Card.character_name.ilike(like)) | (Card.anime_name.ilike(like)),
                )
                .order_by(Card.character_name.asc(), Card.base_value.desc(), Card.id.asc())
                .limit(limit)
            )
        ).all()
        return [(r[0], r[1]) for r in rows]

    @staticmethod
    async def get_card_group_reps(session: AsyncSession, card_id: int) -> Optional[Tuple[int, int]]:
        return await get_group_reps(session, card_id)

    @staticmethod
    async def count_copies(session: AsyncSession, user_id: int, card_id: int) -> int:
        return int(
            (
                await session.execute(
                    select(func.count(UserCard.id)).where(
                        UserCard.user_id == user_id, UserCard.card_id == card_id
                    )
                )
            ).scalar_one()
            or 0
        )

    @staticmethod
    async def get_summary(
        session: AsyncSession,
        user_id: int,
    ) -> Tuple[List[Tuple[str, int]], Dict[str, int], List[Tuple[str, int]]]:
        anime_rows = (
            await session.execute(
                select(func.min(Card.anime_name), func.count(UserCard.id).label("cnt"))
                .join(Card, Card.id == UserCard.card_id)
                .where(UserCard.user_id == user_id)
                .group_by(anime_key())
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
        lower_lookup = {r.lower(): r for r in rarity_map}
        for rarity_name, cnt in rarity_rows:
            raw_clean = str(rarity_name or "").strip()
            canonical = lower_lookup.get(raw_clean.lower())
            if canonical is None:
                resolved = get_rarity_meta(raw_clean).name
                canonical = lower_lookup.get(resolved.lower(), resolved)
            rarity_map[canonical] = rarity_map.get(canonical, 0) + int(cnt)

        # Favorites first, then highest duplicate stacks ("Gojo Satoru × 7")
        fav_rows = (
            await session.execute(
                select(
                    func.min(Card.character_name),
                    func.count(UserCard.id).label("cnt"),
                    func.max(cast(UserCard.is_favorite, Integer)).label("fav"),
                )
                .join(Card, Card.id == UserCard.card_id)
                .where(UserCard.user_id == user_id)
                .group_by(anime_key(), character_key())
                .order_by(func.max(cast(UserCard.is_favorite, Integer)).desc(), func.count(UserCard.id).desc())
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
            aliases = get_rarity_aliases(rarity_filter)
            if aliases:
                stmt = stmt.where(func.lower(Card.rarity).in_(aliases))
            else:
                stmt = stmt.where(func.lower(Card.rarity) == rarity_filter.strip().lower())

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

    @staticmethod
    async def get_filtered_user_cards(
        session: AsyncSession,
        user_id: int,
        filter_tab: str = "all",
        sort_by: str = "newest",
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Tuple[UserCard, Card]], int]:
        stmt = (
            select(UserCard, Card)
            .join(Card, Card.id == UserCard.card_id)
            .where(UserCard.user_id == user_id)
        )
        cnt_stmt = (
            select(func.count(UserCard.id))
            .join(Card, Card.id == UserCard.card_id)
            .where(UserCard.user_id == user_id)
        )
        if filter_tab == "anime":
            cond = Card.media_type == "Anime"
        elif filter_tab == "manga":
            cond = Card.media_type.in_(["Manga", "Manhwa", "Manhua"])
        elif filter_tab == "movie":
            cond = Card.media_type.in_(["Movie", "TV Series"])
        elif filter_tab == "cartoon":
            cond = Card.media_type.in_(["Cartoon", "Comic"])
        elif filter_tab == "other":
            cond = Card.media_type == "Other"
        elif filter_tab in ("event", "limited"):
            cond = Card.is_event_card.is_(True)
        elif filter_tab == "favorites":
            cond = UserCard.is_favorite.is_(True)
        else:
            cond = None

        if cond is not None:
            stmt = stmt.where(cond)
            cnt_stmt = cnt_stmt.where(cond)

        if sort_by == "oldest":
            order_cols = (UserCard.id.asc(),)
        elif sort_by == "rarest":
            order_cols = (Card.base_value.desc(), UserCard.id.desc())
        elif sort_by == "alpha":
            order_cols = (Card.character_name.asc(), UserCard.id.desc())
        elif sort_by == "media":
            order_cols = (Card.media_type.asc(), Card.anime_name.asc(), UserCard.id.desc())
        else:
            order_cols = (UserCard.id.desc(),)

        total = int((await session.execute(cnt_stmt)).scalar_one() or 0)
        offset = (max(1, page) - 1) * page_size
        rows = (await session.execute(stmt.order_by(*order_cols).offset(offset).limit(page_size))).all()
        return [(r[0], r[1]) for r in rows], total

    @staticmethod
    async def reconcile_user_card_locks(session: AsyncSession, user_id: int) -> int:
        """Muddati tugagan transfer/almashuvlarni yopadi va yetim is_locked=True nusxalarni yechadi."""
        from models.base import utcnow
        from models.market import CardListing, PendingTransfer
        from models.v2_features import Auction, CardTrade
        from sqlalchemy import update

        now = utcnow()
        unlocked = 0

        # 1. Muddati o'tgan pending transferlarni yopish
        stale_trfs = (
            await session.execute(
                select(PendingTransfer).where(
                    PendingTransfer.status == "pending",
                    PendingTransfer.expires_at <= now,
                    (PendingTransfer.from_user_id == user_id) | (PendingTransfer.to_user_id == user_id),
                )
            )
        ).scalars().all()
        for trf in stale_trfs:
            trf.status = "expired"
            trf.updated_at = now
            await session.execute(
                update(UserCard)
                .where(UserCard.id == trf.user_card_id)
                .values(is_locked=False, card_state="OWNED", updated_at=now)
            )
            unlocked += 1

        # 2. Muddati o'tgan card trade larni yopish
        stale_trades = (
            await session.execute(
                select(CardTrade).where(
                    CardTrade.status == "pending_pick",
                    CardTrade.expires_at <= now,
                    (CardTrade.initiator_user_id == user_id) | (CardTrade.partner_user_id == user_id),
                )
            )
        ).scalars().all()
        for tr in stale_trades:
            tr.status = "expired"
            for ucid in (tr.initiator_uc_id, tr.partner_uc_id):
                if ucid:
                    await session.execute(
                        update(UserCard)
                        .where(UserCard.id == ucid)
                        .values(is_locked=False, card_state="OWNED", updated_at=now)
                    )
                    unlocked += 1

        # 3. Foydalanuvchining barcha is_locked=True kartalarini tekshirish
        locked_ucs = (
            await session.execute(
                select(UserCard).where(UserCard.user_id == user_id, UserCard.is_locked.is_(True))
            )
        ).scalars().all()
        for uc in locked_ucs:
            has_listing = (
                await session.execute(
                    select(CardListing.id).where(CardListing.user_card_id == uc.id, CardListing.status == "active").limit(1)
                )
            ).scalar_one_or_none()
            has_auction = (
                await session.execute(
                    select(Auction.id).where(Auction.user_card_id == uc.id, Auction.status == "active").limit(1)
                )
            ).scalar_one_or_none()
            has_transfer = (
                await session.execute(
                    select(PendingTransfer.id).where(PendingTransfer.user_card_id == uc.id, PendingTransfer.status == "pending").limit(1)
                )
            ).scalar_one_or_none()
            has_trade = (
                await session.execute(
                    select(CardTrade.id).where(
                        CardTrade.status == "pending_pick",
                        (CardTrade.initiator_uc_id == uc.id) | (CardTrade.partner_uc_id == uc.id),
                    ).limit(1)
                )
            ).scalar_one_or_none()

            if not (has_listing or has_auction or has_transfer or has_trade):
                uc.is_locked = False
                uc.card_state = "OWNED"
                uc.updated_at = now
                unlocked += 1

        if unlocked:
            await session.flush()
        return unlocked

    @staticmethod
    async def get_card_lock_info(session: AsyncSession, user_card_id: int) -> Tuple[str, int, str]:
        """Qaytaradi: (kind, entity_id, human_label). kind in ('market','auction','transfer','trade','none')."""
        from models.market import CardListing, PendingTransfer
        from models.v2_features import Auction, CardTrade

        listing_id = (
            await session.execute(
                select(CardListing.id).where(CardListing.user_card_id == user_card_id, CardListing.status == "active").limit(1)
            )
        ).scalar_one_or_none()
        if listing_id is not None:
            return "market", int(listing_id), f"🔒 Marketda sotuvda (#{listing_id})"

        auc_id = (
            await session.execute(
                select(Auction.id).where(Auction.user_card_id == user_card_id, Auction.status == "active").limit(1)
            )
        ).scalar_one_or_none()
        if auc_id is not None:
            return "auction", int(auc_id), f"🔒 Auksionda band (#{auc_id})"

        trf_id = (
            await session.execute(
                select(PendingTransfer.id).where(PendingTransfer.user_card_id == user_card_id, PendingTransfer.status == "pending").limit(1)
            )
        ).scalar_one_or_none()
        if trf_id is not None:
            return "transfer", int(trf_id), f"🔒 Transfer kutilmoqda (#{trf_id})"

        tr_id = (
            await session.execute(
                select(CardTrade.id).where(
                    CardTrade.status == "pending_pick",
                    (CardTrade.initiator_uc_id == user_card_id) | (CardTrade.partner_uc_id == user_card_id),
                ).limit(1)
            )
        ).scalar_one_or_none()
        if tr_id is not None:
            return "trade", int(tr_id), f"🔒 Almashuv kutilmoqda (#{tr_id})"

        return "none", 0, "🟢 Sotish/Transferga tayyor"

    @staticmethod
    async def unlock_user_card_action(session: AsyncSession, user_id: int, user_card_id: int) -> Tuple[bool, str]:
        """Foydalanuvchi kartasidagi pending transfer/trade yoki yetim bandlikni bekor qilib yechadi."""
        from models.base import utcnow
        from models.market import PendingTransfer
        from models.v2_features import CardTrade
        from sqlalchemy import update

        await CollectionService.reconcile_user_card_locks(session, user_id)
        uc = await session.get(UserCard, user_card_id)
        if uc is None or uc.user_id != user_id:
            return False, "Karta topilmadi."
        if not uc.is_locked:
            return True, "✅ Karta bandligi allaqachon yechildi — endi yuborish/sotish mumkin!"

        kind, eid, _ = await CollectionService.get_card_lock_info(session, uc.id)
        now = utcnow()
        if kind == "transfer":
            trf = await session.get(PendingTransfer, eid)
            if trf and user_id in (trf.from_user_id, trf.to_user_id):
                trf.status = "cancelled"
                trf.updated_at = now
                uc.is_locked = False
                uc.card_state = "OWNED"
                uc.updated_at = now
                await session.flush()
                return True, f"✅ Kutilayotgan transfer (#{eid}) bekor qilindi va karta bo‘shatildi!"
        elif kind == "trade":
            tr = await session.get(CardTrade, eid)
            if tr and user_id in (tr.initiator_user_id, tr.partner_user_id):
                tr.status = "cancelled"
                for ucid in (tr.initiator_uc_id, tr.partner_uc_id):
                    if ucid:
                        await session.execute(
                            update(UserCard)
                            .where(UserCard.id == ucid)
                            .values(is_locked=False, card_state="OWNED", updated_at=now)
                        )
                await session.flush()
                return True, f"✅ Almashuv (#{eid}) bekor qilindi va karta bo‘shatildi!"
        elif kind == "market":
            return False, "🛒 Bu karta Marketda sotuvda turibdi. Uni «Market → Mening kartalarim» bo‘limidan olib tashlang."
        elif kind == "auction":
            return False, "🔨 Bu karta faol Auksionda band. Uni Auksion bo‘limidan bekor qiling (stavka bo‘lmasa)."
        else:
            uc.is_locked = False
            uc.card_state = "OWNED"
            uc.updated_at = now
            await session.flush()
            return True, "✅ Karta bandligi yechildi!"

        return False, "Bu bandlikni yechib bo‘lmadi."


