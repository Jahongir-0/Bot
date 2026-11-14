import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage

from config.settings import Settings
from database.redis_client import LockManager
from handlers import register_all_routers
from middlewares import CallbackSecurityMiddleware, GlobalAuthAndBanMiddleware

logger = logging.getLogger(__name__)


def create_bot(settings: Settings) -> Bot:
    if not settings.bot_token:
        raise RuntimeError(
            "BOT_TOKEN is not set! Please configure BOT_TOKEN in your .env file."
        )
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher(lock_manager: LockManager) -> Dispatcher:
    if lock_manager.redis is not None:
        storage = RedisStorage(redis=lock_manager.redis)
        logger.info("Using RedisStorage for FSM states.")
    else:
        storage = MemoryStorage()
        logger.info("Using MemoryStorage for FSM states.")

    dp = Dispatcher(storage=storage)

    # Register global security and user-sync middlewares
    auth_mw = GlobalAuthAndBanMiddleware()
    cb_guard = CallbackSecurityMiddleware()

    dp.message.outer_middleware(auth_mw)
    dp.callback_query.outer_middleware(auth_mw)
    dp.pre_checkout_query.outer_middleware(auth_mw)
    dp.callback_query.middleware(cb_guard)

    register_all_routers(dp)
    return dp
