import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultCachedPhoto,
    InlineQueryResultPhoto,
    InputTextMessageContent,
    Message,
)

from database.session import get_session
from keyboards.main_menu import back_home_kb
from keyboards.market import (
    search_catalog_inspect_kb,
    search_catalog_results_kb,
    search_inspect_kb,
    search_menu_kb,
    search_rarity_kb,
    search_results_kb,
)
from models.user import User
from services.auction_wishlist_service import AuctionWishlistService
from services.card_level_service import CardLevelService
from services.gameplay_service import GameplayService
from services.search_service import SearchService
from services.settings_service import SettingsService
from utils.callback_data import NavCb, SearchCb
from utils.constants import PAGE_SIZE, get_rarity_meta, sync_runtime_ranks
from utils.formatting import esc, event_tag, fmt_coins, format_v25_card_caption, header
from utils.tg_helpers import NotCommandFilter, edit_or_answer, safe_delete, send_card_message

logger = logging.getLogger(__name__)
router = Router(name="search")


class SearchStates(StatesGroup):
    waiting_query = State()


async def _render_results(
    target,
    owner_id: int,
    mode: str,
    query: str,
    page: int,
) -> None:
    if mode == "unowned":
        async with get_session() as session:
            catalog_cards, catalog_total = await SearchService.search_catalog_cards(
                session=session,
                mode="unowned",
                query=query,
                page=page,
                page_size=PAGE_SIZE,
                only_unowned=True,
            )
        if not catalog_cards:
            await edit_or_answer(
                target,
                f"{header('📖 <b>HALI HECH KIMDA YO‘Q KARTALAR</b>')}\n📭 Barcha mavjud kartalar allaqachon olingan.",
                reply_markup=search_catalog_results_kb(
                    owner_id=owner_id,
                    cards=[],
                    mode=mode,
                    query=query,
                    page=1,
                    total=0,
                    page_size=PAGE_SIZE,
                ),
            )
            return
        lines = [
            f"📖 <b>HALI HECH KIMDA YO‘Q KARTALAR</b> (Jami: {catalog_total})\n"
            "<i>Guruhlarda drop bo‘lishi mumkin bo‘lgan, hali egasi yo‘q kartalar:</i>\n"
        ]
        for card in catalog_cards:
            meta = get_rarity_meta(card.rarity)
            lines.append(
                f"🎴 {event_tag(card)}<b>{esc(card.character_name)}</b> — {meta.emoji} {esc(meta.name)}\n"
                f"📺 {esc(card.anime_name)} · 💰 {fmt_coins(card.base_value)} 🪙 · <code>{esc(card.card_code)}</code>\n"
            )
        await edit_or_answer(
            target,
            "\n".join(lines),
            reply_markup=search_catalog_results_kb(
                owner_id=owner_id,
                cards=catalog_cards,
                mode=mode,
                query=query,
                page=page,
                total=catalog_total,
                page_size=PAGE_SIZE,
            ),
        )
        return

    async with get_session() as session:
        items, total = await SearchService.search_global_cards(
            session=session,
            mode=mode,
            query=query,
            page=page,
            page_size=PAGE_SIZE,
        )
        catalog_cards = []
        catalog_total = 0
        if total == 0:
            catalog_cards, catalog_total = await SearchService.search_catalog_cards(
                session=session,
                mode=mode,
                query=query,
                page=page,
                page_size=PAGE_SIZE,
            )

    if total > 0:
        lines = [header("🔍 <b>QIDIRUV NATIJALARI</b>"), f"📌 Topildi: <b>{total}</b> ta\n"]
        for item in items:
            meta = get_rarity_meta(item.card.rarity)
            status_line = (
                f"🟢 Sotuvda — <b>{fmt_coins(item.active_listing.price)} 🪙</b>"
                if item.active_listing is not None
                else "🔴 Sotuvda emas"
            )
            lines.append(
                f"🎴 {event_tag(item.card)}<b>{esc(item.card.character_name)}</b>\n"
                f"📺 {esc(item.card.anime_name)} · {meta.emoji} {esc(meta.name)}\n"
                f"👤 Egasi: {esc(item.owner.display_name)} · {status_line}\n"
            )
        await edit_or_answer(
            target,
            "\n".join(lines),
            reply_markup=search_results_kb(
                owner_id=owner_id,
                items=items,
                mode=mode,
                query=query,
                page=page,
                total=total,
                page_size=PAGE_SIZE,
            ),
        )
        return

    if catalog_cards:
        lines = [
            f"📖 <b>KATALOGDAN TOPILGAN KARTALAR</b> (Jami: {catalog_total})\n"
            "<i>Hali foydalanuvchilarda yo‘q, umumiy katalogdan topildi:</i>\n"
        ]
        for card in catalog_cards:
            meta = get_rarity_meta(card.rarity)
            lines.append(
                f"🎴 {event_tag(card)}<b>{esc(card.character_name)}</b> — {meta.emoji} {esc(meta.name)}\n"
                f"📺 {esc(card.anime_name)} · 💰 {fmt_coins(card.base_value)} 🪙 · <code>{esc(card.card_code)}</code>\n"
            )
        await edit_or_answer(
            target,
            "\n".join(lines),
            reply_markup=search_catalog_results_kb(
                owner_id=owner_id,
                cards=catalog_cards,
                mode=mode,
                query=query,
                page=page,
                total=catalog_total,
                page_size=PAGE_SIZE,
            ),
        )
        return

    text = f"{header('🔍 <b>QIDIRUV</b>')}\nSo‘rov: <code>{esc(query or mode)}</code>\n📭 Hech qanday karta topilmadi.\n\n💡 Ismni qisqartirib yoki boshqacha yozib ko‘ring."
    await edit_or_answer(
        target,
        text,
        reply_markup=search_results_kb(
            owner_id=owner_id,
            items=[],
            mode=mode,
            query=query,
            page=1,
            total=0,
            page_size=PAGE_SIZE,
        ),
    )


