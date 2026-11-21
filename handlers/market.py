import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from sqlalchemy import func, select

from database.session import get_session
from keyboards.main_menu import back_home_kb
from keyboards.market import bot_sell_confirm_kb, bot_sell_list_kb, listing_detail_kb, market_list_kb
from models.card import Card
from models.user import User
from models.user_card import UserCard
from services.collection_service import CollectionService
from services.market_service import MarketService
from services.user_service import InsufficientCoinsError
from utils.callback_data import MarketCb, NavCb
from utils.constants import PAGE_SIZE
from utils.constants import get_rarity_meta
from utils.formatting import LINE, header, esc, fmt_coins, format_card_block
from utils.tg_helpers import append_private_ad, edit_or_answer

logger = logging.getLogger(__name__)
router = Router(name="market")


async def _show_market(target, owner_id: int, db_user: User, page: int = 1, my_only: bool = False) -> None:
    async with get_session() as session:
        listings, total = await MarketService.get_active_listings(
            session=session,
            page=page,
            page_size=PAGE_SIZE,
            seller_id=db_user.id if my_only else None,
        )

    title = header("📦 <b>MENING E’LONLARIM</b>" if my_only else "🛒 <b>BOZOR</b>")
    if not listings:
        hint = "Kartani sotuvga qo‘yish uchun: 🎴 Kartalarim → kartani tanlang → «Sotish»." if my_only else "Hozircha sotuvda karta yo‘q. Kartangizni birinchi bo‘lib qo‘ying!"
        text = f"{title}\n📭 E’lonlar yo‘q.\n\n💡 {hint}"
    else:
        lines = [title, f"📌 Jami e’lonlar: <b>{total}</b>\n"]
        for item in listings:
            meta = get_rarity_meta(item.card.rarity)
            lines.append(f"{meta.emoji} <b>{esc(item.card.character_name)}</b>")
            seller = "" if my_only else f" · 👤 {esc(item.seller.display_name)}"
            lines.append(f"      💰 {fmt_coins(item.listing.price)} 🪙{seller}")
        lines.append("\n👇 Kartani tanlang:")
        text = "\n".join(lines)

    is_pm = (isinstance(target, Message) and target.chat.type == "private") or (
        isinstance(target, CallbackQuery) and target.message is not None and target.message.chat.type == "private"
    )
    if is_pm:
        text = await append_private_ad(text, target.bot, db_user)

    await edit_or_answer(
        target,
        text,
        reply_markup=market_list_kb(
            owner_id=owner_id,
            listings=listings,
            page=page,
            total=total,
            my_only=my_only,
            page_size=PAGE_SIZE,
        ),
    )


@router.message(Command("market"))
async def cmd_market(message: Message, db_user: User) -> None:
    await _show_market(message, message.from_user.id, db_user, page=1, my_only=False)


@router.message(Command("sell"))
async def cmd_sell(message: Message, db_user: User) -> None:
    """Quick command in groups & private chat: /sell <card_code_or_id> <price>"""
    parts = (message.text or "").split()
    if len(parts) != 3 or not parts[2].isdigit():
        await message.answer(
            "💰 <b>Kartani sotuvga qo‘yish:</b>\n\n"
            "Format: <code>/sell CRD-0001 2500</code>\n"
            "Yoki /cards orqali inline tugma bilan soting."
        )
        return

    card_ref = parts[1].strip()
    price = int(parts[2])

    async with get_session() as session:
        uc = None
        if card_ref.isdigit():
            cand = await session.get(UserCard, int(card_ref))
            if cand is not None and cand.user_id == db_user.id and not cand.is_locked:
                uc = cand
        else:
            card = (
                await session.execute(
                    select(Card).where(func.lower(Card.card_code) == card_ref.lower())
                )
            ).scalar_one_or_none()
            if card is not None:
                uc = await CollectionService.get_unlocked_copy(session, db_user.id, card.id)

        if uc is None:
            await message.answer("❌ Sizda bunday bo‘sh karta topilmadi (yoki allaqachon sotuvda).")
            return

        res = await MarketService.list_card(session, db_user.id, uc.id, price)
        if res.success:
            await session.commit()
        else:
            await session.rollback()

    if not res.success:
        await message.answer(f"❌ {res.message}")
        return

    await message.answer(
        f"✅ <b>{esc(res.card.character_name)}</b> marketga <b>{fmt_coins(price)} 🪙</b> narxda qo‘yildi!\n"
        f"🆔 E'lon ID: <code>{res.listing.id}</code> (Sotib olish: <code>/buy {res.listing.id}</code>)",
        reply_markup=back_home_kb(message.from_user.id, "market"),
    )


