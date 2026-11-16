from datetime import timedelta

import pytest
from sqlalchemy import select

from models.base import utcnow
from models.card import Card
from models.market import Offer
from models.user_card import UserCard
from services.admin_service import AdminService
from services.card_service import CardService
from services.collection_service import CollectionService
from services.offer_service import OfferService
from services.payment_service import PaymentService
from services.ranking_service import RankingService
from services.settings_service import SettingsService
from services.transfer_service import TransferService
from services.user_service import UserService


async def _give(session, user_id, card_id, times=1):
    ids = []
    for _ in range(times):
        uc = UserCard(user_id=user_id, card_id=card_id, acquired_via="drop")
        session.add(uc)
        await session.flush()
        ids.append(uc.id)
    return ids


@pytest.mark.asyncio
async def test_same_anime_and_character_are_merged(db_session):
    # Seed'da "Gojo Satoru" / "Jujutsu Kaisen" bor: boshqacha yozilgan nom shu guruhga qo'shilishi kerak
    gojo2 = await CardService.create_card(
        db_session, "  gojo   satoru ", "jujutsu  kaisen", "https://x/y.png", "Epic", 900
    )
    assert gojo2.character_name == "Gojo Satoru"
    assert gojo2.anime_name == "Jujutsu Kaisen"
    assert gojo2.anime_id == "jk_01"

    gojo1 = (
        await db_session.execute(select(Card).where(Card.card_code == "CRD-0001"))
    ).scalar_one()
    sukuna = (
        await db_session.execute(select(Card).where(Card.character_name == "Ryomen Sukuna"))
    ).scalar_one()
    luffy = (
        await db_session.execute(select(Card).where(Card.character_name == "Monkey D. Luffy"))
    ).scalar_one()

    u = await UserService.upsert_user(db_session, 7001, "col", "Col")
    await _give(db_session, u.id, gojo1.id, 2)
    await _give(db_session, u.id, gojo2.id, 1)
    await _give(db_session, u.id, sukuna.id, 1)
    await _give(db_session, u.id, luffy.id, 1)

    sections, total = await CollectionService.get_anime_sections(db_session, u.id)
    by_name = {s.anime_name: s for s in sections}
    assert total == 2
    assert by_name["Jujutsu Kaisen"].copies == 4
    assert by_name["Jujutsu Kaisen"].characters == 2
    assert by_name["One Piece"].copies == 1

    jjk = by_name["Jujutsu Kaisen"]
    anime_name, chars, ch_total = await CollectionService.get_anime_characters(
        db_session, u.id, jjk.rep_card_id
    )
    assert ch_total == 2
    gojo_section = next(c for c in chars if c.character_name == "Gojo Satoru")
    assert gojo_section.copies == 3 and gojo_section.variants == 2

    name, anime, variants, anime_rep = await CollectionService.get_character_variants(
        db_session, u.id, gojo_section.rep_card_id
    )
    assert name == "Gojo Satoru" and anime == "Jujutsu Kaisen"
    assert sorted(v.quantity for v in variants) == [1, 2]
    assert anime_rep == jjk.rep_card_id


@pytest.mark.asyncio
async def test_admin_grouped_card_lists(db_session):
    await CardService.create_card(db_session, "Gojo Satoru", "Jujutsu Kaisen", "https://x/y.png", "Epic", 900)
    items, total = await AdminService.get_anime_sections(db_session)
    jjk = next(i for i in items if i["name"] == "Jujutsu Kaisen")
    assert jjk["cards"] >= 5 and jjk["characters"] == 4  # Gojo ikki variant bo'lsa ham 1 personaj

    info = await AdminService.get_anime_characters(db_session, jjk["rep_id"])
    assert info is not None
    _, chars, _ = info
    gojo = next(c for c in chars if c["name"] == "Gojo Satoru")
    assert gojo["cards"] == 2

    cards_info = await AdminService.get_character_cards(db_session, gojo["rep_id"])
    assert len(cards_info[2]) == 2


@pytest.mark.asyncio
async def test_stars_packages_admin_control_and_validation(db_session):
    pkg = await SettingsService.save_stars_package(db_session, None, 250, 30000)
    packages = await SettingsService.get_stars_packages(db_session)
    assert any(p["id"] == pkg["id"] and p["coins"] == 30000 for p in packages)

    updated = await SettingsService.save_stars_package(db_session, pkg["id"], 300, 40000)
    assert updated["stars"] == 300 and "40,000" in updated["label"]

    with pytest.raises(ValueError):
        await SettingsService.save_stars_package(db_session, None, 0, 100)
    with pytest.raises(ValueError):
        await SettingsService.save_stars_package(db_session, "yoq", 10, 10)

    assert await SettingsService.delete_stars_package(db_session, pkg["id"])
    assert not await SettingsService.delete_stars_package(db_session, pkg["id"])


