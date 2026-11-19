import html
from typing import Dict, List, Optional

from models.card import Card
from models.event import Event
from models.group import Group
from models.user import User
from utils.constants import PROFILE_BACKGROUNDS, PROFILE_FRAMES, get_rarity_meta
from utils.i18n import normalize_lang


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
    lang: str = "uz",
) -> str:
    l = normalize_lang(lang)
    meta = get_rarity_meta(card.rarity)
    mtype = getattr(card, "media_type", "Anime") or "Anime"
    cstate = state_label or getattr(card, "card_state", "AVAILABLE") or "AVAILABLE"
    serial_str = f"#{serial_number}" if serial_number else "—"
    lbl = {
        "uz": dict(char="Personaj", rank="Rank", val="Qiymat", ser="Serial", state="Holat", owner="Egasi", evt="EVENT KARTA", lim="cheklangan nusxa", st="Status", on="🟢 Mavjud", off="🔴 Nofaol", drp="Tashlanish"),
        "en": dict(char="Character", rank="Rank", val="Value", ser="Serial", state="State", owner="Owner", evt="EVENT CARD", lim="limited edition", st="Status", on="🟢 Available", off="🔴 Inactive", drp="Drops"),
        "ru": dict(char="Персонаж", rank="Ранг", val="Цена", ser="Серия", state="Статус", owner="Владелец", evt="ИВЕНТ КАРТА", lim="лимитная копия", st="Статус", on="🟢 Доступна", off="🔴 Неактивна", drp="Выпадения"),
    }[l]
    lines = [
        f"🎴 <b>{lbl['char']}:</b> {esc(card.character_name)}",
        f"📺 <b>{esc(mtype)}:</b> {esc(card.anime_name)}",
        f"⭐ <b>{lbl['rank']}:</b> {meta.emoji} {esc(card.rarity)}",
        f"💎 <b>{lbl['val']}:</b> {fmt_coins(card.base_value)} coins",
        f"🆔 <b>{lbl['ser']}:</b> <code>{serial_str}</code> | <code>{esc(card.card_code)}</code>",
        f"📦 <b>{lbl['state']}:</b> {esc(cstate)}",
    ]
    if owner_name:
        lines.append(f"👤 <b>{lbl['owner']}:</b> {esc(owner_name)}")
    if card.is_event_card:
        total = f"{card.limited_copies}" if card.limited_copies else lbl["lim"]
        lines.append(f"🎯 <b>{lbl['evt']}</b> · {lbl['lim']} ({total})")
    if show_status:
        status_text = lbl["on"] if (card.is_available and not card.is_deleted) else lbl["off"]
        lines.append(f"📌 <b>{lbl['st']}:</b> {status_text} (Weight: {card.drop_weight})")
        if not card.is_event_card:
            lines.append(f"🔁 <b>{lbl['drp']}:</b> {card.dropped_count}/{card.drop_limit}")
    return "\n".join(lines)


def format_card_about(card: Card, lang: str = "uz") -> str:
    l = normalize_lang(lang)
    meta_dict = getattr(card, "metadata_json", None) or {}
    sub = {"uz": "batafsil ma’lumot", "en": "card details", "ru": "подробнее о карте"}[l]
    lines = [
        f"📖 <b>{esc(card.character_name)}</b> — {sub}",
        f"📺 {esc(getattr(card, 'media_type', 'Anime'))}: {esc(card.anime_name)}",
        "━━━━━━━━━━━━━━━",
    ]
    labels = {
        "uz": [
            ("description", "📝 Tavsif"), ("birthday", "🎂 Tug‘ilgan kun"), ("age", "⏳ Yoshi"),
            ("height", "📏 Bo‘yi"), ("weight", "⚖️ Vazni"), ("personality", "🧠 Xarakter"),
            ("history", "📜 Tarixi"), ("abilities", "⚡ Qobiliyatlari"),
        ],
        "en": [
            ("description", "📝 Description"), ("birthday", "🎂 Birthday"), ("age", "⏳ Age"),
            ("height", "📏 Height"), ("weight", "⚖️ Weight"), ("personality", "🧠 Personality"),
            ("history", "📜 History"), ("abilities", "⚡ Abilities"),
        ],
        "ru": [
            ("description", "📝 Описание"), ("birthday", "🎂 День рождения"), ("age", "⏳ Возраст"),
            ("height", "📏 Рост"), ("weight", "⚖️ Вес"), ("personality", "🧠 Характер"),
            ("history", "📜 История"), ("abilities", "⚡ Способности"),
        ],
    }[l]
    any_data = False
    for key, label in labels:
        val = meta_dict.get(key)
        if val:
            any_data = True
            lines.append(f"<b>{label}:</b> {esc(val)}")
    aliases = getattr(card, "aliases", None) or []
    if aliases:
        any_data = True
        al_lbl = {"uz": "🏷 Aliaslar", "en": "🏷 Aliases", "ru": "🏷 Алиасы"}[l]
        lines.append(f"<b>{al_lbl}:</b> {esc(', '.join(str(a) for a in aliases))}")
    if not any_data:
        none_txt = {
            "uz": "<i>Qo‘shimcha tavsif hali kiritilmagan.</i>",
            "en": "<i>No additional description yet.</i>",
            "ru": "<i>Дополнительное описание пока не добавлено.</i>",
        }[l]
        lines.append(none_txt)
    return "\n".join(lines)


