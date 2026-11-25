"""Viktorina boshqaruvi: yaratish, tasdiqlash, jadval, klonlash."""

import asyncio
import logging
import re
from datetime import datetime
from typing import List

from aiogram import F, Router
from aiogram.exceptions import TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from admin.filters import IsAdminFilter
from admin.v2.common import AdminV2States, _back_home_btn, _cancel_kb, _deny_if_no_perm, _parse_local_dt
from database.session import get_session
from models.group import Group
from models.user import User
from models.v2_features import Quiz
from services.gameplay_service import GameplayService
from services.quiz_announce import build_quiz_announcement
from utils.callback_data import AdmV2Cb
from utils.formatting import esc
from utils.tg_helpers import NotCommandFilter, edit_or_answer, get_bot_username
from utils.timeutils import fmt_tashkent

logger = logging.getLogger(__name__)
router = Router(name="admin_v2_quiz")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())


# ===========================================================================
# 6. QUIZ MANAGER (Zero-pipe Builder + Reuse/Clone + Schedule + Group/All Target)
# ===========================================================================
async def _dispatch_quiz_announcement(bot, session, quiz: Quiz) -> None:
    qs_res = await GameplayService.get_quiz_with_questions(session, quiz.id)
    bot_un = await get_bot_username(bot)
    text, markup = build_quiz_announcement(quiz, len(qs_res[1]) if qs_res else 0, bot_un)
    chat_ids: List[int] = []
    if quiz.target_scope == "group" and quiz.target_group_id:
        grp = await session.get(Group, quiz.target_group_id)
        if grp and grp.is_active:
            chat_ids.append(grp.telegram_chat_id)
    else:
        g_ids = (await session.execute(select(Group.telegram_chat_id).where(Group.is_active.is_(True), Group.is_banned.is_(False)))).scalars().all()
        u_ids = (await session.execute(select(User.telegram_id).where(User.is_banned.is_(False)))).scalars().all()
        chat_ids.extend(int(x) for x in g_ids)
        chat_ids.extend(int(x) for x in u_ids)

    sem = asyncio.Semaphore(6)

    async def _send(cid: int) -> None:
        async with sem:
            try:
                await bot.send_message(cid, text, reply_markup=markup)
            except TelegramRetryAfter as exc:
                await asyncio.sleep(float(exc.retry_after) + 0.5)
            except Exception:
                pass

    if chat_ids:
        await asyncio.gather(*[_send(c) for c in chat_ids])


async def _show_quiz_home(target, owner_id: int) -> None:
    async with get_session() as session:
        quizzes = await GameplayService.list_all_quizzes(session, limit=15)
    lines = ["🧠 <b>QUIZ BOSHQARUVI</b>\n"]
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="➕ Yangi Quiz yaratish", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="new").pack()))
    for q in quizzes:
        st = "⏰" if q.status == "scheduled" else ("🟢" if q.is_active else "⚪")
        lines.append(f"{st} <b>#{q.id} {esc(q.title)}</b> — {q.question_count} savol · {q.reward_coins:,} 🪙")
        b.row(InlineKeyboardButton(text=f"{st} #{q.id} {q.title[:26]}", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="det", tid=q.id).pack()))
    if not quizzes:
        lines.append("📭 Hozircha quizlar yo‘q.")
    b.row(_back_home_btn(owner_id))
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


