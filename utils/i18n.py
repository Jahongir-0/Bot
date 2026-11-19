from typing import Dict, Optional

SUPPORTED_LANGS = ("uz", "en", "ru")

LANG_LABELS: Dict[str, str] = {
    "uz": "🇺🇿 O‘zbekcha",
    "en": "🇬🇧 English",
    "ru": "🇷🇺 Русский",
}

_RU_GROUP_PREFIXES = ("ru", "uk", "be", "kk", "ky", "tg")


def normalize_lang(code: Optional[str]) -> str:
    raw = (code or "").strip().lower()
    if raw in SUPPORTED_LANGS:
        return raw
    return "uz"


def detect_lang(tg_lang_code: Optional[str]) -> str:
    """Foydalanuvchining Telegram til kodidan boshlang'ich bot tilini aniqlaydi."""
    raw = (tg_lang_code or "").strip().lower()
    if not raw:
        return "uz"
    short = raw.split("-")[0].split("_")[0]
    if short in _RU_GROUP_PREFIXES:
        return "ru"
    if short == "en":
        return "en"
    if short in ("uz", "uzb"):
        return "uz"
    return "uz"


def get_lang(user: Optional[object]) -> str:
    if user is None:
        return "uz"
    return normalize_lang(getattr(user, "language", "uz"))


