import math
from typing import List, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from models.card import Card
from models.group import Group
from models.schedule import DropSchedule
from models.user import User
from utils.callback_data import AdminCb, NavCb
from utils.constants import RankingMode, get_rarity_meta, list_rarities


def admin_home_kb(owner_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🎴 CARD BO‘LIMI",
            callback_data=AdminCb(owner_id=owner_id, section="cards", action="menu").pack(),
        ),
        InlineKeyboardButton(
            text="👤 USERLAR BO‘LIMI",
            callback_data=AdminCb(owner_id=owner_id, section="users", action="menu").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="📅 DROP JADVALI",
            callback_data=AdminCb(owner_id=owner_id, section="schedule", action="overview").pack(),
        ),
        InlineKeyboardButton(
            text="⭐ PRIORITY CARD",
            callback_data=AdminCb(owner_id=owner_id, section="priority", action="view").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="⚙️ GURUH SOZLAMALARI",
            callback_data=AdminCb(owner_id=owner_id, section="groups", action="list").pack(),
        ),
        InlineKeyboardButton(
            text="🎯 EVENTLAR",
            callback_data=AdminCb(owner_id=owner_id, section="events", action="list").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="📊 STATISTIKA",
            callback_data=AdminCb(owner_id=owner_id, section="stats", action="view").pack(),
        ),
        InlineKeyboardButton(
            text="🔧 BOT SOZLAMALARI",
            callback_data=AdminCb(owner_id=owner_id, section="settings", action="view").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="⭐ STARS TARIFLAR",
            callback_data=AdminCb(owner_id=owner_id, section="stars", action="list").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="📢 BROADCAST",
            callback_data=AdminCb(owner_id=owner_id, section="broadcast", action="prompt").pack(),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def admin_cards_menu_kb(owner_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="➕ Karta qo‘shish",
            callback_data=AdminCb(owner_id=owner_id, section="cards", action="add_start").pack(),
        ),
        InlineKeyboardButton(
            text="🗑 Karta o‘chirish",
            callback_data=AdminCb(owner_id=owner_id, section="cards", action="del_list", page=1).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="📋 Kartalar ro‘yxati",
            callback_data=AdminCb(owner_id=owner_id, section="cards", action="list", extra="all", page=1).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=AdminCb(owner_id=owner_id, section="home").pack(),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def admin_cards_list_kb(
    owner_id: int,
    cards: List[Card],
    filter_mode: str,
    page: int,
    total: int,
    page_size: int = 5,
    mode: str = "list",  # list | del_list
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if mode == "list":
        builder.row(
            InlineKeyboardButton(
                text="Barchasi",
                callback_data=AdminCb(owner_id=owner_id, section="cards", action="list", extra="all", page=1).pack(),
            ),
            InlineKeyboardButton(
                text="Mavjud",
                callback_data=AdminCb(owner_id=owner_id, section="cards", action="list", extra="available", page=1).pack(),
            ),
            InlineKeyboardButton(
                text="O‘chirilgan",
                callback_data=AdminCb(owner_id=owner_id, section="cards", action="list", extra="disabled", page=1).pack(),
            ),
        )

    for c in cards:
        meta = get_rarity_meta(c.rarity)
        if mode == "del_list":
            btn_text = f"🗑 {meta.emoji} {c.character_name} ({c.card_code})"
            cb = AdminCb(
                owner_id=owner_id,
                section="cards",
                action="delete_confirm",
                target_id=c.id,
                page=page,
            ).pack()
        else:
            st = "🟢" if c.is_available else "🔴"
            btn_text = f"{st} {meta.emoji} {c.character_name} ({c.card_code})"
            cb = AdminCb(
                owner_id=owner_id,
                section="cards",
                action="detail",
                target_id=c.id,
                page=page,
            ).pack()
        builder.row(InlineKeyboardButton(text=btn_text[:60], callback_data=cb))

    total_pages = max(1, math.ceil(total / page_size))
    nav = []
    if page > 1:
        nav.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=AdminCb(
                    owner_id=owner_id, section="cards", action=mode, extra=filter_mode, page=page - 1
                ).pack(),
            )
        )
    nav.append(
        InlineKeyboardButton(
            text=f"{page}/{total_pages}",
            callback_data=AdminCb(
                owner_id=owner_id, section="cards", action=mode, extra=filter_mode, page=page
            ).pack(),
        )
    )
    if page < total_pages:
        nav.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=AdminCb(
                    owner_id=owner_id, section="cards", action=mode, extra=filter_mode, page=page + 1
                ).pack(),
            )
        )
    builder.row(*nav)
    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=AdminCb(owner_id=owner_id, section="cards", action="menu").pack(),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def admin_card_detail_kb(
    owner_id: int,
    card: Card,
    page: int = 1,
    not_dropped_yet: bool = False,
    char_rep_id: int = 0,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    drop_label = "📤 Guruhga tashlash (Hali tashlanmagan)" if not_dropped_yet else "📤 Guruhga tashlash"
    builder.row(
        InlineKeyboardButton(
            text=drop_label,
            callback_data=AdminCb(
                owner_id=owner_id, section="cards", action="drop_pick_grp", target_id=card.id, page=page
            ).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="⭐ KEYINGI DROP QILISH",
            callback_data=AdminCb(
                owner_id=owner_id, section="priority", action="set", target_id=card.id
            ).pack(),
        )
    )
    toggle_txt = "🔴 Disable qilish" if card.is_available else "🟢 Enable qilish"
    builder.row(
        InlineKeyboardButton(
            text=toggle_txt,
            callback_data=AdminCb(
                owner_id=owner_id, section="cards", action="toggle", target_id=card.id, page=page
            ).pack(),
        ),
        InlineKeyboardButton(
            text="🖼 Rasmni almashtirish",
            callback_data=AdminCb(
                owner_id=owner_id, section="cards", action="edit_image", target_id=card.id
            ).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="💰 Value o‘zgartirish",
            callback_data=AdminCb(
                owner_id=owner_id, section="cards", action="edit_value", target_id=card.id
            ).pack(),
        ),
        InlineKeyboardButton(
            text="🗑 O‘chirish",
            callback_data=AdminCb(
                owner_id=owner_id, section="cards", action="delete_confirm", target_id=card.id
            ).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🔁 Tashlash sonini o‘zgartirish",
            callback_data=AdminCb(
                owner_id=owner_id, section="cards", action="edit_limit", target_id=card.id
            ).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=(
                AdminCb(
                    owner_id=owner_id,
                    section="cards",
                    action="char_open",
                    target_id=char_rep_id,
                    extra="all-1-1",
                    page=1,
                ).pack()
                if char_rep_id
                else AdminCb(owner_id=owner_id, section="cards", action="list", page=page).pack()
            ),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
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
            callback_data=AdminCb(owner_id=owner_id, section="cards", action="detail", target_id=card_id).pack(),
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
            text="🚫 Ban qilish",
            callback_data=AdminCb(owner_id=owner_id, section="users", action="list", extra="active", page=1).pack(),
        ),
        InlineKeyboardButton(
            text="🔓 Bandan chiqarish",
            callback_data=AdminCb(owner_id=owner_id, section="users", action="list", extra="banned", page=1).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="👥 Userlar ro‘yxati",
            callback_data=AdminCb(owner_id=owner_id, section="users", action="list", extra="all", page=1).pack(),
        ),
        InlineKeyboardButton(
            text="🔍 User qidirish",
            callback_data=AdminCb(owner_id=owner_id, section="users", action="search_prompt").pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=AdminCb(owner_id=owner_id, section="home").pack(),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def admin_users_list_kb(
    owner_id: int,
    users: List[User],
    filter_mode: str,
    page: int,
    total: int,
    page_size: int = 6,
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
            btn_text = f"{st} {u.display_name} | 🪙{u.coins:,} | 🎴{u.total_cards}"
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
                callback_data=AdminCb(
                    owner_id=owner_id, section="users", action="list", extra=filter_mode, page=page - 1
                ).pack(),
            )
        )
    nav.append(
        InlineKeyboardButton(
            text=f"{page}/{total_pages}",
            callback_data=AdminCb(
                owner_id=owner_id, section="users", action="list", extra=filter_mode, page=page
            ).pack(),
        )
    )
    if page < total_pages:
        nav.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=AdminCb(
                    owner_id=owner_id, section="users", action="list", extra=filter_mode, page=page + 1
                ).pack(),
            )
        )
    builder.row(*nav)
    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=AdminCb(owner_id=owner_id, section="users", action="menu").pack(),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def admin_user_detail_kb(owner_id: int, target_user: User) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="➕ Coin berish",
            callback_data=AdminCb(
                owner_id=owner_id, section="users", action="add_coins", target_id=target_user.id
            ).pack(),
        ),
        InlineKeyboardButton(
            text="➖ Coin olish",
            callback_data=AdminCb(
                owner_id=owner_id, section="users", action="sub_coins", target_id=target_user.id
            ).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="➕ Card berish",
            callback_data=AdminCb(
                owner_id=owner_id, section="users", action="give_pick", target_id=target_user.id, page=1
            ).pack(),
        ),
        InlineKeyboardButton(
            text="➖ Card olish",
            callback_data=AdminCb(
                owner_id=owner_id, section="users", action="rem_pick", target_id=target_user.id, page=1
            ).pack(),
        ),
    )
    ban_btn = (
        InlineKeyboardButton(
            text="🔓 Bandan chiqarish",
            callback_data=AdminCb(
                owner_id=owner_id, section="ban", action="unban", target_id=target_user.id
            ).pack(),
        )
        if target_user.is_banned
        else InlineKeyboardButton(
            text="🚫 Ban qilish",
            callback_data=AdminCb(
                owner_id=owner_id, section="ban", action="ban", target_id=target_user.id
            ).pack(),
        )
    )
    builder.row(ban_btn)
    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=AdminCb(owner_id=owner_id, section="users", action="list", extra="all", page=1).pack(),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def admin_pick_card_for_user_kb(
    owner_id: int,
    target_user_id: int,
    cards: List[Card],
    page: int,
    total: int,
    page_size: int = 6,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for c in cards:
        meta = get_rarity_meta(c.rarity)
        builder.row(
            InlineKeyboardButton(
                text=f"➕ {meta.emoji} {c.character_name} ({c.card_code})",
                callback_data=AdminCb(
                    owner_id=owner_id,
                    section="users",
                    action="give_exec",
                    target_id=target_user_id,
                    extra=str(c.id),
                ).pack(),
            )
        )
    total_pages = max(1, math.ceil(total / page_size))
    nav = []
    if page > 1:
        nav.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=AdminCb(
                    owner_id=owner_id, section="users", action="give_pick", target_id=target_user_id, page=page - 1
                ).pack(),
            )
        )
    nav.append(
        InlineKeyboardButton(
            text=f"{page}/{total_pages}",
            callback_data=AdminCb(
                owner_id=owner_id, section="users", action="give_pick", target_id=target_user_id, page=page
            ).pack(),
        )
    )
    if page < total_pages:
        nav.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=AdminCb(
                    owner_id=owner_id, section="users", action="give_pick", target_id=target_user_id, page=page + 1
                ).pack(),
            )
        )
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
                text=f"➖ {meta.emoji} {card.character_name} × {qty}",
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


