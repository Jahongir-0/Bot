import random
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from models.base import ensure_utc, utcnow
from models.card import Card
from models.user import User
from models.user_card import UserCard
from models.v2_features import (
    Achievement,
    GameSession,
    Quiz,
    QuizAttempt,
    QuizQuestion,
    Season,
    SeasonProgress,
    UserAchievement,
)
from services.settings_service import SettingsService
from services.user_service import UserService
from utils.constants import (
    DEFAULT_DAILY_RANDOM_CARD,
    DEFAULT_GAME_CONFIG,
    DEFAULT_RANK_DEFINITIONS,
    PROFILE_BACKGROUNDS,
    PROFILE_FRAMES,
)


class GameplayService:
    # ------------------------------------------------------------------
    # Dynamic Rank Management (jami drop_probability == 100%)
    # ------------------------------------------------------------------
    @staticmethod
    async def get_rank_definitions(session: AsyncSession) -> List[Dict[str, Any]]:
        raw = await SettingsService.get_value(session, "rank_definitions", DEFAULT_RANK_DEFINITIONS)
        items = list(raw) if isinstance(raw, list) and raw else list(DEFAULT_RANK_DEFINITIONS)
        items.sort(key=lambda x: int(x.get("order", 99)))
        return items

    @staticmethod
    async def save_rank_definitions(session: AsyncSession, ranks: List[Dict[str, Any]]) -> None:
        if not ranks:
            raise ValueError("Kamida 1 ta rank bo‘lishi shart.")
        total_prob = sum(int(r.get("drop_probability", 0)) for r in ranks)
        if total_prob != 100:
            raise ValueError(f"Barcha ranklar drop ehtimolligi yig‘indisi 100% bo‘lishi shart (hozir: {total_prob}%).")
        for i, r in enumerate(ranks, start=1):
            r["order"] = i
        await SettingsService.set_value(session, "rank_definitions", ranks)

        # rarity_config bilan sinxronlash
        rcfg = await SettingsService.get_rarity_config(session)
        for r in ranks:
            rcfg[str(r["name"])] = {
                "value": int(r.get("base_value", 1000)),
                "drops": int(r.get("drop_limit", 1)),
            }
        await SettingsService.set_value(session, "rarity_config", rcfg)

    @staticmethod
    async def update_rank_field(
        session: AsyncSession,
        rank_name: str,
        drop_probability: Optional[int] = None,
        base_value: Optional[int] = None,
        drop_limit: Optional[int] = None,
        emoji: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        ranks = await GameplayService.get_rank_definitions(session)
        target = next((r for r in ranks if str(r["name"]).lower() == rank_name.lower()), None)
        if target is None:
            raise ValueError("Rank topilmadi.")
        if drop_probability is not None:
            target["drop_probability"] = max(0, int(drop_probability))
        if base_value is not None:
            target["base_value"] = max(1, int(base_value))
        if drop_limit is not None:
            target["drop_limit"] = max(1, int(drop_limit))
        if emoji:
            target["emoji"] = emoji[:8]
        await GameplayService.save_rank_definitions(session, ranks)
        return ranks

    @staticmethod
    async def add_rank(
        session: AsyncSession,
        name: str,
        emoji: str,
        drop_probability: int,
        base_value: int,
        drop_limit: int,
        rank_weight: int = 50,
    ) -> List[Dict[str, Any]]:
        ranks = await GameplayService.get_rank_definitions(session)
        if any(str(r["name"]).lower() == name.strip().lower() for r in ranks):
            raise ValueError("Bu nomdagi rank allaqachon mavjud.")
        ranks.append(
            {
                "name": name.strip()[:32],
                "emoji": emoji.strip()[:8] or "⭐",
                "order": len(ranks) + 1,
                "drop_probability": int(drop_probability),
                "base_value": int(base_value),
                "drop_limit": int(drop_limit),
                "rank_weight": int(rank_weight),
            }
        )
        await GameplayService.save_rank_definitions(session, ranks)
        return ranks

    @staticmethod
    async def delete_rank(session: AsyncSession, name: str) -> List[Dict[str, Any]]:
        ranks = await GameplayService.get_rank_definitions(session)
        kept = [r for r in ranks if str(r["name"]).lower() != name.lower()]
        if len(kept) == len(ranks):
            raise ValueError("Rank topilmadi.")
        await GameplayService.save_rank_definitions(session, kept)
        return kept

    # ------------------------------------------------------------------
    # Quiz System
    # ------------------------------------------------------------------
    @staticmethod
    async def create_quiz(
        session: AsyncSession,
        title: str,
        reward_coins: int,
        admin_tg_id: int,
        reward_xp: int = 30,
        reward_card_id: Optional[int] = None,
        max_winners: int = 1,
    ) -> Quiz:
        q = Quiz(
            title=title[:160],
            is_active=True,
            status="active",
            reward_coins=max(0, int(reward_coins)),
            reward_xp=max(0, int(reward_xp)),
            reward_card_id=reward_card_id,
            max_winners=max(1, int(max_winners)),
            question_count=0,
            created_by_tg_id=admin_tg_id,
        )
        session.add(q)
        await session.flush()
        return q

    @staticmethod
    async def add_quiz_question(
        session: AsyncSession,
        quiz_id: int,
        question_text: str,
        options: List[str],
        correct_index: int,
        time_limit_sec: int = 30,
        image_file_id: Optional[str] = None,
    ) -> Optional[QuizQuestion]:
        quiz = await session.get(Quiz, quiz_id)
        if quiz is None:
            return None
        quiz.question_count = int(quiz.question_count or 0) + 1
        qq = QuizQuestion(
            quiz_id=quiz.id,
            order_num=quiz.question_count,
            question_text=question_text,
            options=options,
            correct_index=max(0, min(len(options) - 1, int(correct_index))),
            time_limit_sec=max(5, int(time_limit_sec)),
            image_file_id=image_file_id,
        )
        session.add(qq)
        await session.flush()
        return qq

    @staticmethod
    async def list_active_quizzes(session: AsyncSession) -> List[Quiz]:
        return list(
            (
                await session.execute(
                    select(Quiz).where(Quiz.is_active.is_(True), Quiz.status == "active").order_by(Quiz.id.desc())
                )
            )
            .scalars()
            .all()
        )

    @staticmethod
    async def get_next_unanswered_question(
        session: AsyncSession, quiz_id: int, user_id: int
    ) -> Optional[QuizQuestion]:
        answered_ids = select(QuizAttempt.question_id).where(
            QuizAttempt.quiz_id == quiz_id, QuizAttempt.user_id == user_id
        )
        return (
            await session.execute(
                select(QuizQuestion)
                .where(QuizQuestion.quiz_id == quiz_id, QuizQuestion.id.not_in(answered_ids))
                .order_by(QuizQuestion.order_num.asc())
                .limit(1)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def submit_quiz_answer(
        session: AsyncSession, quiz_id: int, question_id: int, user: User, selected_index: int
    ) -> Tuple[bool, bool, str]:
        """Qaytaradi: (accepted, is_correct, message)."""
        quiz = await session.get(Quiz, quiz_id)
        qq = await session.get(QuizQuestion, question_id)
        if quiz is None or qq is None or qq.quiz_id != quiz.id:
            return False, False, "Quiz savoli topilmadi."
        if not quiz.is_active or quiz.status != "active":
            return False, False, "Bu quiz yakunlangan."

        is_correct = int(selected_index) == int(qq.correct_index)
        try:
            async with session.begin_nested():
                session.add(
                    QuizAttempt(
                        quiz_id=quiz.id,
                        question_id=qq.id,
                        user_id=user.id,
                        selected_index=int(selected_index),
                        is_correct=is_correct,
                    )
                )
                await session.flush()
        except IntegrityError:
            return False, False, "Bu savolga allaqachon javob bergansiz!"

        if not is_correct:
            return True, False, f"❌ Noto‘g‘ri! To‘g‘ri javob: {qq.options[qq.correct_index]}"

        # Mukofot (to'g'ri javob uchun)
        user.quiz_wins = int(user.quiz_wins or 0) + 1
        user.xp = int(user.xp or 0) + quiz.reward_xp
        user.level = max(1, 1 + (user.xp // 250))
        msg = f"✅ To‘g‘ri! +{quiz.reward_xp} XP"
        if quiz.reward_coins > 0:
            await UserService.credit_coins(
                session,
                user.id,
                quiz.reward_coins,
                "quiz_reward",
                f"Quiz #{quiz.id}",
                reference_id=f"quiz:{quiz.id}:q:{qq.id}:u:{user.id}",
            )
            msg += f" · +{quiz.reward_coins:,} 🪙"
        await GameplayService.add_season_points(session, user.id, points=15, xp=quiz.reward_xp, quiz_win=True)
        await session.flush()
        return True, True, msg

    # ------------------------------------------------------------------
    # Mini Games (Dice Roll)
    # ------------------------------------------------------------------
    @staticmethod
    async def get_game_config(session: AsyncSession) -> Dict[str, Any]:
        raw = await SettingsService.get_value(session, "game_config", DEFAULT_GAME_CONFIG)
        cfg = dict(DEFAULT_GAME_CONFIG)
        if isinstance(raw, dict):
            cfg.update(raw)
        return cfg

    @staticmethod
    async def play_dice_game(session: AsyncSession, user: User, dice_value: Optional[int] = None) -> Tuple[bool, str, int, int]:
        """Qaytaradi: (ok, msg, roll, reward_coins)."""
        cfg = await GameplayService.get_game_config(session)
        if not cfg.get("dice_enabled", True):
            return False, "🎮 Mini o‘yinlar vaqtincha o‘chirilgan.", 0, 0

        tz = ZoneInfo(get_settings().timezone)
        today = datetime.now(tz).date()
        limit = int(cfg.get("dice_daily_limit", 3))
        cooldown_m = int(cfg.get("dice_cooldown_minutes", 30))

        today_rolls = list(
            (
                await session.execute(
                    select(GameSession)
                    .where(GameSession.user_id == user.id, GameSession.created_date == today)
                    .order_by(GameSession.id.desc())
                )
            )
            .scalars()
            .all()
        )
        if len(today_rolls) >= limit:
            return False, f"🎲 Bugungi o‘yin limiti tugadi ({limit}/{limit}). Ertaga qayting!", 0, 0

        if today_rolls:
            last_dt = ensure_utc(today_rolls[0].created_at)
            diff_sec = (utcnow() - last_dt).total_seconds()
            if diff_sec < cooldown_m * 60:
                wait_m = max(1, int((cooldown_m * 60 - diff_sec) // 60) + 1)
                return False, f"⏳ Keyingi o‘yin uchun {wait_m} daqiqa kuting.", 0, 0

        roll = int(dice_value) if dice_value and 1 <= int(dice_value) <= 6 else random.randint(1, 6)
        base_r = int(cfg.get("dice_base_reward", 50))
        jackpot_r = int(cfg.get("dice_jackpot_reward", 300))
        reward_xp = int(cfg.get("dice_xp", 15))
        reward_coins = jackpot_r if roll == 6 else roll * max(10, base_r // 3)

        session.add(
            GameSession(
                user_id=user.id,
                game_type="dice",
                roll_value=roll,
                reward_coins=reward_coins,
                reward_xp=reward_xp,
                created_date=today,
            )
        )
        await UserService.credit_coins(
            session,
            user.id,
            reward_coins,
            "mini_game_dice",
            f"Dice roll: {roll}",
            reference_id=f"dice:{user.id}:{today.isoformat()}:{len(today_rolls)+1}",
        )
        user.game_wins = int(user.game_wins or 0) + 1
        user.xp = int(user.xp or 0) + reward_xp
        user.level = max(1, 1 + (user.xp // 250))
        await GameplayService.add_season_points(session, user.id, points=10, xp=reward_xp)
        await session.flush()

        jackpot_txt = " 🎉 JACKPOT!" if roll == 6 else ""
        return True, f"🎲 Tosh: <b>{roll}</b>{jackpot_txt}\n🪙 +{reward_coins:,} coins · ⚡ +{reward_xp} XP", roll, reward_coins

    # ------------------------------------------------------------------
    # Daily Random Card (faqat released/faol kartalardan)
    # ------------------------------------------------------------------
    @staticmethod
    async def pick_daily_random_card(session: AsyncSession, user: User) -> Optional[Card]:
        cfg = await SettingsService.get_value(session, "daily_random_card", DEFAULT_DAILY_RANDOM_CARD)
        if not isinstance(cfg, dict) or not cfg.get("enabled", True):
            return None
        allowed = list(cfg.get("allowed_rarities") or ["Common", "Rare", "Epic"])
        owned_ids = select(UserCard.card_id).where(UserCard.user_id == user.id)
        card = (
            await session.execute(
                select(Card)
                .where(
                    Card.is_available.is_(True),
                    Card.is_deleted.is_(False),
                    Card.is_event_card.is_(False),
                    Card.dropped_count > 0,  # hech qachon unreleased karta berilmaydi
                    Card.rarity.in_(allowed),
                    Card.id.not_in(owned_ids),
                )
                .order_by(func.random())
                .limit(1)
            )
        ).scalar_one_or_none()
        if card is None:
            return None
        try:
            async with session.begin_nested():
                session.add(UserCard(user_id=user.id, card_id=card.id, acquired_via="daily_random"))
                await session.flush()
        except IntegrityError:
            return None
        await UserService.recompute_card_counts(session, user.id, add_xp=15)
        await session.flush()
        return card

    # ------------------------------------------------------------------
    # Season System
    # ------------------------------------------------------------------
    @staticmethod
    async def get_active_season(session: AsyncSession) -> Optional[Season]:
        now = utcnow()
        return (
            await session.execute(
                select(Season)
                .where(Season.status == "active", Season.ends_at > now)
                .order_by(Season.season_number.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def create_season(session: AsyncSession, name: str, days: int, top1_coins: int = 10000) -> Season:
        now = utcnow()
        await session.execute(update(Season).where(Season.status == "active").values(status="ended", finalized=True))
        max_num = int((await session.execute(select(func.max(Season.season_number)))).scalar_one() or 0)
        s = Season(
            season_number=max_num + 1,
            name=name[:128],
            description=f"{days} kunlik yangi mavsum",
            starts_at=now,
            ends_at=now + timedelta(days=max(1, int(days))),
            status="active",
            rewards_config={"top1_coins": int(top1_coins), "top2_coins": int(top1_coins // 2), "top3_coins": int(top1_coins // 4)},
        )
        session.add(s)
        await session.flush()
        return s

    @staticmethod
    async def add_season_points(
        session: AsyncSession,
        user_id: int,
        points: int = 0,
        xp: int = 0,
        card_claimed: bool = False,
        trade_done: bool = False,
        quiz_win: bool = False,
    ) -> None:
        season = await GameplayService.get_active_season(session)
        if season is None:
            return
        sp = (
            await session.execute(
                select(SeasonProgress).where(SeasonProgress.season_id == season.id, SeasonProgress.user_id == user_id)
            )
        ).scalar_one_or_none()
        if sp is None:
            sp = SeasonProgress(
                season_id=season.id,
                user_id=user_id,
                points=points,
                xp=xp,
                cards_claimed=1 if card_claimed else 0,
                trades_done=1 if trade_done else 0,
                quiz_wins=1 if quiz_win else 0,
            )
            session.add(sp)
        else:
            sp.points += points
            sp.xp += xp
            if card_claimed:
                sp.cards_claimed += 1
            if trade_done:
                sp.trades_done += 1
            if quiz_win:
                sp.quiz_wins += 1
        await session.flush()

    @staticmethod
    async def get_season_leaderboard(session: AsyncSession, season_id: int, limit: int = 10) -> List[Tuple[SeasonProgress, User]]:
        rows = (
            await session.execute(
                select(SeasonProgress, User)
                .join(User, User.id == SeasonProgress.user_id)
                .where(SeasonProgress.season_id == season_id, User.is_banned.is_(False))
                .order_by(SeasonProgress.points.desc(), SeasonProgress.xp.desc())
                .limit(limit)
            )
        ).all()
        return [(r[0], r[1]) for r in rows]

    @staticmethod
    async def finalize_expired_seasons(session: AsyncSession) -> int:
        now = utcnow()
        expired = list(
            (
                await session.execute(
                    select(Season).where(Season.status == "active", Season.ends_at <= now, Season.finalized.is_(False))
                )
            )
            .scalars()
            .all()
        )
        count = 0
        for s in expired:
            s.status = "ended"
            s.finalized = True
            top = await GameplayService.get_season_leaderboard(session, s.id, limit=3)
            rcfg = s.rewards_config or {}
            prizes = [int(rcfg.get("top1_coins", 10000)), int(rcfg.get("top2_coins", 5000)), int(rcfg.get("top3_coins", 2500))]
            for idx, (sp, u) in enumerate(top):
                if not sp.reward_claimed and idx < len(prizes) and prizes[idx] > 0:
                    sp.reward_claimed = True
                    await UserService.credit_coins(
                        session,
                        u.id,
                        prizes[idx],
                        "season_reward",
                        f"Season #{s.season_number} Top {idx+1}",
                        reference_id=f"season:{s.id}:top:{idx+1}",
                    )
            count += 1
        await session.flush()
        return count

    # ------------------------------------------------------------------
    # Achievements & Profile Customization
    # ------------------------------------------------------------------
    @staticmethod
    async def sync_user_achievements(session: AsyncSession, user: User) -> List[Achievement]:
        all_ach = list((await session.execute(select(Achievement).where(Achievement.is_active.is_(True)))).scalars().all())
        unlocked_ids = set(
            (
                await session.execute(
                    select(UserAchievement.achievement_id).where(UserAchievement.user_id == user.id)
                )
            )
            .scalars()
            .all()
        )
        newly: List[Achievement] = []
        for ach in all_ach:
            if ach.id in unlocked_ids:
                continue
            val = int(getattr(user, ach.metric, 0) or 0)
            if val >= int(ach.target_value):
                session.add(UserAchievement(user_id=user.id, achievement_id=ach.id))
                if ach.reward_coins > 0:
                    await UserService.credit_coins(
                        session,
                        user.id,
                        ach.reward_coins,
                        "achievement_reward",
                        ach.title,
                        reference_id=f"ach:{ach.id}:u:{user.id}",
                    )
                if ach.reward_xp > 0:
                    user.xp += ach.reward_xp
                    user.level = max(1, 1 + (user.xp // 250))
                if ach.reward_badge and not user.equipped_badge:
                    user.equipped_badge = ach.reward_badge
                if ach.reward_title and not user.equipped_title:
                    user.equipped_title = ach.reward_title
                newly.append(ach)
        if newly:
            await session.flush()
        return newly

    @staticmethod
    async def get_user_achievements(session: AsyncSession, user_id: int) -> Tuple[List[Achievement], Set[int]]:
        all_ach = list((await session.execute(select(Achievement).where(Achievement.is_active.is_(True)).order_by(Achievement.id.asc()))).scalars().all())
        unlocked = set(
            (await session.execute(select(UserAchievement.achievement_id).where(UserAchievement.user_id == user_id))).scalars().all()
        )
        return all_ach, unlocked

    @staticmethod
    async def equip_profile_item(session: AsyncSession, user: User, kind: str, code: str) -> bool:
        if kind == "frame" and code in PROFILE_FRAMES:
            user.equipped_frame = code
        elif kind == "bg" and code in PROFILE_BACKGROUNDS:
            user.equipped_bg = code
        elif kind == "badge":
            user.equipped_badge = code[:64] if code else None
        elif kind == "title":
            user.equipped_title = code[:64] if code else None
        else:
            return False
        await session.flush()
        return True
