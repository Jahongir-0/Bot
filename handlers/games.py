"""Mini o‘yinlar: slot, xazina ovi, g‘ildirak, tosh, tanga, omadli karta, dart, boulling, basketbol va sirli sandiq."""

import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from database.session import get_session
from handlers.common import LINE_SEP
from keyboards.v2 import (
    coinflip_pick_kb,
    dice_pick_kb,
    games_menu_kb,
    lucky_pick_kb,
    mines_bet_kb,
    mines_board_kb,
    seq_bet_kb,
    seq_pad_kb,
    slots_bet_kb,
    wheel_menu_kb,
)
from models.user import User
from services.extra_games_service import BETS as EXTRA_BETS, ExtraGamesService
from services.gameplay_service import GameplayService
from utils.callback_data import GameCb, NavCb
from utils.formatting import fmt_coins
from utils.game_catalog import ANIM_GAMES
from utils.tg_helpers import append_private_ad, edit_or_answer

logger = logging.getLogger(__name__)
router = Router(name="games")


# ------------------------------------------------------------------
# TOSH O'YINI (/game): son tanlaysiz -> bot tosh tashlaydi -> mos tushsa yutasiz
# ------------------------------------------------------------------
_PLAYING: set = set()


_GAME_USER_LOCKS: dict[int, asyncio.Lock] = {}


_MINES_ACTIVE: dict[int, dict] = {}


_SEQ_ACTIVE: dict[int, dict] = {}


def _user_game_lock(user_id: int) -> asyncio.Lock:
    lock = _GAME_USER_LOCKS.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _GAME_USER_LOCKS[user_id] = lock
    return lock


_EXTRA_KEYS = ("dart", "bowling", "basket", "chest", "seq")


def _game_menu_text(st: dict) -> str:
    cfg = st["cfg"]
    coins = st["coins"]

    def _status(en: bool, used: int, lim: int) -> str:
        if not en:
            return "🔴 o‘chirilgan"
        left = max(0, lim - used)
        return f"🎟 {left}/{lim}" if left > 0 else "⏳ bugun tugadi"

    wheel_fee = f"{st['wheel_cost']:,} 🪙 chipta" if st["wheel_cost"] > 0 else "bepul"
    rows = [
        ("🧠", "Xotira Zanjiri", f"4 ta runa eslab qoling ({int(cfg.get('seq_win_pct', 220)) / 100:g}x)", st["seq_enabled"], st["seq_used"], st["seq_limit"]),
        ("🎰", "Slot 777", "777 = 10x yutuq", st["slots_enabled"], st["slots_used"], st["slots_limit"]),
        ("💣", "Xazina ovi", "bombadan qoching, 4x gacha", st["mines_enabled"], st["mines_used"], st["mines_limit"]),
        ("🎡", "Omad g‘ildiragi", wheel_fee, st["wheel_enabled"], st["wheel_used"], st["wheel_limit"]),
        ("📦", "Sirli sandiq", f"{int(cfg.get('chest_cost', 100)):,} 🪙 · jackpot {int(cfg.get('chest_jackpot_pct', 1000)) // 100}x", st["chest_enabled"], st["chest_used"], st["chest_limit"]),
        ("🎯", "Dart", f"nishon markazi {int(cfg.get('dart_jackpot_pct', 400)) / 100:g}x", st["dart_enabled"], st["dart_used"], st["dart_limit"]),
        ("🎳", "Boulling", f"strike {int(cfg.get('bowling_jackpot_pct', 400)) / 100:g}x", st["bowling_enabled"], st["bowling_used"], st["bowling_limit"]),
        ("🏀", "Basketbol", f"toza gol {int(cfg.get('basket_jackpot_pct', 300)) / 100:g}x", st["basket_enabled"], st["basket_used"], st["basket_limit"]),
        ("🎲", "Tosh o‘yini", f"+{fmt_coins(cfg.get('dice_jackpot_reward', 300))} 🪙", st["dice_enabled"], st["dice_used"], st["dice_limit"]),
        ("🪙", "Tanga tashlash", "gerb yoki raqam, 2x", st["flip_enabled"], st["flip_used"], st["flip_limit"]),
        ("🃏", "Omadli karta", f"{cfg.get('lucky_min_reward', 60)}–{cfg.get('lucky_max_reward', 350)} 🪙 bepul", st["lucky_enabled"], st["lucky_used"], st["lucky_limit"]),
    ]
    visible = [r for r in rows if r[3]]  # admin o‘chirgan o‘yinlar umuman ko‘rsatilmaydi
    lines = ["🎮 <b>MINI O‘YINLAR</b>", LINE_SEP, f"🪙 Balansingiz: <b>{fmt_coins(coins)}</b> coin\n"]
    if not visible:
        lines.append("📭 Hozircha mini o‘yinlar mavjud emas. Keyinroq qaytib ko‘ring!")
        return "\n".join(lines)
    for emoji, name, desc, _en, used, lim in visible:
        lines.append(f"{emoji} <b>{name}</b> · {desc}\n      {_status(True, used, lim)}")
    if not any(used < lim for _e, _n, _d, _en, used, lim in visible):
        lines.append("\n⏳ <i>Bugungi barcha urinishlaringiz tugadi. Ertaga qayting!</i>")
    return "\n".join(lines)


