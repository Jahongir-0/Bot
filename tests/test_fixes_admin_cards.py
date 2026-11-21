from datetime import timedelta

import pytest
from sqlalchemy import select

from config.settings import get_settings
from models.admin import Ban
from models.base import utcnow
from models.card import Card
from models.user import User
from models.user_card import UserCard
from models.v2_features import AdminRole
from services.admin_service import AdminService
from services.auction_wishlist_service import AuctionWishlistService
from services.card_level_service import CardLevelService
from services.card_service import CardService
from services.collection_service import CollectionService
from services.event_service import EventService
from services.gameplay_service import GameplayService
from services.ranking_service import RankingService
from services.search_service import SearchService
from services.security_backup_service import SecurityBackupService
from services.transfer_service import TransferService
from services.user_service import UserService
from utils.callback_data import AdmV2Cb, CollectionCb, GiveCb
from utils.constants import RankingMode
from utils.formatting import format_profile_message


@pytest.mark.asyncio
async def test_stale_transfer_lock_reconcile_and_manual_unlock(db_session):
    sender = await UserService.upsert_user(db_session, 70001, "sender70", "Sender")
    receiver = await UserService.upsert_user(db_session, 70002, "recv70", "Receiver")
    card = await CardService.create_card(
        db_session, "Gojo Lock", "JJK", "https://x/gojo.png", "Epic", 4000, drop_limit=5
    )
    uc = UserCard(user_id=sender.id, card_id=card.id, acquired_via="drop", card_state="OWNED")
    db_session.add(uc)
    await db_session.flush()

    # 1. Transfer yaratib, muddatini o'tkazamiz
    res = await TransferService.create_pending_transfer(
        db_session,
        sender_id=sender.id,
        receiver_id=receiver.id,
        user_card_id=uc.id,
        mode="gift",
    )
    assert res.success and res.transfer is not None
    await db_session.refresh(uc)
    assert uc.is_locked is True

    kind, tr_id = await CollectionService.get_card_lock_info(db_session, uc.id)
    assert kind == "transfer" and tr_id == res.transfer.id

    res.transfer.expires_at = utcnow() - timedelta(minutes=2)
    await db_session.flush()

    unlocked = await CollectionService.reconcile_user_card_locks(db_session, sender.id)
    assert unlocked >= 1
    await db_session.refresh(uc)
    assert uc.is_locked is False
    assert uc.card_state == "OWNED"

    # 2. Yetim (orphan) lockni ham avtomatik ochish
    uc.is_locked = True
    uc.card_state = "LOCKED"
    await db_session.flush()
    unlocked2 = await CollectionService.reconcile_user_card_locks(db_session, sender.id)
    assert unlocked2 >= 1
    await db_session.refresh(uc)
    assert uc.is_locked is False

    # 3. Faol transferni foydalanuvchi o'zi bekor qilishi
    res2 = await TransferService.create_pending_transfer(
        db_session,
        sender_id=sender.id,
        receiver_id=receiver.id,
        user_card_id=uc.id,
        mode="gift",
    )
    assert res2.success
    ok_unlock, _ = await CollectionService.unlock_user_card_action(db_session, sender.id, uc.id)
    assert ok_unlock
    await db_session.refresh(uc)
    assert uc.is_locked is False and uc.card_state == "OWNED"


