import pytest
from sqlalchemy import select

from models.user import User
from services.card_level_service import CardLevelService
from services.user_service import UserService
from services.welcome_bonus_service import WELCOME_BONUS_COINS
from utils.constants import DEFAULT_RANK_DEFINITIONS


def test_default_rank_probabilities_total_100_percent():
    assert sum(float(rank["drop_probability"]) for rank in DEFAULT_RANK_DEFINITIONS) == 100


def test_choose_drop_rank_respects_configured_probability(monkeypatch):
    ranks = [
        {"name": "Basic", "order": 1, "drop_probability": 90},
        {"name": "Epic", "order": 3, "drop_probability": 10},
    ]
    seen = {}

    def fake_choices(seq, weights, k):
        seen["weights"] = list(weights)
        return [seq[1]]

    monkeypatch.setattr("services.card_level_service.random.choices", fake_choices)
    chosen = CardLevelService.choose_drop_rank(ranks, {1: 0.15, 3: 1.0})
    assert chosen["name"] == "Epic"
    assert seen["weights"] == [90.0, 10.0]


def test_zero_rank_multiplier_disables_rank(monkeypatch):
    ranks = [
        {"name": "Basic", "order": 1, "drop_probability": 90},
        {"name": "Epic", "order": 3, "drop_probability": 10},
    ]
    monkeypatch.setattr("services.card_level_service.random.choices", lambda seq, weights, k: [seq[0]])
    chosen = CardLevelService.choose_drop_rank(ranks, {1: 0.0, 3: 1.0})
    assert chosen["name"] == "Epic"


def test_event_boost_is_a_separate_rank_probability_modifier(monkeypatch):
    ranks = [
        {"name": "Basic", "order": 1, "drop_probability": 90},
        {"name": "Epic", "order": 3, "drop_probability": 10},
    ]
    seen = {}

    def fake_choices(seq, weights, k):
        seen["weights"] = list(weights)
        return [seq[1]]

    monkeypatch.setattr("services.card_level_service.random.choices", fake_choices)
    chosen = CardLevelService.choose_drop_rank(
        ranks,
        {1: 1.0, 3: 1.0},
        event_boosted_rarity="Epic",
        event_multiplier=5,
    )
    assert chosen["name"] == "Epic"
    assert seen["weights"] == [90.0, 50.0]


@pytest.mark.asyncio
async def test_new_welcome_bonus_is_granted_once_and_existing_balance_is_unchanged(db_session):
    new_user = await UserService.upsert_user(db_session, 99901, "new-user", "New")
    assert new_user.coins == 2_000 == WELCOME_BONUS_COINS

    existing_user = await UserService.upsert_user(db_session, 99902, "old-user", "Old")
    existing_user.coins = 40_000
    await db_session.flush()
    await UserService.upsert_user(db_session, 99902, "old-user", "Old")
    balance = await db_session.scalar(select(User.coins).where(User.id == existing_user.id))
    assert balance == 40_000