@router.callback_query(AdmV2Cb.filter(F.sec == "quiz"))
async def cb_v2_quiz(callback: CallbackQuery, callback_data: AdmV2Cb, state: FSMContext, admin_perms: set | None = None) -> None:
    if await _deny_if_no_perm(callback, admin_perms, "quiz"):
        return
    act = callback_data.act
    owner_id = callback.from_user.id

    if act == "v":
        await state.clear()
        await _show_quiz_home(callback, owner_id)
        return

    if act == "new":
        await state.set_state(AdminV2States.waiting_quiz_title)
        await state.update_data(owner_tg_id=owner_id, quiz_questions=[])
        await edit_or_answer(callback, "🧠 <b>YANGI QUIZ (1/4)</b>\n\nQuiz nomini yozing (masalan: <code>Naruto Viktorinasi</code>):", reply_markup=_cancel_kb(owner_id, "quiz", "v"))
        return

    if act == "coins_p":
        await state.update_data(quiz_coins=callback_data.tid)
        await state.set_state(AdminV2States.waiting_quiz_q_text)
        await edit_or_answer(callback, f"🪙 Mukofot: <b>{callback_data.tid:,}</b> coin.\n\nEndi <b>1-savol matnini</b> yozing:", reply_markup=_cancel_kb(owner_id, "quiz", "v"))
        return

    if act == "q_ans":
        data = await state.get_data()
        qs = list(data.get("quiz_questions") or [])
        opts = list(data.get("cur_q_opts") or [])
        q_text = data.get("cur_q_text", "")
        cidx = max(0, min(len(opts) - 1, callback_data.tid))
        qs.append({"q": q_text, "opts": opts, "cidx": cidx, "acc": [opts[cidx]] if opts else []})
        await state.update_data(quiz_questions=qs)
        await state.set_state(None)
        b = InlineKeyboardBuilder()
        b.row(
            InlineKeyboardButton(text="➕ Yana savol qo‘shish", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="q_more").pack()),
            InlineKeyboardButton(text="✅ Savollar tayyor", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="q_done").pack()),
        )
        await edit_or_answer(callback, f"✅ Savol #{len(qs)} saqlandi!\nYana savol qo‘shasizmi?", reply_markup=b.as_markup())
        return

    if act == "q_more":
        data = await state.get_data()
        await state.set_state(AdminV2States.waiting_quiz_q_text)
        await edit_or_answer(callback, f"❓ <b>{len(data.get('quiz_questions', [])) + 1}-savol matnini</b> yozing:", reply_markup=_cancel_kb(owner_id, "quiz", "v"))
        return

    if act == "q_done":
        # Qayerga/qachon allaqachon tanlangan bo'lsa (tasdiqlash ekranidan qo'shimcha savol qo'shilganda) — to'g'ri tasdiqlashga
        if (await state.get_data()).get("quiz_when"):
            await state.set_state(None)
            await _show_quiz_review(callback, state, owner_id)
            return
        act = "q_scope"

    if act == "q_scope":
        b = InlineKeyboardBuilder()
        b.row(InlineKeyboardButton(text="🌍 Barcha guruh & userlarga", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="scope_all").pack()))
        b.row(InlineKeyboardButton(text="👥 Aniq bitta guruhga", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="scope_grp").pack()))
        await edit_or_answer(callback, "📢 <b>QUIZ QAYERGA YUBORILSIN?</b>", reply_markup=b.as_markup())
        return

    if act == "scope_all":
        await state.update_data(quiz_scope="all", quiz_grp_id=None)
        b = InlineKeyboardBuilder()
        b.row(
            InlineKeyboardButton(text="⚡ Hozir boshlash", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="when_now").pack()),
            InlineKeyboardButton(text="⏰ Vaqtga belgilash", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="when_sched").pack()),
        )
        await edit_or_answer(callback, "⏰ <b>QACHON BOSHLANSIN?</b>", reply_markup=b.as_markup())
        return

    if act == "scope_grp":
        async with get_session() as session:
            groups = list((await session.execute(select(Group).where(Group.is_active.is_(True)).order_by(Group.id.desc()).limit(20))).scalars().all())
        b = InlineKeyboardBuilder()
        for g in groups:
            b.row(InlineKeyboardButton(text=f"👥 {g.title[:36]}", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="set_grp", tid=g.id).pack()))
        b.row(InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="q_scope").pack()))
        await edit_or_answer(callback, "👥 Qaysi guruhni tanlaysiz?", reply_markup=b.as_markup())
        return

    if act == "set_grp":
        await state.update_data(quiz_scope="group", quiz_grp_id=callback_data.tid)
        b = InlineKeyboardBuilder()
        b.row(
            InlineKeyboardButton(text="⚡ Hozir boshlash", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="when_now").pack()),
            InlineKeyboardButton(text="⏰ Vaqtga belgilash", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="when_sched").pack()),
        )
        await edit_or_answer(callback, "⏰ <b>QACHON BOSHLANSIN?</b>", reply_markup=b.as_markup())
        return

    if act == "when_sched":
        await state.set_state(AdminV2States.waiting_quiz_sched_dt)
        await edit_or_answer(callback, "⏰ Boshlanish vaqtini yozing (<code>2026-12-01 18:00</code> yoki <code>01.12.2026 18:00</code>):")
        return

    if act == "when_now":
        await state.update_data(quiz_when="now", quiz_sched_iso=None)
        await _show_quiz_review(callback, state, owner_id)
        return

    if act == "rv":
        await state.set_state(None)
        await _show_quiz_review(callback, state, owner_id)
        return

    if act == "rv_title":
        await state.set_state(AdminV2States.waiting_quiz_edit_title)
        await edit_or_answer(callback, "🏷 Quiz uchun <b>yangi nom</b> yozing:", reply_markup=_rv_back_kb(owner_id))
        return

    if act == "rv_coins":
        await state.set_state(AdminV2States.waiting_quiz_edit_coins)
        await edit_or_answer(callback, "🪙 To‘g‘ri javob uchun <b>yangi coin mukofotini</b> yozing (son):", reply_markup=_rv_back_kb(owner_id))
        return

    if act == "rv_q":
        await state.set_state(None)
        await _show_quiz_question_edit(callback, state, owner_id, callback_data.tid)
        return

    if act == "rv_qt":
        await state.set_state(AdminV2States.waiting_quiz_edit_q_text)
        await state.update_data(edit_idx=callback_data.tid)
        await edit_or_answer(callback, f"📝 <b>{callback_data.tid + 1}-savol</b> uchun yangi matnni yozing:", reply_markup=_rv_back_kb(owner_id, "rv_q", callback_data.tid))
        return

    if act == "rv_qo":
        await state.set_state(AdminV2States.waiting_quiz_edit_q_opts)
        await state.update_data(edit_idx=callback_data.tid)
        await edit_or_answer(
            callback,
            f"🔤 <b>{callback_data.tid + 1}-savol</b> uchun javob variantlarini vergul yoki yangi qator bilan yozing "
            "(masalan: <code>Naruto, Sasuke, Sakura</code>).\n<i>Bitta javob yozsangiz — ochiq javobli savol bo‘ladi.</i>",
            reply_markup=_rv_back_kb(owner_id, "rv_q", callback_data.tid),
        )
        return

    if act == "rv_qc":
        data = await state.get_data()
        qs = list(data.get("quiz_questions") or [])
        qi = int(callback_data.ext or 0)
        if 0 <= qi < len(qs):
            opts = list(qs[qi].get("opts") or [])
            ci = max(0, min(len(opts) - 1, callback_data.tid))
            qs[qi]["cidx"] = ci
            qs[qi]["acc"] = [opts[ci]] if opts else []
            await state.update_data(quiz_questions=qs)
        await _show_quiz_question_edit(callback, state, owner_id, qi)
        return

    if act == "rv_qd":
        data = await state.get_data()
        qs = list(data.get("quiz_questions") or [])
        if len(qs) <= 1:
            await callback.answer("Kamida 1 ta savol bo‘lishi kerak.", show_alert=True)
            return
        if 0 <= callback_data.tid < len(qs):
            qs.pop(callback_data.tid)
            await state.update_data(quiz_questions=qs)
        await _show_quiz_review(callback, state, owner_id)
        return

    if act == "rv_ok":
        data = await state.get_data()
        qs_items = list(data.get("quiz_questions") or [])
        if not qs_items:
            await callback.answer("Kamida 1 ta savol kerak.", show_alert=True)
            return
        when = data.get("quiz_when", "now")
        sched_dt = None
        if when == "sched":
            try:
                sched_dt = datetime.fromisoformat(str(data.get("quiz_sched_iso")))
            except (TypeError, ValueError):
                await callback.answer("Boshlanish vaqti noto‘g‘ri. Qayta belgilang.", show_alert=True)
                return
        await state.clear()
        async with get_session() as session:
            q = await GameplayService.create_quiz(
                session,
                title=data.get("quiz_title", "Quiz"),
                reward_coins=int(data.get("quiz_coins", 300)),
                admin_tg_id=owner_id,
                target_scope=data.get("quiz_scope", "all"),
                target_group_id=data.get("quiz_grp_id"),
                scheduled_at=sched_dt,
                status="scheduled" if sched_dt else "active",
            )
            for item in qs_items:
                await GameplayService.add_quiz_question(
                    session, q.id, item["q"], item["opts"], item["cidx"], accepted_answers=item.get("acc")
                )
            await session.commit()
            if not sched_dt:
                await _dispatch_quiz_announcement(callback.bot, session, q)
        if sched_dt:
            await callback.answer("⏰ Quiz tasdiqlandi va belgilangan vaqtga rejalashtirildi!", show_alert=True)
        else:
            await callback.answer("🎉 Quiz tasdiqlandi va yuborildi!", show_alert=True)
        await _show_quiz_home(callback, owner_id)
        return

    if act == "det":
        async with get_session() as session:
            res = await GameplayService.get_quiz_with_questions(session, callback_data.tid)
        if res is None:
            await callback.answer("Quiz topilmadi.", show_alert=True)
            return
        q, qs = res
        lines = [
            f"🧠 <b>QUIZ #{q.id}: {esc(q.title)}</b>\n",
            f"Holat: <b>{esc(q.status)}</b> ({'🟢 Faol' if q.is_active else '🔴 Yopiq'})",
            f"🪙 Mukofot: <b>{q.reward_coins:,}</b> coin · ⚡ {q.reward_xp} XP",
            f"❓ Savollar: <b>{len(qs)}</b> ta",
        ]
        for idx, qq in enumerate(qs[:10], 1):
            lines.append(f"{idx}. {esc(qq.question_text[:60])}")
        b = InlineKeyboardBuilder()
        b.row(
            InlineKeyboardButton(text="🔄 Yoqish/O‘chirish", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="tog", tid=q.id).pack()),
            InlineKeyboardButton(text="📋 Qayta o‘tkazish", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="clone", tid=q.id).pack()),
        )
        b.row(
            InlineKeyboardButton(text="🗑 O‘chirish", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="del", tid=q.id).pack()),
            InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="v").pack()),
        )
        await edit_or_answer(callback, "\n".join(lines), reply_markup=b.as_markup())
        return

    if act == "clone":
        await state.set_state(AdminV2States.waiting_quiz_clone_coins)
        await state.update_data(owner_tg_id=owner_id, clone_qid=callback_data.tid)
        b = InlineKeyboardBuilder()
        b.row(InlineKeyboardButton(text="⏭ Eski narx bilan boshlash", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="clone_go", tid=callback_data.tid).pack()))
        await edit_or_answer(callback, "📋 <b>QUIZNI QAYTA O‘TKAZISH</b>\n\nYangi coin mukofotini yozing yoki eski narx bilan boshlang:", reply_markup=b.as_markup())
        return

    if act == "clone_go":
        await state.clear()
        async with get_session() as session:
            nq = await GameplayService.clone_quiz(session, callback_data.tid, owner_id)
            if nq:
                await session.commit()
                await _dispatch_quiz_announcement(callback.bot, session, nq)
        await callback.answer("🎉 Quiz qayta boshlandi!", show_alert=True)
        await _show_quiz_home(callback, owner_id)
        return

    async with get_session() as session:
        q = await session.get(Quiz, callback_data.tid)
        if q:
            if act == "tog":
                q.is_active = not q.is_active
                q.status = "active" if q.is_active else "ended"
            elif act == "del":
                await session.delete(q)
            await session.commit()
    await _show_quiz_home(callback, owner_id)


