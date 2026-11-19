from datetime import timedelta
from typing import Optional, Tuple

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.base import ensure_utc, utcnow
from models.card import Card
from models.group import Group, GroupMember
from models.transaction import CoinTransaction
from models.user import User
from models.user_card import UserCard
from services.settings_service import SettingsService
from utils.constants import Rarity


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
            try:
                # SAVEPOINT: ikki parallel birinchi xabar bir vaqtda kelsa, unique xatosi butun sessiyani buzmasin
                async with session.begin_nested():
                    session.add(user)
                    await session.flush()
            except IntegrityError:
                result = await session.execute(select(User).where(User.telegram_id == telegram_id))
                user = result.scalar_one()
            return user

        new_first = first_name[:128] if first_name else user.first_name
        changed = user.username != clean_username or user.first_name != new_first
        stale = user.last_activity is None or (now - ensure_utc(user.last_activity)) > timedelta(seconds=60)
        if changed or stale:
            # Har bir xabarda yozmaymiz: faqat ma'lumot o'zgarganda yoki 60 soniyadan keyin (DB yuklamasini kamaytiradi)
            user.username = clean_username
            user.first_name = new_first
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
    async def owns_card(session: AsyncSession, user_id: int, card_id: int) -> bool:
        """Bir user bir xil kartadan faqat bitta nusxa olishi mumkin."""
        row = (
            await session.execute(
                select(UserCard.id).where(UserCard.user_id == user_id, UserCard.card_id == card_id).limit(1)
            )
        ).first()
        return row is not None

    @staticmethod
    async def get_by_telegram_id(session: AsyncSession, telegram_id: int) -> Optional[User]:
        res = await session.execute(select(User).where(User.telegram_id == telegram_id))
        return res.scalar_one_or_none()

    @staticmethod
    async def find_user(session: AsyncSession, query: str) -> Optional[User]:
        q = query.strip()
        if not q:
            return None
        if q.isdigit():
            res = await session.execute(select(User).where(User.telegram_id == int(q)))
            return res.scalar_one_or_none()
        uname = q.lstrip("@").lower()
        res = await session.execute(select(User).where(func.lower(User.username) == uname))
        user = res.scalar_one_or_none()
        if user is not None:
            return user
        res = await session.execute(
            select(User)
            .where(or_(User.username.ilike(f"%{uname}%"), User.first_name.ilike(f"%{q}%")))
            .order_by(User.total_cards.desc(), User.id.asc())
            .limit(1)
        )
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

    @staticmethod
    async def apply_referral(
        session: AsyncSession,
        new_user: User,
        referrer_tg_id: int,
    ) -> Optional[Tuple[User, int, int]]:
        """
        Faqat botga birinchi marta kirgan foydalanuvchi uchun referal mukofotini qo'llaydi.
        Har ikki tomonga admin belgilagan miqdorda coin beradi.
        """
        if referrer_tg_id == new_user.telegram_id:
            return None
        if new_user.referred_by_id is not None or new_user.total_cards > 0:
            return None
        if new_user.created_at and (utcnow() - ensure_utc(new_user.created_at)) > timedelta(minutes=3):
            return None

        has_tx = (
            await session.execute(
                select(CoinTransaction.id).where(CoinTransaction.user_id == new_user.id).limit(1)
            )
        ).first()
        if has_tx is not None:
            return None

        referrer = await UserService.get_by_telegram_id(session, referrer_tg_id)
        if referrer is None or referrer.is_banned or referrer.id == new_user.id:
            return None

        upd = await session.execute(
            update(User)
            .where(User.id == new_user.id, User.referred_by_id.is_(None))
            .values(referred_by_id=referrer.id, updated_at=utcnow())
        )
        if upd.rowcount != 1:
            return None

        await session.execute(
            update(User)
            .where(User.id == referrer.id)
            .values(referral_count=User.referral_count + 1, updated_at=utcnow())
        )
        new_user.referred_by_id = referrer.id
        referrer.referral_count = int(referrer.referral_count or 0) + 1

        cfg = await SettingsService.get_referral_config(session)
        inviter_coins = int(cfg["inviter_coins"])
        invitee_coins = int(cfg["invitee_coins"])

        if inviter_coins > 0:
            await UserService.credit_coins(
                session=session,
                user_id=referrer.id,
                amount=inviter_coins,
                tx_type="referral_bonus",
                description=f"Referral: {new_user.display_name}",
                reference_id=f"ref_in:{new_user.id}",
            )
        if invitee_coins > 0:
            await UserService.credit_coins(
                session=session,
                user_id=new_user.id,
                amount=invitee_coins,
                tx_type="referral_welcome",
                description=f"Referred by {referrer.display_name}",
                reference_id=f"ref_welcome:{referrer.id}",
            )
        await session.flush()
        return referrer, inviter_coins, invitee_coins

    @staticmethod
    async def reward_group_adder(
        session: AsyncSession,
        group: Group,
        adder: User,
    ) -> Optional[Tuple[int, Optional[Card]]]:
        """
        Botni guruhga birinchi marta qo'shgan foydalanuvchiga coin va hali guruhga tashlanmagan Epic karta beradi.
        Berilgan Epic karta guruhlarga tashlanish ro'yxatidan chiqariladi (drop_limit=0, drop_weight=0).
        """
        upd = await session.execute(
            update(Group)
            .where(Group.id == group.id, Group.add_reward_claimed.is_(False))
            .values(add_reward_claimed=True, added_by_user_id=adder.id, updated_at=utcnow())
        )
        if upd.rowcount != 1:
            return None
        group.add_reward_claimed = True
        group.added_by_user_id = adder.id

        coins = await SettingsService.get_group_add_reward_coins(session)
        if coins > 0:
            await UserService.credit_coins(
                session=session,
                user_id=adder.id,
                amount=coins,
                tx_type="group_add_reward",
                description=f"Group added: {group.title[:80]}",
                reference_id=f"grp_add:{group.id}",
            )

        from utils.constants import get_rank_name_by_order

        order3_name = get_rank_name_by_order(3, Rarity.EPIC.value)
        has_owner = exists().where(UserCard.card_id == Card.id)
        epic_card = (
            await session.execute(
                select(Card)
                .where(
                    func.lower(Card.rarity) == order3_name.lower(),
                    Card.is_available.is_(True),
                    Card.is_deleted.is_(False),
                    Card.is_event_card.is_(False),
                    Card.dropped_count == 0,
                    Card.drop_limit > 0,
                    ~has_owner,
                )
                .order_by(func.random())
                .limit(1)
            )
        ).scalar_one_or_none()

        if epic_card is not None:
            upd_card = await session.execute(
                update(Card)
                .where(Card.id == epic_card.id, Card.dropped_count == 0, Card.drop_limit > 0)
                .values(drop_limit=0, drop_weight=0, updated_at=utcnow())
            )
            if upd_card.rowcount == 1:
                epic_card.drop_limit = 0
                epic_card.drop_weight = 0
                session.add(
                    UserCard(
                        user_id=adder.id,
                        card_id=epic_card.id,
                        acquired_via="group_reward",
                        acquired_group_id=group.id,
                    )
                )
                await session.flush()
                await UserService.recompute_card_counts(session, adder.id, add_xp=50)
            else:
                epic_card = None

        await session.flush()
        return coins, epic_card

