import random
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from models.base import ensure_utc, utcnow
from models.card import Card
from models.schedule import Event
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
from services.user_service import InsufficientCoinsError, UserService
from utils.constants import (
    DEFAULT_DAILY_RANDOM_CARD,
    DEFAULT_GAME_CONFIG,
    DEFAULT_RANK_DEFINITIONS,
    PROFILE_BACKGROUNDS,
    PROFILE_FRAMES,
    get_rank_name_by_order,
    sync_runtime_ranks,
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
        sync_runtime_ranks(ranks)
        from services.ranking_service import RankingService

        RankingService.invalidate_cache()

    @staticmethod
    async def _propagate_rank_rename(session: AsyncSession, old_name: str, new_name: str) -> None:
        old_clean = (old_name or "").strip()
        new_clean = (new_name or "").strip()
        if not old_clean or not new_clean or old_clean == new_clean:
            return
        await session.execute(
            update(Card)
            .where(func.lower(Card.rarity) == old_clean.lower())
            .values(rarity=new_clean)
        )
        await session.execute(
            update(Event)
            .where(func.lower(Event.boosted_rarity) == old_clean.lower())
            .values(boosted_rarity=new_clean)
        )
        # daily_random_card.allowed_rarities yangilash
        drc = await SettingsService.get_value(session, "daily_random_card", DEFAULT_DAILY_RANDOM_CARD)
        if isinstance(drc, dict) and isinstance(drc.get("allowed_rarities"), list):
            new_allowed = [
                new_clean if str(x).strip().lower() == old_clean.lower() else x
                for x in drc["allowed_rarities"]
            ]
            drc["allowed_rarities"] = list(dict.fromkeys(new_allowed))
            await SettingsService.set_value(session, "daily_random_card", drc)
        # rarity_rewards kalitini ko'chirish
        r_rew = await SettingsService.get_value(session, "rarity_rewards", {})
        if isinstance(r_rew, dict):
            matched_keys = [k for k in list(r_rew.keys()) if str(k).strip().lower() == old_clean.lower()]
            if matched_keys:
                val = r_rew.pop(matched_keys[0], 0)
                for mk in matched_keys[1:]:
                    r_rew.pop(mk, None)
                r_rew.setdefault(new_clean, val)
                await SettingsService.set_value(session, "rarity_rewards", r_rew)
        # rarity_config eski kalitini tozalash
        rcfg = await SettingsService.get_rarity_config(session)
        changed_cfg = False
        for k in list(rcfg.keys()):
            if str(k).strip().lower() == old_clean.lower() and k != new_clean:
                old_entry = rcfg.pop(k)
                rcfg.setdefault(new_clean, old_entry)
                changed_cfg = True
        if changed_cfg:
            await SettingsService.set_value(session, "rarity_config", rcfg)
        from services.ranking_service import RankingService

        RankingService.invalidate_cache()

    @staticmethod
    async def sync_runtime_and_legacy_ranks(session: AsyncSession) -> List[Dict[str, Any]]:
        """Bot ishga tushganda DB ranklarini xotira bilan sinxronlaydi va eski Common/Rare/Legendary nomlarini yangilaydi."""
        ranks = await GameplayService.get_rank_definitions(session)
        current_names_lower = {str(r.get("name", "")).strip().lower() for r in ranks}
        legacy_upgrade = {
            "common": ("Basic", 1),
            "rare": ("Elita", 2),
            "legendary": ("Legend", 4),
        }
        migrated = False
        defs_changed = False
        for item in ranks:
            order_num = int(item.get("order", 0) or 0)
            cur_name = str(item.get("name", "")).strip()
            up = legacy_upgrade.get(cur_name.lower())
            if up is not None:
                new_name, expected_order = up
                if order_num == expected_order and new_name.lower() not in current_names_lower:
                    item["name"] = new_name
                    current_names_lower.discard(cur_name.lower())
                    current_names_lower.add(new_name.lower())
                    defs_changed = True
                    await GameplayService._propagate_rank_rename(session, cur_name, new_name)
                    migrated = True

        if defs_changed:
            await SettingsService.set_value(session, "rank_definitions", ranks)

        sync_runtime_ranks(ranks)
        default_by_order = {int(d["order"]): str(d["name"]) for d in DEFAULT_RANK_DEFINITIONS}
        for item in ranks:
            order_num = int(item.get("order", 0) or 0)
            cur_name = str(item.get("name", "")).strip()
            def_name = default_by_order.get(order_num)
            if (
                def_name
                and cur_name
                and cur_name.lower() != def_name.lower()
                and def_name.lower() not in current_names_lower
            ):
                await GameplayService._propagate_rank_rename(session, def_name, cur_name)
                migrated = True
            # Legacy aliaslarni ham har ehtimolga qarshi sinxronlab o'tamiz
            for old_alias, (canon_name, alias_order) in legacy_upgrade.items():
                if order_num == alias_order and cur_name.lower() == canon_name.lower():
                    old_cnt = int(
                        (
                            await session.execute(
                                select(func.count(Card.id)).where(func.lower(Card.rarity) == old_alias)
                            )
                        ).scalar_one()
                        or 0
                    )
                    if old_cnt > 0:
                        await GameplayService._propagate_rank_rename(session, old_alias, cur_name)
                        migrated = True
        if migrated:
            await session.flush()
        return ranks

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
        for i, r in enumerate(kept, start=1):
            r["order"] = i
        await SettingsService.set_value(session, "rank_definitions", kept)
        sync_runtime_ranks(kept)
        from services.ranking_service import RankingService

        RankingService.invalidate_cache()
        return kept

    @staticmethod
    async def update_single_rank_attr(
        session: AsyncSession,
        rank_name: str,
        new_name: Optional[str] = None,
        emoji: Optional[str] = None,
        drop_probability: Optional[int] = None,
        drop_limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Bitta rank maydonini alohida tahrirlash (100% moslash tugmasi bilan birga)."""
        ranks = await GameplayService.get_rank_definitions(session)
        target = next((r for r in ranks if str(r["name"]).lower() == rank_name.lower()), None)
        if target is None:
            raise ValueError("Rank topilmadi.")
        old_name = str(target["name"])
        if new_name and new_name.strip():
            clean = new_name.strip()[:32]
            if any(r is not target and str(r["name"]).lower() == clean.lower() for r in ranks):
                raise ValueError("Bu nomdagi rank allaqachon mavjud.")
            target["name"] = clean
        if emoji:
            target["emoji"] = emoji.strip()[:8]
        if drop_probability is not None:
            target["drop_probability"] = max(0, min(100, int(drop_probability)))
        if drop_limit is not None:
            target["drop_limit"] = max(1, int(drop_limit))
        await SettingsService.set_value(session, "rank_definitions", ranks)
        rcfg = await SettingsService.get_rarity_config(session)
        for r in ranks:
            rcfg[str(r["name"])] = {
                "value": int(r.get("base_value", 0)),
                "drops": int(r.get("drop_limit", 1)),
            }
        await SettingsService.set_value(session, "rarity_config", rcfg)
        sync_runtime_ranks(ranks)
        if new_name and new_name.strip() and str(target["name"]) != old_name:
            await GameplayService._propagate_rank_rename(session, old_name, str(target["name"]))
        from services.ranking_service import RankingService

        RankingService.invalidate_cache()
        return ranks

    @staticmethod
    async def add_rank_flexible(
        session: AsyncSession,
        name: str,
        emoji: str,
        drop_probability: int,
        drop_limit: int,
    ) -> List[Dict[str, Any]]:
        ranks = await GameplayService.get_rank_definitions(session)
        if any(str(r["name"]).lower() == name.strip().lower() for r in ranks):
            raise ValueError("Bu nomdagi rank allaqachon mavjud.")
        ranks.append(
            {
                "name": name.strip()[:32],
                "emoji": emoji.strip()[:8] or "⭐",
                "order": len(ranks) + 1,
                "drop_probability": max(0, min(100, int(drop_probability))),
                "base_value": 0,
                "drop_limit": max(1, int(drop_limit)),
                "rank_weight": max(1, len(ranks) * 20),
            }
        )
        await SettingsService.set_value(session, "rank_definitions", ranks)
        rcfg = await SettingsService.get_rarity_config(session)
        rcfg[name.strip()[:32]] = {"value": 0, "drops": max(1, int(drop_limit))}
        await SettingsService.set_value(session, "rarity_config", rcfg)
        sync_runtime_ranks(ranks)
        return ranks

    @staticmethod
    async def normalize_rank_probabilities(session: AsyncSession) -> List[Dict[str, Any]]:
        ranks = await GameplayService.get_rank_definitions(session)
        total = sum(max(0, int(r.get("drop_probability", 0))) for r in ranks)
        if total <= 0:
            each = 100 // len(ranks)
            for r in ranks:
                r["drop_probability"] = each
            ranks[0]["drop_probability"] += 100 - sum(r["drop_probability"] for r in ranks)
        elif total != 100:
            scaled = [max(1, round(int(r.get("drop_probability", 0)) * 100 / total)) for r in ranks]
            diff = 100 - sum(scaled)
            scaled[0] = max(1, scaled[0] + diff)
            for r, val in zip(ranks, scaled):
                r["drop_probability"] = val
        await GameplayService.save_rank_definitions(session, ranks)
        return ranks

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
        target_scope: str = "all",
        target_group_id: Optional[int] = None,
        scheduled_at: Optional[datetime] = None,
        status: str = "active",
    ) -> Quiz:
        q = Quiz(
            title=title[:160],
            is_active=(status == "active"),
            status=status,
            reward_coins=max(0, int(reward_coins)),
            reward_xp=max(0, int(reward_xp)),
            reward_card_id=reward_card_id,
            max_winners=max(1, int(max_winners)),
            question_count=0,
            target_scope=target_scope,
            target_group_id=target_group_id,
            scheduled_at=scheduled_at,
            is_dispatched=(scheduled_at is None),
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
        accepted_answers: Optional[List[str]] = None,
    ) -> Optional[QuizQuestion]:
        quiz = await session.get(Quiz, quiz_id)
        if quiz is None:
            return None
        quiz.question_count = int(quiz.question_count or 0) + 1
        ans_list = accepted_answers if accepted_answers is not None else ([options[correct_index]] if options else [])
        qq = QuizQuestion(
            quiz_id=quiz.id,
            order_num=quiz.question_count,
            question_text=question_text,
            options=options,
            accepted_answers=ans_list,
            correct_index=max(0, min(len(options) - 1, int(correct_index))) if options else 0,
            time_limit_sec=max(5, int(time_limit_sec)),
            image_file_id=image_file_id,
        )
        session.add(qq)
        await session.flush()
        return qq

    @staticmethod
    async def list_all_quizzes(session: AsyncSession, limit: int = 20) -> List[Quiz]:
        return list((await session.execute(select(Quiz).order_by(Quiz.id.desc()).limit(limit))).scalars().all())

    @staticmethod
    async def get_quiz_with_questions(session: AsyncSession, quiz_id: int) -> Optional[Tuple[Quiz, List[QuizQuestion]]]:
        q = await session.get(Quiz, quiz_id)
        if q is None:
            return None
        qs = list(
            (await session.execute(select(QuizQuestion).where(QuizQuestion.quiz_id == quiz_id).order_by(QuizQuestion.order_num.asc())))
            .scalars()
            .all()
        )
        return q, qs

    @staticmethod
    async def clone_quiz(
        session: AsyncSession,
        source_quiz_id: int,
        admin_tg_id: int,
        new_reward_coins: Optional[int] = None,
        target_scope: str = "all",
        target_group_id: Optional[int] = None,
        scheduled_at: Optional[datetime] = None,
    ) -> Optional[Quiz]:
        res = await GameplayService.get_quiz_with_questions(session, source_quiz_id)
        if res is None:
            return None
        src, questions = res
        coins = int(new_reward_coins) if new_reward_coins is not None else src.reward_coins
        status = "scheduled" if scheduled_at else "active"
        nq = await GameplayService.create_quiz(
            session,
            title=src.title,
            reward_coins=coins,
            admin_tg_id=admin_tg_id,
            reward_xp=src.reward_xp,
            target_scope=target_scope,
            target_group_id=target_group_id,
            scheduled_at=scheduled_at,
            status=status,
        )
        for qq in questions:
            await GameplayService.add_quiz_question(
                session,
                nq.id,
                qq.question_text,
                list(qq.options or []),
                qq.correct_index,
                accepted_answers=list(qq.accepted_answers or []),
            )
        return nq

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
    async def get_dice_status(session: AsyncSession, user_or_id: Union[User, int]) -> Tuple[bool, int, int]:
        """(yoqilganmi, bugun nechta o'ynagan, kunlik limit)"""
        uid = user_or_id if isinstance(user_or_id, int) else int(user_or_id.id)
        cfg = await GameplayService.get_game_config(session)
        tz = ZoneInfo(get_settings().timezone)
        today = datetime.now(tz).date()
        used = int(
            (
                await session.execute(
                    select(func.count(GameSession.id)).where(
                        GameSession.user_id == uid,
                        GameSession.game_type == "dice",
                        GameSession.created_date == today,
                    )
                )
            ).scalar_one()
            or 0
        )
        return bool(cfg.get("dice_enabled", True)), used, int(cfg.get("dice_daily_limit", 3))

    @staticmethod
    async def play_dice_game(
        session: AsyncSession,
        user_or_id: Union[User, int],
        dice_value: Optional[int] = None,
        guessed_number: Optional[int] = None,
    ) -> Tuple[bool, str, int, int]:
        """Qaytaradi: (ok, msg, roll, reward_coins)."""
        uid = user_or_id if isinstance(user_or_id, int) else int(user_or_id.id)
        user = await session.get(User, uid)
        if user is None:
            return False, "Foydalanuvchi topilmadi.", 0, 0
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
                    .where(
                        GameSession.user_id == uid,
                        GameSession.game_type == "dice",
                        GameSession.created_date == today,
                    )
                    .order_by(GameSession.id.desc())
                )
            )
            .scalars()
            .all()
        )
        if len(today_rolls) >= limit:
            return False, f"🎲 Bugungi o‘yin limiti tugadi ({limit}/{limit}). Ertaga qayting!", 0, 0

        if guessed_number is None and today_rolls and cooldown_m > 0:
            last_dt = ensure_utc(today_rolls[0].created_at)
            diff_sec = (utcnow() - last_dt).total_seconds()
            if diff_sec < cooldown_m * 60:
                wait_m = max(1, int((cooldown_m * 60 - diff_sec) // 60) + 1)
                return False, f"⏳ Keyingi o‘yin uchun {wait_m} daqiqa kuting.", 0, 0

        roll = int(dice_value) if dice_value and 1 <= int(dice_value) <= 6 else random.randint(1, 6)
        jackpot_r = int(cfg.get("dice_jackpot_reward", 300))
        reward_xp = int(cfg.get("dice_xp", 15))

        if guessed_number is not None:
            won = int(roll) == int(guessed_number)
            reward_coins = jackpot_r if won else 0
        else:
            won = roll == 6
            reward_coins = jackpot_r if won else roll * max(10, int(cfg.get("dice_base_reward", 50)) // 3)

        session.add(
            GameSession(
                user_id=uid,
                game_type="dice",
                roll_value=roll,
                reward_coins=reward_coins,
                reward_xp=reward_xp if won else 0,
                created_date=today,
            )
        )
        if reward_coins > 0:
            await UserService.credit_coins(
                session,
                uid,
                reward_coins,
                "mini_game_dice",
                f"Dice roll: {roll}",
                reference_id=f"dice:{uid}:{today.isoformat()}:{len(today_rolls)+1}",
            )
            user.game_wins = int(user.game_wins or 0) + 1
            user.xp = int(user.xp or 0) + reward_xp
            user.level = max(1, 1 + (user.xp // 250))
            await GameplayService.add_season_points(session, uid, points=10, xp=reward_xp)
        await session.flush()

        if guessed_number is not None:
            if won:
                return True, (
                    f"🎉 <b>YUTDINGIZ!</b>\n\nSiz <b>{guessed_number}</b> ni tanladingiz va tosh ham <b>{roll}</b> tushdi!\n"
                    f"🪙 <b>+{reward_coins:,}</b> coin · ⚡ +{reward_xp} XP"
                ), roll, reward_coins
            return True, (
                f"😔 <b>Yutqazdingiz</b>\n\nSiz <b>{guessed_number}</b> ni tanladingiz, tosh esa <b>{roll}</b> tushdi."
            ), roll, 0

        jackpot_txt = " 🎉 JACKPOT!" if roll == 6 else ""
        return True, f"🎲 Tosh: <b>{roll}</b>{jackpot_txt}\n🪙 +{reward_coins:,} coins · ⚡ +{reward_xp} XP", roll, reward_coins

    # ------------------------------------------------------------------
    # Mini Games: Coin Flip & Lucky Card
    # ------------------------------------------------------------------
    @staticmethod
    async def get_coinflip_status(session: AsyncSession, user_or_id: Union[User, int]) -> Tuple[bool, int, int]:
        uid = user_or_id if isinstance(user_or_id, int) else int(user_or_id.id)
        cfg = await GameplayService.get_game_config(session)
        tz = ZoneInfo(get_settings().timezone)
        today = datetime.now(tz).date()
        used = int(
            (
                await session.execute(
                    select(func.count(GameSession.id)).where(
                        GameSession.user_id == uid,
                        GameSession.game_type == "coinflip",
                        GameSession.created_date == today,
                    )
                )
            ).scalar_one()
            or 0
        )
        return bool(cfg.get("coinflip_enabled", True)), used, int(cfg.get("coinflip_daily_limit", 5))

    @staticmethod
    async def play_coin_flip(
        session: AsyncSession,
        user_or_id: Union[User, int],
        choice: str,
        bet: int,
    ) -> Tuple[bool, str, bool, int]:
        """Tanga tashlash (gerb/raqam). Qaytaradi: (ok, msg, won, net_delta)."""
        uid = user_or_id if isinstance(user_or_id, int) else int(user_or_id.id)
        user = await session.get(User, uid)
        if user is None:
            return False, "Foydalanuvchi topilmadi.", False, 0

        cfg = await GameplayService.get_game_config(session)
        if not cfg.get("coinflip_enabled", True):
            return False, "🪙 Tanga tashlash o‘yini vaqtincha o‘chirilgan.", False, 0
        choice_norm = (choice or "").strip().lower()
        if choice_norm not in ("heads", "tails"):
            return False, "Noto‘g‘ri tanlov.", False, 0
        bet_amt = int(bet)
        if bet_amt not in (50, 100, 250, 500):
            return False, "Garov miqdori 50, 100, 250 yoki 500 coin bo‘lishi kerak.", False, 0

        tz = ZoneInfo(get_settings().timezone)
        today = datetime.now(tz).date()
        limit = int(cfg.get("coinflip_daily_limit", 5))
        used = int(
            (
                await session.execute(
                    select(func.count(GameSession.id)).where(
                        GameSession.user_id == uid,
                        GameSession.game_type == "coinflip",
                        GameSession.created_date == today,
                    )
                )
            ).scalar_one()
            or 0
        )
        if used >= limit:
            return False, f"🪙 Bugungi Tanga tashlash limiti tugadi ({limit}/{limit}). Ertaga qayting!", False, 0

        try:
            await UserService.debit_coins(
                session,
                uid,
                bet_amt,
                "mini_game_coinflip_bet",
                f"Coin flip bet ({choice_norm})",
                reference_id=f"cf_bet:{uid}:{today.isoformat()}:{used + 1}",
            )
        except InsufficientCoinsError:
            return False, f"🪙 Coinlaringiz yetarli emas ({bet_amt:,} 🪙 kerak).", False, 0

        result_side = random.choice(["heads", "tails"])
        won = result_side == choice_norm
        reward_xp = int(cfg.get("coinflip_xp", 10))
        payout = bet_amt * 2 if won else 0

        session.add(
            GameSession(
                user_id=uid,
                game_type="coinflip",
                roll_value=1 if result_side == "heads" else 2,
                reward_coins=payout,
                reward_xp=reward_xp if won else 0,
                created_date=today,
            )
        )
        if won:
            await UserService.credit_coins(
                session,
                uid,
                payout,
                "mini_game_coinflip_win",
                f"Coin flip won ({result_side})",
                reference_id=f"cf_win:{uid}:{today.isoformat()}:{used + 1}",
            )
            user.game_wins = int(user.game_wins or 0) + 1
            user.xp = int(user.xp or 0) + reward_xp
            user.level = max(1, 1 + (user.xp // 250))
            await GameplayService.add_season_points(session, uid, points=10, xp=reward_xp)
        await session.flush()

        side_lbl = "🦅 Gerb" if result_side == "heads" else "🔢 Raqam"
        pick_lbl = "🦅 Gerb" if choice_norm == "heads" else "🔢 Raqam"
        if won:
            return True, (
                f"🎉 <b>YUTDINGIZ!</b>\n\nSiz: <b>{pick_lbl}</b> · Tushdi: <b>{side_lbl}</b>\n"
                f"🪙 Mukofot: <b>+{payout:,}</b> coin (sof foyda: +{bet_amt:,} 🪙) · ⚡ +{reward_xp} XP"
            ), True, bet_amt
        return True, (
            f"😔 <b>Yutqazdingiz</b>\n\nSiz: <b>{pick_lbl}</b> · Tushdi: <b>{side_lbl}</b>\n"
            f"🪙 Yo‘qotildi: <b>-{bet_amt:,}</b> coin"
        ), False, -bet_amt

    @staticmethod
    async def get_lucky_status(session: AsyncSession, user_or_id: Union[User, int]) -> Tuple[bool, int, int]:
        uid = user_or_id if isinstance(user_or_id, int) else int(user_or_id.id)
        cfg = await GameplayService.get_game_config(session)
        tz = ZoneInfo(get_settings().timezone)
        today = datetime.now(tz).date()
        used = int(
            (
                await session.execute(
                    select(func.count(GameSession.id)).where(
                        GameSession.user_id == uid,
                        GameSession.game_type == "lucky",
                        GameSession.created_date == today,
                    )
                )
            ).scalar_one()
            or 0
        )
        return bool(cfg.get("lucky_enabled", True)), used, int(cfg.get("lucky_daily_limit", 3))

    @staticmethod
    async def play_lucky_card(
        session: AsyncSession,
        user_or_id: Union[User, int],
        picked_slot: int,
    ) -> Tuple[bool, str, int]:
        """3 ta yopiq kartadan 1 tasini ochish (bepul kunlik omad o'yini)."""
        uid = user_or_id if isinstance(user_or_id, int) else int(user_or_id.id)
        user = await session.get(User, uid)
        if user is None:
            return False, "Foydalanuvchi topilmadi.", 0

        cfg = await GameplayService.get_game_config(session)
        if not cfg.get("lucky_enabled", True):
            return False, "🃏 Omadli karta o‘yini vaqtincha o‘chirilgan.", 0
        if picked_slot not in (1, 2, 3):
            return False, "1 dan 3 gacha karta tanlang.", 0

        tz = ZoneInfo(get_settings().timezone)
        today = datetime.now(tz).date()
        limit = int(cfg.get("lucky_daily_limit", 3))
        used = int(
            (
                await session.execute(
                    select(func.count(GameSession.id)).where(
                        GameSession.user_id == uid,
                        GameSession.game_type == "lucky",
                        GameSession.created_date == today,
                    )
                )
            ).scalar_one()
            or 0
        )
        if used >= limit:
            return False, f"🃏 Bugungi Omadli karta limiti tugadi ({limit}/{limit}). Ertaga qayting!", 0

        min_r = max(10, int(cfg.get("lucky_min_reward", 60)))
        max_r = max(min_r, int(cfg.get("lucky_max_reward", 350)))
        mid_r = (min_r + max_r) // 2
        prizes = [min_r, mid_r, max_r]
        random.shuffle(prizes)
        reward_coins = prizes[picked_slot - 1]
        is_jackpot = reward_coins == max_r
        reward_xp = int(cfg.get("lucky_xp", 10))

        session.add(
            GameSession(
                user_id=uid,
                game_type="lucky",
                roll_value=picked_slot,
                reward_coins=reward_coins,
                reward_xp=reward_xp,
                created_date=today,
            )
        )
        await UserService.credit_coins(
            session,
            uid,
            reward_coins,
            "mini_game_lucky",
            f"Lucky card #{picked_slot}",
            reference_id=f"lucky:{uid}:{today.isoformat()}:{used + 1}",
        )
        if is_jackpot:
            user.game_wins = int(user.game_wins or 0) + 1
        user.xp = int(user.xp or 0) + reward_xp
        user.level = max(1, 1 + (user.xp // 250))
        await GameplayService.add_season_points(session, uid, points=8, xp=reward_xp)
        await session.flush()

        cards_reveal = "  ".join(
            f"[{'🌟' if i + 1 == picked_slot else '▫️'} #{i + 1}: {p}🪙]" for i, p in enumerate(prizes)
        )
        title = "🌟 <b>OMADLI JACKPOT!</b>" if is_jackpot else "🃏 <b>KARTA OCHILDI!</b>"
        return True, (
            f"{title}\n\n{cards_reveal}\n\n"
            f"Siz <b>#{picked_slot}</b> kartani ochdingiz: <b>+{reward_coins:,} 🪙</b> · ⚡ +{reward_xp} XP"
        ), reward_coins

    # ------------------------------------------------------------------
    # Daily Random Card (faqat released/faol kartalardan)
    # ------------------------------------------------------------------
    @staticmethod
    async def pick_daily_random_card(session: AsyncSession, user: User) -> Optional[Card]:
        cfg = await SettingsService.get_value(session, "daily_random_card", DEFAULT_DAILY_RANDOM_CARD)
        if not isinstance(cfg, dict) or not cfg.get("enabled", True):
            return None
        allowed = list(
            cfg.get("allowed_rarities")
            or [
                get_rank_name_by_order(1, "Basic"),
                get_rank_name_by_order(2, "Elita"),
                get_rank_name_by_order(3, "Epic"),
            ]
        )
        allowed_lower = [str(a).strip().lower() for a in allowed if str(a).strip()]
        owned_ids = select(UserCard.card_id).where(UserCard.user_id == user.id)
        card = (
            await session.execute(
                select(Card)
                .where(
                    Card.is_available.is_(True),
                    Card.is_deleted.is_(False),
                    Card.is_event_card.is_(False),
                    Card.dropped_count > 0,  # hech qachon unreleased karta berilmaydi
                    func.lower(Card.rarity).in_(allowed_lower),
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
            await GameplayService._finalize_season_obj(session, s)
            count += 1
        await session.flush()
        return count

    @staticmethod
    async def _finalize_season_obj(session: AsyncSession, s: Season) -> None:
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

    @staticmethod
    async def extend_active_season(session: AsyncSession, extra_days: int) -> Optional[Season]:
        s = await GameplayService.get_active_season(session)
        if s is None:
            return None
        s.ends_at = ensure_utc(s.ends_at) + timedelta(days=max(1, int(extra_days)))
        await session.flush()
        return s

    @staticmethod
    async def end_active_season_now(session: AsyncSession) -> Optional[Season]:
        s = await GameplayService.get_active_season(session)
        if s is None:
            return None
        await GameplayService._finalize_season_obj(session, s)
        await session.flush()
        return s

    @staticmethod
    async def get_due_scheduled_quizzes(session: AsyncSession) -> List[Quiz]:
        now = utcnow()
        return list(
            (
                await session.execute(
                    select(Quiz).where(
                        Quiz.status == "scheduled",
                        Quiz.is_dispatched.is_(False),
                        Quiz.scheduled_at.is_not(None),
                        Quiz.scheduled_at <= now,
                    )
                )
            )
            .scalars()
            .all()
        )

    @staticmethod
    async def submit_quiz_text_answer(
        session: AsyncSession, quiz_id: int, question_id: int, user: User, answer_text: str
    ) -> Tuple[bool, bool, str]:
        """Ochiq javobli viktorina savolini tekshiradi: (accepted, is_correct, msg)."""
        quiz = await session.get(Quiz, quiz_id)
        qq = await session.get(QuizQuestion, question_id)
        if quiz is None or qq is None or qq.quiz_id != quiz.id:
            return False, False, "Quiz savoli topilmadi."
        if not quiz.is_active or quiz.status != "active":
            return False, False, "Bu quiz yakunlangan."
        guess = " ".join((answer_text or "").lower().split())
        if not guess:
            return False, False, ""
        accepted_list = [" ".join(str(a).lower().split()) for a in (qq.accepted_answers or []) if str(a).strip()]
        if not accepted_list and qq.options and 0 <= qq.correct_index < len(qq.options):
            accepted_list = [" ".join(str(qq.options[qq.correct_index]).lower().split())]
        if guess not in accepted_list:
            return False, False, ""

        try:
            async with session.begin_nested():
                session.add(
                    QuizAttempt(
                        quiz_id=quiz.id,
                        question_id=qq.id,
                        user_id=user.id,
                        selected_index=0,
                        is_correct=True,
                    )
                )
                await session.flush()
        except IntegrityError:
            return False, False, "Siz bu savolga allaqachon javob bergansiz!"

        user.quiz_wins = int(user.quiz_wins or 0) + 1
        user.xp = int(user.xp or 0) + quiz.reward_xp
        user.level = max(1, 1 + (user.xp // 250))
        msg = f"🎉 <b>{user.display_name}</b> to‘g‘ri javob topdi! (+{quiz.reward_xp} XP"
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
        msg += ")"
        await GameplayService.add_season_points(session, user.id, points=15, xp=quiz.reward_xp, quiz_win=True)
        await session.flush()
        return True, True, msg

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
