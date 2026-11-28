"""VIP imtiyozlar va do‘kon."""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, LabeledPrice, Message

from config.settings import get_settings
from database.session import get_session
from keyboards.v2 import vip_shop_kb
from models.user import User
from services.community_service import CommunityService
from utils.callback_data import VipCb
from utils.formatting import esc, header
from utils.tg_helpers import edit_or_answer
from utils.timeutils import fmt_tashkent

logger = logging.getLogger(__name__)
router = Router(name="vip")


# ------------------------------------------------------------------
# VIP (/vip)
# ------------------------------------------------------------------
async def _show_vip(target, owner_id: int, db_user: User) -> None:
    async with get_session() as session:
        cfg = await CommunityService.get_vip_config(session)
        u = await session.get(User, db_user.id) or db_user
    tiers = cfg.get("tiers") or {}
    active_tier = tiers.get(u.vip_tier or "") if CommunityService.is_vip_active(u) else None
    active_name = (active_tier or {}).get("name") or (u.vip_tier or "VIP")
    active_str = (
        f"💎 Faol: <b>{esc(active_name)}</b> ({fmt_tashkent(u.vip_until, '%d.%m.%Y')} gacha)"
        if CommunityService.is_vip_active(u)
        else "▫️ Hozircha oddiy status"
    )
    tier_lines = []
    for key, t in tiers.items():
        emoji = t.get("emoji", "💎")
        name = t.get("name", key)
        days = max(1, int(t.get("duration_days", 30) or 30))
        mult = float(t.get("daily_bonus_mult", 1.0) or 1.0)
        perks = [f"🎁 {mult:g}x bonus"]
        if t.get("no_ads", False):
            perks.append("🚫 Reklamasiz")
        if t.get("bypass_forced_sub", False):
            perks.append("🔓 Obunasiz")
        tier_lines.append(f"{emoji} <b>{esc(name)}</b> ({days} kun) — {' · '.join(perks)}")
    tiers_block = "\n".join(tier_lines) if tier_lines else "▫️ Hozircha tariflar mavjud emas."
    txt = (
        f"{header('💎 <b>VIP OBUNA</b>')}\n"
        f"📌 Holat: {active_str}\n\n"
        "✨ <b>Tariflar va imtiyozlar:</b>\n"
        f"{tiers_block}\n"
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
    days = max(1, int(tier.get("duration_days", 30) or 30))
    await callback.bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"{tier.get('emoji', '💎')} {tier.get('name', callback_data.tier)}",
        description=f"{days} kunlik VIP obuna ({stars} Stars)",
        payload=f"vip:{callback_data.tier}:{callback.from_user.id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="VIP Obuna", amount=stars)],
    )
    await callback.answer()
