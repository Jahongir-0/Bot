from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database.session import get_session
from keyboards.market import ranking_kb
from models.group import Group
from services.ranking_service import RankingService
from utils.callback_data import NavCb, RankingCb
from utils.formatting import esc
from utils.tg_helpers import edit_or_answer

router = Router(name="ranking")


async def _show_ranking(
    target,
    owner_id: int,
    scope: str,
    db_group: Optional[Group],
) -> None:
    in_group = db_group is not None
    if scope == "group" and not in_group:
        scope = "global"

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
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
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
        reply_markup=ranking_kb(owner_id=owner_id, current_scope=scope, in_group=in_group),
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
    await _show_ranking(callback, callback.from_user.id, callback_data.scope, db_group)
