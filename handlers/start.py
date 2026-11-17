from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

from database.session import get_session
from keyboards.main_menu import back_home_kb, main_menu_kb
from models.user import User
from services.drop_service import DropService
from services.user_service import UserService
from utils.callback_data import NavCb
from utils.formatting import esc
from utils.tg_helpers import edit_or_answer, is_admin_user

router = Router(name="start")

WELCOME_TEXT = (
    "🎴 <b>Anime Card Collection</b> o‘yiniga xush kelibsiz!\n\n"
    "Anime qahramonlari kartalarini yig‘ing, noyob kolleksiya yarating, "
    "marketda soting, do‘stlaringiz bilan almashing va reytingda 1-o‘ringa chiqing!\n\n"
    "👇 Quyidagi menyudan bo‘limni tanlang:"
)

HELP_TEXT = (
    "❓ <b>YORDAM VA BUYRUQLAR</b>\n\n"
    "🏠 /menu, /start — Asosiy menyu (inline tugmalar)\n"
    "👤 /me, /profil, /profilim — Shaxsiy global profil\n"
    "🎴 /cards, /collection — Mening kartalar kolleksiyam\n"
    "🎁 /daily — Kunlik coin bonusi\n"
    "🎯 /event — Limitli event kartalari\n"
    "⭐ /stars — Telegram Stars orqali coin olish\n"
    "🛒 /market — Global kartalar bozori\n"
    "💰 /sell <code>CRD-0001 2500</code> — Kartani sotuvga qo‘yish\n"
    "🛍 /buy <code>id</code> — Marketdan karta sotib olish\n"
    "🔍 /search <code>Gojo</code> — Global qidiruv\n"
    "🏆 /top — Global & Guruh TOP 10 reyting\n"
    "❌ /cancel — Joriy amalni (narx/qidiruv kiritish) bekor qilish\n"
    "🤝 /give <code>@user</code> — Karta berish (karta ismini yozasiz, ro‘yxatdan tanlaysiz)\n"
    "<i>(Guruhda xabarga reply qilib /give yozish ham mumkin. Pullik: /give @user 1000)</i>\n\n"
    "📌 <i>Bir xil kartadan faqat bitta nusxa olish mumkin.</i>\n"
    "💡 <i>Guruhlarda bot tasodifiy vaqtlarda karta tashlaydi — birinchi bo‘lib "
    "<b>[🎴 KARTANI OLISH]</b> tugmasini bosing!</i>"
)


@router.message(CommandStart())
@router.message(Command("menu"))
async def cmd_start(message: Message, state: FSMContext, db_user: User) -> None:
    await state.clear()
    is_admin = is_admin_user(message.from_user)
    text = f"Salom, <b>{esc(db_user.display_name)}</b>!\n\n{WELCOME_TEXT}"
    await message.answer(text, reply_markup=main_menu_kb(message.from_user.id, is_admin=is_admin))


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    await state.clear()
    await message.answer("❌ Amal bekor qilindi." if current else "Bekor qilinadigan amal yo‘q.")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=back_home_kb(message.from_user.id))


@router.callback_query(NavCb.filter(F.target == "home"))
async def cb_home(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    await state.clear()
    is_admin = is_admin_user(callback.from_user)
    text = f"🏠 <b>BOSH MENU</b> — {esc(db_user.display_name)}\n\n{WELCOME_TEXT}"
    await edit_or_answer(callback, text, reply_markup=main_menu_kb(callback.from_user.id, is_admin=is_admin))


@router.callback_query(NavCb.filter(F.target == "help"))
async def cb_help(callback: CallbackQuery) -> None:
    await edit_or_answer(callback, HELP_TEXT, reply_markup=back_home_kb(callback.from_user.id))


@router.my_chat_member()
async def on_bot_added_to_group(event: ChatMemberUpdated) -> None:
    if event.chat.type not in ("group", "supergroup"):
        return
    new_status = event.new_chat_member.status
    async with get_session() as session:
        group = await UserService.upsert_group_and_member(
            session=session,
            telegram_chat_id=event.chat.id,
            title=event.chat.title or "Anime Group",
            username=event.chat.username,
        )
        if new_status in ("member", "administrator"):
            group.is_active = True
            # A'zolar soniga qarab bugungi jadval yaratiladi (qolgan vaqtga mutanosib)
            await DropService.refresh_member_count(event.bot, session, group, force=True)
            await DropService.ensure_daily_schedule_for_group(session, group)
        elif new_status in ("left", "kicked"):
            group.is_active = False
        await session.commit()
