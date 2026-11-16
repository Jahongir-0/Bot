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
1. Install Python **3.11+** (3.10+ supported).
2. Create virtualenv & install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and set `BOT_TOKEN` and `ADMIN_IDS`.
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