def event_tag(card: Card) -> str:
    return "🎯 [EVENT] " if getattr(card, "is_event_card", False) else ""


def format_drop_message(card: Card, active_event: Optional[Event] = None, lang: str = "uz") -> str:
    """V2 yashirin drop dizayni: personaj ismi yashiriladi, reply orqali topiladi."""
    meta = get_rarity_meta(card.rarity)
    mtype = getattr(card, "media_type", "Anime") or "Anime"
    lines = [
        "🎴 <b>NEW CARD DROP!</b>",
        "",
        f"📺 <b>{esc(mtype)}:</b> {esc(card.anime_name)}",
        f"⭐ <b>Rank:</b> {meta.emoji} {esc(card.rarity)}",
        f"🔢 <b>Drop:</b> {card.dropped_count}/{card.drop_limit}",
        "❓ <b>Who is this character? / Personaj kim?</b>",
    ]
    if active_event is not None:
        lines.append(f"🔥 <i>Event: {esc(active_event.name)}</i>")
    lines.extend(["", "✍️ <b>REPLY</b> with the character name! / Personaj ismini <b>REPLY</b> qilib yozing!"])
    return "\n".join(lines)


def format_drop_claimed_message(card: Card, winner: User, reward_coins: int = 0, lang: str = "uz") -> str:
    meta = get_rarity_meta(card.rarity)
    coin_txt = f" (+{fmt_coins(reward_coins)} 🪙)" if reward_coins > 0 else ""
    return (
        f"🎴 <b>{esc(card.character_name)}</b>\n"
        f"📺 {esc(card.anime_name)} · {meta.emoji} {esc(card.rarity)}\n\n"
        f"🏆 {esc(winner.display_name)} claimed the card!{coin_txt}"
    )


