"""Admin paneli (asosiy): barcha bo‘limlar alohida modullarda, bu yerda bitta routerga yig‘iladi."""
from aiogram import Router

from admin.handlers import (
    home,
    cards,
    groups,
    users,
    bot_settings,
    stars,
    events,
)
from admin.handlers.common import AdminStates

router = Router(name="admin")
router.include_router(home.router)
router.include_router(cards.router)
router.include_router(groups.router)
router.include_router(users.router)
router.include_router(bot_settings.router)
router.include_router(stars.router)
router.include_router(events.router)

__all__ = ["AdminStates", "router"]