async def _game_state(user_id: int) -> dict:
    async with get_session() as session:
        user = await session.get(User, user_id)
        cfg = await GameplayService.get_game_config(session)
        d_en, d_used, d_lim = await GameplayService.get_dice_status(session, user_id)
        f_en, f_used, f_lim = await GameplayService.get_coinflip_status(session, user_id)
        l_en, l_used, l_lim = await GameplayService.get_lucky_status(session, user_id)
        s_en, s_used, s_lim = await GameplayService.get_slots_status(session, user_id)
        m_en, m_used, m_lim = await GameplayService.get_mines_status(session, user_id)
        w_en, w_used, w_lim, w_cost = await GameplayService.get_wheel_status(session, user_id)
        extra = {k: await ExtraGamesService.status(session, user_id, k) for k in _EXTRA_KEYS}
        coins = int(user.coins or 0) if user is not None else 0
    st = {
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
        "slots_enabled": s_en,
        "slots_used": s_used,
        "slots_limit": s_lim,
        "mines_enabled": m_en,
        "mines_used": m_used,
        "mines_limit": m_lim,
        "wheel_enabled": w_en,
        "wheel_used": w_used,
        "wheel_limit": w_lim,
        "wheel_cost": w_cost,
    }
    for k, (en, used, lim) in extra.items():
        st[f"{k}_enabled"], st[f"{k}_used"], st[f"{k}_limit"] = en, used, lim
    return st


def _games_kb_from_state(owner_id: int, st: dict) -> InlineKeyboardMarkup:
    has_active_mines = owner_id in _MINES_ACTIVE and not _MINES_ACTIVE[owner_id].get("finished")
    return games_menu_kb(
        owner_id,
        can_dice=st["dice_enabled"] and st["dice_used"] < st["dice_limit"],
        can_flip=st["flip_enabled"] and st["flip_used"] < st["flip_limit"],
        can_lucky=st["lucky_enabled"] and st["lucky_used"] < st["lucky_limit"],
        can_slots=st["slots_enabled"] and st["slots_used"] < st["slots_limit"],
        can_mines=(st["mines_enabled"] and (st["mines_used"] < st["mines_limit"] or has_active_mines)),
        can_wheel=st["wheel_enabled"] and st["wheel_used"] < st["wheel_limit"],
        can_dart=st["dart_enabled"] and st["dart_used"] < st["dart_limit"],
        can_bowling=st["bowling_enabled"] and st["bowling_used"] < st["bowling_limit"],
        can_basket=st["basket_enabled"] and st["basket_used"] < st["basket_limit"],
        can_chest=st["chest_enabled"] and st["chest_used"] < st["chest_limit"],
        can_seq=st["seq_enabled"] and st["seq_used"] < st["seq_limit"],
    )


@router.message(Command("game", "games"))
async def cmd_game(message: Message, db_user: User) -> None:
    st = await _game_state(db_user.id)
    text = _game_menu_text(st)
    if message.chat.type == "private":
        text = await append_private_ad(text, message.bot, db_user)
    await message.answer(
        text,
        reply_markup=_games_kb_from_state(message.from_user.id, st),
    )


