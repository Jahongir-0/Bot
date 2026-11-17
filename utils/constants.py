from dataclasses import dataclass
from enum import Enum
from typing import Dict, List


class Rarity(str, Enum):
    COMMON = "Common"
    RARE = "Rare"
    EPIC = "Epic"
    LEGENDARY = "Legendary"
    MYTHIC = "Mythic"


@dataclass(frozen=True)
class RarityMeta:
    name: str
    emoji: str
    default_reward: int
    default_base_value: int
    default_drop_weight: int
    rank_weight: int
    order: int
    default_drop_limit: int = 1  # karta jami necha marta guruhlarga tashlanishi mumkin


# Extensible rarity registry: add new rarities here and they automatically propagate
RARITY_REGISTRY: Dict[str, RarityMeta] = {
    Rarity.COMMON.value: RarityMeta(
        name="Common",
        emoji="⚪",
        default_reward=20,
        default_base_value=1000,
        default_drop_weight=55,
        rank_weight=1,
        order=1,
        default_drop_limit=3,
    ),
    Rarity.RARE.value: RarityMeta(
        name="Rare",
        emoji="🔵",
        default_reward=50,
        default_base_value=2500,
        default_drop_weight=25,
        rank_weight=3,
        order=2,
        default_drop_limit=2,
    ),
    Rarity.EPIC.value: RarityMeta(
        name="Epic",
        emoji="🟣",
        default_reward=100,
        default_base_value=5000,
        default_drop_weight=12,
        rank_weight=7,
        order=3,
        default_drop_limit=2,
    ),
    Rarity.LEGENDARY.value: RarityMeta(
        name="Legendary",
        emoji="🟡",
        default_reward=300,
        default_base_value=10000,
        default_drop_weight=6,
        rank_weight=15,
        order=4,
        default_drop_limit=1,
    ),
    Rarity.MYTHIC.value: RarityMeta(
        name="Mythic",
        emoji="🔴",
        default_reward=1000,
        default_base_value=25000,
        default_drop_weight=2,
        rank_weight=40,
        order=5,
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


# Guruhlarga karta tashlash qoidalari (admin o'zgartirishi mumkin: settings jadvalidagi "drop_rules")
DEFAULT_DROP_RULES: Dict[str, object] = {
    "members_per_card": 10,        # har 10 a'zoga kuniga 1 ta karta (20 va kam = 2 ta)
    "min_per_day": 2,
    "max_per_day": 200,
    "window_start_hour": 0,        # jadval oynasi (mahalliy vaqt): 0..24 = butun kun
    "window_end_hour": 24,
    "same_group_cooldown_days": 7,   # bir xil karta shu guruhga qayta tashlanmasligi uchun
    "any_group_cooldown_hours": 3,   # bir xil karta ketma-ket boshqa guruhga ham tashlanmasligi uchun
    "announce_in_group": True,       # kun boshida guruhga "bugun N ta karta" e'loni
}

EVENT_DURATION_HOURS = 24

DEFAULT_SETTINGS: Dict[str, object] = {
    "rarity_config": {
        k: {"value": v.default_base_value, "drops": v.default_drop_limit} for k, v in RARITY_REGISTRY.items()
    },
    "drop_rules": DEFAULT_DROP_RULES,
    "rarity_rewards": {k: v.default_reward for k, v in RARITY_REGISTRY.items()},
    "daily_bonus": {
        "base_coins": 150,
        "streak_step": 50,
        "max_streak_bonus": 1000,
    },
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
}

QUEST_DEFINITIONS: List[Dict[str, object]] = [
    {"id": "first_card", "title": "🎴 Ilk qadam", "desc": "Kamida 1 ta karta yig‘ing", "metric": "total_cards", "target": 1, "reward": 150},
    {"id": "cards_10", "title": "📦 Kolleksioner", "desc": "10 ta karta yig‘ing", "metric": "total_cards", "target": 10, "reward": 400},
    {"id": "cards_25", "title": "🏛️ Karta Ustasi", "desc": "25 ta karta yig‘ing", "metric": "total_cards", "target": 25, "reward": 1000},
    {"id": "rare_hunter", "title": "💎 Noyob ovchi", "desc": "Epic, Legendary yoki Mythic karta oling", "metric": "high_rarity", "target": 1, "reward": 500},
    {"id": "streak_3", "title": "🔥 3 kunlik seriya", "desc": "Ketma-ket 3 kun /daily oling", "metric": "daily_streak", "target": 3, "reward": 300},
    {"id": "streak_7", "title": "🌟 Haftalik faollik", "desc": "Ketma-ket 7 kun /daily oling", "metric": "daily_streak", "target": 7, "reward": 800},
    {"id": "ref_1", "title": "🤝 Ilk do‘st", "desc": "1 ta do‘stni referal orqali taklif qiling", "metric": "referrals", "target": 1, "reward": 300},
    {"id": "ref_5", "title": "📣 Jamoa yetakchisi", "desc": "5 ta do‘stni referal orqali taklif qiling", "metric": "referrals", "target": 5, "reward": 1500},
]

PAGE_SIZE = 5
MAX_CARD_PRICE = 10_000_000
MIN_CARD_PRICE = 10
