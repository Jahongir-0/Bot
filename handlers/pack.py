from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database.session import get_session
from keyboards.v2 import packs_menu_kb
from models.user import User
from services.gameplay_service import GameplayService
from services.pack_service import PackService
from utils.callback_data import NavCb, PackCb
from utils.constants import get_pack_configs, get_rarity_meta, sync_runtime_ranks
from utils.formatting import esc, fmt_coins, format_card_block, header
from utils.tg_helpers import edit_or_answer, safe_delete, send_card_message

router = Router(name="pack")


def _packs_text(user: User, enabled: bool, used_today: int, daily_limit: int) -> str:
    left = max(0, daily_limit - used_today)
    if not enabled:
        limit_line = "🔴 Holat: <b>Hozircha o‘chirilgan</b>\n"
    elif left <= 0:
        limit_line = f"⏳ Bugungi limit: <b>0/{daily_limit}</b> (Bugungi paket ochilgan — ertaga qayting!)\n"
    else:
        limit_line = f"🎟 Bugungi limit: <b>{left}/{daily_limit}</b> (Kuniga qat’iy {daily_limit} marta)\n"
    lines = [
        header("🎁 <b>KARTA PAKETLARI (/pack)</b>"),
        f"🪙 Balansingiz: <b>{fmt_coins(user.coins)}</b> coin",
        limit_line,
        "ℹ️ <b>Bu bo‘lim nima uchun?</b>",
        "Coin evaziga <b>3 xil darajadagi paketdan</b> birini ochib, guruh dropini kutmasdan "
        "to‘g‘ridan-to‘g‘ri karta yutib olishingiz mumkin.",
        "• Kuniga faqat <b>1 ta</b> paket ochish ruxsat etiladi (balansni saqlash uchun).",
        "• Agar sizda allaqachon bor karta tushsa — paket narxining <b>50%</b> qismi coin sifatida qaytariladi.\n",
    ]
    for _key, cfg in get_pack_configs().items():
        lines.append(
            f"{cfg['emoji']} <b>{esc(str(cfg['name']))}</b> — <b>{fmt_coins(int(cfg['price']))} 🪙</b>\n"
            f"   <i>{esc(str(cfg['desc']))}</i>"
        )
    return "\n".join(lines)


async def _show_packs(target, owner_id: int, db_user: User) -> None:
    async with get_session() as session:
        sync_runtime_ranks(await GameplayService.get_rank_definitions(session))
        fresh = await session.get(User, db_user.id) or db_user
        enabled, used_today, daily_limit = await PackService.get_daily_pack_status(session, db_user.id)
    can_open = enabled and used_today < daily_limit
    await edit_or_answer(
        target,
        _packs_text(fresh, enabled, used_today, daily_limit),
        reply_markup=packs_menu_kb(owner_id, can_open=can_open),
    )


@router.message(Command("pack", "packs"))
async def cmd_packs(message: Message, db_user: User) -> None:
    await _show_packs(message, message.from_user.id, db_user)


@router.callback_query(NavCb.filter(F.target == "packs"))
@router.callback_query(PackCb.filter(F.act == "v"))
async def cb_packs_menu(callback: CallbackQuery, db_user: User) -> None:
    await _show_packs(callback, callback.from_user.id, db_user)
    await callback.answer()


@router.callback_query(PackCb.filter(F.act == "open"))
async def cb_pack_open(callback: CallbackQuery, callback_data: PackCb, db_user: User) -> None:
    tier = callback_data.tier
    used_now = 0
    limit_now = 1
    enabled = True
    async with get_session() as session:
        res = await PackService.open_pack(session, db_user.id, tier)
        if res.success:
            await session.commit()
            enabled, used_now, limit_now = await PackService.get_daily_pack_status(session, db_user.id)
        else:
            await session.rollback()
            enabled, used_now, limit_now = await PackService.get_daily_pack_status(session, db_user.id)

    if not res.success:
        plain = res.message.replace("<b>", "").replace("</b>", "")
        await callback.answer(plain[:190], show_alert=True)
        if not enabled or used_now >= limit_now:
            await _show_packs(callback, callback.from_user.id, db_user)
        return

    await callback.answer("✅ Paket ochildi!")
    can_more = enabled and used_now < limit_now
    left = max(0, limit_now - used_now)
    footer = f"\n\n🎟 Bugun qolgan limit: <b>{left}/{limit_now}</b>"
    kb = packs_menu_kb(callback.from_user.id, can_open=can_more)
    card = res.card
    if card is not None and not res.is_duplicate and callback.message is not None:
        meta = get_rarity_meta(card.rarity)
        caption = (
            f"{res.message}\n\n"
            f"{format_card_block(card)}\n"
            f"⭐ Daraja: {meta.emoji} <b>{esc(meta.name)}</b>{footer}"
        )
        await safe_delete(callback.bot, callback.message.chat.id, callback.message.message_id)
        await send_card_message(
            callback.bot,
            callback.message.chat.id,
            card,
            caption,
            reply_markup=kb,
        )
        return

    await edit_or_answer(callback, f"{res.message}{footer}", reply_markup=kb)
