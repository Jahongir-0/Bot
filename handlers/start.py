import urllib.parse

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ChatMemberUpdated, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.session import get_session
from keyboards.main_menu import back_home_kb, language_kb, main_menu_kb
from models.user import User
from services.community_service import CommunityService
from services.drop_service import DropService
from services.ranking_service import RankingService
from services.settings_service import SettingsService
from services.user_service import UserService
from utils.callback_data import NavCb
from utils.formatting import esc, fmt_coins, format_card_block
from utils.i18n import LANG_LABELS, SUPPORTED_LANGS, get_lang, tr
from utils.tg_helpers import edit_or_answer, is_admin_user, send_card_message

router = Router(name="start")


def _home_text(user: User, lang: str, welcome_txt: str, ref_notice: str = "", header_prefix: str = "") -> str:
    vip_badge = (
        tr(lang, "vip_active_short", tier=esc(user.vip_tier))
        if CommunityService.is_vip_active(user)
        else tr(lang, "vip_free_short")
    )
    summary = tr(
        lang,
        "home_summary",
        coins=fmt_coins(user.coins),
        cards=int(user.total_cards or 0),
        level=int(user.level or 1),
        vip_badge=vip_badge,
    )
    top = header_prefix or tr(lang, "salom", name=esc(user.display_name))
    return f"{top}\n{summary}\n\n{ref_notice}{welcome_txt}"


async def _show_referral(target, tg_id: int, db_user: User) -> None:
    lang = get_lang(db_user)
    bot = target.bot
    me = await bot.get_me()
    bot_username = me.username or ""
    ref_link = f"https://t.me/{bot_username}?start=ref_{tg_id}" if bot_username else f"/start ref_{tg_id}"

    async with get_session() as session:
        fresh_user = await session.get(User, db_user.id)
        ref_cfg = await SettingsService.get_referral_config(session)
        grp_coins = await SettingsService.get_group_add_reward_coins(session)

    ref_count = int(fresh_user.referral_count if fresh_user else db_user.referral_count or 0)
    text = tr(
        lang,
        "ref_title",
        count=ref_count,
        inviter=fmt_coins(ref_cfg["inviter_coins"]),
        invitee=fmt_coins(ref_cfg["invitee_coins"]),
        grp=fmt_coins(grp_coins),
        link=esc(ref_link),
    )
    builder = InlineKeyboardBuilder()
    if bot_username:
        share_text = urllib.parse.quote(
            "🎴 Anime Card Collection Bot!"
        )
        share_url = f"https://t.me/share/url?url={urllib.parse.quote(ref_link)}&text={share_text}"
        builder.row(InlineKeyboardButton(text=tr(lang, "ref_share_btn"), url=share_url))
        builder.row(
            InlineKeyboardButton(
                text=tr(lang, "ref_add_grp_btn"),
                url=f"https://t.me/{bot_username}?startgroup=true",
            )
        )
    builder.row(
        InlineKeyboardButton(text=tr(lang, "btn_back"), callback_data=NavCb(owner_id=tg_id, target="profile").pack()),
        InlineKeyboardButton(text=tr(lang, "btn_home"), callback_data=NavCb(owner_id=tg_id, target="home").pack()),
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
    lang = get_lang(db_user)
    ref_notice = ""
    args = (command.args or "").strip() if command else ""
    welcome_txt = tr(lang, "welcome_default")
    unread = 0
    fresh_user = db_user

    async with get_session() as session:
        fresh_user = await session.get(User, db_user.id) or db_user
        unread = await CommunityService.unread_count(session, fresh_user.id)
        if lang == "uz":
            custom_w = await SettingsService.get_value(session, "welcome_text", "")
            if custom_w:
                welcome_txt = str(custom_w)
        if args.startswith("ref_") and args[4:].isdigit():
            referrer_tg_id = int(args[4:])
            res = await UserService.apply_referral(session, fresh_user, referrer_tg_id)
            if res is not None:
                await session.commit()
                referrer, inviter_coins, invitee_coins = res
                ref_notice = (
                    f"🎉 <b>{esc(referrer.display_name)}</b> +{fmt_coins(invitee_coins)} 🪙!\n\n"
                )
                try:
                    await message.bot.send_message(
                        referrer.telegram_id,
                        f"🤝 <b>{esc(db_user.display_name)}</b> +{fmt_coins(inviter_coins)} 🪙!",
                    )
                except Exception:
                    pass
            else:
                await session.rollback()

    is_admin = is_bot_admin or is_admin_user(message.from_user)
    text = _home_text(fresh_user, lang, welcome_txt, ref_notice=ref_notice)
    await message.answer(
        text,
        reply_markup=main_menu_kb(message.from_user.id, is_admin=is_admin, lang=lang, unread_notifs=unread),
    )


@router.message(Command("lang", "language", "til"))
async def cmd_lang(message: Message, db_user: User) -> None:
    lang = get_lang(db_user)
    text = tr(lang, "lang_title", current=LANG_LABELS.get(lang, LANG_LABELS["uz"]))
    await message.answer(text, reply_markup=language_kb(message.from_user.id, lang))


@router.callback_query(NavCb.filter(F.target == "lang"))
async def cb_lang_menu(callback: CallbackQuery, db_user: User) -> None:
    lang = get_lang(db_user)
    text = tr(lang, "lang_title", current=LANG_LABELS.get(lang, LANG_LABELS["uz"]))
    await edit_or_answer(callback, text, reply_markup=language_kb(callback.from_user.id, lang))


@router.callback_query(NavCb.filter(F.target.in_({"lang_uz", "lang_en", "lang_ru"})))
async def cb_lang_set(
    callback: CallbackQuery,
    callback_data: NavCb,
    db_user: User,
    is_bot_admin: bool = False,
) -> None:
    chosen = callback_data.target.split("_", 1)[1]
    if chosen not in SUPPORTED_LANGS:
        chosen = "uz"
    async with get_session() as session:
        await UserService.set_user_language(session, db_user.id, chosen)
        fresh = await session.get(User, db_user.id) or db_user
        unread = await CommunityService.unread_count(session, fresh.id)
        await session.commit()
    db_user.language = chosen
    await callback.answer(tr(chosen, "lang_saved"), show_alert=False)
    is_admin = is_bot_admin or is_admin_user(callback.from_user)
    text = _home_text(
        fresh,
        chosen,
        tr(chosen, "welcome_default"),
        header_prefix=tr(chosen, "home_header", name=esc(fresh.display_name)),
    )
    await edit_or_answer(
        callback,
        text,
        reply_markup=main_menu_kb(callback.from_user.id, is_admin=is_admin, lang=chosen, unread_notifs=unread),
    )


@router.message(Command("ref", "referral"))
async def cmd_referral(message: Message, db_user: User) -> None:
    await _show_referral(message, message.from_user.id, db_user)


@router.callback_query(NavCb.filter(F.target == "referral"))
async def cb_referral(callback: CallbackQuery, db_user: User) -> None:
    await _show_referral(callback, callback.from_user.id, db_user)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, db_user: User) -> None:
    lang = get_lang(db_user)
    current = await state.get_state()
    await state.clear()
    await message.answer(tr(lang, "cancelled") if current else tr(lang, "nothing_to_cancel"))


