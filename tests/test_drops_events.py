from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, update

from config.settings import get_settings
from models.card import Card
from models.limited_event import LimitedEvent
from models.user import User
from models.user_card import UserCard
from services.card_service import CardService
from services.collection_service import CollectionService
from services.drop_service import DropService
from services.event_service import EventService, _sign, verify_payload
from services.settings_service import SettingsService
from services.transfer_service import TransferService
from services.user_service import UserService
from utils.constants import DEFAULT_DROP_RULES
from utils.image_check import validate_image_url


async def _user(session, tg_id, coins=0):
    u = await UserService.upsert_user(session, tg_id, f"u{tg_id}", f"U{tg_id}")
    if coins:
        await UserService.credit_coins(session, u.id, coins, "test", "seed", f"seed:{tg_id}")
    return u


async def _coins(session, tg_id):
    return (await session.execute(select(User.coins).where(User.telegram_id == tg_id))).scalar_one()


async def _group(session, chat_id, owner):
    return await UserService.upsert_group_and_member(session, chat_id, f"G{chat_id}", None, owner.id)


# ---------------------------------------------------------------- jadval
def test_daily_drop_count_follows_member_count():
    f = DropService.compute_daily_drop_count
    for members, expected in [(5, 2), (20, 2), (30, 3), (40, 4), (100, 10), (140, 14), (1000, 100)]:
        assert f(members, DEFAULT_DROP_RULES) == expected


def test_slot_times_are_spread_over_the_whole_day():
    tz = ZoneInfo(get_settings().timezone)
    day = date.today() + timedelta(days=1)
    now_local = datetime.now(tz)
    for count in (2, 10, 100):
        times = DropService.generate_slot_times(day, tz, DEFAULT_DROP_RULES, count, now_local)
        assert len(times) == count
        assert times == sorted(times)
        assert all(t.date() == day for t in times)
        if count >= 10:
            gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
            # hammasi yarim soatda tashlab yuborilmaydi: eng kichik oraliq ham sezilarli
            assert min(gaps) > 2 * 60
            assert (times[-1] - times[0]).total_seconds() > 0.6 * 24 * 3600


@pytest.mark.asyncio
async def test_schedule_uses_member_count(db_session):
    owner = await _user(db_session, 8001)
    grp = await _group(db_session, -100801, owner)
    tomorrow = date.today() + timedelta(days=1)
    slots = await DropService.ensure_daily_schedule_for_group(
        db_session, grp, target_date=tomorrow, member_count=100
    )
    assert len(slots) == 10
    assert grp.daily_drop_count == 10
    assert all(s.card_id is None for s in slots)


# ---------------------------------------------------------------- nusxa qoidalari
@pytest.mark.asyncio
async def test_user_duplicate_drop_claim_levels_up_card(db_session):
    a = await _user(db_session, 8101)
    grp = await _group(db_session, -100811, a)
    card = (await db_session.execute(select(Card).where(Card.drop_limit >= 2).limit(1))).scalar_one()
    uc = UserCard(user_id=a.id, card_id=card.id, acquired_via="drop")
    db_session.add(uc)
    await db_session.flush()

    drop = await DropService.create_active_drop(db_session, grp, card, force=True)
    await db_session.commit()

    res = await DropService.claim_drop(db_session, drop.id, a.id)
    assert res.success and res.is_duplicate
    assert res.level_before == 1 and res.level_after == 2


@pytest.mark.asyncio
async def test_drop_limit_is_enforced(db_session):
    owner = await _user(db_session, 8201)
    grp = await _group(db_session, -100821, owner)
    card = (await db_session.execute(select(Card).where(Card.drop_limit == 1).limit(1))).scalar_one()
    first = await DropService.create_active_drop(db_session, grp, card)
    second = await DropService.create_active_drop(db_session, grp, card)
    assert first is not None and second is None
    assert card.dropped_count == 1


@pytest.mark.asyncio
async def test_same_card_is_not_dropped_twice_in_a_row_to_same_group(db_session):
    owner = await _user(db_session, 8301)
    g1 = await _group(db_session, -100831, owner)
    cards = (await db_session.execute(select(Card).order_by(Card.id))).scalars().all()
    a, b = cards[0], cards[1]
    for c in cards[2:]:
        c.is_available = False
    for c in (a, b):
        c.drop_limit = 2
    await db_session.flush()

    assert await DropService.create_active_drop(db_session, g1, a) is not None
    picked, _ = await DropService.pick_card_for_group(db_session, g1)
    assert picked is not None and picked.id == b.id  # a yaqinda shu guruhga tushgan


