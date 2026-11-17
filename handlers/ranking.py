from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database.session import get_session
from keyboards.market import ranking_kb
from models.group import Group
from services.ranking_service import RankingService
from utils.callback_data import NavCb, RankingCb
from utils.formatting import esc, fmt_coins
from utils.tg_helpers import edit_or_answer

router = Router(name="ranking")


async def _show_ranking(
    target,
    owner_id: int,
    scope: str,
    db_group: Optional[Group],
    period: str = "all",
) -> None:
    in_group = db_group is not None
    if scope == "group" and not in_group:
        scope = "global"

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}

    if scope == "groups":
        async with get_session() as session:
            grp_entries = await RankingService.get_groups_top(session, limit=10)
            standing = (
                await RankingService.get_group_standing(session, db_group.id)
                if db_group is not None
                else None
            )

        header = "🏰 <b>GURUHLAR TOP 10 REYTINGI</b>"
        if not grp_entries:
            text = f"{header}\n\n📭 Hozircha faol guruhlar reytingi bo‘sh."
        else:
            lines = [f"{header}\n<i>(Faol bot foydalanuvchilari va olingan kartalar bo‘yicha)</i>\n"]
            for g in grp_entries:
                prefix = medals.get(g.rank, f"<b>#{g.rank}</b>")
                members_info = f" · 💬{g.member_count}" if g.member_count else ""
                lines.append(
                    f"{prefix} <b>{esc(g.group.title)}</b> — "
                    f"👤 {g.active_users} faol | 🎴 {g.claimed_cards} karta{members_info} · ⭐ {g.score}"
                )
            if standing is not None and standing.rank > 10:
                lines.append(
                    f"\n📍 Ushbu guruh o‘rni: <b>#{standing.rank}</b> "
                    f"(👤 {standing.active_users} faol · 🎴 {standing.claimed_cards} karta · ⭐ {standing.score})"
                )
            text = "\n".join(lines)

        await edit_or_answer(
            target,
            text,
            reply_markup=ranking_kb(owner_id=owner_id, current_scope=scope, in_group=in_group, period=period),
        )
        return

    if scope in ("coins", "xp", "quiz", "games", "trades", "auction", "refs", "rare", "mythic", "divine"):
        titles = {
            "coins": ("🪙 ENG BOY O‘YINCHILAR", "coins"),
            "xp": ("⚡ ENG YUQORI XP", "XP"),
            "quiz": ("🧠 QUIZ CHEMPIONLARI", "g‘alaba"),
            "games": ("🎲 MINI O‘YIN USTALARI", "yutuq"),
            "trades": ("🤝 ENG FAOL SAVDOGARLAR", "trade"),
            "auction": ("🔨 AUKSION G‘OLIBLARI", "auksion"),
            "refs": ("📣 REFERAL YETAKCHILARI", "do‘st"),
            "rare": ("💎 NOYOB KARTALAR (RARE+)", "karta"),
            "mythic": ("🔴 MYTHIC KOLLEKSIONERLAR", "Mythic"),
            "divine": ("✨ DIVINE KOLLEKSIONERLAR", "Divine"),
        }
        hdr, unit = titles[scope]
        p_label = {"all": "Barcha vaqt", "monthly": "Oylik", "weekly": "Haftalik"}.get(period, period)
        async with get_session() as session:
            rows = await RankingService.get_extended_top(session, scope, period=period, limit=10)
        lines = [f"<b>{hdr}</b> ({p_label})\n"]
        if not rows:
            lines.append("📭 Ma’lumot topilmadi.")
        else:
            for idx, (u, val) in enumerate(rows, start=1):
                prefix = medals.get(idx, f"<b>#{idx}</b>")
                lines.append(f"{prefix} {esc(u.display_name)} — <b>{fmt_coins(val)}</b> {unit}")
        await edit_or_answer(
            target,
            "\n".join(lines),
            reply_markup=ranking_kb(owner_id=owner_id, current_scope=scope, in_group=in_group, period=period),
        )
        return

    async with get_session() as session:
        if scope == "group" and db_group is not None:
            entries = await RankingService.get_group_top(session, db_group.id, limit=10)
            header = f"🏆 <b>GROUP TOP 10 — {esc(db_group.title)}</b>"
        else:
            entries = await RankingService.get_global_top(session, limit=10)
            header = "🌍 <b>GLOBAL TOP 10</b>"

    if not entries:
        text = f"{header}\n\n📭 Hozircha reyting ma'lumotlari yo‘q."
    else:
        lines = [f"{header}\n"]
        for e in entries:
            prefix = medals.get(e.rank, f"<b>#{e.rank}</b>")
            lines.append(
                f"{prefix} {esc(e.user.display_name)} — "
                f"✨ {e.unique_cards} unique | 🎴 {e.total_cards} total "
                f"(🟡{e.legendary_cards} 🔴{e.mythic_cards})"
            )
        text = "\n".join(lines)

    await edit_or_answer(
        target,
        text,
        reply_markup=ranking_kb(owner_id=owner_id, current_scope=scope, in_group=in_group, period=period),
    )


@router.message(Command("top"))
async def cmd_top(message: Message, db_group: Optional[Group] = None) -> None:
    scope = "group" if db_group is not None else "global"
    await _show_ranking(message, message.from_user.id, scope, db_group)


@router.callback_query(NavCb.filter(F.target == "ranking"))
async def cb_ranking_nav(callback: CallbackQuery, db_group: Optional[Group] = None) -> None:
    scope = "group" if db_group is not None else "global"
    await _show_ranking(callback, callback.from_user.id, scope, db_group)


@router.callback_query(RankingCb.filter())
async def cb_ranking_switch(
    callback: CallbackQuery,
    callback_data: RankingCb,
    db_group: Optional[Group] = None,
) -> None:
    await _show_ranking(callback, callback.from_user.id, callback_data.scope, db_group, callback_data.period)
