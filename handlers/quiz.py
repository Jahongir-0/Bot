"""Viktorina: foydalanuvchi uchun tushuntirish, savollar va javoblar."""

import logging
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from database.session import get_session
from keyboards.v2 import quiz_list_kb, quiz_question_kb
from models.user import User
from models.v2_features import QuizAttempt, QuizQuestion
from services.gameplay_service import GameplayService
from utils.callback_data import NavCb, QuizCb
from utils.formatting import esc
from utils.tg_helpers import NotCommandFilter, edit_or_answer

logger = logging.getLogger(__name__)
router = Router(name="quiz")


# ------------------------------------------------------------------
# QUIZ (/quiz)
# ------------------------------------------------------------------
async def _show_quizzes(target, owner_id: int) -> None:
    async with get_session() as session:
        quizzes = await GameplayService.list_active_quizzes(session)
    if not quizzes:
        await edit_or_answer(
            target,
            "🧠 <b>VIKTORINA (QUIZ)</b>\n\n📭 Hozircha faol quizlar yo‘q. Tez orada adminlar yangi quiz qo‘shadilar!",
            reply_markup=quiz_list_kb(owner_id, []),
        )
        return
    await edit_or_answer(
        target,
        f"🧠 <b>FAOL QUIZLAR</b> ({len(quizzes)} ta)\n\nSavollarga to‘g‘ri javob bering va coin + XP yuting:",
        reply_markup=quiz_list_kb(owner_id, quizzes),
    )


@router.message(Command("quiz"))
async def cmd_quiz(message: Message) -> None:
    await _show_quizzes(message, message.from_user.id)


@router.callback_query(QuizCb.filter(F.act == "v"))
async def cb_quiz_list(callback: CallbackQuery) -> None:
    await _show_quizzes(callback, callback.from_user.id)


