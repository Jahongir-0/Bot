import html
from typing import Dict, List, Optional

from models.card import Card
from models.event import Event
from models.group import Group
from models.user import User
from utils.constants import PROFILE_BACKGROUNDS, PROFILE_FRAMES, get_rarity_meta


def esc(value: object) -> str:
    return "" if value is None else html.escape(str(value))


LINE = "━━━━━━━━━━━━━━━"


def header(title: str) -> str:
    """Bir xil sarlavha: qalin nom + chiziq."""
    return f"{title}\n{LINE}"


def level_badge(level: int) -> str:
    """5-darajadan ⭐, 10-darajadan 👑 belgisi (karta nomi yonida)."""
    level = int(level or 1)
    return "👑" if level >= 10 else ("⭐" if level >= 5 else "")


def level_frame(level: int):
    """(yuqori chiziq, pastki chiziq, nishon matni) yoki None. 5-daraja: yulduzli, 10-daraja: afsona ramkasi."""
    level = int(level or 1)
    if level >= 10:
        line = "👑✨━━━━━━━━━━━━✨👑"
        return line, line, "👑 <b>AFSONA KARTA</b> — eng yuqori daraja"
    if level >= 5:
        line = "⭐━━━━━━━━━━━━━━⭐"
        return line, line, "⭐ <b>YULDUZLI KARTA</b> — 5-daraja belgisi"
    return None


def fmt_coins(amount: int) -> str:
    return f"{int(amount):,}"


def xp_progress_bar(xp: int, xp_per_level: int = 250) -> str:
    if xp_per_level <= 0:
        xp_per_level = 250
    into_lvl = xp % xp_per_level
    filled = int((into_lvl / xp_per_level) * 10)
    return "█" * filled + "░" * (10 - filled) + f" {into_lvl}/{xp_per_level}"


_STATE_LABELS = {
    "AVAILABLE": "🟢 Mavjud",
    "OWNED": "👤 Egasi bor",
    "MARKET": "🛒 Sotuvda",
    "AUCTION": "🔨 Auksionda",
    "EVENT": "🎯 Event",
    "TRADE": "🤝 Almashuvda",
    "LOCKED": "🔒 Band",
    "UNRELEASED": "⚪ Hali chiqmagan",
    "DELETED": "🗑 O‘chirilgan",
}


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
    cstate = _STATE_LABELS.get(str(cstate).upper(), esc(cstate))
    serial_str = f"#{serial_number}" if serial_number else "—"
    lines = [
        f"🎴 <b>{esc(card.character_name)}</b>",
        f"📺 {esc(mtype)}: <b>{esc(card.anime_name)}</b>",
        f"⭐ Rank: {meta.emoji} <b>{esc(meta.name)}</b>",
        f"💎 Qiymati: <b>{fmt_coins(card.base_value)}</b> 🪙",
        f"🆔 Serial: <code>{serial_str}</code> · <code>{esc(card.card_code)}</code>",
        f"📦 Holati: {cstate}",
    ]
    if owner_name:
        lines.append(f"👤 Egasi: <b>{esc(owner_name)}</b>")
    if card.is_event_card:
        total = f"{card.limited_copies} ta" if card.limited_copies else "cheklangan"
        lines.append(f"🎯 <b>EVENT KARTA</b> · cheklangan nusxa ({total})")
    if show_status:
        status_text = "🟢 Mavjud" if (card.is_available and not card.is_deleted) else "🔴 O'chirilgan/Nofaol"
        lines.append(f"📌 Holat: {status_text} · tushish og‘irligi: {card.drop_weight}")
        if not card.is_event_card:
            lines.append(f"🔁 Guruhlarga tashlangan: <b>{card.dropped_count}/{card.drop_limit}</b>")
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
        "🎴 <b>YANGI KARTA TASHLANDI!</b>",
        LINE,
        f"📺 <b>{esc(mtype)}:</b> {esc(card.anime_name)}",
        f"⭐ <b>Rank:</b> {meta.emoji} <b>{esc(meta.name)}</b>",
        f"🔢 <b>Nusxa:</b> {card.dropped_count}/{card.drop_limit}",
        "❓ <b>Personaj kim?</b>",
    ]
    if active_event is not None:
        lines.append(f"🔥 <i>Event: {esc(active_event.name)}</i>")
    lines.extend([LINE, "✍️ Personaj ismini ushbu xabarga <b>REPLY</b> qilib yozing!"])
    return "\n".join(lines)