@router.message(Command("search"))
async def cmd_search(message: Message, state: FSMContext) -> None:
    await state.clear()
    parts = message.text.split(maxsplit=1) if message.text else []
    if len(parts) == 2 and parts[1].strip():
        q = parts[1].strip()
        await state.update_data(last_search_query=q, last_search_mode="any")
        await _render_results(message, message.from_user.id, "any", q, 1)
        return
    await message.answer(
        f"{header('🔍 <b>KARTA QIDIRUVI</b>')}\n"
        "Hamma foydalanuvchilar kartalari va umumiy katalog bo‘yicha qidiring.\n\n"
        "💡 Tez qidirish: <code>/search Gojo</code>",
        reply_markup=search_menu_kb(message.from_user.id),
    )


@router.callback_query(NavCb.filter(F.target == "search"))
@router.callback_query(SearchCb.filter(F.action == "menu"))
async def cb_search_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_or_answer(
        callback,
        f"{header('🔍 <b>KARTA QIDIRUVI</b>')}\nQanday qidiramiz?",
        reply_markup=search_menu_kb(callback.from_user.id),
    )


@router.callback_query(SearchCb.filter(F.action == "mode"))
async def cb_search_mode(
    callback: CallbackQuery,
    callback_data: SearchCb,
    state: FSMContext,
) -> None:
    if callback_data.mode == "unowned":
        await state.clear()
        await _render_results(callback, callback.from_user.id, "unowned", "", 1)
        return
    if callback_data.mode == "rarity":
        async with get_session() as session:
            sync_runtime_ranks(await GameplayService.get_rank_definitions(session))
        await edit_or_answer(
            callback,
            f"{header('⭐ <b>RANK BO‘YICHA QIDIRUV</b>')}\nRankni tanlang:",
            reply_markup=search_rarity_kb(callback.from_user.id),
        )
        return

    prompts = {
        "character": "🔍 Qahramon ismini yozing (masalan: <code>Gojo</code>):",
        "anime": "📺 Anime/Media nomini yozing (masalan: <code>Jujutsu Kaisen</code>):",
        "card_id": "🎴 Card ID yoki nusxa ID yozing (masalan: <code>CRD-0001</code> yoki <code>12</code>):",
    }
    await state.set_state(SearchStates.waiting_query)
    await state.update_data(search_mode=callback_data.mode, owner_tg_id=callback.from_user.id)
    await edit_or_answer(
        callback,
        prompts.get(callback_data.mode, "Qidiruv so‘zini yozing:") + "\n<i>Bekor qilish: /cancel</i>",
        reply_markup=back_home_kb(callback.from_user.id, "search"),
    )