@router.message(Command("buy"))
async def cmd_buy(message: Message, db_user: User) -> None:
    """Quick command in groups & private chat: /buy <listing_id>"""
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("🛍 Format: <code>/buy e'lon_id</code> (masalan: <code>/buy 1</code>)")
        return
    listing_id = int(parts[1])
    async with get_session() as session:
        try:
            res = await MarketService.buy_listing(session, listing_id, db_user.id)
            if res.success:
                await session.commit()
            else:
                await session.rollback()
        except InsufficientCoinsError:
            await session.rollback()
            await message.answer("❌ Coinlaringiz yetarli emas!")
            return

    if not res.success:
        await message.answer(f"❌ {res.message}")
        return
    extra = ""
    if res.is_duplicate:
        if res.maxed:
            extra = f"\n👑 Karta maksimal darajada edi — +{fmt_coins(res.refund)} 🪙 qaytarildi!"
        else:
            extra = f"\n🔁 Bu karta sizda bor edi — daraja ko‘tarildi: <b>{res.level_before} → {res.level_after}</b>!"
    await message.answer(
        f"🎉 <b>{esc(res.card.character_name)}</b> muvaffaqiyatli sotib olindi! (-{fmt_coins(res.price_paid)} 🪙){extra}",
        reply_markup=back_home_kb(message.from_user.id, "cards"),
    )


@router.callback_query(NavCb.filter(F.target == "market"))
@router.callback_query(MarketCb.filter(F.action == "list"))
async def cb_market_list(callback: CallbackQuery, db_user: User, callback_data: MarketCb = None) -> None:
    page = callback_data.page if isinstance(callback_data, MarketCb) else 1
    await _show_market(callback, callback.from_user.id, db_user, page=page, my_only=False)


@router.callback_query(MarketCb.filter(F.action == "my_listings"))
async def cb_my_listings(callback: CallbackQuery, callback_data: MarketCb, db_user: User) -> None:
    await _show_market(callback, callback.from_user.id, db_user, page=callback_data.page, my_only=True)


@router.callback_query(MarketCb.filter(F.action == "view"))
async def cb_listing_view(callback: CallbackQuery, callback_data: MarketCb, db_user: User) -> None:
    async with get_session() as session:
        detail = await MarketService.get_listing_detail(session, callback_data.listing_id)

    if detail is None or detail.listing.status != "active":
        await callback.answer("Bu e'lon allaqachon yopilgan.", show_alert=True)
        await _show_market(callback, callback.from_user.id, db_user, page=callback_data.page)
        return

    text = (
        f"{header('🛒 <b>BOZOR</b>')}\n"
        f"{format_card_block(detail.card)}\n"
        f"{LINE}\n"
        f"👤 Sotuvchi: <b>{esc(detail.seller.display_name)}</b>\n"
        f"💰 Narxi: <b>{fmt_coins(detail.listing.price)}</b> 🪙"
    )
    kb = listing_detail_kb(
        owner_id=callback.from_user.id,
        listing_id=detail.listing.id,
        is_seller=(detail.seller.id == db_user.id),
        page=callback_data.page,
    )
    await edit_or_answer(callback, text, reply_markup=kb)


