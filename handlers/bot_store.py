"""Bot Do‘koni: botdan karta sotib olish va botga karta sotish handlerlari."""

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from database.session import get_session
from keyboards.main_menu import back_home_kb
from keyboards.v2 import (
    bot_store_detail_kb,
    bot_store_list_kb,
    bot_store_menu_kb,
    bot_store_sell_confirm_kb,
    bot_store_sell_list_kb,
)
from models.user import User
from services.bot_store_service import BotStoreService
from services.card_level_service import CardLevelService
from services.market_service import MarketService
from services.settings_service import SettingsService
from services.user_service import InsufficientCoinsError
from utils.callback_data import BotStoreCb
from utils.constants import PAGE_SIZE, get_rarity_meta
from utils.formatting import LINE, esc, fmt_coins, format_v25_card_caption, header
from utils.tg_helpers import edit_or_answer, safe_delete, send_card_message

logger = logging.getLogger(__name__)
router = Router(name="bot_store")


async def _show_store_menu(callback: CallbackQuery) -> None:
    async with get_session() as session:
        _, total = await BotStoreService.list_active_listings(session, page=1, page_size=1)
        cfg = await SettingsService.get_bot_store_config(session)
        lvl_cfg = await CardLevelService.get_config(session)
    disc_pct = int(cfg.get("sell_discount_pct", 15))
    lvl_bonus = int(lvl_cfg.get("bot_sell_level_bonus_pct", 10))
    status_buy = "🟢 Yoqilgan" if (cfg.get("enabled", True) and cfg.get("buy_enabled", True)) else "🔴 O‘chirilgan"
    status_sell = "🟢 Yoqilgan" if (cfg.get("enabled", True) and cfg.get("sell_enabled", True)) else "🔴 O‘chirilgan"
    text = (
        f"{header('🏛 <b>BOT DO‘KONI</b>')}\n"
        f"Bot Do‘konida foydalanuvchilar botga sotgan hamda admin qo‘shgan kartalar jamlanadi.\n\n"
        f"📦 Do‘kondagi faol kartalar: <b>{total}</b> ta\n"
        f"🛒 Sotib olish: {status_buy}\n"
        f"🤖 Botga sotish: {status_sell}\n"
        f"📉 Bot chegirmasi: <b>-{disc_pct}%</b> | ⭐ Daraja bonusi: <b>+{lvl_bonus}%/lvl</b>"
    )
    await edit_or_answer(callback, text, reply_markup=bot_store_menu_kb(callback.from_user.id))


async def _show_buy_list(target, page: int = 1, owner_id: int | None = None) -> None:
    page = max(1, int(page))
    uid = owner_id if owner_id is not None else target.from_user.id
    async with get_session() as session:
        items, total = await BotStoreService.list_active_listings(session, page=page, page_size=PAGE_SIZE)
    if not items:
        await edit_or_answer(
            target,
            f"{header('🏛 <b>BOT DO‘KONIDAN OLISH</b>')}\n📭 Hozircha do‘konda faol karta yo‘q.",
            reply_markup=bot_store_list_kb(uid, [], page=1, total=0, page_size=PAGE_SIZE),
        )
        return
    lines = [header("🏛 <b>BOT DO‘KONIDAN OLISH</b>"), f"📦 Jami mavjud: <b>{total}</b> ta\n"]
    for listing, card in items:
        meta = get_rarity_meta(card.rarity)
        src = "👤 User sotgan" if listing.source == "user_sell" else "🏛 Admin"
        lv = f" · Lvl {listing.card_level}" if int(listing.card_level or 1) > 1 else ""
        lines.append(
            f"{meta.emoji} <b>{esc(card.character_name)}</b>{lv} — <b>{fmt_coins(listing.price_coins)} 🪙</b> ({src})"
        )
    lines.append("\n👇 Tafsilotini ko‘rish uchun tanlang:")
    await edit_or_answer(
        target,
        "\n".join(lines),
        reply_markup=bot_store_list_kb(
            uid, items, page=page, total=total, page_size=PAGE_SIZE
        ),
    )


