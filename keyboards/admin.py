import math
from typing import Optional, List, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from models.card import Card
from models.group import Group
from models.schedule import DropSchedule
from models.user import User
from utils.callback_data import AdmV2Cb, AdminCb, NavCb, PageJumpCb
from utils.constants import RankingMode, get_rarity_meta


def admin_home_kb(owner_id: int) -> InlineKeyboardMarkup:
    """Legacy fallback; asosiy menyu uchun admin_v2_home_kb ishlatiladi."""
    from admin.filters import resolve_admin_perms
    from keyboards.v2 import admin_v2_home_kb

    class _Dummy:
        def __init__(self, uid: int):
            self.id = uid
            self.username = None

    return admin_v2_home_kb(owner_id, resolve_admin_perms(_Dummy(owner_id), None))


def admin_cards_menu_kb(owner_id: int) -> InlineKeyboardMarkup:
    from keyboards.v2 import admin_v2_cards_menu_kb

    return admin_v2_cards_menu_kb(owner_id)


def admin_cards_list_kb(
    owner_id: int,
    cards: List[Card],
    filter_mode: str,
    page: int,
    total: int,
    page_size: int = 20,
    mode: str = "list",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for c in cards:
        meta = get_rarity_meta(c.rarity)
        if mode == "del_list":
            btn_text = f"🗑 {meta.emoji} {c.character_name[:22]} ({c.card_code})"
            cb = AdminCb(owner_id=owner_id, section="cards", action="delete_confirm", target_id=c.id, page=page).pack()
        else:
            st = "🟢" if c.is_available else "🔴"
            btn_text = f"{st} {meta.emoji} {c.character_name[:22]} ({c.card_code})"
            cb = AdmV2Cb(owner_id=owner_id, sec="cards", act="card_det", tid=c.id, ext="all", p=page).pack()
        builder.row(InlineKeyboardButton(text=btn_text[:60], callback_data=cb))

    total_pages = max(1, math.ceil(total / page_size))
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=AdminCb(owner_id=owner_id, section="cards", action=mode, extra=filter_mode, page=page - 1).pack()))
    nav.append(
        InlineKeyboardButton(
            text=f"{page}/{total_pages}",
            callback_data=PageJumpCb(
                owner_id=owner_id,
                key="av_tab",
                total=total_pages,
                ext=(filter_mode or "all").replace(":", " ")[:16],
            ).pack(),
        )
    )
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=AdminCb(owner_id=owner_id, section="cards", action=mode, extra=filter_mode, page=page + 1).pack()))
    builder.row(*nav)
    builder.row(
        InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="v").pack()),
        InlineKeyboardButton(text="🏠 BOSH MENU", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return builder.as_markup()


def admin_pick_group_for_card_kb(
    owner_id: int,
    card_id: int,
    groups: List[Group],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for g in groups[:20]:
        builder.row(
            InlineKeyboardButton(
                text=f"👥 {g.title[:34]}",
                callback_data=AdminCb(
                    owner_id=owner_id,
                    section="cards",
                    action="drop_exec",
                    target_id=card_id,
                    extra=str(g.id),
                ).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=AdmV2Cb(owner_id=owner_id, sec="cards", act="card_det", tid=card_id).pack(),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def admin_users_menu_kb(owner_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="👥 Foydalanuvchilar (20/sahifa)",
            callback_data=AdminCb(owner_id=owner_id, section="users", action="list", extra="all", page=1).pack(),
        ),
        InlineKeyboardButton(
            text="🔍 Qidirish",
            callback_data=AdminCb(owner_id=owner_id, section="users", action="search_prompt").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🚫 Ban qilish",
            callback_data=AdminCb(owner_id=owner_id, section="users", action="list", extra="active", page=1).pack(),
        ),
        InlineKeyboardButton(
            text="🔓 Bandan chiqarish",
            callback_data=AdminCb(owner_id=owner_id, section="users", action="list", extra="banned", page=1).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdminCb(owner_id=owner_id, section="home").pack()),
        InlineKeyboardButton(text="🏠 BOSH MENU", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return builder.as_markup()


def admin_users_list_kb(
    owner_id: int,
    users: List[User],
    filter_mode: str,
    page: int,
    total: int,
    page_size: int = 20,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for u in users:
        st = "🚫" if u.is_banned else "👤"
        if filter_mode == "active":
            btn_text = f"🚫 Ban: {u.display_name} (🎴{u.total_cards})"
            action_name = "quick_ban"
        elif filter_mode == "banned":
            btn_text = f"🔓 Unban: {u.display_name} ({u.telegram_id})"
            action_name = "quick_unban"
        else:
            btn_text = f"{st} {u.display_name} · 🪙{u.coins:,} · 🎴{u.total_cards}"
            action_name = "detail"
        builder.row(
            InlineKeyboardButton(
                text=btn_text[:60],
                callback_data=AdminCb(
                    owner_id=owner_id,
                    section="users",
                    action=action_name,
                    target_id=u.id,
                    extra=filter_mode,
                    page=page,
                ).pack(),
            )
        )

    total_pages = max(1, math.ceil(total / page_size))
    nav = []
    if page > 1:
        nav.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=AdminCb(owner_id=owner_id, section="users", action="list", extra=filter_mode, page=page - 1).pack(),
            )
        )
    nav.append(
        InlineKeyboardButton(
            text=f"{page}/{total_pages}",
            callback_data=PageJumpCb(
                owner_id=owner_id,
                key="ad_ul",
                total=total_pages,
                ext=(filter_mode or "all").replace(":", " ")[:12],
            ).pack(),
        )
    )
    if page < total_pages:
        nav.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=AdminCb(owner_id=owner_id, section="users", action="list", extra=filter_mode, page=page + 1).pack(),
            )
        )
    builder.row(*nav)
    builder.row(
        InlineKeyboardButton(text="🔍 Qidirish", callback_data=AdminCb(owner_id=owner_id, section="users", action="search_prompt").pack()),
        InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdminCb(owner_id=owner_id, section="users", action="menu").pack()),
    )
    builder.row(
        InlineKeyboardButton(text="🏠 BOSH MENU", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return builder.as_markup()


def admin_user_detail_kb(owner_id: int, target_user: User) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="➕ Coin berish",
            callback_data=AdminCb(owner_id=owner_id, section="users", action="add_coins", target_id=target_user.id).pack(),
        ),
        InlineKeyboardButton(
            text="➖ Coin olish",
            callback_data=AdminCb(owner_id=owner_id, section="users", action="sub_coins", target_id=target_user.id).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="➕ Karta berish",
            callback_data=AdminCb(owner_id=owner_id, section="users", action="give_pick", target_id=target_user.id, page=1).pack(),
        ),
        InlineKeyboardButton(
            text="➖ Karta olish",
            callback_data=AdminCb(owner_id=owner_id, section="users", action="rem_pick", target_id=target_user.id, page=1).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="✉️ Xabar yuborish",
            callback_data=AdminCb(owner_id=owner_id, section="users", action="msg_prompt", target_id=target_user.id).pack(),
        ),
        InlineKeyboardButton(
            text="⏱ Vaqtli ban",
            callback_data=AdminCb(owner_id=owner_id, section="ban", action="temp_menu", target_id=target_user.id).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="💎 VIP berish / olish",
            callback_data=AdminCb(owner_id=owner_id, section="users", action="vip_menu", target_id=target_user.id).pack(),
        ),
    )
    ban_btn = (
        InlineKeyboardButton(
            text="🔓 Bandan chiqarish",
            callback_data=AdminCb(owner_id=owner_id, section="ban", action="unban", target_id=target_user.id).pack(),
        )
        if target_user.is_banned
        else InlineKeyboardButton(
            text="🚫 Ban qilish",
            callback_data=AdminCb(owner_id=owner_id, section="ban", action="ban", target_id=target_user.id).pack(),
        )
    )
    builder.row(ban_btn)
    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=AdminCb(owner_id=owner_id, section="users", action="list", extra="all", page=1).pack(),
        ),
        InlineKeyboardButton(text="🏠 BOSH MENU", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return builder.as_markup()


def admin_pick_card_for_user_kb(
    owner_id: int,
    target_user_id: int,
    cards: List[Card],
    page: int,
    total: int,
    page_size: int = 20,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for c in cards:
        meta = get_rarity_meta(c.rarity)
        builder.row(
            InlineKeyboardButton(
                text=f"➕ {meta.emoji} {c.character_name[:24]} · {c.anime_name[:16]}"[:60],
                callback_data=AdminCb(
                    owner_id=owner_id,
                    section="users",
                    action="give_prev",
                    target_id=target_user_id,
                    extra=str(c.id),
                    page=page,
                ).pack(),
            )
        )
    total_pages = max(1, math.ceil(total / page_size))
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=AdminCb(owner_id=owner_id, section="users", action="give_pick", target_id=target_user_id, page=page - 1).pack()))
    nav.append(
        InlineKeyboardButton(
            text=f"{page}/{total_pages}",
            callback_data=PageJumpCb(
                owner_id=owner_id,
                key="ad_gp",
                total=total_pages,
                tid=target_user_id,
            ).pack(),
        )
    )
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=AdminCb(owner_id=owner_id, section="users", action="give_pick", target_id=target_user_id, page=page + 1).pack()))
    builder.row(*nav)
    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=AdminCb(owner_id=owner_id, section="users", action="detail", target_id=target_user_id).pack(),
        )
    )
    return builder.as_markup()


