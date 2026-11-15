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


# Extensible rarity registry: add new rarities here and they automatically propagate
RARITY_REGISTRY: Dict[str, RarityMeta] = {
    Rarity.COMMON.value: RarityMeta(
        name="Common",
        emoji="⚪",
        default_reward=20,
        default_base_value=100,
        default_drop_weight=55,
        rank_weight=1,
        order=1,
    ),
    Rarity.RARE.value: RarityMeta(
        name="Rare",
        emoji="🔵",
        default_reward=50,
        default_base_value=350,
        default_drop_weight=25,
        rank_weight=3,
        order=2,
    ),
    Rarity.EPIC.value: RarityMeta(
        name="Epic",
        emoji="🟣",
        default_reward=100,
        default_base_value=900,
        default_drop_weight=12,
        rank_weight=7,
        order=3,
    ),
    Rarity.LEGENDARY.value: RarityMeta(
        name="Legendary",
        emoji="🟡",
        default_reward=300,
        default_base_value=2500,
        default_drop_weight=6,
        rank_weight=15,
        order=4,
    ),
    Rarity.MYTHIC.value: RarityMeta(
        name="Mythic",
        emoji="🔴",
        default_reward=1000,
        default_base_value=8000,
        default_drop_weight=2,
        rank_weight=40,
        order=5,
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


DEFAULT_SETTINGS: Dict[str, object] = {
    "rarity_rewards": {k: v.default_reward for k, v in RARITY_REGISTRY.items()},
    "daily_bonus": {
        "base_coins": 150,
        "streak_step": 50,
        "max_streak_bonus": 1000,
    },
    "ranking_mode": RankingMode.WEIGHTED.value,
    "offer_expiry_hours": 24,
    "stars_packages": [
        {"id": "pack_100", "stars": 100, "coins": 10000, "label": "⭐ 100 Stars → 10,000 coins"},
        {"id": "pack_500", "stars": 500, "coins": 60000, "label": "⭐ 500 Stars → 60,000 coins"},
        {"id": "pack_1000", "stars": 1000, "coins": 135000, "label": "⭐ 1000 Stars → 135,000 coins"},
    ],
}

PAGE_SIZE = 5
MAX_CARD_PRICE = 10_000_000
MIN_CARD_PRICE = 10
