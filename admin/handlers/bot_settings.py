"""Bot sozlamalari: kunlik bonus, drop qoidalari, mukofotlar, coin yuborish, VIP, ranking."""

import logging
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from admin.filters import IsAdminFilter
from admin.handlers.common import AdminStates, _AdminRbacCallbackMiddleware
from database.session import get_session
from keyboards.admin import admin_settings_kb
from services.admin_service import AdminService
from services.coin_gift_service import CoinGiftService
from services.community_service import CommunityService
from services.gameplay_service import GameplayService
from services.ranking_service import RankingService
from services.settings_service import SettingsService
from services.welcome_bonus_service import WelcomeBonusService
from utils.callback_data import AdminCb
from utils.constants import RankingMode, get_rarity_meta
from utils.formatting import esc
from utils.tg_helpers import NotCommandFilter, edit_or_answer

logger = logging.getLogger(__name__)
router = Router(name="admin_bot_settings")
router.message.filter(IsAdminFilter())
router.callback_query.filter(IsAdminFilter())
router.callback_query.middleware(_AdminRbacCallbackMiddleware())


_RANKING_MODE_LABELS = {
    RankingMode.WEIGHTED.value: "⚖️ Rank vazni bo‘yicha",
    RankingMode.UNIQUE_FIRST.value: "✨ Noyob kartalar birinchi",
    RankingMode.TOTAL_CARDS.value: "🎴 Jami kartalar soni",
}


_DAILY_FIELD_LABELS = {
    "base_coins": "🪙 Asosiy kunlik coin",
    "streak_step": "🔥 Har kunlik seriya qo‘shimchasi (coin)",
    "max_streak_bonus": "🏆 Maksimal seriya bonusi (coin)",
}


_DROP_RULE_LABELS = {
    "members_per_card": "👥 Nechta a’zoga 1 ta karta (kamida 1)",
    "min_members": "🚫 Guruhda kamida nechta a’zo bo‘lsa karta tushsin (0 = cheklov yo‘q)",
    "min_per_day": "🔽 Kuniga eng kam tashlash soni (0+)",
    "max_per_day": "🔼 Kuniga eng ko‘p tashlash soni (1+)",
    "any_group_cooldown_hours": "⏳ Guruhlararo kutish vaqti (soat, 0+)",
    "same_group_cooldown_days": "📅 Bir xil karta guruhga qayta tushishi (kun, 1+)",
    "window_start_hour": "🌅 Boshlanish soati (0–23)",
    "window_end_hour": "🌃 Tugash soati (1–24)",
}


def _settings_cancel_kb(owner_id: int, action: str = "view") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text="❌ Bekor qilish",
            callback_data=AdminCb(owner_id=owner_id, section="settings", action=action).pack(),
        )
    )
    return b.as_markup()


async def _render_settings_home(target, owner_id: int) -> None:
    from utils.constants import get_rank_name_by_order

    async with get_session() as session:
        rank_mode = await SettingsService.get_ranking_mode(session)
        ref_cfg = await SettingsService.get_referral_config(session)
        grp_cfg = await SettingsService.get_group_reward_config(session)
        welcome_coins = await WelcomeBonusService.get_amount(session)
        dcfg = await SettingsService.get_daily_config(session)
        gcfg = await GameplayService.get_game_config(session)
        maint = await SettingsService.get_value(session, "maintenance", {"enabled": False})

    maint_on = bool(isinstance(maint, dict) and maint.get("enabled"))
    pack_lim = max(0, int(gcfg.get("pack_daily_limit", 1)))
    r3_name = get_rank_name_by_order(3, "Epic")
    r3_meta = get_rarity_meta(r3_name)
    mode_lbl = _RANKING_MODE_LABELS.get(rank_mode, rank_mode)
    if not grp_cfg["enabled"]:
        group_reward_txt = "🔴 o‘chiq"
    else:
        amount_txt = f"a’zo × {grp_cfg['per_member_coins']:,} 🪙" if grp_cfg["per_member"] else f"{grp_cfg['flat_coins']:,} 🪙"
        card_txt = f" + {r3_meta.emoji} 1 {esc(r3_name)}" if grp_cfg["give_epic_card"] else ""
        group_reward_txt = f"{amount_txt}{card_txt} (kamida {grp_cfg['min_members']} a’zo)"
    pack_txt = f"<b>{pack_lim}</b> marta/kun" if pack_lim > 0 else "🔴 O‘chirilgan (0)"
    lines = [
        "⚙️ <b>BOT SOZLAMALARI</b>\n━━━━━━━━━━━━━━━",
        f"🎁 <b>Paket limiti (/pack):</b> {pack_txt}",
        f"🎉 <b>Kunlik bonus:</b> {dcfg['base_coins']} 🪙 (+{dcfg['streak_step']}/kun, max +{dcfg['max_streak_bonus']})",
        f"🤝 <b>Referal:</b> {ref_cfg['inviter_coins']:,} 🪙 / {ref_cfg['invitee_coins']:,} 🪙",
        f"🆕 <b>Yangi foydalanuvchi bonusi:</b> {welcome_coins:,} 🪙",
        f"🏰 <b>Guruhga qo‘shish:</b> {group_reward_txt}",
        f"🏆 <b>Reyting tartibi:</b> {esc(mode_lbl)}",
        f"🛠 <b>Texnik rejim:</b> {'🔴 Yoqilgan' if maint_on else '🟢 O‘chirilgan'}",
    ]
    await edit_or_answer(
        target,
        "\n".join(lines),
        reply_markup=admin_settings_kb(owner_id, rank_mode, maint_on, pack_daily_limit=pack_lim),
    )


