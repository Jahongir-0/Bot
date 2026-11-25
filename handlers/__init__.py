from aiogram import Dispatcher

from admin.handlers import router as admin_router
from admin.card_rules import router as admin_card_rules_router
from admin.quick_add import router as admin_quick_add_router
from admin.v2 import router as admin_v2_router
from handlers.achievements import router as achievements_router
from handlers.auction import router as auction_router
from handlers.bot_store import router as bot_store_router
from handlers.collection import router as collection_router
from handlers.daily import router as daily_router
from handlers.drop import router as drop_router
from handlers.event import router as event_router
from handlers.hub import router as hub_router
from handlers.games import router as games_router
from handlers.market import router as market_router
from handlers.notifications import router as notifications_router
from handlers.offer import router as offer_router
from handlers.pack import router as pack_router
from handlers.premium_emoji_admin import router as premium_emoji_router
from handlers.profile import router as profile_router
from handlers.quests import router as quests_router
from handlers.quiz import router as quiz_router
from handlers.ranking import router as ranking_router
from handlers.search import router as search_router
from handlers.stars import router as stars_router
from handlers.start import router as start_router
from handlers.support import router as support_router
from handlers.trade import router as trade_router
from handlers.transfer import router as transfer_router
from handlers.vip import router as vip_router
from handlers.wishlist import router as wishlist_router


def register_all_routers(dp: Dispatcher) -> None:
    dp.include_router(admin_router)
    dp.include_router(admin_quick_add_router)
    dp.include_router(admin_card_rules_router)
    dp.include_router(admin_v2_router)
    dp.include_router(premium_emoji_router)
    dp.include_router(start_router)
    dp.include_router(hub_router)
    dp.include_router(drop_router)
    dp.include_router(profile_router)
    dp.include_router(collection_router)
    dp.include_router(daily_router)
    dp.include_router(market_router)
    dp.include_router(bot_store_router)
    dp.include_router(transfer_router)
    dp.include_router(offer_router)
    dp.include_router(search_router)
    dp.include_router(ranking_router)
    dp.include_router(quests_router)
    dp.include_router(stars_router)
    dp.include_router(event_router)
    dp.include_router(pack_router)
    dp.include_router(quiz_router)
    dp.include_router(games_router)
    dp.include_router(auction_router)
    dp.include_router(wishlist_router)
    dp.include_router(vip_router)
    dp.include_router(achievements_router)
    dp.include_router(notifications_router)
    dp.include_router(support_router)
    dp.include_router(trade_router)
