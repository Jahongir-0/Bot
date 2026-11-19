from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database.session import get_session
from keyboards.v2 import packs_menu_kb
from models.user import User
from services.pack_service import PackService
from utils.callback_data import NavCb, PackCb
from utils.constants import PACK_CONFIG, get_rarity_meta
from utils.formatting import esc, fmt_coins, format_card_block, header
from utils.tg_helpers import edit_or_answer, safe_delete, send_card_message

router = Router(name="pack")


def _packs_text(user: User) -> str:
    lines = [
        header("🎁 <b>KARTA PAKETLARI (GACHA)</b>"),
        f"🪙 Balansingiz: <b>{fmt_coins(user.coins)}</b> coin\n",
        "Paket ochib tasodifiy anime kartalarini qo‘lga kiriting!",
        "<i>Agar sizda allaqachon bor karta tushsa — paket narxining 50% qismi coin sifatida qaytariladi.</i>\n",
    ]
    for _key, cfg in PACK_CONFIG.items():
        lines.append(
            f"{cfg['emoji']} <b>{esc(str(cfg['name']))}</b> — <b>{fmt_coins(int(cfg['price']))} 🪙</b>\n"
            f"   <i>{esc(str(cfg['desc']))}</i>"
        )
    return "\n".join(lines)


async def _show_packs(target, owner_id: int, db_user: User) -> None:
    async with get_session() as session:
        fresh = await session.get(User, db_user.id) or db_user
    await edit_or_answer(target, _packs_text(fresh), reply_markup=packs_menu_kb(owner_id))


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
    async with get_session() as session:
        res = await PackService.open_pack(session, db_user.id, tier)
        if res.success:
            await session.commit()
        else:
            await session.rollback()

    if not res.success:
        await callback.answer(res.message[:190], show_alert=True)
        return

    await callback.answer("✅ Paket ochildi!")
    card = res.card
    if card is not None and not res.is_duplicate and callback.message is not None:
        meta = get_rarity_meta(card.rarity)
        caption = (
            f"{res.message}\n\n"
            f"{format_card_block(card)}\n"
            f"⭐ Daraja: {meta.emoji} <b>{esc(card.rarity)}</b>"
        )
        await safe_delete(callback.bot, callback.message.chat.id, callback.message.message_id)
        await send_card_message(
            callback.bot,
            callback.message.chat.id,
            card,
            caption,
            reply_markup=packs_menu_kb(callback.from_user.id),
        )
        return

    await edit_or_answer(callback, res.message, reply_markup=packs_menu_kb(callback.from_user.id))
