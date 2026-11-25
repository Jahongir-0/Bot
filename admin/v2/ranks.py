"""Rank (nodirlik darajalari) boshqaruvi."""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from admin.filters import IsAdminFilter
from admin.v2.common import AdminV2States, _cancel_kb, _deny_if_no_perm
from database.session import get_session
from keyboards.v2 import admin_rank_detail_kb, admin_ranks_list_kb
from services.gameplay_service import GameplayService
from utils.callback_data import AdmV2Cb
from utils.formatting import esc
from utils.tg_helpers import NotCommandFilter, edit_or_answer

logger = logging.getLogger(__name__)
router = Router(name="admin_v2_ranks")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


# ===========================================================================
# 5. RANKS MANAGER (sec="ranks", Zero-pipe step-by-step + Normalize)
# ===========================================================================
async def _show_ranks_home(target, owner_id: int) -> None:
    async with get_session() as session:
        ranks = await GameplayService.get_rank_definitions(session)
    total_p = sum(int(r.get("drop_probability", 0)) for r in ranks)
    warn = " ✅" if total_p == 100 else " ⚠️ (100% bo‘lishi kerak!)"
    txt = f"⭐ <b>RANKLAR TIZIMI</b>\nJami ehtimollik: <b>{total_p}%</b>{warn}\n\nTahrirlash uchun rank ustiga bosing:"
    await edit_or_answer(target, txt, reply_markup=admin_ranks_list_kb(owner_id, ranks, total_p))


async def _show_rank_detail(target, owner_id: int, rank_idx: int) -> None:
    async with get_session() as session:
        ranks = await GameplayService.get_rank_definitions(session)
    if not (0 <= rank_idx < len(ranks)):
        await _show_ranks_home(target, owner_id)
        return
    r = ranks[rank_idx]
    val = int(r.get("base_value", 0) or 0)
    txt = (
        f"{r.get('emoji', '⭐')} <b>RANK: {esc(r['name'])}</b>\n\n"
        f"• Emoji: {r.get('emoji', '⭐')}\n"
        f"• Tushish ehtimoli: <b>{r.get('drop_probability', 0)}%</b>\n"
        f"• Bir marta yaratilganda guruhga tushish soni: <b>{r.get('drop_limit', 1)} ta</b>\n"
        f"• Karta narxi (botga sotish & daraja asosi): <b>{val:,} 🪙</b>"
    )
    await edit_or_answer(target, txt, reply_markup=admin_rank_detail_kb(owner_id, rank_idx))


@router.callback_query(AdmV2Cb.filter(F.sec == "ranks"))
async def cb_v2_ranks(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "ranks"):
        return
    act = callback_data.act
    owner_id = callback.from_user.id

    if act == "v":
        await state.clear()
        await _show_ranks_home(callback, owner_id)
        return

    if act == "det":
        await state.clear()
        await _show_rank_detail(callback, owner_id, callback_data.tid)
        return

    if act == "norm":
        async with get_session() as session:
            await GameplayService.normalize_rank_probabilities(session)
            await session.commit()
        await callback.answer("⚖️ Ehtimolliklar 100% ga moslandi!", show_alert=True)
        await _show_ranks_home(callback, owner_id)
        return

    if act == "ef":
        prompts = {
            "name": "✏️ Yangi rank nomini yozing:",
            "emoji": "😀 Yangi emoji yuboring (masalan: 🌟):",
            "prob": "📊 Yangi drop ehtimolligini yozing (0–100):",
            "drops": "🔁 Guruhga nechta tushish sonini (drop limit) yozing:",
            "value": "💎 Yangi karta narxini (coin) kiriting (masalan: <code>10000</code>):",
        }
        await state.set_state(AdminV2States.waiting_rank_field)
        await state.update_data(owner_tg_id=owner_id, rank_idx=callback_data.tid, rank_field=callback_data.ext)
        await edit_or_answer(callback, prompts.get(callback_data.ext, "Yangi qiymat:"), reply_markup=_cancel_kb(owner_id, "ranks", "v"))
        return

    if act == "del":
        async with get_session() as session:
            ranks = await GameplayService.get_rank_definitions(session)
            if len(ranks) <= 1 or not (0 <= callback_data.tid < len(ranks)):
                await callback.answer("⚠️ Kamida 1 ta rank qolishi shart.", show_alert=True)
                return
            await GameplayService.delete_rank(session, str(ranks[callback_data.tid]["name"]))
            await session.commit()
        await callback.answer("🗑 Rank o‘chirildi!", show_alert=True)
        await _show_ranks_home(callback, owner_id)
        return

    if act == "add":
        await state.set_state(AdminV2States.waiting_rank_add_name)
        await state.update_data(owner_tg_id=owner_id)
        await edit_or_answer(callback, "➕ <b>YANGI RANK (1/4)</b>\n\nRank nomini yozing (masalan: <code>Celestial</code>):", reply_markup=_cancel_kb(owner_id, "ranks", "v"))


