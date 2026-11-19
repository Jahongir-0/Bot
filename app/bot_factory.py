import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import ErrorEvent

from config.settings import Settings
from database.redis_client import LockManager
from handlers import register_all_routers
from middlewares import CallbackSecurityMiddleware, GlobalAuthAndBanMiddleware
from utils.tg_helpers import send_error_alert_to_super_admins

logger = logging.getLogger(__name__)


def create_bot(settings: Settings) -> Bot:
    if not settings.bot_token:
        raise RuntimeError(
            "BOT_TOKEN is not set! Please configure BOT_TOKEN in your .env file."
        )
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:  # guruh tozalash: muvaffaqiyatsiz bo'lsa bot baribir ishlayveradi
        from middlewares.group_cleanup import AVAILABLE, GroupCleanupRequestMiddleware

        if AVAILABLE and GroupCleanupRequestMiddleware is not None:
            bot.session.middleware(GroupCleanupRequestMiddleware())
    except Exception as exc:  # pragma: no cover
        logger.warning("Group cleanup request middleware ulanmadi: %s", exc)
    try:  # premium emoji: xato bo'lsa bot oddiy emoji bilan ishlayveradi
        from middlewares.premium_emoji import PremiumEmojiRequestMiddleware

        bot.session.middleware(PremiumEmojiRequestMiddleware())
    except Exception as exc:  # pragma: no cover
        logger.warning("Premium emoji middleware ulanmadi: %s", exc)
    return bot


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
    try:
        from middlewares.group_cleanup import AVAILABLE as _CLEAN_OK, GroupCleanupMiddleware

        if _CLEAN_OK:
            dp.message.outer_middleware(GroupCleanupMiddleware())
    except Exception as exc:  # pragma: no cover
        logger.warning("Group cleanup middleware ulanmadi: %s", exc)
    dp.callback_query.outer_middleware(auth_mw)
    dp.pre_checkout_query.outer_middleware(auth_mw)
    dp.callback_query.middleware(cb_guard)

    @dp.errors()
    async def _global_error_handler(event: ErrorEvent) -> bool:
        logger.exception("Unhandled update error: %s", event.exception)
        upd = event.update
        tg_user = None
        chat_id = None
        action = None
        if upd.message:
            tg_user = upd.message.from_user
            chat_id = upd.message.chat.id
            action = (upd.message.text or "")[:80]
        elif upd.callback_query:
            tg_user = upd.callback_query.from_user
            chat_id = upd.callback_query.message.chat.id if upd.callback_query.message else None
            action = upd.callback_query.data
        bot = event.update.bot
        if bot is not None:
            await send_error_alert_to_super_admins(
                bot,
                module_name="dispatcher",
                exc=event.exception,
                user_tg_id=tg_user.id if tg_user else None,
                chat_id=chat_id,
                action=action,
            )
        return True

    register_all_routers(dp)
    return dp
