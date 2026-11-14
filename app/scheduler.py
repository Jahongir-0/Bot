import asyncio
import logging

from aiogram import Bot
from sqlalchemy import select

from database.session import get_session
from keyboards.main_menu import drop_claim_kb
from models.base import utcnow
from models.card import Card
from models.group import Group
from models.schedule import ActiveDrop, DropSchedule
from services.card_service import CardService
from services.drop_service import DropService
from services.offer_service import OfferService
from utils.formatting import format_drop_message
from utils.tg_helpers import send_card_visual

logger = logging.getLogger(__name__)


async def _process_due_drops(bot: Bot) -> None:
    async with get_session() as session:
        # 1. Ensure today's schedule exists for all active groups
        groups = list(
            (
                await session.execute(
                    select(Group).where(Group.is_active.is_(True), Group.drop_enabled.is_(True))
                )
            )
            .scalars()
            .all()
        )
        for group in groups:
            await DropService.ensure_daily_schedule_for_group(session, group)

        # 2. Fetch due pending schedule items
        now = utcnow()
        due_items = list(
            (
                await session.execute(
                    select(DropSchedule)
                    .where(DropSchedule.status == "pending", DropSchedule.scheduled_at <= now)
                    .order_by(DropSchedule.scheduled_at.asc())
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )
        await session.commit()

    for sched in due_items:
        try:
            async with get_session() as session:
                s_row = await session.get(DropSchedule, sched.id)
                if s_row is None or s_row.status != "pending":
                    continue
                group = await session.get(Group, s_row.group_id)
                if group is None or not group.is_active or not group.drop_enabled:
                    s_row.status = "skipped"
                    await session.commit()
                    continue

                card = None
                # Priority card overrides scheduled card when set by admin
                priority_id = await CardService.pick_random_card(session, consume_priority=True) if s_row.card_id is None else None
                if s_row.card_id is not None:
                    # Check if a global next_priority_card was set; if so, prefer it!
                    from services.settings_service import SettingsService
                    global_pid = await SettingsService.get_next_priority_card_id(session)
                    if global_pid is not None:
                        card = await CardService.pick_random_card(session, consume_priority=True)
                    else:
                        cand = await session.get(Card, s_row.card_id)
                        if cand is not None and cand.is_available and not cand.is_deleted:
                            card = cand
                else:
                    card = priority_id

                if card is None:
                    card = await CardService.pick_random_card(session, consume_priority=True)

                if card is None:
                    s_row.status = "skipped"
                    await session.commit()
                    continue

                drop = await DropService.create_active_drop(
                    session=session,
                    group=group,
                    card=card,
                    schedule_id=s_row.id,
                )
                active_event = await CardService.get_active_event(session)
                s_row.status = "sent"
                s_row.executed_at = utcnow()
                await session.commit()

                caption = format_drop_message(card, active_event=active_event)
                msg = await send_card_visual(
                    bot=bot,
                    chat_id=group.telegram_chat_id,
                    image=card.image,
                    caption=caption,
                    reply_markup=drop_claim_kb(drop.id),
                )
                async with get_session() as s2:
                    d2 = await s2.get(ActiveDrop, drop.id)
                    if d2 is not None:
                        d2.telegram_message_id = msg.message_id
                        await s2.commit()

                logger.info(
                    "Dropped card %s (%s) to group %s",
                    card.card_code,
                    card.character_name,
                    group.telegram_chat_id,
                )
        except Exception as exc:
            logger.exception("Error dispatching scheduled drop #%s: %s", sched.id, exc)


async def run_scheduler_loop(bot: Bot, stop_event: asyncio.Event) -> None:
    logger.info("Drop scheduler loop started.")
    tick = 0
    while not stop_event.is_set():
        try:
            await _process_due_drops(bot)
            # Expire stale offers every ~5 minutes (10 ticks * 30s)
            if tick % 10 == 0:
                async with get_session() as session:
                    expired = await OfferService.expire_stale_offers(session)
                    if expired:
                        await session.commit()
            tick += 1
        except Exception as exc:
            logger.exception("Scheduler loop error: %s", exc)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            continue
    logger.info("Drop scheduler loop stopped.")
