from datetime import timedelta

import pytest
from sqlalchemy import select

from models.base import ensure_utc, utcnow
from models.card import Card
from models.user import User
from models.user_card import UserCard
from services.auction_wishlist_service import AuctionWishlistService
from services.bot_store_service import BotStoreService
from services.card_level_service import CardLevelService
from services.extra_games_service import ExtraGamesService
from services.gameplay_service import GameplayService
from services.market_service import MarketService
from services.offer_service import OfferService
from services.search_service import SearchService
from services.settings_service import SettingsService
from services.user_service import UserService
from utils.callback_data import (
    AdmV2Cb,
    AdminCb,
    AucCb,
    BotStoreCb,
    CollectionCb,
    GameCb,
    MarketCb,
    NavCb,
    OfferCb,
    QuizCb,
    RankingCb,
    SearchCb,
    SuggestCb,
    SupCb,
    TradeCb,
    WishCb,
)


async def _user(session, tg_id: int, coins: int = 0) -> User:
    u = await UserService.upsert_user(session, tg_id, f"u{tg_id}", f"U{tg_id}")
    if coins:
        await UserService.credit_coins(session, u.id, coins, "test", "seed", f"seed:{tg_id}")
    return u


@pytest.mark.asyncio
async def test_duplicate_merge_and_max_level_refund(db_session):
    user = await _user(db_session, 8101, coins=0)
    card = (await db_session.execute(select(Card).where(Card.rarity == "Basic").limit(1))).scalar_one()
    await CardLevelService.set_config_value(db_session, "max_level", 3)
    await CardLevelService.set_config_value(db_session, "dup_refund_percent", 50)

    uc = UserCard(user_id=user.id, card_id=card.id, level=2, acquired_via="drop")
    db_session.add(uc)
    await db_session.flush()

    res1 = await CardLevelService.apply_duplicate(db_session, user.id, card)
    assert res1 is not None and not res1.maxed and res1.level_before == 2 and res1.level_after == 3

    before = int(user.coins or 0)
    res2 = await CardLevelService.apply_duplicate(db_session, user.id, card)
    assert res2 is not None and res2.maxed and res2.refund > 0
    await db_session.refresh(user)
    assert int(user.coins) == before + res2.refund


@pytest.mark.asyncio
async def test_referral_reward_and_idempotency(db_session):
    inviter = await _user(db_session, 8102, coins=0)
    invitee = await _user(db_session, 8103, coins=0)
    await SettingsService.set_referral_config(db_session, inviter_coins=1500, invitee_coins=800)

    inv_before = int(inviter.coins or 0)
    ee_before = int(invitee.coins or 0)

    first = await UserService.apply_referral(db_session, invitee, inviter.telegram_id)
    assert first is not None
    second = await UserService.apply_referral(db_session, invitee, inviter.telegram_id)
    assert second is None

    await db_session.refresh(inviter)
    await db_session.refresh(invitee)
    assert int(inviter.coins) == inv_before + 1500
    assert int(invitee.coins) == ee_before + 800
    assert inviter.referral_count == 1


@pytest.mark.asyncio
async def test_bot_store_sell_discount_buy_and_duplicate_merge(db_session):
    seller = await _user(db_session, 8104, coins=0)
    buyer = await _user(db_session, 8105, coins=50000)

    await GameplayService.update_single_rank_attr(db_session, "Epic", base_value=10000)
    await CardLevelService.set_config_value(db_session, "bot_sell_level_bonus_pct", 10)
    await SettingsService.set_bot_store_field(db_session, "sell_discount_pct", 20)

    epic = (await db_session.execute(select(Card).where(Card.rarity == "Epic").limit(1))).scalar_one()
    epic.dropped_count = 1
    uc = UserCard(user_id=seller.id, card_id=epic.id, level=2, acquired_via="drop")
    db_session.add(uc)
    await db_session.flush()

    quote = await MarketService.get_bot_sell_quote(db_session, seller.id, uc.id)
    assert quote is not None
    assert quote.base_rank_value == 10000
    assert quote.bonus_coins == 1000
    assert quote.total_price == 11000

    sell_res = await MarketService.sell_to_bot(db_session, seller.id, uc.id)
    assert sell_res.success and sell_res.price_paid == 11000

    # Admin adds listing and buyer already owns card -> duplicate level up
    ok_add, _msg, listing = await BotStoreService.admin_add_listing(db_session, epic.id, price_coins=5000, card_level=1)
    assert ok_add and listing is not None

    existing_uc = UserCard(user_id=buyer.id, card_id=epic.id, level=1, acquired_via="drop")
    db_session.add(existing_uc)
    await db_session.flush()

    buy_res = await BotStoreService.buy_listing(db_session, listing.id, buyer.id)
    assert buy_res.success and buy_res.is_duplicate and buy_res.level_after == 2


