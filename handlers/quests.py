from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.session import get_session
from models.user import User
from services.quest_service import QuestService
from utils.callback_data import NavCb, QuestCb
from utils.formatting import esc, fmt_coins
from utils.tg_helpers import edit_or_answer

router = Router(name="quests")


async def _show_quests(target, owner_id: int, db_user: User) -> None:
    async with get_session() as session:
        fresh = await session.get(User, db_user.id)
        items = await QuestService.get_user_quests(session, fresh or db_user)

    claimable_coins = sum(q.reward for q in items if q.completed and not q.claimed)
    lines = ["📋 <b>VAZIFALAR VA MUKOFOTLAR</b>\n"]
    for q in items:
        if q.claimed:
            st = "✅ Olingan"
        elif q.completed:
            st = f"🎁 Tayyor! (+{fmt_coins(q.reward)} 🪙)"
        else:
            st = f"⏳ {q.current}/{q.target} · Mukofot: {fmt_coins(q.reward)} 🪙"
        lines.append(f"<b>{esc(q.title)}</b>\n<i>{esc(q.desc)}</i>\n{st}\n")

    if claimable_coins > 0:
        lines.append(f"🪙 Olishga tayyor jami mukofot: <b>{fmt_coins(claimable_coins)} coins</b>!")

    builder = InlineKeyboardBuilder()
    if claimable_coins > 0:
        builder.row(
            InlineKeyboardButton(
                text=f"🎁 MUKOFOTLARNI OLISH (+{fmt_coins(claimable_coins)} 🪙)",
                callback_data=QuestCb(owner_id=owner_id, action="claim_all").pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=NavCb(owner_id=owner_id, target="profile").pack()),
        InlineKeyboardButton(text="🏠 BOSH MENU", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    await edit_or_answer(target, "\n".join(lines), reply_markup=builder.as_markup())


@router.message(Command("quests", "vazifalar"))
async def cmd_quests(message: Message, db_user: User) -> None:
    await _show_quests(message, message.from_user.id, db_user)


@router.callback_query(NavCb.filter(F.target == "quests"))
@router.callback_query(QuestCb.filter(F.action == "view"))
async def cb_quests_view(callback: CallbackQuery, db_user: User) -> None:
    await _show_quests(callback, callback.from_user.id, db_user)


@router.callback_query(QuestCb.filter(F.action == "claim_all"))
async def cb_quests_claim_all(callback: CallbackQuery, db_user: User) -> None:
    async with get_session() as session:
        coins, titles = await QuestService.claim_all_quests(session, db_user.id)
        if coins > 0:
            await session.commit()
        else:
            await session.rollback()

    if coins <= 0:
        await callback.answer("Hozircha olishga tayyor vazifa mukofoti yo‘q.", show_alert=True)
    else:
        await callback.answer(
            f"🎉 {len(titles)} ta vazifa uchun +{fmt_coins(coins)} 🪙 oldingiz!",
            show_alert=True,
        )
    await _show_quests(callback, callback.from_user.id, db_user)
