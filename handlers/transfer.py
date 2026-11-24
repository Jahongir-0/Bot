import logging
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ForceReply, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from database.session import get_session
from keyboards.main_menu import back_home_kb
from keyboards.market import transfer_confirm_kb
from models.card import Card
from models.user import User
from models.user_card import UserCard
from services.coin_gift_service import CoinGiftService
from services.collection_service import CollectionService
from services.transfer_service import TransferService
from services.user_service import InsufficientCoinsError, UserService
from utils.callback_data import GiveCb, NavCb, TransferCb
from utils.constants import get_rarity_meta
from utils.formatting import esc, fmt_coins
from utils.tg_helpers import NotCommandFilter, edit_or_answer, is_private_chat

logger = logging.getLogger(__name__)
router = Router(name="transfer")


CODE_RE = re.compile(r"^crd-\d+$", re.IGNORECASE)


class GiveStates(StatesGroup):
    waiting_recipient = State()
    waiting_name = State()
    choosing = State()
    waiting_amount = State()


GIVE_USAGE = (
    "🤝 <b>Karta berish:</b>\n\n"
    "1) <code>/give @username</code> — keyin karta <b>ismini</b> yozasiz, "
    "sizdagi mos kartalar ro‘yxatidan tanlaysiz.\n"
    "2) Tezkor: <code>/give @username Gojo</code>\n"
    "3) Pullik: <code>/give @username Gojo 1000</code>\n"
    "4) Guruhda xabarga <b>reply</b> qilib: <code>/give Gojo</code>\n\n"
    "<i>Qabul qiluvchi botdan kamida bir marta foydalangan bo‘lishi kerak. "
    "Agar unda bu karta allaqachon bo‘lsa, nusxa birlashib uning kartasiga +1 daraja qo‘shiladi.</i>"
)


async def _create_transfer_and_text(db_user: User, recipient_id: int, user_card_id: int, price: int):
    """Transfer so'rovini yaratadi va tasdiqlash matni/tugmalarini qaytaradi: (res, text, kb)."""
    async with get_session() as session:
        res = await TransferService.create_pending_transfer(
            session=session,
            from_user_id=db_user.id,
            to_user_id=recipient_id,
            user_card_id=user_card_id,
            price=price,
        )
        if res.success:
            await session.commit()
        else:
            await session.rollback()

    if not res.success:
        return res, f"❌ {res.message}", None

    if price == 0:
        confirm_actor_tg_id = db_user.telegram_id
        text = (
            f"🎁 <b>BEPUL YUBORISH TASDIQLASH</b>\n\n"
            f"🎴 Karta: <b>{esc(res.card.character_name)}</b> ({esc(get_rarity_meta(res.card.rarity).name)} · {esc(res.card.card_code)})\n"
            f"👤 Qabul qiluvchi: {esc(res.to_user.display_name)}\n\n"
            f"Ushbu kartani {esc(res.to_user.display_name)} ga berishni tasdiqlaysizmi?"
        )
    else:
        confirm_actor_tg_id = res.to_user.telegram_id
        text = (
            f"💰 <b>PULLIK SOTUV TAKLIFI</b>\n\n"
            f"🎴 Karta: <b>{esc(res.card.character_name)}</b> ({esc(get_rarity_meta(res.card.rarity).name)} · {esc(res.card.card_code)})\n"
            f"👤 Yuboruvchi: {esc(res.from_user.display_name)}\n"
            f"👤 Qabul qiluvchi: {esc(res.to_user.display_name)}\n"
            f"🪙 Narxi: <b>{fmt_coins(price)} coins</b>\n\n"
            f"{esc(res.to_user.display_name)}, ushbu kartani {fmt_coins(price)} coins ga olishni tasdiqlaysizmi?"
        )
    return res, text, transfer_confirm_kb(confirm_actor_tg_id, res.transfer.id)