TEXTS: Dict[str, Dict[str, str]] = {
    "uz": {
        # Navigation & general
        "btn_back": "⬅️ Orqaga",
        "btn_home": "🏠 Bosh menu",
        "btn_cancel": "❌ Bekor qilish",
        "btn_confirm": "✅ Tasdiqlash",
        "btn_admin_panel": "🛡️ Admin Panel",
        "btn_lang": "🌐 Til: O‘zbekcha",
        # Main menu buttons (clean grouped layout)
        "m_cards": "🎴 Kartalarim",
        "m_search": "🔍 Qidiruv",
        "m_daily": "🎁 Kunlik bonus",
        "m_quiz": "🧠 Quiz",
        "m_games": "🎮 O‘yinlar",
        "m_event": "🎯 Eventlar",
        "m_market": "🛒 Market",
        "m_auction": "🔨 Auksion",
        "m_trade": "🤝 Trade",
        "m_wishlist": "💖 Wishlist",
        "m_profile": "👤 Profil",
        "m_ranking": "🏆 Reyting",
        "m_vip": "💎 VIP",
        "m_stars": "⭐ Stars",
        "m_quests": "📋 Vazifalar",
        "m_ach": "🏅 Yutuqlar",
        "m_ref": "🤝 Referal",
        "m_notif": "🔔 Xabarlar",
        "m_notif_n": "🔔 Xabarlar ({n})",
        "m_support": "💬 Support",
        "m_help": "❓ Yordam",
        # Auth / middleware
        "wait_short": "⏳ Biroz kuting...",
        "maint_default": "🔧 Botda texnik ishlar olib borilmoqda. Tez orada qaytamiz.",
        "ban_reason_default": "Qoidalarni buzish",
        "ban_until": "\n⏳ Muddat: {dt} UTC",
        "ban_perm": "\n⛔ Muddat: Umrbod",
        "ban_msg": "🚫 <b>Siz botdan bloklangansiz!</b>\n📌 Sabab: {reason}{exp}",
        "ban_alert": "🚫 Siz bloklangansiz.",
        "contact_admin": "💬 Admin bilan bog‘lanish",
        "sub_prompt": "📢 <b>Botdan foydalanish uchun quyidagi kanallarga obuna bo‘ling:</b>",
        "check_sub": "✅ Obunani tekshirish",
        "sub_missing": "❌ Hali hamma kanallarga obuna bo‘lmadingiz!",
        "sub_verified": "✅ Obuna tasdiqlandi! Xush kelibsiz!",
        "sub_verified_home": "🏠 <b>BOSH MENU</b> — Obuna tasdiqlandi!",
        # Welcome & User Panel Home
        "salom": "Salom, <b>{name}</b>!",
        "home_header": "🏠 <b>BOSH MENU</b> — {name}",
        "home_summary": (
            "🪙 Balans: <b>{coins} coins</b> · 🎴 Kartalar: <b>{cards}</b>\n"
            "⚡ Daraja: <b>Lv.{level}</b> · {vip_badge}"
        ),
        "vip_active_short": "💎 {tier} VIP",
        "vip_free_short": "▫️ Oddiy status",
        "welcome_default": (
            "🎴 <b>Anime Card Collection</b> o‘yiniga xush kelibsiz!\n\n"
            "Guruhlarda tushgan kartalarni topib yig‘ing, noyob kolleksiya yarating, "
            "market va auksionda savdo qiling, do‘stlar bilan almashing!\n\n"
            "👇 Kerakli bo‘limni tanlang:"
        ),
        "help_text": (
            "❓ <b>YORDAM VA ASOSIY BUYRUQLAR</b>\n\n"
            "🏠 /menu, /start — Bosh menyu\n"
            "👤 /me, /profil — Shaxsiy profil va bezaklar\n"
            "🎴 /cards — Kartalar kolleksiyasi (filtrlar va tartiblash)\n"
            "🎁 /daily — Kunlik coin + tasodifiy karta\n"
            "🧠 /quiz — Viktorina savollari\n"
            "🎮 /game — Mini o‘yinlar (Dice Roll)\n"
            "🔨 /auction — Jonli auksionlar\n"
            "💖 /wishlist — Orzu qilingan kartalar\n"
            "🎯 /event — Limitli event kartalari\n"
            "🛒 /market, /sell, /buy — Global bozor\n"
            "🤝 /trade, /give — Karta almashish / sovg‘a qilish\n"
            "💎 /vip, ⭐ /stars — VIP va Stars do‘koni\n"
            "🏆 /top — Reytinglar · 🔔 /notif — Xabarlar\n"
            "💬 /support — Yordam · 🌐 /lang — Tilni o‘zgartirish\n\n"
            "📌 <b>Guruhda karta olish qoidasi:</b>\n"
            "Guruhda karta rasmi tashlanganda uning nomi yashirin bo‘ladi — "
            "xabarga <b>REPLY</b> qilib personaj ismini yozing va kartani qo‘lga kiriting!"
        ),
        # Language screen
        "lang_title": (
            "🌐 <b>BOT TILINI TANLASH</b>\n\n"
            "Joriy til: <b>{current}</b>\n"
            "O‘zingizga qulay tilni tanlang:"
        ),
        "lang_saved": "✅ Bot tili O‘zbek tiliga o‘zgartirildi!",
        # Cancel & generic
        "cancelled": "❌ Amal bekor qilindi.",
        "nothing_to_cancel": "Bekor qilinadigan amal yo‘q.",
        "not_found": "Topilmadi.",
        "card_not_found": "Karta topilmadi.",
        "user_not_found": "❌ Foydalanuvchi topilmadi.",
        "drop_claim_btn": "🎴 Kartani olish",
        # Profile
        "p_collection": "🎴 Kolleksiya",
        "p_custom": "🎨 Bezak & Yutuqlar",
        "p_market": "🛒 Market",
        "p_auction": "🔨 Auksion",
        "p_ranking": "🏆 Reyting",
        "p_search": "🔍 Qidiruv",
        "p_daily": "🎁 Daily",
        "p_vip": "💎 VIP",
        "p_ref": "🤝 Referal",
        "p_notif": "🔔 Xabarlar",
        "p_lang": "🌐 Til",
        # Collection
        "col_v2_all": "🗂 Barcha kartalar",
        "col_media": "📺 Media bo‘limlari",
        "col_rarity": "⭐ Rank bo‘yicha",
        "col_offers": "📩 Kelgan takliflar",
        "col_tab_all": "Hammasi",
        "col_tab_fav": "❤️ Sevimli",
        "col_empty": "📭 Sizda hali kartalar yo‘q. Guruhdagi dropni kuting yoki marketdan oling!",
        "col_empty_tab": "📭 Bu bo‘limda kartalar topilmadi.",
        "col_list_hdr": "🗂 <b>KARTALARIM ({tab})</b> — Jami: <b>{total}</b> ta\n",
        "col_media_hdr": "📺 <b>MEDIA BO‘LIMLARI</b> (Jami: {total} ta)\n",
        "col_rarity_prompt": "⭐ <b>Rank bo‘yicha filtrlash:</b>",
        "col_detail_copies": "📦 <b>Nusxalar soni:</b> {qty} ta",
        "col_detail_copy_id": "🆔 <b>Nusxa ID:</b> <code>{uc_id}</code>",
        "col_detail_locked": "🔒 Marketda/Transferda band",
        "col_detail_ready": "🟢 Sotish/Transferga tayyor",
        "col_detail_status": "📌 <b>Holati:</b> {st}",
        "btn_fav_add": "❤️ Sevimliga qo‘shish",
        "btn_fav_del": "💔 Sevimlidan olish",
        "btn_show_add": "🖼 Showcase qo‘yish",
        "btn_show_del": "🖼 Showcasedan olish",
        "btn_about": "📖 Ma’lumot",
        "btn_wishlist": "💖 Wishlist",
        "btn_list_market": "🛒 Marketga qo‘yish",
        "fav_added": "❤️ Sevimlilarga qo‘shildi!",
        "fav_removed": "💔 Sevimlilardan olindi.",
        "show_added": "🖼 Showcasega qo‘shildi!",
        "show_removed": "🖼 Showcasedan olindi.",
        # Daily
        "daily_ok": (
            "🎁 <b>KUNLIK BONUS</b>\n\n"
            "➕ <b>+{coins} 🪙</b> · ⚡ <b>+15 XP</b>\n"
            "🔥 <b>Ketma-ketlik:</b> {streak} kun{card_line}\n\n"
            "Ertaga yana kiring va ko‘proq bonus oling!"
        ),
        "daily_card_line": "\n🎴 <b>Tasodifiy karta:</b> {emoji} <b>{char}</b> ({anime})!",
        "daily_wait": "⏳ <b>KUNLIK BONUS</b>\n\n{msg}\n🔥 Joriy ketma-ketlik: <b>{streak} kun</b>",
        # Market & Search & Ranking & Stars & Quests & Event
        "mkt_title": "🛒 <b>GLOBAL KARTA MARKETI</b>",
        "mkt_my_title": "📦 <b>MENING SOTUVLARIM</b>",
        "mkt_empty": "📭 Hozircha faol e'lonlar yo‘q.",
        "mkt_all_btn": "🌐 Barcha e'lonlar",
        "mkt_my_btn": "📦 Mening sotuvlarim",
        "mkt_buy_btn": "🛒 Sotib olish",
        "mkt_cancel_btn": "❌ Sotuvdan olish",
        "mkt_seller": "👤 <b>Sotuvchi:</b> {name}",
        "mkt_price": "💰 <b>Narxi:</b> <b>{price} coins</b>",
        "mkt_bought": "🎉 <b>TABRIKLAYMIZ!</b>\n\n🎴 <b>{char}</b> kartasi kolleksiyangizga qo‘shildi!\n💰 To‘landi: <b>{price} 🪙</b>",
        "srch_title": "🔍 <b>GLOBAL KARTA QIDIRUVI</b>\n\nQidiruv turini tanlang:",
        "srch_btn_char": "🔍 Personaj",
        "srch_btn_anime": "📺 Media nomi",
        "srch_btn_rarity": "⭐ Rank",
        "srch_btn_id": "🎴 Karta ID",
        "srch_btn_rand": "🎲 Tasodifiy",
        "srch_again_rand": "🎲 Yana tasodifiy",
        "srch_make_offer": "💰 Taklif yuborish",
        "srch_add_wish": "💖 Wishlistga qo‘shish",
        "rnk_global": "🌍 Global",
        "rnk_group": "🏆 Guruh",
        "rnk_groups": "🏰 Guruhlar",
        "stars_disabled": "Telegram Stars do‘koni vaqtincha o‘chirilgan.",
        "stars_title": "⭐ <b>TELEGRAM STARS DO‘KONI</b>\n\nTelegram Stars orqali tez va xavfsiz coin sotib oling.\n👇 Paketni tanlang:",
        "stars_empty": "⭐ <b>TELEGRAM STARS DO‘KONI</b>\n\n📭 Hozircha coin paketlari mavjud emas.",
        "quests_title": "📋 <b>VAZIFALAR VA MUKOFOTLAR</b>\n",
        "quests_claimed": "✅ Olingan",
        "quests_ready": "🎁 Tayyor! (+{reward} 🪙)",
        "quests_prog": "⏳ {cur}/{tgt} · Mukofot: {reward} 🪙",
        "quests_claim_btn": "🎁 Mukofotlarni olish (+{coins} 🪙)",
        "evt_none": "🎯 <b>EVENT</b>\n\nHozircha faol event yo‘q. Yangi limitli event boshlanganda xabar beriladi!",
        "evt_footer": "☝️ Faol limitli eventlar. Olish uchun tugmani bosing!",
        "evt_buy_coins": "🪙 {price} coin bilan olish",
        "evt_buy_stars": "⭐ {price} Stars bilan olish",
        # V2 user screens
        "quiz_none": "🧠 <b>VIKTORINA (QUIZ)</b>\n\n📭 Hozircha faol quizlar yo‘q. Tez orada qo‘shiladi!",
        "quiz_list": "🧠 <b>FAOL QUIZLAR</b> ({n} ta)\n\nSavollarga javob bering va coin + XP yuting:",
        "quiz_hist_btn": "📜 Quiz tarixim",
        "game_title": "🎮 <b>MINI O‘YINLAR</b>\n\n🎲 <b>Dice Roll:</b> Kuniga {limit} marta tosh oting!\n🎉 6 tushsa JACKPOT: <b>+{jackpot} 🪙</b>\n⏳ Kutish: {cd} daqiqa",
        "game_dice_btn": "🎲 Dice Roll (Tosh otish)",
        "auc_none": "🔨 <b>JONLI AUKSIONLAR</b>\n\n📭 Hozircha faol auksionlar yo‘q.",
        "auc_list": "🔨 <b>FAOL AUKSIONLAR</b> ({n} ta)\n",
        "wish_hdr": "💖 <b>WISHLIST</b> ({n} ta karta)\n\nSevimli kartalaringiz bozor yoki auksionga chiqsa darhol xabar beramiz!\n<i>Qidiruvdan karta ochib Wishlistga qo‘shing.</i>",
        "wish_top_btn": "🔥 Eng ko‘p izlanayotganlar",
        "vip_title": "💎 <b>VIP OBUNA VA IMTIYOZLAR</b>\n\nHolat: {status}\n\n✨ <b>VIP imtiyozlari:</b>\n• 🎁 Daily bonus 1.5x – 3x ko‘proq\n• 🚫 Reklama ko‘rsatilmaydi\n• 🔓 Majburiy kanal bypass (Gold/Diamond)\n• 💎 Profilda maxsus VIP nishoni",
        "vip_active_status": "💎 Faol: <b>{tier}</b> ({dt} gacha)",
        "vip_free_status": "▫️ Hozircha oddiy status",
        "notif_hdr": "🔔 <b>XABARLAR MARKAZI</b> ({n} ta)\n",
        "notif_empty": "📭 Yangi xabarlar yo‘q.",
        "notif_read_btn": "✅ Barchasini o‘qilgan qilish",
        "sup_hdr": "💬 <b>YORDAM VA MUROJAATLAR</b>\n\nSavol, taklif yoki shikoyatingizni yuboring:",
        "sup_new_btn": "➕ Yangi murojaat / shikoyat",
        "trade_help": "🤝 <b>KARTA ALMASHUVI (/trade)</b>\n\nIkki tomonlama xavfsiz karta-karta almashish uchun:\n<code>/trade @username</code> buyrug‘ini yozing!",
        "ref_title": (
            "🤝 <b>REFERAL VA GURUH MUKOFOTI</b>\n\n"
            "👥 Taklif qilgan do‘stlaringiz: <b>{count}</b> ta\n\n"
            "🎁 <b>Do‘st taklif qilish mukofoti:</b>\n"
            "• Sizga: <b>+{inviter} 🪙</b>\n"
            "• Yangi do‘stingizga: <b>+{invitee} 🪙</b>\n\n"
            "🏰 <b>Botni guruhga qo‘shish mukofoti:</b>\n"
            "• <b>+{grp} 🪙</b> + 🟣 <b>1 ta eksklyuziv Epic karta</b>\n\n"
            "🔗 <b>Sizning referal havolangiz:</b>\n<code>{link}</code>"
        ),
        "ref_share_btn": "📤 Do‘stlarga ulashish",
        "ref_add_grp_btn": "➕ Botni guruhga qo‘shish",
    },
    "en": {
        "btn_back": "⬅️ Back",
        "btn_home": "🏠 Main Menu",
        "btn_cancel": "❌ Cancel",
        "btn_confirm": "✅ Confirm",
        "btn_admin_panel": "🛡️ Admin Panel",
        "btn_lang": "🌐 Lang: English",
        "m_cards": "🎴 My Cards",
        "m_search": "🔍 Search",
        "m_daily": "🎁 Daily Bonus",
        "m_quiz": "🧠 Quiz",
        "m_games": "🎮 Mini Games",
        "m_event": "🎯 Events",
        "m_market": "🛒 Market",
        "m_auction": "🔨 Auctions",
        "m_trade": "🤝 Trade",
        "m_wishlist": "💖 Wishlist",
        "m_profile": "👤 Profile",
        "m_ranking": "🏆 Rankings",
        "m_vip": "💎 VIP",
        "m_stars": "⭐ Stars",
        "m_quests": "📋 Quests",
        "m_ach": "🏅 Achievements",
        "m_ref": "🤝 Referral",
        "m_notif": "🔔 Inbox",
        "m_notif_n": "🔔 Inbox ({n})",
        "m_support": "💬 Support",
        "m_help": "❓ Help",
        "wait_short": "⏳ Please wait a moment...",
        "maint_default": "🔧 Bot is under maintenance. We'll be right back!",
        "ban_reason_default": "Violation of rules",
        "ban_until": "\n⏳ Until: {dt} UTC",
        "ban_perm": "\n⛔ Duration: Permanent",
        "ban_msg": "🚫 <b>You are banned from using this bot!</b>\n📌 Reason: {reason}{exp}",
        "ban_alert": "🚫 You are banned.",
        "contact_admin": "💬 Contact Admin",
        "sub_prompt": "📢 <b>Please subscribe to the following channels to use the bot:</b>",
        "check_sub": "✅ Verify Subscription",
        "sub_missing": "❌ You haven't joined all required channels yet!",
        "sub_verified": "✅ Subscription verified! Welcome!",
        "sub_verified_home": "🏠 <b>MAIN MENU</b> — Subscription verified!",
        "salom": "Hello, <b>{name}</b>!",
        "home_header": "🏠 <b>MAIN MENU</b> — {name}",
        "home_summary": (
            "🪙 Balance: <b>{coins} coins</b> · 🎴 Cards: <b>{cards}</b>\n"
            "⚡ Level: <b>Lv.{level}</b> · {vip_badge}"
        ),
        "vip_active_short": "💎 {tier} VIP",
        "vip_free_short": "▫️ Standard",
        "welcome_default": (
            "🎴 Welcome to <b>Anime Card Collection</b>!\n\n"
            "Catch character drops in groups, build a rare collection, "
            "trade on the market & live auctions, and climb to #1!\n\n"
            "👇 Choose a section below:"
        ),
        "help_text": (
            "❓ <b>HELP & COMMANDS</b>\n\n"
            "🏠 /menu, /start — Main menu\n"
            "👤 /me, /profil — Personal profile & customization\n"
            "🎴 /cards — Card collection (filters & sorting)\n"
            "🎁 /daily — Daily coins + random card\n"
            "🧠 /quiz — Anime quizzes\n"
            "🎮 /game — Mini games (Dice Roll)\n"
            "🔨 /auction — Live card auctions\n"
            "💖 /wishlist — Wanted cards list\n"
            "🎯 /event — Limited event cards\n"
            "🛒 /market, /sell, /buy — Global marketplace\n"
            "🤝 /trade, /give — Card trading & gifting\n"
            "💎 /vip, ⭐ /stars — VIP & Stars shop\n"
            "🏆 /top — Leaderboards · 🔔 /notif — Notifications\n"
            "💬 /support — Support · 🌐 /lang — Change language\n\n"
            "📌 <b>How to claim drops in groups:</b>\n"
            "When a hidden card drops in a group, <b>REPLY</b> to that message "
            "with the character's name to claim it!"
        ),
        "lang_title": (
            "🌐 <b>SELECT BOT LANGUAGE</b>\n\n"
            "Current language: <b>{current}</b>\n"
            "Choose your preferred language:"
        ),
        "lang_saved": "✅ Language changed to English!",
        "cancelled": "❌ Action cancelled.",
        "nothing_to_cancel": "Nothing to cancel.",
        "not_found": "Not found.",
        "card_not_found": "Card not found.",
        "user_not_found": "❌ User not found.",
        "drop_claim_btn": "🎴 Claim Card",
        "p_collection": "🎴 Collection",
        "p_custom": "🎨 Style & Badges",
        "p_market": "🛒 Market",
        "p_auction": "🔨 Auctions",
        "p_ranking": "🏆 Rankings",
        "p_search": "🔍 Search",
        "p_daily": "🎁 Daily",
        "p_vip": "💎 VIP",
        "p_ref": "🤝 Referral",
        "p_notif": "🔔 Inbox",
        "p_lang": "🌐 Language",
        "col_v2_all": "🗂 All Cards",
        "col_media": "📺 By Media",
        "col_rarity": "⭐ By Rank",
        "col_offers": "📩 Incoming Offers",
        "col_tab_all": "All",
        "col_tab_fav": "❤️ Fav",
        "col_empty": "📭 Your collection is empty. Catch drops in groups or buy from the market!",
        "col_empty_tab": "📭 No cards found in this category.",
        "col_list_hdr": "🗂 <b>MY CARDS ({tab})</b> — Total: <b>{total}</b>\n",
        "col_media_hdr": "📺 <b>MEDIA SECTIONS</b> (Total: {total})\n",
        "col_rarity_prompt": "⭐ <b>Filter by Rank:</b>",
        "col_detail_copies": "📦 <b>Copies owned:</b> {qty}",
        "col_detail_copy_id": "🆔 <b>Copy ID:</b> <code>{uc_id}</code>",
        "col_detail_locked": "🔒 Locked in Market/Trade",
        "col_detail_ready": "🟢 Ready to sell/trade",
        "col_detail_status": "📌 <b>Status:</b> {st}",
        "btn_fav_add": "❤️ Add to Favorites",
        "btn_fav_del": "💔 Remove Favorite",
        "btn_show_add": "🖼 Add to Showcase",
        "btn_show_del": "🖼 Remove Showcase",
        "btn_about": "📖 About",
        "btn_wishlist": "💖 Wishlist",
        "btn_list_market": "🛒 Sell on Market",
        "fav_added": "❤️ Added to favorites!",
        "fav_removed": "💔 Removed from favorites.",
        "show_added": "🖼 Added to profile showcase!",
        "show_removed": "🖼 Removed from showcase.",
        "daily_ok": (
            "🎁 <b>DAILY BONUS</b>\n\n"
            "➕ <b>+{coins} 🪙</b> · ⚡ <b>+15 XP</b>\n"
            "🔥 <b>Streak:</b> {streak} days{card_line}\n\n"
            "Come back tomorrow for even bigger rewards!"
        ),
        "daily_card_line": "\n🎴 <b>Bonus Card:</b> {emoji} <b>{char}</b> ({anime})!",
        "daily_wait": "⏳ <b>DAILY BONUS</b>\n\n{msg}\n🔥 Current streak: <b>{streak} days</b>",
        "mkt_title": "🛒 <b>GLOBAL CARD MARKET</b>",
        "mkt_my_title": "📦 <b>MY LISTINGS</b>",
        "mkt_empty": "📭 No active listings right now.",
        "mkt_all_btn": "🌐 All Listings",
        "mkt_my_btn": "📦 My Listings",
        "mkt_buy_btn": "🛒 Buy Card",
        "mkt_cancel_btn": "❌ Remove Listing",
        "mkt_seller": "👤 <b>Seller:</b> {name}",
        "mkt_price": "💰 <b>Price:</b> <b>{price} coins</b>",
        "mkt_bought": "🎉 <b>CONGRATULATIONS!</b>\n\n🎴 <b>{char}</b> has been added to your collection!\n💰 Paid: <b>{price} 🪙</b>",
        "srch_title": "🔍 <b>GLOBAL CARD SEARCH</b>\n\nSelect a search mode:",
        "srch_btn_char": "🔍 Character",
        "srch_btn_anime": "📺 Media Title",
        "srch_btn_rarity": "⭐ Rank",
        "srch_btn_id": "🎴 Card ID",
        "srch_btn_rand": "🎲 Random Card",
        "srch_again_rand": "🎲 Another Random",
        "srch_make_offer": "💰 Make Offer",
        "srch_add_wish": "💖 Add to Wishlist",
        "rnk_global": "🌍 Global",
        "rnk_group": "🏆 Group",
        "rnk_groups": "🏰 Groups",
        "stars_disabled": "Telegram Stars shop is temporarily disabled.",
        "stars_title": "⭐ <b>TELEGRAM STARS SHOP</b>\n\nBuy coins safely and instantly via Telegram Stars.\n👇 Select a package:",
        "stars_empty": "⭐ <b>TELEGRAM STARS SHOP</b>\n\n📭 No packages available right now.",
        "quests_title": "📋 <b>QUESTS & REWARDS</b>\n",
        "quests_claimed": "✅ Claimed",
        "quests_ready": "🎁 Ready! (+{reward} 🪙)",
        "quests_prog": "⏳ {cur}/{tgt} · Reward: {reward} 🪙",
        "quests_claim_btn": "🎁 Claim All Rewards (+{coins} 🪙)",
        "evt_none": "🎯 <b>EVENTS</b>\n\nNo active events right now. Stay tuned!",
        "evt_footer": "☝️ Active limited events. Tap a button above to claim!",
        "evt_buy_coins": "🪙 Buy for {price} coins",
        "evt_buy_stars": "⭐ Buy for {price} Stars",
        "quiz_none": "🧠 <b>QUIZZES</b>\n\n📭 No active quizzes right now. Check back soon!",
        "quiz_list": "🧠 <b>ACTIVE QUIZZES</b> ({n})\n\nAnswer questions to win coins + XP:",
        "quiz_hist_btn": "📜 Quiz History",
        "game_title": "🎮 <b>MINI GAMES</b>\n\n🎲 <b>Dice Roll:</b> Roll up to {limit} times a day!\n🎉 Roll a 6 for JACKPOT: <b>+{jackpot} 🪙</b>\n⏳ Cooldown: {cd} min",
        "game_dice_btn": "🎲 Roll Dice",
        "auc_none": "🔨 <b>LIVE AUCTIONS</b>\n\n📭 No active auctions right now.",
        "auc_list": "🔨 <b>ACTIVE AUCTIONS</b> ({n})\n",
        "wish_hdr": "💖 <b>WISHLIST</b> ({n} cards)\n\nWe'll notify you immediately when your wanted cards hit the market or auctions!\n<i>Open any card in Search to add it here.</i>",
        "wish_top_btn": "🔥 Most Wanted Cards",
        "vip_title": "💎 <b>VIP SUBSCRIPTION & PERKS</b>\n\nStatus: {status}\n\n✨ <b>VIP Benefits:</b>\n• 🎁 1.5x – 3x Daily bonus multiplier\n• 🚫 Ad-free experience\n• 🔓 Mandatory channel bypass (Gold/Diamond)\n• 💎 Exclusive VIP profile badge",
        "vip_active_status": "💎 Active: <b>{tier}</b> (until {dt})",
        "vip_free_status": "▫️ Standard user",
        "notif_hdr": "🔔 <b>NOTIFICATIONS</b> ({n})\n",
        "notif_empty": "📭 No notifications yet.",
        "notif_read_btn": "✅ Mark All Read",
        "sup_hdr": "💬 <b>SUPPORT & TICKETS</b>\n\nSend a question, suggestion, or report:",
        "sup_new_btn": "➕ New Support Ticket",
        "trade_help": "🤝 <b>CARD TRADING (/trade)</b>\n\nTo start a secure 2-way card swap, send:\n<code>/trade @username</code>",
        "ref_title": (
            "🤝 <b>REFERRAL & GROUP REWARDS</b>\n\n"
            "👥 Friends invited: <b>{count}</b>\n\n"
            "🎁 <b>Invite Rewards:</b>\n"
            "• For you: <b>+{inviter} 🪙</b>\n"
            "• For your friend: <b>+{invitee} 🪙</b>\n\n"
            "🏰 <b>Add Bot to Group Reward:</b>\n"
            "• <b>+{grp} 🪙</b> + 🟣 <b>1 Exclusive Epic Card</b>\n\n"
            "🔗 <b>Your referral link:</b>\n<code>{link}</code>"
        ),
        "ref_share_btn": "📤 Share with Friends",
        "ref_add_grp_btn": "➕ Add Bot to Group",
    },
    "ru": {
        "btn_back": "⬅️ Назад",
        "btn_home": "🏠 Главная",
        "btn_cancel": "❌ Отмена",
        "btn_confirm": "✅ Подтвердить",
        "btn_admin_panel": "🛡️ Админ Панель",
        "btn_lang": "🌐 Язык: Русский",
        "m_cards": "🎴 Мои карты",
        "m_search": "🔍 Поиск",
        "m_daily": "🎁 Бонус дня",
        "m_quiz": "🧠 Викторина",
        "m_games": "🎮 Мини-игры",
        "m_event": "🎯 Ивенты",
        "m_market": "🛒 Маркет",
        "m_auction": "🔨 Аукцион",
        "m_trade": "🤝 Обмен",
        "m_wishlist": "💖 Желаемое",
        "m_profile": "👤 Профиль",
        "m_ranking": "🏆 Рейтинг",
        "m_vip": "💎 VIP",
        "m_stars": "⭐ Stars",
        "m_quests": "📋 Задания",
        "m_ach": "🏅 Достижения",
        "m_ref": "🤝 Реферал",
        "m_notif": "🔔 Уведомления",
        "m_notif_n": "🔔 Уведомления ({n})",
        "m_support": "💬 Поддержка",
        "m_help": "❓ Помощь",
        "wait_short": "⏳ Подождите немного...",
        "maint_default": "🔧 В боте ведутся технические работы. Скоро вернёмся!",
        "ban_reason_default": "Нарушение правил",
        "ban_until": "\n⏳ До: {dt} UTC",
        "ban_perm": "\n⛔ Срок: Навсегда",
        "ban_msg": "🚫 <b>Вы заблокированы в боте!</b>\n📌 Причина: {reason}{exp}",
        "ban_alert": "🚫 Вы заблокированы.",
        "contact_admin": "💬 Связаться с админом",
        "sub_prompt": "📢 <b>Для использования бота подпишитесь на каналы:</b>",
        "check_sub": "✅ Проверить подписку",
        "sub_missing": "❌ Вы ещё не подписались на все каналы!",
        "sub_verified": "✅ Подписка подтверждена! Добро пожаловать!",
        "sub_verified_home": "🏠 <b>ГЛАВНОЕ МЕНЮ</b> — Подписка подтверждена!",
        "salom": "Привет, <b>{name}</b>!",
        "home_header": "🏠 <b>ГЛАВНОЕ МЕНЮ</b> — {name}",
        "home_summary": (
            "🪙 Баланс: <b>{coins} монет</b> · 🎴 Карт: <b>{cards}</b>\n"
            "⚡ Уровень: <b>Lv.{level}</b> · {vip_badge}"
        ),
        "vip_active_short": "💎 {tier} VIP",
        "vip_free_short": "▫️ Обычный статус",
        "welcome_default": (
            "🎴 Добро пожаловать в <b>Anime Card Collection</b>!\n\n"
            "Ловите карты в группах, собирайте редкие коллекции, "
            "торгуйте на маркете и аукционах, обменивайтесь с друзьями!\n\n"
            "👇 Выберите нужный раздел:"
        ),
        "help_text": (
            "❓ <b>ПОМОЩЬ И КОМАНДЫ</b>\n\n"
            "🏠 /menu, /start — Главное меню\n"
            "👤 /me, /profil — Личный профиль и оформление\n"
            "🎴 /cards — Моя коллекция (фильтры и сортировка)\n"
            "🎁 /daily — Ежедневные монеты + случайная карта\n"
            "🧠 /quiz — Аниме викторины\n"
            "🎮 /game — Мини-игры (Dice Roll)\n"
            "🔨 /auction — Живые аукционы\n"
            "💖 /wishlist — Список желаемых карт\n"
            "🎯 /event — Лимитированные ивент-карты\n"
            "🛒 /market, /sell, /buy — Глобальный рынок\n"
            "🤝 /trade, /give — Обмен и передача карт\n"
            "💎 /vip, ⭐ /stars — VIP и магазин Stars\n"
            "🏆 /top — Рейтинг · 🔔 /notif — Уведомления\n"
            "💬 /support — Поддержка · 🌐 /lang — Сменить язык\n\n"
            "📌 <b>Как забирать карты в группах:</b>\n"
            "Когда в группе падает скрытая карта, сделайте <b>REPLY</b> на сообщение "
            "и напишите имя персонажа!"
        ),
        "lang_title": (
            "🌐 <b>ВЫБОР ЯЗЫКА БОТА</b>\n\n"
            "Текущий язык: <b>{current}</b>\n"
            "Выберите удобный для вас язык:"
        ),
        "lang_saved": "✅ Язык бота изменён на Русский!",
        "cancelled": "❌ Действие отменено.",
        "nothing_to_cancel": "Нечего отменять.",
        "not_found": "Не найдено.",
        "card_not_found": "Карта не найдена.",
        "user_not_found": "❌ Пользователь не найден.",
        "drop_claim_btn": "🎴 Забрать карту",
        "p_collection": "🎴 Коллекция",
        "p_custom": "🎨 Стиль и награды",
        "p_market": "🛒 Маркет",
        "p_auction": "🔨 Аукцион",
        "p_ranking": "🏆 Рейтинг",
        "p_search": "🔍 Поиск",
        "p_daily": "🎁 Бонус",
        "p_vip": "💎 VIP",
        "p_ref": "🤝 Реферал",
        "p_notif": "🔔 Уведомления",
        "p_lang": "🌐 Язык",
        "col_v2_all": "🗂 Все карты",
        "col_media": "📺 По медиа",
        "col_rarity": "⭐ По рангу",
        "col_offers": "📩 Входящие заявки",
        "col_tab_all": "Все",
        "col_tab_fav": "❤️ Избр.",
        "col_empty": "📭 Ваша коллекция пуста. Ловите карты в группах или купите на маркете!",
        "col_empty_tab": "📭 В этом разделе карты не найдены.",
        "col_list_hdr": "🗂 <b>МОИ КАРТЫ ({tab})</b> — Всего: <b>{total}</b>\n",
        "col_media_hdr": "📺 <b>МЕДИА РАЗДЕЛЫ</b> (Всего: {total})\n",
        "col_rarity_prompt": "⭐ <b>Фильтр по рангу:</b>",
        "col_detail_copies": "📦 <b>Копий у вас:</b> {qty}",
        "col_detail_copy_id": "🆔 <b>ID копии:</b> <code>{uc_id}</code>",
        "col_detail_locked": "🔒 Занята на маркете/в обмене",
        "col_detail_ready": "🟢 Готова к продаже/обмену",
        "col_detail_status": "📌 <b>Статус:</b> {st}",
        "btn_fav_add": "❤️ В избранное",
        "btn_fav_del": "💔 Убрать из избр.",
        "btn_show_add": "🖼 На витрину",
        "btn_show_del": "🖼 Снять с витрины",
        "btn_about": "📖 О карте",
        "btn_wishlist": "💖 В желаемое",
        "btn_list_market": "🛒 Продать на маркете",
        "fav_added": "❤️ Добавлено в избранное!",
        "fav_removed": "💔 Удалено из избранного.",
        "show_added": "🖼 Добавлено на витрину профиля!",
        "show_removed": "🖼 Убрано с витрины.",
        "daily_ok": (
            "🎁 <b>ЕЖЕДНЕВНЫЙ БОНУС</b>\n\n"
            "➕ <b>+{coins} 🪙</b> · ⚡ <b>+15 XP</b>\n"
            "🔥 <b>Серия:</b> {streak} дн.{card_line}\n\n"
            "Заходите завтра за ещё большим бонусом!"
        ),
        "daily_card_line": "\n🎴 <b>Бонус-карта:</b> {emoji} <b>{char}</b> ({anime})!",
        "daily_wait": "⏳ <b>ЕЖЕДНЕВНЫЙ БОНУС</b>\n\n{msg}\n🔥 Текущая серия: <b>{streak} дн.</b>",
        "mkt_title": "🛒 <b>ГЛОБАЛЬНЫЙ МАРКЕТ</b>",
        "mkt_my_title": "📦 <b>МОИ ЛОТЫ</b>",
        "mkt_empty": "📭 Активных объявлений пока нет.",
        "mkt_all_btn": "🌐 Все лоты",
        "mkt_my_btn": "📦 Мои лоты",
        "mkt_buy_btn": "🛒 Купить карту",
        "mkt_cancel_btn": "❌ Снять с продажи",
        "mkt_seller": "👤 <b>Продавец:</b> {name}",
        "mkt_price": "💰 <b>Цена:</b> <b>{price} монет</b>",
        "mkt_bought": "🎉 <b>ПОЗДРАВЛЯЕМ!</b>\n\n🎴 Карта <b>{char}</b> добавлена в вашу коллекцию!\n💰 Оплачено: <b>{price} 🪙</b>",
        "srch_title": "🔍 <b>ГЛОБАЛЬНЫЙ ПОИСК КАРТ</b>\n\nВыберите тип поиска:",
        "srch_btn_char": "🔍 Персонаж",
        "srch_btn_anime": "📺 Название",
        "srch_btn_rarity": "⭐ Ранг",
        "srch_btn_id": "🎴 ID карты",
        "srch_btn_rand": "🎲 Случайная",
        "srch_again_rand": "🎲 Ещё случайную",
        "srch_make_offer": "💰 Предложить цену",
        "srch_add_wish": "💖 В желаемое",
        "rnk_global": "🌍 Глобальный",
        "rnk_group": "🏆 Группа",
        "rnk_groups": "🏰 Группы",
        "stars_disabled": "Магазин Telegram Stars временно отключён.",
        "stars_title": "⭐ <b>МАГАЗИН TELEGRAM STARS</b>\n\nПокупайте монеты быстро и безопасно через Telegram Stars.\n👇 Выберите пакет:",
        "stars_empty": "⭐ <b>МАГАЗИН TELEGRAM STARS</b>\n\n📭 Пакеты пока не добавлены.",
        "quests_title": "📋 <b>ЗАДАНИЯ И НАГРАДЫ</b>\n",
        "quests_claimed": "✅ Получено",
        "quests_ready": "🎁 Готово! (+{reward} 🪙)",
        "quests_prog": "⏳ {cur}/{tgt} · Награда: {reward} 🪙",
        "quests_claim_btn": "🎁 Забрать награды (+{coins} 🪙)",
        "evt_none": "🎯 <b>ИВЕНТЫ</b>\n\nАктивных ивентов сейчас нет. Следите за новостями!",
        "evt_footer": "☝️ Активные ивенты. Нажмите кнопку выше, чтобы получить карту!",
        "evt_buy_coins": "🪙 Купить за {price} монет",
        "evt_buy_stars": "⭐ Купить за {price} Stars",
        "quiz_none": "🧠 <b>ВИКТОРИНЫ</b>\n\n📭 Активных викторин пока нет.",
        "quiz_list": "🧠 <b>АКТИВНЫЕ ВИКТОРИНЫ</b> ({n})\n\nОтвечайте на вопросы и получайте монеты + XP:",
        "quiz_hist_btn": "📜 История викторин",
        "game_title": "🎮 <b>МИНИ-ИГРЫ</b>\n\n🎲 <b>Dice Roll:</b> Бросайте кубик до {limit} раз в день!\n🎉 Выпадет 6 — ДЖЕКПОТ: <b>+{jackpot} 🪙</b>\n⏳ Перерыв: {cd} мин",
        "game_dice_btn": "🎲 Бросить кубик",
        "auc_none": "🔨 <b>ЖИВЫЕ АУКЦИОНЫ</b>\n\n📭 Активных аукционов сейчас нет.",
        "auc_list": "🔨 <b>АКТИВНЫЕ АУКЦИОНЫ</b> ({n})\n",
        "wish_hdr": "💖 <b>ЖЕЛАЕМОЕ</b> ({n} карт)\n\nМы сразу сообщим, когда эти карты появятся на маркете или аукционе!\n<i>Откройте карту в поиске, чтобы добавить её сюда.</i>",
        "wish_top_btn": "🔥 Самые желанные",
        "vip_title": "💎 <b>VIP ПОДПИСКА И ПРИВИЛЕГИИ</b>\n\nСтатус: {status}\n\n✨ <b>Преимущества VIP:</b>\n• 🎁 Ежедневный бонус 1.5x – 3x больше\n• 🚫 Без рекламы\n• 🔓 Обход обязательных каналов (Gold/Diamond)\n• 💎 Особый значок в профиле",
        "vip_active_status": "💎 Активна: <b>{tier}</b> (до {dt})",
        "vip_free_status": "▫️ Обычный статус",
        "notif_hdr": "🔔 <b>УВЕДОМЛЕНИЯ</b> ({n})\n",
        "notif_empty": "📭 Новых уведомлений нет.",
        "notif_read_btn": "✅ Прочитать все",
        "sup_hdr": "💬 <b>ПОДДЕРЖКА И ОБРАЩЕНИЯ</b>\n\nЗадайте вопрос или отправьте жалобу:",
        "sup_new_btn": "➕ Новое обращение",
        "trade_help": "🤝 <b>ОБМЕН КАРТАМИ (/trade)</b>\n\nДля безопасного обмена карта на карту введите:\n<code>/trade @username</code>",
        "ref_title": (
            "🤝 <b>РЕФЕРАЛЫ И БОНУС ЗА ГРУППУ</b>\n\n"
            "👥 Приглашено друзей: <b>{count}</b>\n\n"
            "🎁 <b>Награда за друга:</b>\n"
            "• Вам: <b>+{inviter} 🪙</b>\n"
            "• Вашему другу: <b>+{invitee} 🪙</b>\n\n"
            "🏰 <b>Бонус за добавление бота в группу:</b>\n"
            "• <b>+{grp} 🪙</b> + 🟣 <b>1 эксклюзивная Epic карта</b>\n\n"
            "🔗 <b>Ваша реферальная ссылка:</b>\n<code>{link}</code>"
        ),
        "ref_share_btn": "📤 Поделиться с друзьями",
        "ref_add_grp_btn": "➕ Добавить бота в группу",
    },
}


def tr(lang: str, key: str, **kw) -> str:
    lcode = normalize_lang(lang)
    tbl = TEXTS.get(lcode) or TEXTS["uz"]
    tpl = tbl.get(key) or TEXTS["uz"].get(key) or key
    if kw:
        try:
            return tpl.format(**kw)
        except Exception:
            return tpl
    return tpl
