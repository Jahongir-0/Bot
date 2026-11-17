import logging
from typing import List, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.admin import Setting
from models.card import Card
from utils.constants import DEFAULT_SETTINGS, Rarity, get_rarity_meta

logger = logging.getLogger(__name__)

# Starter anime card catalog (admins can edit/replace images via Telegram photo upload or add more anytime)
STARTER_CARDS: List[Tuple[str, str, str, str, str, str, str, int, int, int]] = [
    (
        "CRD-0001",
        "Gojo Satoru",
        "Jujutsu Kaisen",
        "jk_01",
        "chr_gojo",
        "https://s4.anilist.co/file/anilistcdn/character/large/b127691-Um27232325f8.jpg",
        Rarity.LEGENDARY.value,
        10,
        2500,
        8,
    ),
    (
        "CRD-0002",
        "Ryomen Sukuna",
        "Jujutsu Kaisen",
        "jk_01",
        "chr_sukuna",
        "https://s4.anilist.co/file/anilistcdn/character/large/b133282-3M6V3vL6F1u6.png",
        Rarity.MYTHIC.value,
        1,
        8000,
        2,
    ),
    (
        "CRD-0003",
        "Megumi Fushiguro",
        "Jujutsu Kaisen",
        "jk_01",
        "chr_megumi",
        "https://s4.anilist.co/file/anilistcdn/character/large/b126635-bya3N2cW16W2.png",
        Rarity.EPIC.value,
        35,
        900,
        14,
    ),
    (
        "CRD-0004",
        "Yuji Itadori",
        "Jujutsu Kaisen",
        "jk_01",
        "chr_yuji",
        "https://s4.anilist.co/file/anilistcdn/character/large/b127212-F5x9vK9G59fR.png",
        Rarity.RARE.value,
        60,
        350,
        25,
    ),
    (
        "CRD-0005",
        "Monkey D. Luffy",
        "One Piece",
        "op_01",
        "chr_luffy",
        "https://s4.anilist.co/file/anilistcdn/character/large/b40-MN2610621414.jpg",
        Rarity.MYTHIC.value,
        2,
        8500,
        2,
    ),
    (
        "CRD-0006",
        "Roronoa Zoro",
        "One Piece",
        "op_01",
        "chr_zoro",
        "https://s4.anilist.co/file/anilistcdn/character/large/b62-W89262362361.jpg",
        Rarity.LEGENDARY.value,
        12,
        2600,
        7,
    ),
    (
        "CRD-0007",
        "Nami",
        "One Piece",
        "op_01",
        "chr_nami",
        "https://s4.anilist.co/file/anilistcdn/character/large/b723-3F3W6k6Z7x8Q.png",
        Rarity.RARE.value,
        65,
        380,
        24,
    ),
    (
        "CRD-0008",
        "Naruto Uzumaki",
        "Naruto",
        "nr_01",
        "chr_naruto",
        "https://s4.anilist.co/file/anilistcdn/character/large/b17-IazKGogQwFXP.png",
        Rarity.LEGENDARY.value,
        14,
        2400,
        8,
    ),
    (
        "CRD-0009",
        "Sasuke Uchiha",
        "Naruto",
        "nr_01",
        "chr_sasuke",
        "https://s4.anilist.co/file/anilistcdn/character/large/b13-3365826236.jpg",
        Rarity.EPIC.value,
        28,
        950,
        14,
    ),
    (
        "CRD-0010",
        "Kakashi Hatake",
        "Naruto",
        "nr_01",
        "chr_kakashi",
        "https://s4.anilist.co/file/anilistcdn/character/large/b85-mkVbh2vb66r9.png",
        Rarity.EPIC.value,
        30,
        920,
        15,
    ),
    (
        "CRD-0011",
        "Makima",
        "Chainsaw Man",
        "csm_01",
        "chr_makima",
        "https://s4.anilist.co/file/anilistcdn/character/large/b137080-7aW24d2n4R4B.png",
        Rarity.LEGENDARY.value,
        15,
        2550,
        7,
    ),
    (
        "CRD-0012",
        "Denji",
        "Chainsaw Man",
        "csm_01",
        "chr_denji",
        "https://s4.anilist.co/file/anilistcdn/character/large/b130001-3232512321.png",
        Rarity.RARE.value,
        58,
        400,
        22,
    ),
    (
        "CRD-0013",
        "Marin Kitagawa",
        "My Dress-Up Darling",
        "mdd_01",
        "chr_marin",
        "https://s4.anilist.co/file/anilistcdn/character/large/b135032-bB6sD7X5B12B.png",
        Rarity.EPIC.value,
        32,
        1000,
        14,
    ),
    (
        "CRD-0014",
        "Levi Ackerman",
        "Attack on Titan",
        "aot_01",
        "chr_levi",
        "https://s4.anilist.co/file/anilistcdn/character/large/b45627-CR68RyZmddGG.png",
        Rarity.LEGENDARY.value,
        11,
        2700,
        7,
    ),
    (
        "CRD-0015",
        "Mikasa Ackerman",
        "Attack on Titan",
        "aot_01",
        "chr_mikasa",
        "https://s4.anilist.co/file/anilistcdn/character/large/b40881-F3gr1P2m7p8J.png",
        Rarity.EPIC.value,
        29,
        940,
        15,
    ),
    (
        "CRD-0016",
        "Tanjiro Kamado",
        "Demon Slayer",
        "ds_01",
        "chr_tanjiro",
        "https://s4.anilist.co/file/anilistcdn/character/large/b126156-25612512.png",
        Rarity.COMMON.value,
        90,
        120,
        50,
    ),
    (
        "CRD-0017",
        "Nezuko Kamado",
        "Demon Slayer",
        "ds_01",
        "chr_nezuko",
        "https://s4.anilist.co/file/anilistcdn/character/large/b127518-25612512.png",
        Rarity.RARE.value,
        62,
        360,
        25,
    ),
    (
        "CRD-0018",
        "Anya Forger",
        "Spy x Family",
        "sxf_01",
        "chr_anya",
        "https://s4.anilist.co/file/anilistcdn/character/large/b138102-8872512.png",
        Rarity.COMMON.value,
        95,
        110,
        55,
    ),
    (
        "CRD-0019",
        "Chopper",
        "One Piece",
        "op_01",
        "chr_chopper",
        "https://s4.anilist.co/file/anilistcdn/character/large/b309-125125.png",
        Rarity.COMMON.value,
        98,
        100,
        55,
    ),
    (
        "CRD-0020",
        "Konohamaru Sarutobi",
        "Naruto",
        "nr_01",
        "chr_konohamaru",
        "https://s4.anilist.co/file/anilistcdn/character/large/b1699-2512512.png",
        Rarity.COMMON.value,
        110,
        90,
        55,
    ),
]


