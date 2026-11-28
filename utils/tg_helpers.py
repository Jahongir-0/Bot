import asyncio
import logging
import re
import time
import traceback
from typing import Optional, Union

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, LinkPreviewOptions, Message, User as TgUser

from config.settings import get_settings

logger = logging.getLogger(__name__)

_SECRET_PATTERNS = [
    re.compile(r"\d{7,12}:[A-Za-z0-9_-]{25,}"),
    re.compile(r"(postgres(?:ql)?(?:\+\w+)?://[^:\s]+:)([^@\s]+)(@)"),
    re.compile(r"(redis://[^:\s]*:)([^@\s]+)(@)"),
]


def redact_secrets(text: str) -> str:
    if not text:
        return ""
    settings = get_settings()
    out = str(text)
    for sec in (settings.bot_token, settings.stars_payment_secret):
        if sec and len(sec) >= 6:
            out = out.replace(sec, "***SECRET***")
    for pat in _SECRET_PATTERNS:
        if pat.groups >= 3:
            out = pat.sub(r"\1***\3", out)
        else:
            out = pat.sub("***TOKEN***", out)
    return out


class NotCommandFilter(BaseFilter):
    """FSM holatidagi handlerlar '/buyruq' xabarlarini ushlab olmasligi uchun."""

    async def __call__(self, message: Message) -> bool:
        return not (message.text or "").startswith("/")


def is_private_chat(message: Message) -> bool:
    return message.chat.type == "private"


def is_super_admin(tg_user: Optional[TgUser]) -> bool:
    if tg_user is None:
        return False
    settings = get_settings()
    if tg_user.id in settings.admin_ids:
        return True
    if tg_user.username and tg_user.username.lower() in settings.admin_usernames:
        return True
    return False


def is_admin_user(tg_user: Optional[TgUser]) -> bool:
    return is_super_admin(tg_user)


def _uzbek_error_hint(exc: BaseException) -> str:
    name = type(exc).__name__
    msg = str(exc).lower()
    if "constraint" in msg or "integrity" in msg:
        return "Ma’lumotlar bazasi cheklovi (takroriy yoki noto‘g‘ri bog‘lanish)."
    if "column" in msg and ("does not exist" in msg or "no such" in msg):
        return "Ma’lumotlar bazasida ustun yetishmayapti (migratsiya kerak)."
    if "timeout" in msg or isinstance(exc, TimeoutError):
        return "Tarmoq yoki Telegram API javobi kechikdi."
    if "forbidden" in msg or "blocked" in msg:
        return "Foydalanuvchi yoki guruh botni bloklagan / huquq yo‘q."
    if name in ("KeyError", "AttributeError", "TypeError", "NameError"):
        return f"Kod mantiqida xatolik ({name}) — tekshirish talab etiladi."
    return f"Texnik xatolik ({name})."


async def send_error_alert_to_super_admins(
    bot: Bot,
    module_name: str,
    exc: BaseException,
    user_tg_id: Optional[int] = None,
    chat_id: Optional[int] = None,
    action: Optional[str] = None,
) -> None:
    """Super Adminlarga tushunarli o'zbekcha xavfsiz xato hisobotini yuboradi (token/parolsiz)."""
    settings = get_settings()
    if not settings.admin_ids:
        return
    tb = redact_secrets("".join(traceback.format_exception_only(type(exc), exc))).strip()
    short_tb = tb[:320]
    hint = _uzbek_error_hint(exc)
    lines = [
        "🔴 <b>BOTDA XATOLIK ANIQLANDI</b>",
        f"🧠 <b>Sabab:</b> {hint}",
        f"📦 <b>Bo‘lim:</b> <code>{module_name}</code>",
        f"👤 <b>Foydalanuvchi ID:</b> <code>{user_tg_id or '—'}</code> | 💬 <b>Chat:</b> <code>{chat_id or '—'}</code>",
    ]
    if action:
        lines.append(f"⚡ <b>Harakat:</b> <code>{redact_secrets(action)[:110]}</code>")
    lines.append(f"⚠️ <b>Tafsilot:</b> <code>{short_tb}</code>")
    msg_text = "\n".join(lines)
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(chat_id=admin_id, text=msg_text)
        except Exception:
            pass


# Telegram guruhda ~20 xabar/daqiqa limit qo'yadi (flood control). Chat bo'yicha "dam olish" vaqtini eslab qolamiz.
_FLOOD_UNTIL: dict = {}


def note_flood(chat_id: Optional[int], seconds: float) -> None:
    if chat_id is not None:
        _FLOOD_UNTIL[chat_id] = time.monotonic() + max(1.0, float(seconds))


def flood_wait_left(chat_id: Optional[int]) -> int:
    """Shu chatda yana necha soniya kutish kerak (0 = erkin)."""
    until = _FLOOD_UNTIL.get(chat_id, 0.0) if chat_id is not None else 0.0
    left = until - time.monotonic()
    return int(left) + 1 if left > 0 else 0


