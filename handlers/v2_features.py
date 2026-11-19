import logging
import re
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message
from sqlalchemy import select

from config.settings import get_settings
from database.session import get_session
from keyboards.main_menu import back_home_kb, main_menu_kb
from keyboards.v2 import (
    auction_detail_kb,
    auctions_list_kb,
    games_menu_kb,
    notifications_kb,
    profile_custom_kb,
    quiz_list_kb,
    quiz_question_kb,
    support_menu_kb,
    vip_shop_kb,
    wishlist_kb,
)
from models.base import ensure_utc
from models.card import Card
from models.user import User
from models.user_card import UserCard
from models.v2_features import CardTrade, QuizAttempt, QuizQuestion
from services.auction_wishlist_service import AuctionWishlistService
from services.community_service import CommunityService
from services.gameplay_service import GameplayService
from services.user_service import UserService
from utils.callback_data import AchCb, AucCb, GameCb, NotifCb, QuizCb, SupCb, TradeCb, VipCb, WishCb
from utils.constants import get_rarity_meta
from utils.formatting import esc, fmt_coins, format_card_block
from utils.i18n import get_lang, tr
from utils.tg_helpers import NotCommandFilter, edit_or_answer, is_admin_user, send_card_message

logger = logging.getLogger(__name__)
router = Router(name="v2_features")


class V2UserStates(StatesGroup):
    waiting_auction_bid = State()
    waiting_support_subject = State()
    waiting_support_body = State()
    waiting_support_reply = State()
    waiting_trade_partner = State()


# ------------------------------------------------------------------
# QUIZ (/quiz)
# ------------------------------------------------------------------
async def _show_quizzes(target, owner_id: int, lang: str = "uz") -> None:
    async with get_session() as session:
        quizzes = await GameplayService.list_active_quizzes(session)
    if not quizzes:
        await edit_or_answer(
            target,
            tr(lang, "quiz_none"),
            reply_markup=quiz_list_kb(owner_id, [], lang=lang),
        )
        return
    await edit_or_answer(
        target,
        tr(lang, "quiz_list", n=len(quizzes)),
        reply_markup=quiz_list_kb(owner_id, quizzes, lang=lang),
    )


@router.message(Command("quiz"))
async def cmd_quiz(message: Message, db_user: User) -> None:
    await _show_quizzes(message, message.from_user.id, lang=get_lang(db_user))


@router.callback_query(QuizCb.filter(F.act == "v"))
async def cb_quiz_list(callback: CallbackQuery, db_user: User) -> None:
    await _show_quizzes(callback, callback.from_user.id, lang=get_lang(db_user))


@router.callback_query(QuizCb.filter(F.act == "play"))
async def cb_quiz_play(callback: CallbackQuery, callback_data: QuizCb, db_user: User) -> None:
    lang = get_lang(db_user)
    async with get_session() as session:
        qq = await GameplayService.get_next_unanswered_question(session, callback_data.qid, db_user.id)
    if qq is None:
        await callback.answer("🎉!", show_alert=True)
        return
    txt = f"🧠 <b>#{qq.order_num}</b>\n\n{esc(qq.question_text)}"
    await edit_or_answer(callback, txt, reply_markup=quiz_question_kb(callback.from_user.id, qq, lang=lang))


@router.callback_query(QuizCb.filter(F.act == "ans"))
async def cb_quiz_answer(callback: CallbackQuery, callback_data: QuizCb, db_user: User) -> None:
    lang = get_lang(db_user)
    async with get_session() as session:
        user = await session.get(User, db_user.id)
        qq = await session.get(QuizQuestion, callback_data.qid)
        if qq is None:
            await callback.answer(tr(lang, "not_found"), show_alert=True)
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
        async with get_session() as session:
            next_q = await GameplayService.get_next_unanswered_question(session, qq.quiz_id, db_user.id)
        if next_q is not None:
            await edit_or_answer(
                callback,
                f"{esc(msg)}\n\n🧠 <b>#{next_q.order_num}</b>\n\n{esc(next_q.question_text)}",
                reply_markup=quiz_question_kb(callback.from_user.id, next_q, lang=lang),
            )
        else:
            await _show_quizzes(callback, callback.from_user.id, lang=lang)


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


