from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database.session import get_session
from keyboards.market import ranking_kb
from models.group import Group
from models.user import User
from services.ranking_service import RankingService
from utils.callback_data import NavCb, RankingCb
from utils.formatting import esc, fmt_coins
from utils.i18n import get_lang
from utils.tg_helpers import edit_or_answer

router = Router(name="ranking")


async def _show_ranking(
    target,
    owner_id: int,
    scope: str,
    db_group: Optional[Group],
    period: str = "all",
    lang: str = "uz",
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

        header = "🏰 <b>TOP 10 GROUPS</b>"
        if not grp_entries:
            text = f"{header}\n\n📭 —"
        else:
            lines = [f"{header}\n"]
            for g in grp_entries:
                prefix = medals.get(g.rank, f"<b>#{g.rank}</b>")
                members_info = f" · 💬{g.member_count}" if g.member_count else ""
                lines.append(
                    f"{prefix} <b>{esc(g.group.title)}</b> — "
                    f"👤 {g.active_users} | 🎴 {g.claimed_cards}{members_info} · ⭐ {g.score}"
                )
            if standing is not None and standing.rank > 10:
                lines.append(
                    f"\n📍 Group rank: <b>#{standing.rank}</b> "
                    f"(👤 {standing.active_users} · 🎴 {standing.claimed_cards} · ⭐ {standing.score})"
                )
            text = "\n".join(lines)

        await edit_or_answer(
            target,
            text,
            reply_markup=ranking_kb(owner_id=owner_id, current_scope=scope, in_group=in_group, period=period, lang=lang),
        )
        return

    if scope in ("coins", "xp", "quiz", "games", "trades", "auction", "refs", "rare", "mythic", "divine"):
        titles = {
            "coins": ("🪙 TOP COINS", "coins"),
            "xp": ("⚡ TOP XP", "XP"),
            "quiz": ("🧠 QUIZ CHAMPIONS", "wins"),
            "games": ("🎲 MINI GAMES", "wins"),
            "trades": ("🤝 TOP TRADERS", "trades"),
            "auction": ("🔨 AUCTION WINNERS", "wins"),
            "refs": ("📣 TOP REFERRERS", "refs"),
            "rare": ("💎 RARE+ COLLECTORS", "cards"),
            "mythic": ("🔴 MYTHIC COLLECTORS", "Mythic"),
            "divine": ("✨ DIVINE COLLECTORS", "Divine"),
        }
        hdr, unit = titles[scope]
        async with get_session() as session:
            rows = await RankingService.get_extended_top(session, scope, period=period, limit=10)
        lines = [f"<b>{hdr}</b> ({period})\n"]
        if not rows:
            lines.append("📭 —")
        else:
            for idx, (u, val) in enumerate(rows, start=1):
                prefix = medals.get(idx, f"<b>#{idx}</b>")
                lines.append(f"{prefix} {esc(u.display_name)} — <b>{fmt_coins(val)}</b> {unit}")
        await edit_or_answer(
            target,
            "\n".join(lines),
            reply_markup=ranking_kb(owner_id=owner_id, current_scope=scope, in_group=in_group, period=period, lang=lang),
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
        text = f"{header}\n\n📭 —"
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
        reply_markup=ranking_kb(owner_id=owner_id, current_scope=scope, in_group=in_group, period=period, lang=lang),
    )


@router.message(Command("top"))
async def cmd_top(message: Message, db_user: User, db_group: Optional[Group] = None) -> None:
    scope = "group" if db_group is not None else "global"
    await _show_ranking(message, message.from_user.id, scope, db_group, lang=get_lang(db_user))


@router.callback_query(NavCb.filter(F.target == "ranking"))
async def cb_ranking_nav(callback: CallbackQuery, db_user: User, db_group: Optional[Group] = None) -> None:
    scope = "group" if db_group is not None else "global"
    await _show_ranking(callback, callback.from_user.id, scope, db_group, lang=get_lang(db_user))


@router.callback_query(RankingCb.filter())
async def cb_ranking_switch(
    callback: CallbackQuery,
    callback_data: RankingCb,
    db_user: User,
    db_group: Optional[Group] = None,
) -> None:
    await _show_ranking(callback, callback.from_user.id, callback_data.scope, db_group, callback_data.period, lang=get_lang(db_user))