@router.message(Command("help"))
async def cmd_help(message: Message, db_user: User) -> None:
    lang = get_lang(db_user)
    await message.answer(tr(lang, "help_text"), reply_markup=back_home_kb(message.from_user.id, "home", lang))


@router.callback_query(NavCb.filter(F.target == "home"))
async def cb_home(
    callback: CallbackQuery,
    state: FSMContext,
    db_user: User,
    is_bot_admin: bool = False,
) -> None:
    await state.clear()
    lang = get_lang(db_user)
    async with get_session() as session:
        fresh = await session.get(User, db_user.id) or db_user
        unread = await CommunityService.unread_count(session, fresh.id)
    is_admin = is_bot_admin or is_admin_user(callback.from_user)
    text = _home_text(
        fresh,
        lang,
        tr(lang, "welcome_default"),
        header_prefix=tr(lang, "home_header", name=esc(fresh.display_name)),
    )
    await edit_or_answer(
        callback,
        text,
        reply_markup=main_menu_kb(callback.from_user.id, is_admin=is_admin, lang=lang, unread_notifs=unread),
    )


@router.callback_query(NavCb.filter(F.target == "help"))
async def cb_help(callback: CallbackQuery, db_user: User) -> None:
    lang = get_lang(db_user)
    await edit_or_answer(callback, tr(lang, "help_text"), reply_markup=back_home_kb(callback.from_user.id, "home", lang))


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
                language_code=getattr(event.from_user, "language_code", None),
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
            "Guruhga tushgan yashirin kartaga <b>REPLY</b> qilib personaj ismini yozing!",
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
                        f"{format_card_block(epic_card, lang=get_lang(adder_user))}"
                    )
                    await send_card_message(event.bot, adder_tg_id, epic_card, pm_text)
                else:
                    await event.bot.send_message(adder_tg_id, pm_text)
            except Exception:
                pass
