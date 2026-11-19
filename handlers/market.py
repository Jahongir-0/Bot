import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from sqlalchemy import func, select

from database.session import get_session
from keyboards.main_menu import back_home_kb
from keyboards.market import listing_detail_kb, market_list_kb
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
from utils.tg_helpers import edit_or_answer

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
    await message.answer(
        f"🎉 <b>{esc(res.card.character_name)}</b> muvaffaqiyatli sotib olindi! (-{fmt_coins(res.listing.price)} 🪙)",
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
    await edit_or_answer(
        callback,
        f"🎉 <b>TABRIKLAYMIZ!</b>\n\n"
        f"🎴 <b>{esc(res.card.character_name)}</b> kartasi sizning kolleksiyangizga o‘tkazildi!\n"
        f"💰 To‘landi: <b>{fmt_coins(res.listing.price)} 🪙</b>",
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
