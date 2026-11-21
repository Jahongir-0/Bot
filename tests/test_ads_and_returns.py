from datetime import timedelta

import pytest
from sqlalchemy import select

from models.base import utcnow
from models.card import Card
from models.user_card import UserCard
from models.v2_features import CardHistory
from services.admin_service import AdminService
from services.card_service import CardService
from services.community_service import CommunityService
from services.market_service import MarketService
from services.user_service import UserService
from utils.callback_data import AdmV2Cb
from utils.formatting import format_drop_message


@pytest.mark.asyncio
async def test_ad_text_validation_and_link_extraction():
    ok_empty, _, _ = CommunityService.validate_ad_text("   \n  ")
    assert not ok_empty

    ok_long, _, _ = CommunityService.validate_ad_text("A" * (CommunityService.AD_MAX_LEN + 1))
    assert not ok_long

    ok_lines, _, _ = CommunityService.validate_ad_text("a\nb\nc\nd")
    assert not ok_lines

    ok_good, _, cleaned = CommunityService.validate_ad_text("  Yangi anime kanali: @anime_uz_hub  ")
    assert ok_good and cleaned == "Yangi anime kanali: @anime_uz_hub"
    tok, url = CommunityService.extract_ad_link(cleaned)
    assert tok == "@anime_uz_hub" and url == "https://t.me/anime_uz_hub"

    tok2, url2 = CommunityService.extract_ad_link("Sayt: https://example.com/promo.")
    assert tok2 == "https://example.com/promo" and url2 == "https://example.com/promo"


@pytest.mark.asyncio
async def test_ad_slot_exclusivity_edit_expiry_and_tracking(db_session):
    # 1. 'all' barcha joyni band qiladi
    ok, _, ad_all = await CommunityService.create_ad(
        db_session,
        text="Obuna bo'ling: @my_channel",
        admin_tg_id=999999,
        placement="all",
        duration_hours=12,
    )
    assert ok and ad_all is not None
    assert ad_all.expires_at is not None

    ok_g, _, _ = await CommunityService.create_ad(
        db_session, text="Ikkinchi guruh reklama", admin_tg_id=999999, placement="group", duration_hours=24
    )
    assert not ok_g

    ok_p, _, _ = await CommunityService.create_ad(
        db_session, text="Ikkinchi private reklama", admin_tg_id=999999, placement="private", duration_hours=24
    )
    assert not ok_p

    # 2. Joyni 'group' ga o'zgartirsak, 'private' bo'shaydi
    ok_u, _, _ = await CommunityService.update_ad(db_session, ad_all.id, placement="group")
    assert ok_u

    ok_p2, _, ad_pm = await CommunityService.create_ad(
        db_session,
        text="VIP aksiya https://t.me/vip_chan",
        admin_tg_id=999999,
        placement="private",
        duration_hours=0,  # cheksiz
    )
    assert ok_p2 and ad_pm is not None
    assert ad_pm.expires_at is None

    # Endi ikkala joy ham band
    ok_a2, _, _ = await CommunityService.create_ad(
        db_session, text="Yana biri", admin_tg_id=999999, placement="all", duration_hours=12
    )
    assert not ok_a2

    # 3. Faolsizlantirish joyni bo'shatadi
    ok_tog, _, _ = await CommunityService.toggle_ad(db_session, ad_pm.id)
    assert ok_tog and not ad_pm.is_active

    # 4. Matnni tahrirlash va guruh dropida chiqish + klik sanash
    ok_ed, _, _ = await CommunityService.update_ad(
        db_session, ad_all.id, text="Kanalimiz: @anime_new_link"
    )
    assert ok_ed and ad_all.button_url == "https://t.me/anime_new_link"

    rendered_grp = await CommunityService.get_rendered_ad(
        db_session, bot_username="test_anime_bot", user=None, placement="group"
    )
    assert rendered_grp is not None
    assert f"https://t.me/test_anime_bot?start=ad_{ad_all.id}" in rendered_grp
    assert ad_all.views_count == 1

    card = (await db_session.execute(select(Card).limit(1))).scalar_one()
    drop_msg = format_drop_message(card, ad_footer=rendered_grp)
    assert drop_msg.endswith(rendered_grp)
    assert len(drop_msg) <= 1020

    clicked = await CommunityService.record_ad_click(db_session, ad_all.id)
    assert clicked is not None and clicked.clicks_count == 1

    # 5. VIP no_ads private reklamani yashiradi, lekin oddiy userga ko'rinadi
    await CommunityService.toggle_ad(db_session, ad_pm.id)
    u = await UserService.upsert_user(db_session, 88101, "noads", "User")
    pm_footer = await CommunityService.get_rendered_ad(
        db_session, bot_username="test_anime_bot", user=u, placement="private"
    )
    assert pm_footer is not None and ad_pm.views_count == 1

    # 6. Muddati tugagan reklama avtomatik o'chadi va vaqt uzaytirilmaguncha yoqilmaydi
    ad_all.expires_at = utcnow() - timedelta(minutes=1)
    await db_session.flush()
    assert await CommunityService.expire_due_ads(db_session) == 1
    await db_session.refresh(ad_all)
    assert not ad_all.is_active

    ok_re, _, _ = await CommunityService.toggle_ad(db_session, ad_all.id)
    assert not ok_re

    ok_ext, _, _ = await CommunityService.update_ad(db_session, ad_all.id, duration_hours=24)
    assert ok_ext and ad_all.is_active