async def _render_daily_menu(target, owner_id: int) -> None:
    async with get_session() as session:
        dcfg = await SettingsService.get_daily_config(session)
        card_on = await SettingsService.get_daily_card_enabled(session)
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text="🎴 Kunlik bonusda karta: " + ("✅ Beriladi" if card_on else "❌ Berilmaydi"),
            callback_data=AdminCb(owner_id=owner_id, section="settings", action="daily_card_tog").pack(),
        )
    )
    b.row(InlineKeyboardButton(text=f"🪙 Asosiy coin: {dcfg['base_coins']}", callback_data=AdminCb(owner_id=owner_id, section="settings", action="daily_ed", extra="base_coins").pack()))
    b.row(InlineKeyboardButton(text=f"🔥 Streak qadam: +{dcfg['streak_step']}", callback_data=AdminCb(owner_id=owner_id, section="settings", action="daily_ed", extra="streak_step").pack()))
    b.row(InlineKeyboardButton(text=f"🏆 Max streak bonus: {dcfg['max_streak_bonus']}", callback_data=AdminCb(owner_id=owner_id, section="settings", action="daily_ed", extra="max_streak_bonus").pack()))
    b.row(InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdminCb(owner_id=owner_id, section="settings").pack()))
    await edit_or_answer(
        target,
        "🎁 <b>KUNLIK BONUS SOZLAMALARI</b>\n━━━━━━━━━━━━━━━\n"
        f"🎴 Tasodifiy karta: <b>{'✅ beriladi' if card_on else '❌ berilmaydi'}</b>\n"
        f"🪙 Asosiy coin: <b>{dcfg['base_coins']:,}</b> · 🔥 Streak qadam: <b>+{dcfg['streak_step']:,}</b> · 🏆 Max: <b>{dcfg['max_streak_bonus']:,}</b>",
        reply_markup=b.as_markup(),
    )


async def _render_drop_rules(target, owner_id: int) -> None:
    async with get_session() as session:
        rules = await SettingsService.get_drop_rules(session)
    b = InlineKeyboardBuilder()
    ann = "✅ Ha" if rules.get("announce_in_group", True) else "❌ Yo‘q"
    b.row(InlineKeyboardButton(text=f"📢 Guruhda e’lon: {ann}", callback_data=AdminCb(owner_id=owner_id, section="settings", action="dr_tog_ann").pack()))
    b.row(
        InlineKeyboardButton(text=f"👥 {rules['members_per_card']} a’zo/1 karta", callback_data=AdminCb(owner_id=owner_id, section="settings", action="dr_ed", extra="members_per_card").pack()),
        InlineKeyboardButton(text=f"🔽 Min/kun: {rules['min_per_day']}", callback_data=AdminCb(owner_id=owner_id, section="settings", action="dr_ed", extra="min_per_day").pack()),
    )
    b.row(
        InlineKeyboardButton(text=f"🔼 Max/kun: {rules['max_per_day']}", callback_data=AdminCb(owner_id=owner_id, section="settings", action="dr_ed", extra="max_per_day").pack()),
        InlineKeyboardButton(text=f"⏳ Cooldown: {rules['any_group_cooldown_hours']} soat", callback_data=AdminCb(owner_id=owner_id, section="settings", action="dr_ed", extra="any_group_cooldown_hours").pack()),
    )
    b.row(
        InlineKeyboardButton(text=f"📅 Qayta drop: {rules['same_group_cooldown_days']} kun", callback_data=AdminCb(owner_id=owner_id, section="settings", action="dr_ed", extra="same_group_cooldown_days").pack()),
        InlineKeyboardButton(text=f"🌅 Soat: {rules['window_start_hour']}:00", callback_data=AdminCb(owner_id=owner_id, section="settings", action="dr_ed", extra="window_start_hour").pack()),
    )
    b.row(
        InlineKeyboardButton(text=f"🌃 Tugash: {rules['window_end_hour']}:00", callback_data=AdminCb(owner_id=owner_id, section="settings", action="dr_ed", extra="window_end_hour").pack()),
        InlineKeyboardButton(text=f"🚫 Min a’zo: {rules['min_members']}", callback_data=AdminCb(owner_id=owner_id, section="settings", action="dr_ed", extra="min_members").pack()),
    )
    b.row(InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdminCb(owner_id=owner_id, section="settings").pack()))
    await edit_or_answer(target, f"📡 <b>DROP QOIDALARI</b>\n\n🚫 Guruhda <b>{rules['min_members']}</b> tadan kam haqiqiy a’zo bo‘lsa karta tushmaydi.\n\nO‘zgartirish uchun bosing:", reply_markup=b.as_markup())


_VIP_FIELD_LABELS = {
    "name": "✏️ VIP tarif nomi (masalan: Platinum VIP)",
    "emoji": "😀 VIP belgisi / emoji (masalan: 👑)",
    "coins": "🪙 Coin narxi (0 = coin bilan sotilmaydi)",
    "stars": "⭐ Telegram Stars narxi (0 = Stars bilan sotilmaydi)",
    "days": "⏳ Amal qilish muddati (kun, kamida 1)",
    "mult": "🎁 Kunlik bonus ko‘paytiruvchisi (masalan: 1.5, 2, 3)",
}


def _slug_vip_key(name: str, existing: dict) -> str:
    raw = re.sub(r"[^a-z0-9_]+", "_", (name or "").lower()).strip("_")
    base = (raw[:10] if raw else "vip").strip("_") or "vip"
    if base not in existing and len(base) <= 12:
        return base
    for idx in range(2, 100):
        suf = str(idx)
        cand = f"{base[: max(1, 12 - len(suf))]}{suf}"
        if cand not in existing:
            return cand
    return f"vip{len(existing) + 1}"[:12]


async def _render_vip_menu(target, owner_id: int) -> None:
    async with get_session() as session:
        vcfg = await CommunityService.get_vip_config(session)
    tiers = vcfg.get("tiers") or {}
    lines = ["💎 <b>VIP TARIFLAR BOSHQARUVI</b>\n━━━━━━━━━━━━━━━"]
    b = InlineKeyboardBuilder()
    for key, t in tiers.items():
        emoji = t.get("emoji", "💎")
        name = t.get("name", key)
        coins = int(t.get("coins", 0) or 0)
        stars = int(t.get("stars", 0) or 0)
        days = max(1, int(t.get("duration_days", 30) or 30))
        mult = float(t.get("daily_bonus_mult", 1.0) or 1.0)
        perks = []
        if t.get("no_ads", False):
            perks.append("🚫 Reklamasiz")
        if t.get("bypass_forced_sub", False):
            perks.append("🔓 Obunasiz")
        perk_txt = f" · {', '.join(perks)}" if perks else ""
        lines.append(
            f"{emoji} <b>{esc(name)}</b> (<code>{esc(key)}</code>)\n"
            f"   🪙 {coins:,} | ⭐ {stars:,} | ⏳ {days} kun | 🎁 {mult:g}x{perk_txt}"
        )
        b.row(
            InlineKeyboardButton(
                text=f"{emoji} {name[:24]} — Tahrirlash",
                callback_data=AdminCb(owner_id=owner_id, section="settings", action="vip_det", extra=key).pack(),
            )
        )
    b.row(
        InlineKeyboardButton(
            text="➕ Yangi VIP tarif qo‘shish",
            callback_data=AdminCb(owner_id=owner_id, section="settings", action="vip_add").pack(),
        )
    )
    b.row(InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdminCb(owner_id=owner_id, section="settings").pack()))
    await edit_or_answer(target, "\n".join(lines), reply_markup=b.as_markup())


