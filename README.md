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
