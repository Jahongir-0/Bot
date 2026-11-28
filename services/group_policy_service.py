"""Guruh a'zolarini (faqat haqiqiy odamlar) hisoblash, guruh mukofoti qoidalari va adminlarga xabar."""
import asyncio
import logging
from dataclasses import dataclass
from typing import List, Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from models.base import utcnow
from models.schedule import ActiveDrop, DropSchedule
from models.group import Group, GroupMember
from models.v2_features import AdminRole
from services.settings_service import SettingsService
from utils.formatting import esc, fmt_coins
from utils.timeutils import fmt_tashkent

logger = logging.getLogger(__name__)

LINKS_KEY = "group_invite_links"


@dataclass
class MemberStats:
    total: int  # Telegram bergan jami a'zolar (botlar bilan)
    real: int  # botlarsiz, haqiqiy odamlar (taxminiy: faqat admin botlar aniq ajratiladi)
    bots: int  # aniqlangan botlar soni (shu bot ham kiradi)


@dataclass
class RewardDecision:
    eligible: bool
    coins: int
    reason: str = ""
    give_epic_card: bool = True


class GroupPolicyService:
    # ------------------------------------------------------------------
    # A'zolar soni: botlarni chiqarib tashlab
    # ------------------------------------------------------------------
    @staticmethod
    async def fetch_member_stats(bot: Bot, chat_id: int) -> Optional[MemberStats]:
        """Guruhdagi jami a'zolar va haqiqiy odamlar sonini qaytaradi.

        Telegram botlarga oddiy a'zolar ro'yxatini bermaydi, shuning uchun aniqlanadigan botlar:
        shu botning o'zi va guruh adminlari orasidagi botlar. Admin bo'lmagan begona botlarni ajratib bo'lmaydi.
        """
        try:
            total = int(await bot.get_chat_member_count(chat_id))
        except TelegramRetryAfter as exc:
            await asyncio.sleep(float(exc.retry_after) + 0.5)
            try:
                total = int(await bot.get_chat_member_count(chat_id))
            except Exception:
                return None
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            logger.warning("get_chat_member_count failed for %s: %s", chat_id, exc)
            return None
        except Exception as exc:
            logger.warning("get_chat_member_count error for %s: %s", chat_id, exc)
            return None

        bot_ids = {int(bot.id)}
        try:
            admins = await bot.get_chat_administrators(chat_id)
            for m in admins:
                if m.user is not None and m.user.is_bot:
                    bot_ids.add(int(m.user.id))
        except Exception as exc:  # admin ro'yxati olinmasa ham hisob davom etadi
            logger.info("get_chat_administrators failed for %s: %s", chat_id, exc)

        real = max(0, total - len(bot_ids))
        return MemberStats(total=total, real=real, bots=len(bot_ids))

    # ------------------------------------------------------------------
    # Guruhga qo'shgani uchun mukofotni hisoblash
    # ------------------------------------------------------------------
    @staticmethod
    async def decide_reward(session: AsyncSession, real_members: Optional[int]) -> RewardDecision:
        cfg = await SettingsService.get_group_reward_config(session)
        if not cfg["enabled"]:
            return RewardDecision(False, 0, "Guruhga qo‘shish mukofoti hozircha o‘chirilgan.", False)
        if real_members is None:
            return RewardDecision(False, 0, "Guruh a‘zolari sonini aniqlab bo‘lmadi.", False)
        min_m = int(cfg["min_members"])
        if real_members < min_m:
            return RewardDecision(
                False,
                0,
                f"Guruhda kamida {min_m} ta haqiqiy a‘zo bo‘lishi kerak (hozir: {real_members}).",
                False,
            )
        if cfg["per_member"]:
            coins = int(real_members) * int(cfg["per_member_coins"])
        else:
            coins = int(cfg["flat_coins"])
        coins = max(0, min(coins, 1_000_000_000))
        return RewardDecision(True, coins, "", bool(cfg["give_epic_card"]))

    # ------------------------------------------------------------------
    # Guruh havolasi (admin guruhga kira olishi uchun)
    # ------------------------------------------------------------------
    @staticmethod
    async def get_saved_link(session: AsyncSession, chat_id: int) -> Optional[str]:
        links = await SettingsService.get_value(session, LINKS_KEY, {})
        if isinstance(links, dict):
            return links.get(str(chat_id))
        return None

    @staticmethod
    async def save_link(session: AsyncSession, chat_id: int, link: str) -> None:
        links = await SettingsService.get_value(session, LINKS_KEY, {})
        links = dict(links) if isinstance(links, dict) else {}
        if links.get(str(chat_id)) == link:
            return
        links[str(chat_id)] = link
        await SettingsService.set_value(session, LINKS_KEY, links)

    @staticmethod
    async def resolve_link(
        bot: Optional[Bot], session: AsyncSession, chat_id: int, username: Optional[str], try_create: bool = False
    ) -> Optional[str]:
        """Ochiq guruh: https://t.me/username. Yopiq guruh: saqlangan yoki yangi yaratilgan taklif havolasi."""
        if username:
            return f"https://t.me/{username.lstrip('@')}"
        saved = await GroupPolicyService.get_saved_link(session, chat_id)
        if saved:
            return saved
        if bot is None:
            return None
        link: Optional[str] = None
        try:
            chat = await bot.get_chat(chat_id)
            link = getattr(chat, "invite_link", None)
            if not link and getattr(chat, "username", None):
                link = f"https://t.me/{chat.username}"
        except Exception:
            link = None
        if not link and try_create:
            try:
                created = await bot.create_chat_invite_link(chat_id, name="Admin (bot)")
                link = created.invite_link
            except Exception as exc:  # bot admin emas yoki huquqi yo'q
                logger.info("invite link not created for %s: %s", chat_id, exc)
        if link:
            await GroupPolicyService.save_link(session, chat_id, link)
        return link

    @staticmethod
    def format_group_link_line(username: Optional[str], link: Optional[str]) -> str:
        if username:
            return f"🔗 @{esc(username.lstrip('@'))} — https://t.me/{esc(username.lstrip('@'))}"
        if link:
            return f"🔗 {esc(link)}"
        return "🔗 Havola yo‘q (yopiq guruh — botni admin qilsangiz havola olinadi)"

    # ------------------------------------------------------------------
    # Adminlarga xabar
    # ------------------------------------------------------------------
    @staticmethod
    async def admin_chat_ids(session: AsyncSession) -> List[int]:
        ids = {int(x) for x in get_settings().admin_ids if x}
        rows = (
            await session.execute(
                select(AdminRole.telegram_id).where(AdminRole.is_active.is_(True), AdminRole.telegram_id.is_not(None))
            )
        ).scalars().all()
        ids.update(int(r) for r in rows if r)
        return sorted(ids)

    @staticmethod
    async def notify_admins_bot_added(
        bot: Bot,
        session: AsyncSession,
        *,
        group: Group,
        chat_title: str,
        chat_username: Optional[str],
        adder_tg_id: Optional[int],
        adder_name: Optional[str],
        adder_username: Optional[str],
        stats: Optional[MemberStats],
        decision: Optional[RewardDecision],
        already_rewarded: bool = False,
        phase: str = "admin",
        just_added: bool = True,
    ) -> None:
        link = await GroupPolicyService.resolve_link(bot, session, group.telegram_chat_id, chat_username, try_create=True)
        admin_ids = await GroupPolicyService.admin_chat_ids(session)
        if not admin_ids:
            return

        who = esc(adder_name or "Noma’lum")
        if adder_username:
            who += f" (@{esc(adder_username)})"
        if adder_tg_id:
            who += f" · <code>{adder_tg_id}</code>"

        if stats is not None:
            members_line = (
                f"📊 A‘zolar: jami <b>{stats.total}</b> · haqiqiy odamlar <b>{stats.real}</b> · "
                f"aniqlangan botlar <b>{stats.bots}</b>"
            )
        else:
            members_line = "📊 A‘zolar soni: aniqlanmadi"

        if phase == "added":
            reward_line = "⏳ Mukofot: bot <b>admin qilinmaguncha berilmaydi</b> (foydalanuvchiga ogohlantirish yuborildi)"
        elif already_rewarded:
            reward_line = "🎁 Mukofot: bu guruh uchun avval berilgan"
        elif decision is None:
            reward_line = "🎁 Mukofot: —"
        elif decision.eligible:
            reward_line = f"🎁 Mukofot: <b>+{fmt_coins(decision.coins)} 🪙</b> berildi"
        else:
            reward_line = f"🎁 Mukofot: <b>berilmadi</b> — {esc(decision.reason)}"

        if phase == "added":
            title_line = "🆕 <b>BOT YANGI GURUHGA QO‘SHILDI</b> (hali admin emas)"
        elif just_added:
            title_line = "🆕 <b>BOT YANGI GURUHGA QO‘SHILDI VA ADMIN QILINDI</b> ✅"
        else:
            title_line = "🛡 <b>BOT GURUHDA ADMIN QILINDI</b> ✅"
        text = (
            f"{title_line}\n"
            "━━━━━━━━━━━━━━━\n"
            f"👤 Kim qo‘shdi: {who}\n"
            f"🕒 Vaqt: <b>{fmt_tashkent(utcnow(), '%d.%m.%Y %H:%M')}</b> (Toshkent vaqti)\n"
            f"👥 Guruh: <b>{esc(chat_title)}</b>\n"
            f"🆔 Chat ID: <code>{group.telegram_chat_id}</code>\n"
            f"{GroupPolicyService.format_group_link_line(chat_username, link)}\n"
            f"{members_line}\n"
            f"{reward_line}"
        )
        markup: Optional[InlineKeyboardMarkup] = None
        if link:
            markup = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🚪 Guruhga kirish", url=link)]]
            )

        for aid in admin_ids:
            try:
                await bot.send_message(aid, text, reply_markup=markup, link_preview_options=LinkPreviewOptions(is_disabled=True))
            except TelegramRetryAfter as exc:
                await asyncio.sleep(float(exc.retry_after) + 0.5)
                try:
                    await bot.send_message(aid, text, reply_markup=markup, link_preview_options=LinkPreviewOptions(is_disabled=True))
                except Exception:
                    pass
            except Exception as exc:
                logger.info("admin %s ga guruh xabari yuborilmadi: %s", aid, exc)
            await asyncio.sleep(0.05)

    @staticmethod
    async def migrate_group_chat_id(session: AsyncSession, old_cid: int, new_cid: int) -> bool:
        """Guruh superguruhga ko‘chganda telegram_chat_id ni xavfsiz yangilaydi.

        Agar yangi new_cid allaqachon alohida Group qatorida mavjud bo‘lsa,
        UniqueViolationError chiqmasligi uchun bog‘liq yozuvlarni ko‘chirib, eski qatorni o‘chiradi.
        """
        old_cid = int(old_cid)
        new_cid = int(new_cid)
        if old_cid == new_cid:
            return False

        async with session.begin_nested():
            old_group = (
                await session.execute(select(Group).where(Group.telegram_chat_id == old_cid))
            ).scalar_one_or_none()
            if old_group is None:
                return False

            new_group = (
                await session.execute(
                    select(Group).where(Group.telegram_chat_id == new_cid, Group.id != old_group.id)
                )
            ).scalar_one_or_none()

            if new_group is None:
                old_group.telegram_chat_id = new_cid
                await session.flush()
                return True

            await session.execute(
                update(DropSchedule).where(DropSchedule.group_id == old_group.id).values(group_id=new_group.id)
            )
            await session.execute(
                update(ActiveDrop).where(ActiveDrop.group_id == old_group.id).values(group_id=new_group.id)
            )
            existing_uids = set(
                (
                    await session.execute(
                        select(GroupMember.user_id).where(GroupMember.group_id == new_group.id)
                    )
                ).scalars().all()
            )
            if existing_uids:
                await session.execute(
                    delete(GroupMember).where(
                        GroupMember.group_id == old_group.id,
                        GroupMember.user_id.in_(existing_uids),
                    )
                )
            await session.execute(
                update(GroupMember).where(GroupMember.group_id == old_group.id).values(group_id=new_group.id)
            )

            if old_group.added_by_user_id and not new_group.added_by_user_id:
                new_group.added_by_user_id = old_group.added_by_user_id
            if old_group.add_reward_claimed and not new_group.add_reward_claimed:
                new_group.add_reward_claimed = True
            if old_group.member_count and int(new_group.member_count or 0) < int(old_group.member_count):
                new_group.member_count = old_group.member_count

            old_group.is_active = False
            old_group.drop_enabled = False
            old_group.bot_status = "migrated"
            old_group.status_reason = f"Migrated to {new_cid}"
            await session.flush()
            return True

