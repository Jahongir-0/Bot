"""Mini o'yinlar katalogi: foydalanuvchi menyusi va admin sozlamalari uchun yagona manba."""
from typing import Any, Dict, List, Tuple

# Yangi o'yinlar uchun standart sozlamalar (admin paneldan o'zgartiriladi)
EXTRA_GAME_DEFAULTS: Dict[str, Any] = {
    "dart_enabled": True,
    "dart_daily_limit": 5,
    "dart_xp": 15,
    "dart_jackpot_pct": 400,   # 400 = 4x (nishon markazi)
    "dart_win_pct": 150,       # 150 = 1.5x (nishonga yaqin)
    "bowling_enabled": True,
    "bowling_daily_limit": 5,
    "bowling_xp": 15,
    "bowling_jackpot_pct": 400,  # strike
    "bowling_win_pct": 150,
    "basket_enabled": True,
    "basket_daily_limit": 5,
    "basket_xp": 15,
    "basket_jackpot_pct": 300,  # toza gol
    "basket_win_pct": 150,
    "chest_enabled": True,
    "chest_daily_limit": 5,
    "chest_cost": 100,
    "chest_xp": 15,
    "chest_jackpot_pct": 1000,  # 1000 = 10x
}

# Animatsion (Telegram dice) o'yinlar: kalit -> (emoji, nom, jackpot qiymati, yutuq qiymati, jackpot nomi, yutuq nomi)
ANIM_GAMES: Dict[str, Tuple[str, str, int, int, str, str]] = {
    "dart": ("🎯", "Dart", 6, 5, "🎯 BULLSEYE — nishon markazi!", "👏 Nishonga juda yaqin!"),
    "bowling": ("🎳", "Boulling", 6, 5, "🎳 STRIKE — hamma kegli yiqildi!", "👍 Ko‘p kegli yiqildi!"),
    "basket": ("🏀", "Basketbol", 5, 4, "🏀 TOZA GOL — to‘p halqaga tegmadi!", "✅ To‘p savatga tushdi!"),
}

