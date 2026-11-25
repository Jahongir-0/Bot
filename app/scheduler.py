import asyncio
import logging
from datetime import timedelta
from typing import List, Tuple
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramMigrateToChat, TelegramRetryAfter
from sqlalchemy import delete, select, update

from app.event_announcer import finalize_ended_events
from config.settings import get_settings
from database.session import get_session
from models.base import ensure_utc, utcnow
from models.group import Group
from models.schedule import ActiveDrop, DropSchedule
from models.user import User
from models.v2_features import ForcedChannel, ScheduledMessage
from services.auction_wishlist_service import AuctionWishlistService
from services.card_service import CardService
from services.community_service import CommunityService
from services.drop_service import DropService
from services.group_policy_service import GroupPolicyService
from services.gameplay_service import GameplayService
from services.offer_service import OfferService
from services.quiz_announce import build_quiz_announcement
from services.security_backup_service import SecurityBackupService
from services.settings_service import SettingsService
from services.transfer_service import TransferService
from utils.formatting import esc, format_drop_message
from utils.tg_helpers import get_bot_username, send_card_message

logger = logging.getLogger(__name__)

# Bot o'chib turgan paytda o'tib ketgan eski slotlar spam bo'lib guruhga yog'ilmasligi uchun
STALE_SLOT_MINUTES = 30
SEND_CONCURRENCY = 6          # bir vaqtda nechta guruhga yuborish (Telegram limitlariga mos)
MEMBER_COUNT_MAX_AGE_HOURS = 6.0


# ---------------------------------------------------------------------------
# 1. Kunlik jadval: kun boshlanganda har bir guruh uchun avtomatik yangilanadi
# ---------------------------------------------------------------------------
async def _fetch_member_counts(bot: Bot, groups: List[Group]) -> dict:
    sem = asyncio.Semaphore(SEND_CONCURRENCY)
    out: dict = {}
    now = utcnow()

    async def _one(g: Group) -> None:
        fresh = (
            g.member_count is not None
            and g.member_count_updated_at is not None
            and (now - ensure_utc(g.member_count_updated_at)) < timedelta(hours=MEMBER_COUNT_MAX_AGE_HOURS)
        )
        if fresh:
            out[g.id] = g.member_count
            return
        async with sem:
            stats = await GroupPolicyService.fetch_member_stats(bot, g.telegram_chat_id)
            if stats is not None:
                out[g.id] = stats.real  # botlarsiz, haqiqiy odamlar

    await asyncio.gather(*[_one(g) for g in groups])
    return out


async def _daily_schedule_pass(bot: Bot) -> None:
    today = DropService._local_today()
    async with get_session() as session:
        missing = await DropService.get_groups_missing_schedule(session, today)
    if not missing:
        return

    counts = await _fetch_member_counts(bot, missing)
    tz = ZoneInfo(get_settings().timezone)
    summary: List[Tuple[str, int, int, List[str], int]] = []

    async with get_session() as session:
        rules = await SettingsService.get_drop_rules(session)
        for stale_group in missing:
            group = await session.get(Group, stale_group.id)
            if group is None:
                continue
            mc = counts.get(group.id)
            schedules = await DropService.ensure_daily_schedule_for_group(session, group, member_count=mc)
            times = [
                ensure_utc(s.scheduled_at).astimezone(tz).strftime("%H:%M")
                for s in schedules
                if s.status == "pending"
            ]
            summary.append((group.title or str(group.telegram_chat_id), group.member_count or 0, len(times), times, group.telegram_chat_id))
        await session.commit()

    if not summary:
        return
    logger.info("Daily schedule generated for %d groups", len(summary))
    await _announce_schedules(bot, summary, bool(rules.get("announce_in_group", True)))


async def _announce_schedules(bot: Bot, summary, announce_in_group: bool) -> None:
    sem = asyncio.Semaphore(SEND_CONCURRENCY)

    if announce_in_group:
        async def _group_msg(item) -> None:
            title, members, count, _times, chat_id = item
            if count <= 0:
                return
            async with sem:
                try:
                    await bot.send_message(
                        chat_id,
                        "🌅 <b>Yangi kun boshlandi!</b>\n\n"
                        f"👥 Guruh a’zolari: <b>{members:,}</b>\n"
                        f"🎴 Bugun guruhga <b>{count} ta</b> karta tushadi — tayyor turing!\n"
                        "<i>Kartalar kun davomida tasodifiy vaqtlarda tashlanadi.</i>",
                    )
                except TelegramRetryAfter as exc:
                    await asyncio.sleep(float(exc.retry_after) + 0.5)
                except Exception:
                    pass
                await asyncio.sleep(0.04)

        await asyncio.gather(*[_group_msg(i) for i in summary])

    # Adminlarga to'liq jadval (vaqtlar faqat adminga ko'rsatiladi, guruhlarga emas)
    admin_ids = sorted(get_settings().admin_ids)
    if not admin_ids:
        return
    lines = ["📅 <b>BUGUNGI DROP JADVALI</b>\n"]
    shown = 0
    for title, members, count, times, _chat in summary:
        if shown >= 25:
            break
        shown += 1
        t_text = ", ".join(times[:14]) + (" …" if len(times) > 14 else "")
        lines.append(f"• <b>{esc(title)}</b> — 👥 {members:,} · 🎴 {count}\n   <code>{t_text or '—'}</code>")
    if len(summary) > shown:
        lines.append(f"\n… va yana {len(summary) - shown} ta guruh (Admin → Jadval bo‘limida ko‘ring).")
    text = "\n".join(lines)
    chunks = [text[i : i + 3800] for i in range(0, len(text), 3800)][:4]
    for admin_id in admin_ids:
        for chunk in chunks:
            try:
                await bot.send_message(admin_id, chunk)
            except Exception:
                break


