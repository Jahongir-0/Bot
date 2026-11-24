"""Yangi mini o'yinlar: Dart, Boulling, Basketbol (Telegram animatsiyalari) va Sirli sandiq."""
import random
from typing import Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User
from models.v2_features import GameSession
from services.gameplay_service import GameplayService
from services.user_service import InsufficientCoinsError, UserService
from utils.game_catalog import ANIM_GAMES

BETS = (50, 100, 250, 500)
_RNG = random.SystemRandom()

# (og'irlik, narx koeffitsienti | None = jackpot, nom)
CHEST_OUTCOMES = (
    (40, 0.0, "💨 Bo‘sh sandiq"),
    (35, 0.5, "🪙 Kichik hamyon"),
    (17, 1.5, "💰 Oltin hamyon"),
    (6, 4.0, "💎 Qimmatbaho sandiq"),
    (2, None, "👑 JACKPOT SANDIQ"),
)


class ExtraGamesService:
    @staticmethod
    async def status(session: AsyncSession, uid: int, key: str) -> Tuple[bool, int, int]:
        cfg = await GameplayService.get_game_config(session)
        _today, used = await GameplayService._daily_game_count(session, uid, key)
        return bool(cfg.get(f"{key}_enabled", True)), used, int(cfg.get(f"{key}_daily_limit", 5))

    @staticmethod
    async def _finish(session, user, uid, key, roll, payout, xp, today, used, title) -> None:
        session.add(
            GameSession(
                user_id=uid, game_type=key, roll_value=roll, reward_coins=payout,
                reward_xp=xp if payout > 0 else 0, created_date=today,
            )
        )
        if payout > 0:
            await UserService.credit_coins(
                session, uid, payout, f"mini_game_{key}_win", f"{title} yutuq",
                reference_id=f"{key}_win:{uid}:{today.isoformat()}:{used + 1}",
            )
            user.game_wins = int(user.game_wins or 0) + 1
            user.xp = int(user.xp or 0) + xp
            user.level = max(1, 1 + (user.xp // 250))
            await GameplayService.add_season_points(session, uid, points=12, xp=xp)
        await session.flush()

    @staticmethod
    async def play_animated(session: AsyncSession, uid: int, kind: str, dice_value: int, bet: int) -> Tuple[bool, str, int, int]:
        """Qaytaradi: (ok, xabar, payout, sof natija)."""
        info = ANIM_GAMES.get(kind)
        if info is None:
            return False, "Noma’lum o‘yin.", 0, 0
        emoji, title, jackpot_val, win_val, jackpot_lbl, win_lbl = info
        user = await session.get(User, uid)
        if user is None:
            return False, "Foydalanuvchi topilmadi.", 0, 0
        cfg = await GameplayService.get_game_config(session)
        if not cfg.get(f"{kind}_enabled", True):
            return False, f"{emoji} {title} vaqtincha o‘chirilgan.", 0, 0
        bet = int(bet)
        if bet not in BETS:
            return False, "Garov miqdori 50, 100, 250 yoki 500 coin bo‘lishi kerak.", 0, 0
        today, used = await GameplayService._daily_game_count(session, uid, kind)
        limit = int(cfg.get(f"{kind}_daily_limit", 5))
        if used >= limit:
            return False, f"{emoji} Bugungi {title} limiti tugadi ({limit}/{limit}). Ertaga qayting!", 0, 0
        try:
            await UserService.debit_coins(
                session, uid, bet, f"mini_game_{kind}_bet", f"{title} garov {bet}",
                reference_id=f"{kind}_bet:{uid}:{today.isoformat()}:{used + 1}",
            )
        except InsufficientCoinsError:
            return False, f"🪙 Coinlaringiz yetarli emas ({bet:,} 🪙 kerak).", 0, 0

        val = int(dice_value)
        if val == jackpot_val:
            pct, label = int(cfg.get(f"{kind}_jackpot_pct", 400)), jackpot_lbl
        elif val == win_val:
            pct, label = int(cfg.get(f"{kind}_win_pct", 150)), win_lbl
        else:
            pct, label = 0, "😔 Bu safar omad kelmadi"
        payout = int(round(bet * pct / 100)) if pct > 0 else 0
        xp = int(cfg.get(f"{kind}_xp", 15))
        await ExtraGamesService._finish(session, user, uid, kind, val, payout, xp, today, used, title)
        net = payout - bet
        if payout > 0:
            sign = "+" if net >= 0 else ""
            return True, (
                f"{emoji} <b>{label}</b>\n\n"
                f"🪙 Yutuq: <b>+{payout:,}</b> coin (sof: {sign}{net:,} 🪙) · ⚡ +{xp} XP"
            ), payout, net
        return True, f"{emoji} <b>{label}</b>\n\n🪙 Yo‘qotildi: <b>-{bet:,}</b> coin", 0, -bet

    @staticmethod
    async def play_chest(session: AsyncSession, uid: int, pick: int) -> Tuple[bool, str, int, int]:
        user = await session.get(User, uid)
        if user is None:
            return False, "Foydalanuvchi topilmadi.", 0, 0
        cfg = await GameplayService.get_game_config(session)
        if not cfg.get("chest_enabled", True):
            return False, "📦 Sirli sandiq vaqtincha o‘chirilgan.", 0, 0
        today, used = await GameplayService._daily_game_count(session, uid, "chest")
        limit = int(cfg.get("chest_daily_limit", 5))
        if used >= limit:
            return False, f"📦 Bugungi limit tugadi ({limit}/{limit}). Ertaga qayting!", 0, 0
        cost = max(1, int(cfg.get("chest_cost", 100)))
        try:
            await UserService.debit_coins(
                session, uid, cost, "mini_game_chest_bet", f"Sirli sandiq {cost}",
                reference_id=f"chest_bet:{uid}:{today.isoformat()}:{used + 1}",
            )
        except InsufficientCoinsError:
            return False, f"🪙 Coinlaringiz yetarli emas ({cost:,} 🪙 kerak).", 0, 0
        idx = _RNG.choices(range(len(CHEST_OUTCOMES)), weights=[o[0] for o in CHEST_OUTCOMES])[0]
        _w, mult, label = CHEST_OUTCOMES[idx]
        if mult is None:
            mult = int(cfg.get("chest_jackpot_pct", 1000)) / 100
        payout = int(round(cost * mult))
        xp = int(cfg.get("chest_xp", 15))
        await ExtraGamesService._finish(session, user, uid, "chest", idx, payout, xp, today, used, "Sirli sandiq")
        net = payout - cost
        if payout > 0:
            sign = "+" if net >= 0 else ""
            return True, (
                f"📦 <b>Siz {int(pick)}-sandiqni ochdingiz!</b>\n\n{label}\n\n"
                f"🪙 Ichidan: <b>+{payout:,}</b> coin (sof: {sign}{net:,} 🪙) · ⚡ +{xp} XP"
            ), payout, net
        return True, f"📦 <b>Siz {int(pick)}-sandiqni ochdingiz!</b>\n\n{label}\n\n🪙 Yo‘qotildi: <b>-{cost:,}</b> coin", 0, -cost
