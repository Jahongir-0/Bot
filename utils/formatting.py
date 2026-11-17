import html
from typing import Dict, List, Optional

from models.card import Card
from models.event import Event
from models.group import Group
from models.user import User
from utils.constants import get_rarity_meta


def esc(value: object) -> str:
    return "" if value is None else html.escape(str(value))


def fmt_coins(amount: int) -> str:
    return f"{amount:,}"


def format_card_block(card: Card, show_status: bool = False) -> str:
    meta = get_rarity_meta(card.rarity)
    lines = [
        f"🎴 <b>Character:</b> {esc(card.character_name)}",
        f"📺 <b>Anime:</b> {esc(card.anime_name)}",
        f"⭐ <b>Rarity:</b> {meta.emoji} {esc(card.rarity)}",
        f"💰 <b>Value:</b> {fmt_coins(card.base_value)} coins",
        f"🆔 <b>Card ID:</b> <code>{esc(card.card_code)}</code> | 🏅 Rank #{card.rank}",
    ]
    if card.is_event_card:
        total = f"{card.limited_copies} ta" if card.limited_copies else "cheklangan"
        lines.append(f"🎯 <b>EVENT KARTA</b> — cheklangan nusxa ({total})")
    if show_status:
        status_text = "🟢 Mavjud" if (card.is_available and not card.is_deleted) else "🔴 O'chirilgan/Nofaol"
        lines.append(f"📌 <b>Status:</b> {status_text} (Weight: {card.drop_weight})")
        if not card.is_event_card:
            lines.append(f"🔁 <b>Tashlanish:</b> {card.dropped_count}/{card.drop_limit}")
    return "\n".join(lines)


def event_tag(card: Card) -> str:
    """Kolleksiya ro'yxatlarida event/cheklangan kartani belgilash uchun."""
    return "🎯 " if getattr(card, "is_event_card", False) else ""


def format_drop_message(card: Card, active_event: Optional[Event] = None) -> str:
    meta = get_rarity_meta(card.rarity)
    lines = [
        "🎴 <b>YANGI KARTA!</b>",
        "",
        f"👤 <b>{esc(card.character_name)}</b>",
        f"📺 {esc(card.anime_name)}",
        f"⭐ {meta.emoji} {esc(card.rarity)}",
        f"💰 Qiymati: {fmt_coins(card.base_value)} coins",
        f"🔢 Nusxa: <b>{card.dropped_count}/{card.drop_limit}</b>",
    ]
    if active_event is not None:
        lines.append(f"🔥 <i>Event: {esc(active_event.name)} ({active_event.coin_multiplier}x coins)</i>")
    lines.extend(["", "🎁 Birinchi bo‘lib olgan foydalanuvchiga beriladi!"])
    return "\n".join(lines)


def format_drop_claimed_message(card: Card, winner: User, reward_coins: int) -> str:
    meta = get_rarity_meta(card.rarity)
    return (
        f"🎴 <b>{esc(card.character_name)}</b>\n"
        f"📺 {esc(card.anime_name)} · {meta.emoji} {esc(card.rarity)}\n\n"
        f"🏆 {esc(winner.display_name)} kartani oldi! (+{fmt_coins(reward_coins)} 🪙)"
    )


def format_profile_message(
    user: User,
    global_rank: int,
    group_rank: Optional[int],
) -> str:
    group_rank_str = f"#{group_rank}" if group_rank is not None else "—"
    return (
        "👤 <b>USER PROFILE</b>\n\n"
        f"🆔 ID: <code>{user.telegram_id}</code>\n"
        f"👤 Foydalanuvchi: {esc(user.display_name)}\n"
        f"🎴 Total cards: <b>{user.total_cards}</b>\n"
        f"✨ Unique cards: <b>{user.unique_cards}</b>\n"
        f"🪙 Coins: <b>{fmt_coins(user.coins)}</b>\n"
        f"⚡ Level: <b>{user.level}</b> ({user.xp} XP)\n"
        f"🏆 Global rank: <b>#{global_rank}</b>\n"
        f"🏆 Group rank: <b>{group_rank_str}</b>\n"
        f"🔥 Daily streak: <b>{user.daily_streak} kun</b>"
    )


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
        lines.append("📺 <b>Anime bo‘yicha:</b>")
        for anime_name, qty in anime_counts[:8]:
            lines.append(f"• {esc(anime_name)} — {qty}")
        lines.append("")

    lines.append("⭐ <b>Rarity bo‘yicha:</b>")
    for rarity_name, qty in rarity_counts.items():
        meta = get_rarity_meta(rarity_name)
        lines.append(f"{meta.emoji} {esc(rarity_name)} — {qty}")

    return "\n".join(lines)


def format_group_settings(group: Group) -> str:
    status = "🟢 Yoqilgan" if group.drop_enabled else "🔴 O'chirilgan"
    return (
        f"⚙️ <b>GURUH SOZLAMALARI</b>\n\n"
        f"🏷 Guruh: <b>{esc(group.title)}</b>\n"
        f"🆔 Chat ID: <code>{group.telegram_chat_id}</code>\n"
        f"👥 A'zolar soni: <b>{group.member_count if group.member_count is not None else '—'}</b>\n"
        f"🎴 Kunlik drop: <b>{group.daily_drop_count}</b> karta/kun "
        f"({'🤖 avto: a’zolar soniga qarab' if group.auto_drop_count else '✋ qo‘lda'})\n"
        f"📡 Drop holati: {status}\n"
        f"🪙 Coin multiplikator: <b>{group.coin_multiplier}x</b>\n"
        f"🌐 Til: <b>{esc(group.language.upper())}</b>"
    )


def format_event_message(card: Card, event, tz_name: str = "Asia/Tashkent", state: str = "active") -> str:
    """Limitli karta eventi e'loni (guruhlarga va /event bo'limida)."""
    from datetime import timezone as _tz
    from zoneinfo import ZoneInfo

    from models.base import ensure_utc

    meta = get_rarity_meta(card.rarity)
    ends_local = ensure_utc(event.ends_at).astimezone(ZoneInfo(tz_name))
    remaining = max(0, int(event.total_copies) - int(event.sold_count))

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
        title = "🎯 <b>LIMITLI EVENT BOSHLANDI!</b> 🎯"

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
        lines.append(f"✅ Qoldi: <b>{remaining} ta</b>")
        lines.append("🔒 Bu karta guruhlarga <b>tashlanmaydi</b> — faqat shu event orqali olinadi!")
        lines.append(f"💳 Narxi: {'  yoki  '.join(prices)}")
        lines.append(f"⏳ Tugaydi: <b>{ends_local:%d.%m.%Y %H:%M}</b>")
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("👇 Pastdagi tugma orqali oling! (Bir kishi — bitta nusxa)")
    else:
        lines.append(f"🏁 Sotilgan: <b>{event.sold_count}/{event.total_copies}</b>")
        lines.append("━━━━━━━━━━━━━━━")
    return "\n".join(lines)

