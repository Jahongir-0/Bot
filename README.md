# Anime Card Collection — Telegram Bot

Production-ready, asynchronous Telegram bot built with **Python 3.11+**, **aiogram 3**, **SQLAlchemy 2 (async)**, **PostgreSQL** (with **SQLite** local fallback), **Redis**, and **Alembic**.

## Features
- **Global Accounts & Collections**: User coins, cards, unique counts, XP, level, and streaks persist globally across all Telegram groups.
- **Race-Condition-Free Card Drops**: Distributed locking + atomic conditional SQL updates guarantee only one user claims each drop.
- **Daily Drop Schedule & Priority Cards**: Configurable per-group daily drop counts, auto-generated schedules, and admin priority overrides.
- **Global Market, Transfers & Escrowed Offers**: Atomic market purchases, `/give @username card_id [price]` with inline confirmation, and coin-escrowed offers on unlisted cards.
- **Global Cross-Group Search**: Search across all users' cards by character, anime, rarity, card ID, or random with pagination.
- **Telegram Stars (`XTR`) Coin Shop**: HMAC-signed payloads, `PreCheckoutQuery` verification, and idempotent payment crediting.
- **Full Admin Panel (`/admin`)**: Card CRUD + safe soft-delete migration, user management, coin +/-, global ban, group settings, events, and broadcast.

## Quick Start (Local)
1. Install Python **3.11+** (the production Docker image uses Python 3.11).
2. Create a virtualenv and install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and set `BOT_TOKEN`, `ADMIN_IDS`, and a random 32+ character `STARS_PAYMENT_SECRET` if Stars payments are enabled.
4. Run migrations & start bot:
   ```bash
   alembic upgrade head
   python -m app.main
   ```
5. Run tests:
   ```bash
   pytest -v
   ```

## Yangiliklar va tuzatishlar

### Admin paneldan Stars tariflarini boshqarish
`/admin` → **⭐ STARS TARIFLAR**: har bir paket uchun *nechta Stars = nechta coin* ni o'zgartirish (✏️), o'chirish (🗑) va yangi tarif qo'shish (➕).
Qiymat `Stars Coin` ko'rinishida yoziladi, masalan `250 30000`. O'zgarish foydalanuvchilarga `/stars` da darhol ko'rinadi.
Eng ko'pi bilan 8 ta paket; allaqachon yaratilgan buyurtmalar eski narxda qoladi.

### Kartalarni anime → personaj bo'yicha guruhlash
- **Kartalarim** (`/cards`): 📺 ANIME BO'LIMLARI → personajlar → shu personajning kartalari (rarity variantlari).
- **Admin → Card bo'limi → Kartalar ro'yxati**: xuddi shunday, har bir personaj uchun nusxa va egalar soni ko'rinadi.
- Admin bir xil anime/personaj nomini (katta-kichik harf yoki ortiqcha bo'shliqdan qat'i nazar) qayta kiritsa, yangi karta mavjud bo'lim va personaj ostiga avtomatik qo'shiladi.

### Tuzatilgan xatolar (qisqacha)
PostgreSQL'da `max(boolean)` xatosi, filtr tugmalaridagi `:` belgisi sabab tugmalarning ishlamasligi, reyting hisobining barcha userlarni xotiraga yuklashi,
bot o'chib turgandan keyin eski dropllarning guruhga yog'ilishi, muddati o'tgan transferda karta qulflanib qolishi,
admin kartani olib tashlaganda offer escrow coinlarining yo'qolishi, FSM holatida `/buyruq`lar ushlanib qolishi (endi `/cancel` bor),
Stars to'lovi tasdiqlanmasa avtomatik qaytarish, SQLite'da vaqt solishtirish xatosi, Alembic va `create_all` to'qnashuvi.

## Drop tizimi (a'zolar soniga qarab)

- Kunlik karta soni = **guruh a'zolari ÷ 10** (kamida 2, eng ko'pi 200): 20 va kam → 2, 30 → 3, 40 → 4, 100 → 10, 140 → 14, 1000 → 100.
- Kun boshlanganda (mahalliy vaqt, `TIMEZONE`) bot har bir guruhning a'zolar sonini Telegram'dan olib, yangi **24 soatlik jadval** tuzadi.
  Vaqtlar kun bo'yi teng bo'laklarga bo'linib, har bir bo'lak ichida tasodifiy tanlanadi: har kuni boshqacha, lekin yarim soatda hammasi tashlanib ketmaydi.
- Guruhga kun boshida faqat "bugun N ta karta" e'loni boriladi; **to'liq vaqtlar jadvali adminlarga** (shaxsiy xabarda) yuboriladi.
- Admin → Guruhlar bo'limida guruhni **🤖 Avto / ✋ Qo'lda** rejimga o'tkazish mumkin (qo'lda +/- bossangiz avto o'chadi).
- Qoidalarni `settings` jadvalidagi `drop_rules` orqali o'zgartirish mumkin (`members_per_card`, `min_per_day`, `max_per_day`, `window_start_hour`, `window_end_hour`,
  `same_group_cooldown_days`, `any_group_cooldown_hours`, `announce_in_group`).

