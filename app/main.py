import asyncio
import logging

from aiogram.types import BotCommand

from app.bot_factory import create_bot, create_dispatcher
from app.scheduler import run_scheduler_loop
from config.settings import get_settings
from database.redis_client import init_lock_manager
from database.seed import seed_initial_data
from database.session import close_db, get_session, init_db
from services.admin_service import AdminService
from services.gameplay_service import GameplayService
from services.welcome_bonus_service import WelcomeBonusService
from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)


async def _set_bot_commands(bot) -> None:
    commands = [
        BotCommand(command="menu", description="Asosiy menyu (inline tugmalar)"),
        BotCommand(command="me", description="Shaxsiy profilim va VIP status"),
        BotCommand(command="cards", description="Kartalar kolleksiyam (filter + showcase)"),
        BotCommand(command="daily", description="Kunlik bonus"),
        BotCommand(command="pack", description="Karta paketlari (Bronze, Silver, Gold)"),
        BotCommand(command="quiz", description="Kunlik anime quizlar"),
        BotCommand(command="game", description="Mini o‘yinlar (Coin Flip, Dice, Lucky Card)"),
        BotCommand(command="auction", description="Jonli karta auksionlari"),
        BotCommand(command="wishlist", description="Sevimli kartalar ro‘yxati"),
        BotCommand(command="vip", description="VIP obunalar va ustunliklar"),
        BotCommand(command="trade", description="Karta ↔ Karta almashtirish"),
        BotCommand(command="event", description="Limitli event kartalari"),
        BotCommand(command="market", description="Kartalar bozori"),
        BotCommand(command="search", description="Global karta qidiruvi"),
        BotCommand(command="top", description="Global, Guruh va Season TOP"),
        BotCommand(command="notif", description="Bildirishnomalar"),
        BotCommand(command="support", description="Support va muammo yuborish"),
        BotCommand(command="help", description="Yordam va buyruqlar"),
    ]
    await bot.set_my_commands(commands)


async def main() -> None:
    settings = get_settings()
    setup_logging(level=settings.log_level, bot_token=settings.bot_token)
    settings.validate_runtime()

    logger.info("Initializing database...")
    await init_db(auto_schema_upgrade=settings.auto_schema_upgrade)
    async with get_session() as session:
        await seed_initial_data(session)
        await GameplayService.sync_runtime_and_legacy_ranks(session)
        unbanned = await AdminService.ensure_admins_unbanned(session)
        if unbanned:
            logger.info("Avtomatik bandan ochilgan adminlar soni: %s", unbanned)
        await session.commit()

    lock_manager = await init_lock_manager(settings.redis_url)
    bot = create_bot(settings)
    dp = create_dispatcher(lock_manager)

    await _set_bot_commands(bot)

    stop_event = asyncio.Event()
    scheduler_task = asyncio.create_task(run_scheduler_loop(bot, stop_event))
    # Bir martalik: avvalgi barcha foydalanuvchilarga 40 000 coin + xabar (fonda ishlaydi)
    bonus_task = asyncio.create_task(WelcomeBonusService.grant_legacy_users(bot))

    try:
        logger.info("Anime Card Collection bot is starting polling...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        logger.info("Shutting down bot...")
        stop_event.set()
        bonus_task.cancel()
        await asyncio.gather(bonus_task, return_exceptions=True)
        await scheduler_task
        await lock_manager.close()
        await bot.session.close()
        await close_db()


if __name__ == "__main__":
    try:  # uvloop mavjud bo'lsa (Linux/macOS) event loop ancha tezroq ishlaydi
        import uvloop

        uvloop.install()
    except Exception:
        pass
    asyncio.run(main())