@router.message(AdminV2States.waiting_quiz_title, NotCommandFilter())
async def fsm_v2_quiz_title(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id:
        return
    title = (message.text or "").strip()[:160]
    if not title:
        await message.answer("⚠️ Quiz nomini yozing.")
        return
    await state.update_data(quiz_title=title)
    await state.set_state(AdminV2States.waiting_quiz_coins)
    b = InlineKeyboardBuilder()
    for c in (200, 500, 1000, 2500):
        b.add(InlineKeyboardButton(text=f"🪙 {c:,}", callback_data=AdmV2Cb(owner_id=message.from_user.id, sec="quiz", act="coins_p", tid=c).pack()))
    b.adjust(2)
    await message.answer("2/4: To‘g‘ri javob uchun <b>coin mukofotini</b> tanlang yoki son yozing:", reply_markup=b.as_markup())


@router.message(AdminV2States.waiting_quiz_coins, NotCommandFilter())
async def fsm_v2_quiz_coins(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "")
    if not raw.isdigit():
        await message.answer("⚠️ Musbat son kiriting.")
        return
    await state.update_data(quiz_coins=int(raw))
    await state.set_state(AdminV2States.waiting_quiz_q_text)
    await message.answer("3/4: <b>1-savol matnini</b> yozing:")


@router.message(AdminV2States.waiting_quiz_q_text, NotCommandFilter())
async def fsm_v2_quiz_q_text(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id:
        return
    qtxt = (message.text or "").strip()
    if not qtxt:
        await message.answer("⚠️ Savol matnini yozing.")
        return
    await state.update_data(cur_q_text=qtxt)
    await state.set_state(AdminV2States.waiting_quiz_q_opts)
    await message.answer(
        "Javob variantlarini <b>vergul</b> yoki <b>yangi qator</b> bilan yozing (masalan: <code>Naruto, Sasuke, Sakura, Kakashi</code>).\n"
        "<i>Agar bitta to‘g‘ri javob yozsangiz, ochiq javobli savol bo‘ladi!</i>"
    )


@router.message(AdminV2States.waiting_quiz_q_opts, NotCommandFilter())
async def fsm_v2_quiz_q_opts(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip()
    opts = [x.strip() for x in re.split(r"[\n,;]+", raw) if x.strip()][:6]
    if not opts:
        await message.answer("⚠️ Kamida 1 ta javob yozing.")
        return
    if len(opts) == 1:
        qs = list(data.get("quiz_questions") or [])
        qs.append({"q": data.get("cur_q_text", ""), "opts": [opts[0]], "cidx": 0, "acc": [opts[0]]})
        await state.update_data(quiz_questions=qs)
        await state.set_state(None)
        b = InlineKeyboardBuilder()
        b.row(
            InlineKeyboardButton(text="➕ Yana savol qo‘shish", callback_data=AdmV2Cb(owner_id=message.from_user.id, sec="quiz", act="q_more").pack()),
            InlineKeyboardButton(text="✅ Savollar tayyor", callback_data=AdmV2Cb(owner_id=message.from_user.id, sec="quiz", act="q_done").pack()),
        )
        await message.answer(f"✅ Savol #{len(qs)} saqlandi (to‘g‘ri javob: <code>{esc(opts[0])}</code>)!", reply_markup=b.as_markup())
        return

    await state.update_data(cur_q_opts=opts)
    await state.set_state(None)
    b = InlineKeyboardBuilder()
    for idx, opt in enumerate(opts):
        b.row(InlineKeyboardButton(text=f"✅ {opt[:40]}", callback_data=AdmV2Cb(owner_id=message.from_user.id, sec="quiz", act="q_ans", tid=idx).pack()))
    await message.answer("👇 Qaysi variant <b>TO‘G‘RI JAVOB</b>?", reply_markup=b.as_markup())


@router.message(AdminV2States.waiting_quiz_sched_dt, NotCommandFilter())
async def fsm_v2_quiz_sched_dt(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    dt_utc = _parse_local_dt(message.text or "")
    if dt_utc is None:
        await message.answer("⚠️ Sana formati noto‘g‘ri. Misol: <code>2026-12-01 18:00</code>")
        return
    await state.update_data(quiz_when="sched", quiz_sched_iso=dt_utc.isoformat())
    await state.set_state(None)
    await _show_quiz_review(message, state, message.from_user.id)


# ---- Tasdiqlash (review) ekrani: admin hammasini tekshiradi va kerak bo'lsa tuzatadi ----
def _rv_back_kb(owner_id: int, act: str = "rv", tid: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Orqaga", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act=act, tid=tid).pack())]]
    )


async def _show_quiz_review(target, state: FSMContext, owner_id: int) -> None:
    data = await state.get_data()
    qs = list(data.get("quiz_questions") or [])
    scope = data.get("quiz_scope", "all")
    where = "🌍 Barcha guruh va foydalanuvchilarga"
    if scope == "group" and data.get("quiz_grp_id"):
        async with get_session() as session:
            grp = await session.get(Group, int(data["quiz_grp_id"]))
        where = f"👥 Guruh: {esc(grp.title) if grp else '—'}"
    if data.get("quiz_when") == "sched" and data.get("quiz_sched_iso"):
        try:
            when_txt = f"⏰ {fmt_tashkent(datetime.fromisoformat(str(data['quiz_sched_iso'])))} (Toshkent vaqti)"
        except ValueError:
            when_txt = "⏰ Vaqt noto‘g‘ri — qayta belgilang"
    else:
        when_txt = "⚡ Tasdiqlangach darhol boshlanadi"

    lines = [
        "📝 <b>VIKTORINANI TASDIQLASH</b>",
        "Hammasini diqqat bilan tekshiring. Xato bo‘lsa — pastdan o‘zgartiring, so‘ng <b>Tasdiqlash</b> ni bosing.",
        "━━━━━━━━━━━━━━━",
        f"🏷 Nomi: <b>{esc(data.get('quiz_title', 'Quiz'))}</b>",
        f"🪙 Mukofot: <b>{int(data.get('quiz_coins', 300)):,}</b> coin (har bir to‘g‘ri javobga)",
        f"📢 Qayerga: {where}",
        f"{when_txt}",
        "",
        f"❓ <b>Savollar ({len(qs)} ta):</b>",
    ]
    for i, item in enumerate(qs[:12], 1):
        lines.append(f"\n<b>{i}.</b> {esc(str(item.get('q', ''))[:140])}")
        opts = list(item.get("opts") or [])
        if len(opts) <= 1:
            lines.append(f"   ✅ To‘g‘ri javob (yozma): <code>{esc(opts[0] if opts else '—')}</code>")
        else:
            for oi, opt in enumerate(opts):
                mark = "✅" if oi == int(item.get("cidx", 0)) else "▫️"
                lines.append(f"   {mark} {esc(str(opt)[:60])}")
    if len(qs) > 12:
        lines.append(f"\n… yana {len(qs) - 12} ta savol")

    b = InlineKeyboardBuilder()
    for i in range(min(len(qs), 12)):
        b.add(InlineKeyboardButton(text=f"✏️ {i + 1}-savol", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="rv_q", tid=i).pack()))
    b.adjust(3)
    b.row(
        InlineKeyboardButton(text="🏷 Nomini o‘zgartirish", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="rv_title").pack()),
        InlineKeyboardButton(text="🪙 Mukofot", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="rv_coins").pack()),
    )
    b.row(
        InlineKeyboardButton(text="➕ Savol qo‘shish", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="q_more").pack()),
        InlineKeyboardButton(text="📢 Qayerga/qachon", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="q_scope").pack()),
    )
    b.row(InlineKeyboardButton(text="✅ TASDIQLASH VA YUBORISH", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="rv_ok").pack()))
    b.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="v").pack()))
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