@pytest.mark.asyncio
async def test_stars_payment_uses_admin_price_and_is_idempotent(db_session):
    user = await UserService.upsert_user(db_session, 7100, "payer", "Payer")
    pkg = await SettingsService.save_stars_package(db_session, None, 77, 7777)

    order, payload, _label = await PaymentService.create_star_order(db_session, user.id, 7100, pkg["id"])
    assert order.stars_amount == 77 and order.coins_amount == 7777

    done = await PaymentService.complete_star_payment(
        db_session, payload, 7100, "XTR", 77, "charge-1", "prov-1"
    )
    assert done is not None
    await db_session.refresh(user)
    assert user.coins == 100 + 7777

    again = await PaymentService.complete_star_payment(
        db_session, payload, 7100, "XTR", 77, "charge-1", "prov-1"
    )
    assert again is None
    assert await PaymentService.is_charge_processed(db_session, "charge-1")
    await db_session.refresh(user)
    assert user.coins == 100 + 7777

    # Noto'g'ri summa yoki valyuta qabul qilinmaydi
    order2, payload2, _ = await PaymentService.create_star_order(db_session, user.id, 7100, pkg["id"])
    assert await PaymentService.complete_star_payment(db_session, payload2, 7100, "XTR", 1, "charge-2", "p") is None
    assert await PaymentService.complete_star_payment(db_session, payload2, 7100, "USD", 77, "charge-3", "p") is None


@pytest.mark.asyncio
async def test_stale_transfer_unlocks_card(db_session):
    a = await UserService.upsert_user(db_session, 7200, "a", "A")
    b = await UserService.upsert_user(db_session, 7201, "b", "B")
    card = (await db_session.execute(select(Card).limit(1))).scalar_one()
    (uc_id,) = await _give(db_session, a.id, card.id)

    res = await TransferService.create_pending_transfer(db_session, a.id, b.id, uc_id, price=500)
    assert res.success
    uc = await db_session.get(UserCard, uc_id)
    assert uc.is_locked

    res.transfer.expires_at = utcnow() - timedelta(minutes=1)
    await db_session.flush()
    assert await TransferService.expire_stale_transfers(db_session) == 1
    await db_session.refresh(uc)
    assert not uc.is_locked


@pytest.mark.asyncio
async def test_admin_remove_card_refunds_pending_offer(db_session):
    owner = await UserService.upsert_user(db_session, 7300, "own", "Own")
    buyer = await UserService.upsert_user(db_session, 7301, "buy", "Buy")
    await UserService.credit_coins(db_session, buyer.id, 1000, "test", "seed")
    card = (await db_session.execute(select(Card).limit(1))).scalar_one()
    (uc_id,) = await _give(db_session, owner.id, card.id)
    await UserService.recompute_card_counts(db_session, owner.id)

    res = await OfferService.create_offer(db_session, buyer.id, uc_id, 600)
    assert res.success
    await db_session.refresh(buyer)
    assert buyer.coins == 100 + 1000 - 600  # escrow ushlab turibdi

    assert await AdminService.remove_card_from_user(db_session, 999999, owner.id, card.id)
    await db_session.refresh(buyer)
    assert buyer.coins == 100 + 1000  # coin qaytarildi
    off = (await db_session.execute(select(Offer).where(Offer.id == res.offer.id))).scalar_one()
    assert off.status == "cancelled"


@pytest.mark.asyncio
async def test_ranking_sql_matches_modes(db_session):
    a = await UserService.upsert_user(db_session, 7400, "ra", "RA")
    b = await UserService.upsert_user(db_session, 7401, "rb", "RB")
    cards = (await db_session.execute(select(Card).order_by(Card.id).limit(3))).scalars().all()
    for c in cards:
        await _give(db_session, a.id, c.id)
    await _give(db_session, b.id, cards[0].id)
    await UserService.recompute_card_counts(db_session, a.id)
    await UserService.recompute_card_counts(db_session, b.id)

    RankingService.invalidate_cache()
    for mode in ("weighted", "unique_first", "total_cards"):
        await SettingsService.set_ranking_mode(db_session, mode)
        RankingService.invalidate_cache()
        top = await RankingService.get_global_top(db_session, limit=5)
        assert top[0].user.id == a.id
        g_rank, _ = await RankingService.get_user_ranks(db_session, a.id)
        b_rank, _ = await RankingService.get_user_ranks(db_session, b.id)
        assert g_rank == 1 and b_rank == 2