def format_drop_claimed_message(card: Card, winner: User, reward_coins: int = 0) -> str:
    meta = get_rarity_meta(card.rarity)
    coin_txt = f" (+{fmt_coins(reward_coins)} 🪙)" if reward_coins > 0 else ""
    return (
        f"🎴 <b>{esc(card.character_name)}</b>\n"
        f"📺 {esc(card.anime_name)} · {meta.emoji} {esc(meta.name)}\n\n"
        f"🏆 {esc(winner.display_name)} to‘g‘ri topib kartani oldi!{coin_txt}"
    )


_FRAME_STYLE = {
    # kalit: (yuqori chiziq, pastki chiziq, ramka emojisi)
    "default": ("┏━━━━━━━━━━━━━━━┓", "┗━━━━━━━━━━━━━━━┛", "▫️"),
    "fire": ("🔥━━━━━━━━━━━━━━━🔥", "🔥━━━━━━━━━━━━━━━🔥", "🔥"),
    "royal": ("👑━━━━━━━━━━━━━━━👑", "👑━━━━━━━━━━━━━━━👑", "👑"),
    "cyber": ("⚡━━━━━━━━━━━━━━━⚡", "⚡━━━━━━━━━━━━━━━⚡", "⚡"),
    "sakura": ("🌸━━━━━━━━━━━━━━━🌸", "🌸━━━━━━━━━━━━━━━🌸", "🌸"),
    "diamond": ("💎━━━━━━━━━━━━━━━💎", "💎━━━━━━━━━━━━━━━💎", "💎"),
}


def _level_bar(xp: int, per_level: int = 250) -> str:
    into = xp % per_level
    filled = int(into / per_level * 10)
    return "▰" * filled + "▱" * (10 - filled) + f" {int(into / per_level * 100)}%"


