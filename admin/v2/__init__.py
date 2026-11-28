"""Admin paneli (v2): barcha bo‘limlar alohida modullarda, bu yerda bitta routerga yig‘iladi."""
from aiogram import Router

from admin.v2 import (
    hub,
    cards,
    json_import,
    groups_drop,
    rbac,
    channels,
    ranks,
    quiz,
    games,
    auction,
    season,
    reklama,
    broadcast,
    report,
    history,
    groups_hub,
    backup,
    support,
    economy,
)

router = Router(name="admin_v2")
router.include_router(hub.router)
router.include_router(cards.router)
router.include_router(json_import.router)
router.include_router(groups_drop.router)
router.include_router(rbac.router)
router.include_router(channels.router)
router.include_router(ranks.router)
router.include_router(quiz.router)
router.include_router(games.router)
router.include_router(auction.router)
router.include_router(season.router)
router.include_router(reklama.router)
router.include_router(broadcast.router)
router.include_router(report.router)
router.include_router(history.router)
router.include_router(groups_hub.router)
router.include_router(backup.router)
router.include_router(support.router)
router.include_router(economy.router)

__all__ = ["router"]
