from aiogram.filters.callback_data import CallbackData


class NavCb(CallbackData, prefix="nav"):
    owner_id: int
    target: str  # home, cards, daily, market, search, ranking, profile, stars, help


class DropCb(CallbackData, prefix="drop"):
    action: str  # claim
    drop_id: int


class EvtCb(CallbackData, prefix="evt"):
    """Ommaviy event tugmalari (guruh e'lonlarida hamma bosa oladi): owner_id yo'q."""

    action: str  # buy_coins, buy_stars
    event_id: int


class GiveCb(CallbackData, prefix="gv"):
    owner_id: int
    action: str  # pick, cancel
    user_card_id: int = 0


class CollectionCb(CallbackData, prefix="col"):
    owner_id: int
    action: str  # view, by_anime, by_rarity, char_list, card_detail, toggle_fav, list_prompt
    filter_val: str = "all"
    page: int = 1
    item_id: int = 0


class MarketCb(CallbackData, prefix="mkt"):
    owner_id: int
    action: str  # list, view, buy, cancel, my_listings
    listing_id: int = 0
    page: int = 1


class SearchCb(CallbackData, prefix="srch"):
    owner_id: int
    action: str  # menu, mode, results, inspect, random
    mode: str = "character"  # character, anime, rarity, card_id, random
    query: str = ""
    page: int = 1
    user_card_id: int = 0


class OfferCb(CallbackData, prefix="ofr"):
    owner_id: int
    action: str  # prompt, incoming, outgoing, accept, reject, cancel
    offer_id: int = 0
    user_card_id: int = 0
    page: int = 1


class TransferCb(CallbackData, prefix="trf"):
    owner_id: int
    action: str  # confirm, cancel
    transfer_id: int


class RankingCb(CallbackData, prefix="rnk"):
    owner_id: int
    scope: str  # global, group


class StarsCb(CallbackData, prefix="str"):
    owner_id: int
    action: str  # shop, buy
    package_id: str = ""


class AdminCb(CallbackData, prefix="adm"):
    owner_id: int
    section: str  # home, cards, users, coins, settings, stats, ranking, broadcast, ban, events, schedule, groups, priority
    action: str = "view"
    target_id: int = 0
    extra: str = ""
    page: int = 1
