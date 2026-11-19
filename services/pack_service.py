import random
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.redis_client import get_lock_manager
from models.card import Card
from models.user import User
from models.user_card import UserCard
from models.v2_features import GameSession
from services.auction_wishlist_service import AuctionWishlistService
from services.gameplay_service import GameplayService
from services.user_service import InsufficientCoinsError, UserService
from utils.constants import get_pack_configs, sync_runtime_ranks


@dataclass
class PackOpenResult:
    success: bool
    message: str
    card: Optional[Card] = None
    is_duplicate: bool = False
    cashback_coins: int = 0
    price_paid: int = 0


class PackService:
    @staticmethod
    def _choose_weighted_rarity(weights: dict[str, int]) -> str:
        rarities = list(weights.keys())
        probs = [max(1, int(weights[r])) for r in rarities]
        return random.choices(rarities, weights=probs, k=1)[0]

    @staticmethod
    async def get_daily_pack_status(session: AsyncSession, user_id: int) -> Tuple[bool, int, int]:
        """(yoqilganmi, bugun nechta paket ochgan, kunlik limit)"""
        cfg = await GameplayService.get_game_config(session)
        enabled = bool(cfg.get("pack_enabled", True))
        daily_limit = max(0, int(cfg.get("pack_daily_limit", 1)))
        tz = ZoneInfo(get_settings().timezone)
        today = datetime.now(tz).date()
        used = int(
            (
                await session.execute(
                    select(func.count(GameSession.id)).where(
                        GameSession.user_id == user_id,
                        GameSession.game_type == "pack",
                        GameSession.created_date == today,
                    )
                )
            ).scalar_one()
            or 0
        )
        return enabled, used, daily_limit

    @staticmethod
    async def open_pack(
        session: AsyncSession,
        user_id: int,
        tier: str,
    ) -> PackOpenResult:
        sync_runtime_ranks(await GameplayService.get_rank_definitions(session))
        pack_configs = get_pack_configs()
        cfg = pack_configs.get(tier)
        if cfg is None:
            return PackOpenResult(success=False, message="Paket turi topilmadi.")

        price = int(cfg["price"])
        weights = dict(cfg["weights"])
        user = await session.get(User, user_id)
        if user is None:
            return PackOpenResult(success=False, message="Foydalanuvchi topilmadi.")

        async with get_lock_manager().lock(f"pack:{user_id}", ttl_ms=5000):
            enabled, used_today, daily_limit = await PackService.get_daily_pack_status(session, user_id)
            if not enabled or daily_limit <= 0:
                return PackOpenResult(
                    success=False,
                    message="🎁 Karta paketlari bo‘limi hozircha o‘chirilgan.",
                )
            if used_today >= daily_limit:
                return PackOpenResult(
                    success=False,
                    message=(
                        f"⏳ <b>Kunlik paket limiti tugadi!</b> ({used_today}/{daily_limit})\n"
                        f"Kuniga eng ko‘pi bilan <b>{daily_limit}</b> ta paket ochishingiz mumkin. Ertaga qayting!"
                    ),
                )

            # Avval bazada mos kartalar borligini tekshiramiz (coin yechishdan oldin)
            pool_rarities_lower = [str(r).strip().lower() for r in weights.keys()]
            chosen_rarity = PackService._choose_weighted_rarity(weights)

            card = (
                await session.execute(
                    select(Card)
                    .where(
                        Card.is_available.is_(True),
                        Card.is_deleted.is_(False),
                        Card.is_event_card.is_(False),
                        func.lower(Card.rarity) == chosen_rarity.lower(),
                    )
                    .order_by(func.random())
                    .limit(1)
                )
            ).scalar_one_or_none()

            if card is None:
                card = (
                    await session.execute(
                        select(Card)
                        .where(
                            Card.is_available.is_(True),
                            Card.is_deleted.is_(False),
                            Card.is_event_card.is_(False),
                            func.lower(Card.rarity).in_(pool_rarities_lower),
                        )
                        .order_by(func.random())
                        .limit(1)
                    )
                ).scalar_one_or_none()

            if card is None:
                card = (
                    await session.execute(
                        select(Card)
                        .where(
                            Card.is_available.is_(True),
                            Card.is_deleted.is_(False),
                            Card.is_event_card.is_(False),
                        )
                        .order_by(func.random())
                        .limit(1)
                    )
                ).scalar_one_or_none()

            if card is None:
                return PackOpenResult(
                    success=False,
                    message="📭 Hozircha paket uchun mavjud kartalar yo‘q.",
                )

            try:
                await UserService.debit_coins(
                    session,
                    user_id,
                    price,
                    "pack_open",
                    f"Opened {cfg['name']} ({tier})",
                )
            except InsufficientCoinsError:
                return PackOpenResult(
                    success=False,
                    message=f"🪙 Coinlaringiz yetarli emas! {cfg['name']} narxi: {price:,} 🪙.",
                )

            tz = ZoneInfo(get_settings().timezone)
            today = datetime.now(tz).date()
            session.add(
                GameSession(
                    user_id=user_id,
                    game_type="pack",
                    roll_value=1 if tier == "bronze" else (2 if tier == "silver" else 3),
                    reward_coins=0,
                    reward_xp=10,
                    created_date=today,
                )
            )

            already_owned = await UserService.owns_card(session, user_id, card.id)
            if already_owned:
                cashback = max(50, price // 2)
                await UserService.credit_coins(
                    session,
                    user_id,
                    cashback,
                    "pack_duplicate_cashback",
                    f"Duplicate card in {cfg['name']}: {card.character_name}",
                )
                user.xp = int(user.xp or 0) + 10
                user.level = max(1, 1 + (user.xp // 250))
                await session.flush()
                return PackOpenResult(
                    success=True,
                    message=(
                        f"♻️ <b>Dublikat karta tushdi!</b>\n"
                        f"Sizda <b>{card.character_name}</b> allaqachon borligi uchun "
                        f"<b>+{cashback:,} 🪙</b> keshbek va ⚡ +10 XP qaytarildi!"
                    ),
                    card=card,
                    is_duplicate=True,
                    cashback_coins=cashback,
                    price_paid=price,
                )

            try:
                async with session.begin_nested():
                    uc = UserCard(
                        user_id=user_id,
                        card_id=card.id,
                        acquired_via=f"pack_{tier}",
                        card_state="OWNED",
                    )
                    session.add(uc)
                    await session.flush()
                    uc.serial_number = 10000 + uc.id
            except IntegrityError:
                cashback = max(50, price // 2)
                await UserService.credit_coins(
                    session,
                    user_id,
                    cashback,
                    "pack_duplicate_cashback",
                    f"Duplicate card in {cfg['name']}: {card.character_name}",
                )
                await session.flush()
                return PackOpenResult(
                    success=True,
                    message=f"♻️ Dublikat karta uchun +{cashback:,} 🪙 keshbek qaytarildi!",
                    card=card,
                    is_duplicate=True,
                    cashback_coins=cashback,
                    price_paid=price,
                )

            await UserService.recompute_card_counts(session, user_id, add_xp=25)
            await GameplayService.add_season_points(session, user_id, points=15, xp=25, card_claimed=True)
            await AuctionWishlistService.record_history(
                session,
                card.id,
                "pack_opened",
                user_card_id=uc.id,
                to_user_id=user_id,
                price=price,
                details={"tier": tier},
            )
            await session.flush()
            return PackOpenResult(
                success=True,
                message=f"🎉 <b>{cfg['name']}</b> dan yangi karta chiqdi! ⚡ +25 XP",
                card=card,
                is_duplicate=False,
                cashback_coins=0,
                price_paid=price,
            )