@router.callback_query(GameCb.filter(F.act == "v"))
async def cb_games_menu(callback: CallbackQuery, db_user: User) -> None:
    st = await _game_state(db_user.id)
    text = _game_menu_text(st)
    if callback.message and callback.message.chat.type == "private":
        text = await append_private_ad(text, callback.bot, db_user)
    await edit_or_answer(
        callback,
        text,
        reply_markup=_games_kb_from_state(callback.from_user.id, st),
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
            ok, msg, _roll, _coins = await GameplayService.play_dice_game(
                session, uid, dice_value=value, guessed_number=guess
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
    new_coins = 0
    async with _user_game_lock(uid):
        async with get_session() as session:
            ok, msg, _won, _delta = await GameplayService.play_coin_flip(
                session, uid, choice=callback_data.extra, bet=callback_data.val
            )
            if ok:
                new_coins = int(
                    (await session.execute(select(User.coins).where(User.id == uid))).scalar_one_or_none() or 0
                )
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
        (
            f"{msg}\n"
            f"{LINE_SEP}\n"
            f"💰 Balans: <b>{fmt_coins(new_coins)} 🪙</b>\n"
            f"🎟 Qolgan urinishlar: <b>{left}/{limit_now}</b>"
        ),
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
        f"🃏 <b>OMADLI KARTA</b>\n{LINE_SEP}\n3 ta yopiq kartadan birini tanlang va tasodifiy coin yuting!\n🎟 Qolgan urinishlar: <b>{left}/{st['lucky_limit']}</b>",
        reply_markup=lucky_pick_kb(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(GameCb.filter(F.act == "lucky_pick"))
async def cb_game_lucky_pick(callback: CallbackQuery, callback_data: GameCb, db_user: User) -> None:
    uid = db_user.id
    used_now = 0
    limit_now = 3
    new_coins = 0
    async with _user_game_lock(uid):
        async with get_session() as session:
            ok, msg, _coins = await GameplayService.play_lucky_card(
                session, uid, picked_slot=callback_data.val
            )
            if ok:
                new_coins = int(
                    (await session.execute(select(User.coins).where(User.id == uid))).scalar_one_or_none() or 0
                )
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
    await edit_or_answer(
        callback,
        (
            f"{msg}\n"
            f"{LINE_SEP}\n"
            f"💰 Balans: <b>{fmt_coins(new_coins)} 🪙</b>\n"
            f"🎟 Qolgan urinishlar: <b>{left}/{limit_now}</b>"
        ),
        reply_markup=b.as_markup(),
    )
    await callback.answer()


@router.callback_query(GameCb.filter(F.act == "slots"))
async def cb_game_slots_menu(callback: CallbackQuery, db_user: User) -> None:
    st = await _game_state(db_user.id)
    if not st["slots_enabled"]:
        await callback.answer("🔴 Slot mashinasi hozircha o‘chirilgan.", show_alert=True)
        return
    if st["slots_used"] >= st["slots_limit"]:
        await callback.answer(f"🎟 Bugungi Slot urinishlari tugadi ({st['slots_limit']}/{st['slots_limit']}).", show_alert=True)
        return
    left = max(0, st["slots_limit"] - st["slots_used"])
    await edit_or_answer(
        callback,
        (
            f"🎰 <b>SLOT 777</b>\n{LINE_SEP}\n"
            "Barabanni aylantiring va yutuq oling:\n"
            "💎 <b>777</b> → <b>10x</b>\n"
            "🔥 <b>3 ta bir xil</b> → <b>4x</b>\n"
            "✨ <b>2 ta yonma-yon</b> → <b>1.5x</b>\n\n"
            f"💰 Balans: <b>{fmt_coins(st['coins'])} 🪙</b>\n"
            f"🎟 Bugun qoldi: <b>{left}/{st['slots_limit']}</b>\n\n"
            "👇 Garov miqdorini tanlang:"
        ),
        reply_markup=slots_bet_kb(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(GameCb.filter(F.act == "slots_go"))
async def cb_game_slots_go(callback: CallbackQuery, callback_data: GameCb, db_user: User) -> None:
    uid = db_user.id
    bet = int(callback_data.val)
    if uid in _PLAYING:
        await callback.answer("⏳ Baraban aylanmoqda, biroz kuting...")
        return
    _PLAYING.add(uid)
    try:
        st = await _game_state(uid)
        if not st["slots_enabled"]:
            await callback.answer("🔴 Slot mashinasi o‘chirilgan.", show_alert=True)
            return
        if st["slots_used"] >= st["slots_limit"]:
            await callback.answer(f"🎟 Bugungi urinishlar tugadi ({st['slots_limit']}/{st['slots_limit']}).", show_alert=True)
            return
        if st["coins"] < bet:
            await callback.answer(f"🪙 Coinlaringiz yetarli emas ({bet:,} 🪙 kerak).", show_alert=True)
            return
        await callback.answer()
        chat_id = callback.message.chat.id
        try:
            await callback.message.edit_text(f"🎰 Garov: <b>{bet:,} 🪙</b>\nBaraban aylanmoqda...", reply_markup=None)
        except Exception:
            pass

        dice_msg = await callback.bot.send_dice(chat_id, emoji="🎰")
        value = int(dice_msg.dice.value)
        await asyncio.sleep(2.4)

        used_now = st["slots_used"]
        limit_now = st["slots_limit"]
        new_coins = st["coins"]
        async with _user_game_lock(uid):
            async with get_session() as session:
                ok, msg, _payout, _net = await GameplayService.play_slots_game(
                    session, uid, dice_value=value, bet=bet
                )
                if ok:
                    new_coins = int(
                        (await session.execute(select(User.coins).where(User.id == uid))).scalar_one_or_none() or 0
                    )
                    await session.commit()
                    _en, used_now, limit_now = await GameplayService.get_slots_status(session, uid)
                else:
                    await session.rollback()
        left = max(0, limit_now - used_now)
        b = InlineKeyboardBuilder()
        if left > 0:
            b.row(InlineKeyboardButton(text="🎰 Yana aylantirish", callback_data=GameCb(owner_id=callback.from_user.id, act="slots").pack()))
        b.row(
            InlineKeyboardButton(text="🎮 O‘yinlar", callback_data=GameCb(owner_id=callback.from_user.id, act="v").pack()),
            InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=callback.from_user.id, target="home").pack()),
        )
        txt = msg + (
            f"\n{LINE_SEP}\n💰 Balans: <b>{fmt_coins(new_coins)} 🪙</b>\n🎟 Qoldi: <b>{left}/{limit_now}</b>"
            if ok
            else ""
        )
        await callback.bot.send_message(chat_id, txt, reply_markup=b.as_markup())
    except Exception:
        logger.exception("Slot o'yinida xato")
        await callback.bot.send_message(callback.message.chat.id, "⚠️ O‘yinda xatolik bo‘ldi. Qayta urinib ko‘ring.")
    finally:
        _PLAYING.discard(uid)


@router.callback_query(GameCb.filter(F.act == "mines"))
async def cb_game_mines_menu(callback: CallbackQuery, db_user: User) -> None:
    uid = db_user.id
    gstate = _MINES_ACTIVE.get(uid)
    if gstate and not gstate.get("finished"):
        hits = int(gstate.get("safe_hits", 0))
        bet = int(gstate.get("bet", 0))
        mult = GameplayService.MINES_MULTIPLIERS[min(hits, len(GameplayService.MINES_MULTIPLIERS) - 1)]
        cash_amt = int(round(bet * mult)) if hits > 0 else 0
        await edit_or_answer(
            callback,
            (
                f"💣 <b>XAZINA OVI — DAVOM ETISH</b>\n{LINE_SEP}\n"
                f"💰 Garov: <b>{bet:,} 🪙</b> · Topildi: <b>{hits}/4</b>\n"
                f"📈 Joriy yutuq: <b>{cash_amt:,} 🪙</b>\n\n"
                "👇 Yopiq sandiqlardan birini oching:"
            ),
            reply_markup=mines_board_kb(callback.from_user.id, gstate["opened"], gstate["bombs"], False, cashout_amt=cash_amt),
        )
        await callback.answer()
        return

    st = await _game_state(uid)
    if not st["mines_enabled"]:
        await callback.answer("🔴 Xazina ovi hozircha o‘chirilgan.", show_alert=True)
        return
    if st["mines_used"] >= st["mines_limit"]:
        await callback.answer(f"🎟 Bugungi urinishlar tugadi ({st['mines_limit']}/{st['mines_limit']}).", show_alert=True)
        return
    left = max(0, st["mines_limit"] - st["mines_used"])
    await edit_or_answer(
        callback,
        (
            f"💣 <b>XAZINA OVI (MINES)</b>\n{LINE_SEP}\n"
            "6 ta sandiq ichida <b>4 ta oltin</b> 💎 va <b>2 ta bomba</b> 💣 bor!\n"
            "Har bir oltin yutuqni oshiradi: <b>1.35x → 1.85x → 2.60x → 4.00x</b>\n"
            "Istalgan payt <b>«💰 OLIB CHIQISH»</b> tugmasini bosib yutuqni saqlashingiz mumkin.\n\n"
            f"💰 Balans: <b>{fmt_coins(st['coins'])} 🪙</b>\n"
            f"🎟 Bugun qoldi: <b>{left}/{st['mines_limit']}</b>\n\n"
            "👇 Garovni tanlang:"
        ),
        reply_markup=mines_bet_kb(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(GameCb.filter(F.act == "mines_go"))
async def cb_game_mines_go(callback: CallbackQuery, callback_data: GameCb, db_user: User) -> None:
    uid = db_user.id
    async with _user_game_lock(uid):
        prev = _MINES_ACTIVE.get(uid)
        if prev and not prev.get("finished"):
            await callback.answer("⚠️ Avval tugallanmagan Xazina ovi o‘yinini yakunlang!", show_alert=True)
            return
        async with get_session() as session:
            ok, msg, state_obj = await GameplayService.start_mines_game(session, uid, callback_data.val)
            if ok and state_obj is not None:
                await session.commit()
                _MINES_ACTIVE[uid] = state_obj
            else:
                await session.rollback()
    if not ok or state_obj is None:
        await callback.answer(msg[:190], show_alert=True)
        return
    await edit_or_answer(
        callback,
        (
            f"💣 <b>XAZINA OVI BOSHLANDI!</b>\n{LINE_SEP}\n"
            f"💰 Garov: <b>{state_obj['bet']:,} 🪙</b>\n"
            "💎 4 ta oltin · 💣 2 ta bomba\n\n"
            "👇 Sandiq tanlang:"
        ),
        reply_markup=mines_board_kb(callback.from_user.id, [], state_obj["bombs"], False, cashout_amt=0),
    )
    await callback.answer()


@router.callback_query(GameCb.filter(F.act == "mines_open"))
async def cb_game_mines_open(callback: CallbackQuery, callback_data: GameCb, db_user: User) -> None:
    uid = db_user.id
    async with _user_game_lock(uid):
        gstate = _MINES_ACTIVE.get(uid)
        if not gstate or gstate.get("finished"):
            await callback.answer("Bu o‘yin yakunlangan. Yangi o‘yin boshlang!", show_alert=True)
            return
        new_coins = 0
        used_now = 0
        limit_now = 5
        async with get_session() as session:
            outcome, payout, msg = await GameplayService.step_mines_game(session, uid, gstate, callback_data.val)
            if outcome != "invalid":
                new_coins = int(
                    (await session.execute(select(User.coins).where(User.id == uid))).scalar_one_or_none() or 0
                )
                await session.commit()
                _en, used_now, limit_now = await GameplayService.get_mines_status(session, uid)
            else:
                await session.rollback()
    if outcome == "invalid":
        await callback.answer(msg[:190], show_alert=True)
        return
    finished = bool(gstate.get("finished"))
    if finished:
        _MINES_ACTIVE.pop(uid, None)
    left = max(0, limit_now - used_now)
    footer = f"\n{LINE_SEP}\n💰 Balans: <b>{fmt_coins(new_coins)} 🪙</b>\n🎟 Qoldi: <b>{left}/{limit_now}</b>" if finished else ""
    await edit_or_answer(
        callback,
        msg + footer,
        reply_markup=mines_board_kb(
            callback.from_user.id,
            gstate["opened"],
            gstate["bombs"],
            finished,
            cashout_amt=(payout if outcome == "safe" else 0),
        ),
    )
    await callback.answer()


@router.callback_query(GameCb.filter(F.act == "mines_cash"))
async def cb_game_mines_cash(callback: CallbackQuery, db_user: User) -> None:
    uid = db_user.id
    async with _user_game_lock(uid):
        gstate = _MINES_ACTIVE.get(uid)
        if not gstate or gstate.get("finished"):
            await callback.answer("Faol o‘yin topilmadi.", show_alert=True)
            return
        new_coins = 0
        used_now = 0
        limit_now = 5
        async with get_session() as session:
            outcome, _payout, msg = await GameplayService.cashout_mines_game(session, uid, gstate)
            if outcome != "invalid":
                new_coins = int(
                    (await session.execute(select(User.coins).where(User.id == uid))).scalar_one_or_none() or 0
                )
                await session.commit()
                _en, used_now, limit_now = await GameplayService.get_mines_status(session, uid)
            else:
                await session.rollback()
    if outcome == "invalid":
        await callback.answer(msg[:190], show_alert=True)
        return
    _MINES_ACTIVE.pop(uid, None)
    left = max(0, limit_now - used_now)
    await edit_or_answer(
        callback,
        f"{msg}\n{LINE_SEP}\n💰 Balans: <b>{fmt_coins(new_coins)} 🪙</b>\n🎟 Qoldi: <b>{left}/{limit_now}</b>",
        reply_markup=mines_board_kb(callback.from_user.id, gstate["opened"], gstate["bombs"], True, cashout_amt=0),
    )
    await callback.answer()


@router.callback_query(GameCb.filter(F.act == "mines_noop"))
async def cb_game_mines_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(GameCb.filter(F.act == "wheel"))
async def cb_game_wheel_menu(callback: CallbackQuery, db_user: User) -> None:
    st = await _game_state(db_user.id)
    if not st["wheel_enabled"]:
        await callback.answer("🔴 Omad g‘ildiragi hozircha o‘chirilgan.", show_alert=True)
        return
    if st["wheel_used"] >= st["wheel_limit"]:
        await callback.answer(f"🎟 Bugungi urinishlar tugadi ({st['wheel_limit']}/{st['wheel_limit']}).", show_alert=True)
        return
    left = max(0, st["wheel_limit"] - st["wheel_used"])
    cost = st["wheel_cost"]
    fee_line = f"🎫 Chipta narxi: <b>{cost:,} 🪙</b>" if cost > 0 else "🎫 Chipta: <b>BEPUL</b>"
    await edit_or_answer(
        callback,
        (
            f"🎡 <b>OMAD G‘ILDIRAGI</b>\n{LINE_SEP}\n"
            "Sektorlar:\n"
            "• 💎 Jackpot: <b>500 🪙</b> + 30 XP\n"
            "• 🔥 Katta yutuq: <b>220 🪙</b> + 20 XP\n"
            "• 🪙 Oltin: <b>140 🪙</b> · Kichik: <b>75 🪙</b>\n"
            "• ⚡ Energiya: <b>25 🪙 + 50 XP</b>\n\n"
            f"{fee_line}\n"
            f"💰 Balans: <b>{fmt_coins(st['coins'])} 🪙</b>\n"
            f"🎟 Bugun qoldi: <b>{left}/{st['wheel_limit']}</b>"
        ),
        reply_markup=wheel_menu_kb(callback.from_user.id, cost),
    )
    await callback.answer()


@router.callback_query(GameCb.filter(F.act == "wheel_go"))
async def cb_game_wheel_go(callback: CallbackQuery, db_user: User) -> None:
    uid = db_user.id
    used_now = 0
    limit_now = 3
    new_coins = 0
    async with _user_game_lock(uid):
        async with get_session() as session:
            ok, msg, _coins = await GameplayService.play_wheel_game(session, uid)
            if ok:
                new_coins = int(
                    (await session.execute(select(User.coins).where(User.id == uid))).scalar_one_or_none() or 0
                )
                await session.commit()
                _en, used_now, limit_now, _cost = await GameplayService.get_wheel_status(session, uid)
            else:
                await session.rollback()
    if not ok:
        await callback.answer(msg[:190], show_alert=True)
        return
    left = max(0, limit_now - used_now)
    b = InlineKeyboardBuilder()
    if left > 0:
        b.row(InlineKeyboardButton(text="🎡 Yana aylantirish", callback_data=GameCb(owner_id=callback.from_user.id, act="wheel").pack()))
    b.row(
        InlineKeyboardButton(text="🎮 O‘yinlar", callback_data=GameCb(owner_id=callback.from_user.id, act="v").pack()),
        InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=callback.from_user.id, target="home").pack()),
    )
    await edit_or_answer(
        callback,
        (
            f"{msg}\n{LINE_SEP}\n"
            f"💰 Balans: <b>{fmt_coins(new_coins)} 🪙</b>\n"
            f"🎟 Qoldi: <b>{left}/{limit_now}</b>"
        ),
        reply_markup=b.as_markup(),
    )
    await callback.answer()


# ===========================================================================
# YANGI O'YINLAR: Dart / Boulling / Basketbol (animatsiyali) va Sirli sandiq
# ===========================================================================
def _game_result_kb(owner_id: int, again_text: str, again_cb: str, left: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if left > 0:
        b.row(InlineKeyboardButton(text=again_text, callback_data=again_cb))
    b.row(
        InlineKeyboardButton(text="🎮 O‘yinlar", callback_data=GameCb(owner_id=owner_id, act="v").pack()),
        InlineKeyboardButton(text="🏠 Bosh menu", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return b.as_markup()


@router.callback_query(GameCb.filter(F.act == "an"))
async def cb_game_anim_menu(callback: CallbackQuery, callback_data: GameCb, db_user: User) -> None:
    kind = callback_data.extra
    info = ANIM_GAMES.get(kind)
    if info is None:
        await callback.answer("Noma’lum o‘yin.", show_alert=True)
        return
    emoji, title, _jv, _wv, jackpot_lbl, win_lbl = info
    st = await _game_state(db_user.id)
    cfg = st["cfg"]
    if not st[f"{kind}_enabled"]:
        await callback.answer(f"🔴 {title} hozircha o‘chirilgan.", show_alert=True)
        return
    left = max(0, st[f"{kind}_limit"] - st[f"{kind}_used"])
    if left <= 0:
        await callback.answer(f"🎟 Bugungi {title} urinishlari tugadi.", show_alert=True)
        return
    jp = int(cfg.get(f"{kind}_jackpot_pct", 400)) / 100
    wp = int(cfg.get(f"{kind}_win_pct", 150)) / 100
    b = InlineKeyboardBuilder()
    for bet in EXTRA_BETS:
        b.add(
            InlineKeyboardButton(
                text=f"{emoji} {bet} 🪙",
                callback_data=GameCb(owner_id=callback.from_user.id, act="an_go", val=bet, extra=kind).pack(),
            )
        )
    b.adjust(2)
    b.row(InlineKeyboardButton(text="⬅️ O‘yinlar", callback_data=GameCb(owner_id=callback.from_user.id, act="v").pack()))
    await edit_or_answer(
        callback,
        (
            f"{emoji} <b>{title.upper()}</b>\n{LINE_SEP}\n"
            "Garov qo‘ying, bot animatsiyani yuboradi — natija o‘zi hal qiladi:\n"
            f"🏆 {jackpot_lbl} → <b>{jp:g}x</b>\n"
            f"✅ {win_lbl} → <b>{wp:g}x</b>\n\n"
            f"💰 Balans: <b>{fmt_coins(st['coins'])} 🪙</b>\n"
            f"🎟 Bugun qoldi: <b>{left}/{st[f'{kind}_limit']}</b>\n\n"
            "👇 Garov miqdorini tanlang:"
        ),
        reply_markup=b.as_markup(),
    )
    await callback.answer()


@router.callback_query(GameCb.filter(F.act == "an_go"))
async def cb_game_anim_go(callback: CallbackQuery, callback_data: GameCb, db_user: User) -> None:
    uid = db_user.id
    kind = callback_data.extra
    info = ANIM_GAMES.get(kind)
    bet = int(callback_data.val)
    if info is None:
        await callback.answer("Noma’lum o‘yin.", show_alert=True)
        return
    emoji, title = info[0], info[1]
    if uid in _PLAYING:
        await callback.answer("⏳ O‘yin davom etmoqda, biroz kuting...")
        return
    _PLAYING.add(uid)
    try:
        st = await _game_state(uid)
        if not st[f"{kind}_enabled"]:
            await callback.answer(f"🔴 {title} o‘chirilgan.", show_alert=True)
            return
        if st[f"{kind}_used"] >= st[f"{kind}_limit"]:
            await callback.answer("🎟 Bugungi urinishlar tugadi.", show_alert=True)
            return
        if st["coins"] < bet:
            await callback.answer(f"🪙 Coinlaringiz yetarli emas ({bet:,} 🪙 kerak).", show_alert=True)
            return
        await callback.answer()
        chat_id = callback.message.chat.id
        try:
            await callback.message.edit_text(f"{emoji} Garov: <b>{bet:,} 🪙</b>\nOmad tilaymiz...", reply_markup=None)
        except Exception:
            pass
        dice_msg = await callback.bot.send_dice(chat_id, emoji=emoji)
        value = int(dice_msg.dice.value)
        await asyncio.sleep(3.6)

        used_now, limit_now, new_coins = st[f"{kind}_used"], st[f"{kind}_limit"], st["coins"]
        async with _user_game_lock(uid):
            async with get_session() as session:
                ok, msg, _payout, _net = await ExtraGamesService.play_animated(session, uid, kind, value, bet)
                if ok:
                    new_coins = int((await session.execute(select(User.coins).where(User.id == uid))).scalar_one_or_none() or 0)
                    await session.commit()
                    _en, used_now, limit_now = await ExtraGamesService.status(session, uid, kind)
                else:
                    await session.rollback()
        left = max(0, limit_now - used_now)
        txt = msg + (f"\n{LINE_SEP}\n💰 Balans: <b>{fmt_coins(new_coins)} 🪙</b>\n🎟 Qoldi: <b>{left}/{limit_now}</b>" if ok else "")
        kb = _game_result_kb(
            callback.from_user.id,
            f"{emoji} Yana o‘ynash",
            GameCb(owner_id=callback.from_user.id, act="an", extra=kind).pack(),
            left if ok else 0,
        )
        await callback.bot.send_message(chat_id, txt, reply_markup=kb)
    except Exception:
        logger.exception("%s o'yinida xato", kind)
        await callback.bot.send_message(callback.message.chat.id, "⚠️ O‘yinda xatolik bo‘ldi. Qayta urinib ko‘ring.")
    finally:
        _PLAYING.discard(uid)


@router.callback_query(GameCb.filter(F.act == "chest"))
async def cb_game_chest_menu(callback: CallbackQuery, db_user: User) -> None:
    st = await _game_state(db_user.id)
    cfg = st["cfg"]
    if not st["chest_enabled"]:
        await callback.answer("🔴 Sirli sandiq hozircha o‘chirilgan.", show_alert=True)
        return
    left = max(0, st["chest_limit"] - st["chest_used"])
    if left <= 0:
        await callback.answer("🎟 Bugungi urinishlar tugadi.", show_alert=True)
        return
    cost = int(cfg.get("chest_cost", 100))
    b = InlineKeyboardBuilder()
    for n in (1, 2, 3):
        b.add(InlineKeyboardButton(text=f"📦 {n}-sandiq", callback_data=GameCb(owner_id=callback.from_user.id, act="chest_pick", val=n).pack()))
    b.adjust(3)
    b.row(InlineKeyboardButton(text="⬅️ O‘yinlar", callback_data=GameCb(owner_id=callback.from_user.id, act="v").pack()))
    await edit_or_answer(
        callback,
        (
            f"📦 <b>SIRLI SANDIQ</b>\n{LINE_SEP}\n"
            "3 ta sandiqdan birini tanlang. Ichida nima borligi — sir!\n"
            "💨 Bo‘sh · 🪙 Hamyon · 💰 Oltin · 💎 Qimmatbaho · 👑 Jackpot\n\n"
            f"🎟 Bitta sandiq narxi: <b>{cost:,} 🪙</b>\n"
            f"👑 Jackpot: <b>{int(cfg.get('chest_jackpot_pct', 1000)) / 100:g}x</b>\n"
            f"💰 Balans: <b>{fmt_coins(st['coins'])} 🪙</b>\n"
            f"🎟 Bugun qoldi: <b>{left}/{st['chest_limit']}</b>\n\n"
            "👇 Sandiqni tanlang:"
        ),
        reply_markup=b.as_markup(),
    )
    await callback.answer()


@router.callback_query(GameCb.filter(F.act == "chest_pick"))
async def cb_game_chest_pick(callback: CallbackQuery, callback_data: GameCb, db_user: User) -> None:
    uid = db_user.id
    if uid in _PLAYING:
        await callback.answer("⏳ Biroz kuting...")
        return
    _PLAYING.add(uid)
    try:
        st = await _game_state(uid)
        if not st["chest_enabled"] or st["chest_used"] >= st["chest_limit"]:
            await callback.answer("🎟 Sirli sandiq hozir mavjud emas yoki limit tugadi.", show_alert=True)
            return
        cost = int(st["cfg"].get("chest_cost", 100))
        if st["coins"] < cost:
            await callback.answer(f"🪙 Coinlaringiz yetarli emas ({cost:,} 🪙 kerak).", show_alert=True)
            return
        await callback.answer()
        used_now, limit_now, new_coins = st["chest_used"], st["chest_limit"], st["coins"]
        async with _user_game_lock(uid):
            async with get_session() as session:
                ok, msg, _payout, _net = await ExtraGamesService.play_chest(session, uid, int(callback_data.val))
                if ok:
                    new_coins = int((await session.execute(select(User.coins).where(User.id == uid))).scalar_one_or_none() or 0)
                    await session.commit()
                    _en, used_now, limit_now = await ExtraGamesService.status(session, uid, "chest")
                else:
                    await session.rollback()
        left = max(0, limit_now - used_now)
        txt = msg + (f"\n{LINE_SEP}\n💰 Balans: <b>{fmt_coins(new_coins)} 🪙</b>\n🎟 Qoldi: <b>{left}/{limit_now}</b>" if ok else "")
        kb = _game_result_kb(
            callback.from_user.id,
            "📦 Yana ochish",
            GameCb(owner_id=callback.from_user.id, act="chest").pack(),
            left if ok else 0,
        )
        await edit_or_answer(callback, txt, reply_markup=kb)
    except Exception:
        logger.exception("Sirli sandiq o'yinida xato")
        await callback.message.answer("⚠️ O‘yinda xatolik bo‘ldi. Qayta urinib ko‘ring.")
    finally:
        _PLAYING.discard(uid)


# ===========================================================================
# 🧠 XOTIRA ZANJIRI (Skill-Based Rune Memory Challenge)
# ===========================================================================
@router.callback_query(GameCb.filter(F.act == "seq"))
async def cb_game_seq_menu(callback: CallbackQuery, db_user: User) -> None:
    uid = db_user.id
    active = _SEQ_ACTIVE.get(uid)
    if active and not active.get("finished") and active.get("phase") == "recall":
        step = int(active.get("step", 0))
        seq = active.get("seq", [])
        so_far = " → ".join(
            ExtraGamesService.SEQ_RUNES[seq[i]] if i < step else "▫️" for i in range(len(seq))
        )
        await edit_or_answer(
            callback,
            (
                f"🧠 <b>XOTIRA ZANJIRI — DAVOM ETISH</b> ({step}/{len(seq)})\n{LINE_SEP}\n"
                f"Ketma-ketlik: {so_far}\n\n"
                f"👇 {step + 1}-belgini tanlang:"
            ),
            reply_markup=seq_pad_kb(callback.from_user.id, ExtraGamesService.SEQ_RUNES),
        )
        await callback.answer()
        return

    st = await _game_state(uid)
    cfg = st["cfg"]
    if not st["seq_enabled"]:
        await callback.answer("🔴 Xotira Zanjiri hozircha o‘chirilgan.", show_alert=True)
        return
    left = max(0, st["seq_limit"] - st["seq_used"])
    if left <= 0:
        await callback.answer("🎟 Bugungi urinishlar tugadi.", show_alert=True)
        return
    win_x = int(cfg.get("seq_win_pct", 220)) / 100
    await edit_or_answer(
        callback,
        (
            f"🧠 <b>XOTIRA ZANJIRI ({win_x:g}x)</b>\n{LINE_SEP}\n"
            "Bot sizga <b>4 ta runa</b> ketma-ketligini <b>3 soniya</b> ko‘rsatadi.\n"
            "So‘ng runalar yashiriladi — xuddi shu tartibda bosib chiqsangiz yutasiz!\n\n"
            f"🏆 To‘g‘ri zanjir: <b>{win_x:g}x</b> + <b>{int(cfg.get('seq_xp', 25))} XP</b>\n"
            f"💰 Balans: <b>{fmt_coins(st['coins'])} 🪙</b>\n"
            f"🎟 Bugun qoldi: <b>{left}/{st['seq_limit']}</b>\n\n"
            "👇 Garov miqdorini tanlang:"
        ),
        reply_markup=seq_bet_kb(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(GameCb.filter(F.act == "seq_go"))
async def cb_game_seq_go(callback: CallbackQuery, callback_data: GameCb, db_user: User) -> None:
    uid = db_user.id
    bet = int(callback_data.val)
    if uid in _PLAYING:
        await callback.answer("⏳ Biroz kuting...")
        return
    _PLAYING.add(uid)
    try:
        async with _user_game_lock(uid):
            prev = _SEQ_ACTIVE.get(uid)
            if prev and not prev.get("finished"):
                await callback.answer("⚠️ Avval joriy Xotira Zanjiri o‘yinini yakunlang!", show_alert=True)
                return
            async with get_session() as session:
                ok, msg, state_obj = await ExtraGamesService.start_seq_game(session, uid, bet)
                if ok and state_obj is not None:
                    await session.commit()
                    _SEQ_ACTIVE[uid] = state_obj
                else:
                    await session.rollback()
        if not ok or state_obj is None:
            await callback.answer(msg[:190], show_alert=True)
            return

        await callback.answer()
        runes = ExtraGamesService.SEQ_RUNES
        seq_str = "  →  ".join(runes[i] for i in state_obj["seq"])
        await edit_or_answer(
            callback,
            (
                f"🧠 <b>ESLAB QOLING! (3 soniya)</b>\n{LINE_SEP}\n\n"
                f"🔮 <b>{seq_str}</b>\n\n"
                "⏳ <i>Ketma-ketlik yashirilmoqda...</i>"
            ),
            reply_markup=None,
        )
        await asyncio.sleep(3.2)
        async with _user_game_lock(uid):
            if uid not in _SEQ_ACTIVE or _SEQ_ACTIVE[uid] is not state_obj:
                return
            state_obj["phase"] = "recall"
            state_obj["deadline"] = asyncio.get_running_loop().time() + 45.0
        blanks = " → ".join("▫️" for _ in state_obj["seq"])
        await edit_or_answer(
            callback,
            (
                f"🧠 <b>XOTIRA ZANJIRI</b> (0/{len(state_obj['seq'])})\n{LINE_SEP}\n"
                f"Ketma-ketlik: {blanks}\n\n"
                "👇 1-belgidan boshlab tartib bilan bosing (45 soniya):"
            ),
            reply_markup=seq_pad_kb(callback.from_user.id, runes),
        )
    except Exception:
        logger.exception("Xotira Zanjiri boshlashda xato")
        _SEQ_ACTIVE.pop(uid, None)
        await callback.message.answer("⚠️ O‘yinda xatolik bo‘ldi. Qayta urinib ko‘ring.")
    finally:
        _PLAYING.discard(uid)


@router.callback_query(GameCb.filter(F.act == "seq_tap"))
async def cb_game_seq_tap(callback: CallbackQuery, callback_data: GameCb, db_user: User) -> None:
    uid = db_user.id
    rune_idx = int(callback_data.val)
    async with _user_game_lock(uid):
        gstate = _SEQ_ACTIVE.get(uid)
        if not gstate or gstate.get("finished"):
            await callback.answer("Bu o‘yin yakunlangan. Yangi o‘yin boshlang!", show_alert=True)
            return
        now_t = asyncio.get_running_loop().time()
        if gstate.get("phase") == "recall" and now_t > float(gstate.get("deadline", now_t + 1)):
            gstate["finished"] = True
            _SEQ_ACTIVE.pop(uid, None)
            seq_str = " → ".join(ExtraGamesService.SEQ_RUNES[i] for i in gstate.get("seq", []))
            await edit_or_answer(
                callback,
                (
                    f"⏰ <b>VAQT TUGADI!</b>\n{LINE_SEP}\n"
                    f"To‘g‘ri ketma-ketlik: {seq_str}\n"
                    f"🪙 Yo‘qotildi: <b>-{int(gstate.get('bet', 0)):,}</b> coin"
                ),
                reply_markup=_game_result_kb(
                    callback.from_user.id,
                    "🧠 Yana o‘ynash",
                    GameCb(owner_id=callback.from_user.id, act="seq").pack(),
                    1,
                ),
            )
            await callback.answer("Vaqt tugadi!")
            return

        new_coins = 0
        used_now = 0
        limit_now = 5
        async with get_session() as session:
            outcome, _payout, msg = await ExtraGamesService.step_seq_game(session, uid, gstate, rune_idx)
            if outcome in ("win", "wrong"):
                new_coins = int((await session.execute(select(User.coins).where(User.id == uid))).scalar_one_or_none() or 0)
                await session.commit()
                _en, used_now, limit_now = await ExtraGamesService.status(session, uid, "seq")
            else:
                await session.rollback()

    if outcome == "invalid":
        await callback.answer(msg[:190], show_alert=True)
        return
    if outcome == "progress":
        await edit_or_answer(
            callback,
            msg,
            reply_markup=seq_pad_kb(callback.from_user.id, ExtraGamesService.SEQ_RUNES),
        )
        await callback.answer("✅ To‘g‘ri!")
        return

    _SEQ_ACTIVE.pop(uid, None)
    left = max(0, limit_now - used_now)
    txt = f"{msg}\n{LINE_SEP}\n💰 Balans: <b>{fmt_coins(new_coins)} 🪙</b>\n🎟 Qoldi: <b>{left}/{limit_now}</b>"
    kb = _game_result_kb(
        callback.from_user.id,
        "🧠 Yana o‘ynash",
        GameCb(owner_id=callback.from_user.id, act="seq").pack(),
        left,
    )
    await edit_or_answer(callback, txt, reply_markup=kb)
    await callback.answer()
