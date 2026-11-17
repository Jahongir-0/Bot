import logging
from typing import Optional, Union

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, User as TgUser

from config.settings import get_settings

logger = logging.getLogger(__name__)


class NotCommandFilter(BaseFilter):
    """FSM holatidagi handlerlar '/buyruq' xabarlarini ushlab olmasligi uchun (masalan /me, /cancel)."""

    async def __call__(self, message: Message) -> bool:
        return not (message.text or "").startswith("/")


def is_private_chat(message: Message) -> bool:
    return message.chat.type == "private"


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
    """Telegram file_id keshi bo'lsa shuni (tez), bo'lmasa URL/file_id ni qaytaradi."""
    return (getattr(card, "image_file_id", None) or card.image or "").strip()


async def send_card_message(bot: Bot, chat_id: int, card, caption: str, reply_markup=None) -> Message:
    """Karta rasmini yuboradi va birinchi muvaffaqiyatli yuborishdan keyin file_id ni keshlab qo'yadi
    (keyingi yuborishlar URL'ni qayta yuklamaydi, ancha tezroq)."""
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
    except Exception as exc:  # kesh xatosi yuborishni buzmasin
        logger.debug("file_id cache failed: %s", exc)
    return msg

