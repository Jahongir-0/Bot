"""Viktorina e'loni: chiroyli matn + «Boshlash» tugmasi (birinchi savol darhol berilmaydi)."""
from typing import Optional, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from models.v2_features import Quiz
from utils.formatting import esc


def build_quiz_announcement(
    quiz: Quiz, question_count: int, bot_username: Optional[str]
) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    text = (
        f"🧠 <b>YANGI VIKTORINA BOSHLANDI: {esc(quiz.title)}</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"🎯 Savollar: <b>{int(question_count)} ta</b>\n"
        f"🎁 Har bir to‘g‘ri javob uchun: <b>{int(quiz.reward_coins):,} 🪙</b> + <b>{int(quiz.reward_xp)} XP</b>\n\n"
        "📖 <b>Qanday o‘ynaladi?</b>\n"
        "1️⃣ «Viktorinani boshlash» tugmasini bosing\n"
        "2️⃣ Bot sizga savollarni birma-bir beradi\n"
        "3️⃣ Variantlardan to‘g‘ri javobni tanlang\n"
        "4️⃣ Har bir to‘g‘ri javob uchun mukofot olasiz!\n\n"
        f"<code>Quiz #{quiz.id}</code>"
    )
    if not bot_username:
        return text + "\n\n👇 Boshlash uchun botda /quiz yozing.", None
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="▶️ Viktorinani boshlash", url=f"https://t.me/{bot_username}?start=quiz_{quiz.id}")]
        ]
    )
    return text, markup
