"""Event e'lonlarini barcha guruhlarga yuborish va event tugaganda xabarlarni yopish."""
import asyncio
import logging
from typing import List

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import select

from config.settings import get_settings
from database.session import get_session
from keyboards.event import event_buy_kb
from models.group import Group
from models.limited_event import EventBroadcast, LimitedEvent
from services.event_service import EventService
from utils.formatting import format_event_message
from utils.tg_helpers import send_card_message

logger = logging.getLogger(__name__)
_SEND_CONCURRENCY = 8


async def announce_event(bot: Bot, event_id: int, notify_admin_id: int = 0) -> int:
    """Hamma faol guruhlarga 'event boshlandi' e'lonini yuboradi. Yuborilgan guruhlar soni qaytariladi."""
    tz = get_settings().timezone
    async with get_session() as session:
        pair = await EventService.get_event_with_card(session, event_id)
        if pair is None:
            return 0
        event, card = pair
        groups = list((await session.execute(select(Group).where(Group.is_active.is_(True)))).scalars().all())

    caption = format_event_message(card, event, tz, "active")
    kb = event_buy_kb(event)
    sem = asyncio.Semaphore(_SEND_CONCURRENCY)
    sent: List[EventBroadcast] = []
    dead_group_ids: List[int] = []

    async def _send(group: Group) -> None:
        async with sem:
            for attempt in range(2):
                try:
                    msg = await send_card_message(bot, group.telegram_chat_id, card, caption, kb)
                    sent.append(
                        EventBroadcast(
                            event_id=event.id,
                            telegram_chat_id=group.telegram_chat_id,
                            telegram_message_id=msg.message_id,
                            is_photo=bool(msg.photo),
                        )
                    )
                    break
                except TelegramRetryAfter as exc:
                    await asyncio.sleep(float(exc.retry_after) + 0.5)
                except (TelegramForbiddenError, TelegramBadRequest) as exc:
                    logger.warning("Event announce failed for %s: %s", group.telegram_chat_id, exc)
                    dead_group_ids.append(group.id)
                    break
                except Exception as exc:
                    logger.warning("Event announce error for %s: %s", group.telegram_chat_id, exc)
                    break
            await asyncio.sleep(0.04)

    await asyncio.gather(*[_send(g) for g in groups])

    async with get_session() as session:
        session.add_all(sent)
        for gid in dead_group_ids:
            g = await session.get(Group, gid)
            if g is not None:
                g.is_active = False
        await session.commit()

    if notify_admin_id:
        try:
            await bot.send_message(
                notify_admin_id,
                f"📢 Event #{event_id} e'loni {len(sent)}/{len(groups)} ta guruhga yuborildi.",
            )
        except Exception:
            pass
    return len(sent)


async def refresh_event_message(message, card, event) -> None:
    """Sotib olishdan keyin bosilgan e'londagi 'qoldi' sonini yangilaydi."""
    state = "active"
    if event.sold_count >= event.total_copies:
        state = "sold_out"
    text = format_event_message(card, event, get_settings().timezone, state)
    kb = event_buy_kb(event) if state == "active" else None
    try:
        if message.photo:
            await message.edit_caption(caption=text, reply_markup=kb)
        else:
            await message.edit_text(text=text, reply_markup=kb)
    except TelegramBadRequest:
        pass


async def finalize_ended_events(bot: Bot) -> int:
    """Tugagan/sotilib bo'lgan eventlarning e'lonlarini yopadi (tugma o'chadi, holat yoziladi)."""
    tz = get_settings().timezone
    async with get_session() as session:
        await EventService.close_expired(session)
        await session.commit()
        events = await EventService.get_unfinalized(session)

    done = 0
    for ev in events:
        async with get_session() as session:
            pair = await EventService.get_event_with_card(session, ev.id)
            if pair is None:
                continue
            event, card = pair
            broadcasts = list(
                (await session.execute(select(EventBroadcast).where(EventBroadcast.event_id == ev.id))).scalars().all()
            )
        state = "sold_out" if event.status == "sold_out" else "ended"
        text = format_event_message(card, event, tz, state)
        sem = asyncio.Semaphore(_SEND_CONCURRENCY)

        async def _edit(b: EventBroadcast) -> None:
            async with sem:
                try:
                    if b.is_photo:
                        await bot.edit_message_caption(
                            chat_id=b.telegram_chat_id, message_id=b.telegram_message_id, caption=text, reply_markup=None
                        )
                    else:
                        await bot.edit_message_text(
                            chat_id=b.telegram_chat_id, message_id=b.telegram_message_id, text=text, reply_markup=None
                        )
                except Exception:
                    pass
                await asyncio.sleep(0.04)

        await asyncio.gather(*[_edit(b) for b in broadcasts])
        async with get_session() as session:
            row = await session.get(LimitedEvent, ev.id)
            if row is not None:
                row.finalized = True
                await session.commit()
        done += 1
    return done