@pytest.mark.asyncio
async def test_returned_cards_go_back_to_undropped_list(db_session):
    u1 = await UserService.upsert_user(db_session, 88201, "ret1", "Ret1")
    u2 = await UserService.upsert_user(db_session, 88202, "ret2", "Ret2")

    c1 = await CardService.create_card(
        db_session, "Return Hero 1", "Return Anime", "https://x/r1.png", "Epic", 5000, drop_limit=1
    )
    c2 = await CardService.create_card(
        db_session, "Return Hero 2", "Return Anime", "https://x/r2.png", "Elita", 2500, drop_limit=1
    )

    # Ikkalasi guruhga tushib userlarga berilgan holat
    c1.dropped_count = 1
    c1.card_state = "OWNED"
    c2.dropped_count = 1
    c2.card_state = "OWNED"
    uc1 = UserCard(user_id=u1.id, card_id=c1.id, acquired_via="drop", card_state="OWNED")
    uc2 = UserCard(user_id=u2.id, card_id=c2.id, acquired_via="drop", card_state="OWNED")
    db_session.add_all([uc1, uc2])
    await db_session.flush()

    not_sent_before, _ = await AdminService.get_v2_cards_by_tab(db_session, tab="not_sent")
    ids_before = {c.id for c in not_sent_before}
    assert c1.id not in ids_before and c2.id not in ids_before

    # 1) User botga sotadi
    sell_res = await MarketService.sell_to_bot(db_session, u1.id, uc1.id)
    assert sell_res.success
    await db_session.refresh(c1)
    assert c1.dropped_count == 0 and c1.card_state == "AVAILABLE"

    # 2) Admin userdan kartani olib qo'yadi
    removed = await AdminService.remove_card_from_user(db_session, 999999, u2.id, c2.id)
    assert removed is True
    await db_session.refresh(c2)
    assert c2.dropped_count == 0 and c2.card_state == "AVAILABLE"

    hist = (
        await db_session.execute(
            select(CardHistory).where(CardHistory.card_id == c2.id, CardHistory.event_type == "admin_revoked")
        )
    ).scalar_one_or_none()
    assert hist is not None

    # Ikkala karta ham "Tushmaganlar" ro'yxatiga va drop tanlashga qaytgan bo'lishi kerak
    not_sent_after, _ = await AdminService.get_v2_cards_by_tab(db_session, tab="not_sent")
    ids_after = {c.id for c in not_sent_after}
    assert c1.id in ids_after and c2.id in ids_after

    undropped, _ = await AdminService.get_undropped_cards_paginated(db_session)
    undropped_ids = {c.id for c in undropped}
    assert c1.id in undropped_ids and c2.id in undropped_ids


def test_reklama_callbacks_within_64_bytes():
    for act, ext in (
        ("add", ""),
        ("place", "private"),
        ("dur", "168"),
        ("det", ""),
        ("tog", ""),
        ("edtxt", ""),
        ("edplc", ""),
        ("setplc", "private"),
        ("eddur", ""),
        ("setdur", "168"),
        ("del", ""),
    ):
        packed = AdmV2Cb(owner_id=1234567890, sec="reklama", act=act, tid=99999, ext=ext, p=1).pack()
        assert len(packed.encode("utf-8")) <= 64, f"Too long: {packed}"