@router.message(AdminV2States.waiting_rank_field, NotCommandFilter())
async def fsm_v2_rank_field(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    idx = int(data["rank_idx"])
    field = data["rank_field"]
    raw = (message.text or "").strip()
    try:
        async with get_session() as session:
            ranks = await GameplayService.get_rank_definitions(session)
            if not (0 <= idx < len(ranks)):
                await state.clear()
                await message.answer("❌ Rank topilmadi.")
                return
            r_name = str(ranks[idx]["name"])
            if field == "name":
                await GameplayService.update_single_rank_attr(session, r_name, new_name=raw)
            elif field == "emoji":
                await GameplayService.update_single_rank_attr(session, r_name, emoji=raw)
            elif field == "prob":
                if not raw.isdigit():
                    await message.answer("⚠️ 0 dan 100 gacha son kiriting.")
                    return
                await GameplayService.update_single_rank_attr(session, r_name, drop_probability=int(raw))
            elif field == "drops":
                if not raw.isdigit() or int(raw) < 1:
                    await message.answer("⚠️ Kamida 1 butun son kiriting.")
                    return
                await GameplayService.update_single_rank_attr(session, r_name, drop_limit=int(raw))
            elif field == "value":
                cleaned = raw.replace(",", "").replace(" ", "")
                if not cleaned.isdigit() or not (1 <= int(cleaned) <= 100_000_000):
                    await message.answer("⚠️ 1 dan 100,000,000 gacha butun coin narxi kiriting.")
                    return
                await GameplayService.update_single_rank_attr(session, r_name, base_value=int(cleaned))
            await session.commit()
    except ValueError as exc:
        await message.answer(f"⚠️ {esc(str(exc))}")
        return
    await state.clear()
    await _show_rank_detail(message, message.from_user.id, idx)


@router.message(AdminV2States.waiting_rank_add_name, NotCommandFilter())
async def fsm_v2_rank_add_name(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id:
        return
    name = (message.text or "").strip()[:32]
    if not name:
        await message.answer("⚠️ Rank nomini yozing.")
        return
    await state.update_data(new_rank_name=name)
    await state.set_state(AdminV2States.waiting_rank_add_emoji)
    await message.answer("2/4: Rank <b>emojisi</b>ni yuboring (masalan: 🌟):")


@router.message(AdminV2States.waiting_rank_add_emoji, NotCommandFilter())
async def fsm_v2_rank_add_emoji(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id:
        return
    emoji = (message.text or "⭐").strip()[:8]
    await state.update_data(new_rank_emoji=emoji)
    await state.set_state(AdminV2States.waiting_rank_add_prob)
    await message.answer("3/4: Tushish ehtimolini (%) yozing (masalan: <code>5</code>):")


@router.message(AdminV2States.waiting_rank_add_prob, NotCommandFilter())
async def fsm_v2_rank_add_prob(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("⚠️ Son kiriting.")
        return
    await state.update_data(new_rank_prob=int(raw))
    await state.set_state(AdminV2States.waiting_rank_add_drops)
    await message.answer("4/4: Guruhga necha marta tashlanish sonini (drop limit) yozing (masalan: <code>1</code>):")


@router.message(AdminV2States.waiting_rank_add_drops, NotCommandFilter())
async def fsm_v2_rank_add_drops(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) < 1:
        await message.answer("⚠️ Kamida 1 son kiriting.")
        return
    await state.clear()
    try:
        async with get_session() as session:
            await GameplayService.add_rank_flexible(
                session,
                name=data["new_rank_name"],
                emoji=data["new_rank_emoji"],
                drop_probability=int(data["new_rank_prob"]),
                drop_limit=int(raw),
            )
            await session.commit()
    except ValueError as exc:
        await message.answer(f"⚠️ {esc(str(exc))}")
        return
    await _show_ranks_home(message, message.from_user.id)
