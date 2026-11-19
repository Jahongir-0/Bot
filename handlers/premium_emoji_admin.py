"""/premium_emoji — premium emojilarni botga o'rgatish (faqat super admin)."""
from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from utils import premium_emoji as pe
from utils.tg_helpers import NotCommandFilter, is_admin_user

router = Router(name="premium_emoji_admin")

SUGGEST = "🎴 🎁 🎲 🧠 🛒 🔨 🤝 💖 🏆 👤 💎 ⭐ 🪙 🔔 💬 ❓ 🎯 📋 🏅 🔍 👥 ✅ ❌ 🏠"


class PremiumEmojiStates(StatesGroup):
    waiting = State()


@router.message(Command("premium_emoji"))
async def cmd_premium_emoji(message: Message, state: FSMContext) -> None:
    if not is_admin_user(message.from_user):
        return
    await state.set_state(PremiumEmojiStates.waiting)
    await message.answer(
        "💎 <b>PREMIUM EMOJI SOZLASH</b>\n\n"
        "Telegram Premium emoji paketidan botdagi emojilarga mos <b>premium emojilarni</b> tanlab, "
        "bitta xabarda yuboring. Har biri o‘zining oddiy juftini almashtiradi (masalan, premium yulduz — ⭐ ni).\n\n"
        f"Botda ko‘p ishlatiladigan emojilar:\n{SUGGEST}\n\n"
        "Bekor qilish: /cancel · O‘chirish: /premium_emoji_off"
    )


@router.message(PremiumEmojiStates.waiting, NotCommandFilter())
async def got_premium_emojis(message: Message, state: FSMContext) -> None:
    if not is_admin_user(message.from_user):
        return
    ents = (message.entities or []) + (message.caption_entities or [])
    pairs = pe.extract_pairs(message.text or message.caption or "", ents)
    if not pairs:
        await message.answer("⚠️ Xabarda premium emoji topilmadi. Premium emojilarni tanlab qayta yuboring (yoki /cancel).")
        return
    total = await pe.save_map(pairs)
    await state.clear()
    sample = " ".join(pairs.keys())
    await message.answer(
        f"✅ <b>{len(pairs)}</b> ta premium emoji saqlandi (jami: {total}).\n\n"
        f"Sinov: {sample}\n\n"
        "Yuqoridagi emojilar premium ko‘rinsa — hammasi ishlayapti. Oddiy ko‘rinsa, bot hozircha premium emoji "
        "yubora olmaydi (Telegram shartlari), lekin hech narsa buzilmaydi."
    )


@router.message(Command("premium_emoji_off"))
async def cmd_premium_emoji_off(message: Message) -> None:
    if not is_admin_user(message.from_user):
        return
    await pe.save_map({}, replace=True)
    await message.answer("✅ Premium emojilar o‘chirildi. Bot oddiy emojilarga qaytdi.")