def admin_user_cards_remove_kb(
    owner_id: int,
    target_user_id: int,
    items: List[Tuple[Card, int]],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for card, qty in items:
        meta = get_rarity_meta(card.rarity)
        builder.row(
            InlineKeyboardButton(
                text=f"➖ {meta.emoji} {card.character_name} × {qty}"[:60],
                callback_data=AdminCb(
                    owner_id=owner_id,
                    section="users",
                    action="rem_exec",
                    target_id=target_user_id,
                    extra=str(card.id),
                ).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=AdminCb(owner_id=owner_id, section="users", action="detail", target_id=target_user_id).pack(),
        )
    )
    return builder.as_markup()


def admin_groups_list_kb(
    owner_id: int,
    groups: List[Group],
    section: str,
    page: int = 1,
    total: int = 0,
    page_size: int = 20,
    filter_mode: str = "active",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if section == "groups":
        opts = [("active", "🟢 Faol"), ("left", "🗑 O‘chgan"), ("all", "📋 Hammasi")]
        builder.row(
            *[
                InlineKeyboardButton(
                    text=("✅ " if filter_mode == k else "") + lbl,
                    callback_data=AdminCb(owner_id=owner_id, section="groups", action="list", extra=k, page=1).pack(),
                )
                for k, lbl in opts
            ]
        )
    total = total or len(groups)
    for g in groups:
        bot_st = getattr(g, "bot_status", "active")
        if getattr(g, "is_banned", False):
            st = "🚫"
        elif bot_st in ("left", "kicked") or not g.is_active:
            st = "🗑"
        elif g.drop_enabled:
            st = "🟢"
        else:
            st = "🔴"
        members = f" · 👥{g.member_count}" if g.member_count is not None else ""
        uname = f" @{g.username}" if getattr(g, "username", None) else ""
        builder.row(
            InlineKeyboardButton(
                text=f"{st} {g.title[:22]}{uname}{members} ({g.daily_drop_count}/kun)"[:60],
                callback_data=AdminCb(
                    owner_id=owner_id,
                    section=section,
                    action="group_view",
                    target_id=g.id,
                    page=page,
                ).pack(),
            )
        )
    total_pages = max(1, math.ceil(total / page_size))
    if total_pages > 1:
        nav = []
        act_list = "list" if section == "groups" else "groups"
        ext_val = filter_mode if section == "groups" else ""
        if page > 1:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=AdminCb(owner_id=owner_id, section=section, action=act_list, extra=ext_val, page=page - 1).pack()))
        nav.append(
            InlineKeyboardButton(
                text=f"{page}/{total_pages}",
                callback_data=PageJumpCb(
                    owner_id=owner_id,
                    key="ad_gl",
                    total=total_pages,
                    ext=f"{section}|{ext_val}"[:18],
                ).pack(),
            )
        )
        if page < total_pages:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=AdminCb(owner_id=owner_id, section=section, action=act_list, extra=ext_val, page=page + 1).pack()))
        builder.row(*nav)
    builder.row(
        InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdminCb(owner_id=owner_id, section="home").pack()),
        InlineKeyboardButton(text="🏠 BOSH MENU", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return builder.as_markup()


def admin_schedule_detail_kb(
    owner_id: int,
    group_id: int,
    schedules: List[DropSchedule],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🔄 JADVALNI QAYTA YARATISH",
            callback_data=AdminCb(owner_id=owner_id, section="schedule", action="regen", target_id=group_id).pack(),
        )
    )
    for sc in schedules:
        if sc.status == "pending":
            builder.row(
                InlineKeyboardButton(
                    text=f"⭐ Slot #{sc.id} ga karta tanlash",
                    callback_data=AdminCb(owner_id=owner_id, section="schedule", action="slot_prio", target_id=sc.id).pack(),
                )
            )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ Guruh",
            callback_data=AdminCb(owner_id=owner_id, section="groups", action="group_view", target_id=group_id).pack(),
        ),
        InlineKeyboardButton(text="🏠 BOSH MENU", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return builder.as_markup()


def admin_group_settings_kb(owner_id: int, group: Group, link: Optional[str] = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if link:
        builder.row(InlineKeyboardButton(text="🚪 Guruhga kirish", url=link))
    toggle_text = "🔴 Dropni o‘chirish" if group.drop_enabled else "🟢 Dropni yoqish"
    builder.row(
        InlineKeyboardButton(
            text=toggle_text,
            callback_data=AdminCb(owner_id=owner_id, section="groups", action="toggle_drop", target_id=group.id).pack(),
        ),
        InlineKeyboardButton(
            text=("✅ 🤖 Avto" if group.auto_drop_count else "🤖 Avto rejim"),
            callback_data=AdminCb(owner_id=owner_id, section="groups", action="toggle_auto", target_id=group.id).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="➖ (-1)",
            callback_data=AdminCb(owner_id=owner_id, section="groups", action="drop_dec", target_id=group.id).pack(),
        ),
        InlineKeyboardButton(
            text="🔢 Kunlik soni",
            callback_data=AdminCb(owner_id=owner_id, section="groups", action="drop_set", target_id=group.id).pack(),
        ),
        InlineKeyboardButton(
            text="➕ (+1)",
            callback_data=AdminCb(owner_id=owner_id, section="groups", action="drop_inc", target_id=group.id).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🎴 Hozir drop tashlash",
            callback_data=AdmV2Cb(owner_id=owner_id, sec="groups", act="pick_card", tid=group.id, p=1).pack(),
        ),
        InlineKeyboardButton(
            text="⚡ Tasodifiy drop",
            callback_data=AdminCb(owner_id=owner_id, section="groups", action="drop_now", target_id=group.id).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="✉️ Xabar yuborish",
            callback_data=AdminCb(owner_id=owner_id, section="groups", action="msg_prompt", target_id=group.id).pack(),
        ),
        InlineKeyboardButton(
            text="🏆 Eng faol userlar",
            callback_data=AdminCb(owner_id=owner_id, section="groups", action="top_users", target_id=group.id).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="📅 Bugungi jadval",
            callback_data=AdminCb(owner_id=owner_id, section="schedule", action="group_view", target_id=group.id).pack(),
        ),
        InlineKeyboardButton(
            text="❌ Guruhni o‘chirish",
            callback_data=AdminCb(owner_id=owner_id, section="groups", action="remove_prompt", target_id=group.id).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ Guruhlar",
            callback_data=AdminCb(owner_id=owner_id, section="groups", action="list", page=1).pack(),
        ),
        InlineKeyboardButton(text="🏠 BOSH MENU", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return builder.as_markup()


def admin_settings_kb(
    owner_id: int,
    ranking_mode: str,
    maintenance_on: bool = False,
    pack_daily_limit: int = 1,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=f"🎁 Paket limiti ({pack_daily_limit}/kun)",
            callback_data=AdminCb(owner_id=owner_id, section="settings", action="pack_limit").pack(),
        ),
        InlineKeyboardButton(
            text="🎁 Kunlik bonus",
            callback_data=AdminCb(owner_id=owner_id, section="settings", action="daily_menu").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="💸 Coin yuborish",
            callback_data=AdminCb(owner_id=owner_id, section="settings", action="cg_menu").pack(),
        ),
        InlineKeyboardButton(
            text="🏛 Bot Do‘koni",
            callback_data=AdmV2Cb(owner_id=owner_id, sec="econ", act="bs").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="💸 Komissiyalar",
            callback_data=AdmV2Cb(owner_id=owner_id, sec="econ", act="comm").pack(),
        ),
        InlineKeyboardButton(
            text="🔮 Birlashtirish",
            callback_data=AdmV2Cb(owner_id=owner_id, sec="crules", act="v").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🤝 Referal & Guruh",
            callback_data=AdminCb(owner_id=owner_id, section="settings", action="rewards_menu").pack(),
        ),
        InlineKeyboardButton(
            text="📡 Drop qoidalari",
            callback_data=AdminCb(owner_id=owner_id, section="settings", action="drop_rules").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="⭐ Ranklar",
            callback_data=AdmV2Cb(owner_id=owner_id, sec="ranks", act="v").pack(),
        ),
        InlineKeyboardButton(
            text="🎯 Navbatdagi karta",
            callback_data=AdminCb(owner_id=owner_id, section="priority", action="view").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="💎 VIP Narxlari",
            callback_data=AdminCb(owner_id=owner_id, section="settings", action="vip_menu").pack(),
        ),
        InlineKeyboardButton(
            text="👋 Kirish matni",
            callback_data=AdminCb(owner_id=owner_id, section="settings", action="welcome_edit").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=("🔴 Texnik rejim: YOQILGAN" if maintenance_on else "🟢 Texnik rejim: O‘CHIQ"),
            callback_data=AdminCb(owner_id=owner_id, section="settings", action="maint_tog").pack(),
        ),
    )
    mode_labels = {
        RankingMode.WEIGHTED.value: "⚖️ Rank vazni bo‘yicha",
        RankingMode.UNIQUE_FIRST.value: "✨ Noyoblar birinchi",
        RankingMode.TOTAL_CARDS.value: "🎴 Jami kartalar",
    }
    for mode in (RankingMode.WEIGHTED.value, RankingMode.UNIQUE_FIRST.value, RankingMode.TOTAL_CARDS.value):
        mark = "✅ " if mode == ranking_mode else ""
        builder.row(
            InlineKeyboardButton(
                text=f"{mark}🏆 Reyting: {mode_labels.get(mode, mode)}",
                callback_data=AdminCb(owner_id=owner_id, section="settings", action="set_rank", extra=mode).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=AdminCb(owner_id=owner_id, section="home").pack()),
        InlineKeyboardButton(text="🏠 BOSH MENU", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )
    return builder.as_markup()


def _cards_footer(builder: InlineKeyboardBuilder, owner_id: int, back_cb: str) -> None:
    builder.row(
        InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=back_cb),
        InlineKeyboardButton(text="🏠 BOSH MENU", callback_data=NavCb(owner_id=owner_id, target="home").pack()),
    )


def admin_stars_kb(owner_id: int, packages: List[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for p in packages:
        builder.row(
            InlineKeyboardButton(
                text=f"✏️ {p['stars']:,} ⭐ → {p['coins']:,} 🪙",
                callback_data=AdminCb(owner_id=owner_id, section="stars", action="edit", extra=str(p["id"])).pack(),
            ),
            InlineKeyboardButton(
                text="🗑",
                callback_data=AdminCb(owner_id=owner_id, section="stars", action="del", extra=str(p["id"])).pack(),
            ),
        )
    builder.row(
        InlineKeyboardButton(
            text="➕ YANGI TARIF QO‘SHISH",
            callback_data=AdminCb(owner_id=owner_id, section="stars", action="add").pack(),
        )
    )
    _cards_footer(builder, owner_id, AdminCb(owner_id=owner_id, section="home").pack())
    return builder.as_markup()


def admin_stars_confirm_kb(owner_id: int, package_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ HA, O‘CHIRISH",
            callback_data=AdminCb(owner_id=owner_id, section="stars", action="del_ok", extra=package_id).pack(),
        ),
        InlineKeyboardButton(
            text="❌ BEKOR",
            callback_data=AdminCb(owner_id=owner_id, section="stars", action="list").pack(),
        ),
    )
    return builder.as_markup()


def admin_stars_back_kb(owner_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="⬅️ TARIFLARGA QAYTISH",
            callback_data=AdminCb(owner_id=owner_id, section="stars", action="list").pack(),
        )
    )
    return builder.as_markup()
