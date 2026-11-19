import logging
import re
import traceback
from pathlib import Path
from typing import Optional, Union

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardMarkup, Message, User as TgUser

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


async def send_error_alert_to_super_admins(
    bot: Bot,
    module_name: str,
    exc: BaseException,
    user_tg_id: Optional[int] = None,
    chat_id: Optional[int] = None,
    action: Optional[str] = None,
) -> None:
    """Super Adminlarga xavfsiz xato xabarnomasini yuboradi (hech qanday token/parolsiz)."""
    settings = get_settings()
    if not settings.admin_ids:
        return
    tb = redact_secrets("".join(traceback.format_exception_only(type(exc), exc))).strip()
    short_tb = tb[:350]
    lines = [
        "🔴 <b>BOT ERROR</b>",
        f"📦 Module: <code>{module_name}</code>",
        f"👤 User: <code>{user_tg_id or '—'}</code> | 💬 Chat: <code>{chat_id or '—'}</code>",
    ]
    if action:
        lines.append(f"⚡ Action: <code>{redact_secrets(action)[:120]}</code>")
    lines.append(f"⚠️ Error: <code>{short_tb}</code>")
    msg_text = "\n".join(lines)
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(chat_id=admin_id, text=msg_text)
        except Exception:
            pass


async def edit_or_answer(
    target: Union[Message, CallbackQuery],
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> None:
    if isinstance(target, Message):
        await target.answer(text, reply_markup=reply_markup)
        return

    msg = target.message
    if msg is None:
        await target.answer()
        return

    try:
        if msg.photo:
            await msg.delete()
            await msg.answer(text, reply_markup=reply_markup)
        else:
            await msg.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            pass
        else:
            await msg.answer(text, reply_markup=reply_markup)
    await target.answer()


async def send_card_visual(
    bot: Bot,
    chat_id: int,
    image: str,
    caption: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> Message:
    img_str = (image or "").strip()
    if img_str:
        if img_str.startswith(("http://", "https://")):
            try:
                return await bot.send_photo(
                    chat_id=chat_id,
                    photo=img_str,
                    caption=caption,
                    reply_markup=reply_markup,
                )
            except Exception as exc:
                logger.warning("Failed to send photo URL (%s), falling back to text card: %s", img_str[:40], exc)
        else:
            p = Path(img_str)
            if not p.is_file():
                bot_root_rel = Path(__file__).resolve().parent.parent / img_str.lstrip("/")
                if bot_root_rel.is_file():
                    p = bot_root_rel
            if p.is_file():
                try:
                    return await bot.send_photo(
                        chat_id=chat_id,
                        photo=FSInputFile(p),
                        caption=caption,
                        reply_markup=reply_markup,
                    )
                except Exception as exc:
                    logger.warning("Failed to send local card photo (%s): %s", p.name, exc)
            elif "/" not in img_str and "\\" not in img_str and len(img_str) > 20:
                try:
                    return await bot.send_photo(
                        chat_id=chat_id,
                        photo=img_str,
                        caption=caption,
                        reply_markup=reply_markup,
                    )
                except Exception as exc:
                    logger.warning("Failed to send file_id photo (%s): %s", img_str[:25], exc)

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