async def _show_quiz_question_edit(target, state: FSMContext, owner_id: int, idx: int) -> None:
    data = await state.get_data()
    qs = list(data.get("quiz_questions") or [])
    if not (0 <= idx < len(qs)):
        await _show_quiz_review(target, state, owner_id)
        return
    item = qs[idx]
    opts = list(item.get("opts") or [])
    lines = [f"✏️ <b>{idx + 1}-SAVOLNI TAHRIRLASH</b>", "━━━━━━━━━━━━━━━", f"❓ {esc(item.get('q', ''))}", ""]
    b = InlineKeyboardBuilder()
    if len(opts) <= 1:
        lines.append(f"✅ To‘g‘ri javob (yozma): <code>{esc(opts[0] if opts else '—')}</code>")
    else:
        lines.append("Variantlar (✅ — to‘g‘ri javob). To‘g‘ri javobni almashtirish uchun variantni bosing:")
        for oi, opt in enumerate(opts):
            mark = "✅" if oi == int(item.get("cidx", 0)) else "▫️"
            lines.append(f"{mark} {esc(str(opt)[:60])}")
            b.row(
                InlineKeyboardButton(
                    text=f"{mark} {str(opt)[:40]}",
                    callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="rv_qc", tid=oi, ext=str(idx)).pack(),
                )
            )
    b.row(
        InlineKeyboardButton(text="📝 Savol matni", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="rv_qt", tid=idx).pack()),
        InlineKeyboardButton(text="🔤 Variantlar/javob", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="rv_qo", tid=idx).pack()),
    )
    b.row(
        InlineKeyboardButton(text="🗑 Savolni o‘chirish", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="rv_qd", tid=idx).pack()),
        InlineKeyboardButton(text="⬅️ Tasdiqlash ekrani", callback_data=AdmV2Cb(owner_id=owner_id, sec="quiz", act="rv").pack()),
    )
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


