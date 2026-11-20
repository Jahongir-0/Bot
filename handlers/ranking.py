from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database.session import get_session
from keyboards.market import ranking_kb
from models.group import Group
from models.user import User
from services.gameplay_service import GameplayService
from services.ranking_service import RankingService
from utils.callback_data import NavCb, RankingCb
from utils.constants import get_rank_name_by_order, get_rarity_meta, sync_runtime_ranks
from utils.formatting import esc, fmt_coins
from utils.tg_helpers import edit_or_answer

router = Router(name="ranking")

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
LINE = "━━━━━━━━━━━━━━━"


def _extended_titles() -> dict[str, tuple[str, str]]:
    r5 = get_rank_name_by_order(5, "Mythic")
    r6 = get_rank_name_by_order(6, "Divine")
    m5 = get_rarity_meta(r5)
    m6 = get_rarity_meta(r6)
    return {
        "coins": ("🪙 ENG BOY O‘YINCHILAR", "coin"),
        "xp": ("⚡ ENG YUQORI DARAJA", "XP"),
        "quiz": ("🧠 VIKTORINA CHEMPIONLARI", "g‘alaba"),
        "games": ("🎲 O‘YIN USTALARI", "yutuq"),
        "trades": ("🤝 ENG FAOL SAVDOGARLAR", "savdo"),
        "auction": ("🔨 AUKSION G‘OLIBLARI", "g‘alaba"),
        "refs": ("👥 ENG KO‘P DO‘ST TAKLIF QILGANLAR", "do‘st"),
        "rare": ("⭐ YUQORI RANK KARTALARI KOLLEKSIONERLARI", "karta"),
        "mythic": (f"{m5.emoji} {r5.upper()} KOLLEKSIONERLAR", "karta"),
        "divine": (f"{m6.emoji} {r6.upper()} KOLLEKSIONERLAR", "karta"),
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

    async with get_session() as session:
        ranks = await GameplayService.get_rank_definitions(session)
        sync_runtime_ranks(ranks)

    kb = ranking_kb(owner_id=owner_id, current_scope=scope, in_group=in_group, period=period)
    titles = _extended_titles()

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

    # ---------- eng kuchli kolleksiya ----------
    if scope == "power":
        from services.card_level_service import CardLevelService

        async with get_session() as session:
            rows = await CardLevelService.top_power(session, limit=10)
        lines = ["💪 <b>ENG KUCHLI KOLLEKSIYA</b>", f"{LINE}\n<i>Kartalarning rank va darajasi bo‘yicha umumiy kuch</i>\n"]
        if not rows:
            lines.append("📭 Hozircha ma’lumot yo‘q.")
        else:
            for idx, (u, val) in enumerate(rows, start=1):
                me = " 👈" if db_user is not None and u.id == db_user.id else ""
                lines.append(f"{_prefix(idx)} {esc(u.display_name)} — <b>{fmt_coins(val)}</b> 💪{me}")
        await edit_or_answer(target, "\n".join(lines), reply_markup=kb)
        return

    # ---------- maxsus reytinglar ----------
    if scope in titles:
        hdr, unit = titles[scope]
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

    r4_meta = get_rarity_meta(get_rank_name_by_order(4, "Legend"))
    r5_meta = get_rarity_meta(get_rank_name_by_order(5, "Mythic"))

    lines = [header, LINE]
    if not entries:
        lines.append("📭 Hozircha reyting ma’lumotlari yo‘q.")
    else:
        lines.append("<i>Noyob kartalar soni bo‘yicha</i>\n")
        for e in entries:
            me = " 👈" if db_user is not None and e.user.id == db_user.id else ""
            lines.append(f"{_prefix(e.rank)} <b>{esc(e.user.display_name)}</b>{me}")
            lines.append(
                f"      ✨ {e.unique_cards} noyob · 🎴 {e.total_cards} jami · "
                f"{r4_meta.emoji} {e.legendary_cards} · {r5_meta.emoji} {e.mythic_cards}"
            )
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