@router.callback_query(MarketCb.filter(F.action == "buy"))
async def cb_buy_listing(callback: CallbackQuery, callback_data: MarketCb, db_user: User) -> None:
    async with get_session() as session:
        try:
            res = await MarketService.buy_listing(
                session=session,
                listing_id=callback_data.listing_id,
                buyer_id=db_user.id,
            )
            if res.success:
                await session.commit()
            else:
                await session.rollback()
        except InsufficientCoinsError:
            await session.rollback()
            await callback.answer("❌ Coinlaringiz yetarli emas!", show_alert=True)
            return
        except Exception as exc:
            await session.rollback()
            logger.exception("Market purchase error: %s", exc)
            await callback.answer("❌ Xarid vaqtida xatolik yuz berdi.", show_alert=True)
            return

    if not res.success:
        await callback.answer(f"❌ {res.message}", show_alert=True)
        return

    await callback.answer("🎉 Karta sotib olindi!", show_alert=True)
    extra = ""
    if res.is_duplicate:
        if res.maxed:
            extra = f"\n👑 Karta maksimal darajada edi — <b>+{fmt_coins(res.refund)} 🪙</b> qaytarildi!"
        else:
            extra = f"\n🔁 Bu karta sizda bor edi — daraja ko‘tarildi: <b>{res.level_before} → {res.level_after}</b>!"
    await edit_or_answer(
        callback,
        f"🎉 <b>TABRIKLAYMIZ!</b>\n\n"
        f"🎴 <b>{esc(res.card.character_name)}</b> kartasi sizning kolleksiyangizga o‘tkazildi!\n"
        f"💰 To‘landi: <b>{fmt_coins(res.price_paid)} 🪙</b>{extra}",
        reply_markup=back_home_kb(callback.from_user.id, "market"),
    )


@router.callback_query(MarketCb.filter(F.action == "cancel"))
async def cb_cancel_listing(callback: CallbackQuery, callback_data: MarketCb, db_user: User) -> None:
    async with get_session() as session:
        res = await MarketService.cancel_listing(session, callback_data.listing_id, db_user.id)
        if res.success:
            await session.commit()
        else:
            await session.rollback()

    await callback.answer(res.message, show_alert=not res.success)
    await _show_market(callback, callback.from_user.id, db_user, page=1, my_only=True)


# ------------------------------------------------------------------
# Bot Market: kartani botga sotish (rank narxi + level bonusi)
# ------------------------------------------------------------------
async def _show_bot_sell_list(target, owner_id: int, db_user: User, page: int = 1) -> None:
    async with get_session() as session:
        quotes, total = await MarketService.get_bot_sellable_cards(
            session=session,
            user_id=db_user.id,
            page=page,
            page_size=8,
        )
    if not quotes:
        await edit_or_answer(
            target,
            f"{header('🤖 <b>BOTGA SOTISH</b>')}\n"
            "📭 Hozircha botga sotish uchun bo‘sh kartalaringiz yo‘q.\n\n"
            "<i>Marketda, auksionda yoki almashuvda turgan kartalar ko‘rsatilmaydi.</i>",
            reply_markup=bot_sell_list_kb(owner_id, [], page=1, total=0, page_size=8),
        )
        return
    lines = [
        header("🤖 <b>BOTGA SOTISH</b>"),
        f"📦 Mavjud bo‘sh kartalar: <b>{total}</b> ta\n",
        "Narx avtomatik <b>rank narxi + karta darajasi bonusi</b> asosida hisoblanadi.\n",
    ]
    for q in quotes:
        meta = get_rarity_meta(q.card.rarity)
        lv = f" · ⭐ Lvl {q.level}" if q.level > 1 else ""
        lines.append(
            f"{meta.emoji} <b>{esc(q.card.character_name)}</b>{lv} — <b>{fmt_coins(q.total_price)} 🪙</b>"
        )
    lines.append("\n👇 Botga sotmoqchi bo‘lgan kartangizni tanlang:")
    await edit_or_answer(
        target,
        "\n".join(lines),
        reply_markup=bot_sell_list_kb(owner_id, quotes, page=page, total=total, page_size=8),
    )