def admin_groups_list_kb(owner_id: int, groups: List[Group], section: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for g in groups[:15]:
        st = "🟢" if g.drop_enabled else "🔴"
        builder.row(
            InlineKeyboardButton(
                text=f"{st} {g.title[:28]} ({g.daily_drop_count}/kun)",
                callback_data=AdminCb(
                    owner_id=owner_id,
                    section=section,
                    action="group_view",
                    target_id=g.id,
                ).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=AdminCb(owner_id=owner_id, section="home").pack(),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
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
            callback_data=AdminCb(
                owner_id=owner_id, section="schedule", action="regen", target_id=group_id
            ).pack(),
        )
    )
    for sc in schedules:
        if sc.status == "pending":
            builder.row(
                InlineKeyboardButton(
                    text=f"⭐ Slot #{sc.id} ga Priority karta qo‘yish",
                    callback_data=AdminCb(
                        owner_id=owner_id,
                        section="schedule",
                        action="slot_prio",
                        target_id=sc.id,
                    ).pack(),
                )
            )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=AdminCb(owner_id=owner_id, section="schedule", action="overview").pack(),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def admin_group_settings_kb(owner_id: int, group: Group) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    toggle_text = "🔴 Dropni o‘chirish" if group.drop_enabled else "🟢 Dropni yoqish"
    builder.row(
        InlineKeyboardButton(
            text=toggle_text,
            callback_data=AdminCb(
                owner_id=owner_id, section="groups", action="toggle_drop", target_id=group.id
            ).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=("✅ 🤖 Avto (a’zolar soniga qarab)" if group.auto_drop_count else "🤖 Avto rejimni yoqish"),
            callback_data=AdminCb(
                owner_id=owner_id, section="groups", action="toggle_auto", target_id=group.id
            ).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="➖ Kunlik drop (-1)",
            callback_data=AdminCb(
                owner_id=owner_id, section="groups", action="drop_dec", target_id=group.id
            ).pack(),
        ),
        InlineKeyboardButton(
            text="➕ Kunlik drop (+1)",
            callback_data=AdminCb(
                owner_id=owner_id, section="groups", action="drop_inc", target_id=group.id
            ).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="⚡ HOZIR TEST DROP TASHLASH",
            callback_data=AdminCb(
                owner_id=owner_id, section="groups", action="drop_now", target_id=group.id
            ).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=AdminCb(owner_id=owner_id, section="groups", action="list").pack(),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


def admin_settings_kb(owner_id: int, ranking_mode: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🎖 RANK NARXI VA TASHLASH SONI",
            callback_data=AdminCb(owner_id=owner_id, section="settings", action="rank_menu").pack(),
        )
    )
    for r in list_rarities():
        builder.row(
            InlineKeyboardButton(
                text=f"🪙 {r} reward o‘zgartirish",
                callback_data=AdminCb(
                    owner_id=owner_id, section="settings", action="edit_reward", extra=r
                ).pack(),
            )
        )
    for mode in (RankingMode.WEIGHTED.value, RankingMode.UNIQUE_FIRST.value, RankingMode.TOTAL_CARDS.value):
        mark = "✅ " if mode == ranking_mode else ""
        builder.row(
            InlineKeyboardButton(
                text=f"{mark}🏆 Ranking: {mode}",
                callback_data=AdminCb(
                    owner_id=owner_id, section="settings", action="set_rank", extra=mode
                ).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ ORQAGA",
            callback_data=AdminCb(owner_id=owner_id, section="home").pack(),
        ),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )
    return builder.as_markup()


# ---------------------------------------------------------------------------
# Guruhlangan karta ro'yxati: ANIME -> PERSONAJ -> KARTALAR
# extra formati (':' ishlatilmaydi): "<filtr>-<sahifa>[-<sahifa>]"
# ---------------------------------------------------------------------------
def _cards_footer(builder: InlineKeyboardBuilder, owner_id: int, back_cb: str) -> None:
    builder.row(
        InlineKeyboardButton(text="⬅️ ORQAGA", callback_data=back_cb),
        InlineKeyboardButton(
            text="🏠 BOSH MENU",
            callback_data=NavCb(owner_id=owner_id, target="home").pack(),
        ),
    )


def _cards_pager(builder: InlineKeyboardBuilder, page: int, total_pages: int, make_cb) -> None:
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=make_cb(page - 1)))
    nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data=make_cb(page)))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=make_cb(page + 1)))
    builder.row(*nav)