# ------------------------------------------------------------------
# MINI GAMES (/game)
# ------------------------------------------------------------------
@router.message(Command("game", "games"))
async def cmd_game(message: Message, db_user: User) -> None:
    lang = get_lang(db_user)
    async with get_session() as session:
        cfg = await GameplayService.get_game_config(session)
    txt = tr(
        lang,
        "game_title",
        limit=cfg.get("dice_daily_limit", 3),
        jackpot=fmt_coins(cfg.get("dice_jackpot_reward", 300)),
        cd=cfg.get("dice_cooldown_minutes", 30),
    )
    await message.answer(txt, reply_markup=games_menu_kb(message.from_user.id, lang=lang))


@router.callback_query(GameCb.filter(F.act == "v"))
async def cb_games_menu(callback: CallbackQuery, db_user: User) -> None:
    lang = get_lang(db_user)
    async with get_session() as session:
        cfg = await GameplayService.get_game_config(session)
    txt = tr(
        lang,
        "game_title",
        limit=cfg.get("dice_daily_limit", 3),
        jackpot=fmt_coins(cfg.get("dice_jackpot_reward", 300)),
        cd=cfg.get("dice_cooldown_minutes", 30),
    )
    await edit_or_answer(callback, txt, reply_markup=games_menu_kb(callback.from_user.id, lang=lang))


@router.callback_query(GameCb.filter(F.act == "dice"))
async def cb_game_dice(callback: CallbackQuery, db_user: User) -> None:
    lang = get_lang(db_user)
    async with get_session() as session:
        user = await session.get(User, db_user.id)
        ok, msg, _roll, _coins = await GameplayService.play_dice_game(session, user)
        if ok:
            await session.commit()
        else:
            await session.rollback()
    await callback.answer(msg.replace("<b>", "").replace("</b>", "")[:190], show_alert=True)
    if ok:
        await edit_or_answer(callback, f"🎮 <b>DICE ROLL</b>\n\n{msg}", reply_markup=games_menu_kb(callback.from_user.id, lang=lang))


# ------------------------------------------------------------------
# AUCTION (/auction)
# ------------------------------------------------------------------
async def _show_auctions(target, owner_id: int, lang: str = "uz") -> None:
    async with get_session() as session:
        items, total = await AuctionWishlistService.list_active_auctions(session, page=1, page_size=10)
    if not items:
        await edit_or_answer(
            target,
            tr(lang, "auc_none"),
            reply_markup=auctions_list_kb(owner_id, [], lang=lang),
        )
        return
    lines = [tr(lang, "auc_list", n=total)]
    for auc, card in items:
        meta = get_rarity_meta(card.rarity)
        lines.append(
            f"• <b>#{auc.id}</b> {meta.emoji} {esc(card.character_name)} — "
            f"<b>{fmt_coins(auc.current_price)} 🪙</b> ({auc.bid_count})"
        )
    await edit_or_answer(target, "\n".join(lines), reply_markup=auctions_list_kb(owner_id, items, lang=lang))


@router.message(Command("auction"))
async def cmd_auction(message: Message, db_user: User) -> None:
    await _show_auctions(message, message.from_user.id, lang=get_lang(db_user))


@router.callback_query(AucCb.filter(F.act == "v"))
async def cb_auction_list(callback: CallbackQuery, db_user: User) -> None:
    await _show_auctions(callback, callback.from_user.id, lang=get_lang(db_user))


@router.callback_query(AucCb.filter(F.act == "det"))
async def cb_auction_detail(callback: CallbackQuery, callback_data: AucCb, db_user: User) -> None:
    lang = get_lang(db_user)
    async with get_session() as session:
        det = await AuctionWishlistService.get_auction_detail(session, callback_data.aid)
    if det is None:
        await callback.answer(tr(lang, "not_found"), show_alert=True)
        return
    auc, card, leader = det
    leader_name = esc(leader.display_name) if leader else "—"
    txt = (
        f"🔨 <b>AUCTION #{auc.id}</b>\n\n"
        f"{format_card_block(card, lang=lang)}\n\n"
        f"💰 Price: <b>{fmt_coins(auc.current_price)} 🪙</b>\n"
        f"📈 Step: +{fmt_coins(auc.min_step)} 🪙\n"
        f"👑 Leader: <b>{leader_name}</b> ({auc.bid_count})\n"
        f"⏳ Ends: <b>{ensure_utc(auc.ends_at):%d.%m.%Y %H:%M} UTC</b>"
    )
    if callback.message:
        await send_card_message(callback.bot, callback.message.chat.id, card, txt, auction_detail_kb(callback.from_user.id, auc, lang=lang))
    await callback.answer()


