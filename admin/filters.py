from typing import Optional, Set

from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject, User as TgUser

from utils.constants import ADMIN_PERMISSION_SECTIONS
from utils.tg_helpers import is_admin_user, is_super_admin


ALL_ADMIN_SECTIONS: Set[str] = {k for k, _ in ADMIN_PERMISSION_SECTIONS}


class IsAdminFilter(BaseFilter):
    """Server-side RBAC admin authorization filter (env Super Admins + DB AdminRole sub-admins)."""

    async def __call__(self, event: TelegramObject, is_bot_admin: bool = False) -> bool:
        if is_bot_admin:
            return True
        tg_user = getattr(event, "from_user", None)
        return is_admin_user(tg_user)


def resolve_admin_perms(tg_user: Optional[TgUser], admin_perms: Optional[Set[str]]) -> Set[str]:
    if is_super_admin(tg_user):
        return set(ALL_ADMIN_SECTIONS)
    return set(admin_perms or set())


def has_admin_perm(tg_user: Optional[TgUser], admin_perms: Optional[Set[str]], section: str) -> bool:
    if is_super_admin(tg_user):
        return True
    return section in (admin_perms or set())
