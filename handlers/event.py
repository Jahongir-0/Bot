import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, LabeledPrice, Message

from app.event_announcer import refresh_event_message
from config.settings import get_settings
from database.session import get_session
from keyboards.event import event_buy_kb, event_list_footer_kb
from models.user import User
from services.event_service import EventService
from utils.callback_data import EvtCb, NavCb
from utils.formatting import esc, format_event_message
from utils.i18n import get_lang, tr
from utils.tg_helpers import send_card_message

logger = logging.getLogger(__name__)
router = Router(name="event")

MAX_EVENTS_SHOWN = 5


async def _send_active_events(bot: Bot, chat_id: int, owner_tg_id: int, lang: str = "uz") -> None:
    async with get_session() as session:
        events = await EventService.get_active_events(session)

    if not events:
        await bot.send_message(
            chat_id,
            tr(lang, "evt_none"),
            reply_markup=event_list_footer_kb(owner_tg_id, lang=lang),
        )
        return

    tz = get_settings().timezone
    for event, card in events[:MAX_EVENTS_SHOWN]:
        await send_card_message(
            bot,
            chat_id,
            card,
            format_event_message(card, event, tz, "active", lang=lang),
            event_buy_kb(event, lang=lang),
        )
    await bot.send_message(
        chat_id,
        tr(lang, "evt_footer"),
        reply_markup=event_list_footer_kb(owner_tg_id, lang=lang),
    )


@router.message(Command("event", "events"))
async def cmd_event(message: Message, db_user: User) -> None:
    await _send_active_events(message.bot, message.chat.id, message.from_user.id, lang=get_lang(db_user))


@router.callback_query(NavCb.filter(F.target == "event"))
async def nav_event(callback: CallbackQuery, db_user: User) -> None:
    await callback.answer()
    await _send_active_events(callback.bot, callback.message.chat.id, callback.from_user.id, lang=get_lang(db_user))


@router.callback_query(EvtCb.filter(F.action == "buy_coins"))
async def cb_event_buy_coins(callback: CallbackQuery, callback_data: EvtCb, db_user: User) -> None:
    async with get_session() as session:
        res = await EventService.buy_with_coins(session, callback_data.event_id, db_user)
        if res.success:
            await session.commit()
        else:
            await session.rollback()

    if not res.success:
        await callback.answer(res.message, show_alert=True)
        return

    await callback.answer(
        f"🎉 {res.card.character_name} sizniki! Event karta ({res.event.sold_count}/{res.event.total_copies}).",
        show_alert=True,
    )
    if callback.message is not None:
        await refresh_event_message(callback.message, res.card, res.event)
        if callback.message.chat.type != "private":
            try:
                await callback.message.reply(
                    f"🎉 <b>{esc(db_user.display_name)}</b> limitli event kartasini oldi: "
                    f"<b>{esc(res.card.character_name)}</b>! ({res.event.sold_count}/{res.event.total_copies})"
                )
            except TelegramBadRequest:
                pass


@router.callback_query(EvtCb.filter(F.action == "buy_stars"))
async def cb_event_buy_stars(callback: CallbackQuery, callback_data: EvtCb, bot: Bot, db_user: User) -> None:
    async with get_session() as session:
        res, payload = await EventService.create_stars_order(session, callback_data.event_id, db_user)
        if res.success:
            await session.commit()
        else:
            await session.rollback()

    if not res.success or payload is None:
        await callback.answer(res.message, show_alert=True)
        return

    event, card = res.event, res.card
    try:
        await bot.send_invoice(
            chat_id=callback.from_user.id,
            title=f"🎯 {card.character_name}"[:32],
            description=f"Limitli event kartasi ({card.rarity}) — {card.anime_name}. Bir kishi — bitta nusxa."[:255],
            payload=payload,
            currency="XTR",
            prices=[LabeledPrice(label=f"{card.character_name}"[:32], amount=int(event.price_stars))],
        )
    except (TelegramBadRequest, TelegramForbiddenError):
        await callback.answer(
            "Avval botga shaxsiy xabarda /start yuboring, keyin qayta urinib ko‘ring.", show_alert=True
        )
        return
    await callback.answer("⭐ Hisob-faktura shaxsiy chatga yuborildi!")
