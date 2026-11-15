import asyncio
import logging

from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeDefault

from app.bot_factory import create_bot, create_dispatcher
from app.scheduler import run_scheduler_loop
from config.settings import get_settings
from database.redis_client import init_lock_manager
from database.seed import seed_initial_data
from database.session import close_db, get_session, init_db
from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)


async def _set_bot_commands(bot) -> None:
    commands = [
        BotCommand(command="menu", description="Asosiy menyu (inline tugmalar)"),
        BotCommand(command="me", description="Shaxsiy profilim"),
        BotCommand(command="cards", description="Kartalar kolleksiyam"),
        BotCommand(command="daily", description="Kunlik bonus"),
        BotCommand(command="market", description="Kartalar bozori"),
        BotCommand(command="sell", description="Karta sotish (/sell CRD-0001 2500)"),
        BotCommand(command="buy", description="Karta sotib olish (/buy id)"),
        BotCommand(command="search", description="Global karta qidiruvi"),
        BotCommand(command="give", description="Karta berish/sotish"),
        BotCommand(command="top", description="Global va Guruh TOP 10"),
        BotCommand(command="stars", description="Telegram Stars coin do‘koni"),
        BotCommand(command="cancel", description="Amaldagi kiritishni bekor qilish"),
        BotCommand(command="help", description="Yordam va buyruqlar"),
    ]
    try:
        await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
        await bot.set_my_commands(commands, scope=BotCommandScopeAllGroupChats())
    except Exception as exc:
        logger.warning("Could not set bot commands: %s", exc)


async def main() -> None:
    settings = get_settings()
    setup_logging(level=settings.log_level, bot_token=settings.bot_token)

    logger.info("Initializing database...")
    await init_db()
    async with get_session() as session:
        await seed_initial_data(session)

    lock_manager = await init_lock_manager(settings.redis_url)
    bot = create_bot(settings)
    dp = create_dispatcher(lock_manager)

    await _set_bot_commands(bot)

    stop_event = asyncio.Event()
    scheduler_task = asyncio.create_task(run_scheduler_loop(bot, stop_event))

    try:
        logger.info("Anime Card Collection bot is starting polling...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        logger.info("Shutting down bot...")
        stop_event.set()
        await scheduler_task
        await lock_manager.close()
        await bot.session.close()
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
