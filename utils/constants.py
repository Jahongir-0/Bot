from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Tuple


class Rarity(str, Enum):
    COMMON = "Common"
    RARE = "Rare"
    EPIC = "Epic"
    LEGENDARY = "Legendary"
    MYTHIC = "Mythic"
    DIVINE = "Divine"


MEDIA_TYPES: List[str] = [
    "Anime",
    "Manga",
    "Manhwa",
    "Manhua",
    "Comic",
    "Cartoon",
    "Movie",
    "TV Series",
    "Other",
]

MANGA_UNIFIED_TYPES: Tuple[str, ...] = ("Manga", "Manhwa", "Manhua")

CARD_STATES: List[str] = [
    "UNRELEASED",
    "AVAILABLE",
    "OWNED",
    "EVENT",
    "AUCTION",
    "LOCKED",
    "DELETED",
]

ADMIN_PERMISSION_SECTIONS: List[Tuple[str, str]] = [
    ("cards", "🎴 Card"),
    ("events", "🎁 Event"),
    ("users", "👤 User"),
    ("admins", "👑 Admin"),
    ("groups", "👥 Group"),
    ("channels", "📢 Kanal"),
    ("ranks", "⭐ Rank"),
    ("stars", "💎 Stars"),
    ("quiz", "🧠 Quiz"),
    ("games", "🎮 Games"),
    ("stats", "📊 Statistics"),
    ("settings", "⚙️ Settings"),
    ("message", "📨 Message"),
    ("report", "🚨 Report"),
    ("auction", "🔨 Auksion"),
    ("season", "🏆 Sezon"),
    ("reklama", "📣 Reklama"),
    ("memory", "💾 Memory"),
    ("support", "💬 Support"),
    ("security", "🔐 Security"),
    ("logs", "📋 Logs"),
]


@dataclass(frozen=True)
class RarityMeta:
    name: str
    emoji: str
    default_reward: int
    default_base_value: int
    default_drop_weight: int
    rank_weight: int
    order: int
    default_drop_limit: int = 1


RARITY_REGISTRY: Dict[str, RarityMeta] = {
    Rarity.COMMON.value: RarityMeta(
        name="Common",
        emoji="⚪",
        default_reward=0,
        default_base_value=1000,
        default_drop_weight=48,
        rank_weight=1,
        order=1,
        default_drop_limit=3,
    ),
    Rarity.RARE.value: RarityMeta(
        name="Rare",
        emoji="🔵",
        default_reward=0,
        default_base_value=2500,
        default_drop_weight=25,
        rank_weight=3,
        order=2,
        default_drop_limit=2,
    ),
    Rarity.EPIC.value: RarityMeta(
        name="Epic",
        emoji="🟣",
        default_reward=0,
        default_base_value=5000,
        default_drop_weight=14,
        rank_weight=7,
        order=3,
        default_drop_limit=2,
    ),
    Rarity.LEGENDARY.value: RarityMeta(
        name="Legendary",
        emoji="🟡",
        default_reward=0,
        default_base_value=10000,
        default_drop_weight=8,
        rank_weight=15,
        order=4,
        default_drop_limit=1,
    ),
    Rarity.MYTHIC.value: RarityMeta(
        name="Mythic",
        emoji="🔴",
        default_reward=0,
        default_base_value=25000,
        default_drop_weight=4,
        rank_weight=40,
        order=5,
        default_drop_limit=1,
    ),
    Rarity.DIVINE.value: RarityMeta(
        name="Divine",
        emoji="💎",
        default_reward=0,
        default_base_value=60000,
        default_drop_weight=1,
        rank_weight=100,
        order=6,
        default_drop_limit=1,
    ),
}


def get_rarity_meta(rarity: str) -> RarityMeta:
    return RARITY_REGISTRY.get(rarity, RARITY_REGISTRY[Rarity.COMMON.value])


def list_rarities() -> List[str]:
    return [meta.name for meta in sorted(RARITY_REGISTRY.values(), key=lambda m: m.order)]


class RankingMode(str, Enum):
    WEIGHTED = "weighted"
    UNIQUE_FIRST = "unique_first"
    TOTAL_CARDS = "total_cards"


DEFAULT_DROP_RULES: Dict[str, object] = {
    "members_per_card": 10,
    "min_per_day": 2,
    "max_per_day": 200,
    "window_start_hour": 0,
    "window_end_hour": 24,
    "same_group_cooldown_days": 7,
    "any_group_cooldown_hours": 3,
    "announce_in_group": True,
    "active_hours": [9, 12, 15, 18, 21],
}

EVENT_DURATION_HOURS = 24