def admin_anime_sections_kb(
    owner_id: int,
    items: List[dict],
    filter_mode: str,
    page: int,
    total: int,
    page_size: int = 6,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        *[
            InlineKeyboardButton(
                text=("✅ " if filter_mode == mode else "") + label,
                callback_data=AdminCb(owner_id=owner_id, section="cards", action="list", extra=mode, page=1).pack(),
            )
            for mode, label in (("all", "Barchasi"), ("available", "Mavjud"), ("disabled", "O‘chirilgan"))
        ]
    )
    for it in items:
        builder.row(
            InlineKeyboardButton(
                text=f"📺 {it['name'][:24]} · {it['characters']} personaj · {it['cards']} karta"[:60],
                callback_data=AdminCb(
                    owner_id=owner_id,
                    section="cards",
                    action="anime_open",
                    target_id=it["rep_id"],
                    extra=f"{filter_mode}-{page}",
                    page=1,
                ).pack(),
            )
        )
    _cards_pager(
        builder,
        page,
        max(1, math.ceil(total / page_size)),
        lambda p: AdminCb(owner_id=owner_id, section="cards", action="list", extra=filter_mode, page=p).pack(),
    )
    _cards_footer(builder, owner_id, AdminCb(owner_id=owner_id, section="cards", action="menu").pack())
    return builder.as_markup()