@pytest.mark.asyncio
async def test_no_eligible_card_returns_none(db_session):
    owner = await _user(db_session, 8302)
    g1 = await _group(db_session, -100832, owner)
    await db_session.execute(update(Card).values(is_available=False))
    picked, _ = await DropService.pick_card_for_group(db_session, g1)
    assert picked is None


@pytest.mark.asyncio
async def test_transfer_to_user_who_already_has_card_merges_and_levels_up(db_session):
    a = await _user(db_session, 8401)
    b = await _user(db_session, 8402)
    card = (await db_session.execute(select(Card).limit(1))).scalar_one()
    uc_a = UserCard(user_id=a.id, card_id=card.id, acquired_via="drop")
    uc_b = UserCard(user_id=b.id, card_id=card.id, acquired_via="drop")
    db_session.add_all([uc_a, uc_b])
    await db_session.flush()
    res = await TransferService.create_pending_transfer(
        db_session, from_user_id=a.id, to_user_id=b.id, user_card_id=uc_a.id, price=0
    )
    assert res.success and res.transfer is not None
    done = await TransferService.confirm_transfer(db_session, res.transfer.id, actor_user_id=a.id)
    assert done.success and done.is_duplicate and done.level_after == 2
    assert await db_session.get(UserCard, uc_a.id) is None
    await db_session.refresh(uc_b)
    assert uc_b.level == 2


@pytest.mark.asyncio
async def test_find_cards_by_name_for_give(db_session):
    u = await _user(db_session, 8501)
    cards = (await db_session.execute(select(Card).where(Card.character_name.ilike("%gojo%")))).scalars().all()
    assert cards
    db_session.add(UserCard(user_id=u.id, card_id=cards[0].id, acquired_via="drop"))
    await db_session.flush()
    found = await CollectionService.find_cards_by_name(db_session, u.id, "goj")
    assert len(found) == 1 and found[0][1].id == cards[0].id
    assert await CollectionService.find_cards_by_name(db_session, u.id, "yoq-ism") == []


# ---------------------------------------------------------------- rank sozlamalari
@pytest.mark.asyncio
async def test_rank_defaults_and_admin_changes(db_session):
    cfg = await SettingsService.get_rarity_config(db_session)
    assert cfg["Basic"] == {"value": 1000, "drops": 3}
    assert cfg["Elita"] == {"value": 2500, "drops": 2}
    assert cfg["Epic"] == {"value": 5000, "drops": 2}
    assert cfg["Legend"] == {"value": 10000, "drops": 1}
    assert cfg["Mythic"] == {"value": 25000, "drops": 1}

    await SettingsService.set_rarity_config(db_session, "Epic", value=6000, drops=4)
    cfg = await SettingsService.get_rarity_config(db_session)
    assert cfg["Epic"] == {"value": 6000, "drops": 4}
    with pytest.raises(ValueError):
        await SettingsService.set_rarity_config(db_session, "Epic", drops=0)

    card = await CardService.create_card(
        db_session, "Test Hero", "Test Anime", "https://example.com/a.png", "Epic",
        cfg["Epic"]["value"], drop_limit=cfg["Epic"]["drops"],
    )
    assert card.base_value == 6000 and card.drop_limit == 4


@pytest.mark.asyncio
async def test_invalid_image_urls_are_rejected():
    for bad in ("not a url", "ftp://example.com/a.png", "http://localhost/a.png", "http://127.0.0.1/a.jpg", ""):
        ok, err = await validate_image_url(bad)
        assert not ok and err


# ---------------------------------------------------------------- limitli event
async def _event(session, copies=2, coins=500, stars=None):
    cards, total = await EventService.list_candidates(session, page=1, page_size=50)
    assert total > 0
    res = await EventService.create_event(session, 999999, cards[0].id, copies, coins, stars)
    assert res.success
    await session.commit()
    return res.event, res.card


