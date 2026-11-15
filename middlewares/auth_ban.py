import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery, TelegramObject

from database.redis_client import get_lock_manager
from database.session import get_session
from services.user_service import UserService

logger = logging.getLogger(__name__)


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
        if tg_user is None:
            return None
        if tg_user.is_bot:
            # Handle Anonymous Group Admins (GroupAnonymousBot: 1087968824) cleanly instead of crashing on missing db_user
            if (
                tg_user.id == 1087968824
                and isinstance(event, Message)
                and (event.text or "").startswith("/")
            ):
                try:
                    await event.reply(
                        "⚠️ Guruhda <b>Anonim admin</b> rejimida botning shaxsiy buyruqlari ishlamaydi. "
                        "Buyruqlardan foydalanish uchun anonim rejimni vaqtincha o‘chiring."
                    )
                except Exception:
                    pass
            return None

        # Anti-spam throttling (do not throttle slash commands, payments, or migration service messages)
        is_command = isinstance(event, Message) and (event.text or "").startswith("/")
        if isinstance(event, CallbackQuery) or (isinstance(event, Message) and not is_command and event.migrate_to_chat_id is None):
            limiter = get_lock_manager()
            cd_key = f"spam:{tg_user.id}:{'cb' if isinstance(event, CallbackQuery) else 'msg'}"
            allowed = await limiter.check_and_set_cooldown(cd_key, seconds=0.25)
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
                group = await UserService.upsert_group_and_member(
                    session=session,
                    telegram_chat_id=chat.id,
                    title=chat.title or "Anime Group",
                    username=chat.username,
                    user_id=user.id,
                )

            await session.commit()

            if user.is_banned:
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
