from typing import List

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, LabeledPrice, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config.settings import get_settings
from database.session import get_session
from keyboards.collection import (
    CARDS_PAGE_SIZE,
    anime_characters_kb,
    anime_sections_kb,
    card_detail_kb,
    character_variants_kb,
    collection_list_kb,
    collection_my_search_results_kb,
    collection_rarity_filter_kb,
    collection_search_hub_kb,
    collection_summary_kb,
    v2_collection_list_kb,
)
from keyboards.main_menu import back_home_kb
from models.card import Card
from models.user import User
from models.user_card import UserCard
from services.card_level_service import CardLevelService
from services.collection_service import CollectionService
from services.gameplay_service import GameplayService
from services.market_service import MarketService
from services.settings_service import SettingsService
from utils.callback_data import CollectionCb, NavCb
from utils.constants import (
    MAX_CARD_PRICE,
    MIN_CARD_PRICE,
    get_rank_name_by_order,
    get_rarity_meta,
    sync_runtime_ranks,
)
from utils.formatting import (
    esc,
    event_tag,
    fmt_coins,
    format_card_about,
    format_collection_summary,
    format_v25_card_caption,
)
from utils.tg_helpers import NotCommandFilter, edit_or_answer, is_private_chat

router = Router(name="collection")

ANIME_PAGE_SIZE = CARDS_PAGE_SIZE
CHAR_PAGE_SIZE = CARDS_PAGE_SIZE


class SellCardStates(StatesGroup):
    waiting_price = State()
    waiting_my_search = State()


def _ints(value: str, count: int) -> List[int]:
    """'3-2' -> [3, 2]; noto'g'ri/yetishmagan qiymatlar 1 bilan to'ldiriladi."""
    out: List[int] = []
    for part in (value or "").split("-")[:count]:
        out.append(int(part) if part.isdigit() and int(part) > 0 else 1)
    while len(out) < count:
        out.append(1)
    return out


async def _show_summary(target, owner_id: int, db_user: User) -> None:
    async with get_session() as session:
        sync_runtime_ranks(await GameplayService.get_rank_definitions(session))
        fresh = await session.get(User, db_user.id) or db_user
        anime_counts, rarity_counts, favorites = await CollectionService.get_summary(
            session, db_user.id
        )
    text = format_collection_summary(fresh, anime_counts, rarity_counts, favorites)
    await edit_or_answer(target, text, reply_markup=collection_summary_kb(owner_id))


@router.message(Command("cards", "collection"))
async def cmd_collection(message: Message, db_user: User) -> None:
    await _show_summary(message, message.from_user.id, db_user)


