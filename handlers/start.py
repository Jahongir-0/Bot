import urllib.parse

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ChatMemberUpdated, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.session import get_session
from keyboards.main_menu import back_home_kb, main_menu_kb
from models.user import User
from services.drop_service import DropService
from services.ranking_service import RankingService
from services.settings_service import SettingsService
from services.user_service import UserService
from utils.callback_data import NavCb
from utils.formatting import esc, fmt_coins, format_card_block
from utils.tg_helpers import edit_or_answer, is_admin_user, send_card_message

router = Router(name="start")

WELCOME_TEXT = (
    "🎴 <b>Anime Card Collection</b> o‘yiniga xush kelibsiz!\n\n"
    "Anime qahramonlari kartalarini yig‘ing, noyob kolleksiya yarating, "
    "marketda soting, do‘stlaringiz bilan almashing va reytingda 1-o‘ringa chiqing!\n\n"
    "👇 Quyidagi menyudan bo‘limni tanlang:"
)

HELP_TEXT = (
    "❓ <b>YORDAM VA V2 BUYRUQLAR</b>\n\n"
    "🏠 /menu, /start — Asosiy menyu (inline tugmalar)\n"
    "👤 /me, /profil — Shaxsiy V2 profil va bezaklar\n"
    "🎴 /cards — Kolleksiya (20/sahifa, filtrlar)\n"
    "🎁 /daily — Kunlik coin + tasodifiy karta\n"
    "🧠 /quiz — Viktorina savollari\n"
    "🎮 /game — Mini o‘yinlar (Dice Roll)\n"
    "🔨 /auction — Jonli auksionlar\n"
    "💖 /wishlist — Orzu qilingan kartalar\n"
    "🎯 /event — Limitli event kartalari\n"
    "🛒 /market, /sell, /buy — Global bozor\n"
    "🤝 /trade, /give — Karta almashish / berish\n"
    "💎 /vip, ⭐ /stars — VIP va Stars do‘koni\n"
    "🏆 /top — Reytinglar · 🔔 /notif — Xabarlar · 💬 /support — Yordam\n\n"
    "📌 <b>Karta olish qoidasi (V2):</b>\n"
    "Guruhda karta rasmi tashlanganda uning nomi yashirin bo‘ladi — "
    "xabarga <b>REPLY</b> qilib personaj ismini yozing va kartani qo‘lga kiriting!"
)


async def _show_referral(target, tg_id: int, db_user: User) -> None:
    bot = target.bot
    me = await bot.get_me()
    bot_username = me.username or ""
    ref_link = f"https://t.me/{bot_username}?start=ref_{tg_id}" if bot_username else f"/start ref_{tg_id}"

    async with get_session() as session:
        fresh_user = await session.get(User, db_user.id)
        ref_cfg = await SettingsService.get_referral_config(session)
        grp_coins = await SettingsService.get_group_add_reward_coins(session)

    ref_count = int(fresh_user.referral_count if fresh_user else db_user.referral_count or 0)
    text = (
        "🤝 <b>REFERAL VA GURUH MUKOFOTI</b>\n\n"
        f"👥 Taklif qilgan do‘stlaringiz: <b>{ref_count}</b> ta\n\n"
        "🎁 <b>Do‘st taklif qilish mukofoti:</b>\n"
        f"• Sizga: <b>+{fmt_coins(ref_cfg['inviter_coins'])} 🪙</b>\n"
        f"• Yangi do‘stingizga: <b>+{fmt_coins(ref_cfg['invitee_coins'])} 🪙</b>\n"
        "<i>(Faqat botga birinchi marta kirgan do‘stlaringiz uchun ishlaydi!)</i>\n\n"
        "🏰 <b>Botni guruhga qo‘shish mukofoti:</b>\n"
        f"• <b>+{fmt_coins(grp_coins)} 🪙</b> + 🟣 <b>1 ta eksklyuziv Epic karta</b>!\n"
        "<i>(Bu karta boshqa guruhlarga tashlanmagan bo‘ladi va sizga maxsus beriladi!)</i>\n\n"
        f"🔗 <b>Sizning referal havolangiz:</b>\n<code>{esc(ref_link)}</code>"
    )
    builder = InlineKeyboardBuilder()
    if bot_username:
        share_text = urllib.parse.quote(
            "🎴 Anime Card Collection V2 botiga qo‘shiling! Noyob kartalar yig‘ing va bonus coin oling:"
        )
        share_url = f"https://t.me/share/url?url={urllib.parse.quote(ref_link)}&text={share_text}"
        builder.row(InlineKeyboardButton(text="📤 DO‘STLARGA ULASHISH", url=share_url))
        builder.row(
            InlineKeyboardButton(
                text="➕ BOTNI GURUHGA QO‘SHISH",
                url=f"https://t.me/{bot_username}?startgroup=true",
            )
        )
    builder.row(
        InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=NavCb(owner_id=tg_id, target="profile").pack()),
        InlineKeyboardButton(text="🏠 BOSH MENU", callback_data=NavCb(owner_id=tg_id, target="home").pack()),
    )
    await edit_or_answer(target, text, reply_markup=builder.as_markup())