async def _render_vip_tier_detail(target, owner_id: int, tier_key: str) -> None:
    async with get_session() as session:
        vcfg = await CommunityService.get_vip_config(session)
    tiers = vcfg.get("tiers") or {}
    t = tiers.get(tier_key)
    if not isinstance(t, dict):
        await _render_vip_menu(target, owner_id)
        return
    emoji = t.get("emoji", "💎")
    name = t.get("name", tier_key)
    coins = int(t.get("coins", 0) or 0)
    stars = int(t.get("stars", 0) or 0)
    days = max(1, int(t.get("duration_days", 30) or 30))
    mult = float(t.get("daily_bonus_mult", 1.0) or 1.0)
    no_ads = bool(t.get("no_ads", False))
    bypass_sub = bool(t.get("bypass_forced_sub", False))

    text = (
        f"{emoji} <b>{esc(name)}</b> (<code>{esc(tier_key)}</code>)\n"
        "━━━━━━━━━━━━━━━\n"
        f"🪙 Coin narxi: <b>{coins:,}</b>\n"
        f"⭐ Stars narxi: <b>{stars:,}</b>\n"
        f"⏳ Muddati: <b>{days} kun</b>\n"
        f"🎁 Kunlik bonus: <b>{mult:g}x</b>\n"
        f"🚫 Reklamasiz: <b>{'✅ Ha' if no_ads else '❌ Yo‘q'}</b>\n"
        f"🔓 Majburiy obunadan ozod: <b>{'✅ Ha' if bypass_sub else '❌ Yo‘q'}</b>"
    )
    cb = lambda act, extra="": AdminCb(owner_id=owner_id, section="settings", action=act, extra=extra).pack()  # noqa: E731
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="✏️ Nomi", callback_data=cb("vip_ed", f"{tier_key}.name")),
        InlineKeyboardButton(text="😀 Emoji", callback_data=cb("vip_ed", f"{tier_key}.emoji")),
    )
    b.row(
        InlineKeyboardButton(text=f"🪙 Coin: {coins:,}", callback_data=cb("vip_ed", f"{tier_key}.coins")),
        InlineKeyboardButton(text=f"⭐ Stars: {stars:,}", callback_data=cb("vip_ed", f"{tier_key}.stars")),
    )
    b.row(
        InlineKeyboardButton(text=f"⏳ Muddat: {days}k", callback_data=cb("vip_ed", f"{tier_key}.days")),
        InlineKeyboardButton(text=f"🎁 Bonus: {mult:g}x", callback_data=cb("vip_ed", f"{tier_key}.mult")),
    )
    b.row(
        InlineKeyboardButton(text=f"🚫 Reklamasiz: {'✅' if no_ads else '❌'}", callback_data=cb("vip_tog", f"{tier_key}.no_ads")),
        InlineKeyboardButton(text=f"🔓 Obunasiz: {'✅' if bypass_sub else '❌'}", callback_data=cb("vip_tog", f"{tier_key}.sub")),
    )
    if len(tiers) > 1:
        b.row(InlineKeyboardButton(text="🗑 Tarifni o‘chirish", callback_data=cb("vip_del", tier_key)))
    b.row(InlineKeyboardButton(text="⬅️ VIP tariflar", callback_data=cb("vip_menu")))
    await edit_or_answer(target, text, reply_markup=b.as_markup())


