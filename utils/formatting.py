import html
from typing import Dict, List, Optional

from models.card import Card
from models.event import Event
from models.group import Group
from models.user import User
from utils.constants import PROFILE_BACKGROUNDS, PROFILE_FRAMES, get_rarity_meta


def esc(value: object) -> str:
    return "" if value is None else html.escape(str(value))


def fmt_coins(amount: int) -> str:
    return f"{int(amount):,}"


def xp_progress_bar(xp: int, xp_per_level: int = 250) -> str:
    if xp_per_level <= 0:
        xp_per_level = 250
    into_lvl = xp % xp_per_level
    filled = int((into_lvl / xp_per_level) * 10)
    return "█" * filled + "░" * (10 - filled) + f" {into_lvl}/{xp_per_level}"


def format_card_block(
    card: Card,
    show_status: bool = False,
    serial_number: Optional[int] = None,
    owner_name: Optional[str] = None,
    state_label: Optional[str] = None,
) -> str:
    meta = get_rarity_meta(card.rarity)
    mtype = getattr(card, "media_type", "Anime") or "Anime"
    cstate = state_label or getattr(card, "card_state", "AVAILABLE") or "AVAILABLE"
    serial_str = f"#{serial_number}" if serial_number else "—"
    lines = [
        f"🎴 <b>Character:</b> {esc(card.character_name)}",
        f"📺 <b>{esc(mtype)}:</b> {esc(card.anime_name)}",
        f"⭐ <b>Rank:</b> {meta.emoji} {esc(card.rarity)}",
        f"💎 <b>Value:</b> {fmt_coins(card.base_value)} coins",
        f"🆔 <b>Serial:</b> <code>{serial_str}</code> | <code>{esc(card.card_code)}</code>",
        f"📦 <b>State:</b> {esc(cstate)}",
    ]
    if owner_name:
        lines.append(f"👤 <b>Owner:</b> {esc(owner_name)}")
    if card.is_event_card:
        total = f"{card.limited_copies} ta" if card.limited_copies else "cheklangan"
        lines.append(f"🎯 <b>EVENT KARTA</b> · cheklangan nusxa ({total})")
    if show_status:
        status_text = "🟢 Mavjud" if (card.is_available and not card.is_deleted) else "🔴 O'chirilgan/Nofaol"
        lines.append(f"📌 <b>Status:</b> {status_text} (Weight: {card.drop_weight})")
        if not card.is_event_card:
            lines.append(f"🔁 <b>Tashlanish:</b> {card.dropped_count}/{card.drop_limit}")
    return "\n".join(lines)


def format_card_about(card: Card) -> str:
    meta_dict = getattr(card, "metadata_json", None) or {}
    lines = [
        f"📖 <b>{esc(card.character_name)}</b> — batafsil ma’lumot",
        f"📺 {esc(getattr(card, 'media_type', 'Anime'))}: {esc(card.anime_name)}",
        "━━━━━━━━━━━━━━━",
    ]
    field_labels = [
        ("description", "📝 Tavsif"),
        ("birthday", "🎂 Tug‘ilgan kun"),
        ("age", "⏳ Yoshi"),
        ("height", "📏 Bo‘yi"),
        ("weight", "⚖️ Vazni"),
        ("personality", "🧠 Xarakter"),
        ("history", "📜 Tarixi"),
        ("abilities", "⚡ Qobiliyatlari"),
    ]
    any_data = False
    for key, label in field_labels:
        val = meta_dict.get(key)
        if val:
            any_data = True
            lines.append(f"<b>{label}:</b> {esc(val)}")
    aliases = getattr(card, "aliases", None) or []
    if aliases:
        any_data = True
        lines.append(f"<b>🏷 Aliaslar:</b> {esc(', '.join(str(a) for a in aliases))}")
    if not any_data:
        lines.append("<i>Qo‘shimcha tavsif hali kiritilmagan.</i>")
    return "\n".join(lines)


def event_tag(card: Card) -> str:
    return "🎯 [EVENT] " if getattr(card, "is_event_card", False) else ""


def format_drop_message(card: Card, active_event: Optional[Event] = None) -> str:
    """V2 yashirin drop dizayni: personaj ismi yashiriladi, reply orqali topiladi."""
    meta = get_rarity_meta(card.rarity)
    mtype = getattr(card, "media_type", "Anime") or "Anime"
    lines = [
        "🎴 <b>NEW CARD DROP!</b>",
        "",
        f"📺 <b>{esc(mtype)}:</b> {esc(card.anime_name)}",
        f"⭐ <b>Rank:</b> {meta.emoji} {esc(card.rarity)}",
        f"🔢 <b>Nusxa:</b> {card.dropped_count}/{card.drop_limit}",
        "❓ <b>Personaj kim?</b>",
    ]
    if active_event is not None:
        lines.append(f"🔥 <i>Event: {esc(active_event.name)}</i>")
    lines.extend(["", "✍️ Personaj ismini ushbu xabarga <b>REPLY</b> qilib yozing!"])
    return "\n".join(lines)