@pytest.mark.asyncio
async def test_admin_unban_protection_and_exclusion_from_rankings_and_search(db_session):
    settings = get_settings()
    admin_tg = next(iter(settings.admin_ids)) if settings.admin_ids else 999999

    adm_user = await UserService.upsert_user(db_session, admin_tg, "boss_adm", "Boss")
    role_adm = await UserService.upsert_user(db_session, 70010, "rbac_adm", "RbacAdm")
    normal_u = await UserService.upsert_user(db_session, 70011, "normal_u", "Normal")

    db_session.add(
        AdminRole(
            telegram_id=role_adm.telegram_id,
            username="rbac_adm",
            role_name="Moderator",
            permissions=["users", "cards"],
            is_super_admin=False,
            is_active=True,
        )
    )
    await db_session.flush()

    # Admin tasodifan ban bo'lib qolgan holat -> ensure_admins_unbanned ochib beradi
    adm_user.is_banned = True
    adm_user.ban_reason = "accident"
    adm_user.is_frozen = True
    db_session.add(Ban(user_id=adm_user.id, banned_by_telegram_id=admin_tg, reason="accident", is_active=True))
    await db_session.flush()

    healed = await AdminService.ensure_admins_unbanned(db_session)
    assert healed >= 1
    await db_session.refresh(adm_user)
    assert adm_user.is_banned is False and adm_user.is_frozen is False

    # Adminni ban qilishga urinish rad etiladi
    with pytest.raises(PermissionError):
        await AdminService.set_global_ban(db_session, admin_tg, adm_user.id, ban=True)
    with pytest.raises(ValueError):
        await SecurityBackupService.ban_user(db_session, admin_tg, role_adm, "test")

    # Admin va oddiy userga karta/coin beramiz
    card = await CardService.create_card(
        db_session, "Ranking Hero", "Ranking Anime", "https://x/rh.png", "Mythic", 50000, drop_limit=5
    )
    await AdminService.give_card_to_user(db_session, admin_tg, adm_user.id, card.id, level=5)
    await AdminService.give_card_to_user(db_session, admin_tg, normal_u.id, card.id, level=1)
    adm_user.coins = 9_999_999
    normal_u.coins = 100
    await UserService.recompute_card_counts(db_session, adm_user.id)
    await UserService.recompute_card_counts(db_session, normal_u.id)
    await db_session.flush()

    # Rankinglardan adminlar chiqarilgan
    top_cards = await RankingService.get_top_users(db_session, mode=RankingMode.TOTAL_CARDS, limit=10)
    top_ids = {u.id for u, _ in top_cards}
    assert adm_user.id not in top_ids
    assert role_adm.id not in top_ids
    assert normal_u.id in top_ids

    top_coins = await RankingService.get_top_users(db_session, mode=RankingMode.COINS, limit=10)
    assert adm_user.id not in {u.id for u, _ in top_coins}
    assert normal_u.id in {u.id for u, _ in top_coins}

    adm_rank, _ = await RankingService.get_user_ranks(db_session, adm_user)
    assert adm_rank == 0
    prof_txt = format_profile_message(adm_user, adm_rank)
    assert "Admin (reytingdan tashqari)" in prof_txt

    power_top = await CardLevelService.top_power(db_session, limit=10)
    assert adm_user.id not in {u.id for u, _, _ in power_top}
    assert normal_u.id in {u.id for u, _, _ in power_top}

    # SearchService admin kartalarini yashiradi
    s_global, _ = await SearchService.search_global_cards(db_session, "Ranking Hero", page=1, page_size=20)
    assert all(u.id != adm_user.id for _, u, _ in s_global)
    assert any(u.id == normal_u.id for _, u, _ in s_global)

    insp = await SearchService.inspect_user_card(db_session, card.id, adm_user.id)
    assert insp is None

    # Lekin oddiy foydalanuvchi adminga sovg'a yubora oladi
    gift_card = await CardService.create_card(
        db_session, "GiftToAdmin", "GiftAnime", "https://x/ga.png", "Basic", 500, drop_limit=5
    )
    uc_norm = UserCard(user_id=normal_u.id, card_id=gift_card.id, acquired_via="drop", card_state="OWNED")
    db_session.add(uc_norm)
    await db_session.flush()
    tr = await TransferService.create_pending_transfer(
        db_session, sender_id=normal_u.id, receiver_id=adm_user.id, user_card_id=uc_norm.id, mode="gift"
    )
    assert tr.success and tr.transfer is not None
    acc = await TransferService.accept_pending_transfer(db_session, tr.transfer.id, adm_user.id)
    assert acc.success
    assert await UserService.owns_card(db_session, adm_user.id, gift_card.id)


