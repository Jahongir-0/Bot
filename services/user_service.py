from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.base import utcnow
from models.group import Group, GroupMember
from models.transaction import CoinTransaction
from models.user import User
from models.user_card import UserCard


class InsufficientCoinsError(Exception):
    """Raised when a user does not have enough coins for an atomic debit."""


class UserService:
    @staticmethod
    async def upsert_user(
        session: AsyncSession,
        telegram_id: int,
        username: Optional[str],
        first_name: str,
    ) -> User:
        clean_username = username.lstrip("@").lower() if username else None
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        now = utcnow()
        if user is None:
            user = User(
                telegram_id=telegram_id,
                username=clean_username,
                first_name=first_name[:128] if first_name else "Collector",
                coins=100,
                stats={"claims": 0, "trades": 0, "sales": 0},
                last_activity=now,
            )
            session.add(user)
            await session.flush()
        else:
            user.username = clean_username
            if first_name:
                user.first_name = first_name[:128]
            user.last_activity = now
            await session.flush()
        return user

    @staticmethod
    async def upsert_group_and_member(
        session: AsyncSession,
        telegram_chat_id: int,
        title: str,
        username: Optional[str],
        user_id: Optional[int] = None,
    ) -> Group:
        result = await session.execute(
            select(Group).where(Group.telegram_chat_id == telegram_chat_id)
        )
        group = result.scalar_one_or_none()
        if group is None:
            group = Group(
                telegram_chat_id=telegram_chat_id,
                title=title[:255] if title else "Anime Group",
                username=username,
                is_active=True,
            )
            session.add(group)
            await session.flush()
        else:
            if title:
                group.title = title[:255]
            group.username = username
            group.is_active = True
            await session.flush()

        if user_id is not None:
            gm_res = await session.execute(
                select(GroupMember).where(
                    GroupMember.group_id == group.id,
                    GroupMember.user_id == user_id,
                )
            )
            gm = gm_res.scalar_one_or_none()
            if gm is None:
                session.add(
                    GroupMember(
                        group_id=group.id,
                        user_id=user_id,
                        last_seen=utcnow(),
                    )
                )
            else:
                gm.last_seen = utcnow()
            await session.flush()

        return group

    @staticmethod
    async def get_by_telegram_id(session: AsyncSession, telegram_id: int) -> Optional[User]:
        res = await session.execute(select(User).where(User.telegram_id == telegram_id))
        return res.scalar_one_or_none()

    @staticmethod
    async def find_user(session: AsyncSession, query: str) -> Optional[User]:
        q = query.strip()
        if q.isdigit():
            res = await session.execute(select(User).where(User.telegram_id == int(q)))
            return res.scalar_one_or_none()
        uname = q.lstrip("@").lower()
        res = await session.execute(select(User).where(func.lower(User.username) == uname))
        return res.scalar_one_or_none()

    @staticmethod
    async def credit_coins(
        session: AsyncSession,
        user_id: int,
        amount: int,
        tx_type: str,
        description: str,
        reference_id: Optional[str] = None,
        meta: Optional[dict] = None,
    ) -> int:
        if amount <= 0:
            raise ValueError("Credit amount must be positive")
        await session.execute(
            update(User)
            .where(User.id == user_id)
            .values(coins=User.coins + amount, updated_at=utcnow())
        )
        new_balance = (
            await session.execute(select(User.coins).where(User.id == user_id))
        ).scalar_one()
        session.add(
            CoinTransaction(
                user_id=user_id,
                amount=amount,
                balance_after=new_balance,
                tx_type=tx_type,
                description=description[:255],
                reference_id=reference_id,
                meta=meta or {},
            )
        )
        await session.flush()
        return int(new_balance)

    @staticmethod
    async def debit_coins(
        session: AsyncSession,
        user_id: int,
        amount: int,
        tx_type: str,
        description: str,
        reference_id: Optional[str] = None,
        meta: Optional[dict] = None,
    ) -> int:
        if amount <= 0:
            raise ValueError("Debit amount must be positive")
        res = await session.execute(
            update(User)
            .where(User.id == user_id, User.coins >= amount)
            .values(coins=User.coins - amount, updated_at=utcnow())
        )
        if res.rowcount != 1:
            raise InsufficientCoinsError("Coin yetarli emas!")
        new_balance = (
            await session.execute(select(User.coins).where(User.id == user_id))
        ).scalar_one()
        session.add(
            CoinTransaction(
                user_id=user_id,
                amount=-amount,
                balance_after=new_balance,
                tx_type=tx_type,
                description=description[:255],
                reference_id=reference_id,
                meta=meta or {},
            )
        )
        await session.flush()
        return int(new_balance)

    @staticmethod
    async def recompute_card_counts(session: AsyncSession, user_id: int, add_xp: int = 0) -> None:
        total_q = await session.execute(
            select(func.count(UserCard.id)).where(UserCard.user_id == user_id)
        )
        unique_q = await session.execute(
            select(func.count(func.distinct(UserCard.card_id))).where(UserCard.user_id == user_id)
        )
        total = int(total_q.scalar_one() or 0)
        unique = int(unique_q.scalar_one() or 0)

        user = await session.get(User, user_id)
        if user is not None:
            user.total_cards = total
            user.unique_cards = unique
            if add_xp > 0:
                user.xp += add_xp
                user.level = max(1, 1 + (user.xp // 250))
            await session.flush()