# ---------------------------------------------------------------------------
# 2. Vaqti kelgan slotlarni bajarish
# ---------------------------------------------------------------------------
async def _process_due_drops(bot: Bot) -> None:
    now = utcnow()
    async with get_session() as session:
        await session.execute(
            update(DropSchedule)
            .where(
                DropSchedule.status == "pending",
                DropSchedule.scheduled_at < now - timedelta(minutes=STALE_SLOT_MINUTES),
            )
            .values(status="skipped")
        )
        due_ids = list(
            (
                await session.execute(
                    select(DropSchedule.id)
                    .where(DropSchedule.status == "pending", DropSchedule.scheduled_at <= now)
                    .order_by(DropSchedule.scheduled_at.asc())
                    .limit(60)
                )
            )
            .scalars()
            .all()
        )
        await session.commit()

    if not due_ids:
        return
    sem = asyncio.Semaphore(SEND_CONCURRENCY)

    async def _run(sid: int) -> None:
        async with sem:
            try:
                await _dispatch_slot(bot, sid)
            except Exception as exc:
                logger.exception("Error dispatching scheduled drop #%s: %s", sid, exc)

    await asyncio.gather(*[_run(i) for i in due_ids])


async def _dispatch_slot(bot: Bot, schedule_id: int) -> None:
    async with get_session() as session:
        s_row = await session.get(DropSchedule, schedule_id)
        if s_row is None or s_row.status != "pending":
            return
        group = await session.get(Group, s_row.group_id)
        if group is None or not group.is_active or not group.drop_enabled:
            s_row.status = "skipped"
            await session.commit()
            return

        # Guruhda haqiqiy a'zolar admin belgilagan minimumdan kam bo'lsa karta tushmaydi
        _rules = await SettingsService.get_drop_rules(session)
        _min_members = max(0, int(_rules.get("min_members", 0)))
        if _min_members and group.member_count is not None and int(group.member_count) < _min_members:
            s_row.status = "skipped"
            await session.commit()
            logger.info("Group %s has %s members (< %s); drop skipped.", group.telegram_chat_id, group.member_count, _min_members)
            return

        # Slotni atomik band qilamiz (parallel ishlovdan himoya)
        claimed = await session.execute(
            update(DropSchedule)
            .where(DropSchedule.id == schedule_id, DropSchedule.status == "pending")
            .values(status="sent", executed_at=utcnow())
        )
        if claimed.rowcount != 1:
            await session.rollback()
            return

        drop = None
        card = None
        for attempt in range(4):
            card, forced = await DropService.pick_card_for_group(
                session, group, s_row.card_id if attempt == 0 else None
            )
            if card is None:
                break
            drop = await DropService.create_active_drop(session, group, card, schedule_id=s_row.id, force=forced)
            if drop is not None:
                break

        if drop is None or card is None:
            s_row.status = "skipped"
            await session.commit()
            logger.info("No eligible card for group %s; slot %s skipped.", group.telegram_chat_id, schedule_id)
            return

        bot_username = await get_bot_username(bot)
        active_event = await CardService.get_active_event(session)
        ad_footer = await CommunityService.get_rendered_ad(
            session, bot_username=bot_username, user=None, placement="group"
        )
        await session.commit()
        chat_id = group.telegram_chat_id
        group_pk = group.id

    caption = format_drop_message(card, active_event=active_event, ad_footer=ad_footer)
    try:
        msg = await send_card_message(bot, chat_id, card, caption, reply_markup=None)
    except TelegramRetryAfter as exc:
        await asyncio.sleep(float(exc.retry_after) + 0.5)
        try:
            msg = await send_card_message(bot, chat_id, card, caption, reply_markup=None)
        except Exception as retry_exc:
            logger.exception("Drop retry failed for chat %s: %s", chat_id, retry_exc)
            async with get_session() as s_fail:
                await s_fail.execute(delete(ActiveDrop).where(ActiveDrop.id == drop.id))
                await s_fail.execute(
                    update(DropSchedule)
                    .where(DropSchedule.id == schedule_id)
                    .values(status="failed", updated_at=utcnow())
                )
                await s_fail.commit()
            return
    except TelegramForbiddenError as send_exc:
        # Bot removed/blocked: this is a reliable signal that the group should be disabled.
        logger.warning("Cannot send drop to chat %s (%s). Deactivating group.", chat_id, send_exc)
        async with get_session() as s_fail:
            await s_fail.execute(delete(ActiveDrop).where(ActiveDrop.id == drop.id))
            await s_fail.execute(
                update(DropSchedule)
                .where(DropSchedule.id == schedule_id)
                .values(status="failed", updated_at=utcnow())
            )
            g_fail = await s_fail.get(Group, group_pk)
            if g_fail is not None:
                g_fail.is_active = False
                g_fail.drop_enabled = False
            await s_fail.commit()
        return
    except TelegramBadRequest as send_exc:
        # A malformed media/message must not automatically disable an otherwise healthy group.
        logger.warning("Drop message rejected for chat %s: %s", chat_id, send_exc)
        async with get_session() as s_fail:
            await s_fail.execute(delete(ActiveDrop).where(ActiveDrop.id == drop.id))
            await s_fail.execute(
                update(DropSchedule)
                .where(DropSchedule.id == schedule_id)
                .values(status="failed", updated_at=utcnow())
            )
            await s_fail.commit()
        return
    except Exception as send_exc:
        logger.exception("Unexpected drop send failure for chat %s: %s", chat_id, send_exc)
        async with get_session() as s_fail:
            await s_fail.execute(delete(ActiveDrop).where(ActiveDrop.id == drop.id))
            await s_fail.execute(
                update(DropSchedule)
                .where(DropSchedule.id == schedule_id)
                .values(status="failed", updated_at=utcnow())
            )
            await s_fail.commit()
        return

    async with get_session() as s2:
        d2 = await s2.get(ActiveDrop, drop.id)
        if d2 is not None:
            d2.telegram_message_id = msg.message_id
            await s2.commit()

    logger.info("Dropped card %s (%s) to group %s", card.card_code, card.character_name, chat_id)