def format_profile_message(
    user: User,
    global_rank: int,
    group_rank: Optional[int],
    rarest_card: Optional[Card] = None,
    showcase_cards: Optional[List[Card]] = None,
    achievements_count: int = 0,
    season_points: Optional[int] = None,
    total_power: Optional[int] = None,
) -> str:
    from models.base import ensure_utc, utcnow

    frame_key = getattr(user, "equipped_frame", None) or "default"
    top, bottom, frame_emoji = _FRAME_STYLE.get(frame_key, _FRAME_STYLE["default"])
    bg_label = PROFILE_BACKGROUNDS.get(getattr(user, "equipped_bg", None) or "classic", "🌌 Klassik")
    title_str = getattr(user, "equipped_title", None)
    badge_str = getattr(user, "equipped_badge", None) or ""

    vip_tier = getattr(user, "vip_tier", None)
    vip_until = getattr(user, "vip_until", None)
    is_vip = bool(vip_tier and vip_until and ensure_utc(vip_until) > utcnow())
    if is_vip:
        top, bottom = "💎✨━━━━━━━━━━━━━💎✨", "💎✨━━━━━━━━━━━━━💎✨"
        status = f"💎 <b>{esc(vip_tier)} VIP</b> · {ensure_utc(vip_until):%d.%m.%Y} gacha"
    else:
        status = "▫️ Oddiy a’zo  <i>(💎 VIP olib ko‘proq imkoniyat oching)</i>"

    name_line = f"{frame_emoji} <b>{esc(user.display_name)}</b> {esc(badge_str)}".rstrip()
    lines = [top, name_line]
    if title_str:
        lines.append(f"🏷 <i>{esc(title_str)}</i>")
    lines += [bottom, status, f"🆔 <code>{user.telegram_id}</code> · {esc(bg_label)}", ""]

    locked = getattr(user, "locked_coins", 0) or 0
    coins = f"🪙 <b>{fmt_coins(user.coins)}</b> coin"
    if locked > 0:
        coins += f"  <i>(🔒 {fmt_coins(locked)} band)</i>"
    lines += [
        "💰 <b>HAMYON</b>",
        coins,
        "",
        "📈 <b>DARAJA</b>",
        f"⚡ <b>{user.level}</b>-daraja · {fmt_coins(user.xp)} XP",
        f"<code>{_level_bar(user.xp)}</code>",
        "",
        "🎴 <b>KOLLEKSIYA</b>",
        f"🎴 Jami: <b>{user.total_cards}</b>  ·  ✨ Noyob: <b>{user.unique_cards}</b>",
    ]
    if total_power is not None:
        lines.append(f"💪 Umumiy kuch: <b>{fmt_coins(total_power)}</b>")
    if rarest_card is not None:
        rmeta = get_rarity_meta(rarest_card.rarity)
        lines.append(f"💠 Eng noyob: {rmeta.emoji} <b>{esc(rarest_card.character_name)}</b> · {esc(rmeta.name)}")
    if showcase_cards:
        lines.append("🖼 Vitrina: " + " · ".join(f"<b>{esc(c.character_name)}</b>" for c in showcase_cards[:3]))

    group_part = f"  ·  👥 Guruh <b>#{group_rank}</b>" if group_rank is not None else ""
    lines += [
        "",
        "🏆 <b>YUTUQLAR</b>",
        f"🌍 Global <b>#{global_rank}</b>{group_part}",
        f"🔥 Kunlik seriya: <b>{user.daily_streak}</b> kun  ·  🏅 Nishonlar: <b>{achievements_count}</b>",
    ]
    if season_points is not None:
        lines.append(f"🏆 Mavsum ballari: <b>{season_points}</b>")
    lines += [
        "",
        "🎮 <b>FAOLLIK</b>",
        f"🧠 Viktorina: <b>{getattr(user, 'quiz_wins', 0)}</b>  ·  🎲 O‘yin: <b>{getattr(user, 'game_wins', 0)}</b>  ·  🤝 Almashuv: <b>{getattr(user, 'trade_count', 0)}</b>",
    ]
    return "\n".join(lines)


def format_collection_summary(
    user: User,
    anime_counts: List[tuple[str, int]],
    rarity_counts: Dict[str, int],
    favorites: List[tuple[str, int]],
) -> str:
    lines = [
        header("🎴 <b>KARTALARIM</b>"),
        f"🎴 Jami: <b>{user.total_cards}</b>  ·  ✨ Noyob: <b>{user.unique_cards}</b>",
    ]
    if user.total_cards == 0:
        lines += [
            "",
            "📭 Hali kartangiz yo‘q.",
            "💡 Guruhda tashlangan karta xabariga <b>REPLY</b> qilib personaj ismini yozing yoki 🎁 kunlik bonusni oling!",
        ]
        return "\n".join(lines)

    nonzero = {k: v for k, v in rarity_counts.items() if v}
    if nonzero:
        lines += ["", "⭐ <b>Rank bo‘yicha</b>"]
        for rarity_name, qty in nonzero.items():
            meta = get_rarity_meta(rarity_name)
            lines.append(f"{meta.emoji} <b>{esc(meta.name)}</b>: {qty} ta")
    if anime_counts:
        lines += ["", "📺 <b>Eng ko‘p (asar bo‘yicha)</b>"]
        for anime_name, qty in anime_counts[:5]:
            lines.append(f"• {esc(anime_name)} — <b>{qty}</b>")
    if favorites:
        lines += ["", "❤️ <b>Sevimli personajlar</b>"]
        for char_name, qty in favorites[:3]:
            lines.append(f"• {esc(char_name)} × {qty}")
    lines += [LINE, "👇 Kartalarni ko‘rish usulini tanlang:"]
    return "\n".join(lines)


def format_group_settings(group: Group) -> str:
    status = "🟢 Yoqilgan" if group.drop_enabled else "🔴 O'chirilgan"
    ban_str = "🚫 Bloklangan" if getattr(group, "is_banned", False) else "✅ Faol"
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
        f"⭐ Rank: {meta.emoji} <b>{esc(meta.name)}</b>",
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