def admin_anime_characters_kb(
    owner_id: int,
    anime_rep_id: int,
    items: List[dict],
    filter_mode: str,
    anime_page: int,
    page: int,
    total: int,
    page_size: int = 6,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for it in items:
        builder.row(
            InlineKeyboardButton(
                text=f"🎴 {it['name'][:22]} · {it['cards']} karta · {it['copies']} nusxa · {it['owners']} ega"[:60],
                callback_data=AdminCb(
                    owner_id=owner_id,
                    section="cards",
                    action="char_open",
                    target_id=it["rep_id"],
                    extra=f"{filter_mode}-{page}-{anime_page}",
                    page=1,
                ).pack(),
            )
        )
    _cards_pager(
        builder,
        page,
        max(1, math.ceil(total / page_size)),
        lambda p: AdminCb(
            owner_id=owner_id,
            section="cards",
            action="anime_open",
            target_id=anime_rep_id,
            extra=f"{filter_mode}-{anime_page}",
            page=p,
        ).pack(),
    )
    _cards_footer(
        builder,
        owner_id,
        AdminCb(owner_id=owner_id, section="cards", action="list", extra=filter_mode, page=anime_page).pack(),
    )
    return builder.as_markup()


def admin_character_cards_kb(
    owner_id: int,
    cards: List[Tuple[Card, int, int]],
    anime_rep_id: int,
    filter_mode: str,
    char_page: int,
    anime_page: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for card, copies, _owners in cards:
        meta = get_rarity_meta(card.rarity)
        st = "🟢" if card.is_available else "🔴"
        builder.row(
            InlineKeyboardButton(
                text=f"{st} {meta.emoji} {card.rarity} · {card.card_code} · {copies} nusxa"[:60],
                callback_data=AdminCb(
                    owner_id=owner_id, section="cards", action="detail", target_id=card.id, page=1
                ).pack(),
            )
        )
    _cards_footer(
        builder,
        owner_id,
        AdminCb(
            owner_id=owner_id,
            section="cards",
            action="anime_open",
            target_id=anime_rep_id,
            extra=f"{filter_mode}-{anime_page}",
            page=char_page,
        ).pack(),
    )
    return builder.as_markup()


# ---------------------------------------------------------------------------
# Telegram Stars tariflari boshqaruvi
# ---------------------------------------------------------------------------
def admin_stars_kb(owner_id: int, packages: List[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for p in packages:
        builder.row(
            InlineKeyboardButton(
                text=f"✏️ {p['stars']:,} ⭐ → {p['coins']:,} 🪙",
                callback_data=AdminCb(
                    owner_id=owner_id, section="stars", action="edit", extra=str(p["id"])
                ).pack(),
            ),
            InlineKeyboardButton(
                text="🗑",
                callback_data=AdminCb(
                    owner_id=owner_id, section="stars", action="del", extra=str(p["id"])
                ).pack(),
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
