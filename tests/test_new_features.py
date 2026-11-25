"""Yangi imkoniyatlar: boshlang'ich bonus, guruh mukofoti, birlashtirish, coin yuborish, yangi o'yinlar."""
import pytest
from sqlalchemy import select

from models.transaction import CoinTransaction
from models.user import User
from services.card_level_service import CardLevelService
from services.coin_gift_service import CoinGiftService
from services.extra_games_service import ExtraGamesService
from services.gameplay_service import GameplayService
from services.group_policy_service import GroupPolicyService
from services.settings_service import SettingsService
from services.user_service import UserService
from services.welcome_bonus_service import DEFAULT_WELCOME_BONUS, TX_NEW, WelcomeBonusService
from utils.game_catalog import ADMIN_GAMES, ANIM_GAMES, EXTRA_GAME_DEFAULTS


async def _user(session, tg_id: int, coins: int | None = None) -> User:
    user = await UserService.upsert_user(session, telegram_id=tg_id, username=f"u{tg_id}", first_name=f"U{tg_id}")
    if coins is not None:
        user.coins = coins
    await session.flush()
    return user


def test_default_welcome_bonus_is_2000():
    assert DEFAULT_WELCOME_BONUS == 2000


@pytest.mark.asyncio
async def test_welcome_bonus_is_configurable_and_only_for_new_users(db_session):
    old = await _user(db_session, 1001)
    old_coins = old.coins
    await WelcomeBonusService.set_amount(db_session, 2000)
    new = await _user(db_session, 1002)
    assert new.coins == 2000
    # eski foydalanuvchi coiniga tegilmaydi
    await db_session.refresh(old)
    assert old.coins == old_coins
    tx = (await db_session.execute(select(CoinTransaction).where(CoinTransaction.user_id == new.id, CoinTransaction.tx_type == TX_NEW))).scalars().all()
    assert len(tx) == 1 and tx[0].amount == 2000


@pytest.mark.asyncio
async def test_welcome_bonus_rejects_invalid_values(db_session):
    with pytest.raises(ValueError):
        await WelcomeBonusService.set_amount(db_session, -1)


@pytest.mark.asyncio
async def test_group_reward_rules(db_session):
    # standart: a'zo x 1000, kamida 5 ta haqiqiy a'zo
    assert (await GroupPolicyService.decide_reward(db_session, 4)).eligible is False
    d = await GroupPolicyService.decide_reward(db_session, 123)
    assert d.eligible and d.coins == 123_000
    assert (await GroupPolicyService.decide_reward(db_session, None)).eligible is False
    await SettingsService.set_group_reward_field(db_session, "per_member", False)
    await SettingsService.set_group_reward_field(db_session, "flat_coins", 777)
    assert (await GroupPolicyService.decide_reward(db_session, 50)).coins == 777
    await SettingsService.set_group_reward_field(db_session, "enabled", False)
    assert (await GroupPolicyService.decide_reward(db_session, 50)).eligible is False


@pytest.mark.asyncio
async def test_merge_options_cover_all_ranks_and_can_be_disabled(db_session):
    cfg = await CardLevelService.get_config(db_session)
    orders = [o for o, _c in CardLevelService.merge_options(cfg)]
    assert orders == [1, 2, 3, 4, 5]
    await CardLevelService.set_config_value(db_session, "merge_cost_2", 0)
    cfg = await CardLevelService.get_config(db_session)
    assert 2 not in [o for o, _c in CardLevelService.merge_options(cfg)]


@pytest.mark.asyncio
async def test_coin_gift_moves_coins_and_respects_limits(db_session):
    a = await _user(db_session, 2001, coins=1000)
    b = await _user(db_session, 2002, coins=0)
    ok, _msg, received, new_balance = await CoinGiftService.send_coins(db_session, a.id, b.id, 400)
    assert ok and received == 400 and new_balance == 600
    await db_session.refresh(b)
    assert b.coins == 400
    # yetarli coin yo'q
    ok, *_ = await CoinGiftService.send_coins(db_session, a.id, b.id, 5000)
    assert not ok
    # o'ziga yuborib bo'lmaydi
    ok, *_ = await CoinGiftService.send_coins(db_session, a.id, a.id, 100)
    assert not ok
    # kunlik limit
    await CoinGiftService.set_field(db_session, "daily_limit", 500)
    ok, *_ = await CoinGiftService.send_coins(db_session, a.id, b.id, 200)
    assert not ok  # 400 allaqachon yuborilgan, 200 limitdan oshadi


@pytest.mark.asyncio
async def test_coin_gift_fee(db_session):
    a = await _user(db_session, 2101, coins=1000)
    b = await _user(db_session, 2102, coins=0)
    await CoinGiftService.set_field(db_session, "fee_percent", 10)
    ok, _msg, received, new_balance = await CoinGiftService.send_coins(db_session, a.id, b.id, 100)
    assert ok and received == 90 and new_balance == 900


def test_game_catalog_matches_default_config():
    from utils.constants import DEFAULT_GAME_CONFIG

    known = set(DEFAULT_GAME_CONFIG) | set(EXTRA_GAME_DEFAULTS)
    for game in ADMIN_GAMES:
        assert game["toggle"] in known or game["key"] == "pack", game["key"]
        for field, *_ in game["fields"]:
            assert field in known, field


@pytest.mark.asyncio
async def test_animated_game_pays_jackpot_and_enforces_limit(db_session):
    user = await _user(db_session, 3001, coins=1000)
    _emoji, _title, jackpot_value, *_ = ANIM_GAMES["dart"]
    ok, _msg, payout, _net = await ExtraGamesService.play_animated(db_session, user.id, "dart", jackpot_value, 100)
    assert ok and payout == 400  # standart 4x
    cfg = await GameplayService.get_game_config(db_session)
    cfg["dart_daily_limit"] = 1
    await SettingsService.set_value(db_session, "game_config", cfg)
    ok, *_ = await ExtraGamesService.play_animated(db_session, user.id, "dart", jackpot_value, 100)
    assert not ok


@pytest.mark.asyncio
async def test_disabled_game_cannot_be_played(db_session):
    user = await _user(db_session, 3101, coins=1000)
    cfg = await GameplayService.get_game_config(db_session)
    cfg["chest_enabled"] = False
    await SettingsService.set_value(db_session, "game_config", cfg)
    ok, *_ = await ExtraGamesService.play_chest(db_session, user.id, 1)
    assert not ok
