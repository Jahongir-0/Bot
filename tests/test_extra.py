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
    await _give(db_session, u.id, gojo1.id, 1)
    await _give(db_session, u.id, gojo2.id, 1)
    await _give(db_session, u.id, sukuna.id, 1)
    await _give(db_session, u.id, luffy.id, 1)

    sections, total = await CollectionService.get_anime_sections(db_session, u.id)
    by_name = {s.anime_name: s for s in sections}
    assert total == 2
    assert by_name["Jujutsu Kaisen"].copies == 3
    assert by_name["Jujutsu Kaisen"].characters == 2
    assert by_name["One Piece"].copies == 1

    jjk = by_name["Jujutsu Kaisen"]
    anime_name, chars, ch_total = await CollectionService.get_anime_characters(
        db_session, u.id, jjk.rep_card_id
    )
    assert ch_total == 2
    gojo_section = next(c for c in chars if c.character_name == "Gojo Satoru")
    assert gojo_section.copies == 2 and gojo_section.variants == 2

    name, anime, variants, anime_rep = await CollectionService.get_character_variants(
        db_session, u.id, gojo_section.rep_card_id
    )
    assert name == "Gojo Satoru" and anime == "Jujutsu Kaisen"
    assert sorted(v.quantity for v in variants) == [1, 1]
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


# ---------------------------------------------------------------- V2 Features Tests
@pytest.mark.asyncio
async def test_v2_rbac_permissions_and_revocation(db_session):
    from services.community_service import CommunityService

    await CommunityService.upsert_admin_role(
        db_session, 555001, "Moderator", ["cards", "support"], added_by_tg_id=999999, is_super=False
    )
    is_adm, perms = await CommunityService.get_admin_permissions(db_session, 555001)
    assert is_adm is True
    assert perms == {"cards", "support"}
    assert await CommunityService.has_section_access(db_session, 555001, "cards") is True
    assert await CommunityService.has_section_access(db_session, 555001, "security") is False

    assert await CommunityService.remove_admin_role(db_session, 555001) is True
    is_adm2, perms2 = await CommunityService.get_admin_permissions(db_session, 555001)
    assert is_adm2 is False and perms2 == set()


@pytest.mark.asyncio
async def test_v2_zip_import_zipslip_protection_and_duplicates(db_session):
    import io
    import json
    import zipfile
    from services.security_backup_service import SecurityBackupService

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../etc/passwd", "bad")
        zf.writestr("cards/hero.jpg", b"\xff\xd8\xff\xe0TESTJPG")
        manifest = [
            {"character_name": "V2 Test Hero", "anime_name": "V2 Anime", "rarity": "Epic", "image": "hero.jpg"},
            {"character_name": "V2 Test Hero", "anime_name": "V2 Anime", "rarity": "Epic", "image": "hero.jpg"},
        ]
        zf.writestr("cards.json", json.dumps(manifest))

    rep = await SecurityBackupService.import_cards_from_zip(db_session, buf.getvalue(), admin_tg_id=999999)
    assert rep.added == 1
    assert rep.duplicates == 1
    assert rep.failed >= 1  # ZipSlip entry rejected


@pytest.mark.asyncio
async def test_v2_dynamic_ranks_must_sum_to_100(db_session):
    from services.gameplay_service import GameplayService

    with pytest.raises(ValueError):
        await GameplayService.save_rank_definitions(
            db_session,
            [{"name": "Basic", "emoji": "⚪", "chance": 50.0, "value": 1000, "drops": 3}],
        )

    await GameplayService.save_rank_definitions(
        db_session,
        [
            {"name": "Basic", "emoji": "⚪", "chance": 60.0, "value": 1000, "drops": 3},
            {"name": "Elita", "emoji": "🔵", "chance": 40.0, "value": 2500, "drops": 2},
        ],
    )
    ranks = await GameplayService.get_rank_definitions(db_session)
    assert len(ranks) == 2 and sum(r["chance"] for r in ranks) == 100.0


@pytest.mark.asyncio
async def test_v2_auction_bid_outbid_refund_and_finalize(db_session):
    from models.v2_features import Auction
    from services.auction_wishlist_service import AuctionWishlistService

    b1 = await UserService.upsert_user(db_session, 9501, "bid1", "Bid1")
    b2 = await UserService.upsert_user(db_session, 9502, "bid2", "Bid2")
    await UserService.credit_coins(db_session, b1.id, 5000, "test", "seed")
    await UserService.credit_coins(db_session, b2.id, 5000, "test", "seed")

    card = await CardService.create_card(db_session, "Auc Hero", "Auc Anime", "https://x/auc.png", "Epic", 5000)
    ok, _, auc = await AuctionWishlistService.create_auction(
        db_session, card.id, start_price=1000, hours=2, created_by_tg_id=999999, min_step=100
    )
    assert ok and auc is not None

    ok1, _ = await AuctionWishlistService.place_bid(db_session, auc.id, b1, 1000)
    assert ok1
    await db_session.refresh(b1)
    assert b1.coins == 4100 and b1.locked_coins == 1000

    ok2, _ = await AuctionWishlistService.place_bid(db_session, auc.id, b2, 1500)
    assert ok2
    await db_session.refresh(b1)
    await db_session.refresh(b2)
    assert b1.coins == 5100 and b1.locked_coins == 0  # outbid refund
    assert b2.coins == 3600 and b2.locked_coins == 1500

    row = await db_session.get(Auction, auc.id)
    row.ends_at = utcnow() - timedelta(minutes=1)
    await db_session.flush()
    assert await AuctionWishlistService.finalize_expired_auctions(db_session) == 1
    await db_session.refresh(b2)
    assert b2.locked_coins == 0
    assert await UserService.owns_card(db_session, b2.id, card.id)