@pytest.mark.asyncio
async def test_commission_calculation(db_session):
    await SettingsService.set_commission_field(db_session, "market_buy", enabled=True, percent=5)
    cfg = await SettingsService.get_commission_config(db_session)
    fee, pct = SettingsService.compute_commission(10000, cfg, "market_buy")
    assert fee == 500 and pct == 5

    await SettingsService.set_commission_field(db_session, "market_buy", enabled=False)
    cfg2 = await SettingsService.get_commission_config(db_session)
    fee2, pct2 = SettingsService.compute_commission(10000, cfg2, "market_buy")
    assert fee2 == 0 and pct2 == 0


@pytest.mark.asyncio
async def test_auction_anti_snipe_20_min_extension(db_session):
    bidder = await _user(db_session, 8106, coins=20000)
    card = (await db_session.execute(select(Card).limit(1))).scalar_one()

    ok, _m, auc = await AuctionWishlistService.create_auction(
        db_session,
        card_id=card.id,
        start_price=1000,
        hours=1,
        created_by_tg_id=999999,
        min_step=100,
    )
    assert ok and auc is not None
    # Set ends_at within final 5 minutes
    auc.ends_at = utcnow() + timedelta(minutes=5)
    old_end = ensure_utc(auc.ends_at)
    await db_session.flush()

    ok_bid, msg = await AuctionWishlistService.place_bid(db_session, auc.id, bidder, 1200)
    assert ok_bid
    await db_session.refresh(auc)
    assert ensure_utc(auc.ends_at) >= old_end + timedelta(minutes=14)
    assert "uzaytirildi" in msg.lower()


@pytest.mark.asyncio
async def test_card_for_card_offer_and_trade_cancel_unlock(db_session):
    owner = await _user(db_session, 8107, coins=0)
    buyer = await _user(db_session, 8108, coins=0)
    cards = (await db_session.execute(select(Card).limit(3))).scalars().all()

    uc_target = UserCard(user_id=owner.id, card_id=cards[0].id, acquired_via="drop")
    uc_offer = UserCard(user_id=buyer.id, card_id=cards[1].id, acquired_via="drop")
    uc_trade = UserCard(user_id=owner.id, card_id=cards[2].id, acquired_via="drop")
    db_session.add_all([uc_target, uc_offer, uc_trade])
    await db_session.flush()

    off_res = await OfferService.create_card_offer(db_session, buyer.id, uc_target.id, uc_offer.id)
    assert off_res.success and off_res.offer is not None
    await db_session.refresh(uc_offer)
    assert uc_offer.is_locked and uc_offer.card_state == "OFFER"

    acc_res = await OfferService.accept_offer(db_session, off_res.offer.id, owner.id)
    assert acc_res.success
    await db_session.refresh(uc_target)
    await db_session.refresh(uc_offer)
    assert uc_target.user_id == buyer.id and not uc_target.is_locked
    assert uc_offer.user_id == owner.id and not uc_offer.is_locked

    ok_t, _mt, tr = await AuctionWishlistService.create_trade(db_session, owner.id, buyer.id, uc_trade.id)
    assert ok_t and tr is not None
    await db_session.refresh(uc_trade)
    assert uc_trade.is_locked

    ok_c, _mc = await AuctionWishlistService.cancel_trade(db_session, tr.id, owner.id)
    assert ok_c
    await db_session.refresh(uc_trade)
    assert not uc_trade.is_locked and uc_trade.card_state == "OWNED"


@pytest.mark.asyncio
async def test_wishlist_blocks_owned_card_and_digit_search(db_session):
    user = await _user(db_session, 8109, coins=0)
    card = (await db_session.execute(select(Card).limit(1))).scalar_one()
    card.dropped_count = 1
    uc = UserCard(user_id=user.id, card_id=card.id, acquired_via="drop")
    db_session.add(uc)
    await db_session.flush()

    ok, msg = await AuctionWishlistService.toggle_wishlist(db_session, user.id, card.id)
    assert not ok and "allaqachon bor" in msg

    cat_cards, cat_total = await SearchService.search_catalog_cards(db_session, mode="card_id", query=str(card.id))
    assert cat_total >= 1 and any(c.id == card.id for c in cat_cards)

    g_items, g_total = await SearchService.search_global_cards(db_session, mode="card_id", query=str(card.id))
    assert g_total >= 1 and any(it.card.id == card.id for it in g_items)