@router.callback_query(MarketCb.filter(F.action == "bot_list"))
async def cb_market_bot_list(callback: CallbackQuery, callback_data: MarketCb, db_user: User) -> None:
    await _show_bot_sell_list(callback, callback.from_user.id, db_user, page=max(1, callback_data.page))


@router.callback_query(MarketCb.filter(F.action == "bot_ask"))
async def cb_market_bot_ask(callback: CallbackQuery, callback_data: MarketCb, db_user: User) -> None:
    async with get_session() as session:
        ok, msg, q = await MarketService.get_bot_sell_quote(session, db_user.id, callback_data.listing_id)
    if not ok or q is None:
        await callback.answer(msg, show_alert=True)
        await _show_bot_sell_list(callback, callback.from_user.id, db_user, page=max(1, callback_data.page))
        return
    meta = get_rarity_meta(q.card.rarity)
    warn = "\n⚠️ <i>Diqqat: bu karta sevimli/vitrinada belgilangan!</i>" if (q.is_favorite or q.is_showcased) else ""
    bonus_line = (
        f"\n⭐ Daraja bonusi (Lvl {q.level}, +{q.bonus_pct}%/lvl): <b>+{fmt_coins(q.bonus_coins)} 🪙</b>"
        if q.bonus_coins > 0
        else (f"\n⭐ Daraja: <b>{q.level}</b>" if q.level > 1 else "")
    )
    txt = (
        f"{header('🤖 <b>BOTGA SOTISHNI TASDIQLANG</b>')}\n"
        f"{meta.emoji} <b>{esc(q.card.character_name)}</b> ({esc(q.card.anime_name)})\n"
        f"🏷 Rank: <b>{esc(meta.name)}</b> · 🆔 <code>{esc(q.card.card_code)}</code>\n"
        f"{LINE}\n"
        f"💎 Rank narxi: <b>{fmt_coins(q.base_rank_value)} 🪙</b>"
        f"{bonus_line}\n"
        f"💰 <b>Bot to‘laydigan narx: {fmt_coins(q.total_price)} 🪙</b>"
        f"{warn}\n\n"
        "<i>Sotilgach karta botga qaytariladi va coin darhol hisobingizga tushadi.</i>"
    )
    await edit_or_answer(
        callback,
        txt,
        reply_markup=bot_sell_confirm_kb(
            callback.from_user.id,
            q.user_card_id,
            q.total_price,
            page=max(1, callback_data.page),
        ),
    )


@router.callback_query(MarketCb.filter(F.action == "bot_do"))
async def cb_market_bot_do(callback: CallbackQuery, callback_data: MarketCb, db_user: User) -> None:
    async with get_session() as session:
        res = await MarketService.sell_to_bot(session, db_user.id, callback_data.listing_id)
        if res.success:
            await session.commit()
        else:
            await session.rollback()
    if not res.success:
        await callback.answer(f"❌ {res.message}", show_alert=True)
        await _show_bot_sell_list(callback, callback.from_user.id, db_user, page=max(1, callback_data.page))
        return
    await callback.answer(f"✅ +{res.price_paid:,} 🪙!", show_alert=True)
    await edit_or_answer(
        callback,
        f"🤖 <b>KARTA BOTGA SOTILDI!</b>\n\n"
        f"🎴 <b>{esc(res.card.character_name)}</b> (Lvl {res.level_before})\n"
        f"💰 Hisobingizga tushdi: <b>+{fmt_coins(res.price_paid)} 🪙</b>",
        reply_markup=back_home_kb(callback.from_user.id, "market"),
    )
