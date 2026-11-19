from dataclasses import dataclass
from typing import Dict, List, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.card import Card
from models.user import User
from models.user_card import UserCard
from services.settings_service import SettingsService
from services.user_service import UserService
from utils.constants import DEFAULT_RANK_DEFINITIONS, get_quest_definitions, sync_runtime_ranks


@dataclass
class QuestProgress:
    quest_id: str
    title: str
    desc: str
    current: int
    target: int
    reward: int
    completed: bool
    claimed: bool


class QuestService:
    @staticmethod
    async def _metrics(session: AsyncSession, user: User) -> Dict[str, int]:
        raw_ranks = await SettingsService.get_value(session, "rank_definitions", DEFAULT_RANK_DEFINITIONS)
        ranks = list(raw_ranks) if isinstance(raw_ranks, list) and raw_ranks else list(DEFAULT_RANK_DEFINITIONS)
        sync_runtime_ranks(ranks)
        high_names = [str(r.get("name", "")).strip().lower() for r in ranks if int(r.get("order", 0)) >= 3]
        if not high_names:
            high_names = ["epic", "legendary", "legend", "mythic", "divine"]
        high_cnt = int(
            (
                await session.execute(
                    select(func.count(UserCard.id))
                    .join(Card, Card.id == UserCard.card_id)
                    .where(
                        UserCard.user_id == user.id,
                        func.lower(Card.rarity).in_(high_names),
                    )
                )
            ).scalar_one()
            or 0
        )
        return {
            "total_cards": int(user.total_cards or 0),
            "high_rarity": high_cnt,
            "daily_streak": int(user.daily_streak or 0),
            "referrals": int(user.referral_count or 0),
            "quiz_wins": int(getattr(user, "quiz_wins", 0) or 0),
            "game_wins": int(getattr(user, "game_wins", 0) or 0),
            "trade_count": int(getattr(user, "trade_count", 0) or 0),
        }

    @staticmethod
    async def get_user_quests(session: AsyncSession, user: User) -> List[QuestProgress]:
        metrics = await QuestService._metrics(session, user)
        claimed_set = set((user.stats or {}).get("claimed_quests", []))
        items: List[QuestProgress] = []
        for q in get_quest_definitions():
            qid = str(q["id"])
            target = int(q["target"])
            cur = min(target, int(metrics.get(str(q["metric"]), 0)))
            completed = int(metrics.get(str(q["metric"]), 0)) >= target
            items.append(
                QuestProgress(
                    quest_id=qid,
                    title=str(q["title"]),
                    desc=str(q["desc"]),
                    current=cur,
                    target=target,
                    reward=int(q["reward"]),
                    completed=completed,
                    claimed=qid in claimed_set,
                )
            )
        return items

    @staticmethod
    async def claim_all_quests(session: AsyncSession, user_id: int) -> Tuple[int, List[str]]:
        user = await session.get(User, user_id)
        if user is None:
            return 0, []
        items = await QuestService.get_user_quests(session, user)
        claimable = [i for i in items if i.completed and not i.claimed]
        if not claimable:
            return 0, []

        total_reward = sum(i.reward for i in claimable)
        claimed_ids = list((user.stats or {}).get("claimed_quests", []))
        for i in claimable:
            if i.quest_id not in claimed_ids:
                claimed_ids.append(i.quest_id)
        stats = dict(user.stats or {})
        stats["claimed_quests"] = claimed_ids
        user.stats = stats

        if total_reward > 0:
            await UserService.credit_coins(
                session=session,
                user_id=user.id,
                amount=total_reward,
                tx_type="quest_reward",
                description=f"Quests claimed: {', '.join(i.quest_id for i in claimable)}",
                reference_id=f"qst:{','.join(i.quest_id for i in claimable)}",
            )
        await session.flush()
        return total_reward, [i.title for i in claimable]