@pytest.mark.asyncio
async def test_merge_exclusions_and_daily_limit(db_session):
    user = await _user(db_session, 8110, coins=0)
    await CardLevelService.set_config_value(db_session, "merge_enabled", True)
    await CardLevelService.set_config_value(db_session, "merge_count", 2)
    await CardLevelService.set_config_value(db_session, "merge_daily_limit", 1)

    basics = (await db_session.execute(select(Card).where(Card.rarity == "Basic").limit(5))).scalars().all()
    if len(basics) < 5:
        pytest.skip("Not enough Basic seed cards")

    # 3 ineligible cards (level > 1, favorite, showcased) + 2 eligible cards
    db_session.add_all([
        UserCard(user_id=user.id, card_id=basics[0].id, level=2, acquired_via="drop"),
        UserCard(user_id=user.id, card_id=basics[1].id, level=1, is_favorite=True, acquired_via="drop"),
        UserCard(user_id=user.id, card_id=basics[2].id, level=1, is_showcased=True, acquired_via="drop"),
        UserCard(user_id=user.id, card_id=basics[3].id, level=1, acquired_via="drop"),
        UserCard(user_id=user.id, card_id=basics[4].id, level=1, acquired_via="drop"),
    ])
    await db_session.flush()

    assert await CardLevelService.count_spare(db_session, user.id, 1) == 2

    ok, _msg, reward = await CardLevelService.merge(db_session, user.id, 1)
    assert ok and reward is not None

    # Second merge on same day hits daily limit 1
    ok2, msg2, _r2 = await CardLevelService.merge(db_session, user.id, 1)
    assert not ok2 and "limiti tugadi" in msg2


@pytest.mark.asyncio
async def test_seq_skill_game_win_and_wrong(db_session):
    user = await _user(db_session, 8111, coins=1000)
    ok, _m, st = await ExtraGamesService.start_seq_game(db_session, user.id, bet=100, forced_seq=[0, 1, 2, 3])
    assert ok and st is not None
    st["phase"] = "recall"

    for i in range(3):
        out, pay, _ = await ExtraGamesService.step_seq_game(db_session, user.id, st, i)
        assert out == "progress" and pay == 0
    out_win, win_pay, _ = await ExtraGamesService.step_seq_game(db_session, user.id, st, 3)
    assert out_win == "win" and win_pay == 220

    ok2, _m2, st2 = await ExtraGamesService.start_seq_game(db_session, user.id, bet=100, forced_seq=[0, 1, 2, 3])
    assert ok2 and st2 is not None
    st2["phase"] = "recall"
    out_bad, bad_pay, _ = await ExtraGamesService.step_seq_game(db_session, user.id, st2, 2)
    assert out_bad == "wrong" and bad_pay == 0 and st2["finished"]


def test_all_callback_data_fit_64_bytes():
    uid = 9999999999
    samples = [
        NavCb(owner_id=uid, target="profile_wishlist").pack(),
        CollectionCb(owner_id=uid, action="merge_ok", filter_val="Basic", sort_by="newest", page=99, item_id=99999).pack(),
        MarketCb(owner_id=uid, action="bsell_go", listing_id=99999, page=99).pack(),
        SearchCb(owner_id=uid, action="cat_view", mode="character", query="", page=99, user_card_id=99999).pack(),
        OfferCb(owner_id=uid, action="card_pick", offer_id=99999, user_card_id=99999, page=99).pack(),
        RankingCb(owner_id=uid, scope="activity", period="monthly").pack(),
        AdminCb(owner_id=uid, section="groups", action="group_view", target_id=99999, extra="active", page=99).pack(),
        AdmV2Cb(owner_id=uid, sec="cards", act="give_go", tid=99999, ext="in_auction", p=99).pack(),
        GameCb(owner_id=uid, act="seq_step", val=5, extra="100").pack(),
        AucCb(owner_id=uid, act="cancel_u", aid=99999, amt=999999, p=99).pack(),
        BotStoreCb(owner_id=uid, act="adm_pick", lid=99999, p=99).pack(),
        SuggestCb(owner_id=uid, act="adm", sid=99999, p=99, ext="Manhwa").pack(),
        SupCb(owner_id=uid, act="check_sub", tid=99999, p=99).pack(),
        TradeCb(owner_id=uid, act="accept", tid=99999, ucid=99999, p=99).pack(),
        WishCb(owner_id=uid, act="top", cid=99999, p=99).pack(),
        QuizCb(owner_id=uid, act="ans", qid=99999, opt=4).pack(),
    ]
    for packed in samples:
        assert len(packed.encode("utf-8")) <= 64, f"Callback exceeded 64 bytes ({len(packed.encode('utf-8'))}): {packed}"
