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


def _row(rank: int, name: str, full: str, short: str, me: bool = False) -> str:
    """Reyting qatori: 1-o'rin alohida ajralib turadi, 2-3 oddiyroq, qolganlari bir xil ixcham."""
    tag = "  👈 <b>Siz</b>" if me else ""
    if rank == 1:
        return (
            f"<blockquote>👑 <b>1-O‘RIN</b> 👑\n"
            f"✨ <b>{name}</b> ✨{tag}\n"
            f"{full}</blockquote>"
        )
    if rank == 2:
        return f"🥈 <b>2.</b> <b>{name}</b>{tag}\n      {full}\n"
    if rank == 3:
        return f"🥉 <b>3.</b> <b>{name}</b>{tag}\n      {full}\n"
    return f"<b>{rank}.</b> {name} — {short}{tag}"


def _join_rows(rows: list[str]) -> str:
    out: list[str] = []
    for i, r in enumerate(rows):
        out.append(r)
    return "\n".join(out)


def _group_title_html(g: Group) -> str:
    title = esc(g.title)
    if getattr(g, "username", None):
        return f'<a href="https://t.me/{esc(g.username)}">{title}</a>'
    return title


def _footer_rank(mine: Optional[int]) -> str:
    if mine == 0:
        return "\n📍 Siz: 🛡 <b>Admin (reytingdan tashqari)</b>"
    if mine is None:
        return ""
    if mine == 1:
        return "\n📍 Siz hozir <b>1-o‘rindasiz</b> 👑 — o‘rningizni saqlab qoling!"
    return f"\n📍 Sizning o‘rningiz: <b>#{mine}</b>\n🚀 1-o‘ringa chiqish uchun harakat qiling!"


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
            lines.append("<i>Faol a’zolar va olingan kartalar bo‘yicha. Guruh nomini bossangiz, guruhga o‘tasiz.</i>\n")
            rows = []
            for g in grp_entries:
                name = _group_title_html(g.group)
                full = f"👤 <b>{g.active_users}</b> faol · 🎴 <b>{g.claimed_cards}</b> karta · ⭐ <b>{g.score}</b>"
                short = f"👤 {g.active_users} · 🎴 {g.claimed_cards} · ⭐ {g.score}"
                is_me = db_group is not None and g.group.id == db_group.id
                rows.append(_row(g.rank, name, full, short, is_me))
            lines.append(_join_rows(rows))
            if standing is not None and standing.rank > 10:
                lines.append(f"\n📍 Sizning guruhingiz: <b>#{standing.rank}</b> · ⭐ {standing.score}")
        await edit_or_answer(target, "\n".join(lines), reply_markup=kb, disable_web_page_preview=True)
        return

    # ---------- eng kuchli kolleksiya ----------
    if scope == "power":
        from services.card_level_service import CardLevelService

        async with get_session() as session:
            rows_p = await CardLevelService.top_power(session, limit=10)
        lines = ["💪 <b>ENG KUCHLI KOLLEKSIYA</b>", f"{LINE}\n<i>Kartalarning rank va darajasi bo‘yicha umumiy kuch</i>\n"]
        if not rows_p:
            lines.append("📭 Hozircha ma’lumot yo‘q.")
        else:
            rows = []
            for idx, (u, val) in enumerate(rows_p, start=1):
                is_me = db_user is not None and u.id == db_user.id
                rows.append(_row(idx, esc(u.display_name), f"💪 <b>{fmt_coins(val)}</b> kuch", f"💪 {fmt_coins(val)}", is_me))
            lines.append(_join_rows(rows))
        await edit_or_answer(target, "\n".join(lines), reply_markup=kb)
        return

    # ---------- maxsus reytinglar ----------
    if scope in titles:
        hdr, unit = titles[scope]
        async with get_session() as session:
            rows_e = await RankingService.get_extended_top(session, scope, period=period, limit=10)
        lines = [f"<b>{hdr}</b>", f"{LINE}\n🗓 {PERIOD_LABELS.get(period, period)}\n"]
        if not rows_e:
            lines.append("📭 Hozircha ma’lumot yo‘q.")
        else:
            rows = []
            for idx, (u, val) in enumerate(rows_e, start=1):
                is_me = db_user is not None and u.id == db_user.id
                if scope == "coins":
                    full, short = f"🪙 <b>{fmt_coins(val)}</b> coin", f"🪙 {fmt_coins(val)}"
                elif scope == "xp":
                    lvl = max(1, int(getattr(u, "level", 1) or 1))
                    full = f"⚡ <b>Lv.{lvl}</b> · {fmt_coins(val)} XP"
                    short = f"⚡ Lv.{lvl} · {fmt_coins(val)} XP"
                else:
                    full, short = f"<b>{fmt_coins(val)}</b> {unit}", f"{fmt_coins(val)} {unit}"
                rows.append(_row(idx, esc(u.display_name), full, short, is_me))
            lines.append(_join_rows(rows))
        await edit_or_answer(target, "\n".join(lines), reply_markup=kb)
        return

    # ---------- global / guruh ----------
    async with get_session() as session:
        if scope == "group" and db_group is not None:
            entries = await RankingService.get_group_top(session, db_group.id, limit=10)
            header = f"👥 <b>GURUH REYTINGI</b>\n🏷 {_group_title_html(db_group)}"
        else:
            entries = await RankingService.get_global_top(session, limit=10)
            header = "🌍 <b>GLOBAL REYTING</b>"
        my_global = my_group = None
        if db_user is not None:
            my_global, my_group = await RankingService.get_user_ranks(
                session=session, user_id=db_user.id, group_id=db_group.id if db_group else None
            )

    r5_name = get_rank_name_by_order(5, "Mythic")
    r6_name = get_rank_name_by_order(6, "Divine")
    m5 = get_rarity_meta(r5_name)
    m6 = get_rarity_meta(r6_name)

    lines = [header, LINE]
    if not entries:
        lines.append("📭 Hozircha reyting ma’lumotlari yo‘q.")
    else:
        lines.append(f"<i>🏅 Kollektsiya qiymati bo‘yicha</i>\n🎴 jami karta · {m5.emoji} {esc(r5_name)} · {m6.emoji} {esc(r6_name)}\n")
        rows = []
        for e in entries:
            is_me = db_user is not None and e.user.id == db_user.id
            full = (
                f"🎴 <b>{e.total_cards}</b> karta · {m5.emoji} <b>{e.mythic_cards}</b> {esc(r5_name)} · "
                f"{m6.emoji} <b>{e.divine_cards}</b> {esc(r6_name)}"
            )
            short = f"🎴 {e.total_cards} · {m5.emoji} {e.mythic_cards} · {m6.emoji} {e.divine_cards}"
            rows.append(_row(e.rank, esc(e.user.display_name), full, short, is_me))
        lines.append(_join_rows(rows))
    mine = my_group if scope == "group" else my_global
    lines.append(_footer_rank(mine))
    await edit_or_answer(target, "\n".join(lines), reply_markup=kb, disable_web_page_preview=True)


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