DEFAULT_RANK_DEFINITIONS: List[Dict[str, object]] = [
    {
        "name": m.name,
        "emoji": m.emoji,
        "order": m.order,
        "drop_probability": m.default_drop_weight,
        "base_value": m.default_base_value,
        "drop_limit": m.default_drop_limit,
        "rank_weight": m.rank_weight,
    }
    for m in sorted(RARITY_REGISTRY.values(), key=lambda x: x.order)
]

DEFAULT_VIP_CONFIG: Dict[str, object] = {
    "enabled": True,
    "tiers": {
        "Silver": {
            "name": "Silver VIP",
            "emoji": "🥈",
            "stars": 150,
            "coins": 15000,
            "duration_days": 30,
            "daily_bonus_mult": 1.5,
            "no_ads": True,
            "bypass_forced_sub": False,
        },
        "Gold": {
            "name": "Gold VIP",
            "emoji": "🥇",
            "stars": 350,
            "coins": 35000,
            "duration_days": 30,
            "daily_bonus_mult": 2.0,
            "no_ads": True,
            "bypass_forced_sub": True,
        },
        "Diamond": {
            "name": "Diamond VIP",
            "emoji": "💎",
            "stars": 700,
            "coins": 75000,
            "duration_days": 30,
            "daily_bonus_mult": 3.0,
            "no_ads": True,
            "bypass_forced_sub": True,
        },
    },
}

DEFAULT_GAME_CONFIG: Dict[str, object] = {
    "dice_enabled": True,
    "dice_daily_limit": 3,
    "dice_cooldown_minutes": 0,
    "dice_base_reward": 50,
    "dice_jackpot_reward": 300,
    "dice_xp": 15,
    "coinflip_enabled": True,
    "coinflip_daily_limit": 5,
    "coinflip_xp": 10,
    "lucky_enabled": True,
    "lucky_daily_limit": 3,
    "lucky_min_reward": 60,
    "lucky_max_reward": 350,
    "lucky_xp": 10,
}

PACK_CONFIG: Dict[str, Dict[str, object]] = {
    "bronze": {
        "name": "Bronze Paket",
        "emoji": "🥉",
        "price": 500,
        "weights": {"Common": 60, "Rare": 30, "Epic": 10},
        "desc": "Common / Rare / Epic kartalar (Epic 10%)",
    },
    "silver": {
        "name": "Silver Paket",
        "emoji": "🥈",
        "price": 1500,
        "weights": {"Rare": 50, "Epic": 35, "Legendary": 15},
        "desc": "Rare / Epic / Legendary kartalar (Legendary 15%)",
    },
    "gold": {
        "name": "Gold Paket",
        "emoji": "🥇",
        "price": 4000,
        "weights": {"Epic": 45, "Legendary": 35, "Mythic": 16, "Divine": 4},
        "desc": "Epic / Legendary / Mythic / Divine kartalar",
    },
}

DEFAULT_DAILY_RANDOM_CARD: Dict[str, object] = {
    "enabled": True,
    "allowed_rarities": ["Common", "Rare", "Epic"],
}

DEFAULT_ANTICHEAT_CONFIG: Dict[str, object] = {
    "enabled": True,
    "warn_threshold": 30,
    "freeze_threshold": 60,
    "ban_threshold": 90,
    "min_claim_seconds": 1.2,
    "max_mutual_transfers_24h": 5,
}

DEFAULT_XP_CONFIG: Dict[str, object] = {
    "claim_xp": 20,
    "quiz_xp": 30,
    "game_xp": 15,
    "trade_xp": 25,
    "daily_xp": 15,
    "xp_per_level": 250,
}