# Admin paneli uchun: (kalit, emoji, nom, yoqish kaliti, [(maydon, qisqa nom, 0 ruxsatmi, min, max)])
Field = Tuple[str, str, bool, int, int]
ADMIN_GAMES: List[Dict[str, Any]] = [
    {"key": "slots", "emoji": "🎰", "name": "Slot 777", "toggle": "slots_enabled",
     "hint": "777 = 10x, 3 ta bir xil = 4x, juft = 1.5x. Garov: 50/100/250/500.",
     "fields": [("slots_daily_limit", "Kunlik limit", False, 1, 1000), ("slots_xp", "XP mukofoti", True, 0, 10000)]},
    {"key": "mines", "emoji": "💣", "name": "Xazina ovi", "toggle": "mines_enabled",
     "hint": "6 sandiqdan oltin toping, bombadan qoching (4x gacha).",
     "fields": [("mines_daily_limit", "Kunlik limit", False, 1, 1000), ("mines_xp", "XP mukofoti", True, 0, 10000)]},
    {"key": "wheel", "emoji": "🎡", "name": "Omad g‘ildiragi", "toggle": "wheel_enabled",
     "hint": "Chipta evaziga g‘ildirakni aylantiring.",
     "fields": [("wheel_daily_limit", "Kunlik limit", False, 1, 1000), ("wheel_ticket_cost", "Chipta narxi (0 = bepul)", True, 0, 1000000), ("wheel_xp", "XP mukofoti", True, 0, 10000)]},
    {"key": "dice", "emoji": "🎲", "name": "Tosh o‘yini", "toggle": "dice_enabled",
     "hint": "1–6 sonni toping.",
     "fields": [("dice_daily_limit", "Kunlik limit", False, 1, 1000), ("dice_jackpot_reward", "Yutuq (coin)", False, 1, 1000000), ("dice_cooldown_minutes", "Kutish vaqti (daq., 0 = yo‘q)", True, 0, 1440), ("dice_xp", "XP mukofoti", True, 0, 10000)]},
    {"key": "flip", "emoji": "🪙", "name": "Tanga tashlash", "toggle": "coinflip_enabled",
     "hint": "Gerb yoki raqam — to‘g‘ri topsangiz 2x.",
     "fields": [("coinflip_daily_limit", "Kunlik limit", False, 1, 1000), ("coinflip_xp", "XP mukofoti", True, 0, 10000)]},
    {"key": "lucky", "emoji": "🃏", "name": "Omadli karta", "toggle": "lucky_enabled",
     "hint": "Bepul omad: tasodifiy coin yutuq.",
     "fields": [("lucky_daily_limit", "Kunlik limit", False, 1, 1000), ("lucky_min_reward", "Minimal yutuq (coin)", False, 1, 1000000), ("lucky_max_reward", "Maksimal yutuq (coin)", False, 1, 1000000), ("lucky_xp", "XP mukofoti", True, 0, 10000)]},
    {"key": "dart", "emoji": "🎯", "name": "Dart", "toggle": "dart_enabled",
     "hint": "Nishonga otiladi: markaz (6) — jackpot, 5 — yutuq. Foizlarda: 400 = 4x.",
     "fields": [("dart_daily_limit", "Kunlik limit", False, 1, 1000), ("dart_jackpot_pct", "Markaz yutug‘i (%, 400 = 4x)", True, 0, 100000), ("dart_win_pct", "Yaqin yutug‘i (%, 150 = 1.5x)", True, 0, 100000), ("dart_xp", "XP mukofoti", True, 0, 10000)]},
    {"key": "bowling", "emoji": "🎳", "name": "Boulling", "toggle": "bowling_enabled",
     "hint": "Strike (6) — jackpot, 5 kegli — yutuq. Foizlarda: 400 = 4x.",
     "fields": [("bowling_daily_limit", "Kunlik limit", False, 1, 1000), ("bowling_jackpot_pct", "Strike yutug‘i (%, 400 = 4x)", True, 0, 100000), ("bowling_win_pct", "Yaxshi zarba yutug‘i (%)", True, 0, 100000), ("bowling_xp", "XP mukofoti", True, 0, 10000)]},
    {"key": "basket", "emoji": "🏀", "name": "Basketbol", "toggle": "basket_enabled",
     "hint": "Toza gol (5) — jackpot, savatga tushsa (4) — yutuq.",
     "fields": [("basket_daily_limit", "Kunlik limit", False, 1, 1000), ("basket_jackpot_pct", "Toza gol yutug‘i (%, 300 = 3x)", True, 0, 100000), ("basket_win_pct", "Gol yutug‘i (%, 150 = 1.5x)", True, 0, 100000), ("basket_xp", "XP mukofoti", True, 0, 10000)]},
    {"key": "chest", "emoji": "📦", "name": "Sirli sandiq", "toggle": "chest_enabled",
     "hint": "3 ta sandiqdan birini tanlang: bo‘sh, hamyon yoki jackpot.",
     "fields": [("chest_daily_limit", "Kunlik limit", False, 1, 1000), ("chest_cost", "Sandiq narxi (coin)", False, 1, 1000000), ("chest_jackpot_pct", "Jackpot (%, 1000 = 10x)", True, 0, 1000000), ("chest_xp", "XP mukofoti", True, 0, 10000)]},
    {"key": "pack", "emoji": "🎁", "name": "Karta paketlari", "toggle": "pack_enabled",
     "hint": "Coin evaziga tasodifiy karta paketi.",
     "fields": [("pack_daily_limit", "Kunlik limit (0 = o‘chirish)", True, 0, 100)]},
]
FIELD_META: Dict[str, Dict[str, Any]] = {}
for _g in ADMIN_GAMES:
    for _f in _g["fields"]:
        FIELD_META[_f[0]] = {"label": f"{_g['emoji']} {_g['name']}: {_f[1]}", "zero": _f[2], "min": _f[3], "max": _f[4], "game": _g["key"]}
TOGGLE_KEYS = tuple(g["toggle"] for g in ADMIN_GAMES)