async def _show_matches(target: Message, state: FSMContext, db_user: User, query: str) -> None:
    """Jo'natuvchidagi shu ismdagi kartalar ro'yxati (rarity bilan) — o'zi tanlaydi."""
    await state.update_data(last_query=query)
    data = await state.get_data()
    to_user_id = int(data["to_user_id"])
    async with get_session() as session:
        if await CollectionService.reconcile_user_card_locks(session, db_user.id):
            await session.commit()
        recipient = await session.get(User, to_user_id)
        matches = await CollectionService.find_cards_by_name(session, db_user.id, query, limit=8)
        recipient_has = set()
        lock_infos = {}
        if matches:
            rows = await session.execute(
                select(UserCard.card_id).where(
                    UserCard.user_id == to_user_id,
                    UserCard.card_id.in_([c.id for _, c in matches]),
                )
            )
            recipient_has = {r[0] for r in rows.all()}
            for uc, _c in matches:
                if uc.is_locked:
                    lock_infos[uc.id] = await CollectionService.get_card_lock_info(session, uc.id)

    if not matches:
        await target.answer(
            f"❌ Sizda «{esc(query)}» nomli karta topilmadi.\n"
            "Boshqa ism yozing yoki /cancel bilan bekor qiling."
        )
        return

    builder = InlineKeyboardBuilder()
    notes = []
    for uc, card in matches:
        meta = get_rarity_meta(card.rarity)
        tag = " (+1 lvl)" if card.id in recipient_has else ""
        label = f"{meta.emoji} {meta.name} · {card.character_name} ({card.card_code}){tag}"[:60]
        if uc.is_locked:
            lock_kind, _eid, lock_lbl = lock_infos.get(uc.id, ("none", 0, "band"))
            notes.append(f"{lock_lbl}: {esc(card.character_name)} ({esc(meta.name)})")
            if lock_kind == "transfer":
                builder.row(
                    InlineKeyboardButton(
                        text=f"🔓 Transferni yechish · {card.character_name}"[:60],
                        callback_data=GiveCb(owner_id=db_user.telegram_id, action="unlock", user_card_id=uc.id).pack(),
                    )
                )
            continue
        builder.row(
            InlineKeyboardButton(
                text=label,
                callback_data=GiveCb(owner_id=db_user.telegram_id, action="pick", user_card_id=uc.id).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="❌ BEKOR", callback_data=GiveCb(owner_id=db_user.telegram_id, action="cancel").pack()
        )
    )
    await state.set_state(GiveStates.choosing)
    price = int(data.get("price", 0))
    price_line = f"\n🪙 Narx: <b>{fmt_coins(price)} coins</b>" if price else "\n🎁 Bepul sovg‘a"
    text = (
        f"🤝 <b>{esc(recipient.display_name)}</b> ga qaysi kartani berasiz?{price_line}\n"
        f"🔍 Qidiruv: «{esc(query)}»\n\n"
        "Pastdagi ro‘yxatdan tanlang (darajasi yonida ko‘rsatilgan):"
    )
    if notes:
        text += "\n\n" + "\n".join(notes)
    await target.answer(text, reply_markup=builder.as_markup())