DEFAULT_SETTINGS: Dict[str, object] = {
    "rarity_config": {
        k: {"value": v.default_base_value, "drops": v.default_drop_limit} for k, v in RARITY_REGISTRY.items()
    },
    "rank_definitions": DEFAULT_RANK_DEFINITIONS,
    "drop_rules": DEFAULT_DROP_RULES,
    "rarity_rewards": {k: 0 for k in RARITY_REGISTRY},
    "daily_bonus": {
        "base_coins": 150,
        "streak_step": 50,
        "max_streak_bonus": 1000,
    },
    "daily_random_card": DEFAULT_DAILY_RANDOM_CARD,
    "ranking_mode": RankingMode.WEIGHTED.value,
    "offer_expiry_hours": 24,
    "referral_config": {
        "inviter_coins": 500,
        "invitee_coins": 250,
    },
    "group_add_reward_coins": 1000,
    "stars_packages": [
        {"id": "pack_100", "stars": 100, "coins": 10000, "label": "⭐ 100 Stars → 10,000 coins"},
        {"id": "pack_500", "stars": 500, "coins": 60000, "label": "⭐ 500 Stars → 60,000 coins"},
        {"id": "pack_1000", "stars": 1000, "coins": 135000, "label": "⭐ 1000 Stars → 135,000 coins"},
    ],
    "vip_config": DEFAULT_VIP_CONFIG,
    "game_config": DEFAULT_GAME_CONFIG,
    "anticheat_config": DEFAULT_ANTICHEAT_CONFIG,
    "xp_config": DEFAULT_XP_CONFIG,
    "maintenance": {"enabled": False, "message": "🔧 Botda texnik ishlar olib borilmoqda. Birozdan so‘ng qayting."},
    "welcome_text": (
        "🎴 <b>ANIME CARD COLLECTION V2</b> ga xush kelibsiz!\n\n"
        "📌 <b>Qanday o‘ynaladi?</b>\n"
        "1️⃣ Guruhlarda tasodifiy karta rasmi tashlanadi.\n"
        "2️⃣ Karta xabariga <b>REPLY</b> qilib personaj ismini yozing va kartani oling!\n"
        "3️⃣ /daily bilan kunlik coin + karta, /quiz va /game bilan qo‘shimcha mukofotlar yuting!\n"
        "4️⃣ /trade, /market, /auction va /wishlist orqali kolleksiyangizni kuchaytiring!"
    ),
    "pagination": {"page_size": 20},
    "notifications": {"wishlist": True, "auction": True, "event": True, "trade": True},
    "backup_config": {"auto_daily": True, "retention_days": 14, "backup_hour": 3},
}

PROFILE_FRAMES: Dict[str, str] = {
    "default": "▫️ Oddiy ramka",
    "fire": "🔥 Olovli ramka",
    "royal": "👑 Qirollik ramkasi",
    "cyber": "⚡ Kiber ramka",
    "sakura": "🌸 Sakura ramkasi",
    "diamond": "💎 Olmos ramka",
}

PROFILE_BACKGROUNDS: Dict[str, str] = {
    "classic": "🌌 Klassik",
    "neon": "🌆 Neon Tokyo",
    "samurai": "⛩️ Samuray",
    "galaxy": "✨ Galaktika",
}

QUEST_DEFINITIONS: List[Dict[str, object]] = [
    {"id": "first_card", "title": "🎴 Ilk qadam", "desc": "Kamida 1 ta karta yig‘ing", "metric": "total_cards", "target": 1, "reward": 150},
    {"id": "cards_10", "title": "📦 Kolleksioner", "desc": "10 ta karta yig‘ing", "metric": "total_cards", "target": 10, "reward": 400},
    {"id": "cards_25", "title": "🏛️ Karta Ustasi", "desc": "25 ta karta yig‘ing", "metric": "total_cards", "target": 25, "reward": 1000},
    {"id": "cards_50", "title": "🌌 Karta Afsonasi", "desc": "50 ta karta yig‘ing", "metric": "total_cards", "target": 50, "reward": 2500},
    {"id": "rare_hunter", "title": "💎 Noyob ovchi", "desc": "Epic, Legendary, Mythic yoki Divine karta oling", "metric": "high_rarity", "target": 1, "reward": 500},
    {"id": "streak_3", "title": "🔥 3 kunlik seriya", "desc": "Ketma-ket 3 kun /daily oling", "metric": "daily_streak", "target": 3, "reward": 300},
    {"id": "streak_7", "title": "🌟 Haftalik faollik", "desc": "Ketma-ket 7 kun /daily oling", "metric": "daily_streak", "target": 7, "reward": 800},
    {"id": "quiz_5", "title": "🧠 Viktorina bilimdoni", "desc": "Viktorinalarda 5 ta to‘g‘ri javob bering", "metric": "quiz_wins", "target": 5, "reward": 600},
    {"id": "game_5", "title": "🎮 Mini-o‘yin g‘olibi", "desc": "Mini-o‘yinlarda 5 marta yuting", "metric": "game_wins", "target": 5, "reward": 550},
    {"id": "trade_1", "title": "🤝 Ilk almashuv", "desc": "1 marta karta almashuvini yakunlang", "metric": "trade_count", "target": 1, "reward": 400},
    {"id": "ref_1", "title": "👥 Ilk do‘st", "desc": "1 ta do‘stni referal orqali taklif qiling", "metric": "referrals", "target": 1, "reward": 300},
    {"id": "ref_5", "title": "📣 Jamoa yetakchisi", "desc": "5 ta do‘stni referal orqali taklif qiling", "metric": "referrals", "target": 5, "reward": 1500},
]

PAGE_SIZE = 20
MAX_CARD_PRICE = 10_000_000
MIN_CARD_PRICE = 10
