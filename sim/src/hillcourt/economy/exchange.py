"""Локальный обмен и подмога: перевод материи между стоками на одной клетке.

Обмен ничего не создаёт: это `Ledger.transfer` между дворами. Свинья не берётся
из воздуха — её покупают за серебро у двора с приплодом; зерно выменивают на
серебро или получают подмогой от более сытого соседа.
"""

from __future__ import annotations

from ..engine.hexgrid import neighbor_ids
from ..engine.manor import manor_of_household, manor_stock
from ..ontology import Household, SimDate, Stock
from ..world import World
from .needs import food_months

EPSILON = 1e-9
PIG_PRICE_SILVER = 3.0
GRAIN_PRICE_SILVER = 0.5
TRADE_GRAIN_MAX = 4.0
# Соседский торг (допуск, ADR 0057): носитель — сам двор (пеший шаг в соседнюю
# клетку, Pack не вводится). Чужая сделка (покупатель и продавец на разных
# клетках кластера): те же фикс-цены; цена носителя — сбор PORTER_FEE там, где
# покупатель сыт (свиньи/серебро, брёвна/скот/зерно), а на голодном зерне —
# укус носильщика PORTER_BITE с богатой стороны (продавец даёт сверх, укус —
# в отход, покупатель платит чистую цену: голодного сбором не облагать);
# зерно через клетку — не больше NEIGHBOR_GRAIN_MAX (кап против
# перераспределения голода). Дар — без цены (неимущим).
PORTER_FEE = 0.5
PORTER_BITE = 0.25
NEIGHBOR_GRAIN_MAX = 2.0
WASTE_STOCK_ID = "sink:waste"
# Цены покупки (зерно за единицу): бревно — стройка и топливо; скот — тягло.
# Цены фиксированы каталогом смысла (труд × ставка найма), а не рынком.
LOG_PRICE_GRAIN = 0.5
TRADE_LOG_MAX = 4.0
LIVESTOCK_PRICE_GRAIN = {
    "ox_m": 8.0, "ox_f": 8.0,
    "donkey_m": 5.0, "donkey_f": 5.0,
    "horse_m": 12.0, "horse_f": 12.0,
    "ox_calf": 4.0, "donkey_foal": 2.5, "horse_foal": 6.0,
}
YOUNG_GOODS = frozenset({"ox_calf", "donkey_foal", "horse_foal"})


def hidden_stock_id(household: Household) -> str:
    """id потайного стока двора (для действия hide_stores)."""
    return f"household:{household.id}:hidden"


def _hidden_stock(world: World, household: Household) -> Stock:
    sid = hidden_stock_id(household)
    if sid not in world.stocks:
        world.stocks[sid] = Stock(
            id=sid, owner_kind="household", owner_id=household.id, amounts={}
        )
    return world.stocks[sid]


def apply_relief(world: World, date: SimDate) -> None:
    """Просящие дворы получают зерно из амбара СВОЕГО манора.

    Источник подмоги решает книга двора (`Household.manor_id`), а не замок:
    корневой двор кормится из замка (его книга — корневой манор), двор тэна —
    из `manor:<id>`. Двор без книги (соляной держатель) подмоги не получает:
    он равный, вне тяглой книги, и замок ему не стол. Материя — перевод.
    """
    needs = world.needs
    if needs is None:
        return
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None:
            continue
        if "request_relief" not in (household.main_action, household.minor_action):
            continue
        book = manor_of_household(world, hid)
        if book is None:
            continue
        source = manor_stock(world, book)
        if source is None:
            continue
        if source.amounts.get("grain", 0.0) < needs.relief_min_court_grain:
            continue
        amount = min(needs.relief_amount, source.amounts.get("grain", 0.0))
        if amount <= EPSILON:
            continue
        world.ledger.transfer(
            source, world.get_stock(household.stock_id), "grain", amount,
            "relief", date,
        )
        world.bump("relief_given", amount)


def apply_hide_stores(world: World, date: SimDate) -> None:
    """Прятать часть зерна от волков в потайной сток."""
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None or household.minor_action != "hide_stores":
            continue
        stock = world.get_stock(household.stock_id)
        grain = stock.amounts.get("grain", 0.0)
        hide = grain * 0.5
        if hide <= EPSILON:
            continue
        world.ledger.transfer(stock, _hidden_stock(world, household), "grain", hide,
                              "hide", date)


def _by_tile(world: World) -> dict[str, list[Household]]:
    groups: dict[str, list[Household]] = {}
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None:
            continue
        groups.setdefault(household.current_tile_id, []).append(household)
    return groups