@router.message(AdminV2States.waiting_quiz_edit_title, NotCommandFilter())
async def fsm_v2_quiz_edit_title(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id:
        return
    title = (message.text or "").strip()[:160]
    if not title:
        await message.answer("⚠️ Nomni yozing.")
        return
    await state.update_data(quiz_title=title)
    await state.set_state(None)
    await _show_quiz_review(message, state, message.from_user.id)


@router.message(AdminV2States.waiting_quiz_edit_coins, NotCommandFilter())
async def fsm_v2_quiz_edit_coins(message: Message, state: FSMContext) -> None:
    if (await state.get_data()).get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit():
        await message.answer("⚠️ Musbat son kiriting.")
        return
    await state.update_data(quiz_coins=int(raw))
    await state.set_state(None)
    await _show_quiz_review(message, state, message.from_user.id)


@router.message(AdminV2States.waiting_quiz_edit_q_text, NotCommandFilter())
async def fsm_v2_quiz_edit_q_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    txt = (message.text or "").strip()
    if not txt:
        await message.answer("⚠️ Savol matnini yozing.")
        return
    qs = list(data.get("quiz_questions") or [])
    idx = int(data.get("edit_idx", 0))
    if 0 <= idx < len(qs):
        qs[idx]["q"] = txt
        await state.update_data(quiz_questions=qs)
    await state.set_state(None)
    await _show_quiz_question_edit(message, state, message.from_user.id, idx)


@router.message(AdminV2States.waiting_quiz_edit_q_opts, NotCommandFilter())
async def fsm_v2_quiz_edit_q_opts(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    opts = [x.strip() for x in re.split(r"[\n,;]+", (message.text or "").strip()) if x.strip()][:6]
    if not opts:
        await message.answer("⚠️ Kamida 1 ta javob yozing.")
        return
    qs = list(data.get("quiz_questions") or [])
    idx = int(data.get("edit_idx", 0))
    if not (0 <= idx < len(qs)):
        await state.set_state(None)
        await _show_quiz_review(message, state, message.from_user.id)
        return
    qs[idx]["opts"] = opts
    qs[idx]["cidx"] = 0
    qs[idx]["acc"] = [opts[0]]
    await state.update_data(quiz_questions=qs)
    await state.set_state(None)
    if len(opts) > 1:
        await message.answer("👇 Endi <b>to‘g‘ri javobni</b> tanlang (hozir 1-variant belgilangan):")
    await _show_quiz_question_edit(message, state, message.from_user.id, idx)


@router.message(AdminV2States.waiting_quiz_clone_coins, NotCommandFilter())
async def fsm_v2_quiz_clone_coins(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "")
    if not raw.isdigit():
        await message.answer("⚠️ Son kiriting.")
        return
    qid = int(data["clone_qid"])
    await state.clear()
    async with get_session() as session:
        nq = await GameplayService.clone_quiz(session, qid, message.from_user.id, new_reward_coins=int(raw))
        if nq:
            await session.commit()
            await _dispatch_quiz_announcement(message.bot, session, nq)
    await message.answer("🎉 Quiz yangi mukofot bilan qayta boshlandi!")
    await _show_quiz_home(message, message.from_user.id)