@router.callback_query(NavCb.filter(F.target == "cards"))
@router.callback_query(CollectionCb.filter(F.action == "view"))
async def cb_collection_summary(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    await state.clear()
    await _show_summary(callback, callback.from_user.id, db_user)


async def _render_v2_collection_list(
    target, owner_id: int, db_user: User, filter_tab: str, sort_by: str, page: int
) -> None:
    page = max(1, page)
    async with get_session() as session:
        rows, total = await CollectionService.get_filtered_user_cards(
            session, db_user.id, filter_tab=filter_tab, sort_by=sort_by, page=page, page_size=CARDS_PAGE_SIZE
        )
        if not rows and total > 0 and page > 1:
            page = 1
            rows, total = await CollectionService.get_filtered_user_cards(
                session, db_user.id, filter_tab=filter_tab, sort_by=sort_by, page=1, page_size=CARDS_PAGE_SIZE
            )
    tab_names = {
        "all": "Hammasi",
        "anime": "Anime",
        "manga": "Manga",
        "movie": "Kino",
        "cartoon": "Multfilm",
        "event": "Event",
        "limited": "Limitli",
        "favorites": "Sevimlilar",
    }
    pages = max(1, (total + CARDS_PAGE_SIZE - 1) // CARDS_PAGE_SIZE)
    lines = [
        f"🗂 <b>KARTALARIM · {tab_names.get(filter_tab, filter_tab.title())}</b>",
        f"━━━━━━━━━━━━━━━\n📦 Jami: <b>{total}</b> ta  ·  📄 Sahifa: <b>{page}/{pages}</b>\n",
    ]
    if not rows:
        lines.append("📭 Bu bo‘limda kartalar topilmadi.")
    else:
        lines.append("👇 Kartani tanlang (❤️ sevimli · 🖼 vitrinada):")
    await edit_or_answer(
        target,
        "\n".join(lines),
        reply_markup=v2_collection_list_kb(
            owner_id, rows, filter_tab, sort_by, page, total, page_size=CARDS_PAGE_SIZE
        ),
    )


@router.callback_query(CollectionCb.filter(F.action == "v2_list"))
async def cb_v2_collection_list(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    await _render_v2_collection_list(
        callback,
        callback.from_user.id,
        db_user,
        callback_data.filter_val or "all",
        callback_data.sort_by or "newest",
        max(1, callback_data.page),
    )


# ---------------------------------------------------------------------------
# Qidiruv (Kartalarim ichida: O'zimni kartalarim / Boshqa kartalar / Tasodifiy)
# ---------------------------------------------------------------------------
@router.callback_query(CollectionCb.filter(F.action == "search"))
async def cb_collection_search_hub(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_or_answer(
        callback,
        "🔍 <b>KARTA QIDIRUVI</b>\n━━━━━━━━━━━━━━━\n"
        "Qayerdan qidiramiz?\n\n"
        "🎴 <b>O‘zimni kartalarim</b> — faqat sizning kolleksiyangizdan\n"
        "🌐 <b>Boshqa kartalar</b> — hamma foydalanuvchilar va katalog\n"
        "🎲 <b>Tasodifiy kartalar</b> — tasodifiy kashfiyot",
        reply_markup=collection_search_hub_kb(callback.from_user.id),
    )


@router.callback_query(CollectionCb.filter(F.action == "my_search"))
async def cb_my_search_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SellCardStates.waiting_my_search)
    await state.update_data(owner_tg_id=callback.from_user.id)
    await edit_or_answer(
        callback,
        "🔍 <b>O‘ZIMNI KARTALARIMDAN QIDIRISH</b>\n"
        "━━━━━━━━━━━━━━━\n"
        "Anime/Manga/Manhwa/Kino nomi, personaj ismi, rank yoki karta ID raqamini yozing (masalan: <code>Gojo</code>, <code>Naruto</code>, <code>Epic</code>, <code>5</code>):\n"
        "<i>Bekor qilish: /cancel</i>",
        reply_markup=back_home_kb(callback.from_user.id, "cards"),
    )


async def _render_my_search_results(target, owner_id: int, db_user_id: int, query: str, page: int) -> None:
    async with get_session() as session:
        rows, total = await CollectionService.search_user_cards(
            session, db_user_id, query, page=page, page_size=CARDS_PAGE_SIZE
        )
    if not rows:
        await edit_or_answer(
            target,
            f"🔍 So‘rov: <code>{esc(query)}</code>\n📭 Sizning kartalaringiz orasidan hech narsa topilmadi.",
            reply_markup=collection_my_search_results_kb(owner_id, [], 1, 0, CARDS_PAGE_SIZE),
        )
        return
    pages = max(1, (total + CARDS_PAGE_SIZE - 1) // CARDS_PAGE_SIZE)
    lines = [
        f"🔍 <b>QIDIRUV NATIJALARI</b> (<code>{esc(query)}</code>)",
        f"📦 Topildi: <b>{total}</b> ta  ·  📄 Sahifa: <b>{page}/{pages}</b>\n",
        "👇 Kartani tanlang:",
    ]
    await edit_or_answer(
        target,
        "\n".join(lines),
        reply_markup=collection_my_search_results_kb(owner_id, rows, page, total, CARDS_PAGE_SIZE),
    )


@router.message(SellCardStates.waiting_my_search, NotCommandFilter())
async def fsm_my_search_query(message: Message, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    q = (message.text or "").strip()
    if not q:
        await message.answer("⚠️ Qidiruv so‘zini yozing.")
        return
    await state.set_state(None)
    await state.update_data(my_search_query=q)
    await _render_my_search_results(message, message.from_user.id, db_user.id, q, 1)


@router.callback_query(CollectionCb.filter(F.action == "my_search_p"))
async def cb_my_search_page(callback: CallbackQuery, callback_data: CollectionCb, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    q = str(data.get("my_search_query") or "").strip()
    if not q:
        await cb_my_search_start(callback, state)
        return
    await _render_my_search_results(callback, callback.from_user.id, db_user.id, q, max(1, callback_data.page))


# ---------------------------------------------------------------------------
# 1-daraja: ANIME BO'LIMLARI (5 tadan sahifa)
# ---------------------------------------------------------------------------
async def _render_anime_list(target, owner_id: int, db_user: User, page: int) -> None:
    page = max(1, page)
    async with get_session() as session:
        sections, total = await CollectionService.get_anime_sections(
            session, db_user.id, page=page, page_size=ANIME_PAGE_SIZE
        )
        if not sections and total > 0:
            page = 1
            sections, total = await CollectionService.get_anime_sections(
                session, db_user.id, page=1, page_size=ANIME_PAGE_SIZE
            )

    if total == 0:
        await edit_or_answer(
            target,
            "📭 Sizda hali kartalar yo‘q. Guruhdagi dropni kuting yoki marketdan sotib oling!",
            reply_markup=back_home_kb(owner_id, "cards"),
        )
        return

    lines = [f"📺 <b>ANIME BO‘LIMLARI</b> (Jami: {total} ta asar)\n"]
    for sec in sections:
        lines.append(f"• <b>{esc(sec.anime_name)}</b> — {sec.characters} personaj · <b>{sec.copies}</b> ta karta")
    await edit_or_answer(
        target,
        "\n".join(lines),
        reply_markup=anime_sections_kb(owner_id, sections, page, total, ANIME_PAGE_SIZE),
    )


@router.callback_query(CollectionCb.filter(F.action.in_({"anime_list", "by_anime"})))
async def cb_anime_list(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    await _render_anime_list(callback, callback.from_user.id, db_user, max(1, callback_data.page))


# ---------------------------------------------------------------------------
# 2-daraja: anime ichidagi PERSONAJLAR (5 tadan sahifa)
# ---------------------------------------------------------------------------
async def _render_anime_open(
    target, owner_id: int, db_user: User, item_id: int, anime_page: int, page: int
) -> None:
    page = max(1, page)
    async with get_session() as session:
        info = await CollectionService.get_anime_characters(
            session, db_user.id, item_id, page=page, page_size=CHAR_PAGE_SIZE
        )
        if info is not None and not info[1]:
            page = 1
            info = await CollectionService.get_anime_characters(
                session, db_user.id, item_id, page=1, page_size=CHAR_PAGE_SIZE
            )

    if info is None:
        if isinstance(target, CallbackQuery):
            await target.answer("Bu bo‘limda kartalaringiz qolmagan.", show_alert=True)
        await _render_anime_list(target, owner_id, db_user, anime_page)
        return

    anime_name, chars, total = info
    lines = [f"📺 <b>{esc(anime_name)}</b> — personajlar (Jami: {total})\n"]
    for ch in chars:
        fav = ("❤️ " if ch.is_favorite else "") + ("🎯 " if ch.has_event_card else "")
        note = f" ({ch.variants} xil karta)" if ch.variants > 1 else ""
        lines.append(f"{fav}🎴 <b>{esc(ch.character_name)}</b> — <b>{ch.copies} ta</b>{note}")
    await edit_or_answer(
        target,
        "\n".join(lines),
        reply_markup=anime_characters_kb(
            owner_id, item_id, anime_page, chars, page, total, CHAR_PAGE_SIZE
        ),
    )


@router.callback_query(CollectionCb.filter(F.action == "anime_open"))
async def cb_anime_open(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    anime_page = _ints(callback_data.filter_val, 1)[0]
    await _render_anime_open(
        callback, callback.from_user.id, db_user, callback_data.item_id, anime_page, max(1, callback_data.page)
    )


# ---------------------------------------------------------------------------
# 3-daraja: personajning kartalari
# ---------------------------------------------------------------------------
@router.callback_query(CollectionCb.filter(F.action == "char_open"))
async def cb_char_open(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    char_page, anime_page = _ints(callback_data.filter_val, 2)
    async with get_session() as session:
        info = await CollectionService.get_character_variants(session, db_user.id, callback_data.item_id)

    if info is None:
        await callback.answer("Bu personaj kartalari sizda qolmagan.", show_alert=True)
        await cb_anime_list(
            callback, CollectionCb(owner_id=callback.from_user.id, action="anime_list", page=anime_page), db_user
        )
        return

    character_name, anime_name, variants, anime_rep_id = info
    total_copies = sum(v.quantity for v in variants)
    lines = [
        f"🎴 <b>{esc(character_name)}</b>",
        f"📺 {esc(anime_name)}",
        "",
        f"📦 Jami: <b>{total_copies}</b> ta nusxa · {len(variants)} xil karta\n",
    ]
    for v in variants:
        meta = get_rarity_meta(v.card.rarity)
        tag = "  🎯 <i>EVENT · cheklangan nusxa</i>" if v.card.is_event_card else ""
        lines.append(
            f"{event_tag(v.card)}{meta.emoji} {esc(meta.name)} · <code>{esc(v.card.card_code)}</code> × {v.quantity}{tag}"
        )
    await edit_or_answer(
        callback,
        "\n".join(lines),
        reply_markup=character_variants_kb(
            callback.from_user.id, variants, anime_rep_id, char_page, anime_page, callback_data.item_id
        ),
    )


# ---------------------------------------------------------------------------
# Rarity bo'yicha tekis ro'yxat (5 tadan sahifa)
# ---------------------------------------------------------------------------
@router.callback_query(CollectionCb.filter(F.action == "by_rarity"))
async def cb_by_rarity(callback: CallbackQuery) -> None:
    async with get_session() as session:
        sync_runtime_ranks(await GameplayService.get_rank_definitions(session))
    await edit_or_answer(
        callback,
        "⭐ <b>Rank bo‘yicha filtrlash:</b>",
        reply_markup=collection_rarity_filter_kb(callback.from_user.id),
    )


async def _render_char_list(
    target, owner_id: int, db_user: User, filter_val: str, page: int
) -> None:
    page = max(1, page)
    rarity_filter = None
    if filter_val.startswith("rar_"):
        rarity_filter = filter_val[4:]

    async with get_session() as session:
        stacks, total = await CollectionService.get_character_stacks(
            session=session,
            user_id=db_user.id,
            rarity_filter=rarity_filter,
            page=page,
            page_size=CARDS_PAGE_SIZE,
        )

    if not stacks:
        text = "📭 Ushbu bo‘limda kartalar topilmadi."
    else:
        lines = [f"🎴 <b>KOLLEKSIYA RO‘YXATI</b> (Jami: {total} xil karta)\n"]
        for st in stacks:
            fav = "❤️ " if st.is_favorite else ""
            lines.append(
                f"{fav}• {event_tag(st.card)}<b>{esc(st.card.character_name)}</b> × {st.quantity} ({esc(st.card.anime_name)})"
            )
        text = "\n".join(lines)

    await edit_or_answer(
        target,
        text,
        reply_markup=collection_list_kb(
            owner_id=owner_id,
            stacks=stacks,
            filter_val=filter_val,
            page=page,
            total=total,
            page_size=CARDS_PAGE_SIZE,
        ),
    )


@router.callback_query(CollectionCb.filter(F.action == "char_list"))
async def cb_char_list(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    await _render_char_list(
        callback, callback.from_user.id, db_user, callback_data.filter_val, max(1, callback_data.page)
    )


# ---------------------------------------------------------------------------
# Karta tafsiloti (Bot 2.5 dizayni)
# ---------------------------------------------------------------------------
@router.callback_query(CollectionCb.filter(F.action == "card_detail"))
async def cb_card_detail(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    async with get_session() as session:
        await CollectionService.reconcile_user_card_locks(session, db_user.id)
        uc = await session.get(UserCard, callback_data.item_id)
        if uc is None or uc.user_id != db_user.id:
            await callback.answer("Karta topilmadi.", show_alert=True)
            return
        card = await session.get(Card, uc.card_id)
        unlocked_copy = await CollectionService.get_unlocked_copy(session, db_user.id, card.id)
        reps = await CollectionService.get_card_group_reps(session, card.id)
        lvl_cfg = await CardLevelService.get_config(session)
        rcfg = await SettingsService.get_rarity_config(session)
        target_uc_id = unlocked_copy.id if unlocked_copy else uc.id
        is_locked = unlocked_copy is None
        lock_label = "🟢 Sotish/Transferga tayyor"
        if is_locked:
            _kind, _eid, lock_label = await CollectionService.get_card_lock_info(session, uc.id)
        await session.commit()

    char_rep_id = reps[1] if reps else card.id
    level = int(getattr(uc, "level", 1) or 1)
    max_level = int(lvl_cfg["max_level"])
    power = CardLevelService.power(card.rarity, level, lvl_cfg)
    rank_val = CardLevelService.rank_value_for_card(card, rcfg)
    t_emoji, t_name = CardLevelService.title(level)
    can_upgrade = level < max_level
    single_id = f"{card.card_code} · #{uc.serial_number}" if getattr(uc, "serial_number", None) else f"{card.card_code} · #{uc.id}"

    text = format_v25_card_caption(
        card,
        level=level,
        max_level=max_level,
        power=power,
        level_title_emoji=t_emoji,
        level_title_name=t_name,
        acquired_price_coins=int(getattr(uc, "acquired_price_coins", 0) or 0),
        acquired_price_stars=int(getattr(uc, "acquired_price_stars", 0) or 0),
        rank_price_coins=rank_val,
        single_id=single_id,
        owner_name=None,
        status_line=lock_label,
    )
    kb = card_detail_kb(
        owner_id=callback.from_user.id,
        user_card_id=target_uc_id,
        is_locked=is_locked,
        is_favorite=uc.is_favorite,
        char_rep_id=char_rep_id,
        card_id=card.id,
        is_showcased=bool(getattr(uc, "is_showcased", False)),
        can_upgrade=can_upgrade,
    )
    img_ref = getattr(card, "image_file_id", None) or card.image
    if callback.message and img_ref and (img_ref.startswith("http") or len(img_ref) > 20):
        try:
            await callback.message.delete()
            await callback.message.answer_photo(photo=img_ref, caption=text, reply_markup=kb)
            await callback.answer()
            return
        except Exception:
            pass
    await edit_or_answer(callback, text, reply_markup=kb)


@router.callback_query(CollectionCb.filter(F.action == "unlock_uc"))
async def cb_unlock_user_card(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    async with get_session() as session:
        ok, msg = await CollectionService.unlock_user_card_action(session, db_user.id, callback_data.item_id)
        if ok:
            await session.commit()
        else:
            await session.rollback()
    await callback.answer(msg[:190], show_alert=True)
    if ok:
        await cb_card_detail(
            callback,
            CollectionCb(owner_id=callback.from_user.id, action="card_detail", item_id=callback_data.item_id),
            db_user,
        )


# ---------------------------------------------------------------------------
# Birlashtirish (Bot 2.5: 3 ta tasodifiy mos karta, kuniga 1 marta)
# ---------------------------------------------------------------------------
async def _render_merge(callback: CallbackQuery, db_user: User) -> None:
    owner = callback.from_user.id
    async with get_session() as session:
        cfg = await CardLevelService.get_config(session)
        options = CardLevelService.merge_options(cfg)
        used_today = await CardLevelService.merge_used_today(session, db_user.id)
        daily_limit = int(cfg.get("merge_daily_limit", 1))
        rows = []
        for order, cost in options:
            have = await CardLevelService.count_spare(session, db_user.id, order)
            rows.append((order, cost, have))

    limit_line = (
        f"⏳ Bugungi limit: <b>{used_today}/{daily_limit}</b>"
        if daily_limit > 0
        else "♾ Bugungi limit: cheklovsiz"
    )
    lines = [
        "🔮 <b>BIRLASHTIRISH</b>",
        "━━━━━━━━━━━━━━━",
        "Bir xil rankdagi bo‘sh kartalaringizni topshirib, <b>keyingi rankdagi</b> tasodifiy karta oling.",
        f"{limit_line}",
        "<i>⚠️ Sevimli ❤️, Vitrina 🖼, darajasi 1 dan katta ⬆️ va savdoda band kartalar olinmaydi.</i>",
        "",
    ]
    b = InlineKeyboardBuilder()
    if not rows:
        lines.append("📭 Birlashtirish hozircha o‘chirilgan.")
    for order, cost, have in rows:
        from_name = get_rank_name_by_order(order)
        to_name = get_rank_name_by_order(order + 1)
        fm, tm = get_rarity_meta(from_name), get_rarity_meta(to_name)
        mark = "✅" if have >= cost else "🔒"
        lines.append(
            f"{mark} {fm.emoji} <b>{esc(from_name)}</b> × {cost}  →  {tm.emoji} <b>{esc(to_name)}</b>  (yaroqli: {have})"
        )
        b.row(
            InlineKeyboardButton(
                text=f"{fm.emoji} {from_name} × {cost} → {tm.emoji} {to_name}"[:60],
                callback_data=CollectionCb(owner_id=owner, action="merge_do", item_id=order).pack(),
            )
        )
    b.row(InlineKeyboardButton(text="⬅️ Kartalarim", callback_data=NavCb(owner_id=owner, target="cards").pack()))
    await edit_or_answer(callback, "\n".join(lines), reply_markup=b.as_markup())


@router.callback_query(CollectionCb.filter(F.action == "merge"))
async def cb_merge_home(callback: CallbackQuery, db_user: User) -> None:
    await _render_merge(callback, db_user)


@router.callback_query(CollectionCb.filter(F.action == "merge_do"))
async def cb_merge_do(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    async with get_session() as session:
        ok, msg, reward = await CardLevelService.merge(session, db_user.id, int(callback_data.item_id))
        if ok:
            await session.commit()
        else:
            await session.rollback()
    if not ok:
        await callback.answer(msg[:190], show_alert=True)
        return
    meta = get_rarity_meta(reward.rarity)
    await callback.answer("🎉 Birlashtirildi!")
    text = (
        "🔮 <b>BIRLASHTIRISH MUVAFFAQIYATLI!</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"🎴 <b>{esc(reward.character_name)}</b>\n"
        f"📺 {esc(reward.anime_name)} · {meta.emoji} {esc(meta.name)}\n\n"
        "✨ Karta kolleksiyangizga qo‘shildi! (+50 XP)"
    )
    kb = back_home_kb(callback.from_user.id, "cards")
    img_ref = getattr(reward, "image_file_id", None) or reward.image
    if callback.message and img_ref and (img_ref.startswith("http") or len(img_ref) > 20):
        try:
            await callback.message.delete()
            await callback.message.answer_photo(photo=img_ref, caption=text, reply_markup=kb)
            return
        except Exception:
            pass
    await edit_or_answer(callback, text, reply_markup=kb)


# ---------------------------------------------------------------------------
# Daraja ko'tarish (Coin / Stars + progressiv ramka & afzallik)
# ---------------------------------------------------------------------------
@router.callback_query(CollectionCb.filter(F.action == "lvl_ask"))
async def cb_level_ask(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    async with get_session() as session:
        uc = await session.get(UserCard, callback_data.item_id)
        if uc is None or uc.user_id != db_user.id:
            await callback.answer("Karta topilmadi.", show_alert=True)
            return
        card = await session.get(Card, uc.card_id)
        cfg = await CardLevelService.get_config(session)
        rcfg = await SettingsService.get_rarity_config(session)
        fresh = await session.get(User, db_user.id)
    level = int(uc.level or 1)
    max_level = int(cfg["max_level"])
    if level >= max_level:
        await callback.answer("👑 Karta allaqachon eng yuqori darajada.", show_alert=True)
        return
    card_val = CardLevelService.rank_value_for_card(card, rcfg)
    cost = CardLevelService.upgrade_cost(card_val, level, cfg)
    cur_power = CardLevelService.power(card.rarity, level, cfg)
    new_power = CardLevelService.power(card.rarity, level + 1, cfg)
    frame_name, perk_desc = CardLevelService.level_frame_and_perk(level + 1, max_level)
    stars_ok = get_settings().enable_telegram_stars and (level + 1) <= min(int(cfg["stars_max_level"]), max_level)
    stars_cost = CardLevelService.stars_price(level, cfg) if stars_ok else 0

    text = (
        "⬆️ <b>DARAJA KO‘TARISH</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🎴 <b>{esc(card.character_name)}</b>\n"
        f"⭐ Daraja: <b>{level}</b> → <b>{level + 1}</b> / {max_level} ({CardLevelService.title_text(level + 1)})\n"
        f"💪 Kuch: {cur_power:,} → <b>{new_power:,}</b>\n"
        f"🖼 Ramka: <b>{esc(frame_name)}</b>\n"
        f"✨ Afzallik: <i>{esc(perk_desc)}</i>\n\n"
        f"🪙 Coin narxi: <b>{cost:,} 🪙</b>\n"
        + (f"⭐ Stars narxi: <b>{stars_cost} ⭐</b>\n" if stars_ok else "")
        + f"👛 Balansingiz: <b>{int(fresh.coins):,} 🪙</b>"
    )
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text=f"🪙 Coin bilan ({cost:,})",
            callback_data=CollectionCb(owner_id=callback.from_user.id, action="lvl_do", filter_val="d", page=1, item_id=uc.id).pack(),
        )
    )
    if stars_ok:
        b.row(
            InlineKeyboardButton(
                text=f"⭐ Stars bilan ({stars_cost} ⭐)",
                callback_data=CollectionCb(owner_id=callback.from_user.id, action="lvl_stars", filter_val="d", page=1, item_id=uc.id).pack(),
            )
        )
    b.row(
        InlineKeyboardButton(
            text="⬅️ Karta",
            callback_data=CollectionCb(owner_id=callback.from_user.id, action="card_detail", filter_val="d", page=1, item_id=uc.id).pack(),
        )
    )
    try:
        if callback.message and callback.message.photo:
            await callback.message.edit_caption(caption=text, reply_markup=b.as_markup())
            await callback.answer()
            return
    except Exception:
        pass
    await edit_or_answer(callback, text, reply_markup=b.as_markup())


@router.callback_query(CollectionCb.filter(F.action == "lvl_stars"))
async def cb_level_stars(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    if not get_settings().enable_telegram_stars:
        await callback.answer("Telegram Stars vaqtincha o‘chirilgan.", show_alert=True)
        return
    async with get_session() as session:
        uc = await session.get(UserCard, callback_data.item_id)
        if uc is None or uc.user_id != db_user.id:
            await callback.answer("Karta topilmadi.", show_alert=True)
            return
        card = await session.get(Card, uc.card_id)
        cfg = await CardLevelService.get_config(session)
    level = int(uc.level or 1)
    target = level + 1
    if target > min(int(cfg["stars_max_level"]), int(cfg["max_level"])):
        await callback.answer("Bu darajaga Stars bilan ko‘tarib bo‘lmaydi. Coin yoki dublikat bilan ko‘taring.", show_alert=True)
        return
    price = CardLevelService.stars_price(level, cfg)
    try:
        await callback.bot.send_invoice(
            chat_id=callback.from_user.id,
            title=f"⬆️ {card.character_name}"[:32],
            description=f"Karta darajasi {level} → {target} ({CardLevelService.title_text(target)})"[:255],
            payload=f"lvl:{uc.id}:{target}:{callback.from_user.id}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=f"Daraja {target}", amount=price)],
        )
        await callback.answer("⭐ To‘lov oynasi shaxsiy chatga yuborildi.")
    except Exception:
        await callback.answer("Avval botga shaxsiy chatda /start bosing.", show_alert=True)


@router.callback_query(CollectionCb.filter(F.action == "lvl_do"))
async def cb_level_do(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    async with get_session() as session:
        res = await CardLevelService.upgrade_with_coins(session, db_user.id, callback_data.item_id)
        if res.success:
            await session.commit()
        else:
            await session.rollback()
    if not res.success:
        await callback.answer(res.message, show_alert=True)
        return
    await callback.answer(
        f"🎉 Daraja {res.new_level} ga ko‘tarildi! {CardLevelService.title_text(res.new_level)}", show_alert=True
    )
    await cb_card_detail(
        callback,
        CollectionCb(owner_id=callback.from_user.id, action="card_detail", filter_val="d", page=1, item_id=callback_data.item_id),
        db_user,
    )


@router.callback_query(CollectionCb.filter(F.action == "toggle_fav"))
async def cb_toggle_fav(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    async with get_session() as session:
        new_state = await CollectionService.toggle_favorite_for_card(
            session, db_user.id, callback_data.item_id
        )
        await session.commit()
    await callback.answer("❤️ Sevimlilarga qo‘shildi!" if new_state else "💔 Sevimlilardan olindi.")
    await cb_card_detail(callback, callback_data, db_user)


@router.callback_query(CollectionCb.filter(F.action == "toggle_show"))
async def cb_toggle_showcase(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    async with get_session() as session:
        uc = await session.get(UserCard, callback_data.item_id)
        if uc is None or uc.user_id != db_user.id:
            await callback.answer("Karta topilmadi.", show_alert=True)
            return
        uc.is_showcased = not bool(getattr(uc, "is_showcased", False))
        new_state = uc.is_showcased
        await session.commit()
    await callback.answer("🖼 Vitrinaga qo‘shildi!" if new_state else "🖼 Vitrinadan olindi.")
    await cb_card_detail(callback, callback_data, db_user)


@router.callback_query(CollectionCb.filter(F.action == "about"))
async def cb_card_about(callback: CallbackQuery, callback_data: CollectionCb, db_user: User) -> None:
    from services.card_service import CardService

    async with get_session() as session:
        uc = await session.get(UserCard, callback_data.item_id)
        if uc is None or uc.user_id != db_user.id:
            await callback.answer("Karta topilmadi.", show_alert=True)
            return
        card = await session.get(Card, uc.card_id)
        if card is None:
            await callback.answer("Karta topilmadi.", show_alert=True)
            return
        eff_meta, eff_aliases = await CardService.get_effective_about(session, card)
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text="⬅️ Kartaga qaytish",
            callback_data=CollectionCb(
                owner_id=callback.from_user.id, action="card_detail", filter_val="d", page=1, item_id=uc.id
            ).pack(),
        ),
        InlineKeyboardButton(
            text="🏠 Bosh Menu",
            callback_data=NavCb(owner_id=callback.from_user.id, target="home").pack(),
        ),
    )
    await edit_or_answer(
        callback,
        format_card_about(card, meta_override=eff_meta, aliases_override=eff_aliases),
        reply_markup=b.as_markup(),
    )


# ---------------------------------------------------------------------------
# Marketga qo'yish (narx so'rash)
# ---------------------------------------------------------------------------
@router.callback_query(CollectionCb.filter(F.action == "list_prompt"))
async def cb_list_prompt(
    callback: CallbackQuery,
    callback_data: CollectionCb,
    state: FSMContext,
    db_user: User,
) -> None:
    async with get_session() as session:
        uc = await session.get(UserCard, callback_data.item_id)
        if uc is None or uc.user_id != db_user.id or uc.is_locked:
            await callback.answer("Bu kartani hozir sotuvga qo‘yib bo‘lmaydi.", show_alert=True)
            return
        card = await session.get(Card, uc.card_id)

    await state.set_state(SellCardStates.waiting_price)
    await state.update_data(user_card_id=uc.id, owner_tg_id=callback.from_user.id)
    await edit_or_answer(
        callback,
        f"🛒 <b>{esc(card.character_name)}</b> kartasini marketga qo‘yish\n\n"
        f"💰 Tavsiya etilgan qiymat: {card.base_value:,} coins\n"
        f"Narxni kiriting ({MIN_CARD_PRICE:,} — {MAX_CARD_PRICE:,} coins).\n"
        f"<i>Bekor qilish: /cancel</i>",
        reply_markup=back_home_kb(callback.from_user.id, back_target="cards"),
    )


@router.message(SellCardStates.waiting_price, NotCommandFilter())
async def fsm_sell_price(message: Message, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return

    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit():
        if is_private_chat(message):
            await message.answer("⚠️ Iltimos, faqat musbat raqam (coins) kiriting. Bekor qilish: /cancel")
        return

    price = int(raw)
    user_card_id = int(data["user_card_id"])
    async with get_session() as session:
        res = await MarketService.list_card(session, db_user.id, user_card_id, price)
        if res.success:
            await session.commit()
        else:
            await session.rollback()

    await state.clear()
    if not res.success:
        await message.answer(f"❌ {res.message}", reply_markup=back_home_kb(message.from_user.id, "cards"))
        return

    await message.answer(
        f"✅ <b>{esc(res.card.character_name)}</b> marketga <b>{price:,} 🪙</b> narxda qo‘yildi!",
        reply_markup=back_home_kb(message.from_user.id, "market"),
    )


# ---------------------------------------------------------------------------
# Kartani to'g'ridan-to'g'ri botga sotish (karta tafsilotidan)
# ---------------------------------------------------------------------------
@router.callback_query(CollectionCb.filter(F.action == "bot_sell_ask"))
async def cb_collection_bot_sell_ask(
    callback: CallbackQuery,
    callback_data: CollectionCb,
    db_user: User,
) -> None:
    async with get_session() as session:
        ok, msg, q = await MarketService.get_bot_sell_quote(session, db_user.id, callback_data.item_id)
    if not ok or q is None:
        await callback.answer(msg, show_alert=True)
        return

    meta = get_rarity_meta(q.card.rarity)
    warn = "\n⚠️ <i>Diqqat: bu karta sevimli/vitrinada belgilangan!</i>" if (q.is_favorite or q.is_showcased) else ""
    bonus_line = (
        f"\n⭐ Daraja bonusi (Lvl {q.level}): <b>+{fmt_coins(q.bonus_coins)} 🪙</b>"
        if q.bonus_coins > 0
        else (f"\n⭐ Daraja: <b>{q.level}</b>" if q.level > 1 else "")
    )
    disc_line = (
        f"\n🏛 Bot chegirmasi (-{q.discount_pct}%): <b>-{fmt_coins(q.discount_coins)} 🪙</b>"
        if getattr(q, "discount_coins", 0) > 0
        else ""
    )
    text = (
        "🤖 <b>BOTGA SOTISH</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"{meta.emoji} <b>{esc(q.card.character_name)}</b> ({esc(q.card.anime_name)})\n"
        f"💎 Rank narxi: <b>{fmt_coins(q.base_rank_value)} 🪙</b>"
        f"{bonus_line}{disc_line}\n"
        f"💰 <b>Jami to‘lov: {fmt_coins(q.total_price)} 🪙</b>"
        f"{warn}\n\n"
        "<i>Karta Bot Do‘koniga joylanadi va coin darhol hisobingizga tushadi.</i>"
    )
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text=f"✅ Botga sotish (+{q.total_price:,} 🪙)",
            callback_data=CollectionCb(
                owner_id=callback.from_user.id, action="bot_sell_do", filter_val="d", page=1, item_id=q.user_card_id
            ).pack(),
        )
    )
    b.row(
        InlineKeyboardButton(
            text="❌ Bekor qilish",
            callback_data=CollectionCb(
                owner_id=callback.from_user.id, action="card_detail", filter_val="d", page=1, item_id=q.user_card_id
            ).pack(),
        )
    )
    try:
        if callback.message and callback.message.photo:
            await callback.message.edit_caption(caption=text, reply_markup=b.as_markup())
            await callback.answer()
            return
    except Exception:
        pass
    await edit_or_answer(callback, text, reply_markup=b.as_markup())


@router.callback_query(CollectionCb.filter(F.action == "bot_sell_do"))
async def cb_collection_bot_sell_do(
    callback: CallbackQuery,
    callback_data: CollectionCb,
    db_user: User,
) -> None:
    async with get_session() as session:
        res = await MarketService.sell_to_bot(session, db_user.id, callback_data.item_id)
        if res.success:
            await session.commit()
        else:
            await session.rollback()
    if not res.success:
        await callback.answer(f"❌ {res.message}", show_alert=True)
        return
    await callback.answer(f"✅ +{res.price_paid:,} 🪙!", show_alert=True)
    await edit_or_answer(
        callback,
        f"🤖 <b>KARTA BOTGA SOTILDI!</b>\n\n"
        f"🎴 <b>{esc(res.card.character_name)}</b> (Lvl {res.level_before})\n"
        f"💰 Hisobingizga tushdi: <b>+{fmt_coins(res.price_paid)} 🪙</b>",
        reply_markup=back_home_kb(callback.from_user.id, "cards"),
    )