@router.callback_query(AucCb.filter(F.act == "bid"))
async def cb_auction_bid(callback: CallbackQuery, callback_data: AucCb, db_user: User) -> None:
    async with get_session() as session:
        user = await session.get(User, db_user.id)
        ok, msg = await AuctionWishlistService.place_bid(session, callback_data.aid, user, callback_data.amt)
        if ok:
            await session.commit()
        else:
            await session.rollback()
    await callback.answer(msg[:190], show_alert=True)


@router.callback_query(AucCb.filter(F.act == "custom"))
async def cb_auction_custom(callback: CallbackQuery, callback_data: AucCb, state: FSMContext) -> None:
    await state.set_state(V2UserStates.waiting_auction_bid)
    await state.update_data(auc_id=callback_data.aid, owner_tg_id=callback.from_user.id)
    await callback.message.answer(f"🔨 Auksion #{callback_data.aid} uchun stavka summasini kiriting (masalan: <code>5000</code>):\n/cancel")
    await callback.answer()


@router.message(V2UserStates.waiting_auction_bid, NotCommandFilter())
async def fsm_auction_bid(message: Message, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    txt = (message.text or "").strip()
    if not txt.isdigit():
        await message.answer("⚠️ Faqat musbat son kiriting.")
        return
    aid = int(data["auc_id"])
    await state.clear()
    async with get_session() as session:
        user = await session.get(User, db_user.id)
        ok, msg = await AuctionWishlistService.place_bid(session, aid, user, int(txt))
        if ok:
            await session.commit()
        else:
            await session.rollback()
    await message.answer(msg)


# ------------------------------------------------------------------
# WISHLIST (/wishlist)
# ------------------------------------------------------------------
async def _show_wishlist(target, owner_id: int, db_user: User) -> None:
    lang = get_lang(db_user)
    async with get_session() as session:
        cards = await AuctionWishlistService.get_user_wishlist(session, db_user.id)
    txt = tr(lang, "wish_hdr", n=len(cards))
    await edit_or_answer(target, txt, reply_markup=wishlist_kb(owner_id, cards, lang=lang))


@router.message(Command("wishlist"))
async def cmd_wishlist(message: Message, db_user: User) -> None:
    await _show_wishlist(message, message.from_user.id, db_user)


@router.callback_query(WishCb.filter(F.act == "v"))
async def cb_wishlist(callback: CallbackQuery, db_user: User) -> None:
    await _show_wishlist(callback, callback.from_user.id, db_user)


@router.callback_query(WishCb.filter(F.act == "add"))
async def cb_wishlist_add(callback: CallbackQuery, callback_data: WishCb, db_user: User) -> None:
    async with get_session() as session:
        _added, msg = await AuctionWishlistService.toggle_wishlist(session, db_user.id, callback_data.cid)
        await session.commit()
    await callback.answer(msg[:190], show_alert=True)


@router.callback_query(WishCb.filter(F.act == "del"))
async def cb_wishlist_del(callback: CallbackQuery, callback_data: WishCb, db_user: User) -> None:
    async with get_session() as session:
        await AuctionWishlistService.toggle_wishlist(session, db_user.id, callback_data.cid)
        await session.commit()
    await _show_wishlist(callback, callback.from_user.id, db_user)


@router.callback_query(WishCb.filter(F.act == "top"))
async def cb_wishlist_top(callback: CallbackQuery, db_user: User) -> None:
    lang = get_lang(db_user)
    async with get_session() as session:
        top = await AuctionWishlistService.get_most_wanted(session, limit=10)
    lines = ["🔥 <b>MOST WANTED CARDS</b>\n"]
    for idx, (c, cnt) in enumerate(top, start=1):
        meta = get_rarity_meta(c.rarity)
        lines.append(f"{idx}. {meta.emoji} <b>{esc(c.character_name)}</b> ({esc(c.anime_name)}) — 💖 {cnt}")
    if not top:
        lines.append("📭 —")
    await edit_or_answer(callback, "\n".join(lines), reply_markup=back_home_kb(callback.from_user.id, "home", lang))


# ------------------------------------------------------------------
# VIP (/vip)
# ------------------------------------------------------------------
async def _show_vip(target, owner_id: int, db_user: User) -> None:
    lang = get_lang(db_user)
    async with get_session() as session:
        cfg = await CommunityService.get_vip_config(session)
        u = await session.get(User, db_user.id) or db_user
    active_str = (
        tr(lang, "vip_active_status", tier=esc(u.vip_tier), dt=f"{ensure_utc(u.vip_until):%d.%m.%Y}")
        if CommunityService.is_vip_active(u)
        else tr(lang, "vip_free_status")
    )
    txt = tr(lang, "vip_title", status=active_str)
    await edit_or_answer(target, txt, reply_markup=vip_shop_kb(owner_id, cfg, lang=lang))


@router.message(Command("vip"))
async def cmd_vip(message: Message, db_user: User) -> None:
    await _show_vip(message, message.from_user.id, db_user)


@router.callback_query(VipCb.filter(F.act == "v"))
async def cb_vip(callback: CallbackQuery, db_user: User) -> None:
    await _show_vip(callback, callback.from_user.id, db_user)


@router.callback_query(VipCb.filter(F.act == "buy_coins"))
async def cb_vip_buy_coins(callback: CallbackQuery, callback_data: VipCb, db_user: User) -> None:
    async with get_session() as session:
        u = await session.get(User, db_user.id)
        ok, msg = await CommunityService.buy_vip_with_coins(session, u, callback_data.tier)
        if ok:
            await session.commit()
        else:
            await session.rollback()
    await callback.answer(msg[:190], show_alert=True)
    if ok:
        await _show_vip(callback, callback.from_user.id, db_user)


@router.callback_query(VipCb.filter(F.act == "buy_stars"))
async def cb_vip_buy_stars(callback: CallbackQuery, callback_data: VipCb) -> None:
    if not get_settings().enable_telegram_stars:
        await callback.answer("Telegram Stars vaqtincha o‘chirilgan.", show_alert=True)
        return
    async with get_session() as session:
        cfg = await CommunityService.get_vip_config(session)
    tier = (cfg.get("tiers") or {}).get(callback_data.tier)
    if not tier or int(tier.get("stars", 0)) <= 0:
        await callback.answer("VIP tarif topilmadi.", show_alert=True)
        return
    stars = int(tier["stars"])
    await callback.bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"💎 {tier.get('name', callback_data.tier)}",
        description=f"30 kunlik VIP obuna ({stars} Stars)",
        payload=f"vip:{callback_data.tier}:{callback.from_user.id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="VIP Obuna", amount=stars)],
    )
    await callback.answer()