@router.message(CommandStart())
@router.message(Command("menu"))
async def cmd_start(
    message: Message,
    state: FSMContext,
    db_user: User,
    command: CommandObject | None = None,
    is_bot_admin: bool = False,
) -> None:
    await state.clear()
    ref_notice = ""
    args = (command.args or "").strip() if command else ""
    welcome_txt = WELCOME_TEXT
    async with get_session() as session:
        custom_w = await SettingsService.get_value(session, "welcome_text", WELCOME_TEXT)
        if custom_w:
            welcome_txt = str(custom_w)
        if args.startswith("ref_") and args[4:].isdigit():
            referrer_tg_id = int(args[4:])
            fresh = await session.get(User, db_user.id)
            res = await UserService.apply_referral(session, fresh or db_user, referrer_tg_id)
            if res is not None:
                await session.commit()
                referrer, inviter_coins, invitee_coins = res
                ref_notice = (
                    f"🎉 <b>{esc(referrer.display_name)}</b> taklifi bilan keldingiz! "
                    f"Sizga <b>+{fmt_coins(invitee_coins)} 🪙</b> bonus berildi!\n\n"
                )
                try:
                    await message.bot.send_message(
                        referrer.telegram_id,
                        f"🤝 <b>{esc(db_user.display_name)}</b> sizning referal havolangiz orqali botga qo‘shildi!\n"
                        f"🪙 Mukofot: <b>+{fmt_coins(inviter_coins)} coins</b>!",
                    )
                except Exception:
                    pass
            else:
                await session.rollback()

    is_admin = is_bot_admin or is_admin_user(message.from_user)
    text = f"Salom, <b>{esc(db_user.display_name)}</b>!\n\n{ref_notice}{welcome_txt}"
    await message.answer(text, reply_markup=main_menu_kb(message.from_user.id, is_admin=is_admin))


@router.message(Command("ref", "referral"))
async def cmd_referral(message: Message, db_user: User) -> None:
    await _show_referral(message, message.from_user.id, db_user)


@router.callback_query(NavCb.filter(F.target == "referral"))
async def cb_referral(callback: CallbackQuery, db_user: User) -> None:
    await _show_referral(callback, callback.from_user.id, db_user)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    await state.clear()
    await message.answer("❌ Amal bekor qilindi." if current else "Bekor qilinadigan amal yo‘q.")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=back_home_kb(message.from_user.id))


@router.callback_query(NavCb.filter(F.target == "home"))
async def cb_home(
    callback: CallbackQuery,
    state: FSMContext,
    db_user: User,
    is_bot_admin: bool = False,
) -> None:
    await state.clear()
    is_admin = is_bot_admin or is_admin_user(callback.from_user)
    text = f"🏠 <b>BOSH MENU</b> — {esc(db_user.display_name)}\n\n{WELCOME_TEXT}"
    await edit_or_answer(callback, text, reply_markup=main_menu_kb(callback.from_user.id, is_admin=is_admin))


@router.callback_query(NavCb.filter(F.target == "help"))
async def cb_help(callback: CallbackQuery) -> None:
    await edit_or_answer(callback, HELP_TEXT, reply_markup=back_home_kb(callback.from_user.id))


@router.my_chat_member()
async def on_bot_added_to_group(event: ChatMemberUpdated) -> None:
    if event.chat.type not in ("group", "supergroup"):
        return
    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status
    reward_result = None
    adder_tg_id = None
    group_title = event.chat.title or "Anime Group"

    async with get_session() as session:
        adder_user = None
        if event.from_user and not event.from_user.is_bot:
            adder_tg_id = event.from_user.id
            adder_user = await UserService.upsert_user(
                session=session,
                telegram_id=event.from_user.id,
                username=event.from_user.username,
                first_name=event.from_user.first_name or "Collector",
            )

        group = await UserService.upsert_group_and_member(
            session=session,
            telegram_chat_id=event.chat.id,
            title=group_title,
            username=event.chat.username,
            user_id=adder_user.id if adder_user is not None else None,
        )
        if new_status in ("member", "administrator"):
            group.is_active = True
            await DropService.refresh_member_count(event.bot, session, group, force=True)
            await DropService.ensure_daily_schedule_for_group(session, group)
            if old_status not in ("member", "administrator") and adder_user is not None:
                reward_result = await UserService.reward_group_adder(session, group, adder_user)
            RankingService.invalidate_cache()
        elif new_status in ("left", "kicked"):
            group.is_active = False
            RankingService.invalidate_cache()
        await session.commit()

    if new_status in ("member", "administrator") and old_status not in ("member", "administrator"):
        welcome_lines = [
            f"🎴 <b>Anime Card Collection</b> boti <b>{esc(group_title)}</b> guruhiga qo‘shildi!",
            "Har kuni tasodifiy vaqtlarda anime kartalari tashlanadi — birinchi bo‘lib <b>[🎴 KARTANI OLISH]</b> tugmasini bosing!",
            "🏆 Guruh reytingi: /top | 🎯 Eventlar: /event",
        ]
        if reward_result is not None and event.from_user:
            coins, epic_card = reward_result
            extra = f" + 🟣 <b>{esc(epic_card.character_name)}</b> (Epic)" if epic_card else ""
            welcome_lines.append(
                f"\n🎁 <b>{esc(event.from_user.first_name)}</b> botni qo‘shgani uchun "
                f"<b>+{fmt_coins(coins)} 🪙</b>{extra} mukofot oldi!"
            )
        try:
            await event.bot.send_message(event.chat.id, "\n".join(welcome_lines))
        except Exception:
            pass

        if reward_result is not None and adder_tg_id:
            coins, epic_card = reward_result
            pm_text = (
                f"🏰 Botni <b>{esc(group_title)}</b> guruhiga qo‘shganingiz uchun mukofot oldingiz!\n\n"
                f"🪙 Coin: <b>+{fmt_coins(coins)}</b>"
            )
            try:
                if epic_card is not None:
                    pm_text += (
                        f"\n🟣 Eksklyuziv Epic karta (guruhlarga tashlanmaydi):\n\n"
                        f"{format_card_block(epic_card)}"
                    )
                    await send_card_message(event.bot, adder_tg_id, epic_card, pm_text)
                else:
                    await event.bot.send_message(adder_tg_id, pm_text)
            except Exception:
                pass
