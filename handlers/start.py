from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message
from sqlalchemy import select

from config.settings import get_settings
from database.session import get_session
from keyboards.main_menu import back_home_kb, main_menu_kb
from keyboards.market import stars_shop_kb
from models.group import Group
from models.user import User
from services.drop_service import DropService
from services.settings_service import SettingsService
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
    "⭐ /stars — Telegram Stars orqali coin olish\n"
    "🛒 /market — Global kartalar bozori\n"
    "💰 /sell <code>CRD-0001 2500</code> — Kartani sotuvga qo‘yish\n"
    "🛍 /buy <code>id</code> — Marketdan karta sotib olish\n"
    "🔍 /search <code>Gojo</code> — Global qidiruv\n"
    "🏆 /top — Global & Guruh TOP 10 reyting\n"
    "🤝 /give <code>@user CRD-0001 [narx]</code> — Karta berish/sotish\n"
    "❌ /cancel — Joriy kiritish jarayonini bekor qilish\n"
    "<i>(Guruhda xabarga reply qilib /give CRD-0001 yozish ham mumkin!)</i>\n\n"
    "💡 <i>Guruhlarda bot tasodifiy vaqtlarda karta tashlaydi — birinchi bo‘lib "
    "<b>[🎴 KARTANI OLISH]</b> tugmasini bosing!</i>"
)


@router.message(CommandStart())
@router.message(Command("menu"))
async def cmd_start(
    message: Message,
    state: FSMContext,
    db_user: User,
    command: Optional[CommandObject] = None,
) -> None:
    await state.clear()
    if command is not None and command.args == "stars" and get_settings().enable_telegram_stars:
        async with get_session() as session:
            packages = await SettingsService.get_stars_packages(session)
        await message.answer(
            "⭐ <b>TELEGRAM STARS COIN DO‘KONI</b>\n\n"
            "Telegram Stars orqali rasmiy va xavfsiz tarzda coin sotib oling.\n"
            "👇 Kerakli paketni tanlang:",
            reply_markup=stars_shop_kb(message.from_user.id, packages),
        )
        return

    is_admin = is_admin_user(message.from_user)
    text = f"Salom, <b>{esc(db_user.display_name)}</b>!\n\n{WELCOME_TEXT}"
    await message.answer(text, reply_markup=main_menu_kb(message.from_user.id, is_admin=is_admin))


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "❌ Amaldagi jarayon bekor qilindi.",
        reply_markup=back_home_kb(message.from_user.id),
    )


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


@router.message(F.migrate_to_chat_id)
async def on_group_migrated(message: Message) -> None:
    new_chat_id = message.migrate_to_chat_id
    if not new_chat_id:
        return
    async with get_session() as session:
        grp = (
            await session.execute(select(Group).where(Group.telegram_chat_id == message.chat.id))
        ).scalar_one_or_none()
        if grp is not None:
            grp.telegram_chat_id = new_chat_id
            await session.commit()


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
            await DropService.ensure_daily_schedule_for_group(session, group)
        elif new_status in ("left", "kicked"):
            group.is_active = False
        await session.commit()
