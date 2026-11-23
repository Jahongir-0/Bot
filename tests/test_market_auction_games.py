from datetime import timedelta

import pytest
from sqlalchemy import select

from models.base import utcnow
from models.card import Card
from models.market import Offer
from models.user import User
from models.user_card import UserCard
from services.auction_wishlist_service import AuctionWishlistService
from services.card_level_service import CardLevelService
from services.gameplay_service import GameplayService
from services.market_service import MarketService
from services.card_service import CardService
from services.settings_service import SettingsService
from services.user_service import UserService


async def _user(session, tg_id: int, coins: int = 0) -> User:
    u = await UserService.upsert_user(session, tg_id, f"u{tg_id}", f"U{tg_id}")
    u.coins = coins
    await session.flush()
    return u


@pytest.mark.asyncio
async def test_bot_market_quote_and_sell_respects_rank_and_level_bonus(db_session):
    seller = await _user(db_session, 7001, coins=100)
    buyer = await _user(db_session, 7002, coins=5000)

    # Admin updates Epic rank price to 10,000 coins and level bonus to 10%
    await GameplayService.update_single_rank_attr(db_session, "Epic", base_value=10000)
    await CardLevelService.set_config_value(db_session, "bot_sell_level_bonus_pct", 10)

    epic_card = (
        await db_session.execute(select(Card).where(Card.rarity == "Epic").limit(1))
    ).scalar_one()
    bonus_card = await CardService.create_card(
        db_session, "Epic Level Bonus", "Test Anime", "https://x/epic-bonus.png", "Epic", 5000
    )
    epic_card.dropped_count = 2
    bonus_card.dropped_count = 2
    uc_lvl1 = UserCard(user_id=seller.id, card_id=epic_card.id, acquired_via="drop", level=1)
    uc_lvl3 = UserCard(user_id=seller.id, card_id=bonus_card.id, acquired_via="drop", level=3)
    db_session.add_all([uc_lvl1, uc_lvl3])
    await db_session.flush()

    ok_q1, _, q1 = await MarketService.get_bot_sell_quote(db_session, seller.id, uc_lvl1.id)
    ok_q3, _, q3 = await MarketService.get_bot_sell_quote(db_session, seller.id, uc_lvl3.id)
    assert ok_q1 and q1 is not None
    assert ok_q3 and q3 is not None
    assert q1 is not None and q1.base_rank_value == 10000 and q1.bonus_coins == 0 and q1.total_price == 10000
    assert q3 is not None and q3.base_rank_value == 10000 and q3.bonus_coins == 2000 and q3.total_price == 12000

    # Pending coin offer on uc_lvl3 should be refunded when sold to bot
    db_session.add(
        Offer(
            user_card_id=uc_lvl3.id,
            card_id=bonus_card.id,
            buyer_id=buyer.id,
            owner_id=seller.id,
            amount=500,
            status="pending",
            expires_at=utcnow() + timedelta(hours=12),
        )
    )
    buyer.coins -= 500
    await db_session.flush()

    before_seller = int(seller.coins or 0)
    res = await MarketService.sell_to_bot(db_session, seller.id, uc_lvl3.id)
    assert res.success and res.price_paid == 12000
    await db_session.commit()

    await db_session.refresh(seller)
    await db_session.refresh(buyer)
    await db_session.refresh(epic_card)
    assert int(seller.coins) == before_seller + 12000
    assert int(buyer.coins) == 5000  # seed + 500 escrowed - 500 refund
    assert await db_session.get(UserCard, uc_lvl3.id) is None
    assert bonus_card.dropped_count == 1


@pytest.mark.asyncio
async def test_admin_can_update_rank_price_and_level_cost_factor(db_session):
    await GameplayService.update_single_rank_attr(db_session, "Legend", base_value=20000)
    rcfg = await SettingsService.get_rarity_config(db_session)
    assert rcfg["Legend"]["value"] == 20000

    await CardLevelService.set_config_value(db_session, "coin_cost_factor", 0.5)
    cfg = await CardLevelService.get_config(db_session)
    assert float(cfg["coin_cost_factor"]) == pytest.approx(0.5)
    assert CardLevelService.upgrade_cost(20000, 1, cfg) == 10000


