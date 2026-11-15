import logging
from typing import Optional, Union

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, User as TgUser

from config.settings import get_settings

logger = logging.getLogger(__name__)


def is_admin_user(tg_user: Optional[TgUser]) -> bool:
    if tg_user is None:
        return False
    settings = get_settings()
    if tg_user.id in settings.admin_ids:
        return True
    if tg_user.username and tg_user.username.lower() in settings.admin_usernames:
        return True
    return False


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
        try:
            await target.answer()
        except Exception:
            pass
        return

    if msg.photo:
        try:
            await msg.delete()
        except Exception:
            pass
        await msg.answer(text, reply_markup=reply_markup)
    else:
        try:
            await msg.edit_text(text, reply_markup=reply_markup)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                await msg.answer(text, reply_markup=reply_markup)
        except TelegramAPIError:
            await msg.answer(text, reply_markup=reply_markup)

    try:
        await target.answer()
    except Exception:
        pass


async def send_card_visual(
    bot: Bot,
    chat_id: int,
    image: str,
    caption: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> Message:
    if image and (image.startswith("http://") or image.startswith("https://") or len(image) > 20):
        try:
            safe_caption = caption if len(caption) <= 1024 else caption[:1020] + "..."
            return await bot.send_photo(
                chat_id=chat_id,
                photo=image,
                caption=safe_caption,
                reply_markup=reply_markup,
            )
        except Exception as exc:
            logger.warning("Failed to send photo (%s), falling back to text card: %s", image[:40], exc)

    return await bot.send_message(
        chat_id=chat_id,
        text=caption,
        reply_markup=reply_markup,
    )
