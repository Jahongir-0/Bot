"""Karta darajasi (level), unvon, kuch (power) va drop qoidalari.

- Rank o'zgarmaydi, faqat foydalanuvchining shu kartasi darajasi ko'tariladi.
- Daraja ko'tarish: coin bilan yoki shu kartani yana bir marta olish (dublikat) bilan.
- Drop ehtimoli rank tartib raqamiga (order) bog'langan: yuqori ranklar tasodifiy dropdan chiqariladi.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.card import Card
from models.user_card import UserCard
from services.settings_service import SettingsService
from services.user_service import InsufficientCoinsError, UserService
from utils.constants import get_rank_name_by_order, get_rarity_aliases, get_rarity_meta

logger = logging.getLogger(__name__)
_POWER_CACHE: Dict[str, Any] = {"ts": 0.0, "rows": []}

CFG_KEY = "card_level_config"
MULT_KEY = "rank_drop_mult"

# Unvonlar: hamma personajga (o'g'il/qiz/jonzot) mos, jinsga bog'liq bo'lmagan otlar.
LEVEL_TITLES = [
    ("🌱", "Uyg‘onish"),
    ("🌿", "Intilish"),
    ("🔥", "Jasorat"),
    ("⚡", "Mahorat"),
    ("⭐", "Yuksalish"),
    ("🌟", "Jilo"),
    ("💫", "Shuhrat"),
    ("🌠", "Buyuklik"),
    ("✨", "Ulug‘vorlik"),
    ("👑", "Afsona"),
]

DEFAULT_LEVEL_CONFIG: Dict[str, Any] = {
    "max_level": 10,
    "coin_cost_factor": 0.5,   # narx = karta qiymati x omil x joriy daraja
    "power_step": 0.15,        # har bir daraja kuchni +15% oshiradi
    "dup_refund_percent": 50,  # maksimal darajada dublikat tushsa, qiymatning necha foizi coin bo'lib qaytadi
    "stars_per_level": 20,     # Stars narxi = shu son x joriy daraja
    "stars_max_level": 5,      # Stars bilan faqat shu darajagacha ko'tariladi
    "bot_sell_level_bonus_pct": 10,  # Botga sotishda har bir qo'shimcha daraja uchun +foiz (o'rtacha 10%)
    "merge_cost_4": 5,         # 4-rank (Legendary) -> 5-rank (Mythic): nechta karta kerak (0 = o'chirilgan)
    "merge_cost_5": 10,        # 5-rank (Mythic) -> 6-rank (Divine): nechta karta kerak (0 = o'chirilgan)
    "base_power": {"1": 10, "2": 25, "3": 60, "4": 150, "5": 400, "6": 1000},
}

# rank tartib raqami -> tasodifiy dropdagi ko'paytiruvchi (0 = umuman tushmaydi)
DEFAULT_RANK_DROP_MULT: Dict[int, float] = {1: 1.0, 2: 1.0, 3: 1.0, 4: 0.15}
DROP_MULT_PRESETS = [0.0, 0.05, 0.15, 0.5, 1.0]


@dataclass
class UpgradeResult:
    success: bool
    message: str
    new_level: int = 0
    cost: int = 0


@dataclass
class DupResult:
    user_card_id: int
    level_before: int
    level_after: int
    maxed: bool
    refund: int


class CardLevelService:
    # ---------------------------------------------------------------- config
    @staticmethod
    async def get_config(session: AsyncSession) -> Dict[str, Any]:
        stored = await SettingsService.get_value(session, CFG_KEY, None)
        cfg = dict(DEFAULT_LEVEL_CONFIG)
        cfg["base_power"] = dict(DEFAULT_LEVEL_CONFIG["base_power"])
        if isinstance(stored, dict):
            for k, v in stored.items():
                if k == "base_power" and isinstance(v, dict):
                    cfg["base_power"].update({str(a): int(b) for a, b in v.items()})
                elif k in cfg:
                    cfg[k] = v
        cfg["max_level"] = max(2, min(50, int(cfg["max_level"])))
        return cfg

    @staticmethod
    async def set_config_value(session: AsyncSession, key: str, value: Any) -> None:
        cfg = await CardLevelService.get_config(session)
        if key not in cfg or key == "base_power":
            raise ValueError("Noma’lum sozlama")
        cfg[key] = value
        await SettingsService.set_value(session, CFG_KEY, cfg)

    @staticmethod
    async def get_drop_mults(session: AsyncSession) -> Dict[int, float]:
        stored = await SettingsService.get_value(session, MULT_KEY, None)
        out: Dict[int, float] = {}
        if isinstance(stored, dict):
            for k, v in stored.items():
                try:
                    out[int(k)] = max(0.0, float(v))
                except (TypeError, ValueError):
                    continue
        return out

    @staticmethod
    def mult_for(mults: Dict[int, float], order: int) -> float:
        if order in mults:
            return mults[order]
        return DEFAULT_RANK_DROP_MULT.get(order, 0.0)

    @staticmethod
    async def set_drop_mult(session: AsyncSession, order: int, mult: float) -> None:
        mults = await CardLevelService.get_drop_mults(session)
        mults[int(order)] = max(0.0, float(mult))
        await SettingsService.set_value(session, MULT_KEY, {str(k): v for k, v in mults.items()})

    @staticmethod
    def rarity_mult(mults: Dict[int, float], rarity: str) -> float:
        return CardLevelService.mult_for(mults, get_rarity_meta(rarity).order)

    # ---------------------------------------------------------------- hisob-kitob
    @staticmethod
    def title(level: int) -> Tuple[str, str]:
        idx = max(1, min(len(LEVEL_TITLES), int(level))) - 1
        return LEVEL_TITLES[idx]

    @staticmethod
    def title_text(level: int) -> str:
        emoji, name = CardLevelService.title(level)
        return f"{emoji} {name}"

    @staticmethod
    def power(rarity: str, level: int, cfg: Dict[str, Any]) -> int:
        order = get_rarity_meta(rarity).order
        base_map = cfg.get("base_power", {})
        if str(order) in base_map:
            base = int(base_map[str(order)])
        else:
            last = int(base_map.get("6", 1000))
            base = last * (2 ** max(1, order - 6))
        step = float(cfg.get("power_step", 0.15))
        return int(round(base * (1 + step * (max(1, int(level)) - 1))))

    @staticmethod
    def upgrade_cost(base_value: int, level: int, cfg: Dict[str, Any]) -> int:
        factor = float(cfg.get("coin_cost_factor", 0.5))
        return max(100, int(round(int(base_value) * factor * max(1, int(level)))))

    @staticmethod
    def bot_sell_price(rank_base_value: int, level: int, cfg: Dict[str, Any]) -> Tuple[int, int, int]:
        """Botga sotish narxi: (jami, rank_narxi, daraja_bonusi).

        Lvl 1 -> rank_narxi. Har +1 lvl uchun bot_sell_level_bonus_pct% (standart +10%) qo'shiladi.
        """
        base = max(1, int(rank_base_value or 0))
        lv = max(1, int(level or 1))
        pct = max(0, int(cfg.get("bot_sell_level_bonus_pct", 10)))
        bonus = int(round(base * (pct / 100.0) * (lv - 1))) if lv > 1 and pct > 0 else 0
        return base + bonus, base, bonus

    @staticmethod
    def rank_value_for_card(card: Card, rcfg: Dict[str, Dict[str, int]]) -> int:
        """Kartaning ranki bo'yicha joriy coin qiymatini qaytaradi."""
        rarity_low = (card.rarity or "").strip().lower()
        for k, entry in (rcfg or {}).items():
            if str(k).strip().lower() == rarity_low and isinstance(entry, dict):
                val = int(entry.get("value", 0) or 0)
                if val > 0:
                    return val
        if int(card.base_value or 0) > 0:
            return int(card.base_value)
        return max(1, int(get_rarity_meta(card.rarity).default_base_value))

    # ---------------------------------------------------------------- amallar
    @staticmethod
    async def upgrade_with_coins(session: AsyncSession, user_id: int, user_card_id: int) -> UpgradeResult:
        uc = await session.get(UserCard, user_card_id)
        if uc is None or uc.user_id != user_id:
            return UpgradeResult(False, "Karta topilmadi.")
        card = await session.get(Card, uc.card_id)
        if card is None:
            return UpgradeResult(False, "Karta topilmadi.")
        cfg = await CardLevelService.get_config(session)
        rcfg = await SettingsService.get_rarity_config(session)
        level = int(uc.level or 1)
        if level >= cfg["max_level"]:
            return UpgradeResult(False, "👑 Bu karta allaqachon eng yuqori darajada.", level)
        card_val = CardLevelService.rank_value_for_card(card, rcfg)
        cost = CardLevelService.upgrade_cost(card_val, level, cfg)
        try:
            await UserService.debit_coins(
                session, user_id, cost, "card_level_up", f"Level up: {card.character_name} {level}->{level + 1}"
            )
        except InsufficientCoinsError:
            return UpgradeResult(False, f"🪙 Coin yetarli emas. Kerak: {cost:,} 🪙", level, cost)
        uc.level = level + 1
        await session.flush()
        return UpgradeResult(True, "ok", uc.level, cost)

    @staticmethod
    async def apply_duplicate(session: AsyncSession, user_id: int, card: Card) -> Optional[DupResult]:
        """Foydalanuvchida bor kartadan yana tushsa: +1 daraja (maksimalda: coin qaytariladi)."""
        uc = (
            await session.execute(
                select(UserCard).where(UserCard.user_id == user_id, UserCard.card_id == card.id).limit(1)
            )
        ).scalar_one_or_none()
        if uc is None:
            return None
        cfg = await CardLevelService.get_config(session)
        before = int(uc.level or 1)
        if before >= cfg["max_level"]:
            refund = int(int(card.base_value) * int(cfg.get("dup_refund_percent", 50)) / 100)
            if refund > 0:
                await UserService.credit_coins(
                    session, user_id, refund, "card_duplicate_refund", f"Dublikat (max daraja): {card.character_name}"
                )
            return DupResult(uc.id, before, before, True, refund)
        uc.level = before + 1
        await session.flush()
        return DupResult(uc.id, before, uc.level, False, 0)

    @staticmethod
    async def total_power(session: AsyncSession, user_id: int) -> int:
        cfg = await CardLevelService.get_config(session)
        rows = (
            await session.execute(
                select(UserCard.level, Card.rarity)
                .join(Card, Card.id == UserCard.card_id)
                .where(UserCard.user_id == user_id)
            )
        ).all()
        return sum(CardLevelService.power(r, int(lv or 1), cfg) for lv, r in rows)

    @staticmethod
    async def top_power(session: AsyncSession, limit: int = 10):
        """Eng kuchli kolleksiya egalari: [(User, umumiy_kuch)] — 60 soniya keshlanadi."""
        from models.user import User

        now = time.monotonic()
        if now - _POWER_CACHE["ts"] < 60 and _POWER_CACHE["rows"]:
            ranked = _POWER_CACHE["rows"]
        else:
            cfg = await CardLevelService.get_config(session)
            rows = (
                await session.execute(
                    select(UserCard.user_id, UserCard.level, Card.rarity).join(Card, Card.id == UserCard.card_id)
                )
            ).all()
            totals: Dict[int, int] = {}
            for uid, lv, rarity in rows:
                totals[uid] = totals.get(uid, 0) + CardLevelService.power(rarity, int(lv or 1), cfg)
            ranked = sorted(totals.items(), key=lambda x: -x[1])[:50]
            _POWER_CACHE["ts"], _POWER_CACHE["rows"] = now, ranked
        out = []
        for uid, val in ranked[:limit]:
            u = await session.get(User, uid)
            if u is not None:
                out.append((u, val))
        return out

    # ---------------------------------------------------------------- Stars bilan ko'tarish
    @staticmethod
    def stars_price(level: int, cfg: Dict[str, Any]) -> int:
        return max(1, int(cfg.get("stars_per_level", 20)) * max(1, int(level)))

    @staticmethod
    async def validate_stars_upgrade(
        session: AsyncSession, user_id: int, user_card_id: int, target_level: int, total_amount: int
    ) -> Tuple[bool, str]:
        uc = await session.get(UserCard, user_card_id)
        if uc is None or uc.user_id != user_id:
            return False, "Karta topilmadi."
        cfg = await CardLevelService.get_config(session)
        level = int(uc.level or 1)
        if target_level != level + 1:
            return False, "Karta darajasi o‘zgargan. Qaytadan urinib ko‘ring."
        if target_level > min(int(cfg["stars_max_level"]), int(cfg["max_level"])):
            return False, "Bu darajaga Stars bilan ko‘tarib bo‘lmaydi."
        if int(total_amount) != CardLevelService.stars_price(level, cfg):
            return False, "Narx o‘zgargan. Qaytadan urinib ko‘ring."
        return True, "ok"

    @staticmethod
    async def apply_stars_upgrade(session: AsyncSession, user_id: int, user_card_id: int, target_level: int) -> Optional[int]:
        """To'lov tasdiqlangach darajani ko'taradi. Takroriy xabar bo'lsa (daraja allaqachon ko'tarilgan) None."""
        uc = await session.get(UserCard, user_card_id)
        if uc is None or uc.user_id != user_id:
            return None
        if int(uc.level or 1) != target_level - 1:
            return None
        uc.level = target_level
        await session.flush()
        return target_level


    # ---------------------------------------------------------------- Birlashtirish
    @staticmethod
    def merge_options(cfg: Dict[str, Any]):
        """[(manba rank tartibi, kerakli kartalar soni)] — faqat yoqilganlari."""
        out = []
        for order in (4, 5):
            cost = int(cfg.get(f"merge_cost_{order}", 0))
            if cost > 0:
                out.append((order, cost))
        return out

    @staticmethod
    async def _spare_cards(session: AsyncSession, user_id: int, order: int):
        """Birlashtirish uchun yaroqli kartalar: band emas, vitrinada/sevimlida emas. Past darajali birinchi."""
        names = get_rarity_aliases(get_rank_name_by_order(order))
        if not names:
            return []
        from sqlalchemy import func

        rows = (
            await session.execute(
                select(UserCard)
                .join(Card, Card.id == UserCard.card_id)
                .where(
                    UserCard.user_id == user_id,
                    UserCard.is_locked.is_(False),
                    UserCard.is_showcased.is_(False),
                    UserCard.is_favorite.is_(False),
                    func.lower(Card.rarity).in_(names),
                )
                .order_by(UserCard.level.asc(), UserCard.id.asc())
            )
        ).scalars().all()
        return list(rows)

    @staticmethod
    async def count_spare(session: AsyncSession, user_id: int, order: int) -> int:
        return len(await CardLevelService._spare_cards(session, user_id, order))

    @staticmethod
    async def merge(session: AsyncSession, user_id: int, order: int) -> Tuple[bool, str, Optional[Card]]:
        """`cost` ta kartani topshirib, keyingi rankdan bitta yangi karta olish."""
        import random

        from sqlalchemy import func

        from services.user_service import UserService

        cfg = await CardLevelService.get_config(session)
        cost = dict(CardLevelService.merge_options(cfg)).get(order)
        if not cost:
            return False, "Bu birlashtirish hozircha o‘chirilgan.", None
        spare = await CardLevelService._spare_cards(session, user_id, order)
        if len(spare) < cost:
            return False, f"Yetarli karta yo‘q: kerak {cost} ta, bo‘sh kartalar {len(spare)} ta.", None

        next_name = get_rank_name_by_order(order + 1)
        next_names = get_rarity_aliases(next_name)
        owned_ids = select(UserCard.card_id).where(UserCard.user_id == user_id)
        pool = (
            await session.execute(
                select(Card).where(
                    Card.is_available.is_(True),
                    Card.is_deleted.is_(False),
                    Card.is_event_card.is_(False),
                    Card.dropped_count < Card.drop_limit,
                    func.lower(Card.rarity).in_(next_names),
                    Card.id.not_in(owned_ids),
                )
            )
        ).scalars().all()
        if not pool:
            return False, f"Hozircha {next_name} kartalari mavjud emas (hammasi tarqatilgan yoki sizda bor).", None

        reward = random.choice(list(pool))
        for uc in spare[:cost]:
            await session.delete(uc)
        await session.flush()
        reward.dropped_count = int(reward.dropped_count or 0) + 1
        new_uc = UserCard(user_id=user_id, card_id=reward.id, acquired_via="merge", card_state="OWNED")
        session.add(new_uc)
        await session.flush()
        new_uc.serial_number = 10000 + new_uc.id
        await UserService.recompute_card_counts(session, user_id, add_xp=50)
        return True, "ok", reward