@router.message(Command("give"))
async def cmd_give(message: Message, state: FSMContext, db_user: User) -> None:
    parts = (message.text or "").split()
    reply_user = message.reply_to_message.from_user if message.reply_to_message else None

    recipient_query = ""
    rest: list = []
    if reply_user is not None and not reply_user.is_bot and not (len(parts) >= 2 and parts[1].startswith("@")):
        recipient_query = str(reply_user.id)
        rest = parts[1:]
    elif len(parts) >= 2 and (parts[1].startswith("@") or parts[1].isdigit()):
        recipient_query = parts[1]
        rest = parts[2:]
    else:
        await message.answer(GIVE_USAGE)
        return

    price = 0
    if len(rest) >= 2 and rest[-1].isdigit():
        price = int(rest[-1])
        rest = rest[:-1]

    async with get_session() as session:
        if reply_user is not None and recipient_query == str(reply_user.id):
            recipient = await UserService.upsert_user(
                session,
                telegram_id=reply_user.id,
                username=reply_user.username,
                first_name=reply_user.first_name or "Collector",
            )
            await session.commit()
        else:
            recipient = await UserService.find_user(session, recipient_query)
    if recipient is None:
        await message.answer(
            "❌ Foydalanuvchi topilmadi. U botdan kamida bir marta foydalangan bo‘lishi kerak (yoki xabariga reply qiling)."
        )
        return
    if recipient.id == db_user.id:
        await message.answer("❌ O‘zingizga karta bera olmaysiz.")
        return

    # Eski usul (karta kodi yoki nusxa ID) ham ishlayveradi
    if len(rest) == 1 and (CODE_RE.match(rest[0]) or rest[0].isdigit()):
        await _legacy_give(message, db_user, recipient, rest[0], price)
        return

    await state.clear()
    await state.update_data(
        to_user_id=recipient.id,
        from_tg=db_user.telegram_id,
        price=price,
    )
    name = " ".join(rest).strip()
    if name:
        await _show_matches(message, state, db_user, name)
        return

    await state.set_state(GiveStates.waiting_name)
    await message.reply(
        f"🎴 <b>{esc(recipient.display_name)}</b> ga qaysi kartani berasiz?\n"
        "Karta <b>ismini</b> yozing (masalan: <code>Gojo</code>). Bekor qilish: /cancel",
        reply_markup=ForceReply(selective=True, input_field_placeholder="Karta ismi..."),
    )


@router.callback_query(NavCb.filter(F.target == "give"))
async def cb_give_start(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    """🎁 SOVG'A BERISH: karta yoki coin yuborishni tanlash."""
    await state.clear()
    oid = callback.from_user.id
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="🎴 Karta yuborish", callback_data=GiveCb(owner_id=oid, action="start_card").pack()),
        InlineKeyboardButton(text="🪙 Coin yuborish", callback_data=GiveCb(owner_id=oid, action="start_coin").pack()),
    )
    kb.row(InlineKeyboardButton(text="🏠 BOSH MENU", callback_data=NavCb(owner_id=oid, target="home").pack()))
    await edit_or_answer(
        callback,
        "🎁 <b>SOVG‘A BERISH</b>\n"
        "━━━━━━━━━━━━━━━\n"
        "Do‘stlaringizni xursand qiling!\n\n"
        "🎴 <b>Karta yuborish</b> — kartangizni sovg‘a qiling yoki sotib yuboring\n"
        "🪙 <b>Coin yuborish</b> — o‘zingizdagi coinning xohlagan qismini yuboring "
        "(yuborgan miqdor sizdan ayriladi, do‘stingizga qo‘shiladi)\n\n"
        "👇 Tanlang:",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(GiveCb.filter(F.action == "start_card"))
async def cb_give_start_card(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    await state.clear()
    await state.set_state(GiveStates.waiting_recipient)
    await state.update_data(from_tg=db_user.telegram_id, price=0, kind="card")
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data=GiveCb(owner_id=callback.from_user.id, action="cancel", user_card_id=0).pack()))
    await edit_or_answer(
        callback,
        "🎴 <b>KARTA YUBORISH</b>\n"
        "━━━━━━━━━━━━━━━\n"
        "1️⃣ Qabul qiluvchining <b>@username</b> yoki <b>ID</b> raqamini yozing\n"
        "2️⃣ Karta <b>ismini</b> yozing\n"
        "3️⃣ Ro‘yxatdan kartani tanlang va tasdiqlang\n\n"
        "<i>Qabul qiluvchi botdan kamida bir marta foydalangan bo‘lishi kerak. "
        "Pullik sotish uchun: <code>/give @username Gojo 1000</code></i>",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(GiveCb.filter(F.action == "start_coin"))
async def cb_give_start_coin(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    async with get_session() as session:
        cfg = await CoinGiftService.get_config(session)
    if not cfg["enabled"]:
        await callback.answer("🪙 Coin yuborish hozircha o‘chirilgan.", show_alert=True)
        return
    await state.clear()
    await state.set_state(GiveStates.waiting_recipient)
    await state.update_data(from_tg=db_user.telegram_id, price=0, kind="coin")
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data=GiveCb(owner_id=callback.from_user.id, action="cancel", user_card_id=0).pack()))
    limit_line = f"\n📅 Kunlik limit: <b>{cfg['daily_limit']:,} 🪙</b>" if cfg["daily_limit"] else ""
    fee_line = f"\n💸 Komissiya: <b>{cfg['fee_percent']}%</b>" if cfg["fee_percent"] else ""
    await edit_or_answer(
        callback,
        "🪙 <b>COIN YUBORISH</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"💰 Balansingiz: <b>{fmt_coins(db_user.coins)} 🪙</b>\n"
        f"🔻 Minimal miqdor: <b>{cfg['min_amount']:,} 🪙</b>{limit_line}{fee_line}\n\n"
        "1️⃣ Qabul qiluvchining <b>@username</b> yoki <b>ID</b> raqamini yozing\n"
        "2️⃣ Miqdorni yozing\n"
        "3️⃣ Tasdiqlang\n\n"
        "<i>Qabul qiluvchi botdan kamida bir marta foydalangan bo‘lishi kerak.</i>",
        reply_markup=kb.as_markup(),
    )