@pytest.mark.asyncio
async def test_event_card_is_reserved_and_sold_with_coins_without_reward(db_session):
    ev, card = await _event(db_session, copies=2, coins=500)
    assert card.is_event_card and card.limited_copies == 2

    # event kartasi endi nomzod emas va guruhga tashlanmaydi
    cands, _ = await EventService.list_candidates(db_session, page=1, page_size=50)
    assert card.id not in [c.id for c in cands]
    owner = await _user(db_session, 8601)
    g = await _group(db_session, -100861, owner)
    await db_session.execute(update(Card).where(Card.id != card.id).values(is_available=False))
    picked, _ = await DropService.pick_card_for_group(db_session, g)
    assert picked is None

    buyer = await _user(db_session, 8602, coins=1000)
    before = await _coins(db_session, 8602)
    res = await EventService.buy_with_coins(db_session, ev.id, buyer)
    assert res.success
    await db_session.commit()
    after = await _coins(db_session, 8602)
    assert before - after == 500  # faqat narx yechildi, reward qo'shilmadi

    again = await EventService.buy_with_coins(db_session, ev.id, buyer)
    assert not again.success and again.code == "owned"


@pytest.mark.asyncio
async def test_event_sells_exactly_the_given_number_of_copies(db_session):
    ev, card = await _event(db_session, copies=1, coins=100)
    u1 = await _user(db_session, 8701, coins=500)
    u2 = await _user(db_session, 8702, coins=500)
    assert (await EventService.buy_with_coins(db_session, ev.id, u1)).success
    await db_session.commit()
    res = await EventService.buy_with_coins(db_session, ev.id, u2)
    assert not res.success and res.code in ("sold_out", "ended")
    row = (await db_session.execute(select(LimitedEvent).where(LimitedEvent.id == ev.id))).scalar_one()
    assert row.status == "sold_out" and row.sold_count == 1


@pytest.mark.asyncio
async def test_event_not_enough_coins_rolls_back(db_session):
    ev, card = await _event(db_session, copies=3, coins=900)
    poor = await _user(db_session, 8801, coins=10)
    res = await EventService.buy_with_coins(db_session, ev.id, poor)
    assert not res.success and res.code == "funds"
    await db_session.rollback()
    row = (await db_session.execute(select(LimitedEvent).where(LimitedEvent.id == ev.id))).scalar_one()
    assert row.sold_count == 0


@pytest.mark.asyncio
async def test_event_stars_purchase_gives_card_and_is_idempotent(db_session):
    ev, card = await _event(db_session, copies=2, coins=None, stars=50)
    buyer = await _user(db_session, 8901)
    res, payload = await EventService.create_stars_order(db_session, ev.id, buyer)
    assert res.success and payload and verify_payload(payload, buyer.telegram_id, 50)
    await db_session.commit()

    ok, _ = await EventService.validate_pre_checkout(db_session, payload, buyer, "XTR", 50)
    assert ok
    done = await EventService.complete_stars_purchase(db_session, payload, buyer, "XTR", 50, "chg-1")
    assert done.success
    await db_session.commit()
    assert await UserService.owns_card(db_session, buyer.id, card.id)
    assert await _coins(db_session, 8901) == 100  # coin berilmadi (boshlang'ich 100)

    dup = await EventService.complete_stars_purchase(db_session, payload, buyer, "XTR", 50, "chg-1")
    assert dup.code == "duplicate"

    # soxta/noto'g'ri summa yoki imzo
    assert verify_payload(payload, buyer.telegram_id, 51) is None
    assert verify_payload(_sign("abc", 1, 5) + "x", 1, 5) is None


@pytest.mark.asyncio
async def test_event_stars_refund_when_sold_out_meanwhile(db_session):
    ev, card = await _event(db_session, copies=1, coins=100, stars=40)
    late = await _user(db_session, 9001)
    res, payload = await EventService.create_stars_order(db_session, ev.id, late)
    await db_session.commit()

    quick = await _user(db_session, 9002, coins=500)
    assert (await EventService.buy_with_coins(db_session, ev.id, quick)).success
    await db_session.commit()

    done = await EventService.complete_stars_purchase(db_session, payload, late, "XTR", 40, "chg-late")
    assert not done.success and done.code == "refund"  # Stars qaytariladi
    await db_session.rollback()
    assert not await UserService.owns_card(db_session, late.id, card.id)


@pytest.mark.asyncio
async def test_event_ends_after_expiry(db_session):
    ev, card = await _event(db_session, copies=5, coins=100)
    await db_session.execute(
        update(LimitedEvent).where(LimitedEvent.id == ev.id).values(ends_at=datetime.now(ZoneInfo("UTC")) - timedelta(minutes=1))
    )
    assert await EventService.close_expired(db_session) == 1
    buyer = await _user(db_session, 9101, coins=500)
    res = await EventService.buy_with_coins(db_session, ev.id, buyer)
    assert not res.success