# ------------------------------------------------------------------
# ACHIEVEMENTS, PROFILE CUSTOMIZATION & SEASON
# ------------------------------------------------------------------
@router.message(Command("achievements", "season"))
async def cmd_ach(message: Message, db_user: User) -> None:
    lang = get_lang(db_user)
    async with get_session() as session:
        all_ach, unlocked = await GameplayService.get_user_achievements(session, db_user.id)
    lines = [f"🏅 <b>ACHIEVEMENTS</b> ({len(unlocked)}/{len(all_ach)})\n"]
    for a in all_ach:
        mark = "✅" if a.id in unlocked else "🔒"
        lines.append(f"{mark} {a.icon} <b>{esc(a.title)}</b> — {esc(a.description)} (+{a.reward_coins:,} 🪙)")
    await message.answer("\n".join(lines), reply_markup=profile_custom_kb(message.from_user.id, lang=lang))


@router.callback_query(AchCb.filter(F.act == "v"))
async def cb_ach_list(callback: CallbackQuery, db_user: User) -> None:
    lang = get_lang(db_user)
    async with get_session() as session:
        all_ach, unlocked = await GameplayService.get_user_achievements(session, db_user.id)
    lines = [f"🏅 <b>ACHIEVEMENTS</b> ({len(unlocked)}/{len(all_ach)})\n"]
    for a in all_ach:
        mark = "✅" if a.id in unlocked else "🔒"
        lines.append(f"{mark} {a.icon} <b>{esc(a.title)}</b> — {esc(a.description)}")
    await edit_or_answer(callback, "\n".join(lines), reply_markup=profile_custom_kb(callback.from_user.id, lang=lang))


