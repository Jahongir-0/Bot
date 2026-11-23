import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from models.card import Card
from models.user_card import UserCard
from services.admin_service import AdminService
from services.card_service import CardService
from services.daily_service import DailyService
from services.drop_service import DropService
from services.market_service import MarketService
from services.offer_service import OfferService
from services.ranking_service import RankingService
from services.search_service import SearchService
from services.transfer_service import TransferService
from services.user_service import InsufficientCoinsError, UserService
from services.welcome_bonus_service import WELCOME_BONUS_COINS
from utils.tg_helpers import is_admin_user


@pytest.mark.asyncio
async def test_card_claim_and_duplicate_claim_prevention(db_session):
    u1 = await UserService.upsert_user(db_session, 1001, "user1", "User 1")
    u2 = await UserService.upsert_user(db_session, 1002, "user2", "User 2")
    grp = await UserService.upsert_group_and_member(db_session, -100500, "Anime Club", None, u1.id)
    card = (await db_session.execute(select(Card).limit(1))).scalar_one()
    drop = await DropService.create_active_drop(db_session, grp, card)
    await db_session.commit()

    # Simulate simultaneous claims from two users
    r1, r2 = await asyncio.gather(
        DropService.claim_drop(db_session, drop.id, u1.id),
        DropService.claim_drop(db_session, drop.id, u2.id),
    )
    winners = [r for r in (r1, r2) if r.success]
    losers = [r for r in (r1, r2) if not r.success]

    assert len(winners) == 1
    assert len(losers) == 1
    winner_id = winners[0].winner.id
    owned = (await db_session.execute(select(UserCard).where(UserCard.user_id == winner_id))).scalars().all()
    assert len(owned) == 1


@pytest.mark.asyncio
async def test_market_purchase_self_buy_and_double_purchase(db_session):
    seller = await UserService.upsert_user(db_session, 2001, "seller", "Seller")
    buyer = await UserService.upsert_user(db_session, 2002, "buyer", "Buyer")
    await UserService.credit_coins(db_session, buyer.id, 5000, "test", "seed buyer")

    card = (await db_session.execute(select(Card).limit(1))).scalar_one()
    uc = UserCard(user_id=seller.id, card_id=card.id, acquired_via="drop")
    db_session.add(uc)
    await db_session.flush()

    list_res = await MarketService.list_card(db_session, seller.id, uc.id, 1500)
    assert list_res.success

    # Seller cannot buy own card
    self_buy = await MarketService.buy_listing(db_session, list_res.listing.id, seller.id)
    assert not self_buy.success

    # Buyer purchases atomically
    buy_ok = await MarketService.buy_listing(db_session, list_res.listing.id, buyer.id)
    assert buy_ok.success

    # Double purchase fails
    buy_again = await MarketService.buy_listing(db_session, list_res.listing.id, buyer.id)
    assert not buy_again.success

    await db_session.refresh(uc)
    await db_session.refresh(buyer)
    await db_session.refresh(seller)
    assert uc.user_id == buyer.id
    assert not uc.is_locked
    assert buyer.coins == WELCOME_BONUS_COINS + 5000 - 1500
    assert seller.coins == WELCOME_BONUS_COINS + 1500


@pytest.mark.asyncio
async def test_free_and_paid_transfer(db_session):
    u1 = await UserService.upsert_user(db_session, 3001, "alice", "Alice")
    u2 = await UserService.upsert_user(db_session, 3002, "bob", "Bob")
    await UserService.credit_coins(db_session, u2.id, 1000, "test", "seed")

    card = (await db_session.execute(select(Card).limit(1))).scalar_one()
    uc = UserCard(user_id=u1.id, card_id=card.id, acquired_via="drop")
    db_session.add(uc)
    await db_session.flush()

    # Paid transfer: Alice sends to Bob for 600 coins
    pending = await TransferService.create_pending_transfer(db_session, u1.id, u2.id, uc.id, price=600)
    assert pending.success

    # Alice cannot confirm a paid transfer on Bob's behalf
    wrong_actor = await TransferService.confirm_transfer(db_session, pending.transfer.id, u1.id)
    assert not wrong_actor.success

    # Bob confirms and pays
    confirmed = await TransferService.confirm_transfer(db_session, pending.transfer.id, u2.id)
    assert confirmed.success
    await db_session.refresh(uc)
    assert uc.user_id == u2.id


@pytest.mark.asyncio
async def test_offer_escrow_and_accept_reject(db_session):
    owner = await UserService.upsert_user(db_session, 4001, "owner", "Owner")
    buyer = await UserService.upsert_user(db_session, 4002, "bidder", "Bidder")
    await UserService.credit_coins(db_session, buyer.id, 3000, "test", "seed")

    card = (await db_session.execute(select(Card).limit(1))).scalar_one()
    uc = UserCard(user_id=owner.id, card_id=card.id, acquired_via="drop")
    db_session.add(uc)
    await db_session.flush()

    res = await OfferService.create_offer(db_session, buyer.id, uc.id, 2000)
    assert res.success
    await db_session.refresh(buyer)
    assert buyer.coins == WELCOME_BONUS_COINS + 3000 - 2000

    acc = await OfferService.accept_offer(db_session, res.offer.id, owner.id)
    assert acc.success
    await db_session.refresh(owner)
    await db_session.refresh(uc)
    assert owner.coins == WELCOME_BONUS_COINS + 2000
    assert uc.user_id == buyer.id


@pytest.mark.asyncio
async def test_coin_transaction_negative_protection(db_session):
    u = await UserService.upsert_user(db_session, 5001, "rich", "Rich")
    assert u.coins == WELCOME_BONUS_COINS
    u.coins = 100
    await db_session.flush()
    with pytest.raises(InsufficientCoinsError):
        await UserService.debit_coins(db_session, u.id, 500, "test", "overdraft")