@pytest.mark.asyncio
async def test_undropped_card_preset_level_for_event_auction_and_grant(db_session):
    settings = get_settings()
    admin_tg = next(iter(settings.admin_ids)) if settings.admin_ids else 999999
    adm_user = await UserService.upsert_user(db_session, admin_tg, "boss_adm2", "Boss2")
    buyer = await UserService.upsert_user(db_session, 70021, "buyer70", "Buyer")
    buyer.coins = 500_000
    await db_session.flush()

    # 1. Eventga qo'yishda preset_level saqlanishi
    c_evt = await CardService.create_card(
        db_session, "Preset Event Hero", "PresetAnime", "https://x/pe.png", "Legend", 5000, drop_limit=3
    )
    c_evt.preset_level = 4
    await db_session.flush()

    ev_res = await EventService.create_event(
        db_session, admin_telegram_id=admin_tg, card_id=c_evt.id, total_copies=2, price_coins=1000, price_stars=None
    )
    assert ev_res.success and ev_res.event is not None and ev_res.event.card_level == 4
    buy_res = await EventService.buy_with_coins(db_session, ev_res.event.id, buyer.id)
    assert buy_res.success
    uc_evt = (
        await db_session.execute(select(UserCard).where(UserCard.user_id == buyer.id, UserCard.card_id == c_evt.id))
    ).scalar_one()
    assert uc_evt.level == 4

    # 2. Auksionga qo'yishda preset_level saqlanishi
    c_auc = await CardService.create_card(
        db_session, "Preset Auc Hero", "PresetAnime", "https://x/pa.png", "Legend", 5000, drop_limit=3
    )
    c_auc.preset_level = 3
    await db_session.flush()
    ok_auc, _, auc = await AuctionWishlistService.create_auction(
        db_session, card_id=c_auc.id, start_price=1000, hours=1, created_by_tg_id=admin_tg
    )
    assert ok_auc and auc is not None and auc.card_level == 3
    ok_bid, _ = await AuctionWishlistService.place_bid(db_session, auc.id, buyer.id, 1500)
    assert ok_bid
    ok_fin, _ = await AuctionWishlistService.admin_finalize_auction(db_session, auc.id)
    assert ok_fin
    uc_auc = (
        await db_session.execute(select(UserCard).where(UserCard.user_id == buyer.id, UserCard.card_id == c_auc.id))
    ).scalar_one()
    assert uc_auc.level == 3

    # 3. Userga berish (o'ziga ham) + user ro'yxatida o'zi birinchi chiqishi
    c_gift = await CardService.create_card(
        db_session, "Preset Gift Hero", "PresetAnime", "https://x/pg.png", "Epic", 3000, drop_limit=3
    )
    c_gift.preset_level = 5
    await db_session.flush()

    users_list, total = await AdminService.get_users_for_card_grant(db_session, admin_tg, page=1, page_size=12)
    assert total >= 2 and users_list[0].telegram_id == admin_tg

    granted = await AdminService.give_card_to_user(db_session, admin_tg, adm_user.id, c_gift.id)
    assert granted is not None
    uc_self = (
        await db_session.execute(select(UserCard).where(UserCard.user_id == adm_user.id, UserCard.card_id == c_gift.id))
    ).scalar_one()
    assert uc_self.level == 5


def test_new_callbacks_within_64_bytes():
    samples = [
        AdmV2Cb(owner_id=1234567890, sec="cards", act="lvl_inc", tid=99999, ext="sent_group", p=10).pack(),
        AdmV2Cb(owner_id=1234567890, sec="cards", act="lvl_dec", tid=99999, ext="not_sent", p=10).pack(),
        AdmV2Cb(owner_id=1234567890, sec="cards", act="evt_go", tid=99999, ext="", p=1).pack(),
        AdmV2Cb(owner_id=1234567890, sec="cards", act="give_u", tid=99999, ext="sent_group", p=10).pack(),
        AdmV2Cb(owner_id=1234567890, sec="cards", act="give_us", tid=99999, ext="sent_group", p=1).pack(),
        AdmV2Cb(owner_id=1234567890, sec="cards", act="give_go", tid=99999, ext="999999", p=10).pack(),
        CollectionCb(owner_id=1234567890, action="unlock", filter_val="d", page=10, item_id=99999).pack(),
        GiveCb(owner_id=1234567890, action="unlock", user_card_id=99999).pack(),
    ]
    for packed in samples:
        assert len(packed.encode("utf-8")) <= 64, f"Callback exceeds 64 bytes: {packed}"