@router.callback_query(AchCb.filter(F.act == "frames"))
async def cb_profile_custom(callback: CallbackQuery, db_user: User) -> None:
    lang = get_lang(db_user)
    await edit_or_answer(
        callback,
        "🎨 <b>PROFILE STYLE</b>\n\nSelect frame or background:",
        reply_markup=profile_custom_kb(callback.from_user.id, lang=lang),
    )


@router.callback_query(AchCb.filter(F.act == "equip"))
async def cb_equip_item(callback: CallbackQuery, callback_data: AchCb, db_user: User) -> None:
    code = callback_data.code
    kind, val = ("frame", code[2:]) if code.startswith("f_") else ("bg", code[2:])
    async with get_session() as session:
        u = await session.get(User, db_user.id)
        ok = await GameplayService.equip_profile_item(session, u, kind, val)
        if ok:
            await session.commit()
    await callback.answer("✅ Updated!" if ok else "❌ Not found.", show_alert=True)


@router.callback_query(AchCb.filter(F.act == "season"))
async def cb_season_view(callback: CallbackQuery, db_user: User) -> None:
    lang = get_lang(db_user)
    async with get_session() as session:
        s = await GameplayService.get_active_season(session)
        top = await GameplayService.get_season_leaderboard(session, s.id, limit=10) if s else []
    if s is None:
        await callback.answer("No active season.", show_alert=True)
        return
    lines = [
        f"🏆 <b>{esc(s.name)}</b>\n",
        f"⏳ Ends: <b>{ensure_utc(s.ends_at):%d.%m.%Y %H:%M} UTC</b>\n",
        "<b>TOP 10 PLAYERS:</b>",
    ]
    for idx, (sp, u) in enumerate(top, start=1):
        lines.append(f"{idx}. {esc(u.display_name)} — ⭐ <b>{sp.points}</b> pts ({sp.cards_claimed})")
    if not top:
        lines.append("📭 —")
    await edit_or_answer(callback, "\n".join(lines), reply_markup=back_home_kb(callback.from_user.id, "profile", lang))


# ------------------------------------------------------------------
# NOTIFICATIONS (/notif)
# ------------------------------------------------------------------
async def _show_notifs(target, owner_id: int, db_user: User) -> None:
    lang = get_lang(db_user)
    async with get_session() as session:
        notifs, total = await CommunityService.list_notifications(session, db_user.id, page=1, page_size=12)
    lines = [tr(lang, "notif_hdr", n=total)]
    for n in notifs:
        dot = "🔵" if not n.is_read else "⚪"
        lines.append(f"{dot} <b>{esc(n.title)}</b>\n{esc(n.body)}\n")
    if not notifs:
        lines.append(tr(lang, "notif_empty"))
    await edit_or_answer(target, "\n".join(lines), reply_markup=notifications_kb(owner_id, lang=lang))


@router.message(Command("notif", "notifications"))
async def cmd_notifs(message: Message, db_user: User) -> None:
    await _show_notifs(message, message.from_user.id, db_user)


@router.callback_query(NotifCb.filter(F.act == "v"))
async def cb_notifs(callback: CallbackQuery, db_user: User) -> None:
    await _show_notifs(callback, callback.from_user.id, db_user)


@router.callback_query(NotifCb.filter(F.act == "read_all"))
async def cb_notifs_read(callback: CallbackQuery, db_user: User) -> None:
    async with get_session() as session:
        await CommunityService.mark_all_read(session, db_user.id)
        await session.commit()
    await callback.answer("✅")
    await _show_notifs(callback, callback.from_user.id, db_user)


