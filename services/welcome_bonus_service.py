import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import exists, select, update

from database.session import get_session
from models.base import utcnow
from models.transaction import CoinTransaction
from models.user import User
from services.settings_service import SettingsService

logger = logging.getLogger(__name__)

WELCOME_BONUS_COINS = 2_000
TX_NEW = "welcome_bonus"
TX_LEGACY = "welcome_bonus_legacy"
DONE_KEY = "welcome_bonus_legacy_migration_disabled"
BATCH_SIZE = 300
SEND_DELAY = 0.05  # ~20 xabar/soniya (Telegram limitidan past)

NEW_USER_TEXT = (
    "🎁 <b>Xush kelibsiz!</b>\n"
    f"Sizga boshlang‘ich bonus sifatida <b>{WELCOME_BONUS_COINS:,} 🪙</b> berildi!\n\n"
    f"Har bir yangi qo‘shilgan foydalanuvchi <b>{WELCOME_BONUS_COINS:,} coin</b> oladi. Omad!\n\n"
)

LEGACY_TEXT = (
    f"🎁 <b>Sizga {WELCOME_BONUS_COINS:,} 🪙 berildi!</b>\n\n"
    f"Endi har bir yangi qo‘shilgan foydalanuvchi boshidanoq <b>{WELCOME_BONUS_COINS:,} coin</b> oladi.\n"
    f"Avval qo‘shilgan foydalanuvchilar xafa bo‘lmasligi uchun sizga ham bonus sifatida "
    f"<b>{WELCOME_BONUS_COINS:,} 🪙</b> qo‘shib berildi.\n\n"
    "Balansingizni /me orqali ko‘rishingiz mumkin. Yaxshi o‘yin! 🎴"
)


async def _send(bot: Bot, tg_id: int, text: str) -> bool:
    for _ in range(3):
        try:
            await bot.send_message(tg_id, text)
            return True
        except TelegramRetryAfter as exc:
            await asyncio.sleep(float(exc.retry_after) + 0.5)
        except (TelegramForbiddenError, TelegramBadRequest):
            return False  # bot bloklangan / chat yo'q
        except Exception:
            logger.exception("welcome bonus xabari yuborilmadi (%s)", tg_id)
            return False
    return False


class WelcomeBonusService:
    @staticmethod
    async def grant_legacy_users(bot: Bot) -> None:
        """Bot yangilanganda BIR MARTA: avvalgi barcha foydalanuvchilarga 40 000 coin + xabar.

        Har bir user uchun 'welcome_bonus*' tranzaksiyasi tekshiriladi, shuning uchun qayta
        ishga tushirilsa ham hech kim ikki marta olmaydi (jarayon yarim yo'lda to'xtasa, davom etadi).
        """
        try:
            async with get_session() as session:
                if await SettingsService.get_value(session, DONE_KEY, False):
                    return

            granted = sent = 0
            while True:
                async with get_session() as session:
                    rows = (
                        await session.execute(
                            select(User.id, User.telegram_id)
                            .where(
                                ~exists().where(
                                    CoinTransaction.user_id == User.id,
                                    CoinTransaction.tx_type.in_((TX_NEW, TX_LEGACY)),
                                )
                            )
                            .order_by(User.id)
                            .limit(BATCH_SIZE)
                        )
                    ).all()
                    if not rows:
                        break
                    ids = [r.id for r in rows]
                    await session.execute(
                        update(User)
                        .where(User.id.in_(ids))
                        .values(coins=User.coins + WELCOME_BONUS_COINS, updated_at=utcnow())
                        .execution_options(synchronize_session=False)
                    )
                    balances = dict(
                        (await session.execute(select(User.id, User.coins).where(User.id.in_(ids)))).all()
                    )
                    session.add_all(
                        [
                            CoinTransaction(
                                user_id=uid,
                                amount=WELCOME_BONUS_COINS,
                                balance_after=int(balances[uid]),
                                tx_type=TX_LEGACY,
                                description="Yangilanish bonusi (barcha foydalanuvchilarga)",
                                reference_id=f"{TX_LEGACY}:{uid}",
                                meta={},
                            )
                            for uid in ids
                        ]
                    )
                    await session.commit()
                granted += len(rows)

                for r in rows:
                    if await _send(bot, int(r.telegram_id), LEGACY_TEXT):
                        sent += 1
                    await asyncio.sleep(SEND_DELAY)

            async with get_session() as session:
                await SettingsService.set_value(session, DONE_KEY, True)
                await session.commit()
            logger.info("Welcome bonus: %s ta userga berildi, %s tasiga xabar yetdi", granted, sent)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Welcome bonus jarayonida xato")
