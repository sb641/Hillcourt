"""Водный контур v1: ёмкость судна, фураж-ночёвка, портовый сбор (ADR 0075).

Закон владельца 0075 п.1–2, 5–6; числа порций —Economist:

- **Ёмкость:** груз `raft` 6.0 / `boat` 12.0, люди 2 / 4 взрослых. Люди в груз
  не входят и в ёмкость груза не считаются, но судно без людей не плывёт
  (плоту нужен кормчий, лодке — тем более): проверка людей обязательна, иначе
  «бесплатная» перевозка людей. Перебор — отказ.
- **Фураж:** порция на день пути (ADR 0075 п.6 + порция хозяина): нога 1.75,
  конь 2.75, обоз 4.5 (+ тягло 2.75). Счёт **от часов**: сутки = `HOURS_PER_DAY`
  (ADR 0078), день пути = `hours / 24`; переход через сутки (>24 ч) честно
  множит порцию. На воде фураж — за ночёвку: рейс ≤ 6 ч без фуража (течение и
  ветер уже съедены часами).
- **Портовый сбор:** 10 % перевозимого груза натурой при разгрузке в портовом
  гексе — `Ledger.transfer` в `Settlement.stores` (амбар поселения назначения;
  порт = поселение у воды, отдельного склада нет — 0075 п.4). Соль пошлину не
  платит (не еда). Серебра нет и не создаётся (И-1).

Барка/шхуна, вода как ресурс, колодцы — вне v1 (новые товары, И-7; запрещено
0075 п.7). Здесь только плот/лодка.
"""

from __future__ import annotations

from ..engine.terrain import HOURS_PER_DAY
from ..ontology import Pack, SimDate, Stock
from ..world import World

EPSILON = 1e-9
VESSEL_FOR_PROFILE = {"water_raft": "raft", "water_boat": "boat"}
CARGO_CAPACITY = {"raft": 6.0, "boat": 12.0}
PERSON_CAPACITY = {"raft": 2, "boat": 4}
PORTION_PER_DAY = {"foot": 1.75, "horse": 2.75, "caravan": 4.5, "draft": 2.75}
PORT_TOLL_SHARE = 0.10
NIGHT_FODDER_HOURS = 6.0
WITHOUT_VESSEL_FREE = 1.0
WASTE_STOCK_ID = "sink:waste"
EATEN_STOCK_ID = "sink:eaten"


def vessel_capacity(vessel_good: str) -> float:
    """Ёмкость груза судна по массе: `raft` 6.0, `boat` 12.0."""
    return float(CARGO_CAPACITY.get(vessel_good, 0.0))


def vessel_persons(vessel_good: str) -> int:
    """Сколько взрослых судно везёт: `raft` 2, `boat` 4."""
    return int(PERSON_CAPACITY.get(vessel_good, 0))


def water_capacity(world: World, household_id: str, profile_id: str) -> tuple[float, int]:
    """(груз, люди) по судам origin: берётся наибольшее доступное судно.

    Без судна: груз ≤ 1.0 (ночёвка/коромысло, ADR 0027) и 1 взрослый.
    """
    if profile_id not in VESSEL_FOR_PROFILE:
        raise ValueError(
            f"Профиль '{profile_id}' сухопутный: водная ёмкость только "
            f"{sorted(VESSEL_FOR_PROFILE)}"
        )
    vessel = VESSEL_FOR_PROFILE[profile_id]
    mass = 0.0
    household = world.households.get(household_id)
    if household is not None:
        stock = world.stocks.get(household.stock_id)
        if stock is not None:
            mass += float(stock.amounts.get(vessel, 0.0))
        settlement = world.settlements.get(household.settlement_id or "")
        if settlement is not None:
            stores = world.stocks.get(settlement.stores_stock_id)
            if stores is not None:
                mass += float(stores.amounts.get(vessel, 0.0))
    if mass < 1.0 - EPSILON:
        return WITHOUT_VESSEL_FREE, 1
    best_good, best_mass = None, 0.0
    for good in sorted(CARGO_CAPACITY):
        have = 0.0
        household = world.households.get(household_id)
        if household is not None:
            stock = world.stocks.get(household.stock_id)
            if stock is not None:
                have += float(stock.amounts.get(good, 0.0))
            settlement = world.settlements.get(household.settlement_id or "")
            if settlement is not None:
                stores = world.stocks.get(settlement.stores_stock_id)
                if stores is not None:
                    have += float(stores.amounts.get(good, 0.0))
        if have >= 1.0 - EPSILON and have > best_mass:
            best_good, best_mass = good, have
    if best_good is None:
        return WITHOUT_VESSEL_FREE, 1
    return vessel_capacity(best_good), vessel_persons(best_good)


def check_water_load(
    world: World, household_id: str, profile_id: str, cargo_kg: float, persons: int
) -> tuple[float, int]:
    """Проверить перевозку по воде; вернуть кап (груз, люди). Перебор — отказ."""
    cargo_cap, persons_cap = water_capacity(world, household_id, profile_id)
    if float(cargo_kg) > cargo_cap + EPSILON:
        raise ValueError(
            f"Перебор груза: {cargo_kg:.1f} > ёмкости {cargo_cap:.1f} "
            f"({profile_id})"
        )
    if int(persons) > persons_cap:
        raise ValueError(
            f"Перебор людей: {persons} > {persons_cap} ({profile_id})"
        )
    return cargo_cap, persons_cap


def travel_day_fraction(hours: float) -> float:
    """Сутки пути от часов (ADR 0078): доля суток, не месяц."""
    return max(0.0, float(hours)) / float(HOURS_PER_DAY)


def water_fodder(hours: float, draft: str = "none") -> float:
    """Фураж на воде — за ночёвку: рейс ≤ 6 ч без фуража (ADR 0075 п.6).

    Длинный переход: порция обоза 4.5/сутки + тягло 2.75/сутки, по честным
    суткам `hours / 24`.
    """
    if hours <= NIGHT_FODDER_HOURS:
        return 0.0
    days = travel_day_fraction(hours)
    portion = PORTION_PER_DAY["caravan"] * days
    if draft in ("donkey", "horse"):
        portion += PORTION_PER_DAY["draft"] * days
    return portion


def is_water_route(world: World, pack: Pack) -> bool:
    """Есть ли в маршруте вода (речной контур этого воза)."""
    return any(
        (tile := world.tiles.get(tid)) is not None and tile.terrain == "water"
        for tid in pack.route
    )


def port_toll(
    world: World, pack: Pack, destination_stock: Stock, date: SimDate
) -> dict[str, float]:
    """10 % натурой при разгрузке в порту: transfer в `Settlement.stores`.

    Соль (не еда) пошлину не платит (ADR 0075 п.5). Денег нет и не создаётся.
    """
    taken: dict[str, float] = {}
    for good in sorted(pack.cargo.amounts):
        rule = world.catalogs.goods.get(good)
        if rule is None or not rule.edible:
            continue
        amount = pack.cargo.amounts.get(good, 0.0) * PORT_TOLL_SHARE
        if amount <= EPSILON:
            continue
        world.ledger.transfer(
            pack.cargo, destination_stock, good, amount, "port_toll", date
        )
        taken[good] = amount
        world.bump("port_toll", amount)
    return taken