@pytest.mark.asyncio
async def test_v2_wishlist_and_two_sided_card_trade(db_session):
    from services.auction_wishlist_service import AuctionWishlistService
    from services.community_service import CommunityService

    u1 = await UserService.upsert_user(db_session, 9601, "tr1", "Tr1")
    u2 = await UserService.upsert_user(db_session, 9602, "tr2", "Tr2")
    cards = (await db_session.execute(select(Card).order_by(Card.id).limit(2))).scalars().all()
    c1, c2 = cards[0], cards[1]

    added, _ = await AuctionWishlistService.toggle_wishlist(db_session, u1.id, c2.id)
    assert added is True
    await AuctionWishlistService.notify_wishlist_watchers(db_session, c2.id, "drop", "Dropped!")
    assert await CommunityService.unread_count(db_session, u1.id) >= 1

    (uc1,) = await _give(db_session, u1.id, c1.id)
    (uc2,) = await _give(db_session, u2.id, c2.id)
    ok_t, _, tr = await AuctionWishlistService.create_trade(db_session, u1.id, u2.id, uc1)
    assert ok_t and tr is not None

    ok_c, _ = await AuctionWishlistService.confirm_trade(db_session, tr.id, u2.id, uc2)
    assert ok_c
    assert await UserService.owns_card(db_session, u1.id, c2.id)
    assert await UserService.owns_card(db_session, u2.id, c1.id)


@pytest.mark.asyncio
async def test_v2_reply_drop_name_matching_and_temp_ban_expiry(db_session):
    from services.drop_service import DropService
    from services.security_backup_service import SecurityBackupService

    card = await CardService.create_card(
        db_session, "Killua Zoldyck", "Hunter x Hunter", "https://x/kil.png", "Epic", 5000
    )
    card.aliases = ["Killua", "Godspeed"]
    await db_session.flush()

    assert DropService.matches_character_guess(card, "killua") is True
    assert DropService.matches_character_guess(card, "Killua Zoldyck") is True
    assert DropService.matches_character_guess(card, "godspeed") is True
    assert DropService.matches_character_guess(card, "naruto") is False

    bad = await UserService.upsert_user(db_session, 9701, "tmpban", "Tmp")
    await SecurityBackupService.ban_user(db_session, 999999, bad, "Spam", hours=1)
    assert bad.is_banned is True
    bad.ban_until = utcnow() - timedelta(minutes=1)
    await db_session.flush()
    assert await SecurityBackupService.expire_temp_bans(db_session) >= 1
    await db_session.refresh(bad)
    assert bad.is_banned is False

    bak = await SecurityBackupService.create_full_backup_zip(db_session, "manual", 999999)
    assert bak.file_size_bytes > 0


@pytest.mark.asyncio
async def test_v2_admin_media_titles_ranks_and_auction_cancel(db_session):
    from models.v2_features import Auction
    from services.auction_wishlist_service import AuctionWishlistService
    from services.gameplay_service import GameplayService

    await CardService.create_card(
        db_session, "Chainsaw Hero", "Chainsaw Man", "https://x/csm.png", "Epic", 4000, media_type="Manga"
    )
    rows, total = await AdminService.get_v2_media_titles(db_session, media_type="Manga")
    assert total >= 1
    rep_id, title, cnt = rows[0]
    assert isinstance(rep_id, int) and title == "Chainsaw Man" and cnt >= 1

    # Single rank field edit + 1-click normalize
    await GameplayService.save_rank_definitions(
        db_session,
        [
            {"name": "Basic", "emoji": "⚪", "chance": 60.0, "value": 1000, "drops": 3},
            {"name": "Elita", "emoji": "🔵", "chance": 40.0, "value": 2500, "drops": 2},
        ],
    )
    await GameplayService.update_single_rank_attr(db_session, 0, "chance", "70")
    await GameplayService.normalize_rank_probabilities(db_session)
    ranks = await GameplayService.get_rank_definitions(db_session)
    assert sum(int(r.get("drop_probability", 0)) for r in ranks) == 100

    # Auction cancel refunds highest bidder
    bidder = await UserService.upsert_user(db_session, 9801, "aucb", "AucB")
    await UserService.credit_coins(db_session, bidder.id, 3000, "test", "seed")
    c = await CardService.create_card(db_session, "Cancel Auc", "Auc World", "https://x/ca.png", "Elita", 2000)
    ok, _, auc = await AuctionWishlistService.create_auction(db_session, c.id, 500, 3, 999999, min_step=50)
    assert ok and auc is not None
    ok_b, _ = await AuctionWishlistService.place_bid(db_session, auc.id, bidder, 800)
    assert ok_b
    await db_session.refresh(bidder)
    assert bidder.locked_coins == 800

    ok_can, _ = await AuctionWishlistService.admin_cancel_auction(db_session, auc.id)
    assert ok_can is True
    await db_session.refresh(bidder)
    row = await db_session.get(Auction, auc.id)
    assert row.status == "cancelled" and bidder.locked_coins == 0

