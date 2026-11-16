import logging
import time
from typing import Any, Awaitable, Callable, Dict, Tuple

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery, TelegramObject
from sqlalchemy import select

from database.redis_client import get_lock_manager
from database.session import get_session
from models.group import Group
from services.user_service import UserService

logger = logging.getLogger(__name__)

# (chat_id, user_id) -> oxirgi yozilgan vaqt. Har bir guruh xabarida DB'ga yozmaslik uchun.
_GROUP_TOUCH_TTL = 600.0
_group_touched: Dict[Tuple[int, int], float] = {}


class GlobalAuthAndBanMiddleware(BaseMiddleware):
    """
    1. Syncs global User account across all groups & private chats.
    2. Registers/updates Group & GroupMember when interacting inside a group.
    3. Enforces global ban across ALL groups and private chats.
    4. Applies gentle anti-spam rate limiting.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        tg_user = getattr(event, "from_user", None)
        # Botlar va anonim adminlar (GroupAnonymousBot) uchun akkaunt yo'q: handlerlar db_user kutadi,
        # shuning uchun bunday xabarlarni jimgina o'tkazib yuboramiz.
        if tg_user is None or tg_user.is_bot:
            return None

        # To'lov xabari hech qachon yo'qolmasin: throttle ham, ban ham uni to'xtatmaydi
        is_payment = isinstance(event, Message) and event.successful_payment is not None

        if isinstance(event, (Message, CallbackQuery)) and not is_payment:
            limiter = get_lock_manager()
            cd_key = f"spam:{tg_user.id}:{'cb' if isinstance(event, CallbackQuery) else 'msg'}"
            allowed = await limiter.check_and_set_cooldown(cd_key, seconds=0.35)
            if not allowed:
                if isinstance(event, CallbackQuery):
                    try:
                        await event.answer("⏳ Biroz kuting...", show_alert=False)
                    except Exception:
                        pass
                return None

        chat = None
        if isinstance(event, Message):
            chat = event.chat
        elif isinstance(event, CallbackQuery) and event.message is not None:
            chat = event.message.chat

        async with get_session() as session:
            user = await UserService.upsert_user(
                session=session,
                telegram_id=tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name or "Collector",
            )

            group = None
            if chat is not None and chat.type in ("group", "supergroup"):
                touch_key = (chat.id, tg_user.id)
                now = time.monotonic()
                if now - _group_touched.get(touch_key, 0.0) > _GROUP_TOUCH_TTL:
                    group = await UserService.upsert_group_and_member(
                        session=session,
                        telegram_chat_id=chat.id,
                        title=chat.title or "Anime Group",
                        username=chat.username,
                        user_id=user.id,
                    )
                    if len(_group_touched) > 50_000:
                        _group_touched.clear()
                    _group_touched[touch_key] = now
                else:
                    group = (
                        await session.execute(select(Group).where(Group.telegram_chat_id == chat.id))
                    ).scalar_one_or_none()

            await session.commit()

            if user.is_banned and not is_payment:
                logger.info("Blocked globally banned user %s", tg_user.id)
                if isinstance(event, CallbackQuery):
                    await event.answer("🚫 Siz botdan global ban qilingansiz.", show_alert=True)
                elif isinstance(event, Message) and event.chat.type == "private":
                    await event.answer("🚫 Siz botdan global ban qilingansiz.")
                elif isinstance(event, PreCheckoutQuery):
                    await event.answer(ok=False, error_message="Account banned.")
                return None

            data["db_user"] = user
            data["db_group"] = group

        return await handler(event, data)