async def show_quiz_intro(target, owner_id: int, quiz_id: int, user_db_id: int) -> None:
    """Viktorina qanday o'ynalishini tushuntiradi; birinchi savol faqat «Boshlash» bosilganda beriladi."""
    async with get_session() as session:
        res = await GameplayService.get_quiz_with_questions(session, quiz_id)
        answered = 0
        if res is not None:
            answered = int(
                (
                    await session.execute(
                        select(func.count(QuizAttempt.id)).where(
                            QuizAttempt.quiz_id == quiz_id, QuizAttempt.user_id == user_db_id
                        )
                    )
                ).scalar_one()
                or 0
            )
    if res is None or not res[0].is_active or res[0].status != "active" or not res[1]:
        await edit_or_answer(
            target,
            "🧠 <b>VIKTORINA</b>\n\n📭 Bu viktorina tugagan yoki hozir mavjud emas.",
            reply_markup=quiz_list_kb(owner_id, []),
        )
        return
    quiz, questions = res
    total = len(questions)
    lines = [
        f"🧠 <b>VIKTORINA: {esc(quiz.title)}</b>",
        "━━━━━━━━━━━━━━━",
        "📖 <b>Qanday o‘ynaladi?</b>",
        "• Bot sizga savollarni <b>birma-bir</b> beradi",
        "• Har savolda variantlardan <b>to‘g‘ri javobni tanlang</b>",
        "• Har bir to‘g‘ri javob uchun coin va XP olasiz",
        "• Har bir savolga faqat <b>bir marta</b> javob berish mumkin",
        "",
        f"🎯 Savollar: <b>{total} ta</b>",
        f"🎁 Mukofot: <b>{int(quiz.reward_coins):,} 🪙</b> + <b>{int(quiz.reward_xp)} XP</b> (har bir to‘g‘ri javobga)",
    ]
    b = InlineKeyboardBuilder()
    if answered >= total:
        lines += ["", "✅ Siz bu viktorinadagi barcha savollarga javob bergansiz. Boshqa viktorinalarni kuting!"]
    else:
        if answered:
            lines += ["", f"📊 Siz javob bergan: <b>{answered}/{total}</b> — davom ettirishingiz mumkin."]
        else:
            lines += ["", "Tayyor bo‘lsangiz, boshlang! 🚀"]
        b.row(
            InlineKeyboardButton(
                text="▶️ Viktorinani boshlash" if not answered else "▶️ Davom etish",
                callback_data=QuizCb(owner_id=owner_id, act="play", qid=quiz_id).pack(),
            )
        )
    b.row(
        InlineKeyboardButton(text="⬅️ Viktorinalar", callback_data=QuizCb(owner_id=owner_id, act="v").pack()),
        InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


@router.callback_query(QuizCb.filter(F.act == "intro"))
async def cb_quiz_intro(callback: CallbackQuery, callback_data: QuizCb, db_user: User) -> None:
    await show_quiz_intro(callback, callback.from_user.id, callback_data.qid, db_user.id)


async def _question_caption(session, qq: QuizQuestion, prefix: str = "") -> str:
    total_q = int(
        (
            await session.execute(
                select(func.count(QuizQuestion.id)).where(QuizQuestion.quiz_id == qq.quiz_id)
            )
        ).scalar_one()
        or 0
    )
    pos = int(qq.order_num or 1)
    t_sec = int(qq.time_limit_sec or 30)
    head = f"{prefix}\n\n" if prefix else ""
    return (
        f"{head}🧠 <b>Savol {pos}/{max(total_q, pos)}</b>  <i>(⏱ ~{t_sec} soniya)</i>\n"
        "━━━━━━━━━━━━━━━\n"
        f"{esc(qq.question_text)}"
    )


@router.callback_query(QuizCb.filter(F.act == "play"))
async def cb_quiz_play(callback: CallbackQuery, callback_data: QuizCb, db_user: User) -> None:
    async with get_session() as session:
        qq = await GameplayService.get_next_unanswered_question(session, callback_data.qid, db_user.id)
        if qq is None:
            await callback.answer("🎉 Siz ushbu quizdagi barcha savollarga javob bergansiz!", show_alert=True)
            return
        txt = await _question_caption(session, qq)
    await edit_or_answer(callback, txt, reply_markup=quiz_question_kb(callback.from_user.id, qq))


@router.callback_query(QuizCb.filter(F.act == "ans"))
async def cb_quiz_answer(callback: CallbackQuery, callback_data: QuizCb, db_user: User) -> None:
    async with get_session() as session:
        user = await session.get(User, db_user.id)
        qq = await session.get(QuizQuestion, callback_data.qid)
        if qq is None:
            await callback.answer("Savol topilmadi.", show_alert=True)
            return
        accepted, _ok, msg = await GameplayService.submit_quiz_answer(
            session,
            quiz_id=qq.quiz_id,
            question_id=qq.id,
            user=user,
            selected_index=callback_data.opt,
        )
        if accepted:
            await session.commit()
        else:
            await session.rollback()
    await callback.answer(msg[:190], show_alert=True)
    if accepted:
        owner_id = callback.from_user.id
        async with get_session() as session:
            next_q = await GameplayService.get_next_unanswered_question(session, qq.quiz_id, db_user.id)
            if next_q is not None:
                txt = await _question_caption(session, next_q, prefix=esc(msg))
            else:
                total_q = int(
                    (
                        await session.execute(
                            select(func.count(QuizQuestion.id)).where(QuizQuestion.quiz_id == qq.quiz_id)
                        )
                    ).scalar_one()
                    or 0
                )
                ok_q = int(
                    (
                        await session.execute(
                            select(func.count(QuizAttempt.id)).where(
                                QuizAttempt.quiz_id == qq.quiz_id,
                                QuizAttempt.user_id == db_user.id,
                                QuizAttempt.is_correct.is_(True),
                            )
                        )
                    ).scalar_one()
                    or 0
                )
        if next_q is not None:
            await edit_or_answer(
                callback,
                txt,
                reply_markup=quiz_question_kb(owner_id, next_q),
            )
        else:
            b = InlineKeyboardBuilder()
            b.row(
                InlineKeyboardButton(text="🧠 Boshqa quizlar", callback_data=QuizCb(owner_id=owner_id, act="v").pack()),
                InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
            )
            await edit_or_answer(
                callback,
                (
                    f"{esc(msg)}\n\n"
                    "🏁 <b>VIKTORINA YAKUNLANDI!</b>\n"
                    "━━━━━━━━━━━━━━━\n"
                    f"📊 Natija: <b>{ok_q}/{total_q}</b> to‘g‘ri javob\n"
                    "Barcha savollarga javob berdingiz!"
                ),
                reply_markup=b.as_markup(),
            )


@router.callback_query(QuizCb.filter(F.act == "hist"))
async def cb_quiz_hist(callback: CallbackQuery, db_user: User) -> None:
    async with get_session() as session:
        rows = (
            await session.execute(
                select(QuizAttempt)
                .where(QuizAttempt.user_id == db_user.id)
                .order_by(QuizAttempt.id.desc())
                .limit(15)
            )
        ).scalars().all()
    if not rows:
        await callback.answer("📭 Hali quiz tarixi yo‘q.", show_alert=True)
        return
    ok_cnt = sum(1 for r in rows if r.is_correct)
    txt = f"📜 <b>QUIZ TARIXINGIZ</b>\n\nOxirgi {len(rows)} ta javobdan <b>{ok_cnt}</b> tasi to‘g‘ri!\nUmumiy quiz g‘alabalar: <b>{db_user.quiz_wins}</b>"
    await edit_or_answer(callback, txt, reply_markup=quiz_list_kb(callback.from_user.id, []))


@router.message(F.reply_to_message, F.text, NotCommandFilter())
async def msg_reply_quiz_answer(message: Message, db_user: User) -> None:
    """Viktorina e'loniga reply qilib yozilgan javobni tekshiradi."""
    replied = message.reply_to_message
    if replied is None or replied.from_user is None or not replied.from_user.is_bot:
        return
    rtxt = replied.text or ""
    if "YANGI VIKTORINA BOSHLANDI" not in rtxt:
        return
    m = re.search(r"Quiz #(\d+)", rtxt)
    if not m:
        return
    quiz_id = int(m.group(1))
    guess = (message.text or "").strip()
    if not guess:
        return

    async with get_session() as session:
        user = await session.get(User, db_user.id)
        if user is None:
            return
        qq = await GameplayService.get_next_unanswered_question(session, quiz_id, user.id)
        if qq is None:
            return
        accepted, is_ok, msg = await GameplayService.submit_quiz_text_answer(
            session, quiz_id, qq.id, user, guess
        )
        if accepted:
            await session.commit()
        else:
            await session.rollback()
    if accepted and is_ok and msg:
        await message.reply(msg)
    elif msg:
        await message.reply(esc(msg))