def _tile_id(x: int, y: int) -> str:
    return f"t_{x:02d}_{y:02d}"


def _neighbor_clusters(world: World) -> dict[str, list[Household]]:
    """Кластеры соседского торга: разбиение (клетка + ортогональные соседи).

    Жадное разбиение по id: каждая жилая клетка ровно в одном кластере
    (двойных сделок нет). Порядок детерминирован. Дальний торг — только
    Pack-обозом (не этот проект).
    """
    base = _by_tile(world)
    ids = set(base)
    adjacent: dict[str, set[str]] = {}
    for tid in ids:
        tile = world.tiles.get(tid)
        # Соседство — общий хелпер гекс-сетки (ADR 0071), шесть направлений.
        adjacent[tid] = set(neighbor_ids(world, tile)) & ids if tile is not None else set()
    done: set[str] = set()
    out: dict[str, list[Household]] = {}
    for tid in sorted(base):
        if tid in done:
            continue
        members: list[Household] = []
        for cell in sorted({tid} | adjacent[tid]):
            members.extend(base[cell])
            done.add(cell)
        out[tid] = members
    return out


def _cross_ok(buyer: Household, seller: Household, cross: bool) -> bool:
    """Годится ли продавец: чужой двор, а при `cross` — ещё и чужая клетка."""
    if seller.id == buyer.id:
        return False
    if cross and seller.current_tile_id == buyer.current_tile_id:
        return False
    return True


def _trade_pigs(
    world: World, members: list[Household], date: SimDate, cross: bool = False
) -> None:
    fee = PORTER_FEE if cross else 0.0
    for buyer in members:
        bstock = world.get_stock(buyer.stock_id)
        if bstock.amounts.get("pig", 0.0) > EPSILON:
            continue
        if bstock.amounts.get("silver", 0.0) < PIG_PRICE_SILVER + fee:
            continue
        if food_months(world, buyer) < 1.0:
            continue
        seller = next(
            (
                s
                for s in members
                if _cross_ok(buyer, s, cross)
                and world.get_stock(s.stock_id).amounts.get("pig", 0.0) >= 2.0
            ),
            None,
        )
        if seller is None:
            continue
        sstock = world.get_stock(seller.stock_id)
        world.ledger.transfer(
            bstock, sstock, "silver", PIG_PRICE_SILVER + fee, "buy_pig", date
        )
        world.ledger.transfer(sstock, bstock, "pig", 1.0, "buy_pig", date)
        world.bump("pigs_bought")


def _trade_grain(
    world: World, members: list[Household], date: SimDate, cross: bool = False
) -> None:
    for buyer in members:
        if food_months(world, buyer) >= 0.5:
            continue
        bstock = world.get_stock(buyer.stock_id)
        seller = next(
            (
                s
                for s in members
                if _cross_ok(buyer, s, cross) and food_months(world, s) > 2.5
            ),
            None,
        )
        if seller is None:
            continue
        sstock = world.get_stock(seller.stock_id)
        want = NEIGHBOR_GRAIN_MAX if cross else TRADE_GRAIN_MAX
        affordable = bstock.amounts.get("silver", 0.0) / GRAIN_PRICE_SILVER
        amount = min(want, affordable, sstock.amounts.get("grain", 0.0) * 0.5)
        if amount > EPSILON:
            price = amount * GRAIN_PRICE_SILVER
            bite = min(PORTER_BITE, sstock.amounts.get("grain", 0.0) - amount) if cross else 0.0
            world.ledger.transfer(bstock, sstock, "silver", price, "buy_grain", date)
            world.ledger.transfer(sstock, bstock, "grain", amount, "buy_grain", date)
            if bite > EPSILON:
                world.ledger.transfer(
                    sstock, world.get_stock(WASTE_STOCK_ID), "grain", bite,
                    "porter_loss", date,
                )
            world.bump("grain_bought", amount)
        elif sstock.amounts.get("grain", 0.0) > 3.0 and world.rng.economy.random() < 0.3:
            world.ledger.transfer(sstock, bstock, "grain", 1.0, "neighbour_gift", date)
            world.bump("grain_gifted")