@router.message(GiveStates.waiting_recipient, NotCommandFilter())
async def fsm_give_recipient(message: Message, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    if data.get("from_tg") != message.from_user.id:
        return
    raw = (message.text or "").strip().split()[0] if (message.text or "").strip() else ""
    if not raw or not (raw.startswith("@") or raw.isdigit()):
        if is_private_chat(message):
            await message.answer("⚠️ @username yoki ID raqam yozing (masalan: <code>@doston</code>). Bekor qilish: /cancel")
        return
    async with get_session() as session:
        recipient = await UserService.find_user(session, raw)
    if recipient is None:
        await message.answer("❌ Foydalanuvchi topilmadi. U botdan kamida bir marta foydalangan bo‘lishi kerak. Qayta yozing yoki /cancel.")
        return
    if recipient.id == db_user.id:
        await message.answer("❌ O‘zingizga karta bera olmaysiz. Boshqa foydalanuvchini yozing.")
        return
    await state.update_data(to_user_id=recipient.id, from_tg=db_user.telegram_id, price=0)
    if data.get("kind") == "coin":
        await state.set_state(GiveStates.waiting_amount)
        await message.reply(
            f"🪙 <b>{esc(recipient.display_name)}</b> ga necha coin yuborasiz?\n"
            f"💰 Balansingiz: <b>{fmt_coins(db_user.coins)} 🪙</b>\n"
            "Miqdorni yozing (masalan: <code>500</code>). Bekor qilish: /cancel",
            reply_markup=ForceReply(selective=True, input_field_placeholder="Miqdor..."),
        )
        return
    await state.set_state(GiveStates.waiting_name)
    await message.reply(
        f"🎴 <b>{esc(recipient.display_name)}</b> ga qaysi kartani berasiz?\n"
        "Karta <b>ismini</b> yozing (masalan: <code>Gojo</code>). Bekor qilish: /cancel",
        reply_markup=ForceReply(selective=True, input_field_placeholder="Karta ismi..."),
    )


def _coin_confirm_kb(owner_id: int) -> "InlineKeyboardMarkup":
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ Yuborish", callback_data=GiveCb(owner_id=owner_id, action="coin_ok").pack()),
        InlineKeyboardButton(text="❌ Bekor", callback_data=GiveCb(owner_id=owner_id, action="cancel").pack()),
    )
    return kb.as_markup()