async def _show_sell_list(target, db_user: User, page: int = 1, owner_id: int | None = None) -> None:
    page = max(1, int(page))
    uid = owner_id if owner_id is not None else target.from_user.id
    async with get_session() as session:
        quotes, total = await MarketService.get_bot_sellable_cards(
            session=session, user_id=db_user.id, page=page, page_size=PAGE_SIZE
        )
    if not quotes:
        await edit_or_answer(
            target,
            f"{header('🤖 <b>BOTGA SOTISH</b>')}\n"
            "📭 Hozircha botga sotish uchun bo‘sh kartalaringiz yo‘q.\n\n"
            "<i>Marketda, auksionda, taklifda yoki almashuvda band kartalar ko‘rsatilmaydi.</i>",
            reply_markup=bot_store_sell_list_kb(
                uid, [], page=1, total=0, page_size=PAGE_SIZE
            ),
        )
        return
    lines = [
        header("🤖 <b>BOTGA SOTISH</b>"),
        f"📦 Mavjud bo‘sh kartalar: <b>{total}</b> ta\n",
        "Narx <b>Rank narxi + Daraja bonusi − Bot chegirmasi</b> asosida hisoblanadi.\n",
    ]
    for q in quotes:
        meta = get_rarity_meta(q.card.rarity)
        lv = f" · Lvl {q.level}" if q.level > 1 else ""
        lines.append(f"{meta.emoji} <b>{esc(q.card.character_name)}</b>{lv} — <b>{fmt_coins(q.total_price)} 🪙</b>")
    lines.append("\n👇 Botga sotmoqchi bo‘lgan kartani tanlang:")
    await edit_or_answer(
        target,
        "\n".join(lines),
        reply_markup=bot_store_sell_list_kb(
            uid, quotes, page=page, total=total, page_size=PAGE_SIZE
        ),
    )


@router.callback_query(BotStoreCb.filter(F.act == "v"))
async def cb_bot_store_menu(callback: CallbackQuery) -> None:
    await _show_store_menu(callback)


@router.callback_query(BotStoreCb.filter(F.act == "buy_list"))
async def cb_bot_store_buy_list(callback: CallbackQuery, callback_data: BotStoreCb) -> None:
    await _show_buy_list(callback, page=max(1, callback_data.p))


@router.callback_query(BotStoreCb.filter(F.act == "buy_det"))
async def cb_bot_store_buy_detail(callback: CallbackQuery, callback_data: BotStoreCb) -> None:
    async with get_session() as session:
        det = await BotStoreService.get_listing_detail(session, callback_data.lid)
        lvl_cfg = await CardLevelService.get_config(session)
        rcfg = await SettingsService.get_rarity_config(session)

    if det is None or det[0].status != "active":
        await callback.answer("Bu karta allaqachon sotilgan yoki olib tashlangan.", show_alert=True)
        await _show_buy_list(callback, page=max(1, callback_data.p))
        return

    listing, card, seller = det
    lv = max(1, int(listing.card_level or 1))
    power = CardLevelService.power(card.rarity, lv, lvl_cfg)
    l_emoji, l_name = CardLevelService.title(lv)
    rank_val = CardLevelService.rank_value_for_card(card, rcfg)
    src_txt = f"👤 User sotgan ({esc(seller.display_name)})" if seller else "🏛 Bot Do‘koni"
    caption = format_v25_card_caption(
        card,
        level=lv,
        max_level=int(lvl_cfg.get("max_level", 10)),
        power=power,
        level_title_emoji=l_emoji,
        level_title_name=l_name,
        acquired_price_coins=int(listing.price_coins),
        rank_price_coins=rank_val,
        owner_name=src_txt,
        status_line=f"🏛 Bot Do‘koni narxi: <b>{fmt_coins(listing.price_coins)} 🪙</b>",
    )
    if callback.message:
        await safe_delete(callback.bot, callback.message.chat.id, callback.message.message_id)
        await send_card_message(
            callback.bot,
            callback.message.chat.id,
            card,
            caption,
            reply_markup=bot_store_detail_kb(callback.from_user.id, listing, page=max(1, callback_data.p)),
        )
    await callback.answer()