async def _process_v2_periodic_jobs(bot: Bot, tick: int) -> None:
    # 1. Auktsion, Season, Temp Ban, Kanal muddati, Rejalashtirilgan Quiz va Xabarlar (~1 daqiqada bir)
    if tick % 2 == 0:
        now = utcnow()
        due_msgs: List[ScheduledMessage] = []
        due_quizzes = []
        async with get_session() as session:
            finalized_auc = await AuctionWishlistService.finalize_expired_auctions(session)
            ending_auc_notifs = await AuctionWishlistService.notify_ending_auctions(session)
            finalized_seasons = await GameplayService.finalize_expired_seasons(session)
            expired_bans = await SecurityBackupService.expire_temp_bans(session)
            expired_ads = await CommunityService.expire_due_ads(session)
            expired_ch = await session.execute(
                update(ForcedChannel)
                .where(
                    ForcedChannel.is_active.is_(True),
                    ForcedChannel.sub_mode == "daily",
                    ForcedChannel.expires_at.is_not(None),
                    ForcedChannel.expires_at <= now,
                )
                .values(is_active=False)
            )
            due_msgs = await CommunityService.get_due_scheduled_messages(session)
            due_quizzes = await GameplayService.get_due_scheduled_quizzes(session)
            for q in due_quizzes:
                q.status = "active"
                q.is_active = True
                q.is_dispatched = True
            if finalized_auc or ending_auc_notifs or finalized_seasons or expired_bans or expired_ads or expired_ch.rowcount or due_msgs or due_quizzes:
                await session.commit()

        # Rejalashtirilgan xabarlarni yuborish
        for sm in due_msgs:
            sent_cnt = 0
            targets: List[int] = []
            async with get_session() as s_msg:
                if sm.target_scope in ("users", "all"):
                    u_ids = (await s_msg.execute(select(User.telegram_id).where(User.is_banned.is_(False)))).scalars().all()
                    targets.extend(int(x) for x in u_ids)
                if sm.target_scope in ("groups", "all"):
                    g_ids = (
                        await s_msg.execute(
                            select(Group.telegram_chat_id).where(Group.is_active.is_(True), Group.is_banned.is_(False))
                        )
                    ).scalars().all()
                    targets.extend(int(x) for x in g_ids)
            targets = list(dict.fromkeys(targets))

            migrated_msgs: List[Tuple[int, int]] = []
            sem = asyncio.Semaphore(4)

            async def _send_one(cid: int) -> None:
                nonlocal sent_cnt
                async with sem:
                    target_cid = cid
                    for attempt in range(3):
                        try:
                            if sm.from_chat_id and sm.forward_message_id:
                                await bot.copy_message(chat_id=target_cid, from_chat_id=sm.from_chat_id, message_id=sm.forward_message_id)
                            else:
                                await bot.send_message(target_cid, sm.body)
                            sent_cnt += 1
                            break
                        except TelegramRetryAfter as exc:
                            await asyncio.sleep(float(exc.retry_after) + 0.5)
                        except TelegramMigrateToChat as exc:
                            new_cid = int(exc.migrate_to_chat_id)
                            migrated_msgs.append((target_cid, new_cid))
                            target_cid = new_cid
                        except Exception:
                            if attempt == 0 and sm.from_chat_id and sm.forward_message_id and sm.body:
                                try:
                                    await bot.send_message(target_cid, sm.body)
                                    sent_cnt += 1
                                except Exception:
                                    pass
                            break
                    await asyncio.sleep(0.05)

            if targets:
                await asyncio.gather(*[_send_one(c) for c in targets])

            async with get_session() as s_done:
                for old_cid, new_cid in migrated_msgs:
                    await s_done.execute(
                        update(Group).where(Group.telegram_chat_id == old_cid).values(telegram_chat_id=new_cid)
                    )
                row = await s_done.get(ScheduledMessage, sm.id)
                if row is not None:
                    row.status = "sent"
                    row.sent_count = sent_cnt
                await s_done.commit()

        # Rejalashtirilgan viktorinalarni e'lon qilish
        for q in due_quizzes:
            async with get_session() as s_q:
                qs_res = await GameplayService.get_quiz_with_questions(s_q, q.id)
                q_targets: List[int] = []
                if q.target_scope == "group" and q.target_group_id:
                    grp = await s_q.get(Group, q.target_group_id)
                    if grp and grp.is_active and not grp.is_banned:
                        q_targets.append(grp.telegram_chat_id)
                else:
                    g_ids = (
                        await s_q.execute(
                            select(Group.telegram_chat_id).where(Group.is_active.is_(True), Group.is_banned.is_(False))
                        )
                    ).scalars().all()
                    u_ids = (await s_q.execute(select(User.telegram_id).where(User.is_banned.is_(False)))).scalars().all()
                    q_targets.extend(int(x) for x in g_ids)
                    q_targets.extend(int(x) for x in u_ids)
            q_targets = list(dict.fromkeys(q_targets))

            _bot_un = await get_bot_username(bot)
            q_text, q_markup = build_quiz_announcement(q, len(qs_res[1]) if qs_res else 0, _bot_un)
            sem_q = asyncio.Semaphore(4)

            async def _send_quiz_msg(cid: int) -> None:
                async with sem_q:
                    target_cid = cid
                    for _attempt in range(2):
                        try:
                            await bot.send_message(target_cid, q_text, reply_markup=q_markup)
                            break
                        except TelegramRetryAfter as exc:
                            await asyncio.sleep(float(exc.retry_after) + 0.5)
                        except TelegramMigrateToChat as exc:
                            target_cid = int(exc.migrate_to_chat_id)
                        except Exception:
                            break
                    await asyncio.sleep(0.05)

            if q_targets:
                await asyncio.gather(*[_send_quiz_msg(c) for c in q_targets])

    # 2. Avtomatik kunlik backup + eski backuplarni tozalash (~24 soatda bir: 2880 tick * 30s)
    if tick > 0 and tick % 2880 == 120:
        async with get_session() as s_bak:
            await SecurityBackupService.create_full_backup_zip(s_bak, backup_type="auto")
            await SecurityBackupService.cleanup_old_backups(s_bak, retention_days=14)
            await s_bak.commit()


# ---------------------------------------------------------------------------
# Asosiy sikl
# ---------------------------------------------------------------------------
async def run_scheduler_loop(bot: Bot, stop_event: asyncio.Event) -> None:
    logger.info("Drop scheduler loop started.")
    tick = 0
    while not stop_event.is_set():
        try:
            await _daily_schedule_pass(bot)
            await _process_due_drops(bot)
            if tick % 2 == 0:
                await finalize_ended_events(bot)
            await _process_v2_periodic_jobs(bot, tick)
            # Muddati o'tgan takliflar/transferlar/tradelar ~5 daqiqada bir (10 tick * 30s)
            if tick % 10 == 0:
                async with get_session() as session:
                    expired = await OfferService.expire_stale_offers(session)
                    expired_transfers = await TransferService.expire_stale_transfers(session)
                    expired_trades = await AuctionWishlistService.expire_stale_trades(session)
                    if expired or expired_transfers or expired_trades:
                        await session.commit()
            tick += 1
        except Exception as exc:
            logger.exception("Scheduler loop error: %s", exc)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            continue
    logger.info("Drop scheduler loop stopped.")