def format_profile_message(
    user: User,
    global_rank: int,
    group_rank: Optional[int],
    rarest_card: Optional[Card] = None,
    showcase_cards: Optional[List[Card]] = None,
    achievements_count: int = 0,
    season_points: Optional[int] = None,
    lang: str = "uz",
) -> str:
    from models.base import ensure_utc, utcnow

    l = normalize_lang(lang)
    group_rank_str = f"#{group_rank}" if group_rank is not None else "—"
    frame_label = PROFILE_FRAMES.get(getattr(user, "equipped_frame", None) or "default", "▫️ Default")
    bg_label = PROFILE_BACKGROUNDS.get(getattr(user, "equipped_bg", None) or "classic", "🌌 Classic")
    title_str = getattr(user, "equipped_title", None)
    badge_str = getattr(user, "equipped_badge", None) or ""

    vip_line = {"uz": "▫️ Oddiy", "en": "▫️ Standard", "ru": "▫️ Обычный"}[l]
    vip_tier = getattr(user, "vip_tier", None)
    vip_until = getattr(user, "vip_until", None)
    if vip_tier and vip_until and ensure_utc(vip_until) > utcnow():
        until_w = {"uz": "gacha", "en": "until", "ru": "до"}[l]
        if l == "uz":
            vip_line = f"💎 <b>{esc(vip_tier)} VIP</b> ({ensure_utc(vip_until):%d.%m.%Y} {until_w})"
        else:
            vip_line = f"💎 <b>{esc(vip_tier)} VIP</b> ({until_w} {ensure_utc(vip_until):%d.%m.%Y})"

    locked = getattr(user, "locked_coins", 0) or 0
    locked_w = {"uz": "band", "en": "locked", "ru": "в залоге"}[l]
    coins_lbl = {"uz": "Coinlar", "en": "Coins", "ru": "Монеты"}[l]
    coins_line = f"🪙 {coins_lbl}: <b>{fmt_coins(user.coins)}</b>"
    if locked > 0:
        coins_line += f" (🔒 {fmt_coins(locked)} {locked_w})"

    lbl = {
        "uz": dict(
            hdr="SHAXSIY PROFIL", title="Unvon", usr="Foydalanuvchi", st="Status",
            lvl="Daraja", prog="Progress", cards="Kartalar", uniq="Noyob",
            glob="Global", grp="Guruh", stk="Daily streak", days="kun",
            ach="Yutuqlar", seas="Mavsum ballari", rare="Eng noyob karta", show="Showcase",
        ),
        "en": dict(
            hdr="USER PROFILE", title="Title", usr="Player", st="Status",
            lvl="Level", prog="Progress", cards="Cards", uniq="Unique",
            glob="Global", grp="Group", stk="Daily streak", days="days",
            ach="Achievements", seas="Season points", rare="Rarest card", show="Showcase",
        ),
        "ru": dict(
            hdr="ПРОФИЛЬ ИГРОКА", title="Титул", usr="Игрок", st="Статус",
            lvl="Уровень", prog="Прогресс", cards="Карты", uniq="Уник.",
            glob="Глобал", grp="Группа", stk="Серия дней", days="дн.",
            ach="Достижения", seas="Очки сезона", rare="Редчайшая карта", show="Витрина",
        ),
    }[l]

    lines = [
        f"👤 <b>{lbl['hdr']}</b> {esc(badge_str)}",
        f"🖼 {esc(frame_label)} · {esc(bg_label)}",
    ]
    if title_str:
        lines.append(f"🏷 {lbl['title']}: <b>{esc(title_str)}</b>")
    lines.extend(
        [
            "━━━━━━━━━━━━━━━",
            f"🆔 ID: <code>{user.telegram_id}</code>",
            f"👤 {lbl['usr']}: {esc(user.display_name)}",
            f"💎 {lbl['st']}: {vip_line}",
            coins_line,
            f"⚡ {lbl['lvl']}: <b>{user.level}</b> ({user.xp} XP)",
            f"📊 {lbl['prog']}: <code>{xp_progress_bar(user.xp)}</code>",
            f"🎴 {lbl['cards']}: <b>{user.total_cards}</b> | ✨ {lbl['uniq']}: <b>{user.unique_cards}</b>",
            f"🏆 {lbl['glob']}: <b>#{global_rank}</b> | {lbl['grp']}: <b>{group_rank_str}</b>",
            f"🔥 {lbl['stk']}: <b>{user.daily_streak} {lbl['days']}</b> | 🏅 {lbl['ach']}: <b>{achievements_count}</b>",
            f"🧠 Quiz: <b>{getattr(user, 'quiz_wins', 0)}</b> · 🎲 Games: <b>{getattr(user, 'game_wins', 0)}</b> · 🤝 Trade: <b>{getattr(user, 'trade_count', 0)}</b>",
        ]
    )
    if season_points is not None:
        lines.append(f"🏆 {lbl['seas']}: <b>{season_points}</b>")
    if rarest_card is not None:
        rmeta = get_rarity_meta(rarest_card.rarity)
        lines.append(f"💎 {lbl['rare']}: {rmeta.emoji} <b>{esc(rarest_card.character_name)}</b> ({esc(rarest_card.rarity)})")
    if showcase_cards:
        sc = ", ".join(esc(c.character_name) for c in showcase_cards[:3])
        lines.append(f"🖼 {lbl['show']}: <i>{sc}</i>")
    return "\n".join(lines)


def format_collection_summary(
    user: User,
    anime_counts: List[tuple[str, int]],
    rarity_counts: Dict[str, int],
    favorites: List[tuple[str, int]],
    lang: str = "uz",
) -> str:
    l = normalize_lang(lang)
    lbl = {
        "uz": dict(hdr="KOLLEKSIYA", cards="Jami kartalar", uniq="Noyob kartalar", fav="Sevimli qahramonlar", media="Media bo‘yicha", rank="Rank bo‘yicha"),
        "en": dict(hdr="COLLECTION", cards="Total cards", uniq="Unique cards", fav="Favorite characters", media="By Media", rank="By Rank"),
        "ru": dict(hdr="КОЛЛЕКЦИЯ", cards="Всего карт", uniq="Уникальных", fav="Избранные герои", media="По медиа", rank="По рангу"),
    }[l]
    lines = [
        f"👤 <b>{lbl['hdr']} — {esc(user.display_name)}</b>\n",
        f"🎴 {lbl['cards']}: <b>{user.total_cards}</b>",
        f"✨ {lbl['uniq']}: <b>{user.unique_cards}</b>\n",
    ]
    if favorites:
        lines.append(f"❤️ <b>{lbl['fav']}:</b>")
        for char_name, qty in favorites[:5]:
            lines.append(f"• {esc(char_name)} × {qty}")
        lines.append("")
    if anime_counts:
        lines.append(f"📺 <b>{lbl['media']}:</b>")
        for anime_name, qty in anime_counts[:8]:
            lines.append(f"• {esc(anime_name)} — {qty}")
        lines.append("")
    lines.append(f"⭐ <b>{lbl['rank']}:</b>")
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