@pytest.mark.asyncio
async def test_user_created_auction_lifecycle_and_cancel(db_session):
    seller = await _user(db_session, 7101, coins=0)
    bidder = await _user(db_session, 7102, coins=10000)
    cards = (await db_session.execute(select(Card).limit(2))).scalars().all()
    uc1 = UserCard(user_id=seller.id, card_id=cards[0].id, acquired_via="drop")
    uc2 = UserCard(user_id=seller.id, card_id=cards[1].id, acquired_via="drop")
    db_session.add_all([uc1, uc2])
    await db_session.flush()

    # Cancelable while 0 bids
    ok1, _m1, auc1 = await AuctionWishlistService.create_auction(
        db_session,
        card_id=cards[0].id,
        start_price=1000,
        hours=12,
        created_by_tg_id=seller.telegram_id,
        min_step=100,
        seller_user_id=seller.id,
        user_card_id=uc1.id,
    )
    assert ok1 and auc1 is not None
    await db_session.refresh(uc1)
    assert uc1.is_locked and uc1.card_state == "AUCTION"

    ok_can, _mc = await AuctionWishlistService.cancel_user_auction(db_session, auc1.id, seller.id)
    assert ok_can
    await db_session.refresh(uc1)
    assert not uc1.is_locked and uc1.card_state == "OWNED"

    # Bidding + finalization pays seller and transfers ownership
    ok2, _m2, auc2 = await AuctionWishlistService.create_auction(
        db_session,
        card_id=cards[1].id,
        start_price=2000,
        hours=24,
        created_by_tg_id=seller.telegram_id,
        min_step=200,
        seller_user_id=seller.id,
        user_card_id=uc2.id,
    )
    assert ok2 and auc2 is not None
    ok_bid, _mb = await AuctionWishlistService.place_bid(db_session, auc2.id, bidder, 2500)
    assert ok_bid

    ok_can2, _mc2 = await AuctionWishlistService.cancel_user_auction(db_session, auc2.id, seller.id)
    assert not ok_can2  # cannot cancel after bids exist

    before_seller = int(seller.coins or 0)
    ok_fin, _mf = await AuctionWishlistService.admin_finalize_auction(db_session, auc2.id)
    assert ok_fin
    await db_session.commit()

    await db_session.refresh(seller)
    await db_session.refresh(uc2)
    assert int(seller.coins) == before_seller + 2500
    assert uc2.user_id == bidder.id
    assert not uc2.is_locked


@pytest.mark.asyncio
async def test_new_mini_games_payouts_and_daily_limits(db_session):
    u = await _user(db_session, 7201, coins=5000)

    # 1. Slots 777 (dice_value=64 -> 10x) & limit enforcement
    ok, _msg, payout, net = await GameplayService.play_slots_game(db_session, u.id, dice_value=64, bet=100)
    assert ok and payout == 1000 and net == 900
    await SettingsService.set_value(
        db_session,
        "game_config",
        {**(await GameplayService.get_game_config(db_session)), "slots_daily_limit": 1, "mines_daily_limit": 2, "wheel_daily_limit": 1},
    )
    ok2, _msg2, _p2, _n2 = await GameplayService.play_slots_game(db_session, u.id, dice_value=1, bet=50)
    assert not ok2

    # 2. Mines: start -> 2 safe opens -> cashout; then hit bomb
    ok_m, _mm, st = await GameplayService.start_mines_game(db_session, u.id, bet=100, forced_bombs=[4, 5])
    assert ok_m and st is not None
    out1, pay1, _ = await GameplayService.step_mines_game(db_session, u.id, st, 0)
    out2, pay2, _ = await GameplayService.step_mines_game(db_session, u.id, st, 1)
    assert out1 == "safe" and pay1 == 135
    assert out2 == "safe" and pay2 == 185
    out_cash, cash_pay, _ = await GameplayService.cashout_mines_game(db_session, u.id, st)
    assert out_cash == "cashout" and cash_pay == 185 and st["finished"]

    ok_m2, _mm2, st2 = await GameplayService.start_mines_game(db_session, u.id, bet=100, forced_bombs=[0, 1])
    assert ok_m2 and st2 is not None
    out_boom, pay_boom, _ = await GameplayService.step_mines_game(db_session, u.id, st2, 0)
    assert out_boom == "boom" and pay_boom == 0 and st2["finished"]

    ok_m3, _mm3, _st3 = await GameplayService.start_mines_game(db_session, u.id, bet=100)
    assert not ok_m3  # daily limit 2 reached

    # 3. Wheel spin + limit 1
    ok_w, _mw, _cw = await GameplayService.play_wheel_game(db_session, u.id)
    assert ok_w
    ok_w2, _mw2, _cw2 = await GameplayService.play_wheel_game(db_session, u.id)
    assert not ok_w2
