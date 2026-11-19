from datetime import timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.admin import AdminAction, Ban
from models.base import utcnow
from models.card import Card
from models.event import Event
from models.group import Group, GroupMember
from models.market import CardListing, Offer
from models.user import User
from models.user_card import UserCard
from services.user_service import InsufficientCoinsError, UserService
from utils.grouping import anime_key, character_key, get_anime_key_of, get_character_key_of, get_group_reps


class AdminService:
    @staticmethod
    async def log_action(
        session: AsyncSession,
        admin_telegram_id: int,
        action_type: str,
        target_type: str,
        target_id: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> None:
        session.add(
            AdminAction(
                admin_telegram_id=admin_telegram_id,
                action_type=action_type,
                target_type=target_type,
                target_id=target_id,
                details=details or {},
            )
        )
        await session.flush()

    @staticmethod
    async def get_global_stats(session: AsyncSession) -> Dict[str, int]:
        total_users = int((await session.execute(select(func.count(User.id)))).scalar_one() or 0)
        banned_users = int(
            (await session.execute(select(func.count(User.id)).where(User.is_banned.is_(True)))).scalar_one() or 0
        )
        total_groups = int((await session.execute(select(func.count(Group.id)))).scalar_one() or 0)
        total_cards = int(
            (await session.execute(select(func.count(Card.id)).where(Card.is_deleted.is_(False)))).scalar_one() or 0
        )
        total_copies = int((await session.execute(select(func.count(UserCard.id)))).scalar_one() or 0)
        active_listings = int(
            (
                await session.execute(
                    select(func.count(CardListing.id)).where(CardListing.status == "active")
                )
            ).scalar_one()
            or 0
        )
        total_coins = int((await session.execute(select(func.sum(User.coins)))).scalar_one() or 0)

        return {
            "total_users": total_users,
            "banned_users": banned_users,
            "total_groups": total_groups,
            "total_cards": total_cards,
            "total_copies": total_copies,
            "active_listings": active_listings,
            "total_coins": total_coins,
        }

    @staticmethod
    async def get_user_groups(session: AsyncSession, user_id: int) -> List[str]:
        rows = (
            await session.execute(
                select(Group.title)
                .join(GroupMember, GroupMember.group_id == Group.id)
                .where(GroupMember.user_id == user_id)
            )
        ).scalars().all()
        return list(rows)

    @staticmethod
    async def adjust_user_coins(
        session: AsyncSession,
        admin_telegram_id: int,
        user_id: int,
        delta: int,
    ) -> Tuple[bool, str, int]:
        if delta == 0:
            return False, "Miqdor 0 bo‘lishi mumkin emas.", 0
        if delta > 0:
            new_bal = await UserService.credit_coins(
                session=session,
                user_id=user_id,
                amount=delta,
                tx_type="admin_grant",
                description=f"Admin +{delta} coins",
                reference_id=f"admin:{admin_telegram_id}",
            )
        else:
            try:
                new_bal = await UserService.debit_coins(
                    session=session,
                    user_id=user_id,
                    amount=abs(delta),
                    tx_type="admin_deduct",
                    description=f"Admin {delta} coins",
                    reference_id=f"admin:{admin_telegram_id}",
                )
            except InsufficientCoinsError:
                return False, "Foydalanuvchida buncha coin yo‘q (manfiy balansga ruxsat yo‘q).", 0

        await AdminService.log_action(
            session,
            admin_telegram_id,
            "adjust_coins",
            "user",
            str(user_id),
            {"delta": delta, "balance_after": new_bal},
        )
        return True, f"Balans yangilandi: {new_bal:,} 🪙", new_bal

    @staticmethod
    async def give_card_to_user(
        session: AsyncSession,
        admin_telegram_id: int,
        user_id: int,
        card_id: int,
    ) -> Optional[Card]:
        card = await session.get(Card, card_id)
        user = await session.get(User, user_id)
        if card is None or user is None:
            return None
        if await UserService.owns_card(session, user_id, card_id):
            return None  # bir user bir xil kartadan faqat bitta nusxaga ega bo'ladi
        session.add(
            UserCard(
                user_id=user.id,
                card_id=card.id,
                acquired_via="admin_grant",
            )
        )
        await UserService.recompute_card_counts(session, user.id, add_xp=10)
        await AdminService.log_action(
            session,
            admin_telegram_id,
            "give_card",
            "user",
            str(user.id),
            {"card_id": card.id, "card_code": card.card_code},
        )
        await session.flush()
        return card

    @staticmethod
    async def remove_card_from_user(
        session: AsyncSession,
        admin_telegram_id: int,
        user_id: int,
        card_id: int,
    ) -> bool:
        uc = (
            await session.execute(
                select(UserCard)
                .where(
                    UserCard.user_id == user_id,
                    UserCard.card_id == card_id,
                    UserCard.is_locked.is_(False),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if uc is None:
            return False

        # Bu nusxaga yuborilgan faol takliflar escrow coin ushlab turadi: o'chirishdan oldin qaytaramiz
        # (aks holda FK CASCADE takliflarni jimgina o'chirib, xaridor coinlari yo'qolardi).
        now = utcnow()
        pending = (
            await session.execute(
                select(Offer).where(Offer.user_card_id == uc.id, Offer.status == "pending")
            )
        ).scalars().all()
        for off in pending:
            off.status = "cancelled"
            off.resolved_at = now
            await UserService.credit_coins(
                session=session,
                user_id=off.buyer_id,
                amount=off.amount,
                tx_type="offer_refund",
                description="Card removed by admin - offer refunded",
                reference_id=f"offer:{off.id}",
            )

        await session.delete(uc)
        await session.flush()
        await UserService.recompute_card_counts(session, user_id)
        await AdminService.log_action(
            session,
            admin_telegram_id,
            "remove_card",
            "user",
            str(user_id),
            {"card_id": card_id, "user_card_id": uc.id},
        )
        return True

    @staticmethod
    async def set_global_ban(
        session: AsyncSession,
        admin_telegram_id: int,
        user_id: int,
        ban: bool,
        reason: str = "Admin qarori",
    ) -> Optional[User]:
        user = await session.get(User, user_id)
        if user is None:
            return None
        user.is_banned = ban
        now = utcnow()
        if ban:
            session.add(
                Ban(
                    user_id=user.id,
                    banned_by_telegram_id=admin_telegram_id,
                    reason=reason,
                    is_active=True,
                )
            )
        else:
            active_bans = (
                await session.execute(
                    select(Ban).where(Ban.user_id == user.id, Ban.is_active.is_(True))
                )
            ).scalars().all()
            for b in active_bans:
                b.is_active = False
                b.unbanned_at = now

        await AdminService.log_action(
            session,
            admin_telegram_id,
            "ban_user" if ban else "unban_user",
            "user",
            str(user.id),
            {"reason": reason},
        )
        await session.flush()
        return user

    @staticmethod
    async def create_event(
        session: AsyncSession,
        admin_telegram_id: int,
        name: str,
        duration_hours: int,
        coin_multiplier: float = 1.0,
        boosted_rarity: Optional[str] = None,
        rarity_weight_multiplier: float = 1.0,
    ) -> Event:
        now = utcnow()
        ev = Event(
            name=name[:128],
            description=f"{coin_multiplier}x coins" + (f" | {boosted_rarity} x{rarity_weight_multiplier}" if boosted_rarity else ""),
            coin_multiplier=max(1.0, coin_multiplier),
            boosted_rarity=boosted_rarity,
            rarity_weight_multiplier=max(1.0, rarity_weight_multiplier),
            starts_at=now,
            ends_at=now + timedelta(hours=max(1, duration_hours)),
            is_active=True,
        )
        session.add(ev)
        await session.flush()
        await AdminService.log_action(
            session,
            admin_telegram_id,
            "create_event",
            "event",
            str(ev.id),
            {"name": ev.name, "hours": duration_hours},
        )
        return ev

    @staticmethod
    async def filter_cards(
        session: AsyncSession,
        filter_mode: str = "all",
        query: str = "",
        page: int = 1,
        page_size: int = 5,
    ) -> Tuple[List[Card], int]:
        stmt = select(Card).where(Card.is_deleted.is_(False))
        cnt_stmt = select(func.count(Card.id)).where(Card.is_deleted.is_(False))

        if filter_mode == "available":
            stmt = stmt.where(Card.is_available.is_(True))
            cnt_stmt = cnt_stmt.where(Card.is_available.is_(True))
        elif filter_mode == "disabled":
            stmt = stmt.where(Card.is_available.is_(False))
            cnt_stmt = cnt_stmt.where(Card.is_available.is_(False))
        elif filter_mode == "rarity" and query:
            stmt = stmt.where(func.lower(Card.rarity) == query.lower())
            cnt_stmt = cnt_stmt.where(func.lower(Card.rarity) == query.lower())
        elif filter_mode == "search" and query:
            pat = f"%{query}%"
            stmt = stmt.where(Card.character_name.ilike(pat) | Card.anime_name.ilike(pat))
            cnt_stmt = cnt_stmt.where(Card.character_name.ilike(pat) | Card.anime_name.ilike(pat))

        total = int((await session.execute(cnt_stmt)).scalar_one() or 0)
        offset = (max(1, page) - 1) * page_size
        cards = list(
            (await session.execute(stmt.order_by(Card.id.desc()).offset(offset).limit(page_size)))
            .scalars()
            .all()
        )
        return cards, total

    # ------------------------------------------------------------------
    # Guruhlangan ro'yxat: anime bo'limi -> personaj -> kartalar (variantlar)
    # ------------------------------------------------------------------
    @staticmethod
    def _availability_clause(filter_mode: str):
        if filter_mode == "available":
            return Card.is_available.is_(True)
        if filter_mode == "disabled":
            return Card.is_available.is_(False)
        return None

    @staticmethod
    async def get_anime_sections(
        session: AsyncSession,
        filter_mode: str = "all",
        page: int = 1,
        page_size: int = 6,
    ) -> Tuple[List[dict], int]:
        stmt = (
            select(
                func.min(Card.id),
                func.min(Card.anime_name),
                func.count(func.distinct(character_key())),
                func.count(func.distinct(Card.id)),
                func.count(UserCard.id),
            )
            .outerjoin(UserCard, UserCard.card_id == Card.id)
            .where(Card.is_deleted.is_(False))
            .group_by(anime_key())
            .order_by(func.min(Card.anime_name).asc())
        )
        clause = AdminService._availability_clause(filter_mode)
        if clause is not None:
            stmt = stmt.where(clause)
        rows = (await session.execute(stmt)).all()
        total = len(rows)
        start = (max(1, page) - 1) * page_size
        items = [
            {"rep_id": int(r[0]), "name": str(r[1]), "characters": int(r[2]), "cards": int(r[3]), "copies": int(r[4])}
            for r in rows[start : start + page_size]
        ]
        return items, total

    @staticmethod
    async def get_anime_characters(
        session: AsyncSession,
        anime_rep_id: int,
        filter_mode: str = "all",
        page: int = 1,
        page_size: int = 6,
    ) -> Optional[Tuple[str, List[dict], int]]:
        a_key = await get_anime_key_of(session, anime_rep_id)
        if a_key is None:
            return None
        stmt = (
            select(
                func.min(Card.id),
                func.min(Card.character_name),
                func.min(Card.anime_name),
                func.count(func.distinct(Card.id)),
                func.count(UserCard.id),
                func.count(func.distinct(UserCard.user_id)),
            )
            .outerjoin(UserCard, UserCard.card_id == Card.id)
            .where(Card.is_deleted.is_(False), anime_key() == a_key)
            .group_by(character_key())
            .order_by(func.min(Card.character_name).asc())
        )
        clause = AdminService._availability_clause(filter_mode)
        if clause is not None:
            stmt = stmt.where(clause)
        rows = (await session.execute(stmt)).all()
        if not rows:
            return None
        total = len(rows)
        start = (max(1, page) - 1) * page_size
        items = [
            {
                "rep_id": int(r[0]),
                "name": str(r[1]),
                "cards": int(r[3]),
                "copies": int(r[4]),
                "owners": int(r[5]),
            }
            for r in rows[start : start + page_size]
        ]
        return str(rows[0][2]), items, total

    @staticmethod
    async def get_character_cards(
        session: AsyncSession,
        char_rep_id: int,
        filter_mode: str = "all",
    ) -> Optional[Tuple[str, str, List[Tuple[Card, int, int]], int]]:
        """Personajning barcha kartalari: [(Card, nusxalar, egalar)], anime_rep_id bilan."""
        a_key = await get_anime_key_of(session, char_rep_id)
        c_key = await get_character_key_of(session, char_rep_id)
        if a_key is None or c_key is None:
            return None
        stmt = (
            select(Card, func.count(UserCard.id), func.count(func.distinct(UserCard.user_id)))
            .outerjoin(UserCard, UserCard.card_id == Card.id)
            .where(Card.is_deleted.is_(False), anime_key() == a_key, character_key() == c_key)
            .group_by(Card.id)
            .order_by(Card.id.asc())
        )
        clause = AdminService._availability_clause(filter_mode)
        if clause is not None:
            stmt = stmt.where(clause)
        rows = (await session.execute(stmt)).all()
        if not rows:
            return None
        reps = await get_group_reps(session, char_rep_id)
        anime_rep = reps[0] if reps else char_rep_id
        cards = [(r[0], int(r[1]), int(r[2])) for r in rows]
        return cards[0][0].character_name, cards[0][0].anime_name, cards, anime_rep

    @staticmethod
    async def get_card_group_reps(session: AsyncSession, card_id: int) -> Optional[Tuple[int, int]]:
        return await get_group_reps(session, card_id)

    @staticmethod
    async def get_card_ownership_and_drops(
        session: AsyncSession,
        card_id: int,
    ) -> Tuple[List[Tuple[str, int]], int]:
        """Returns ([(owner_display_name, qty), ...], total_drops_sent)."""
        from models.schedule import ActiveDrop

        owner_rows = (
            await session.execute(
                select(User.username, User.first_name, User.telegram_id, func.count(UserCard.id))
                .join(UserCard, UserCard.user_id == User.id)
                .where(UserCard.card_id == card_id)
                .group_by(User.id, User.username, User.first_name, User.telegram_id)
                .order_by(func.count(UserCard.id).desc())
            )
        ).all()
        owners = []
        for uname, fname, tg_id, cnt in owner_rows:
            label = f"@{uname}" if uname else (fname or str(tg_id))
            owners.append((label, int(cnt)))

        drops_sent = int(
            (
                await session.execute(
                    select(func.count(ActiveDrop.id)).where(ActiveDrop.card_id == card_id)
                )
            ).scalar_one()
            or 0
        )
        return owners, drops_sent

    @staticmethod
    async def get_users_paginated(
        session: AsyncSession,
        filter_mode: str = "all",
        page: int = 1,
        page_size: int = 6,
    ) -> Tuple[List[User], int]:
        stmt = select(User)
        cnt_stmt = select(func.count(User.id))
        if filter_mode == "active":
            stmt = stmt.where(User.is_banned.is_(False))
            cnt_stmt = cnt_stmt.where(User.is_banned.is_(False))
        elif filter_mode == "banned":
            stmt = stmt.where(User.is_banned.is_(True))
            cnt_stmt = cnt_stmt.where(User.is_banned.is_(True))

        total = int((await session.execute(cnt_stmt)).scalar_one() or 0)
        offset = (max(1, page) - 1) * page_size
        users = list(
            (await session.execute(stmt.order_by(User.last_activity.desc()).offset(offset).limit(page_size)))
            .scalars()
            .all()
        )
        return users, total

    @staticmethod
    async def get_user_cards_for_admin(
        session: AsyncSession,
        user_id: int,
        limit: int = 12,
    ) -> List[Tuple[Card, int]]:
        rows = (
            await session.execute(
                select(Card, func.count(UserCard.id))
                .join(UserCard, UserCard.card_id == Card.id)
                .where(UserCard.user_id == user_id, UserCard.is_locked.is_(False))
                .group_by(Card.id)
                .order_by(func.count(UserCard.id).desc())
                .limit(limit)
            )
        ).all()
        return [(r[0], int(r[1])) for r in rows]

    @staticmethod
    async def get_upcoming_schedules_all_groups(
        session: AsyncSession,
        limit: int = 15,
    ) -> List[tuple]:
        from models.schedule import DropSchedule

        rows = (
            await session.execute(
                select(DropSchedule, Group, Card)
                .join(Group, Group.id == DropSchedule.group_id)
                .outerjoin(Card, Card.id == DropSchedule.card_id)
                .where(DropSchedule.status == "pending")
                .order_by(DropSchedule.scheduled_at.asc())
                .limit(limit)
            )
        ).all()
        return list(rows)

    @staticmethod
    async def get_v2_cards_by_tab(
        session: AsyncSession,
        tab: str = "all",
        query: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Card], int]:
        from models.schedule import ActiveDrop

        if tab == "deleted":
            conds = [Card.is_deleted.is_(True)]
        else:
            conds = [Card.is_deleted.is_(False)]

        if tab == "sent_group":
            conds.append(Card.dropped_count > 0)
        elif tab == "not_sent":
            conds.append(Card.dropped_count == 0)
            conds.append(Card.is_event_card.is_(False))
        elif tab == "anime":
            conds.append(Card.media_type == "Anime")
        elif tab == "manga":
            conds.append(Card.media_type.in_(["Manga", "Manhwa", "Manhua"]))
        elif tab == "movie":
            conds.append(Card.media_type.in_(["Movie", "TV Series"]))
        elif tab == "cartoon":
            conds.append(Card.media_type.in_(["Cartoon", "Comic"]))
        elif tab == "event":
            conds.append(Card.is_event_card.is_(True))
        elif tab == "limited":
            conds.append(Card.limited_copies.is_not(None))
        elif tab == "other":
            conds.append(Card.media_type == "Other")

        if query.strip():
            q = query.strip()
            if q.isdigit():
                conds.append(Card.id == int(q))
            else:
                pat = f"%{q}%"
                conds.append(
                    Card.character_name.ilike(pat) | Card.anime_name.ilike(pat) | Card.card_code.ilike(pat)
                )

        total = int((await session.execute(select(func.count(Card.id)).where(*conds))).scalar_one() or 0)
        offset = (max(1, page) - 1) * page_size
        if tab == "sent_group":
            last_drop_sq = (
                select(ActiveDrop.card_id.label("cid"), func.max(ActiveDrop.created_at).label("last_dt"))
                .group_by(ActiveDrop.card_id)
                .subquery()
            )
            stmt = (
                select(Card)
                .outerjoin(last_drop_sq, last_drop_sq.c.cid == Card.id)
                .where(*conds)
                .order_by(last_drop_sq.c.last_dt.desc().nullslast(), Card.id.desc())
                .offset(offset)
                .limit(page_size)
            )
        else:
            stmt = select(Card).where(*conds).order_by(Card.id.desc()).offset(offset).limit(page_size)

        cards = list((await session.execute(stmt)).scalars().all())
        return cards, total

    @staticmethod
    async def get_v2_media_titles(
        session: AsyncSession,
        media_group: str = "anime",
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Tuple[int, str, int]], int]:
        """Media nomlari card soni bo'yicha kamayish tartibida: [(rep_id, title, count)]."""
        conds = [Card.is_deleted.is_(False)]
        if media_group == "manga":
            conds.append(Card.media_type.in_(["Manga", "Manhwa", "Manhua"]))
        elif media_group == "anime":
            conds.append(Card.media_type == "Anime")
        elif media_group == "movie":
            conds.append(Card.media_type.in_(["Movie", "TV Series"]))
        elif media_group == "cartoon":
            conds.append(Card.media_type.in_(["Cartoon", "Comic"]))

        rows = (
            await session.execute(
                select(func.min(Card.id), func.min(Card.anime_name), func.count(Card.id))
                .where(*conds)
                .group_by(anime_key())
                .order_by(func.count(Card.id).desc(), func.min(Card.anime_name).asc())
            )
        ).all()
        total = len(rows)
        start = (max(1, page) - 1) * page_size
        return [(int(r[0]), str(r[1]), int(r[2])) for r in rows[start : start + page_size]], total

    @staticmethod
    async def get_v2_cards_by_media_title(
        session: AsyncSession,
        media_group: str,
        rep_card_id: int,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[str, List[Card], int]:
        a_key = await get_anime_key_of(session, rep_card_id)
        if a_key is None:
            return "", [], 0
        conds = [Card.is_deleted.is_(False), anime_key() == a_key]
        if media_group == "manga":
            conds.append(Card.media_type.in_(["Manga", "Manhwa", "Manhua"]))
        elif media_group == "anime":
            conds.append(Card.media_type == "Anime")
        elif media_group == "movie":
            conds.append(Card.media_type.in_(["Movie", "TV Series"]))
        elif media_group == "cartoon":
            conds.append(Card.media_type.in_(["Cartoon", "Comic"]))

        total = int((await session.execute(select(func.count(Card.id)).where(*conds))).scalar_one() or 0)
        offset = (max(1, page) - 1) * page_size
        cards = list(
            (await session.execute(select(Card).where(*conds).order_by(Card.id.desc()).offset(offset).limit(page_size)))
            .scalars()
            .all()
        )
        title = cards[0].anime_name if cards else ""
        return title, cards, total

    @staticmethod
    async def get_undropped_cards_paginated(
        session: AsyncSession,
        page: int = 1,
        page_size: int = 20,
        query: str = "",
    ) -> Tuple[List[Card], int]:
        conds = [
            Card.is_deleted.is_(False),
            Card.is_available.is_(True),
            Card.is_event_card.is_(False),
            Card.dropped_count == 0,
        ]
        if query.strip():
            pat = f"%{query.strip()}%"
            conds.append(Card.character_name.ilike(pat) | Card.anime_name.ilike(pat) | Card.card_code.ilike(pat))
        total = int((await session.execute(select(func.count(Card.id)).where(*conds))).scalar_one() or 0)
        offset = (max(1, page) - 1) * page_size
        cards = list(
            (await session.execute(select(Card).where(*conds).order_by(Card.id.desc()).offset(offset).limit(page_size)))
            .scalars()
            .all()
        )
        return cards, total

    @staticmethod
    async def search_users_for_admin(
        session: AsyncSession,
        query: str,
        limit: int = 20,
    ) -> List[User]:
        q = query.strip().lstrip("@")
        if not q:
            return []
        conds = [User.username.ilike(f"%{q}%"), User.first_name.ilike(f"%{q}%")]
        if q.isdigit():
            conds.append(User.telegram_id == int(q))
            conds.append(User.id == int(q))
        from sqlalchemy import or_

        return list(
            (
                await session.execute(
                    select(User).where(or_(*conds)).order_by(User.last_activity.desc()).limit(limit)
                )
            )
            .scalars()
            .all()
        )

    @staticmethod
    async def get_group_analytics(session: AsyncSession, group_id: int) -> dict:
        from models.schedule import ActiveDrop

        active_members = int(
            (await session.execute(select(func.count(GroupMember.id)).where(GroupMember.group_id == group_id))).scalar_one() or 0
        )
        total_drops = int(
            (await session.execute(select(func.count(ActiveDrop.id)).where(ActiveDrop.group_id == group_id))).scalar_one() or 0
        )
        claimed_drops = int(
            (
                await session.execute(
                    select(func.count(ActiveDrop.id)).where(ActiveDrop.group_id == group_id, ActiveDrop.claimed_by_user_id.is_not(None))
                )
            ).scalar_one()
            or 0
        )
        top_users = (
            await session.execute(
                select(User, func.count(ActiveDrop.id).label("cnt"))
                .join(ActiveDrop, ActiveDrop.claimed_by_user_id == User.id)
                .where(ActiveDrop.group_id == group_id)
                .group_by(User.id)
                .order_by(func.count(ActiveDrop.id).desc())
                .limit(10)
            )
        ).all()
        return {
            "active_members": active_members,
            "total_drops": total_drops,
            "claimed_drops": claimed_drops,
            "top_users": [(r[0], int(r[1])) for r in top_users],
        }

    @staticmethod
    async def get_v2_activity_report(session: AsyncSession) -> dict:
        from models.schedule import ActiveDrop
        from models.transaction import DailyReward
        from models.v2_features import GameSession, Quiz, SecurityIncident, SupportTicket

        now = utcnow()
        day_ago = now - timedelta(hours=24)
        today = now.date()

        active_users_24h = int(
            (await session.execute(select(func.count(User.id)).where(User.last_activity >= day_ago))).scalar_one() or 0
        )
        active_groups = int(
            (await session.execute(select(func.count(Group.id)).where(Group.is_active.is_(True)))).scalar_one() or 0
        )
        games_total = int((await session.execute(select(func.count(GameSession.id)))).scalar_one() or 0)
        games_today = int(
            (await session.execute(select(func.count(GameSession.id)).where(GameSession.created_date == today))).scalar_one() or 0
        )
        daily_claims_today = int(
            (await session.execute(select(func.count(DailyReward.id)).where(DailyReward.reward_date == today))).scalar_one() or 0
        )
        quizzes_total = int((await session.execute(select(func.count(Quiz.id)))).scalar_one() or 0)
        open_tickets = int(
            (await session.execute(select(func.count(SupportTicket.id)).where(SupportTicket.status == "open"))).scalar_one() or 0
        )
        unresolved_sec = int(
            (await session.execute(select(func.count(SecurityIncident.id)).where(SecurityIncident.is_resolved.is_(False)))).scalar_one() or 0
        )
        top_groups = (
            await session.execute(
                select(Group.title, func.count(ActiveDrop.id).label("c"))
                .join(ActiveDrop, ActiveDrop.group_id == Group.id)
                .where(ActiveDrop.claimed_by_user_id.is_not(None))
                .group_by(Group.id, Group.title)
                .order_by(func.count(ActiveDrop.id).desc())
                .limit(5)
            )
        ).all()
        top_users = (
            await session.execute(
                select(User).order_by(User.last_activity.desc(), User.total_cards.desc()).limit(5)
            )
        ).scalars().all()
        return {
            "active_users_24h": active_users_24h,
            "active_groups": active_groups,
            "games_total": games_total,
            "games_today": games_today,
            "daily_claims_today": daily_claims_today,
            "quizzes_total": quizzes_total,
            "open_tickets": open_tickets,
            "unresolved_sec": unresolved_sec,
            "top_groups": [(str(r[0]), int(r[1])) for r in top_groups],
            "top_users": list(top_users),
        }



