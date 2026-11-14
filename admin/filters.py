from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

from utils.tg_helpers import is_admin_user


class IsAdminFilter(BaseFilter):
    """Server-side admin authorization filter using numeric ADMIN_IDS (and optional usernames)."""

    async def __call__(self, event: TelegramObject) -> bool:
        tg_user = getattr(event, "from_user", None)
        return is_admin_user(tg_user)
