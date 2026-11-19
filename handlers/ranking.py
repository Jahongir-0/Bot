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
from utils.tg_helpers import edit_or_answer

router = Router(name="ranking")

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
LINE = "━━━━━━━━━━━━━━━"

EXTENDED_TITLES = {
    "coins": ("🪙 ENG BOY O‘YINCHILAR", "coin"),
    "xp": ("⚡ ENG YUQORI DARAJA", "XP"),
    "quiz": ("🧠 VIKTORINA CHEMPIONLARI", "g‘alaba"),
    "games": ("🎲 O‘YIN USTALARI", "yutuq"),
    "trades": ("🤝 ENG FAOL SAVDOGARLAR", "savdo"),
    "auction": ("🔨 AUKSION G‘OLIBLARI", "g‘alaba"),
    "refs": ("👥 ENG KO‘P DO‘ST TAKLIF QILGANLAR", "do‘st"),
    "rare": ("💎 NOYOB KARTALAR KOLLEKSIONERLARI", "karta"),
    "mythic": ("🔴 MYTHIC KOLLEKSIONERLAR", "karta"),
    "divine": ("✨ DIVINE KOLLEKSIONERLAR", "karta"),
}
PERIOD_LABELS = {"all": "barcha vaqt", "monthly": "shu oy", "weekly": "shu hafta"}


def _prefix(rank: int) -> str:
    return MEDALS.get(rank, f"<b>{rank}.</b>")


async def _show_ranking(
    target,
    owner_id: int,
    scope: str,
    db_group: Optional[Group],
    db_user: Optional[User] = None,
    period: str = "all",
) -> None:
    in_group = db_group is not None
    if scope == "group" and not in_group:
        scope = "global"
    kb = ranking_kb(owner_id=owner_id, current_scope=scope, in_group=in_group, period=period)

    # ---------- guruhlar reytingi ----------
    if scope == "groups":
        async with get_session() as session:
            grp_entries = await RankingService.get_groups_top(session, limit=10)
            standing = await RankingService.get_group_standing(session, db_group.id) if db_group is not None else None
        lines = ["🏰 <b>GURUHLAR REYTINGI</b>", LINE]
        if not grp_entries:
            lines.append("📭 Hozircha faol guruhlar yo‘q.")
        else:
            lines.append("<i>Faol a’zolar va olingan kartalar bo‘yicha</i>\n")
            for g in grp_entries:
                lines.append(f"{_prefix(g.rank)} <b>{esc(g.group.title)}</b>")
                lines.append(f"      👤 {g.active_users} faol · 🎴 {g.claimed_cards} karta · ⭐ {g.score}")
            if standing is not None and standing.rank > 10:
                lines.append(f"\n📍 Sizning guruhingiz: <b>#{standing.rank}</b> · ⭐ {standing.score}")
        await edit_or_answer(target, "\n".join(lines), reply_markup=kb)
        return

    # ---------- maxsus reytinglar ----------
    if scope in EXTENDED_TITLES:
        hdr, unit = EXTENDED_TITLES[scope]
        async with get_session() as session:
            rows = await RankingService.get_extended_top(session, scope, period=period, limit=10)
        lines = [f"<b>{hdr}</b>", f"{LINE}\n🗓 {PERIOD_LABELS.get(period, period)}\n"]
        if not rows:
            lines.append("📭 Hozircha ma’lumot yo‘q.")
        else:
            for idx, (u, val) in enumerate(rows, start=1):
                me = " 👈" if db_user is not None and u.id == db_user.id else ""
                lines.append(f"{_prefix(idx)} {esc(u.display_name)} — <b>{fmt_coins(val)}</b> {unit}{me}")
        await edit_or_answer(target, "\n".join(lines), reply_markup=kb)
        return

    # ---------- global / guruh ----------
    async with get_session() as session:
        if scope == "group" and db_group is not None:
            entries = await RankingService.get_group_top(session, db_group.id, limit=10)
            header = f"👥 <b>GURUH REYTINGI</b>\n🏷 {esc(db_group.title)}"
        else:
            entries = await RankingService.get_global_top(session, limit=10)
            header = "🌍 <b>GLOBAL REYTING</b>"
        my_global = my_group = None
        if db_user is not None:
            my_global, my_group = await RankingService.get_user_ranks(
                session=session, user_id=db_user.id, group_id=db_group.id if db_group else None
            )

    lines = [header, LINE]
    if not entries:
        lines.append("📭 Hozircha reyting ma’lumotlari yo‘q.")
    else:
        lines.append("<i>Noyob kartalar soni bo‘yicha</i>\n")
        for e in entries:
            me = " 👈" if db_user is not None and e.user.id == db_user.id else ""
            lines.append(f"{_prefix(e.rank)} <b>{esc(e.user.display_name)}</b>{me}")
            lines.append(f"      ✨ {e.unique_cards} noyob · 🎴 {e.total_cards} jami · 🟡 {e.legendary_cards} · 🔴 {e.mythic_cards}")
    mine = my_group if scope == "group" else my_global
    if mine is not None:
        lines.append(f"\n📍 Sizning o‘riningiz: <b>#{mine}</b>")
    await edit_or_answer(target, "\n".join(lines), reply_markup=kb)


@router.message(Command("top"))
async def cmd_top(message: Message, db_user: User, db_group: Optional[Group] = None) -> None:
    scope = "group" if db_group is not None else "global"
    await _show_ranking(message, message.from_user.id, scope, db_group, db_user)


@router.callback_query(NavCb.filter(F.target == "ranking"))
async def cb_ranking_nav(callback: CallbackQuery, db_user: User, db_group: Optional[Group] = None) -> None:
    scope = "group" if db_group is not None else "global"
    await _show_ranking(callback, callback.from_user.id, scope, db_group, db_user)
    await callback.answer()


@router.callback_query(RankingCb.filter())
async def cb_ranking_switch(
    callback: CallbackQuery,
    callback_data: RankingCb,
    db_user: User,
    db_group: Optional[Group] = None,
) -> None:
    await _show_ranking(callback, callback.from_user.id, callback_data.scope, db_group, db_user, callback_data.period)
    await callback.answer()