def format_event_message(card: Card, event, tz_name: str = "Asia/Tashkent", state: str = "active", lang: str = "uz") -> str:
    from zoneinfo import ZoneInfo
    from models.base import ensure_utc, utcnow

    l = normalize_lang(lang)
    meta = get_rarity_meta(card.rarity)
    ends_utc = ensure_utc(event.ends_at)
    ends_local = ends_utc.astimezone(ZoneInfo(tz_name))
    remaining = max(0, int(event.total_copies) - int(event.sold_count))

    secs_left = max(0, int((ends_utc - utcnow()).total_seconds()))
    hours_left = secs_left // 3600
    mins_left = (secs_left % 3600) // 60
    dur_str = (
        f"{hours_left}h {mins_left}m" if l != "uz" else (f"{hours_left} soat {mins_left} daq." if hours_left > 0 else f"{mins_left} daqiqa")
    )

    prices = []
    if event.price_coins:
        prices.append(f"🪙 <b>{fmt_coins(event.price_coins)}</b> coins")
    if event.price_stars:
        prices.append(f"⭐ <b>{fmt_coins(event.price_stars)}</b> Stars")

    titles = {
        "uz": dict(so="🔥 <b>EVENT KARTASI SOTILIB BO‘LDI!</b>", end="⌛ <b>EVENT TUGADI</b>", act="🎯 <b>YANGI LIMITLI EVENT BOSHLANDI!</b> 🎯", tot="Cheklangan nusxa", rem="Qolgan nusxa", note="✨ <i>Bu karta boshqa hech kimda yo‘q va guruhlarga tashlanmaydi!</i>", pr="Narxi", sep=" yoki ", unt="Amal qiladi", hint="👇 Pastdagi tugma yoki /event orqali oling!", sld="Sotilgan"),
        "en": dict(so="🔥 <b>EVENT CARD SOLD OUT!</b>", end="⌛ <b>EVENT ENDED</b>", act="🎯 <b>NEW LIMITED EVENT LIVE!</b> 🎯", tot="Limited copies", rem="Remaining", note="✨ <i>Exclusive limited card — never drops in groups!</i>", pr="Price", sep=" or ", unt="Valid until", hint="👇 Tap below or use /event to claim (1 per player)!", sld="Sold"),
        "ru": dict(so="🔥 <b>ИВЕНТ-КАРТА РАСПРОДАНА!</b>", end="⌛ <b>ИВЕНТ ЗАВЕРШЁН</b>", act="🎯 <b>НОВЫЙ ЛИМИТИРОВАННЫЙ ИВЕНТ!</b> 🎯", tot="Лимит копий", rem="Осталось", note="✨ <i>Эксклюзивная карта — не выпадает в группах!</i>", pr="Цена", sep=" или ", unt="До", hint="👇 Нажмите кнопку ниже или /event (1 копия на игрока)!", sld="Продано"),
    }[l]

    title = titles["so"] if state == "sold_out" else (titles["end"] if state == "ended" else titles["act"])

    lines = [
        title,
        "━━━━━━━━━━━━━━━",
        f"🎴 <b>{esc(card.character_name)}</b>",
        f"📺 {esc(card.anime_name)}",
        f"⭐ Rank: {meta.emoji} <b>{esc(card.rarity)}</b>",
        "",
        f"📦 {titles['tot']}: <b>{event.total_copies}</b>",
    ]
    if state == "active":
        lines.extend(
            [
                f"✅ {titles['rem']}: <b>{remaining}</b>",
                titles["note"],
                f"💳 {titles['pr']}: {titles['sep'].join(prices)}",
                f"⏳ {titles['unt']}: <b>{ends_local:%d.%m.%Y %H:%M}</b> ({dur_str})",
                "━━━━━━━━━━━━━━━",
                titles["hint"],
            ]
        )
    else:
        lines.append(f"🏁 {titles['sld']}: <b>{event.sold_count}/{event.total_copies}</b>")
        lines.append("━━━━━━━━━━━━━━━")
    return "\n".join(lines)