# ---------------------------------------------------------------------------
# BOT SETTINGS (Clean V2 controls: Daily, Referral, Drop Rules, Welcome, VIP, Maint, Ranking)
# ---------------------------------------------------------------------------
@router.callback_query(AdminCb.filter(F.section == "settings"))
async def cb_admin_settings(callback: CallbackQuery, callback_data: AdminCb, state: FSMContext) -> None:
    act = callback_data.action
    owner_id = callback.from_user.id

    if act == "rewards_menu":
        await state.clear()
        await _render_rewards_menu(callback)
        return

    if act in ("edit_ref_inviter", "edit_ref_invitee"):
        prompts = {
            "edit_ref_inviter": (AdminStates.edit_ref_inviter, "🤝 Taklif qiluvchi uchun <b>referal coin</b> miqdorini kiriting:"),
            "edit_ref_invitee": (AdminStates.edit_ref_invitee, "🆕 Yangi foydalanuvchi uchun <b>referal coin</b> miqdorini kiriting:"),
        }
        st, txt = prompts[act]
        await state.set_state(st)
        await state.update_data(owner_tg_id=owner_id)
        await edit_or_answer(callback, txt, reply_markup=_settings_cancel_kb(owner_id, "rewards_menu"))
        return

    if act == "grp_tog":
        field = callback_data.extra
        async with get_session() as session:
            cur = await SettingsService.get_group_reward_config(session)
            if field not in ("enabled", "per_member", "give_epic_card"):
                await callback.answer("Noma’lum sozlama", show_alert=True)
                return
            await SettingsService.set_group_reward_field(session, field, not cur[field])
            await AdminService.log_action(session, owner_id, "edit_group_reward_cfg", "settings", field, {"value": not cur[field]})
            await session.commit()
        await callback.answer("✅ Yangilandi!")
        await state.clear()
        await _render_rewards_menu(callback)
        return

    if act == "grp_ed":
        field = callback_data.extra
        if field not in _GRP_CFG_LABELS:
            await callback.answer("Noma’lum sozlama", show_alert=True)
            return
        await state.set_state(AdminStates.edit_group_cfg_field)
        await state.update_data(owner_tg_id=owner_id, field=field)
        await edit_or_answer(
            callback,
            f"🏰 <b>{esc(_GRP_CFG_LABELS[field])}</b>\n\nYangi qiymatni kiriting (butun son, 0 ham mumkin):",
            reply_markup=_settings_cancel_kb(owner_id, "rewards_menu"),
        )
        return

    if act == "cg_menu":
        await state.clear()
        text, kb = await _coin_gift_menu_text_and_kb(owner_id)
        await edit_or_answer(callback, text, reply_markup=kb)
        return

    if act == "cg_tog":
        async with get_session() as session:
            cfg = await CoinGiftService.get_config(session)
            await CoinGiftService.set_field(session, "enabled", not cfg["enabled"])
            await AdminService.log_action(session, owner_id, "toggle_coin_gift", "settings", "enabled", {"value": not cfg["enabled"]})
            await session.commit()
        await callback.answer("✅ Yangilandi!")
        text, kb = await _coin_gift_menu_text_and_kb(owner_id)
        await edit_or_answer(callback, text, reply_markup=kb)
        return

    if act == "cg_ed":
        field = callback_data.extra
        if field not in _CG_LABELS:
            await callback.answer("Noma’lum sozlama", show_alert=True)
            return
        await state.set_state(AdminStates.edit_coin_gift_field)
        await state.update_data(owner_tg_id=owner_id, field=field)
        await edit_or_answer(
            callback,
            f"💸 <b>{esc(_CG_LABELS[field])}</b>\n\nYangi qiymatni kiriting (butun son):",
            reply_markup=_settings_cancel_kb(owner_id, "cg_menu"),
        )
        return

    if act == "daily_card_tog":
        async with get_session() as session:
            cur = await SettingsService.get_daily_card_enabled(session)
            await SettingsService.set_daily_card_enabled(session, not cur)
            await AdminService.log_action(session, owner_id, "toggle_daily_card", "settings", "daily_card_enabled", {"value": not cur})
            await session.commit()
        await callback.answer("✅ Yangilandi!")
        await _render_daily_menu(callback, owner_id)
        return

    if act == "daily_menu":
        await state.clear()
        await _render_daily_menu(callback, owner_id)
        return

    if act == "daily_ed":
        field = callback_data.extra
        await state.set_state(AdminStates.edit_daily_field)
        await state.update_data(owner_tg_id=owner_id, field=field)
        label = _DAILY_FIELD_LABELS.get(field, field)
        await edit_or_answer(
            callback,
            f"🎁 <b>{esc(label)}</b>\n\nYangi qiymatni kiriting (butun son):",
            reply_markup=_settings_cancel_kb(owner_id, "daily_menu"),
        )
        return

    if act == "drop_rules":
        await state.clear()
        await _render_drop_rules(callback, owner_id)
        return

    if act == "dr_tog_ann":
        async with get_session() as session:
            rules = await SettingsService.get_drop_rules(session)
            rules["announce_in_group"] = not bool(rules.get("announce_in_group", True))
            await SettingsService.set_value(session, "drop_rules", rules)
            await session.commit()
        await callback.answer("✅ Yangilandi!")
        await state.clear()
        await _render_drop_rules(callback, owner_id)
        return

    if act == "dr_ed":
        field = callback_data.extra
        await state.set_state(AdminStates.edit_drop_rule_field)
        await state.update_data(owner_tg_id=owner_id, field=field)
        label = _DROP_RULE_LABELS.get(field, field)
        await edit_or_answer(
            callback,
            f"📡 <b>{esc(label)}</b>\n\nYangi qiymatni kiriting:",
            reply_markup=_settings_cancel_kb(owner_id, "drop_rules"),
        )
        return

    if act == "welcome_edit":
        await state.set_state(AdminStates.edit_welcome_text)
        await state.update_data(owner_tg_id=owner_id)
        async with get_session() as session:
            cur = str(await SettingsService.get_value(session, "welcome_text", "") or "")
        preview = esc(cur[:1500]) if cur else "<i>Bo‘sh</i>"
        await edit_or_answer(
            callback,
            f"👋 <b>KIRISH MATNI (/start)</b>\n\nHozirgi matn:\n<code>{preview}</code>\n\nYangi matnni yuboring:",
            reply_markup=_settings_cancel_kb(owner_id, "view"),
        )
        return

    if act == "vip_menu":
        await state.clear()
        await _render_vip_menu(callback, owner_id)
        return

    if act == "vip_det":
        await state.clear()
        tier_key = callback_data.extra or ""
        await _render_vip_tier_detail(callback, owner_id, tier_key)
        return

    if act == "vip_add":
        await state.set_state(AdminStates.add_vip_name)
        await state.update_data(owner_tg_id=owner_id)
        await edit_or_answer(
            callback,
            "💎 <b>YANGI VIP TARIF QO‘SHISH</b> (1/4)\n\n"
            "Tarif nomini kiriting (masalan: <code>Platinum VIP</code>):",
            reply_markup=_settings_cancel_kb(owner_id, "vip_menu"),
        )
        return

    if act == "vip_tog":
        raw_ex = callback_data.extra or ""
        if "." not in raw_ex:
            await callback.answer("Tarif topilmadi.", show_alert=True)
            return
        tier_key, flag = raw_ex.rsplit(".", 1)
        field_name = "no_ads" if flag == "no_ads" else ("bypass_forced_sub" if flag == "sub" else "")
        if not field_name:
            await callback.answer("Noma’lum parametr.", show_alert=True)
            return
        async with get_session() as session:
            vcfg = await CommunityService.get_vip_config(session)
            tiers = {k: dict(v) for k, v in (vcfg.get("tiers") or {}).items()}
            if tier_key not in tiers:
                await callback.answer("Tarif topilmadi.", show_alert=True)
                return
            tiers[tier_key][field_name] = not bool(tiers[tier_key].get(field_name, False))
            vcfg["tiers"] = tiers
            await SettingsService.set_value(session, "vip_config", vcfg)
            await AdminService.log_action(
                session, owner_id, "toggle_vip_perk", "settings", f"{tier_key}.{field_name}", {"value": tiers[tier_key][field_name]}
            )
            await session.commit()
        await callback.answer("✅ Yangilandi!")
        await _render_vip_tier_detail(callback, owner_id, tier_key)
        return

    if act == "vip_del":
        tier_key = callback_data.extra or ""
        async with get_session() as session:
            vcfg = await CommunityService.get_vip_config(session)
            tiers = {k: dict(v) for k, v in (vcfg.get("tiers") or {}).items()}
            if len(tiers) <= 1:
                await callback.answer("⚠️ Kamida 1 ta VIP tarif qolishi shart.", show_alert=True)
                return
            if tier_key not in tiers:
                await callback.answer("Tarif topilmadi.", show_alert=True)
                return
            del tiers[tier_key]
            vcfg["tiers"] = tiers
            await SettingsService.set_value(session, "vip_config", vcfg)
            await AdminService.log_action(session, owner_id, "delete_vip_tier", "settings", tier_key, {})
            await session.commit()
        await callback.answer("🗑 VIP tarif o‘chirildi!")
        await _render_vip_menu(callback, owner_id)
        return

    if act == "vip_ed":
        tier_metric = callback_data.extra or ""
        if "." not in tier_metric:
            await callback.answer("Tarif topilmadi.", show_alert=True)
            return
        tier_key, field = tier_metric.rsplit(".", 1)
        if field not in _VIP_FIELD_LABELS:
            await callback.answer("Noma’lum maydon.", show_alert=True)
            return
        await state.set_state(AdminStates.edit_vip_field)
        await state.update_data(owner_tg_id=owner_id, vip_tier=tier_key, vip_field=field)
        await edit_or_answer(
            callback,
            f"💎 <b>{esc(tier_key)}</b> — {esc(_VIP_FIELD_LABELS[field])}\n\nYangi qiymatni kiriting:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text="❌ Bekor qilish",
                        callback_data=AdminCb(owner_id=owner_id, section="settings", action="vip_det", extra=tier_key).pack(),
                    )
                ]]
            ),
        )
        return

    if act == "pack_limit":
        async with get_session() as session:
            gcfg = await GameplayService.get_game_config(session)
        cur_lim = max(0, int(gcfg.get("pack_daily_limit", 1)))
        await state.set_state(AdminStates.edit_pack_limit)
        await state.update_data(owner_tg_id=owner_id)
        await edit_or_answer(
            callback,
            "🎁 <b>KUNLIK PAKET LIMITI (/pack)</b>\n"
            "━━━━━━━━━━━━━━━\n"
            f"Hozirgi limit: <b>{cur_lim} marta/kun</b>\n\n"
            "Bir foydalanuvchi 1 kunda nechta paket ocha olsin?\n"
            "• <code>1</code> — standart (kuniga 1 marta)\n"
            "• <code>0</code> — paketlarni vaqtincha yopish\n"
            "• <code>1–100</code> — kunlik limit soni\n\n"
            "Yangi sonni kiriting:",
            reply_markup=_settings_cancel_kb(owner_id, "view"),
        )
        return

    await state.clear()
    async with get_session() as session:
        if act == "set_rank":
            if callback_data.extra in {m.value for m in RankingMode}:
                await SettingsService.set_ranking_mode(session, callback_data.extra)
                await AdminService.log_action(
                    session, owner_id, "set_ranking_mode", "settings", "ranking_mode", {"mode": callback_data.extra}
                )
                await session.commit()
                RankingService.invalidate_cache()
                await callback.answer("🏆 Reyting tartibi yangilandi!")
        elif act == "maint_tog":
            maint = await SettingsService.get_value(session, "maintenance", {"enabled": False})
            maint = dict(maint) if isinstance(maint, dict) else {"enabled": False}
            maint["enabled"] = not bool(maint.get("enabled", False))
            await SettingsService.set_value(session, "maintenance", maint)
            await session.commit()
            await callback.answer("🛠 Texnik rejim o‘zgartirildi!")

    await _render_settings_home(callback, owner_id)