def format_drop_claimed_message(card: Card, winner: User, reward_coins: int = 0) -> str:
    meta = get_rarity_meta(card.rarity)
    coin_txt = f" (+{fmt_coins(reward_coins)} 🪙)" if reward_coins > 0 else ""
    return (
        f"🎴 <b>{esc(card.character_name)}</b>\n"
        f"📺 {esc(card.anime_name)} · {meta.emoji} {esc(card.rarity)}\n\n"
        f"🏆 {esc(winner.display_name)} to‘g‘ri topib kartani oldi!{coin_txt}"
    )


def format_profile_message(
    user: User,
    global_rank: int,
    group_rank: Optional[int],
    rarest_card: Optional[Card] = None,
    showcase_cards: Optional[List[Card]] = None,
    achievements_count: int = 0,
    season_points: Optional[int] = None,
) -> str:
    from models.base import ensure_utc, utcnow

    group_rank_str = f"#{group_rank}" if group_rank is not None else "—"
    frame_label = PROFILE_FRAMES.get(getattr(user, "equipped_frame", None) or "default", "▫️ Oddiy ramka")
    bg_label = PROFILE_BACKGROUNDS.get(getattr(user, "equipped_bg", None) or "classic", "🌌 Klassik")
    title_str = getattr(user, "equipped_title", None)
    badge_str = getattr(user, "equipped_badge", None) or ""

    vip_line = "▫️ Oddiy"
    vip_tier = getattr(user, "vip_tier", None)
    vip_until = getattr(user, "vip_until", None)
    if vip_tier and vip_until and ensure_utc(vip_until) > utcnow():
        vip_line = f"💎 <b>{esc(vip_tier)} VIP</b> ({ensure_utc(vip_until):%d.%m.%Y} gacha)"

    locked = getattr(user, "locked_coins", 0) or 0
    coins_line = f"🪙 Coins: <b>{fmt_coins(user.coins)}</b>"
    if locked > 0:
        coins_line += f" (🔒 {fmt_coins(locked)} band)"

    lines = [
        f"👤 <b>PROFILE V2</b> {esc(badge_str)}",
        f"🖼 {esc(frame_label)} · {esc(bg_label)}",
    ]
    if title_str:
        lines.append(f"🏷 Unvon: <b>{esc(title_str)}</b>")
    lines.extend(
        [
            "━━━━━━━━━━━━━━━",
            f"🆔 ID: <code>{user.telegram_id}</code>",
            f"👤 Foydalanuvchi: {esc(user.display_name)}",
            f"💎 Status: {vip_line}",
            coins_line,
            f"⚡ Level: <b>{user.level}</b> ({user.xp} XP)",
            f"📊 Progress: <code>{xp_progress_bar(user.xp)}</code>",
            f"🎴 Cards: <b>{user.total_cards}</b> | ✨ Unique: <b>{user.unique_cards}</b>",
            f"🏆 Global: <b>#{global_rank}</b> | Group: <b>{group_rank_str}</b>",
            f"🔥 Daily streak: <b>{user.daily_streak} kun</b> | 🏅 Yutuqlar: <b>{achievements_count}</b>",
            f"🧠 Quiz: <b>{getattr(user, 'quiz_wins', 0)}</b> · 🎲 Games: <b>{getattr(user, 'game_wins', 0)}</b> · 🤝 Trade: <b>{getattr(user, 'trade_count', 0)}</b>",
        ]
    )
    if season_points is not None:
        lines.append(f"🏆 Mavsum ballari: <b>{season_points}</b>")
    if rarest_card is not None:
        rmeta = get_rarity_meta(rarest_card.rarity)
        lines.append(f"💎 Eng noyob karta: {rmeta.emoji} <b>{esc(rarest_card.character_name)}</b> ({esc(rarest_card.rarity)})")
    if showcase_cards:
        sc = ", ".join(esc(c.character_name) for c in showcase_cards[:3])
        lines.append(f"🖼 Showcase: <i>{sc}</i>")
    return "\n".join(lines)