async def _ask_coin_confirm(message: Message, state: FSMContext, db_user: User, amount: int) -> None:
    data = await state.get_data()
    async with get_session() as session:
        cfg = await CoinGiftService.get_config(session)
        recipient = await session.get(User, int(data["to_user_id"]))
        sender = await session.get(User, db_user.id)
        sent_today = await CoinGiftService.sent_today(session, db_user.id)
    if not cfg["enabled"]:
        await state.clear()
        await message.answer("🪙 Coin yuborish hozircha o‘chirilgan.")
        return
    if amount < cfg["min_amount"]:
        await message.answer(f"⚠️ Minimal miqdor: <b>{cfg['min_amount']:,} 🪙</b>. Qayta yozing yoki /cancel.")
        return
    if sender is None or int(sender.coins or 0) < amount:
        await message.answer(f"⚠️ Coinlaringiz yetarli emas. Balans: <b>{fmt_coins(sender.coins if sender else 0)} 🪙</b>. Qayta yozing yoki /cancel.")
        return
    if cfg["daily_limit"] and sent_today + amount > cfg["daily_limit"]:
        left = max(0, cfg["daily_limit"] - sent_today)
        await message.answer(f"⚠️ Kunlik limit: {cfg['daily_limit']:,} 🪙. Bugun yana <b>{left:,} 🪙</b> yubora olasiz. Qayta yozing yoki /cancel.")
        return
    fee = amount * cfg["fee_percent"] // 100
    await state.update_data(coin_amount=amount)
    await state.set_state(GiveStates.choosing)
    fee_line = f"\n💸 Komissiya: <b>{fee:,} 🪙</b> ({cfg['fee_percent']}%) — qabul qiluvchi <b>{amount - fee:,} 🪙</b> oladi" if fee else ""
    await message.answer(
        "🪙 <b>COIN YUBORISHNI TASDIQLANG</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"👤 Qabul qiluvchi: <b>{esc(recipient.display_name)}</b>\n"
        f"🪙 Miqdor: <b>{amount:,}</b>{fee_line}\n"
        f"💰 Yuborgandan keyin balansingiz: <b>{int(sender.coins) - amount:,} 🪙</b>",
        reply_markup=_coin_confirm_kb(db_user.telegram_id),
    )