@router.message(SearchStates.waiting_query, NotCommandFilter())
async def fsm_search_query(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    query = (message.text or "").strip()
    if not query:
        await message.answer("⚠️ Qidiruv so‘zini yozing.")
        return
    mode = data.get("search_mode", "character")
    await state.set_state(None)
    await state.update_data(last_search_query=query, last_search_mode=mode)
    await _render_results(message, message.from_user.id, mode, query, 1)


@router.callback_query(SearchCb.filter(F.action.in_({"results", "random"})))
async def cb_search_results(
    callback: CallbackQuery,
    callback_data: SearchCb,
    state: FSMContext,
) -> None:
    query = callback_data.query
    if callback_data.mode not in ("random", "rarity", "unowned"):
        data = await state.get_data()
        saved_q = data.get("last_search_query", "")
        if saved_q and (not query or saved_q.startswith(query)):
            query = saved_q
    await _render_results(
        callback,
        owner_id=callback.from_user.id,
        mode=callback_data.mode,
        query=query,
        page=callback_data.page,
    )


@router.callback_query(SearchCb.filter(F.action == "inspect"))
async def cb_search_inspect(
    callback: CallbackQuery,
    callback_data: SearchCb,
    db_user: User,
) -> None:
    async with get_session() as session:
        item = await SearchService.inspect_user_card(session, callback_data.user_card_id)
        lvl_cfg = await CardLevelService.get_config(session)
        rcfg = await SettingsService.get_rarity_config(session)

    if item is None:
        await callback.answer("Karta topilmadi.", show_alert=True)
        return

    lv = int(getattr(item.user_card, "level", 1) or 1)
    power = CardLevelService.power(item.card.rarity, lv, lvl_cfg)
    l_emoji, l_name = CardLevelService.title(lv)
    rank_val = CardLevelService.rank_value_for_card(item.card, rcfg)
    status = (
        f"🟢 Sotuvda — <b>{fmt_coins(item.active_listing.price)} 🪙</b>"
        if item.active_listing is not None
        else "🔒 Sotuvda emas"
    )
    caption = format_v25_card_caption(
        item.card,
        level=lv,
        max_level=int(lvl_cfg.get("max_level", 10)),
        power=power,
        level_title_emoji=l_emoji,
        level_title_name=l_name,
        acquired_price_coins=int(getattr(item.user_card, "acquired_price_coins", 0) or 0),
        acquired_price_stars=int(getattr(item.user_card, "acquired_price_stars", 0) or 0),
        rank_price_coins=rank_val,
        single_id=f"{item.card.card_code} · #{item.user_card.id}",
        owner_name=item.owner.display_name,
        status_line=f"📌 Holati: {status}",
    )
    kb = search_inspect_kb(
        owner_id=callback.from_user.id,
        item=item,
        is_self_owner=(item.owner.id == db_user.id),
        mode=callback_data.mode,
        query=callback_data.query,
        page=callback_data.page,
    )
    if callback.message:
        await safe_delete(callback.bot, callback.message.chat.id, callback.message.message_id)
        await send_card_message(callback.bot, callback.message.chat.id, item.card, caption, reply_markup=kb)
    await callback.answer()


@router.callback_query(SearchCb.filter(F.action == "cat"))
async def cb_search_catalog_inspect(
    callback: CallbackQuery,
    callback_data: SearchCb,
    db_user: User,
) -> None:
    async with get_session() as session:
        card = await SearchService.inspect_catalog_card(session, callback_data.user_card_id)
        if card is None:
            await callback.answer("Karta topilmadi.", show_alert=True)
            return
        w = await AuctionWishlistService.is_wishlisted(session, db_user.id, card.id)
        lvl_cfg = await CardLevelService.get_config(session)
        rcfg = await SettingsService.get_rarity_config(session)

    extra = (
        "🎯 Limitli event kartasi"
        if card.is_event_card
        else f"🔁 Guruhlarga tashlanishi: <b>{card.dropped_count}/{card.drop_limit}</b>"
    )
    rank_val = CardLevelService.rank_value_for_card(card, rcfg)
    power = CardLevelService.power(card.rarity, 1, lvl_cfg)
    l_emoji, l_name = CardLevelService.title(1)
    caption = format_v25_card_caption(
        card,
        level=1,
        max_level=int(lvl_cfg.get("max_level", 10)),
        power=power,
        level_title_emoji=l_emoji,
        level_title_name=l_name,
        rank_price_coins=rank_val,
        owner_name="Katalog (hali olinmagan)" if card.dropped_count == 0 else "Umumiy katalog",
        status_line=f"📖 {extra}",
    )
    kb = search_catalog_inspect_kb(
        owner_id=callback.from_user.id,
        mode=callback_data.mode,
        query=callback_data.query,
        page=callback_data.page,
        card_id=card.id,
        notify_on=bool(w and w.notify_available),
    )
    if callback.message:
        await safe_delete(callback.bot, callback.message.chat.id, callback.message.message_id)
        await send_card_message(callback.bot, callback.message.chat.id, card, caption, reply_markup=kb)
    await callback.answer()


@router.inline_query()
async def inline_card_search(inline_query: InlineQuery) -> None:
    """Inline qidiruv: @botusername <so'rov>."""
    q = (inline_query.query or "").strip()
    try:
        async with get_session() as session:
            lvl_cfg = await CardLevelService.get_config(session)
            rcfg = await SettingsService.get_rarity_config(session)
            cards, _total = await SearchService.search_catalog_cards(
                session=session,
                mode="any" if q else "random",
                query=q,
                page=1,
                page_size=15,
            )
    except Exception as exc:
        logger.debug("Inline query error: %s", exc)
        await inline_query.answer([], cache_time=5)
        return

    results = []
    for card in cards:
        meta = get_rarity_meta(card.rarity)
        rank_val = CardLevelService.rank_value_for_card(card, rcfg)
        power = CardLevelService.power(card.rarity, 1, lvl_cfg)
        l_emoji, l_name = CardLevelService.title(1)
        caption = format_v25_card_caption(
            card,
            level=1,
            max_level=int(lvl_cfg.get("max_level", 10)),
            power=power,
            level_title_emoji=l_emoji,
            level_title_name=l_name,
            rank_price_coins=rank_val,
            owner_name="Karta katalogi",
            status_line=f"🆔 {card.card_code}",
        )
        fid = (getattr(card, "image_file_id", None) or "").strip()
        img = (card.image or "").strip()
        if fid:
            results.append(
                InlineQueryResultCachedPhoto(
                    id=f"card_{card.id}",
                    photo_file_id=fid,
                    title=f"{meta.emoji} {card.character_name}",
                    description=f"{card.anime_name} · {meta.name} · {rank_val:,} 🪙",
                    caption=caption,
                )
            )
        elif img.startswith("http://") or img.startswith("https://"):
            results.append(
                InlineQueryResultPhoto(
                    id=f"card_{card.id}",
                    photo_url=img,
                    thumbnail_url=img,
                    title=f"{meta.emoji} {card.character_name}",
                    description=f"{card.anime_name} · {meta.name} · {rank_val:,} 🪙",
                    caption=caption,
                )
            )
        else:
            results.append(
                InlineQueryResultArticle(
                    id=f"card_{card.id}",
                    title=f"{meta.emoji} {card.character_name} ({meta.name})",
                    description=f"{card.anime_name} · {rank_val:,} 🪙",
                    input_message_content=InputTextMessageContent(message_text=caption),
                )
            )

    await inline_query.answer(results, cache_time=10, is_personal=False)