async def edit_or_answer(
    target: Union[Message, CallbackQuery],
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    disable_web_page_preview: bool = False,
) -> None:
    lpo = LinkPreviewOptions(is_disabled=True) if disable_web_page_preview else None
    if isinstance(target, Message):
        await target.answer(text, reply_markup=reply_markup, link_preview_options=lpo)
        return

    msg = target.message
    if msg is None:
        await target.answer()
        return

    # Chat limitga tushgan bo'lsa, so'rov yubormay, foydalanuvchiga tushunarli xabar beramiz
    left = flood_wait_left(msg.chat.id)
    if left:
        try:
            await target.answer(f"⏳ Guruh juda band. {left} soniyadan keyin qayta bosing.", show_alert=False)
        except Exception:
            pass
        return

    try:
        if msg.photo:
            await msg.delete()
            await msg.answer(text, reply_markup=reply_markup, link_preview_options=lpo)
        else:
            await msg.edit_text(text, reply_markup=reply_markup, link_preview_options=lpo)
    except TelegramRetryAfter as exc:
        note_flood(msg.chat.id, exc.retry_after)
        try:
            await target.answer(f"⏳ Guruh juda band. {int(exc.retry_after)} soniyadan keyin qayta bosing.", show_alert=True)
        except Exception:
            pass
        return
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            pass
        else:
            try:
                await msg.answer(text, reply_markup=reply_markup, link_preview_options=lpo)
            except TelegramRetryAfter as exc2:
                note_flood(msg.chat.id, exc2.retry_after)
                return
    try:
        await target.answer()
    except TelegramBadRequest:
        pass  # callback allaqachon javoblangan bo'lishi mumkin


async def send_card_visual(
    bot: Bot,
    chat_id: int,
    image: str,
    caption: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> Message:
    if image and (image.startswith("http://") or image.startswith("https://") or len(image) > 20):
        try:
            return await bot.send_photo(
                chat_id=chat_id,
                photo=image,
                caption=caption,
                reply_markup=reply_markup,
            )
        except Exception as exc:
            logger.warning("Failed to send photo (%s), falling back to text card: %s", image[:40], exc)

    return await bot.send_message(
        chat_id=chat_id,
        text=caption,
        reply_markup=reply_markup,
    )


def card_image_ref(card) -> str:
    return (getattr(card, "image_file_id", None) or card.image or "").strip()


async def send_card_message(bot: Bot, chat_id: int, card, caption: str, reply_markup=None) -> Message:
    msg = await send_card_visual(bot, chat_id, card_image_ref(card), caption, reply_markup)
    try:
        if msg.photo and not getattr(card, "image_file_id", None):
            from database.session import get_session
            from models.card import Card as _Card

            file_id = msg.photo[-1].file_id
            async with get_session() as s:
                row = await s.get(_Card, card.id)
                if row is not None and not row.image_file_id:
                    row.image_file_id = file_id
                    await s.commit()
            card.image_file_id = file_id
    except Exception as exc:
        logger.debug("file_id cache failed: %s", exc)
    return msg


_BG_TASKS: set = set()


async def safe_delete(bot: Bot, chat_id: int, message_id: Optional[int]) -> bool:
    """Xabarni o'chiradi. Ruxsat yo'q yoki xabar allaqachon yo'q bo'lsa False qaytaradi (xato bermaydi)."""
    if not message_id:
        return False
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except Exception as exc:
        logger.debug("delete_message(%s,%s) bo'lmadi: %s", chat_id, message_id, exc)
        return False


def schedule_delete(bot: Bot, chat_id: int, message_ids, delay: float = 6.0) -> None:
    """Bir necha xabarni `delay` soniyadan keyin o'chiradi (guruh toza turishi uchun)."""
    ids = [m for m in message_ids if m]
    if not ids:
        return

    async def _job() -> None:
        await asyncio.sleep(delay)
        for mid in ids:
            await safe_delete(bot, chat_id, mid)

    task = asyncio.create_task(_job())
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


_BOT_USERNAMES: dict[int, str] = {}


async def get_bot_username(bot: Optional[Bot]) -> Optional[str]:
    if bot is None:
        return None
    cached = _BOT_USERNAMES.get(bot.id)
    if cached:
        return cached
    try:
        me = await bot.get_me()
        if me and me.username:
            _BOT_USERNAMES[bot.id] = me.username
            return me.username
    except Exception:
        pass
    return None


async def append_private_ad(text: str, bot: Optional[Bot], db_user=None) -> str:
    """User panel xabarining eng pastiga faol 'private'/'all' reklamani bezarar qo'shadi."""
    try:
        from database.session import get_session
        from services.community_service import CommunityService

        bot_username = await get_bot_username(bot)
        async with get_session() as session:
            footer = await CommunityService.get_rendered_ad(
                session, bot_username=bot_username, user=db_user, placement="private"
            )
            if footer:
                await session.commit()
                return f"{text}\n\n━━━━━━━━━━━━━━━\n{footer}"
    except Exception as exc:
        logger.debug("append_private_ad skipped: %s", exc)
    return text


async def show_card_with_prompt(target, card, prompt: str, reply_markup=None, title: str = "") -> Optional[Message]:
    """Admin oqimlarida karta tanlangach: AVVAL karta rasmi va ma'lumotlari, KEYIN so'rov matni.

    Eski (ro'yxat) xabari o'chiriladi, o'rniga karta rasmi + to'liq ma'lumot + so'rov (caption) yuboriladi.
    Rasm bo'lmasa oddiy matn bo'lib chiqadi.
    """
    from utils.formatting import format_card_block

    msg = target.message if isinstance(target, CallbackQuery) else target
    bot = msg.bot
    chat_id = msg.chat.id
    block = format_card_block(card, show_status=True)
    caption = (f"{title}\n\n" if title else "") + f"{block}\n\n━━━━━━━━━━━━━━━\n{prompt}"
    if len(caption) > 1020:
        caption = caption[:1017] + "..."
    try:
        await msg.delete()
    except Exception:
        pass
    try:
        return await send_card_message(bot, chat_id, card, caption, reply_markup)
    except Exception as exc:
        logger.debug("show_card_with_prompt rasm yuborilmadi: %s", exc)
        return await bot.send_message(chat_id, caption, reply_markup=reply_markup)
