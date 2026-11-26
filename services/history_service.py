"""Admin uchun tarix va hisobotlar: kim nima qildi, kim nima sotdi / sotib oldi, komissiya, top foydalanuvchilar.

Barcha ma'lumot mavjud jadvallardan o'qiladi (tranzaksiyalar, bozor sotuvlari, olingan kartalar,
guruhlar, murojaatlar) — yangi jadval talab qilinmaydi.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from aiogram import Bot
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.base import utcnow
from models.card import Card
from models.group import Group
from models.market import CardListing
from models.transaction import CoinTransaction
from models.user import User
from models.user_card import UserCard
from models.v2_features import SupportTicket
from utils.formatting import esc
from utils.timeutils import fmt_tashkent

logger = logging.getLogger(__name__)

PERIODS = [("d1", "24 soat"), ("d7", "7 kun"), ("d30", "30 kun"), ("all", "Hammasi")]

COMMISSION_TYPES = ("market_sell", "offer_accepted_sale", "paid_transfer_sell", "auction_sale")

TX_CATEGORIES: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "all": ("Hammasi", ()),
    "market": ("🛒 Bozor/Taklif/Sovg‘a", ("market_buy", "market_sell", "offer_accepted_sale", "offer_escrow", "offer_refund", "paid_transfer_buy", "paid_transfer_sell")),
    "auction": ("🔨 Auksion", ("auction_win", "auction_sale", "auction_bid", "auction_refund")),
    "store": ("🏛 Do‘kon/Event", ("bot_store_buy", "bot_market_sell", "event_buy", "card_migration_refund")),
    "reward": ("🎁 Mukofotlar", ("daily_bonus", "quest_reward", "group_add_reward", "referral_bonus", "referral_welcome", "card_drop_reward")),
    "admin": ("🛡 Admin", ("admin_grant", "admin_deduct")),
    "stars": ("⭐ Stars", ("telegram_stars_purchase",)),
    "other": ("📦 Boshqa", ()),
}

TX_LABELS = {
    "market_buy": "🛒 Bozordan sotib oldi",
    "market_sell": "💰 Bozorda sotdi",
    "offer_accepted_sale": "🤝 Taklifni qabul qilib sotdi",
    "offer_escrow": "🔒 Taklif uchun coin band",
    "offer_refund": "↩️ Taklif qaytarildi",
    "paid_transfer_buy": "🎁 Pullik sovg‘ani oldi",
    "paid_transfer_sell": "🎁 Pullik sovg‘a berdi",
    "auction_win": "🔨 Auksionda yutdi",
    "auction_sale": "🔨 Auksionda sotdi",
    "bot_store_buy": "🏛 Bot do‘konidan oldi",
    "bot_market_sell": "🏛 Botga sotdi",
    "event_buy": "🎯 Event kartasi oldi",
    "card_migration_refund": "♻️ Karta o‘chirildi (kompensatsiya)",
    "daily_bonus": "📅 Kunlik bonus",
    "quest_reward": "📜 Vazifa mukofoti",
    "group_add_reward": "🏰 Guruhga qo‘shish mukofoti",
    "referral_bonus": "👥 Referal bonusi",
    "referral_welcome": "👋 Referal xush kelibsiz",
    "card_drop_reward": "🎴 Karta topish mukofoti",
    "admin_grant": "🛡 Admin coin berdi",
    "admin_deduct": "🛡 Admin coin oldi",
    "telegram_stars_purchase": "⭐ Stars bilan coin sotib oldi",
}


def period_since(period: str) -> Optional[datetime]:
    now = utcnow()
    return {"d1": now - timedelta(days=1), "d7": now - timedelta(days=7), "d30": now - timedelta(days=30)}.get(period)


def period_label(period: str) -> str:
    return dict(PERIODS).get(period, "Hammasi")


def _known_types() -> List[str]:
    out: List[str] = []
    for key, (_t, types) in TX_CATEGORIES.items():
        out.extend(types)
    return out


def _tx_conds(since: Optional[datetime], category: str = "all", user_id: Optional[int] = None) -> list:
    conds: list = []
    if since is not None:
        conds.append(CoinTransaction.created_at >= since)
    if user_id is not None:
        conds.append(CoinTransaction.user_id == user_id)
    if category == "other":
        conds.append(CoinTransaction.tx_type.not_in(_known_types()))
    elif category != "all":
        types = TX_CATEGORIES.get(category, ("", ()))[1]
        if category == "auction":
            conds.append(or_(CoinTransaction.tx_type.in_(types), CoinTransaction.tx_type.like("auction%")))
        elif types:
            conds.append(CoinTransaction.tx_type.in_(types))
    return conds


class HistoryService:
    # ------------------------------------------------------------------ umumiy hisobot
    @staticmethod
    async def overview(session: AsyncSession, since: Optional[datetime]) -> Dict[str, Any]:
        def _t(col):
            return [col >= since] if since is not None else []

        new_users = int((await session.execute(select(func.count(User.id)).where(*_t(User.created_at)))).scalar_one() or 0)
        total_users = int((await session.execute(select(func.count(User.id)))).scalar_one() or 0)
        active_users = int(
            (await session.execute(select(func.count(User.id)).where(*_t(User.last_activity)))).scalar_one() or 0
        )
        tx_conds = _tx_conds(since)
        tx_count = int((await session.execute(select(func.count(CoinTransaction.id)).where(*tx_conds))).scalar_one() or 0)
        coins_in = int(
            (await session.execute(select(func.coalesce(func.sum(CoinTransaction.amount), 0)).where(*tx_conds, CoinTransaction.amount > 0))).scalar_one()
            or 0
        )
        coins_out = int(
            (await session.execute(select(func.coalesce(func.sum(CoinTransaction.amount), 0)).where(*tx_conds, CoinTransaction.amount < 0))).scalar_one()
            or 0
        )
        sold_conds = [CardListing.status == "sold"] + ([CardListing.sold_at >= since] if since is not None else [])
        mk_row = (
            await session.execute(select(func.count(CardListing.id), func.coalesce(func.sum(CardListing.price), 0)).where(*sold_conds))
        ).one()
        commission_total, commission_by_type = await HistoryService.commission(session, since)

        async def _cnt(tx_type: str) -> Tuple[int, int]:
            r = (
                await session.execute(
                    select(func.count(CoinTransaction.id), func.coalesce(func.sum(CoinTransaction.amount), 0)).where(
                        *tx_conds, CoinTransaction.tx_type == tx_type
                    )
                )
            ).one()
            return int(r[0] or 0), int(r[1] or 0)

        auc_cnt, _auc_sum = await _cnt("auction_sale")
        store_cnt, store_sum = await _cnt("bot_store_buy")
        stars_cnt, stars_sum = await _cnt("telegram_stars_purchase")
        grp_conds = [Group.add_reward_claimed.is_(True)] + ([Group.updated_at >= since] if since is not None else [])
        groups_rewarded = int((await session.execute(select(func.count(Group.id)).where(*grp_conds))).scalar_one() or 0)
        groups_new = int((await session.execute(select(func.count(Group.id)).where(*_t(Group.created_at)))).scalar_one() or 0)
        via_rows = (
            await session.execute(
                select(UserCard.acquired_via, func.count(UserCard.id)).where(*_t(UserCard.created_at)).group_by(UserCard.acquired_via)
            )
        ).all()
        tickets = int((await session.execute(select(func.count(SupportTicket.id)).where(*_t(SupportTicket.created_at)))).scalar_one() or 0)
        return {
            "new_users": new_users,
            "total_users": total_users,
            "active_users": active_users,
            "tx_count": tx_count,
            "coins_in": coins_in,
            "coins_out": abs(coins_out),
            "market_count": int(mk_row[0] or 0),
            "market_volume": int(mk_row[1] or 0),
            "commission_total": commission_total,
            "commission_by_type": commission_by_type,
            "auction_sales": auc_cnt,
            "store_buys": store_cnt,
            "store_spent": abs(store_sum),
            "stars_count": stars_cnt,
            "stars_coins": stars_sum,
            "groups_rewarded": groups_rewarded,
            "groups_new": groups_new,
            "cards_by_via": {str(v or "?"): int(c) for v, c in via_rows},
            "tickets": tickets,
        }

    # ------------------------------------------------------------------ komissiya
    @staticmethod
    async def commission(session: AsyncSession, since: Optional[datetime]) -> Tuple[int, Dict[str, int]]:
        rows = (
            await session.execute(
                select(CoinTransaction.tx_type, CoinTransaction.meta).where(
                    *_tx_conds(since), CoinTransaction.tx_type.in_(COMMISSION_TYPES)
                )
            )
        ).all()
        by_type: Dict[str, int] = {}
        total = 0
        for tx_type, meta in rows:
            try:
                fee = int((meta or {}).get("commission") or 0)
            except (TypeError, ValueError):
                fee = 0
            if fee > 0:
                by_type[tx_type] = by_type.get(tx_type, 0) + fee
                total += fee
        return total, by_type

    # ------------------------------------------------------------------ tranzaksiyalar
    @staticmethod
    async def tx_page(
        session: AsyncSession,
        since: Optional[datetime],
        category: str,
        page: int,
        per_page: int = 12,
        user_id: Optional[int] = None,
    ) -> Tuple[List[Tuple[CoinTransaction, User]], int]:
        conds = _tx_conds(since, category, user_id)
        total = int((await session.execute(select(func.count(CoinTransaction.id)).where(*conds))).scalar_one() or 0)
        rows = (
            await session.execute(
                select(CoinTransaction, User)
                .join(User, User.id == CoinTransaction.user_id)
                .where(*conds)
                .order_by(CoinTransaction.id.desc())
                .offset((max(1, page) - 1) * per_page)
                .limit(per_page)
            )
        ).all()
        return [(t, u) for t, u in rows], total

    @staticmethod
    def format_tx_line(tx: CoinTransaction, user: Optional[User], show_user: bool = True) -> str:
        sign = "➕" if tx.amount > 0 else "➖"
        label = TX_LABELS.get(tx.tx_type, tx.tx_type)
        who = f"{esc(user.display_name)} · " if (show_user and user is not None) else ""
        fee = ""
        try:
            f = int((tx.meta or {}).get("commission") or 0)
            if f > 0:
                fee = f" (komissiya {f:,})"
        except (TypeError, ValueError):
            pass
        desc = esc((tx.description or "")[:38])
        return (
            f"<code>{fmt_tashkent(tx.created_at, '%d.%m %H:%M')}</code> {who}{sign} <b>{abs(int(tx.amount)):,}</b> 🪙\n"
            f"   {label}{fee} — {desc}"
        )

    # ------------------------------------------------------------------ bozor savdolari
    @staticmethod
    async def market_sales_page(
        session: AsyncSession, since: Optional[datetime], page: int, per_page: int = 8
    ) -> Tuple[List[dict], int]:
        conds = [CardListing.status == "sold"] + ([CardListing.sold_at >= since] if since is not None else [])
        total = int((await session.execute(select(func.count(CardListing.id)).where(*conds))).scalar_one() or 0)
        listings = (
            await session.execute(
                select(CardListing).where(*conds).order_by(CardListing.sold_at.desc().nullslast(), CardListing.id.desc())
                .offset((max(1, page) - 1) * per_page).limit(per_page)
            )
        ).scalars().all()
        if not listings:
            return [], total
        user_ids = {l.seller_id for l in listings} | {l.buyer_id for l in listings if l.buyer_id}
        card_ids = {l.card_id for l in listings}
        users = {u.id: u for u in (await session.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()}
        cards = {c.id: c for c in (await session.execute(select(Card).where(Card.id.in_(card_ids)))).scalars().all()}
        refs = [f"listing:{l.id}" for l in listings]
        fee_rows = (
            await session.execute(
                select(CoinTransaction.reference_id, CoinTransaction.meta).where(
                    CoinTransaction.reference_id.in_(refs), CoinTransaction.tx_type == "market_sell"
                )
            )
        ).all()
        fees: Dict[str, int] = {}
        for ref, meta in fee_rows:
            try:
                fees[str(ref)] = int((meta or {}).get("commission") or 0)
            except (TypeError, ValueError):
                fees[str(ref)] = 0
        out = []
        for l in listings:
            seller = users.get(l.seller_id)
            buyer = users.get(l.buyer_id) if l.buyer_id else None
            card = cards.get(l.card_id)
            out.append(
                {
                    "when": l.sold_at or l.updated_at,
                    "seller": seller.display_name if seller else f"#{l.seller_id}",
                    "buyer": buyer.display_name if buyer else "—",
                    "card": card.character_name if card else f"#{l.card_id}",
                    "code": card.card_code if card else "",
                    "price": int(l.price),
                    "fee": fees.get(f"listing:{l.id}", 0),
                }
            )
        return out, total

    # ------------------------------------------------------------------ top foydalanuvchilar
    @staticmethod
    async def top_users(session: AsyncSession, since: Optional[datetime], kind: str, limit: int = 10) -> List[Tuple[User, int, int]]:
        """kind: seller | buyer | spender | earner -> [(user, soni, summa)]"""
        types = {
            "seller": ("market_sell", "offer_accepted_sale", "auction_sale", "paid_transfer_sell"),
            "buyer": ("market_buy", "auction_win", "bot_store_buy", "event_buy", "paid_transfer_buy"),
        }.get(kind)
        conds = _tx_conds(since)
        if types:
            conds.append(CoinTransaction.tx_type.in_(types))
            amount_expr = func.sum(func.abs(CoinTransaction.amount))
        elif kind == "spender":
            conds.append(CoinTransaction.amount < 0)
            amount_expr = func.sum(func.abs(CoinTransaction.amount))
        else:
            conds.append(CoinTransaction.amount > 0)
            amount_expr = func.sum(CoinTransaction.amount)
        rows = (
            await session.execute(
                select(CoinTransaction.user_id, func.count(CoinTransaction.id), amount_expr.label("s"))
                .where(*conds)
                .group_by(CoinTransaction.user_id)
                .order_by(amount_expr.desc())
                .limit(limit)
            )
        ).all()
        if not rows:
            return []
        users = {u.id: u for u in (await session.execute(select(User).where(User.id.in_([r[0] for r in rows])))).scalars().all()}
        return [(users[r[0]], int(r[1]), int(r[2] or 0)) for r in rows if r[0] in users]

    # ------------------------------------------------------------------ olingan kartalar
    @staticmethod
    async def claims_page(session: AsyncSession, since: Optional[datetime], page: int, per_page: int = 10):
        conds = [UserCard.created_at >= since] if since is not None else []
        total = int((await session.execute(select(func.count(UserCard.id)).where(*conds))).scalar_one() or 0)
        rows = (
            await session.execute(
                select(UserCard, Card, User)
                .join(Card, Card.id == UserCard.card_id)
                .join(User, User.id == UserCard.user_id)
                .where(*conds)
                .order_by(UserCard.id.desc())
                .offset((max(1, page) - 1) * per_page)
                .limit(per_page)
            )
        ).all()
        return rows, total

    # ------------------------------------------------------------------ foydalanuvchi xulosasi
    @staticmethod
    async def find_users(session: AsyncSession, query: str, limit: int = 8) -> List[User]:
        q = (query or "").strip().lstrip("@")
        if not q:
            return []
        conds = [User.username.ilike(f"%{q}%"), User.first_name.ilike(f"%{q}%")]
        if q.isdigit():
            conds.append(User.telegram_id == int(q))
            conds.append(User.id == int(q))
        return list((await session.execute(select(User).where(or_(*conds)).order_by(User.last_activity.desc()).limit(limit))).scalars().all())

    @staticmethod
    async def user_summary(session: AsyncSession, user: User) -> Dict[str, Any]:
        async def _agg(types: Tuple[str, ...]) -> Tuple[int, int]:
            r = (
                await session.execute(
                    select(func.count(CoinTransaction.id), func.coalesce(func.sum(func.abs(CoinTransaction.amount)), 0)).where(
                        CoinTransaction.user_id == user.id, CoinTransaction.tx_type.in_(types)
                    )
                )
            ).one()
            return int(r[0] or 0), int(r[1] or 0)

        sold_n, sold_sum = await _agg(("market_sell", "offer_accepted_sale", "auction_sale", "paid_transfer_sell"))
        bought_n, bought_sum = await _agg(("market_buy", "auction_win", "paid_transfer_buy"))
        store_n, store_sum = await _agg(("bot_store_buy", "event_buy"))
        rew_n, rew_sum = await _agg(("daily_bonus", "quest_reward", "group_add_reward", "referral_bonus", "referral_welcome", "card_drop_reward"))
        fee_rows = (
            await session.execute(
                select(CoinTransaction.meta).where(CoinTransaction.user_id == user.id, CoinTransaction.tx_type.in_(COMMISSION_TYPES))
            )
        ).scalars().all()
        fee_paid = 0
        for meta in fee_rows:
            try:
                fee_paid += int((meta or {}).get("commission") or 0)
            except (TypeError, ValueError):
                pass
        groups_added = int((await session.execute(select(func.count(Group.id)).where(Group.added_by_user_id == user.id))).scalar_one() or 0)
        tickets = int((await session.execute(select(func.count(SupportTicket.id)).where(SupportTicket.user_id == user.id))).scalar_one() or 0)
        return {
            "sold_n": sold_n, "sold_sum": sold_sum,
            "bought_n": bought_n, "bought_sum": bought_sum,
            "store_n": store_n, "store_sum": store_sum,
            "rew_n": rew_n, "rew_sum": rew_sum,
            "fee_paid": fee_paid, "groups_added": groups_added, "tickets": tickets,
        }


# ======================================================================
# Jonli xabarlar (digest): admin har N daqiqada yangi harakatlar haqida xabar oladi
# ======================================================================
FEED_KEY = "admin_live_feed"
FEED_DEFAULT: Dict[str, Any] = {"enabled": False, "interval_min": 15, "last_tx_id": 0, "last_sent": None, "skip_rewards": True}
_FEED_SKIP_TYPES = ("daily_bonus", "card_drop_reward", "quest_reward", "referral_welcome")


class AdminFeedService:
    @staticmethod
    async def get_cfg(session: AsyncSession) -> Dict[str, Any]:
        from services.settings_service import SettingsService

        cfg = dict(FEED_DEFAULT)
        stored = await SettingsService.get_value(session, FEED_KEY, None)
        if isinstance(stored, dict):
            cfg.update(stored)
        return cfg

    @staticmethod
    async def save_cfg(session: AsyncSession, cfg: Dict[str, Any]) -> None:
        from services.settings_service import SettingsService

        await SettingsService.set_value(session, FEED_KEY, cfg)

    @staticmethod
    async def run(bot: Bot) -> None:
        """Scheduler chaqiradi (~5 daqiqada bir). Interval o'tgan bo'lsa yangi harakatlar digestini yuboradi."""
        from database.session import get_session
        from services.group_policy_service import GroupPolicyService

        async with get_session() as session:
            cfg = await AdminFeedService.get_cfg(session)
            if not cfg.get("enabled"):
                return
            now = utcnow()
            last_sent = cfg.get("last_sent")
            if last_sent:
                try:
                    if now - datetime.fromisoformat(last_sent) < timedelta(minutes=int(cfg.get("interval_min", 15)) - 1):
                        return
                except Exception:
                    pass
            last_id = int(cfg.get("last_tx_id") or 0)
            conds = [CoinTransaction.id > last_id]
            if cfg.get("skip_rewards", True):
                conds.append(CoinTransaction.tx_type.not_in(_FEED_SKIP_TYPES))
            if last_id == 0:
                # birinchi ishga tushishda eski tarixni yubormaymiz
                max_id = int((await session.execute(select(func.max(CoinTransaction.id)))).scalar_one() or 0)
                cfg["last_tx_id"] = max_id
                cfg["last_sent"] = now.isoformat()
                await AdminFeedService.save_cfg(session, cfg)
                await session.commit()
                return
            rows = (
                await session.execute(
                    select(CoinTransaction, User)
                    .join(User, User.id == CoinTransaction.user_id)
                    .where(*conds)
                    .order_by(CoinTransaction.id.asc())
                    .limit(200)
                )
            ).all()
            new_last = max([int(t.id) for t, _u in rows], default=last_id)
            # offer_escrow/refund shovqin — digestda ko'rsatilmaydi
            shown = [(t, u) for t, u in rows if t.tx_type not in ("offer_escrow", "offer_refund")]
            cfg["last_tx_id"] = max(new_last, last_id)
            cfg["last_sent"] = now.isoformat()
            await AdminFeedService.save_cfg(session, cfg)
            admin_ids = await GroupPolicyService.admin_chat_ids(session)
            await session.commit()
        if not shown:
            return
        lines = [f"🔔 <b>JONLI HARAKATLAR</b> — oxirgi {int(cfg.get('interval_min', 15))} daqiqa ({len(shown)} ta)\n━━━━━━━━━━━━━━━"]
        for t, u in shown[-25:]:
            lines.append(HistoryService.format_tx_line(t, u))
        if len(shown) > 25:
            lines.append(f"… oldingi {len(shown) - 25} ta ko‘rsatilmadi (Tarix bo‘limidan ko‘ring)")
        text = "\n".join(lines)
        chunks = [text[i : i + 3800] for i in range(0, len(text), 3800)]
        for aid in admin_ids:
            for ch in chunks:
                try:
                    await bot.send_message(aid, ch)
                except Exception as exc:
                    logger.debug("feed -> %s: %s", aid, exc)
                await asyncio.sleep(0.05)
