from datetime import timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.admin import AdminAction, Ban
from models.base import utcnow
from models.card import Card
from models.event import Event
from models.group import Group, GroupMember
from models.market import CardListing
from models.user import User
from models.user_card import UserCard
from services.user_service import InsufficientCoinsError, UserService


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