## Nusxa qoidalari
- Har bir kartaning **tashlash soni** (`drop_limit`) bor: shuncha marta guruhlarga tashlanadi, keyin tashlanmaydi.
- Bir xil karta ketma-ket bir guruhga tushmaydi (7 kun) va boshqa guruhlarga ham darhol emas (3 soat).
- **Bir user bir xil kartadan faqat bitta nusxa** ola oladi: drop, market, offer, transfer, event va admin berishda tekshiriladi ("Bu kartadan sizda bor").

## Rank narxi va tashlash soni
Admin → ⚙️ Sozlamalar → **⭐ Ranklar**. Boshlang'ich: Basic 1000 coin/3 ta, Elita 2500/2, Epic 5000/2, Legend 10000/1, Mythic 25000/1, Divine 60000/1.
Karta qo'shishda faqat: personaj ismi → anime nomi → rank (inline tugma) → rasm. Narx va tashlash soni rankdan avtomatik olinadi.
Rasm: Telegram foto yoki to'g'ri havola (jpg/png/webp). Noto'g'ri havola yoki boshqa format qabul qilinmaydi.

## Limitli event
Admin → 🎯 Eventlar → ➕ Yangi event: hali tashlanmagan va hech kimda yo'q karta → nusxa soni → narx (coin / Stars tarifi / ikkalasi) → boshlash.
Event 24 soat davom etadi, karta guruhlarga tashlanmaydi, hamma faol guruhlarga e'lon yuboriladi. Foydalanuvchilar `/event` yoki 🎯 EVENT tugmasi orqali ham oladi.
Stars bilan olinganda **coin emas, kartaning o'zi** beriladi; event kartada coin reward berilmaydi; kolleksiyada 🎯 belgisi turadi.

## /give
`/give @username` → karta ismini yozasiz → sizdagi mos kartalar darajasi bilan chiqadi → tanlaysiz → tasdiqlaysiz. Pullik: `/give @user Gojo 1000`.

## Yangilanish
Eski bazaga yangi ustun/jadvallar bot ishga tushganda **avtomatik** qo'shiladi. Alembic ishlatsangiz: `alembic upgrade head`.
Eslatma: guruhda ismni yozish (FSM) uchun BotFather'da `/setprivacy` → **Disable** tavsiya etiladi.


## Production / Railway

- Railway PostgreSQL should be provided through `DATABASE_URL`. The application normalizes Railway's `postgresql://` URL to SQLAlchemy's asyncpg driver.
- The Docker entrypoint runs `python -m alembic upgrade head` before starting the bot and stops if migration fails. Keep `AUTO_SCHEMA_UPGRADE=false` in production so Alembic remains the single schema-upgrade path.
- For small Railway PostgreSQL plans, the default pool is intentionally conservative: `DB_POOL_SIZE=5`, `DB_MAX_OVERFLOW=10`, `DB_POOL_TIMEOUT=30`. Increase only after measuring connection usage.
- `REDIS_URL` is recommended in production for distributed locks, cooldowns and FSM state. Without Redis the bot falls back to in-memory state/locks and should run as a single instance.
- Never commit `.env`, bot tokens, Stars secrets or database credentials. Use Railway environment variables / GitHub Secrets.

## GitHub + Railway yangilash

Agar bot GitHub repository va Railway orqali deploy qilingan bo'lsa:

1. Ushbu yangi kodni GitHub repository'dagi eski kod o'rniga qo'ying.
2. `.env` yoki Railway secretlarini GitHub'ga yuklamang. Faqat `.env.example` commit qilinadi.
3. Commit qiling va `main`/production branch'ga push qiling.
4. Railway GitHub repository bilan bog'langan bo'lsa, push'dan keyin yangi deployment avtomatik boshlanadi.
5. Docker entrypoint avval `python -m alembic upgrade head` bajaradi, keyin `python -m app.main`ni ishga tushiradi. Railway start command app buyrug'ini ko'rsatadi; migration barcha Docker start command override'laridan oldin entrypoint orqali bajariladi.
6. Railway Variables ichida `BOT_TOKEN`, `ADMIN_IDS`, `DATABASE_URL`, `REDIS_URL` (agar ishlatilsa) va `STARS_PAYMENT_SECRET` saqlanadi.
7. Deploy logida `Anime Card Collection bot is starting polling...` ko'rinsa, bot pollingni boshlagan bo'ladi.

### Muhim

Production PostgreSQL bazasini o'zgartiradigan kod yangilanishlarida avval yangi Alembic migration yarating. `AUTO_SCHEMA_UPGRADE=true` ni production'da yoqmang.

Redis bir nechta Railway instance ishlatilsa tavsiya etiladi: local MemoryStorage/lock fallback faqat bitta instance uchun xavfsiz.


## 2026-11-23 P0/P1 stability changes

- Random card drops now choose the rank from the admin-configured `drop_probability` first, then choose a card inside that rank.
- `rank_drop_mult` acts as an enable/disable gate for random drops; it no longer silently changes the configured rank probability.
- Active event rarity boosts are applied as a temporary rank-probability multiplier and normalized.
- New-user welcome bonus is 2,000 coins. Existing balances are not modified by this change.
- The startup process no longer runs the old legacy mass-welcome-bonus migration.
- `/game` documentation now lists all six implemented mini-games.