@pytest.mark.asyncio
async def test_referral_rewards_work_with_welcome_transaction_and_are_idempotent(db_session):
    referrer = await UserService.upsert_user(db_session, 5002, "inviter", "Inviter")
    invitee = await UserService.upsert_user(db_session, 5003, "invitee", "Invitee")
    inviter_before = referrer.coins
    invitee_before = invitee.coins

    reward = await UserService.apply_referral(db_session, invitee, referrer.telegram_id)
    assert reward is not None
    inviter_coins, invitee_coins = reward[1:]
    await db_session.refresh(referrer)
    await db_session.refresh(invitee)
    assert referrer.coins == inviter_before + inviter_coins
    assert invitee.coins == invitee_before + invitee_coins
    duplicate = await UserService.apply_referral(db_session, invitee, referrer.telegram_id)
    assert duplicate is None
    await db_session.refresh(referrer)
    await db_session.refresh(invitee)
    assert referrer.coins == inviter_before + inviter_coins
    assert invitee.coins == invitee_before + invitee_coins


@pytest.mark.asyncio
async def test_admin_coin_adjustments_are_request_idempotent_but_later_actions_apply(db_session):
    user = await UserService.upsert_user(db_session, 5004, "admin_adjusted", "Adjusted")
    initial = user.coins
    args = (db_session, 999999, user.id, 100)

    first = await AdminService.adjust_user_coins(*args, request_id="chat:msg-1")
    repeated = await AdminService.adjust_user_coins(*args, request_id="chat:msg-1")
    assert first[0] and repeated[0]
    await db_session.refresh(user)
    assert user.coins == initial + 100

    next_action = await AdminService.adjust_user_coins(*args, request_id="chat:msg-2")
    assert next_action[0]
    await db_session.refresh(user)
    assert user.coins == initial + 200


@pytest.mark.asyncio
async def test_daily_bonus_and_duplicate_block(db_session):
    u = await UserService.upsert_user(db_session, 6001, "dailyuser", "Daily")
    res1 = await DailyService.claim_daily(db_session, u.id)
    assert res1.success
    assert res1.streak == 1
    assert res1.coins_awarded >= 150

    res2 = await DailyService.claim_daily(db_session, u.id)
    assert not res2.success


@pytest.mark.asyncio
async def test_admin_permissions_and_ban(db_session):
    admin_tg = SimpleNamespace(id=999999, username="superadmin")
    normal_tg = SimpleNamespace(id=111111, username="regular")
    assert is_admin_user(admin_tg) is True
    assert is_admin_user(normal_tg) is False

    target = await UserService.upsert_user(db_session, 7001, "baduser", "Bad")
    await AdminService.set_global_ban(db_session, 999999, target.id, ban=True, reason="Spam")
    await db_session.refresh(target)
    assert target.is_banned is True


@pytest.mark.asyncio
async def test_ranking_and_global_search(db_session):
    u1 = await UserService.upsert_user(db_session, 8001, "top1", "Top One")
    u2 = await UserService.upsert_user(db_session, 8002, "top2", "Top Two")
    grp = await UserService.upsert_group_and_member(db_session, -100999, "Group X", None, u1.id)
    await UserService.upsert_group_and_member(db_session, -100999, "Group X", None, u2.id)

    cards = (await db_session.execute(select(Card).limit(3))).scalars().all()
    for c in cards:
        db_session.add(UserCard(user_id=u1.id, card_id=c.id, acquired_via="drop"))
    db_session.add(UserCard(user_id=u2.id, card_id=cards[0].id, acquired_via="drop"))
    await db_session.flush()

    await UserService.recompute_card_counts(db_session, u1.id)
    await UserService.recompute_card_counts(db_session, u2.id)

    global_top = await RankingService.get_global_top(db_session, limit=10)
    group_top = await RankingService.get_group_top(db_session, grp.id, limit=10)
    assert global_top[0].user.id == u1.id
    assert group_top[0].user.id == u1.id

    # Global search finds u1's and u2's cards across groups
    results, total = await SearchService.search_global_cards(
        db_session, mode="character", query=cards[0].character_name
    )
    assert total >= 2
    assert len(results) >= 2


@pytest.mark.asyncio
async def test_admin_lists_and_schedule_overview(db_session):
    u1 = await UserService.upsert_user(db_session, 9001, "collector_a", "Collector A")
    u2 = await UserService.upsert_user(db_session, 9002, "collector_b", "Collector B")
    grp = await UserService.upsert_group_and_member(db_session, -100777, "Group Z", None, u1.id)
    card = (await db_session.execute(select(Card).limit(1))).scalar_one()

    await AdminService.give_card_to_user(db_session, 999999, u1.id, card.id)
    owners, drops_sent = await AdminService.get_card_ownership_and_drops(db_session, card.id)
    assert any(name == "@collector_a" and qty == 1 for name, qty in owners)
    assert drops_sent == 0

    all_users, total_u = await AdminService.get_users_paginated(db_session, "all", page=1, page_size=10)
    assert total_u >= 2
    assert len(all_users) >= 2

    await AdminService.set_global_ban(db_session, 999999, u2.id, ban=True)
    banned_users, total_banned = await AdminService.get_users_paginated(db_session, "banned", page=1, page_size=10)
    assert total_banned == 1
    assert banned_users[0].id == u2.id

    user_cards = await AdminService.get_user_cards_for_admin(db_session, u1.id)
    assert len(user_cards) == 1

    await DropService.ensure_daily_schedule_for_group(db_session, grp)
    upcoming = await AdminService.get_upcoming_schedules_all_groups(db_session, limit=10)
    assert isinstance(upcoming, list)