# ------------------------------------------------------------------
# SUPPORT & BAN APPEAL (/support) + FORCED SUB CHECK
# ------------------------------------------------------------------
@router.message(Command("support", "report"))
async def cmd_support(message: Message, db_user: User) -> None:
    lang = get_lang(db_user)
    async with get_session() as session:
        tickets = await CommunityService.list_tickets(session, user_id=db_user.id)
    await message.answer(
        tr(lang, "sup_hdr"),
        reply_markup=support_menu_kb(message.from_user.id, tickets, lang=lang),
    )


@router.callback_query(SupCb.filter(F.act == "v"))
async def cb_support_menu(callback: CallbackQuery, db_user: User) -> None:
    lang = get_lang(db_user)
    async with get_session() as session:
        tickets = await CommunityService.list_tickets(session, user_id=db_user.id)
    await edit_or_answer(
        callback,
        tr(lang, "sup_hdr"),
        reply_markup=support_menu_kb(callback.from_user.id, tickets, lang=lang),
    )


@router.callback_query(SupCb.filter(F.act == "new"))
async def cb_support_new(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(V2UserStates.waiting_support_subject)
    await state.update_data(owner_tg_id=callback.from_user.id)
    await callback.message.answer("📝 Murojaat mavzusini yozing / Subject:\n<i>/cancel</i>")
    await callback.answer()


@router.message(V2UserStates.waiting_support_subject, NotCommandFilter())
async def fsm_sup_subject(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    await state.update_data(sup_subject=(message.text or "Ticket").strip()[:200])
    await state.set_state(V2UserStates.waiting_support_body)
    await message.answer("✍️ Endi murojaat matnini yozing / Message body:")


@router.message(V2UserStates.waiting_support_body, NotCommandFilter())
async def fsm_sup_body(message: Message, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    subject = data.get("sup_subject", "Ticket")
    await state.clear()
    async with get_session() as session:
        t = await CommunityService.create_ticket(
            session, db_user.id, subject, (message.text or "").strip(), message.from_user.id
        )
        await session.commit()
    await message.answer(f"✅ Ticket <b>#{t.id}</b> sent!")


@router.callback_query(SupCb.filter(F.act == "view"))
async def cb_support_view(callback: CallbackQuery, callback_data: SupCb, db_user: User) -> None:
    lang = get_lang(db_user)
    async with get_session() as session:
        res = await CommunityService.get_ticket_with_messages(session, callback_data.tid)
    if res is None or res[0].user_id != db_user.id:
        await callback.answer(tr(lang, "not_found"), show_alert=True)
        return
    t, msgs, _ = res
    lines = [f"💬 <b>#{t.id}: {esc(t.subject)}</b>\nStatus: <b>{t.status}</b>\n"]
    for m in msgs[-10:]:
        who = "👤 User" if m.sender_type == "user" else "🛡 Admin"
        lines.append(f"<b>{who}:</b> {esc(m.body)}")
    await edit_or_answer(callback, "\n".join(lines), reply_markup=back_home_kb(callback.from_user.id, "home", lang))


@router.callback_query(SupCb.filter(F.act == "check_sub"))
async def cb_check_sub(callback: CallbackQuery, db_user: User, is_bot_admin: bool = False) -> None:
    lang = get_lang(db_user)
    async with get_session() as session:
        ok, missing = await CommunityService.check_user_subscriptions(
            callback.bot, session, db_user, callback.from_user.id
        )
        await session.commit()
    if not ok and missing:
        await callback.answer(tr(lang, "sub_missing"), show_alert=True)
        return
    await callback.answer(tr(lang, "sub_verified"), show_alert=True)
    is_adm = is_bot_admin or is_admin_user(callback.from_user)
    await edit_or_answer(
        callback,
        tr(lang, "sub_verified_home"),
        reply_markup=main_menu_kb(callback.from_user.id, is_admin=is_adm, lang=lang),
    )


# ------------------------------------------------------------------
# TWO-SIDED CARD-FOR-CARD TRADING (/trade)
# ------------------------------------------------------------------
@router.message(Command("trade"))
async def cmd_trade(message: Message, state: FSMContext, db_user: User) -> None:
    lang = get_lang(db_user)
    await state.clear()
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(tr(lang, "trade_help"))
        return
    async with get_session() as session:
        partner = await UserService.find_user(session, parts[1].strip())
        if partner is None or partner.id == db_user.id:
            await message.answer(tr(lang, "user_not_found"))
            return
        my_cards = (
            await session.execute(
                select(UserCard, Card)
                .join(Card, Card.id == UserCard.card_id)
                .where(UserCard.user_id == db_user.id, UserCard.is_locked.is_(False))
                .limit(20)
            )
        ).all()
    if not my_cards:
        await message.answer("📭 No available cards.")
        return
    rows = [
        [
            InlineKeyboardButton(
                text=f"🎴 {c.character_name[:28]} ({c.rarity})",
                callback_data=TradeCb(owner_id=message.from_user.id, act="pick", tid=partner.id, ucid=uc.id).pack(),
            )
        ]
        for uc, c in my_cards
    ]
    await message.answer(
        f"🤝 <b>{esc(partner.display_name)}</b>:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(TradeCb.filter(F.act == "v"))
async def cb_trade_help(callback: CallbackQuery, db_user: User) -> None:
    lang = get_lang(db_user)
    await edit_or_answer(
        callback,
        tr(lang, "trade_help"),
        reply_markup=back_home_kb(callback.from_user.id, "home", lang),
    )


@router.callback_query(TradeCb.filter(F.act == "pick"))
async def cb_trade_pick(callback: CallbackQuery, callback_data: TradeCb, db_user: User) -> None:
    async with get_session() as session:
        ok, msg, trade = await AuctionWishlistService.create_trade(
            session, db_user.id, callback_data.tid, callback_data.ucid
        )
        if not ok or trade is None:
            await session.rollback()
            await callback.answer(msg[:190], show_alert=True)
            return
        partner = await session.get(User, callback_data.tid)
        p_cards = (
            await session.execute(
                select(UserCard, Card)
                .join(Card, Card.id == UserCard.card_id)
                .where(UserCard.user_id == partner.id, UserCard.is_locked.is_(False))
                .limit(15)
            )
        ).all()
        await session.commit()

    if partner:
        rows = [
            [
                InlineKeyboardButton(
                    text=f"🔁 {c.character_name[:26]} ({c.rarity})",
                    callback_data=TradeCb(owner_id=partner.telegram_id, act="accept", tid=trade.id, ucid=uc.id).pack(),
                )
            ]
            for uc, c in p_cards
        ]
        rows.append(
            [
                InlineKeyboardButton(
                    text="❌ Rad etish",
                    callback_data=TradeCb(owner_id=partner.telegram_id, act="reject", tid=trade.id).pack(),
                )
            ]
        )
        try:
            await callback.bot.send_message(
                partner.telegram_id,
                f"🤝 <b>{esc(db_user.display_name)}</b> sizga karta almashishni taklif qildi!\n"
                "Almashtirmoqchi bo‘lgan kartangizni tanlang:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            )
        except Exception:
            pass
    await edit_or_answer(callback, "✅ Almashuv taklifi yuborildi! Hamkoringiz karta tanlashini kuting.")


@router.callback_query(TradeCb.filter(F.act == "accept"))
async def cb_trade_accept(callback: CallbackQuery, callback_data: TradeCb, db_user: User) -> None:
    async with get_session() as session:
        ok, msg = await AuctionWishlistService.confirm_trade(
            session, callback_data.tid, db_user.id, callback_data.ucid
        )
        if ok:
            await session.commit()
        else:
            await session.rollback()
    await callback.answer(msg[:190], show_alert=True)
    if ok:
        await edit_or_answer(callback, msg)


@router.callback_query(TradeCb.filter(F.act == "reject"))
async def cb_trade_reject(callback: CallbackQuery, callback_data: TradeCb, db_user: User) -> None:
    async with get_session() as session:
        tr = await session.get(CardTrade, callback_data.tid)
        if tr and tr.partner_user_id == db_user.id and tr.status == "pending_pick":
            tr.status = "rejected"
            uc = await session.get(UserCard, tr.initiator_uc_id)
            if uc:
                uc.is_locked = False
                uc.card_state = "OWNED"
            await session.commit()
    await callback.answer("❌ Almashuv rad etildi.")
    await edit_or_answer(callback, "❌ Almashuv rad etildi.")