def format_collection_summary(
    user: User,
    anime_counts: List[tuple[str, int]],
    rarity_counts: Dict[str, int],
    favorites: List[tuple[str, int]],
) -> str:
    lines = [
        f"👤 <b>KOLLEKSIYA — {esc(user.display_name)}</b>\n",
        f"🎴 Cards: <b>{user.total_cards}</b>",
        f"✨ Unique: <b>{user.unique_cards}</b>\n",
    ]
    if favorites:
        lines.append("❤️ <b>Sevimlilar / Top qahramonlar:</b>")
        for char_name, qty in favorites[:5]:
            lines.append(f"• {esc(char_name)} × {qty}")
        lines.append("")
    if anime_counts:
        lines.append("📺 <b>Media bo‘yicha:</b>")
        for anime_name, qty in anime_counts[:8]:
            lines.append(f"• {esc(anime_name)} — {qty}")
        lines.append("")
    lines.append("⭐ <b>Rank bo‘yicha:</b>")
    for rarity_name, qty in rarity_counts.items():
        meta = get_rarity_meta(rarity_name)
        lines.append(f"{meta.emoji} {esc(rarity_name)} — {qty}")
    return "\n".join(lines)


def format_group_settings(group: Group) -> str:
    status = "🟢 Yoqilgan" if group.drop_enabled else "🔴 O'chirilgan"
    ban_str = "🚫 Banned" if getattr(group, "is_banned", False) else "✅ Faol"
    return (
        f"⚙️ <b>GURUH SOZLAMALARI</b>\n\n"
        f"🏷 Guruh: <b>{esc(group.title)}</b>\n"
        f"🆔 Chat ID: <code>{group.telegram_chat_id}</code>\n"
        f"🛡 Holat: {ban_str}\n"
        f"👥 A'zolar soni: <b>{group.member_count if group.member_count is not None else '—'}</b>\n"
        f"🎴 Kunlik drop: <b>{group.daily_drop_count}</b> karta/kun "
        f"({'🤖 avto' if group.auto_drop_count else '✋ qo‘lda'})\n"
        f"📡 Drop holati: {status}\n"
        f"🪙 Coin multiplikator: <b>{group.coin_multiplier}x</b>\n"
        f"🌐 Til: <b>{esc(group.language.upper())}</b>"
    )


def format_event_message(card: Card, event, tz_name: str = "Asia/Tashkent", state: str = "active") -> str:
    from zoneinfo import ZoneInfo
    from models.base import ensure_utc, utcnow

    meta = get_rarity_meta(card.rarity)
    ends_utc = ensure_utc(event.ends_at)
    ends_local = ends_utc.astimezone(ZoneInfo(tz_name))
    remaining = max(0, int(event.total_copies) - int(event.sold_count))

    secs_left = max(0, int((ends_utc - utcnow()).total_seconds()))
    hours_left = secs_left // 3600
    mins_left = (secs_left % 3600) // 60
    dur_str = f"{hours_left} soat {mins_left} daq." if hours_left > 0 else f"{mins_left} daqiqa"

    prices = []
    if event.price_coins:
        prices.append(f"🪙 <b>{fmt_coins(event.price_coins)}</b> coins")
    if event.price_stars:
        prices.append(f"⭐ <b>{fmt_coins(event.price_stars)}</b> Stars")

    if state == "sold_out":
        title = "🔥 <b>EVENT KARTASI SOTILIB BO‘LDI!</b>"
    elif state == "ended":
        title = "⌛ <b>EVENT TUGADI</b>"
    else:
        title = "🎯 <b>YANGI LIMITLI EVENT BOSHLANDI!</b> 🎯"

    lines = [
        title,
        "━━━━━━━━━━━━━━━",
        f"🎴 <b>{esc(card.character_name)}</b>",
        f"📺 {esc(card.anime_name)}",
        f"⭐ Rank: {meta.emoji} <b>{esc(card.rarity)}</b>",
        "",
        f"📦 Cheklangan nusxa: <b>{event.total_copies} ta</b>",
    ]
    if state == "active":
        lines.extend(
            [
                f"✅ Qolgan nusxa: <b>{remaining} ta</b>",
                "✨ <i>Bu karta boshqa hech kimda yo‘q va guruhlarga tashlanmaydi!</i>",
                f"💳 Narxi: {'  yoki  '.join(prices)}",
                f"⏳ Amal qiladi: <b>{ends_local:%d.%m.%Y %H:%M}</b> gacha ({dur_str})",
                "━━━━━━━━━━━━━━━",
                "👇 Pastdagi tugma yoki /event orqali oling! (Har bir odamga 1 ta nusxa)",
            ]
        )
    else:
        lines.append(f"🏁 Sotilgan: <b>{event.sold_count}/{event.total_copies}</b>")
        lines.append("━━━━━━━━━━━━━━━")
    return "\n".join(lines)