@router.message(GiveStates.waiting_amount, NotCommandFilter())
async def fsm_give_amount(message: Message, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    if data.get("from_tg") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit() or int(raw) <= 0:
        if is_private_chat(message):
            await message.answer("⚠️ Faqat musbat butun son yozing (masalan: <code>500</code>). Bekor qilish: /cancel")
        return
    await _ask_coin_confirm(message, state, db_user, int(raw))


@router.callback_query(GiveCb.filter(F.action == "coin_ok"))
async def cb_give_coin_ok(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    to_id = data.get("to_user_id")
    amount = int(data.get("coin_amount") or 0)
    if not to_id or amount <= 0 or data.get("from_tg") != callback.from_user.id or data.get("kind") != "coin":
        await callback.answer("Muddat tugagan. Qaytadan boshlang.", show_alert=True)
        return
    await state.clear()
    async with get_session() as session:
        try:
            ok, msg, received, new_balance = await CoinGiftService.send_coins(session, db_user.id, int(to_id), amount)
            recipient = await session.get(User, int(to_id))
            if ok:
                await session.commit()
            else:
                await session.rollback()
        except Exception:
            await session.rollback()
            logger.exception("Coin yuborishda xato")
            await callback.answer("❌ Xatolik yuz berdi.", show_alert=True)
            return
    if not ok:
        await edit_or_answer(callback, f"❌ {esc(msg)}", reply_markup=back_home_kb(callback.from_user.id, "give"))
        return
    await edit_or_answer(
        callback,
        "✅ <b>COIN YUBORILDI!</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"👤 {esc(recipient.display_name)} ga <b>{amount:,} 🪙</b> yuborildi\n"
        f"💰 Yangi balansingiz: <b>{new_balance:,} 🪙</b>",
        reply_markup=back_home_kb(callback.from_user.id, "give"),
    )
    try:
        await callback.bot.send_message(
            recipient.telegram_id,
            "🎁 <b>SIZGA COIN YUBORILDI!</b>\n"
            "━━━━━━━━━━━━━━━\n"
            f"👤 Kimdan: <b>{esc(db_user.display_name)}</b>\n"
            f"🪙 Miqdor: <b>+{received:,}</b>",
        )
    except Exception:
        pass


@router.message(Command("givecoin"))
async def cmd_give_coin(message: Message, state: FSMContext, db_user: User) -> None:
    """/givecoin @username 500  yoki xabarga reply qilib: /givecoin 500"""
    parts = (message.text or "").split()
    reply_user = message.reply_to_message.from_user if message.reply_to_message else None
    amount_raw = ""
    if reply_user is not None and not reply_user.is_bot and not (len(parts) >= 2 and parts[1].startswith("@")):
        query, amount_raw = str(reply_user.id), (parts[1] if len(parts) >= 2 else "")
    elif len(parts) >= 3 and (parts[1].startswith("@") or parts[1].isdigit()):
        query, amount_raw = parts[1], parts[2]
    else:
        await message.answer(
            "🪙 <b>Coin yuborish:</b>\n\n<code>/givecoin @username 500</code>\n"
            "Yoki guruhda xabarga <b>reply</b> qilib: <code>/givecoin 500</code>"
        )
        return
    amount_raw = amount_raw.replace(",", "")
    if not amount_raw.isdigit() or int(amount_raw) <= 0:
        await message.answer("⚠️ Miqdorni musbat butun son bilan yozing.")
        return
    async with get_session() as session:
        if reply_user is not None and query == str(reply_user.id):
            recipient = await UserService.upsert_user(
                session,
                telegram_id=reply_user.id,
                username=reply_user.username,
                first_name=reply_user.first_name or "Collector",
            )
            await session.commit()
        else:
            recipient = await UserService.find_user(session, query)
    if recipient is None:
        await message.answer("❌ Foydalanuvchi topilmadi. U botdan kamida bir marta foydalangan bo‘lishi kerak.")
        return
    if recipient.id == db_user.id:
        await message.answer("❌ O‘zingizga coin yubora olmaysiz.")
        return
    await state.clear()
    await state.update_data(to_user_id=recipient.id, from_tg=db_user.telegram_id, price=0, kind="coin")
    await _ask_coin_confirm(message, state, db_user, int(amount_raw))


@router.message(GiveStates.waiting_name, NotCommandFilter())
async def fsm_give_name(message: Message, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    if data.get("from_tg") != message.from_user.id:
        return
    query = (message.text or "").strip()
    if len(query) < 2:
        if is_private_chat(message):
            await message.answer("⚠️ Karta ismini yozing (kamida 2 harf). Bekor qilish: /cancel")
        return
    await _show_matches(message, state, db_user, query[:40])


@router.callback_query(GiveCb.filter(F.action == "cancel"))
async def cb_give_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_or_answer(callback, "❌ Karta berish bekor qilindi.", reply_markup=back_home_kb(callback.from_user.id))


@router.callback_query(GiveCb.filter(F.action == "unlock"))
async def cb_give_unlock(callback: CallbackQuery, callback_data: GiveCb, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    if data.get("from_tg") != callback.from_user.id:
        await callback.answer("Muddat tugagan. /give ni qaytadan yozing.", show_alert=True)
        return
    async with get_session() as session:
        res = await TransferService.cancel_or_unlock_for_user(session, db_user.id, callback_data.user_card_id)
        if res.success:
            await session.commit()
        else:
            await session.rollback()
    await callback.answer(res.message[:190], show_alert=True)
    last_q = str(data.get("last_query") or "").strip()
    if res.success and last_q and callback.message:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await _show_matches(callback.message, state, db_user, last_q)


@router.callback_query(GiveCb.filter(F.action == "pick"))
async def cb_give_pick(callback: CallbackQuery, callback_data: GiveCb, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    to_user_id = data.get("to_user_id")
    if not to_user_id or data.get("from_tg") != callback.from_user.id:
        await callback.answer("Muddat tugagan. /give ni qaytadan yozing.", show_alert=True)
        return

    price = int(data.get("price", 0))
    res, text, kb = await _create_transfer_and_text(
        db_user, int(to_user_id), callback_data.user_card_id, price
    )
    if not res.success:
        await callback.answer(res.message, show_alert=True)
        return
    await state.clear()
    await edit_or_answer(callback, text, reply_markup=kb)
    if price > 0 and res.to_user and callback.bot:
        chat_id = callback.message.chat.id if callback.message else None
        if chat_id != res.to_user.telegram_id:
            try:
                await callback.bot.send_message(res.to_user.telegram_id, text, reply_markup=kb)
            except Exception:
                pass


async def _legacy_give(message: Message, db_user: User, recipient: User, card_ref: str, price: int) -> None:
    async with get_session() as session:
        if await CollectionService.reconcile_user_card_locks(session, db_user.id):
            await session.commit()
        uc = None
        if card_ref.isdigit():
            cand = await session.get(UserCard, int(card_ref))
            if cand is not None and cand.user_id == db_user.id and not cand.is_locked:
                uc = cand
        else:
            card = (
                await session.execute(select(Card).where(func.lower(Card.card_code) == card_ref.strip().lower()))
            ).scalar_one_or_none()
            if card is not None:
                uc = await CollectionService.get_unlocked_copy(session, db_user.id, card.id)

    if uc is None:
        await message.answer("❌ Sizda bunday bo‘sh karta topilmadi (yoki karta marketda/transferda band).")
        return
    res, text, kb = await _create_transfer_and_text(db_user, recipient.id, uc.id, price)
    await message.answer(text, reply_markup=kb)
    if res.success and price > 0 and res.to_user and message.bot and message.chat.id != res.to_user.telegram_id:
        try:
            await message.bot.send_message(res.to_user.telegram_id, text, reply_markup=kb)
        except Exception:
            pass


@router.callback_query(TransferCb.filter(F.action == "confirm"))
async def cb_transfer_confirm(callback: CallbackQuery, callback_data: TransferCb, db_user: User) -> None:
    async with get_session() as session:
        try:
            res = await TransferService.confirm_transfer(
                session=session,
                transfer_id=callback_data.transfer_id,
                actor_user_id=db_user.id,
            )
            if res.success:
                await session.commit()
            else:
                await session.rollback()
        except InsufficientCoinsError:
            await session.rollback()
            await callback.answer("❌ Transfer uchun coinlaringiz yetarli emas!", show_alert=True)
            return
        except Exception as exc:
            await session.rollback()
            logger.exception("Transfer confirm failed: %s", exc)
            await callback.answer("❌ Transferda xatolik yuz berdi.", show_alert=True)
            return

    if not res.success:
        await callback.answer(f"❌ {res.message}", show_alert=True)
        return

    extra = ""
    if res.is_duplicate:
        if res.maxed:
            extra = f"\n👑 Qabul qiluvchida karta maksimal darajada — +{fmt_coins(res.refund)} 🪙 qaytarildi!"
        else:
            extra = f"\n🔁 Qabul qiluvchida bu karta bor edi — daraja ko‘tarildi: <b>{res.level_before} → {res.level_after}</b>!"

    await edit_or_answer(
        callback,
        f"✅ <b>TRANSFER YAKUNLANDI!</b>\n\n"
        f"🎴 <b>{esc(res.card.character_name)}</b> → {esc(res.to_user.display_name)} ga o‘tkazildi!{extra}",
        reply_markup=back_home_kb(callback.from_user.id),
    )


@router.callback_query(TransferCb.filter(F.action == "cancel"))
async def cb_transfer_cancel(callback: CallbackQuery, callback_data: TransferCb, db_user: User) -> None:
    async with get_session() as session:
        res = await TransferService.cancel_transfer(
            session=session,
            transfer_id=callback_data.transfer_id,
            actor_user_id=db_user.id,
        )
        if res.success:
            await session.commit()
        else:
            await session.rollback()

    await edit_or_answer(
        callback,
        f"❌ {res.message}",
        reply_markup=back_home_kb(callback.from_user.id),
    )
