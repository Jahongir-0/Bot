import asyncio
import logging
import re
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from config.settings import get_settings
from database.session import get_session
from keyboards.main_menu import back_home_kb, main_menu_kb
from keyboards.v2 import (
    VIP_ONLY_FRAMES,
    auction_detail_kb,
    auctions_list_kb,
    coinflip_pick_kb,
    dice_pick_kb,
    games_menu_kb,
    lucky_pick_kb,
    notifications_kb,
    ach_kb,
    profile_style_kb,
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
from utils.callback_data import AchCb, AucCb, GameCb, NavCb, NotifCb, QuizCb, SupCb, TradeCb, VipCb, WishCb
from utils.constants import PROFILE_BACKGROUNDS, PROFILE_FRAMES, get_rarity_meta
from utils.formatting import header, esc, fmt_coins, format_card_block
from utils.tg_helpers import NotCommandFilter, edit_or_answer, is_admin_user, send_card_message

logger = logging.getLogger(__name__)
LINE_SEP = "━━━━━━━━━━━━━━━"
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


@router.callback_query(QuizCb.filter(F.act == "play"))
async def cb_quiz_play(callback: CallbackQuery, callback_data: QuizCb, db_user: User) -> None:
    async with get_session() as session:
        qq = await GameplayService.get_next_unanswered_question(session, callback_data.qid, db_user.id)
    if qq is None:
        await callback.answer("🎉 Siz ushbu quizdagi barcha savollarga javob bergansiz!", show_alert=True)
        return
    txt = f"🧠 <b>Savol #{qq.order_num}</b>\n\n{esc(qq.question_text)}"
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
        async with get_session() as session:
            next_q = await GameplayService.get_next_unanswered_question(session, qq.quiz_id, db_user.id)
        if next_q is not None:
            await edit_or_answer(
                callback,
                f"{esc(msg)}\n\n🧠 <b>Keyingi savol #{next_q.order_num}</b>\n\n{esc(next_q.question_text)}",
                reply_markup=quiz_question_kb(callback.from_user.id, next_q),
            )
        else:
            await _show_quizzes(callback, callback.from_user.id)


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
# TOSH O'YINI (/game): son tanlaysiz -> bot tosh tashlaydi -> mos tushsa yutasiz
# ------------------------------------------------------------------
_PLAYING: set = set()


def _game_menu_text(
    cfg: dict,
    dice_en: bool,
    dice_used: int,
    flip_en: bool,
    flip_used: int,
    lucky_en: bool,
    lucky_used: int,
    coins: int = 0,
) -> str:
    d_limit = int(cfg.get("dice_daily_limit", 3))
    f_limit = int(cfg.get("coinflip_daily_limit", 5))
    l_limit = int(cfg.get("lucky_daily_limit", 3))
    d_left = max(0, d_limit - dice_used)
    f_left = max(0, f_limit - flip_used)
    l_left = max(0, l_limit - lucky_used)
    lines = [
        "🎮 <b>MINI O‘YINLAR MARKAZI</b>",
        "━━━━━━━━━━━━━━━",
        f"🪙 Balansingiz: <b>{fmt_coins(coins)}</b> coin\n",
        f"🎲 <b>Tosh o‘yini</b> — 1..6 sonni toping (+{fmt_coins(cfg.get('dice_jackpot_reward', 300))} 🪙)",
        f"   {'🔴 O‘chirilgan' if not dice_en else f'🎟 Bugun qoldi: <b>{d_left}/{d_limit}</b>'}\n",
        "🪙 <b>Tanga tashlash</b> — gerb yoki raqam (2x yutuq)",
        f"   {'🔴 O‘chirilgan' if not flip_en else f'🎟 Bugun qoldi: <b>{f_left}/{f_limit}</b>'}\n",
        f"🃏 <b>Omadli karta</b> — bepul omad ({cfg.get('lucky_min_reward', 60)}–{cfg.get('lucky_max_reward', 350)} 🪙)",
        f"   {'🔴 O‘chirilgan' if not lucky_en else f'🎟 Bugun qoldi: <b>{l_left}/{l_limit}</b>'}",
    ]
    if not (
        (dice_en and d_left > 0)
        or (flip_en and f_left > 0)
        or (lucky_en and l_left > 0)
    ):
        lines.append("\n⏳ <i>Bugungi barcha mini-o‘yin urinishlaringiz tugadi. Ertaga qayting!</i>")
    return "\n".join(lines)


async def _game_state(user_id: int):
    async with get_session() as session:
        user = await session.get(User, user_id)
        cfg = await GameplayService.get_game_config(session)
        d_en, d_used, d_lim = await GameplayService.get_dice_status(session, user_id)
        f_en, f_used, f_lim = await GameplayService.get_coinflip_status(session, user_id)
        l_en, l_used, l_lim = await GameplayService.get_lucky_status(session, user_id)
        coins = int(user.coins or 0) if user is not None else 0
    return {
        "cfg": cfg,
        "coins": coins,
        "dice_enabled": d_en,
        "dice_used": d_used,
        "dice_limit": d_lim,
        "flip_enabled": f_en,
        "flip_used": f_used,
        "flip_limit": f_lim,
        "lucky_enabled": l_en,
        "lucky_used": l_used,
        "lucky_limit": l_lim,
    }


@router.message(Command("game", "games"))
async def cmd_game(message: Message, db_user: User) -> None:
    st = await _game_state(db_user.id)
    await message.answer(
        _game_menu_text(
            st["cfg"],
            st["dice_enabled"],
            st["dice_used"],
            st["flip_enabled"],
            st["flip_used"],
            st["lucky_enabled"],
            st["lucky_used"],
            coins=st["coins"],
        ),
        reply_markup=games_menu_kb(
            message.from_user.id,
            can_dice=st["dice_enabled"] and st["dice_used"] < st["dice_limit"],
            can_flip=st["flip_enabled"] and st["flip_used"] < st["flip_limit"],
            can_lucky=st["lucky_enabled"] and st["lucky_used"] < st["lucky_limit"],
        ),
    )


@router.callback_query(GameCb.filter(F.act == "v"))
async def cb_games_menu(callback: CallbackQuery, db_user: User) -> None:
    st = await _game_state(db_user.id)
    await edit_or_answer(
        callback,
        _game_menu_text(
            st["cfg"],
            st["dice_enabled"],
            st["dice_used"],
            st["flip_enabled"],
            st["flip_used"],
            st["lucky_enabled"],
            st["lucky_used"],
            coins=st["coins"],
        ),
        reply_markup=games_menu_kb(
            callback.from_user.id,
            can_dice=st["dice_enabled"] and st["dice_used"] < st["dice_limit"],
            can_flip=st["flip_enabled"] and st["flip_used"] < st["flip_limit"],
            can_lucky=st["lucky_enabled"] and st["lucky_used"] < st["lucky_limit"],
        ),
    )
    await callback.answer()


@router.callback_query(GameCb.filter(F.act == "dice"))
async def cb_game_dice(callback: CallbackQuery, db_user: User) -> None:
    """Son tanlash ekrani."""
    st = await _game_state(db_user.id)
    if not st["dice_enabled"]:
        await callback.answer("🔴 O‘yin hozircha o‘chirilgan.", show_alert=True)
        return
    if st["dice_used"] >= st["dice_limit"]:
        await callback.answer(f"🎟 Bugungi urinishlar tugadi ({st['dice_limit']}/{st['dice_limit']}). Ertaga qayting!", show_alert=True)
        return
    await edit_or_answer(
        callback,
        f"🎲 <b>SONNI TANLANG</b>\n\nTosh qaysi songa tushadi deb o‘ylaysiz?\n🏆 To‘g‘ri topsangiz: <b>+{fmt_coins(st['cfg'].get('dice_jackpot_reward', 300))} 🪙</b>",
        reply_markup=dice_pick_kb(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(GameCb.filter(F.act == "pick"))
async def cb_game_pick(callback: CallbackQuery, callback_data: GameCb, db_user: User) -> None:
    guess = int(callback_data.val)
    if not 1 <= guess <= 6:
        await callback.answer("Noto‘g‘ri son", show_alert=True)
        return
    uid = db_user.id
    if uid in _PLAYING:
        await callback.answer("⏳ Tosh aylanmoqda, biroz kuting...")
        return
    _PLAYING.add(uid)
    try:
        st = await _game_state(uid)
        if not st["dice_enabled"]:
            await callback.answer("🔴 O‘yin hozircha o‘chirilgan.", show_alert=True)
            return
        if st["dice_used"] >= st["dice_limit"]:
            await callback.answer(f"🎟 Bugungi urinishlar tugadi ({st['dice_limit']}/{st['dice_limit']}). Ertaga qayting!", show_alert=True)
            return
        await callback.answer()
        chat_id = callback.message.chat.id
        try:
            await callback.message.edit_text(f"🎲 Siz <b>{guess}</b> ni tanladingiz.\nTosh aylanmoqda...", reply_markup=None)
        except Exception:
            pass

        # Telegram'ning haqiqiy zari: natijani Telegram o'zi tasodifiy tanlaydi (aldab bo'lmaydi)
        dice_msg = await callback.bot.send_dice(chat_id, emoji="🎲")
        value = int(dice_msg.dice.value)
        await asyncio.sleep(3.8)  # animatsiya tugashini kutamiz

        used_now = st["dice_used"]
        limit_now = st["dice_limit"]
        async with get_session() as session:
            user = await session.get(User, uid)
            if user is None:
                ok, msg = False, "Foydalanuvchi topilmadi."
            else:
                ok, msg, _roll, _coins = await GameplayService.play_dice_game(
                    session, user, dice_value=value, guessed_number=guess
                )
            if ok:
                await session.commit()
                _en, used_now, limit_now = await GameplayService.get_dice_status(session, uid)
            else:
                await session.rollback()
        left = max(0, limit_now - used_now)
        footer = f"\n\n🎟 Qolgan urinishlar: <b>{left}/{limit_now}</b>"
        b = InlineKeyboardBuilder()
        if left > 0:
            b.row(InlineKeyboardButton(text="🎲 Yana o‘ynash", callback_data=GameCb(owner_id=callback.from_user.id, act="dice").pack()))
        b.row(InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=callback.from_user.id, target="home").pack()))
        await callback.bot.send_message(chat_id, msg + (footer if ok else ""), reply_markup=b.as_markup())
    except Exception:
        logger.exception("Tosh o'yinida xato")
        await callback.bot.send_message(callback.message.chat.id, "⚠️ O‘yinda xatolik bo‘ldi. Iltimos, qayta urinib ko‘ring.")
    finally:
        _PLAYING.discard(uid)


@router.callback_query(GameCb.filter(F.act == "flip"))
async def cb_game_flip_menu(callback: CallbackQuery, db_user: User) -> None:
    st = await _game_state(db_user.id)
    if not st["flip_enabled"]:
        await callback.answer("🔴 Tanga tashlash hozircha o‘chirilgan.", show_alert=True)
        return
    if st["flip_used"] >= st["flip_limit"]:
        await callback.answer(f"🎟 Bugungi urinishlar tugadi ({st['flip_limit']}/{st['flip_limit']}).", show_alert=True)
        return
    left = max(0, st["flip_limit"] - st["flip_used"])
    await edit_or_answer(
        callback,
        (
            f"🪙 <b>TANGA TASHLASH (2x)</b>\n\n"
            f"Tomon va garov miqdorini tanlang:\n"
            f"💰 Balansingiz: <b>{fmt_coins(st['coins'])} 🪙</b>\n"
            f"🎟 Qolgan urinishlar: <b>{left}/{st['flip_limit']}</b>"
        ),
        reply_markup=coinflip_pick_kb(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(GameCb.filter(F.act == "flip_go"))
async def cb_game_flip_go(callback: CallbackQuery, callback_data: GameCb, db_user: User) -> None:
    uid = db_user.id
    used_now = 0
    limit_now = 5
    new_coins = int(db_user.coins or 0)
    async with get_session() as session:
        user = await session.get(User, uid)
        if user is None:
            ok, msg = False, "Foydalanuvchi topilmadi."
        else:
            ok, msg, _won, _delta = await GameplayService.play_coin_flip(
                session, user, choice=callback_data.extra, bet=callback_data.val
            )
        if ok:
            new_coins = int(user.coins or 0)
            await session.commit()
            _en, used_now, limit_now = await GameplayService.get_coinflip_status(session, uid)
        else:
            await session.rollback()
    if not ok:
        await callback.answer(msg[:190], show_alert=True)
        return
    left = max(0, limit_now - used_now)
    b = InlineKeyboardBuilder()
    if left > 0:
        b.row(InlineKeyboardButton(text="🪙 Yana o‘ynash", callback_data=GameCb(owner_id=callback.from_user.id, act="flip").pack()))
    b.row(
        InlineKeyboardButton(text="🎮 O‘yinlar", callback_data=GameCb(owner_id=callback.from_user.id, act="v").pack()),
        InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=callback.from_user.id, target="home").pack()),
    )
    await edit_or_answer(
        callback,
        f"{msg}\n\n💰 Balans: <b>{fmt_coins(new_coins)} 🪙</b>\n🎟 Qolgan urinishlar: <b>{left}/{limit_now}</b>",
        reply_markup=b.as_markup(),
    )
    await callback.answer()


@router.callback_query(GameCb.filter(F.act == "lucky"))
async def cb_game_lucky_menu(callback: CallbackQuery, db_user: User) -> None:
    st = await _game_state(db_user.id)
    if not st["lucky_enabled"]:
        await callback.answer("🔴 Omadli karta o‘yini hozircha o‘chirilgan.", show_alert=True)
        return
    if st["lucky_used"] >= st["lucky_limit"]:
        await callback.answer(f"🎟 Bugungi urinishlar tugadi ({st['lucky_limit']}/{st['lucky_limit']}).", show_alert=True)
        return
    left = max(0, st["lucky_limit"] - st["lucky_used"])
    await edit_or_answer(
        callback,
        f"🃏 <b>OMADLI KARTA</b>\n\n3 ta yopiq kartadan birini tanlang va tasodifiy coin yuting!\n🎟 Qolgan urinishlar: <b>{left}/{st['lucky_limit']}</b>",
        reply_markup=lucky_pick_kb(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(GameCb.filter(F.act == "lucky_pick"))
async def cb_game_lucky_pick(callback: CallbackQuery, callback_data: GameCb, db_user: User) -> None:
    uid = db_user.id
    used_now = 0
    limit_now = 3
    async with get_session() as session:
        user = await session.get(User, uid)
        if user is None:
            ok, msg = False, "Foydalanuvchi topilmadi."
        else:
            ok, msg, _coins = await GameplayService.play_lucky_card(session, user, picked_slot=callback_data.val)
        if ok:
            await session.commit()
            _en, used_now, limit_now = await GameplayService.get_lucky_status(session, uid)
        else:
            await session.rollback()
    if not ok:
        await callback.answer(msg[:190], show_alert=True)
        return
    left = max(0, limit_now - used_now)
    b = InlineKeyboardBuilder()
    if left > 0:
        b.row(InlineKeyboardButton(text="🃏 Yana ochish", callback_data=GameCb(owner_id=callback.from_user.id, act="lucky").pack()))
    b.row(
        InlineKeyboardButton(text="🎮 O‘yinlar", callback_data=GameCb(owner_id=callback.from_user.id, act="v").pack()),
        InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=callback.from_user.id, target="home").pack()),
    )
    await edit_or_answer(callback, f"{msg}\n\n🎟 Qolgan urinishlar: <b>{left}/{limit_now}</b>", reply_markup=b.as_markup())
    await callback.answer()


# ------------------------------------------------------------------
# AUCTION (/auction)
# ------------------------------------------------------------------
async def _show_auctions(target, owner_id: int) -> None:
    async with get_session() as session:
        items, total = await AuctionWishlistService.list_active_auctions(session, page=1, page_size=10)
    if not items:
        await edit_or_answer(
            target,
            f"{header('🔨 <b>AUKSION</b>')}\n📭 Hozircha faol auksion yo‘q.\n\n💡 Yangi auksion boshlanganda xabar beramiz.",
            reply_markup=auctions_list_kb(owner_id, []),
        )
        return
    lines = [header("🔨 <b>AUKSION</b>"), f"🟢 Faol auksionlar: <b>{total}</b> ta\n"]
    for auc, card in items:
        meta = get_rarity_meta(card.rarity)
        lines.append(f"{meta.emoji} <b>{esc(card.character_name)}</b>")
        lines.append(f"      💰 {fmt_coins(auc.current_price)} 🪙 · 📈 {auc.bid_count} stavka")
    lines.append("\n👇 Auksionni tanlang:")
    await edit_or_answer(target, "\n".join(lines), reply_markup=auctions_list_kb(owner_id, items))


@router.message(Command("auction"))
async def cmd_auction(message: Message) -> None:
    await _show_auctions(message, message.from_user.id)


@router.callback_query(AucCb.filter(F.act == "v"))
async def cb_auction_list(callback: CallbackQuery) -> None:
    await _show_auctions(callback, callback.from_user.id)


@router.callback_query(AucCb.filter(F.act == "det"))
async def cb_auction_detail(callback: CallbackQuery, callback_data: AucCb) -> None:
    async with get_session() as session:
        det = await AuctionWishlistService.get_auction_detail(session, callback_data.aid)
    if det is None:
        await callback.answer("Auksion topilmadi.", show_alert=True)
        return
    auc, card, leader = det
    leader_name = esc(leader.display_name) if leader else "Hali yo‘q"
    txt = (
        f"{header(f'🔨 <b>AUKSION #{auc.id}</b>')}\n"
        f"{format_card_block(card)}\n"
        f"{LINE_SEP}\n"
        f"💰 Joriy narx: <b>{fmt_coins(auc.current_price)} 🪙</b>\n"
        f"📈 Eng kam qadam: <b>+{fmt_coins(auc.min_step)}</b> 🪙\n"
        f"👑 Yetakchi: <b>{leader_name}</b> · {auc.bid_count} stavka\n"
        f"⏳ Tugaydi: <b>{ensure_utc(auc.ends_at):%d.%m.%Y %H:%M} UTC</b>"
    )
    if callback.message:
        await send_card_message(callback.bot, callback.message.chat.id, card, txt, auction_detail_kb(callback.from_user.id, auc))
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
    async with get_session() as session:
        cards = await AuctionWishlistService.get_user_wishlist(session, db_user.id)
    txt = (
        f"{header('💖 <b>ISTAKLAR RO‘YXATI</b>')}\n"
        f"📌 Ro‘yxatda: <b>{len(cards)}</b> ta karta\n\n"
        "Kerakli kartani qo‘shib qo‘ying: u guruhga tushsa, bozorga yoki auksionga chiqsa — sizga darhol xabar beramiz!\n\n"
        "💡 <i>Karta qo‘shish: 🔍 Qidiruvdan kartani oching va «💖 Istaklarga» tugmasini bosing.</i>"
    )
    await edit_or_answer(target, txt, reply_markup=wishlist_kb(owner_id, cards))


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
async def cb_wishlist_top(callback: CallbackQuery) -> None:
    async with get_session() as session:
        top = await AuctionWishlistService.get_most_wanted(session, limit=10)
    lines = [header("🔥 <b>ENG KO‘P ISTALGAN KARTALAR</b>")]
    for idx, (c, cnt) in enumerate(top, start=1):
        meta = get_rarity_meta(c.rarity)
        lines.append(f"{idx}. {meta.emoji} <b>{esc(c.character_name)}</b> ({esc(c.anime_name)}) — 💖 {cnt} ta")
    if not top:
        lines.append("📭 Hozircha bo‘sh.")
    await edit_or_answer(callback, "\n".join(lines), reply_markup=back_home_kb(callback.from_user.id, "home"))


# ------------------------------------------------------------------
# VIP (/vip)
# ------------------------------------------------------------------
async def _show_vip(target, owner_id: int, db_user: User) -> None:
    async with get_session() as session:
        cfg = await CommunityService.get_vip_config(session)
        u = await session.get(User, db_user.id) or db_user
    active_str = (
        f"💎 Faol: <b>{esc(u.vip_tier)}</b> ({ensure_utc(u.vip_until):%d.%m.%Y} gacha)"
        if CommunityService.is_vip_active(u)
        else "▫️ Hozircha oddiy status"
    )
    txt = (
        f"{header('💎 <b>VIP OBUNA</b>')}\n"
        f"📌 Holat: {active_str}\n\n"
        "✨ <b>VIP imtiyozlari</b>\n"
        "🎁 Kunlik bonus 1.5x – 3x ko‘proq\n"
        "🚫 Reklamalarsiz\n"
        "🔓 Majburiy obunalardan ozod (Gold/Diamond)\n"
        "🖼 Maxsus VIP ramkalar va 💎 nishon\n\n"
        "👇 Tarifni tanlang (coin yoki Stars bilan):"
    )
    await edit_or_answer(target, txt, reply_markup=vip_shop_kb(owner_id, cfg))


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
def _ach_text(all_ach, unlocked) -> str:
    done = [a for a in all_ach if a.id in unlocked]
    todo = [a for a in all_ach if a.id not in unlocked]
    lines = [
        "🏅 <b>YUTUQLAR</b>",
        "━━━━━━━━━━━━━━━",
        f"✅ Ochilgan: <b>{len(done)}</b> / {len(all_ach)}",
    ]
    if done:
        lines += ["", "🏆 <b>Sizning nishonlaringiz</b>"]
        lines += [f"{a.icon} <b>{esc(a.title)}</b> — {esc(a.description)}" for a in done]
    if todo:
        lines += ["", "🔒 <b>Hali ochilmagan</b>"]
        lines += [f"{a.icon} {esc(a.title)} — {esc(a.description)}  <i>(+{a.reward_coins:,} 🪙)</i>" for a in todo]
    return "\n".join(lines)


@router.message(Command("achievements", "season"))
async def cmd_ach(message: Message, db_user: User) -> None:
    async with get_session() as session:
        all_ach, unlocked = await GameplayService.get_user_achievements(session, db_user.id)
    await message.answer(_ach_text(all_ach, unlocked), reply_markup=ach_kb(message.from_user.id))


@router.callback_query(AchCb.filter(F.act == "v"))
async def cb_ach_list(callback: CallbackQuery, db_user: User) -> None:
    async with get_session() as session:
        all_ach, unlocked = await GameplayService.get_user_achievements(session, db_user.id)
    await edit_or_answer(callback, _ach_text(all_ach, unlocked), reply_markup=ach_kb(callback.from_user.id))
    await callback.answer()


async def _render_style(callback: CallbackQuery, user_id: int) -> None:
    async with get_session() as session:
        u = await session.get(User, user_id)
    is_vip = CommunityService.is_vip_active(u)
    frame = u.equipped_frame or "default"
    bg = u.equipped_bg or "classic"
    text = (
        "🎨 <b>PROFIL BEZAGI</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"🖼 Ramka: <b>{esc(PROFILE_FRAMES.get(frame, '—'))}</b>\n"
        f"🌌 Fon: <b>{esc(PROFILE_BACKGROUNDS.get(bg, '—'))}</b>\n\n"
        "✅ — hozir tanlangan   🔒 — faqat 💎 VIP uchun\n"
        "👇 Tanlang, profilingiz shu zahoti o‘zgaradi:"
    )
    await edit_or_answer(callback, text, reply_markup=profile_style_kb(callback.from_user.id, frame, bg, is_vip))


@router.callback_query(AchCb.filter(F.act == "frames"))
async def cb_profile_custom(callback: CallbackQuery, db_user: User) -> None:
    await _render_style(callback, db_user.id)
    await callback.answer()


@router.callback_query(AchCb.filter(F.act == "equip"))
async def cb_equip_item(callback: CallbackQuery, callback_data: AchCb, db_user: User) -> None:
    code = callback_data.code
    kind, val = ("frame", code[2:]) if code.startswith("f_") else ("bg", code[2:])
    async with get_session() as session:
        u = await session.get(User, db_user.id)
        if kind == "frame" and val in VIP_ONLY_FRAMES and not CommunityService.is_vip_active(u):
            await callback.answer("🔒 Bu ramka faqat 💎 VIP a’zolar uchun.", show_alert=True)
            return
        ok = await GameplayService.equip_profile_item(session, u, kind, val)
        if ok:
            await session.commit()
    if not ok:
        await callback.answer("❌ Topilmadi.", show_alert=True)
        return
    await callback.answer("✅ Saqlandi!")
    await _render_style(callback, db_user.id)


@router.callback_query(AchCb.filter(F.act == "season"))
async def cb_season_view(callback: CallbackQuery) -> None:
    async with get_session() as session:
        s = await GameplayService.get_active_season(session)
        top = await GameplayService.get_season_leaderboard(session, s.id, limit=10) if s else []
    if s is None:
        await callback.answer("Hozircha faol mavsum yo‘q.", show_alert=True)
        return
    lines = [
        f"🏆 <b>{esc(s.name)}</b>\n",
        f"⏳ Tugash: <b>{ensure_utc(s.ends_at):%d.%m.%Y %H:%M} UTC</b>\n",
        "<b>TOP 10 O‘YINCHILAR:</b>",
    ]
    for idx, (sp, u) in enumerate(top, start=1):
        lines.append(f"{idx}. {esc(u.display_name)} — ⭐ <b>{sp.points}</b> ball ({sp.cards_claimed} karta)")
    if not top:
        lines.append("📭 Hali ball yig‘ilmagan.")
    await edit_or_answer(callback, "\n".join(lines), reply_markup=back_home_kb(callback.from_user.id, "profile"))


# ------------------------------------------------------------------
# NOTIFICATIONS (/notif)
# ------------------------------------------------------------------
async def _show_notifs(target, owner_id: int, db_user: User) -> None:
    async with get_session() as session:
        notifs, total = await CommunityService.list_notifications(session, db_user.id, page=1, page_size=12)
    unread = sum(1 for n in notifs if not n.is_read)
    lines = [header("🔔 <b>XABARLAR</b>"), f"📬 Jami: <b>{total}</b> · 🔵 O‘qilmagan: <b>{unread}</b>\n"]
    for n in notifs:
        dot = "🔵" if not n.is_read else "⚪"
        lines.append(f"{dot} <b>{esc(n.title)}</b>\n{esc(n.body)}\n")
    if not notifs:
        lines.append("📭 Hozircha xabarlar yo‘q.")
    await edit_or_answer(target, "\n".join(lines), reply_markup=notifications_kb(owner_id))


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
    await callback.answer("✅ Barcha xabarlar o‘qilgan deb belgilandi.")
    await _show_notifs(callback, callback.from_user.id, db_user)


# ------------------------------------------------------------------
# SUPPORT & BAN APPEAL (/support) + FORCED SUB CHECK
# ------------------------------------------------------------------
@router.message(Command("support", "report"))
async def cmd_support(message: Message, db_user: User) -> None:
    async with get_session() as session:
        tickets = await CommunityService.list_tickets(session, user_id=db_user.id)
    await message.answer(
        f"{header('💬 <b>ALOQA</b>')}\nSavol, taklif yoki shikoyatingizni adminga yuboring. Javob shu yerda va xabar ko‘rinishida keladi.\n\n👇 Yangi murojaat yozing yoki oldingisini oching:",
        reply_markup=support_menu_kb(message.from_user.id, tickets),
    )


@router.callback_query(SupCb.filter(F.act == "v"))
async def cb_support_menu(callback: CallbackQuery, db_user: User) -> None:
    async with get_session() as session:
        tickets = await CommunityService.list_tickets(session, user_id=db_user.id)
    await edit_or_answer(
        callback,
        f"{header('💬 <b>ALOQA</b>')}\nSavol, taklif yoki shikoyatingizni adminga yuboring. Javob shu yerda va xabar ko‘rinishida keladi.\n\n👇 Yangi murojaat yozing yoki oldingisini oching:",
        reply_markup=support_menu_kb(callback.from_user.id, tickets),
    )


@router.callback_query(SupCb.filter(F.act == "new"))
async def cb_support_new(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(V2UserStates.waiting_support_subject)
    await state.update_data(owner_tg_id=callback.from_user.id)
    await callback.message.answer("📝 Murojaat mavzusini yozing (qisqacha):\n<i>Bekor qilish: /cancel</i>")
    await callback.answer()


@router.message(V2UserStates.waiting_support_subject, NotCommandFilter())
async def fsm_sup_subject(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    await state.update_data(sup_subject=(message.text or "Murojaat").strip()[:200])
    await state.set_state(V2UserStates.waiting_support_body)
    await message.answer("✍️ Endi murojaat matnini batafsil yozing:")


@router.message(V2UserStates.waiting_support_body, NotCommandFilter())
async def fsm_sup_body(message: Message, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    subject = data.get("sup_subject", "Murojaat")
    await state.clear()
    async with get_session() as session:
        t = await CommunityService.create_ticket(
            session, db_user.id, subject, (message.text or "").strip(), message.from_user.id
        )
        await session.commit()
    await message.answer(f"✅ Murojaatingiz qabul qilindi (<b>#{t.id}</b>).\nAdmin javob berishi bilan sizga xabar keladi.")


@router.callback_query(SupCb.filter(F.act == "view"))
async def cb_support_view(callback: CallbackQuery, callback_data: SupCb, db_user: User) -> None:
    async with get_session() as session:
        res = await CommunityService.get_ticket_with_messages(session, callback_data.tid)
    if res is None or res[0].user_id != db_user.id:
        await callback.answer("Murojaat topilmadi.", show_alert=True)
        return
    t, msgs, _ = res
    status_label = {"open": "🟡 Ko‘rib chiqilmoqda", "answered": "🟢 Javob berildi", "closed": "🔴 Yopilgan"}.get(t.status, t.status)
    lines = [header(f"💬 <b>Murojaat #{t.id}</b>"), f"📝 {esc(t.subject)}\n📌 Holat: <b>{status_label}</b>\n"]
    for m in msgs[-10:]:
        who = "👤 Siz" if m.sender_type == "user" else "🛡 Admin"
        lines.append(f"<b>{who}:</b> {esc(m.body)}")
    await edit_or_answer(callback, "\n".join(lines), reply_markup=back_home_kb(callback.from_user.id, "home"))


@router.callback_query(SupCb.filter(F.act == "check_sub"))
async def cb_check_sub(callback: CallbackQuery, db_user: User, is_bot_admin: bool = False) -> None:
    async with get_session() as session:
        ok, missing = await CommunityService.check_user_subscriptions(
            callback.bot, session, db_user, callback.from_user.id
        )
        await session.commit()
    if not ok and missing:
        await callback.answer("❌ Hali hamma kanallarga obuna bo‘lmadingiz!", show_alert=True)
        return
    await callback.answer("✅ Obuna tasdiqlandi! Xush kelibsiz!", show_alert=True)
    is_adm = is_bot_admin or is_admin_user(callback.from_user)
    await edit_or_answer(
        callback,
        "🏠 <b>BOSH MENU</b> — Obuna tasdiqlandi!",
        reply_markup=main_menu_kb(callback.from_user.id, is_admin=is_adm),
    )


# ------------------------------------------------------------------
# TWO-SIDED CARD-FOR-CARD TRADING (/trade)
# ------------------------------------------------------------------
@router.message(Command("trade"))
async def cmd_trade(message: Message, state: FSMContext, db_user: User) -> None:
    await state.clear()
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("🤝 <b>KARTA ALMASHISH</b>\n\nFoydalanish: <code>/trade @username</code>\nMasalan: <code>/trade @doston</code>")
        return
    async with get_session() as session:
        partner = await UserService.find_user(session, parts[1].strip())
        if partner is None or partner.id == db_user.id:
            await message.answer("❌ Foydalanuvchi topilmadi.")
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
        await message.answer("📭 Almashish uchun bo‘sh kartalaringiz yo‘q.")
        return
    rows = [
        [
            InlineKeyboardButton(
                text=f"🎴 {c.character_name[:28]} ({get_rarity_meta(c.rarity).name})",
                callback_data=TradeCb(owner_id=message.from_user.id, act="pick", tid=partner.id, ucid=uc.id).pack(),
            )
        ]
        for uc, c in my_cards
    ]
    await message.answer(
        f"🤝 <b>{esc(partner.display_name)}</b> ga taklif qilmoqchi bo‘lgan kartangizni tanlang:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(TradeCb.filter(F.act == "v"))
async def cb_trade_help(callback: CallbackQuery) -> None:
    await edit_or_answer(
        callback,
        f"{header('🤝 <b>KARTA ALMASHISH</b>')}\n"
        "Do‘stingiz bilan kartani karta bilan <b>xavfsiz</b> almashing — ikki tomon ham rozi bo‘lmaguncha hech narsa o‘tmaydi.\n\n"
        "<b>Qanday?</b>\n"
        "1️⃣ <code>/trade @username</code> deb yozing\n"
        "2️⃣ O‘z kartangizni tanlang\n"
        "3️⃣ Do‘stingiz o‘z kartasini tanlaydi\n"
        "4️⃣ Ikkalangiz tasdiqlaysiz — almashuv bo‘ladi ✅\n\n"
        "🎁 Bepul sovg‘a uchun: <code>/give @username</code>",
        reply_markup=back_home_kb(callback.from_user.id, "home"),
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
                    text=f"🔁 {c.character_name[:26]} ({get_rarity_meta(c.rarity).name})",
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
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Taklifni bekor qilish",
                    callback_data=TradeCb(owner_id=callback.from_user.id, act="cancel", tid=trade.id).pack(),
                )
            ]
        ]
    )
    await edit_or_answer(
        callback,
        "✅ Almashuv taklifi yuborildi! Hamkoringiz karta tanlashini kuting.\n<i>Taklif 12 soat ichida javob berilmasa avtomatik bekor qilinadi.</i>",
        reply_markup=cancel_kb,
    )


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


@router.callback_query(TradeCb.filter(F.act == "cancel"))
async def cb_trade_cancel(callback: CallbackQuery, callback_data: TradeCb, db_user: User) -> None:
    async with get_session() as session:
        ok, msg = await AuctionWishlistService.cancel_trade(session, callback_data.tid, db_user.id)
        if ok:
            await session.commit()
        else:
            await session.rollback()
    await callback.answer(msg[:190], show_alert=True)
    if ok:
        await edit_or_answer(callback, msg, reply_markup=back_home_kb(callback.from_user.id, "home"))
