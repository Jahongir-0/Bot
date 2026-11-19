import random
from dataclasses import dataclass
from typing import Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.card import Card
from models.user import User
from models.user_card import UserCard
from services.auction_wishlist_service import AuctionWishlistService
from services.gameplay_service import GameplayService
from services.user_service import InsufficientCoinsError, UserService
from utils.constants import PACK_CONFIG


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
    async def open_pack(
        session: AsyncSession,
        user_id: int,
        tier: str,
    ) -> PackOpenResult:
        cfg = PACK_CONFIG.get(tier)
        if cfg is None:
            return PackOpenResult(success=False, message="Paket turi topilmadi.")

        price = int(cfg["price"])
        weights = dict(cfg["weights"])
        user = await session.get(User, user_id)
        if user is None:
            return PackOpenResult(success=False, message="Foydalanuvchi topilmadi.")

        # Avval bazada mos kartalar borligini tekshiramiz (coin yechishdan oldin)
        pool_rarities = list(weights.keys())
        chosen_rarity = PackService._choose_weighted_rarity(weights)

        card = (
            await session.execute(
                select(Card)
                .where(
                    Card.is_available.is_(True),
                    Card.is_deleted.is_(False),
                    Card.is_event_card.is_(False),
                    Card.rarity == chosen_rarity,
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
                        Card.rarity.in_(pool_rarities),
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