async def seed_initial_data(session: AsyncSession) -> None:
    """Seeds default global settings and starter anime cards if empty."""
    for key, val in DEFAULT_SETTINGS.items():
        existing = await session.get(Setting, key)
        if existing is None:
            session.add(Setting(key=key, value={"data": val}))

    existing_card = (await session.execute(select(Card.id).limit(1))).scalar_one_or_none()
    if existing_card is None:
        for (
            card_code,
            character_name,
            anime_name,
            anime_id,
            character_id,
            image,
            rarity,
            rank,
            base_value,
            drop_weight,
        ) in STARTER_CARDS:
            session.add(
                Card(
                    card_code=card_code,
                    character_name=character_name,
                    anime_name=anime_name,
                    anime_id=anime_id,
                    character_id=character_id,
                    image=image,
                    rarity=rarity,
                    rank=rank,
                    base_value=base_value,
                    drop_weight=drop_weight,
                    drop_limit=get_rarity_meta(rarity).default_drop_limit,
                    dropped_count=0,
                    is_available=True,
                    is_deleted=False,
                )
            )
        logger.info("Seeded %d starter anime cards.", len(STARTER_CARDS))

    # V2: Season 1 va default Achievementlar (mavjud ma'lumotlarni o'chirmasdan)
    from datetime import timedelta
    from models.base import utcnow
    from models.v2_features import Achievement, Season

    existing_season = (await session.execute(select(Season.id).limit(1))).scalar_one_or_none()
    if existing_season is None:
        now = utcnow()
        session.add(
            Season(
                season_number=1,
                name="Season 1: Awakening",
                description="Anime Card Collection V2 ilk rasmiy mavsumi!",
                starts_at=now,
                ends_at=now + timedelta(days=30),
                status="active",
                rewards_config={
                    "top1_coins": 10000,
                    "top2_coins": 5000,
                    "top3_coins": 2500,
                    "top10_coins": 1000,
                    "top1_title": "👑 Mavsum Chempioni",
                },
            )
        )

    default_achievements = [
        ("collector_10", "📦 Yosh Kolleksioner", "10 ta karta yig'ing", "📦", "total_cards", 10, 300, 50, "📦", None),
        ("collector_100", "🏛️ Collector", "100 ta karta yig'ing", "🏛️", "total_cards", 100, 2000, 200, "🏛️", "Collector"),
        ("collector_500", "⚔️ Master Collector", "500 ta karta yig'ing", "⚔️", "total_cards", 500, 7500, 500, "⚔️", "Master Collector"),
        ("collector_1000", "👑 Grand Collector", "1000 ta karta yig'ing", "👑", "total_cards", 1000, 20000, 1000, "👑", "Grand Collector"),
        ("quiz_10", "🧠 Bilimdon", "10 ta quiz savoliga to'g'ri javob bering", "🧠", "quiz_wins", 10, 1000, 150, "🧠", "Quiz Master"),
        ("game_10", "🎲 Omadli", "10 marta mini o'yin yuting", "🎲", "game_wins", 10, 800, 100, "🎲", None),
        ("trade_5", "🤝 Savdogar", "5 ta muvaffaqiyatli savdo/almashuv qiling", "🤝", "trade_count", 5, 1200, 150, "🤝", "Merchant"),
        ("auction_1", "🔨 Auksion g'olibi", "1 ta auksionda g'olib bo'ling", "🔨", "auction_wins", 1, 1500, 200, "🔨", None),
        ("ref_5", "📣 Elchi", "5 ta do'stni referal orqali taklif qiling", "📣", "referral_count", 5, 2500, 250, "📣", "Ambassador"),
    ]
    for code, title, desc, icon, metric, target_val, r_coins, r_xp, badge, r_title in default_achievements:
        exists = (await session.execute(select(Achievement.id).where(Achievement.code == code))).scalar_one_or_none()
        if exists is None:
            session.add(
                Achievement(
                    code=code,
                    title=title,
                    description=desc,
                    icon=icon,
                    metric=metric,
                    target_value=target_val,
                    reward_coins=r_coins,
                    reward_xp=r_xp,
                    reward_badge=badge,
                    reward_title=r_title,
                )
            )

    await session.commit()

