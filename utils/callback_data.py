from aiogram.filters.callback_data import CallbackData


class NavCb(CallbackData, prefix="nav"):
    owner_id: int
    target: str


class DropCb(CallbackData, prefix="drop"):
    action: str
    drop_id: int


class EvtCb(CallbackData, prefix="evt"):
    """Ommaviy event tugmalari (guruh e'lonlarida hamma bosa oladi)."""

    action: str
    event_id: int


class GiveCb(CallbackData, prefix="gv"):
    owner_id: int
    action: str
    user_card_id: int = 0


class CollectionCb(CallbackData, prefix="col"):
    owner_id: int
    action: str
    filter_val: str = "all"
    sort_by: str = "newest"
    page: int = 1
    item_id: int = 0


class MarketCb(CallbackData, prefix="mkt"):
    owner_id: int
    action: str
    listing_id: int = 0
    page: int = 1


class SearchCb(CallbackData, prefix="srch"):
    owner_id: int
    action: str
    mode: str = "character"
    query: str = ""
    page: int = 1
    user_card_id: int = 0


class OfferCb(CallbackData, prefix="ofr"):
    owner_id: int
    action: str
    offer_id: int = 0
    user_card_id: int = 0
    page: int = 1


class TransferCb(CallbackData, prefix="trf"):
    owner_id: int
    action: str
    transfer_id: int


class RankingCb(CallbackData, prefix="rnk"):
    owner_id: int
    scope: str = "global"  # global, group, groups, coins, xp, quiz, games, trades, auction, refs, season, activity, rare, mythic, divine
    period: str = "all"    # all, weekly, monthly


class QuestCb(CallbackData, prefix="qst"):
    owner_id: int
    action: str


class StarsCb(CallbackData, prefix="str"):
    owner_id: int
    action: str
    package_id: str = ""


class AdminCb(CallbackData, prefix="adm"):
    owner_id: int
    section: str
    action: str = "view"
    target_id: int = 0
    extra: str = ""
    page: int = 1


# ---------------- V2 Compact CallbackData classes (har biri <= 64 bytes) ----------------

class AdmV2Cb(CallbackData, prefix="av2"):
    owner_id: int
    sec: str
    act: str = "v"
    tid: int = 0
    ext: str = ""
    p: int = 1


class QuizCb(CallbackData, prefix="qz"):
    owner_id: int
    act: str = "v"  # v, play, ans, hist
    qid: int = 0
    opt: int = 0


class GameCb(CallbackData, prefix="gm"):
    owner_id: int
    act: str = "v"  # v, dice, pick, flip, flip_go, lucky, lucky_pick, slots, slots_go, mines, mines_go, mines_open, mines_cash, wheel, wheel_go
    val: int = 0
    extra: str = ""


class AucCb(CallbackData, prefix="auc"):
    owner_id: int
    act: str = "v"  # v, det, bid, custom, mine, new, uc_new, dur, cancel_u
    aid: int = 0
    amt: int = 0
    p: int = 1


class WishCb(CallbackData, prefix="wsh"):
    owner_id: int
    act: str = "v"  # v, add, del, top
    cid: int = 0
    p: int = 1


class VipCb(CallbackData, prefix="vip"):
    owner_id: int
    act: str = "v"  # v, buy_stars, buy_coins
    tier: str = ""


class AchCb(CallbackData, prefix="ach"):
    owner_id: int
    act: str = "v"  # v, frames, bgs, badges, titles, equip, season
    code: str = ""
    p: int = 1


class SupCb(CallbackData, prefix="sup"):
    owner_id: int
    act: str = "v"  # v, new, view, reply, close, check_sub
    tid: int = 0
    p: int = 1


class TradeCb(CallbackData, prefix="trd"):
    owner_id: int
    act: str = "v"  # v, pick, accept, reject, cancel
    tid: int = 0
    ucid: int = 0
    p: int = 1


class NotifCb(CallbackData, prefix="ntf"):
    owner_id: int
    act: str = "v"  # v, read_all, read
    nid: int = 0
    p: int = 1


class PackCb(CallbackData, prefix="pck"):
    owner_id: int
    act: str = "v"  # v, open
    tier: str = ""