@router.message(AdminStates.edit_pack_limit, NotCommandFilter())
async def fsm_edit_pack_limit(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit() or not (0 <= int(raw) <= 100):
        await message.answer("⚠️ 0 dan 100 gacha butun son kiriting.")
        return
    val = int(raw)
    await state.clear()
    async with get_session() as session:
        gcfg = await GameplayService.get_game_config(session)
        gcfg["pack_daily_limit"] = val
        gcfg["pack_enabled"] = val > 0
        await SettingsService.set_value(session, "game_config", gcfg)
        await AdminService.log_action(
            session, message.from_user.id, "set_pack_daily_limit", "settings", "pack_daily_limit", {"limit": val}
        )
        await session.commit()
    await message.answer(f"✅ Kunlik paket limiti <b>{val}/kun</b> qilib saqlandi!")
    await _render_settings_home(message, message.from_user.id)


@router.message(AdminStates.edit_daily_field, NotCommandFilter())
async def fsm_edit_daily_field(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    field = data["field"]
    min_val = 1 if field == "base_coins" else 0
    if not raw.isdigit() or int(raw) < min_val:
        await message.answer(f"⚠️ Kamida {min_val} butun son kiriting.")
        return
    await state.clear()
    async with get_session() as session:
        dcfg = await SettingsService.get_daily_config(session)
        dcfg[field] = int(raw)
        await SettingsService.set_value(session, "daily_bonus", dcfg)
        await session.commit()
    await message.answer("✅ Daily Bonus sozlamasi yangilandi!")
    await _render_daily_menu(message, message.from_user.id)


@router.message(AdminStates.edit_drop_rule_field, NotCommandFilter())
async def fsm_edit_drop_rule_field(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    field = data["field"]
    if not raw.isdigit():
        await message.answer("⚠️ Faqat butun son kiriting.")
        return
    val = int(raw)
    if field in ("min_per_day", "any_group_cooldown_hours", "min_members") and val < 0:
        await message.answer("⚠️ 0 yoki musbat son kiriting.")
        return
    if field in ("members_per_card", "max_per_day", "same_group_cooldown_days") and val < 1:
        await message.answer("⚠️ Kamida 1 son kiriting.")
        return
    if field == "window_start_hour" and not (0 <= val <= 23):
        await message.answer("⚠️ Boshlanish soati 0 dan 23 gacha bo‘lishi kerak.")
        return
    if field == "window_end_hour" and not (1 <= val <= 24):
        await message.answer("⚠️ Tugash soati 1 dan 24 gacha bo‘lishi kerak.")
        return
    await state.clear()
    async with get_session() as session:
        rules = await SettingsService.get_drop_rules(session)
        rules[field] = val
        await SettingsService.set_value(session, "drop_rules", rules)
        await session.commit()
    await message.answer(f"✅ Drop qoidasi saqlandi (<b>{val}</b>)!")
    await _render_drop_rules(message, message.from_user.id)


@router.message(AdminStates.edit_welcome_text, NotCommandFilter())
async def fsm_edit_welcome_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    txt = (message.text or "").strip()
    if not txt:
        await message.answer("⚠️ Matn kiriting.")
        return
    await state.clear()
    async with get_session() as session:
        await SettingsService.set_value(session, "welcome_text", txt)
        await session.commit()
    await message.answer("✅ Welcome matni saqlandi!")
    await _render_settings_home(message, message.from_user.id)


@router.message(AdminStates.edit_vip_price, NotCommandFilter())
async def fsm_edit_vip_price(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("⚠️ Musbat son kiriting.")
        return
    tier_key, metric = data["vip_key"].rsplit(".", 1)
    await state.clear()
    async with get_session() as session:
        vcfg = await CommunityService.get_vip_config(session)
        tiers = {k: dict(v) for k, v in (vcfg.get("tiers") or {}).items()}
        if tier_key in tiers and metric in ("coins", "stars"):
            tiers[tier_key][metric] = int(raw)
            vcfg["tiers"] = tiers
            await SettingsService.set_value(session, "vip_config", vcfg)
            await session.commit()
    await message.answer(f"✅ {esc(tier_key)} narxi = <b>{int(raw):,}</b> saqlandi!")
    await _render_vip_menu(message, message.from_user.id)


@router.message(AdminStates.edit_vip_field, NotCommandFilter())
async def fsm_edit_vip_field(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    tier_key = str(data.get("vip_tier") or "")
    field = str(data.get("vip_field") or "")
    raw_txt = (message.text or "").strip()

    store_key = field
    parsed_val: object
    if field == "name":
        if not raw_txt or len(raw_txt) > 32:
            await message.answer("⚠️ Nom 1 dan 32 tagacha belgi bo‘lsin.")
            return
        parsed_val = raw_txt
    elif field == "emoji":
        tok = raw_txt.split()[0] if raw_txt else ""
        if not tok or len(tok) > 8:
            await message.answer("⚠️ 1–2 ta emoji kiriting (masalan: 👑).")
            return
        parsed_val = tok
    elif field in ("coins", "stars"):
        num_str = raw_txt.replace(",", "").replace(" ", "")
        if not num_str.isdigit() or int(num_str) < 0:
            await message.answer("⚠️ 0 yoki musbat butun son kiriting.")
            return
        parsed_val = int(num_str)
    elif field == "days":
        num_str = raw_txt.replace(",", "").replace(" ", "")
        if not num_str.isdigit() or not (1 <= int(num_str) <= 3650):
            await message.answer("⚠️ Muddat 1 dan 3650 kungacha bo‘lishi kerak.")
            return
        store_key = "duration_days"
        parsed_val = int(num_str)
    elif field == "mult":
        num_str = raw_txt.replace(",", ".").replace(" ", "")
        try:
            fval = round(float(num_str), 2)
        except ValueError:
            await message.answer("⚠️ Masalan <code>1.5</code> yoki <code>2</code> kabi son kiriting.")
            return
        if not (1.0 <= fval <= 20.0):
            await message.answer("⚠️ Ko‘paytiruvchi 1.0 dan 20.0 gacha bo‘lsin.")
            return
        store_key = "daily_bonus_mult"
        parsed_val = fval
    else:
        await state.clear()
        await _render_vip_menu(message, message.from_user.id)
        return

    await state.clear()
    async with get_session() as session:
        vcfg = await CommunityService.get_vip_config(session)
        tiers = {k: dict(v) for k, v in (vcfg.get("tiers") or {}).items()}
        if tier_key in tiers:
            tiers[tier_key][store_key] = parsed_val
            vcfg["tiers"] = tiers
            await SettingsService.set_value(session, "vip_config", vcfg)
            await AdminService.log_action(
                session, message.from_user.id, "edit_vip_tier", "settings", f"{tier_key}.{store_key}", {"value": parsed_val}
            )
            await session.commit()
    await _render_vip_tier_detail(message, message.from_user.id, tier_key)


@router.message(AdminStates.add_vip_name, NotCommandFilter())
async def fsm_add_vip_name(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    name = (message.text or "").strip()
    if not name or len(name) > 32:
        await message.answer("⚠️ Tarif nomi 1 dan 32 tagacha belgi bo‘lsin.")
        return
    await state.update_data(new_vip_name=name)
    await state.set_state(AdminStates.add_vip_coins)
    await message.answer(
        f"💎 <b>{esc(name)}</b> (2/4)\n\n"
        "Coin narxini kiriting (<code>0</code> — coin bilan sotilmaydi):",
        reply_markup=_settings_cancel_kb(message.from_user.id, "vip_menu"),
    )


@router.message(AdminStates.add_vip_coins, NotCommandFilter())
async def fsm_add_vip_coins(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit() or int(raw) < 0:
        await message.answer("⚠️ 0 yoki musbat butun son kiriting.")
        return
    await state.update_data(new_vip_coins=int(raw))
    await state.set_state(AdminStates.add_vip_stars)
    await message.answer(
        f"💎 <b>{esc(data.get('new_vip_name', 'VIP'))}</b> (3/4)\n\n"
        "Telegram Stars narxini kiriting (<code>0</code> — Stars bilan sotilmaydi):",
        reply_markup=_settings_cancel_kb(message.from_user.id, "vip_menu"),
    )


@router.message(AdminStates.add_vip_stars, NotCommandFilter())
async def fsm_add_vip_stars(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit() or int(raw) < 0:
        await message.answer("⚠️ 0 yoki musbat butun son kiriting.")
        return
    coins_val = int(data.get("new_vip_coins", 0) or 0)
    stars_val = int(raw)
    if coins_val == 0 and stars_val == 0:
        await message.answer("⚠️ Coin yoki Stars narxidan kamida bittasi 0 dan katta bo‘lishi kerak.")
        return
    await state.update_data(new_vip_stars=stars_val)
    await state.set_state(AdminStates.add_vip_days)
    await message.answer(
        f"💎 <b>{esc(data.get('new_vip_name', 'VIP'))}</b> (4/4)\n\n"
        "Amal qilish muddatini kiriting (kun, masalan: <code>30</code>):",
        reply_markup=_settings_cancel_kb(message.from_user.id, "vip_menu"),
    )


@router.message(AdminStates.add_vip_days, NotCommandFilter())
async def fsm_add_vip_days(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit() or not (1 <= int(raw) <= 3650):
        await message.answer("⚠️ Muddat 1 dan 3650 kungacha butun son bo‘lsin.")
        return
    days = int(raw)
    name = str(data.get("new_vip_name") or "VIP")
    coins_val = int(data.get("new_vip_coins", 0) or 0)
    stars_val = int(data.get("new_vip_stars", 0) or 0)
    await state.clear()
    async with get_session() as session:
        vcfg = await CommunityService.get_vip_config(session)
        tiers = {k: dict(v) for k, v in (vcfg.get("tiers") or {}).items()}
        new_key = _slug_vip_key(name, tiers)
        tiers[new_key] = {
            "name": name,
            "emoji": "💎",
            "stars": stars_val,
            "coins": coins_val,
            "duration_days": days,
            "daily_bonus_mult": 1.5,
            "no_ads": True,
            "bypass_forced_sub": False,
        }
        vcfg["tiers"] = tiers
        await SettingsService.set_value(session, "vip_config", vcfg)
        await AdminService.log_action(session, message.from_user.id, "add_vip_tier", "settings", new_key, tiers[new_key])
        await session.commit()
    await _render_vip_tier_detail(message, message.from_user.id, new_key)


_CG_LABELS = {
    "min_amount": "🔻 Minimal miqdor (coin)",
    "daily_limit": "📅 Kunlik limit (jami coin, 0 = cheklovsiz)",
    "fee_percent": "💸 Komissiya (%, 0–90)",
}


async def _coin_gift_menu_text_and_kb(owner_id: int):
    async with get_session() as session:
        cfg = await CoinGiftService.get_config(session)
    on = "✅ Yoqilgan" if cfg["enabled"] else "❌ O‘chiq"
    limit_txt = "cheklovsiz" if not cfg["daily_limit"] else format(cfg["daily_limit"], ",") + " 🪙"
    text = (
        "💸 <b>COIN YUBORISH SOZLAMALARI</b>\n━━━━━━━━━━━━━━━\n"
        f"Holat: <b>{on}</b>\n"
        f"🔻 Minimal miqdor: <b>{cfg['min_amount']:,} 🪙</b>\n"
        f"📅 Kunlik limit: <b>{limit_txt}</b>\n"
        f"💸 Komissiya: <b>{cfg['fee_percent']}%</b>\n\n"
        "<i>Foydalanuvchi yuborgan coin undan ayriladi, qabul qiluvchiga (komissiyasiz qismi) qo‘shiladi.</i>"
    )
    cb = lambda action, extra="": AdminCb(owner_id=owner_id, section="settings", action=action, extra=extra).pack()  # noqa: E731
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text=f"Coin yuborish: {on}", callback_data=cb("cg_tog")))
    b.row(
        InlineKeyboardButton(text=f"🔻 Min: {cfg['min_amount']:,}", callback_data=cb("cg_ed", "min_amount")),
        InlineKeyboardButton(text=f"📅 Limit: {cfg['daily_limit']:,}", callback_data=cb("cg_ed", "daily_limit")),
    )
    b.row(InlineKeyboardButton(text=f"💸 Komissiya: {cfg['fee_percent']}%", callback_data=cb("cg_ed", "fee_percent")))
    b.row(InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdminCb(owner_id=owner_id, section="settings", action="view").pack()))
    return text, b.as_markup()


_GRP_CFG_LABELS = {
    "welcome_bonus": "🆕 Yangi foydalanuvchi boshlang‘ich bonusi (coin)",
    "per_member_coins": "👥 Har bir a’zo uchun coin",
    "flat_coins": "🪙 Qat’iy mukofot (a’zo soniga bog‘liq bo‘lmasa)",
    "min_members": "🚫 Kamida nechta haqiqiy a’zo kerak",
}


async def _rewards_menu_text_and_kb(owner_id: int):
    from utils.constants import get_rank_name_by_order

    async with get_session() as session:
        ref_cfg = await SettingsService.get_referral_config(session)
        gcfg = await SettingsService.get_group_reward_config(session)
        welcome_coins = await WelcomeBonusService.get_amount(session)
    r3_name = get_rank_name_by_order(3, "Epic")
    r3_meta = get_rarity_meta(r3_name)
    on = lambda v: "✅ Yoqilgan" if v else "❌ O‘chiq"  # noqa: E731
    if gcfg["per_member"]:
        amount_line = f"👥 Hisoblash: <b>a’zolar soni × {gcfg['per_member_coins']:,} 🪙</b> (masalan 100 a’zo = {100 * gcfg['per_member_coins']:,} 🪙)"
    else:
        amount_line = f"🪙 Hisoblash: qat’iy <b>{gcfg['flat_coins']:,} 🪙</b>"
    text = (
        "🤝 <b>BONUS VA MUKOFOTLAR</b>\n\n"
        "<b>Yangi foydalanuvchi</b>\n"
        f"🆕 Boshlang‘ich bonus: <b>{welcome_coins:,} 🪙</b> (faqat yangi qo‘shiladiganlarga)\n\n"
        "<b>Referal (do‘st taklif qilish)</b>\n"
        f"👤 Taklif qilgan odamga: <b>{ref_cfg['inviter_coins']:,} 🪙</b>\n"
        f"🆕 Taklif qilingan do‘stga: <b>{ref_cfg['invitee_coins']:,} 🪙</b>\n\n"
        "<b>Botni guruhga qo‘shish mukofoti</b>\n"
        f"Holat: <b>{on(gcfg['enabled'])}</b>\n"
        f"{amount_line}\n"
        f"🚫 Kamida <b>{gcfg['min_members']}</b> ta haqiqiy a’zo bo‘lmasa mukofot berilmaydi (botlar hisobga olinmaydi)\n"
        f"{r3_meta.emoji} Qo‘shimcha {esc(r3_name)} karta: <b>{on(gcfg['give_epic_card'])}</b>"
    )
    cb = lambda action, extra="": AdminCb(owner_id=owner_id, section="settings", action=action, extra=extra).pack()  # noqa: E731
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=f"🆕 Yangi user bonusi: {welcome_coins:,} 🪙", callback_data=cb("grp_ed", "welcome_bonus")))
    builder.row(
        InlineKeyboardButton(text="👤 Taklif qilgan coin", callback_data=cb("edit_ref_inviter")),
        InlineKeyboardButton(text="🆕 Do‘st coin", callback_data=cb("edit_ref_invitee")),
    )
    builder.row(InlineKeyboardButton(text=f"🏰 Guruh mukofoti: {on(gcfg['enabled'])}", callback_data=cb("grp_tog", "enabled")))
    builder.row(
        InlineKeyboardButton(
            text="🔀 A’zo soniga hisoblash: " + ("✅ Ha" if gcfg["per_member"] else "❌ Yo‘q"),
            callback_data=cb("grp_tog", "per_member"),
        )
    )
    builder.row(
        InlineKeyboardButton(text=f"👥 1 a’zo = {gcfg['per_member_coins']:,} 🪙", callback_data=cb("grp_ed", "per_member_coins")),
        InlineKeyboardButton(text=f"🪙 Qat’iy: {gcfg['flat_coins']:,}", callback_data=cb("grp_ed", "flat_coins")),
    )
    builder.row(
        InlineKeyboardButton(text=f"🚫 Min a’zo: {gcfg['min_members']}", callback_data=cb("grp_ed", "min_members")),
        InlineKeyboardButton(text=f"{r3_meta.emoji} Karta: {'✅' if gcfg['give_epic_card'] else '❌'}", callback_data=cb("grp_tog", "give_epic_card")),
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdminCb(owner_id=owner_id, section="settings", action="view").pack())
    )
    return text, builder.as_markup()


async def _render_rewards_menu(callback: CallbackQuery) -> None:
    text, kb = await _rewards_menu_text_and_kb(callback.from_user.id)
    await edit_or_answer(callback, text, reply_markup=kb)


async def _save_reward_coin_setting(message: Message, state: FSMContext, kind: str) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") is not None and data.get("owner_tg_id") != message.from_user.id:
        return
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit():
        await message.answer("⚠️ Faqat musbat butun son (yoki 0) kiriting.")
        return
    val = int(raw)
    try:
        async with get_session() as session:
            if kind == "inviter":
                await SettingsService.set_referral_config(session, inviter_coins=val)
            else:
                await SettingsService.set_referral_config(session, invitee_coins=val)
            await AdminService.log_action(session, message.from_user.id, f"edit_{kind}_reward", "settings", kind, {"coins": val})
            await session.commit()
    except ValueError as exc:
        await message.answer(f"⚠️ {esc(str(exc))}")
        return
    await state.clear()
    text, kb = await _rewards_menu_text_and_kb(message.from_user.id)
    await message.answer("✅ Saqlandi!\n\n" + text, reply_markup=kb)


@router.message(AdminStates.edit_ref_inviter, NotCommandFilter())
async def fsm_edit_ref_inviter(message: Message, state: FSMContext) -> None:
    await _save_reward_coin_setting(message, state, "inviter")


@router.message(AdminStates.edit_ref_invitee, NotCommandFilter())
async def fsm_edit_ref_invitee(message: Message, state: FSMContext) -> None:
    await _save_reward_coin_setting(message, state, "invitee")


@router.message(AdminStates.edit_coin_gift_field, NotCommandFilter())
async def fsm_edit_coin_gift_field(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") is not None and data.get("owner_tg_id") != message.from_user.id:
        return
    field = str(data.get("field", ""))
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit():
        await message.answer("⚠️ Faqat butun son kiriting.")
        return
    try:
        async with get_session() as session:
            await CoinGiftService.set_field(session, field, int(raw))
            await AdminService.log_action(session, message.from_user.id, "edit_coin_gift", "settings", field, {"value": int(raw)})
            await session.commit()
    except ValueError as exc:
        await message.answer(f"⚠️ {esc(str(exc))}")
        return
    await state.clear()
    text, kb = await _coin_gift_menu_text_and_kb(message.from_user.id)
    await message.answer("✅ Saqlandi!\n\n" + text, reply_markup=kb)


@router.message(AdminStates.edit_group_cfg_field, NotCommandFilter())
async def fsm_edit_group_cfg_field(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("owner_tg_id") is not None and data.get("owner_tg_id") != message.from_user.id:
        return
    field = str(data.get("field", ""))
    raw = (message.text or "").strip().replace(",", "").replace(" ", "")
    if not raw.isdigit():
        await message.answer("⚠️ Faqat musbat butun son (yoki 0) kiriting.")
        return
    try:
        async with get_session() as session:
            if field == "welcome_bonus":
                await WelcomeBonusService.set_amount(session, int(raw))
            else:
                await SettingsService.set_group_reward_field(session, field, int(raw))
            await AdminService.log_action(session, message.from_user.id, "edit_group_reward_cfg", "settings", field, {"value": int(raw)})
            await session.commit()
    except ValueError as exc:
        await message.answer(f"⚠️ {esc(str(exc))}")
        return
    await state.clear()
    text, kb = await _rewards_menu_text_and_kb(message.from_user.id)
    await message.answer("✅ Saqlandi!\n\n" + text, reply_markup=kb)


# ---------------------------------------------------------------------------
# TELEGRAM STARS TARIFLARI
# ---------------------------------------------------------------------------
STARS_INPUT_HELP = (
    "Ikkita raqamni bo‘sh joy bilan yozing: <code>Stars Coin</code>\n"
    "Misol: <code>250 30000</code> — 250 ⭐ evaziga 30,000 coin.\n"
    "<i>Bekor qilish: /cancel</i>"
)