@router.callback_query(BotStoreCb.filter(F.act == "buy_go"))
async def cb_bot_store_buy_go(callback: CallbackQuery, callback_data: BotStoreCb, db_user: User) -> None:
    async with get_session() as session:
        try:
            res = await BotStoreService.buy_listing(session, callback_data.lid, db_user.id)
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
            logger.exception("BotStore purchase error: %s", exc)
            await callback.answer("❌ Xarid vaqtida xatolik yuz berdi.", show_alert=True)
            return

    if not res.success:
        await callback.answer(f"❌ {res.message}", show_alert=True)
        await _show_buy_list(callback, page=max(1, callback_data.p))
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
        f"🏛 <b>BOT DO‘KONIDAN XARID!</b>\n\n"
        f"🎴 <b>{esc(res.card.character_name)}</b> kolleksiyangizga qo‘shildi!\n"
        f"💰 To‘landi: <b>{fmt_coins(res.price_paid)} 🪙</b>{extra}",
        reply_markup=back_home_kb(callback.from_user.id, "savdo"),
    )


@router.callback_query(BotStoreCb.filter(F.act == "sell_list"))
async def cb_bot_store_sell_list(callback: CallbackQuery, callback_data: BotStoreCb, db_user: User) -> None:
    await _show_sell_list(callback, db_user, page=max(1, callback_data.p))


@router.callback_query(BotStoreCb.filter(F.act == "sell_det"))
async def cb_bot_store_sell_detail(callback: CallbackQuery, callback_data: BotStoreCb, db_user: User) -> None:
    async with get_session() as session:
        ok, msg, q = await MarketService.get_bot_sell_quote(session, db_user.id, callback_data.lid)
    if not ok or q is None:
        await callback.answer(msg, show_alert=True)
        await _show_sell_list(callback, db_user, page=max(1, callback_data.p))
        return
    meta = get_rarity_meta(q.card.rarity)
    warn = "\n⚠️ <i>Diqqat: bu karta sevimli/vitrinada belgilangan!</i>" if (q.is_favorite or q.is_showcased) else ""
    bonus_line = (
        f"\n⭐ Daraja bonusi (Lvl {q.level}, +{q.bonus_pct}%/lvl): <b>+{fmt_coins(q.bonus_coins)} 🪙</b>"
        if q.bonus_coins > 0
        else (f"\n⭐ Daraja: <b>{q.level}</b>" if q.level > 1 else "")
    )
    disc_line = (
        f"\n📉 Bot chegirmasi ({q.discount_pct}%): <b>-{fmt_coins(q.discount_coins)} 🪙</b>"
        if q.discount_coins > 0
        else ""
    )
    txt = (
        f"{header('🤖 <b>BOTGA SOTISHNI TASDIQLANG</b>')}\n"
        f"{meta.emoji} <b>{esc(q.card.character_name)}</b> ({esc(q.card.anime_name)})\n"
        f"🏷 Rank: <b>{esc(meta.name)}</b> · 🆔 <code>{esc(q.card.card_code)}</code>\n"
        f"{LINE}\n"
        f"💎 Rank narxi: <b>{fmt_coins(q.base_rank_value)} 🪙</b>"
        f"{bonus_line}"
        f"{disc_line}\n"
        f"💰 <b>Jami to‘lov: {fmt_coins(q.total_price)} 🪙</b>"
        f"{warn}\n\n"
        "<i>Sotilgach karta Bot Do‘koniga o‘tadi va coin darhol hisobingizga tushadi.</i>"
    )
    await edit_or_answer(
        callback,
        txt,
        reply_markup=bot_store_sell_confirm_kb(
            callback.from_user.id, q.user_card_id, q.total_price, page=max(1, callback_data.p)
        ),
    )


@router.callback_query(BotStoreCb.filter(F.act == "sell_go"))
async def cb_bot_store_sell_go(callback: CallbackQuery, callback_data: BotStoreCb, db_user: User) -> None:
    async with get_session() as session:
        res = await MarketService.sell_to_bot(session, db_user.id, callback_data.lid)
        if res.success:
            await session.commit()
        else:
            await session.rollback()
    if not res.success:
        await callback.answer(f"❌ {res.message}", show_alert=True)
        await _show_sell_list(callback, db_user, page=max(1, callback_data.p))
        return
    await callback.answer(f"✅ +{res.price_paid:,} 🪙!", show_alert=True)
    await edit_or_answer(
        callback,
        f"🤖 <b>KARTA BOTGA SOTILDI!</b>\n\n"
        f"🎴 <b>{esc(res.card.character_name)}</b> (Lvl {res.level_before})\n"
        f"💰 Hisobingizga tushdi: <b>+{fmt_coins(res.price_paid)} 🪙</b>",
        reply_markup=back_home_kb(callback.from_user.id, "savdo"),
    )
