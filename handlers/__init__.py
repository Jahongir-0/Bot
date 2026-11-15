from aiogram import Dispatcher

from admin.handlers import router as admin_router
from handlers.collection import router as collection_router
from handlers.daily import router as daily_router
from handlers.drop import router as drop_router
from handlers.market import router as market_router
from handlers.offer import router as offer_router
from handlers.profile import router as profile_router
from handlers.ranking import router as ranking_router
from handlers.search import router as search_router
from handlers.stars import router as stars_router
from handlers.start import router as start_router
from handlers.transfer import router as transfer_router


def register_all_routers(dp: Dispatcher) -> None:
    dp.include_router(admin_router)
    dp.include_router(start_router)
    dp.include_router(drop_router)
    dp.include_router(profile_router)
    dp.include_router(collection_router)
    dp.include_router(daily_router)
    dp.include_router(market_router)
    dp.include_router(transfer_router)
    dp.include_router(offer_router)
    dp.include_router(search_router)
    dp.include_router(ranking_router)
    dp.include_router(stars_router)
