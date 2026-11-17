import logging
import time
from typing import Any, Awaitable, Callable, Dict, Tuple

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, PreCheckoutQuery, TelegramObject
from sqlalchemy import select

from database.redis_client import get_lock_manager
from database.session import get_session
from models.base import ensure_utc, utcnow
from models.group import Group
from services.community_service import CommunityService
from services.settings_service import SettingsService
from services.user_service import UserService
from utils.callback_data import SupCb
from utils.tg_helpers import is_super_admin

logger = logging.getLogger(__name__)

_GROUP_TOUCH_TTL = 600.0
_group_touched: Dict[Tuple[int, int], float] = {}


class GlobalAuthAndBanMiddleware(BaseMiddleware):
    """
    1. Syncs User across groups & private chats.
    2. Registers/updates Group & GroupMember.
    3. Enforces perm/temp user bans & group bans (allowing appeal/support).
    4. Enforces Maintenance Mode (Super Admin bypasses).
    5. Enforces Forced Channel subscription in private chat (VIP/Admin bypasses).
    6. Gentle anti-spam throttling.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        tg_user = getattr(event, "from_user", None)
        if tg_user is None or tg_user.is_bot:
            return None

        is_payment = isinstance(event, Message) and event.successful_payment is not None
        is_support_cb = isinstance(event, CallbackQuery) and (event.data or "").startswith("sup:")

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

        bot = data.get("bot")

        async with get_session() as session:
            user = await UserService.upsert_user(
                session=session,
                telegram_id=tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name or "Collector",
            )

            # Muddati o'tgan temp ban avtomatik yechiladi
            now_dt = utcnow()
            if user.is_banned and user.ban_until and ensure_utc(user.ban_until) <= now_dt:
                user.is_banned = False
                user.ban_until = None
                user.ban_reason = None

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

                if group is not None and group.is_banned and group.ban_until and ensure_utc(group.ban_until) <= now_dt:
                    group.is_banned = False
                    group.ban_until = None
                    group.ban_reason = None

            is_adm, admin_perms = await CommunityService.get_admin_permissions(
                session, tg_user.id, tg_user.username
            )
            maint = await SettingsService.get_value(session, "maintenance", {"enabled": False})

            # Forced subscription faqat shaxsiy chatda (support/start/payment bundan mustasno)
            missing_channels = []
            if (
                chat is not None
                and chat.type == "private"
                and not is_payment
                and not is_adm
                and not is_support_cb
                and not user.is_banned
                and bot is not None
            ):
                txt = (event.text or "") if isinstance(event, Message) else ""
                if not txt.startswith("/start"):
                    ok_sub, missing_channels = await CommunityService.check_user_subscriptions(
                        bot, session, user, tg_user.id
                    )

            await session.commit()

            # Maintenance check
            if isinstance(maint, dict) and maint.get("enabled") and not is_adm and not is_payment:
                msg_txt = str(maint.get("message") or "🔧 Botda texnik ishlar olib borilmoqda.")
                if isinstance(event, CallbackQuery):
                    await event.answer(msg_txt[:190], show_alert=True)
                elif isinstance(event, Message) and event.chat.type == "private":
                    await event.answer(msg_txt)
                return None

            # Group ban check
            if group is not None and getattr(group, "is_banned", False) and not is_adm:
                return None

            # User ban check (allow support/appeal callbacks)
            if user.is_banned and not is_payment and not is_support_cb:
                reason = user.ban_reason or "Qoidalarni buzish"
                exp = f"\n⏳ Muddat: {ensure_utc(user.ban_until):%d.%m.%Y %H:%M} UTC" if user.ban_until else "\n⛔ Muddat: Umrbod"
                ban_msg = f"🚫 <b>Siz botdan bloklangansiz!</b>\n📌 Sabab: {reason}{exp}"
                kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="💬 Admin bilan bog‘lanish",
                                callback_data=SupCb(owner_id=tg_user.id, act="new").pack(),
                            )
                        ]
                    ]
                )
                if isinstance(event, CallbackQuery):
                    await event.answer("🚫 Siz bloklangansiz.", show_alert=True)
                elif isinstance(event, Message) and event.chat.type == "private":
                    await event.answer(ban_msg, reply_markup=kb)
                elif isinstance(event, PreCheckoutQuery):
                    await event.answer(ok=False, error_message="Account banned.")
                return None

            # Forced subscription prompt
            if missing_channels and isinstance(event, (Message, CallbackQuery)):
                rows = []
                for ch in missing_channels:
                    url = ch.invite_link or (f"https://t.me/{ch.username}" if ch.username else None)
                    if url:
                        rows.append([InlineKeyboardButton(text=f"📢 {ch.title}", url=url)])
                rows.append(
                    [
                        InlineKeyboardButton(
                            text="✅ Tekshirish",
                            callback_data=SupCb(owner_id=tg_user.id, act="check_sub").pack(),
                        )
                    ]
                )
                markup = InlineKeyboardMarkup(inline_keyboard=rows)
                txt_sub = "📢 <b>Botdan foydalanish uchun quyidagi kanallarga obuna bo‘ling:</b>"
                if isinstance(event, CallbackQuery):
                    await event.message.answer(txt_sub, reply_markup=markup)
                    await event.answer()
                else:
                    await event.answer(txt_sub, reply_markup=markup)
                return None

            data["db_user"] = user
            data["db_group"] = group
            data["is_bot_admin"] = is_adm or is_super_admin(tg_user)
            data["admin_perms"] = admin_perms

        return await handler(event, data)
