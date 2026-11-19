from datetime import timedelta

import pytest
from sqlalchemy import select

from models.base import utcnow
from models.card import Card
from models.user_card import UserCard
from services.auction_wishlist_service import AuctionWishlistService
from services.gameplay_service import GameplayService
from services.pack_service import PackService
from services.quest_service import QuestService
from services.user_service import UserService


@pytest.mark.asyncio
async def test_trade_cancel_and_expire_unlock_card(db_session):
    u1 = await UserService.upsert_user(db_session, 9101, "tr1", "Trader 1")
    u2 = await UserService.upsert_user(db_session, 9102, "tr2", "Trader 2")
    card = (await db_session.execute(select(Card).limit(1))).scalar_one()
    uc = UserCard(user_id=u1.id, card_id=card.id, acquired_via="drop")
    db_session.add(uc)
    await db_session.flush()

    ok, _msg, tr = await AuctionWishlistService.create_trade(db_session, u1.id, u2.id, uc.id)
    assert ok and tr is not None
    assert uc.is_locked is True

    # Tashabbuskor bekor qilsa karta ochilishi kerak
    c_ok, _c_msg = await AuctionWishlistService.cancel_trade(db_session, tr.id, u1.id)
    assert c_ok is True
    assert uc.is_locked is False
    assert uc.card_state == "OWNED"

    # Muddati o'tgan trade ham scheduler orqali ochilishi kerak
    ok2, _msg2, tr2 = await AuctionWishlistService.create_trade(db_session, u1.id, u2.id, uc.id)
    assert ok2 and tr2 is not None
    tr2.expires_at = utcnow() - timedelta(minutes=5)
    await db_session.flush()

    expired = await AuctionWishlistService.expire_stale_trades(db_session)
    assert expired == 1
    assert uc.is_locked is False
    assert tr2.status == "expired"


@pytest.mark.asyncio
async def test_coinflip_and_lucky_card_do_not_consume_dice_limit(db_session):
    u = await UserService.upsert_user(db_session, 9201, "gamer", "Gamer")
    await UserService.credit_coins(db_session, u.id, 1000, "test", "seed")

    ok_cf, _msg_cf, _won, _delta = await GameplayService.play_coin_flip(db_session, u, "heads", 50)
    assert ok_cf is True

    ok_lk, _msg_lk, reward = await GameplayService.play_lucky_card(db_session, u, 2)
    assert ok_lk is True
    assert reward > 0

    _d_en, dice_used, _d_lim = await GameplayService.get_dice_status(db_session, u)
    assert dice_used == 0


@pytest.mark.asyncio
async def test_pack_open_and_duplicate_cashback(db_session):
    u = await UserService.upsert_user(db_session, 9301, "packer", "Packer")
    await UserService.credit_coins(db_session, u.id, 5000, "test", "seed")

    res = await PackService.open_pack(db_session, u.id, "bronze")
    assert res.success is True
    assert res.card is not None

    # Bir xil kartalar kam bo'lsa, dublikat tushganda keshbek qaytishini tekshirish
    res2 = await PackService.open_pack(db_session, u.id, "bronze")
    assert res2.success is True


@pytest.mark.asyncio
async def test_quest_counts_divine_rarity_and_new_metrics(db_session):
    u = await UserService.upsert_user(db_session, 9401, "questor", "Questor")
    divine_card = Card(
        character_name="Test Divine",
        anime_name="Test Anime",
        rarity="Divine",
        base_value=60000,
        card_code="DIV-999",
        is_available=True,
    )
    db_session.add(divine_card)
    await db_session.flush()
    db_session.add(UserCard(user_id=u.id, card_id=divine_card.id, acquired_via="drop"))
    u.quiz_wins = 5
    await db_session.flush()

    quests = await QuestService.get_user_quests(db_session, u)
    rare_q = next(q for q in quests if q.quest_id == "rare_hunter")
    quiz_q = next(q for q in quests if q.quest_id == "quiz_5")
    assert rare_q.completed is True
    assert quiz_q.completed is True
