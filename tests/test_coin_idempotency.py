import pytest
from sqlalchemy import select

from models.transaction import CoinTransaction
from models.user import User
from models.v2_features import GameSession
from services.gameplay_service import GameplayService
from services.user_service import UserService


@pytest.mark.asyncio
async def test_coin_mutations_are_idempotent_by_source_and_reference(db_session):
    user = await UserService.upsert_user(db_session, 99001, "coin-test", "Coin Test")
    starting_balance = user.coins

    await UserService.credit_coins(
        db_session, user.id, 500, "test_reward", "reward", reference_id="reward:1"
    )
    await UserService.credit_coins(
        db_session, user.id, 500, "test_reward", "retry", reference_id="reward:1"
    )
    await UserService.credit_coins(
        db_session, user.id, 200, "other_reward", "different source", reference_id="reward:1"
    )

    await UserService.debit_coins(
        db_session, user.id, 100, "test_spend", "spend", reference_id="spend:1"
    )
    await UserService.debit_coins(
        db_session, user.id, 100, "test_spend", "retry", reference_id="spend:1"
    )

    balance = await db_session.scalar(select(User.coins).where(User.id == user.id))
    assert balance == starting_balance + 500 + 200 - 100
    keyed_rows = await db_session.scalar(
        select(CoinTransaction.id).where(
            CoinTransaction.user_id == user.id,
            CoinTransaction.idempotency_key.is_not(None),
        )
    )
    assert keyed_rows is not None


@pytest.mark.asyncio
async def test_game_callback_retry_does_not_pay_twice(db_session):
    user = await UserService.upsert_user(db_session, 99002, "game-test", "Game Test")
    before = user.coins

    first = await GameplayService.play_dice_game(
        db_session, user.id, dice_value=6, guessed_number=6, request_id="callback-dice-1"
    )
    assert first[0] is True
    await db_session.flush()
    retry = await GameplayService.play_dice_game(
        db_session, user.id, dice_value=6, guessed_number=6, request_id="callback-dice-1"
    )

    assert retry[0] is False
    assert await db_session.scalar(select(User.coins).where(User.id == user.id)) == before + first[3]
    sessions = await db_session.scalar(
        select(GameSession.id).where(GameSession.request_id == "callback-dice-1")
    )
    assert sessions is not None


@pytest.mark.asyncio
async def test_mines_session_can_only_be_settled_once(db_session):
    user = await UserService.upsert_user(db_session, 99003, "mines-test", "Mines Test")
    before = user.coins
    ok, _, game_state = await GameplayService.start_mines_game(
        db_session, user.id, bet=50, forced_bombs=[4, 5], request_id="callback-mines-1"
    )
    assert ok and game_state is not None

    outcome, _, _ = await GameplayService.step_mines_game(db_session, user.id, game_state, 0)
    assert outcome == "safe"
    first, payout, _ = await GameplayService.cashout_mines_game(db_session, user.id, game_state)
    retry, _, _ = await GameplayService.cashout_mines_game(db_session, user.id, game_state)

    assert first == "cashout"
    assert retry == "invalid"
    assert await db_session.scalar(select(User.coins).where(User.id == user.id)) == before - 50 + payout
    game = await db_session.scalar(
        select(GameSession).where(GameSession.request_id == "callback-mines-1")
    )
    assert game is not None and game.status == "completed"