def _trade_logs(
    world: World, members: list[Household], date: SimDate, cross: bool = False
) -> None:
    """Брёвна за зерно: стройка и топливо без рубки своим трудом."""
    fee = PORTER_FEE if cross else 0.0
    for buyer in members:
        bstock = world.get_stock(buyer.stock_id)
        if bstock.amounts.get("log", 0.0) >= 1.0:
            continue
        if food_months(world, buyer) < 1.0:
            continue
        seller = next(
            (
                s
                for s in members
                if _cross_ok(buyer, s, cross)
                and world.get_stock(s.stock_id).amounts.get("log", 0.0) >= 4.0
            ),
            None,
        )
        if seller is None:
            continue
        sstock = world.get_stock(seller.stock_id)
        want = min(
            TRADE_LOG_MAX,
            max(0.0, bstock.amounts.get("grain", 0.0) - fee) / LOG_PRICE_GRAIN,
            sstock.amounts.get("log", 0.0),
        )
        if want <= EPSILON:
            continue
        world.ledger.transfer(
            bstock, sstock, "grain", want * LOG_PRICE_GRAIN + fee, "buy_log", date
        )
        world.ledger.transfer(sstock, bstock, "log", want, "buy_log", date)
        world.bump("logs_bought", want)


def _mate_of(good: str) -> str | None:
    """Парный пол для проверки, что продажа не разбивает пару."""
    if good.endswith("_m"):
        return good[:-2] + "_f"
    if good.endswith("_f"):
        return good[:-2] + "_m"
    return None


def _keeps_pair(world: World, seller: Household, good: str) -> bool:
    """Можно ли продать голову, не разбив пару и не продав последнее рабочее.

    Разрешено, если после продажи останется пара (самец+самка) либо хотя бы
    две головы того же пола (будущая пара). Последнее и служебное не трогаем.
    """
    stock = world.get_stock(seller.stock_id)
    have = stock.amounts.get(good, 0.0)
    if have < 2.0 - EPSILON:
        return False
    mate = _mate_of(good)
    if mate is not None and stock.amounts.get(mate, 0.0) >= 1.0 - EPSILON:
        return True
    return have >= 3.0 - EPSILON


def _sellable(world: World, seller: Household, good: str) -> bool:
    """Можно ли продать голову: молодняк — излишек, взрослых — только сверх пары."""
    if good in YOUNG_GOODS:
        return world.get_stock(seller.stock_id).amounts.get(good, 0.0) >= 1.0 - EPSILON
    return _keeps_pair(world, seller, good)


def _trade_livestock(
    world: World, members: list[Household], date: SimDate, cross: bool = False
) -> None:
    """Скот за зерно: тягло покупается, а не появляется. Конь — только свободному."""
    from .livestock import can_hold_horse

    fee = PORTER_FEE if cross else 0.0
    for good, price in sorted(LIVESTOCK_PRICE_GRAIN.items()):
        for buyer in members:
            bstock = world.get_stock(buyer.stock_id)
            if bstock.amounts.get(good, 0.0) > EPSILON:
                continue
            if good.startswith("horse_") and not can_hold_horse(world, buyer):
                continue
            if food_months(world, buyer) < 1.0:
                continue
            if bstock.amounts.get("grain", 0.0) < price + fee:
                continue
            seller = next(
                (
                    s
                    for s in members
                    if _cross_ok(buyer, s, cross)
                    and _keeps_pair(world, s, good)
                ),
                None,
            )
            if seller is None:
                continue
            sstock = world.get_stock(seller.stock_id)
            world.ledger.transfer(bstock, sstock, "grain", price + fee, "buy_beast", date)
            world.ledger.transfer(sstock, bstock, good, 1.0, "buy_beast", date)
            world.bump("beasts_bought")


def local_exchange(world: World, date: SimDate) -> None:
    """Обмен на клетке и с соседями: покупатель и продавец — свои или рядом.

    Свиньи за серебро, зерно, брёвна и скот за зерно — сначала соклеточники
    (те же цены, без сбора), затем соседский кластер (те же цены + сбор
    носильщику, зерно капом; дар — без сбора). Дальний торг — только
    Pack-обозом. Межклеточный торг — допуск ADR 0057.
    """
    for _tile_id, members in sorted(_by_tile(world).items()):
        if len(members) < 2:
            continue
        _trade_pigs(world, members, date)
        _trade_grain(world, members, date)
        _trade_logs(world, members, date)
        _trade_livestock(world, members, date)
    for _cluster_id, members in sorted(_neighbor_clusters(world).items()):
        if len(members) < 2:
            continue
        _trade_pigs(world, members, date, cross=True)
        _trade_grain(world, members, date, cross=True)
        _trade_logs(world, members, date, cross=True)
        _trade_livestock(world, members, date, cross=True)
