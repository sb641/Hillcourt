"""Обоз: конкретный груз между поселениями — без биржи и мировых цен.

Модуль даёт стоки-источники поселения, сумму соли деревни и две операции
месячного тика: отправку обозов (`dispatch_caravans`) и разгрузку дошедшего
(`resolve_caravans`). Правило обоза — это `SpawnRule` с `target=pack` и
`params.kind=caravan`; `dispatch_caravans` обходит их все (id по возрастанию),
у каждого своя каденция `every_months`, origin, destination, cargo и
`cargo_amount`. Если поселения origin/destination нет в мире, правило молча
пропускается: сценарий `v0_hill_and_salt` не знает `ash_village`. id воза —
`caravan_{rule_id}_{год:04d}_{месяц:02d}`, сток — `pack:<id>`.

Материя не создаётся: груз уходит со стока origin в сток воза (`caravan_load`),
фураж съедается в `sink:eaten` (`fodder`), потеря у клетки назначения ложится
в её стоячий сток (`scattered`), остаток — в сток поселения назначения
(`caravan_unload`). Путь воза строит `engine/path.py::find_path` профилем
`caravan` (дорога/брод/мост дешевят вход, вода без переправы непроходима),
`route` включает origin; дни — сумма дней входа из `find_path`, поэтому
`eta_date` сдвигается на `ceil(days/30)` месяцев; путь и срок видны в `Pack`.
Фураж — по честным дням маршрута (`_route_days`, допуск ADR 0041), риск — по
клеткам после origin (`len(route)-1` входов), а не по манхэттену.

D3: глобальных цен нет вовсе — их нет как сущности, и биржи нет. Обмен — это
конкретный груз (зерно с холма в деревню у ясеня, соль из соляной деревни
в замок), а не число на «мировом рынке».

Телега (`cart`) — обычное благо, а не флаг: правило с `params.cart_bonus=true`
смотрит, лежит ли у поселения-origin хотя бы одна телега (дворы плюс склад).
С телегой ёмкость воза ×1.5 и дни пути как есть; без неё ёмкость ×0.6, дни
пути ×1.5 (округление вверх). Телега не расходуется — она «в инструменте»,
материю обоз по-прежнему не создаёт. Поверх телеги — тягло origin
(`economy/livestock.py::origin_draft`): осёл ×1.2 ёмкости, конь ×1.3 ёмкости
и ×0.75 дней; вол воза не знает.

G4: память сделки вместо биржи. `World.barter_memory` — словарь по ключу
`"{origin}->{destination}:{good}"`, а не цена: `ratio` — доля доехавшего груза
(`delivered / carried`), а не меновое число. Память заполняется при разгрузке
(`resolve_caravans`): `carried` — сколько груза ушло со стоков origin,
`delivered` — сколько доехало до стока назначения, `lost` — сколько рассеялось
у клетки. При отправке правило смотрит СВОЮ память (последняя сделка с
`ratio < min_ratio` — не везти впустую) и ДОСТАВЛЕННЫЕ `Report` о поселении
назначения (свежий отчёт с избытком зерна/соли — везти нечего); прямого
чтения стоков или клеток назначения нет. Нет памяти и нет отчёта — пробный
рейс разрешён. `min_ratio`, `report_window_months`, `skip_surplus` — параметры
правила со значениями по умолчанию 0.5 / 6 / 120.

H1: излишек выше буфера и живая телега.
- Съедобный груз (зерно) не вывозится ниже `need_buffer_months` (2.0) месячной
  нужды живых дворов origin: загрузка — только излишек выше буфера, ниже
  буфера воз не идёт (`caravan_below_buffer`). Соль — не еда, буфера нет.
- Телега изнашивается на рейсе: при `cart_bonus` и cart ≥ `cart_break_below`
  (0.5) бонус действует, cart теряет `cart_wear_per_trip` (0.05) в
  `sink:waste`. Опустившись ниже порога, телега ломается (`cart_broken`),
  остаток идёт в отход; бонус пропадает, пока не справят новую (`craft_cart`).
  Материя не исчезает.
- При провале риск-броска с вероятностью `catastrophe_chance` (0.25) воз
  гибнет целиком: `hazards.travel.resolve_pack_loss` кладёт весь груз в
  стоячий сток клетки назначения (`scattered`), ставит `lost` и рождает
  `silence`; иначе — прежнее рассеяние доли `loss_share` и `arrived`.
"""

from __future__ import annotations

import math

from ..engine.path import find_path
from ..engine.terrain import DAYS_PER_MONTH
from ..hazards.model import strongest_hazard
from ..hazards.travel import resolve_pack_loss
from ..info.sources import SILENCE
from ..news.propagation import make_report
from ..ontology import Pack, SimDate, SpawnRule, Stock
from ..world import World
from .livestock import draft_capacity_factor, draft_days_factor, origin_draft
from .needs import monthly_food_need

EPSILON = 1e-9
CARAVAN_KIND = "caravan"
PACK_TARGET = "pack"
CARAVAN_SOURCE_SETTLEMENT = "salt_village"
EATEN_STOCK_ID = "sink:eaten"
WASTE_STOCK_ID = "sink:waste"
FODDER_GOOD = "hay"
PACK_ID_PREFIX = "caravan_"
CART_GOOD = "cart"
CART_BONUS_PARAM = "cart_bonus"
CART_MIN_AMOUNT = 1.0
CART_BREAK_BELOW = 0.5
CART_WEAR_PER_TRIP = 0.05
CART_BREAK_BELOW_PARAM = "cart_break_below"
CART_WEAR_PER_TRIP_PARAM = "cart_wear_per_trip"
CART_WEAR_REASON = "cart_wear"

DEFAULT_FODDER_PER_DAY = 4.5
DEFAULT_RISK_PER_HAZARD_TILE = 0.15
DEFAULT_LOSS_SHARE = 0.25
MAX_ROUTE_RISK = 0.75
CART_CAPACITY_FACTOR = 1.5
NO_CART_CAPACITY_FACTOR = 0.6
NO_CART_DAYS_FACTOR = 1.5

MIN_RATIO_PARAM = "min_ratio"
REPORT_WINDOW_PARAM = "report_window_months"
SKIP_SURPLUS_PARAM = "skip_surplus"
NEED_BUFFER_MONTHS_PARAM = "need_buffer_months"
CATASTROPHE_CHANCE_PARAM = "catastrophe_chance"
DEFAULT_MIN_RATIO = 0.5
DEFAULT_REPORT_WINDOW_MONTHS = 6
DEFAULT_SKIP_SURPLUS = 120.0
DEFAULT_NEED_BUFFER_MONTHS = 2.0
DEFAULT_CATASTROPHE_CHANCE = 0.0
SKIP_BAD_DEAL = "caravan_skipped_bad_deal"
SKIP_SURPLUS = "caravan_skipped_surplus"
BELOW_BUFFER = "caravan_below_buffer"
CART_BROKEN = "cart_broken"
CARAVAN_LOST = "caravan_lost"


def _tile_id(x: int, y: int) -> str:
    """id клетки в формате сценария (`scenario._tile_id`)."""
    return f"t_{x:02d}_{y:02d}"


def caravan_rules(world: World) -> list[SpawnRule]:
    """Все правила обоза (`target=pack`, `params.kind=caravan`) по id.

    `kind` в YAML — календарность правила (`calendric`), а семантика обоза
    живёт в `params.kind`; объезд шерифа (`params.kind=messenger`) сюда не
    попадает.
    """
    rules: list[SpawnRule] = []
    for rule_id in sorted(world.catalogs.spawn_rules):
        rule = world.catalogs.spawn_rules[rule_id]
        if rule.target != PACK_TARGET:
            continue
        if str(rule.params.get("kind", "")) != CARAVAN_KIND:
            continue
        rules.append(rule)
    return rules


def caravan_source_stocks(world: World, settlement_id: str) -> list[Stock]:
    """Стоки-источники поселения: живые дворы по id, затем склад поселения."""
    settlement = world.settlements.get(settlement_id)
    if settlement is None:
        return []
    stocks: list[Stock] = []
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.settlement_id != settlement_id or household.left_at is not None:
            continue
        stock = world.stocks.get(household.stock_id)
        if stock is not None:
            stocks.append(stock)
    stores = world.stocks.get(settlement.stores_stock_id)
    if stores is not None:
        stocks.append(stores)
    return stocks


def salt_in_village(world: World) -> float:
    """Сколько соли лежит в соляной деревне: дворы плюс склад поселения."""
    return sum(
        stock.amounts.get("salt", 0.0)
        for stock in caravan_source_stocks(world, CARAVAN_SOURCE_SETTLEMENT)
    )


def origin_cart_mass(world: World, settlement_id: str) -> float:
    """Масса телег у поселения-origin: живые дворы плюс склад."""
    return sum(
        stock.amounts.get(CART_GOOD, 0.0)
        for stock in caravan_source_stocks(world, settlement_id)
    )


def origin_has_cart(
    world: World, settlement_id: str, min_amount: float = CART_BREAK_BELOW
) -> bool:
    """Есть ли у поселения-origin телега массой не ниже порога.

    Порог — `cart_break_below` (0.5): телега «в инструменте», пока её масса
    не упала ниже. Сломанная (списанная в отход) телега бонуса не даёт.
    """
    return origin_cart_mass(world, settlement_id) >= min_amount - EPSILON


def origin_amount(world: World, settlement_id: str, good: str) -> float:
    """Сколько товара лежит у поселения-origin: живые дворы плюс склад."""
    return sum(
        stock.amounts.get(good, 0.0)
        for stock in caravan_source_stocks(world, settlement_id)
    )


def origin_monthly_food_need(world: World, settlement_id: str) -> float:
    """Месячная нужда в еде живых дворов поселения (рот-единицы)."""
    total = 0.0
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.settlement_id != settlement_id or household.left_at is not None:
            continue
        total += monthly_food_need(world, household)
    return total


def origin_food_buffer(world: World, rule: SpawnRule, settlement_id: str) -> float:
    """Неприкосновенный запас еды origin: `need_buffer_months` месячной нужды.

    Посев и зима не продаются: съедобный груз можно грузить только выше этого
    буфера. Для несъедобного груза буфер не считается (соль — не еда).
    """
    months = float(
        rule.params.get(NEED_BUFFER_MONTHS_PARAM, DEFAULT_NEED_BUFFER_MONTHS) or 0.0
    )
    return months * origin_monthly_food_need(world, settlement_id)


def _drain_to_waste(
    world: World, settlement_id: str, good: str, amount: float, reason: str, date: SimDate
) -> float:
    """Списать до `amount` товара со стоков origin в `sink:waste`."""
    remaining = max(0.0, amount)
    moved = 0.0
    waste = world.get_stock(WASTE_STOCK_ID)
    for source in caravan_source_stocks(world, settlement_id):
        if remaining <= EPSILON:
            break
        take = min(source.amounts.get(good, 0.0), remaining)
        if take <= EPSILON:
            continue
        world.ledger.transfer(source, waste, good, take, reason, date)
        remaining -= take
        moved += take
    return moved


def wear_cart(world: World, settlement_id: str, rule: SpawnRule, date: SimDate) -> bool:
    """Износить телегу на рейсе; True, если телега сломалась.

    Телега теряет `cart_wear_per_trip` (0.05) массы в `sink:waste`. Если после
    износа её масса падает ниже `cart_break_below` (0.5), остаток тоже идёт в
    отход, счётчик `cart_broken` растёт, и бонус пропадает до новой телеги.
    Материя сохраняется: вся масса телеги оседает в отходе.
    """
    break_below = float(
        rule.params.get(CART_BREAK_BELOW_PARAM, CART_BREAK_BELOW) or 0.0
    )
    wear = float(rule.params.get(CART_WEAR_PER_TRIP_PARAM, CART_WEAR_PER_TRIP) or 0.0)
    if origin_cart_mass(world, settlement_id) < break_below - EPSILON:
        return False
    if wear > EPSILON:
        _drain_to_waste(world, settlement_id, CART_GOOD, wear, CART_WEAR_REASON, date)
    remaining = origin_cart_mass(world, settlement_id)
    if remaining < break_below - EPSILON:
        if remaining > EPSILON:
            _drain_to_waste(
                world, settlement_id, CART_GOOD, remaining, CART_WEAR_REASON, date
            )
        world.bump(CART_BROKEN)
        return True
    return False


def caravan_capacity(rule: SpawnRule, has_cart: bool, draft: str = "none") -> float:
    """Ёмкость воза по правилу: номинал, а с `cart_bonus` — множитель телеги.

    С телегой ёмкость ×1.5, без неё ×0.6; правило без `cart_bonus` возит
    номинал `cargo_amount` и от телеги не зависит. Телега не расходуется.
    Тягло origin — поверх телеги (economy/livestock.py): осёл ×1.2,
    конь ×1.3; вол и пеший — база. Без телеги воз жив, но хуже, как раньше.
    """
    base = float(rule.params.get("cargo_amount", 0.0) or 0.0)
    if not rule.params.get(CART_BONUS_PARAM):
        return base * draft_capacity_factor(draft)
    factor = CART_CAPACITY_FACTOR if has_cart else NO_CART_CAPACITY_FACTOR
    return base * factor * draft_capacity_factor(draft)


def caravan_days(base_days: float, has_cart: bool, draft: str = "none") -> float:
    """Дни пути воза от дней `find_path`: с телегой как есть, без — ×1.5 вверх.

    `base_days` — сумма дней входа маршрута (не число клеток): дорога/брод
    дешевят путь. Конь сверху быстрее (×0.75 вверх); осёл и пеший идут как шли.
    """
    if has_cart:
        days = float(base_days)
    else:
        days = float(math.ceil(float(base_days) * NO_CART_DAYS_FACTOR))
    return float(math.ceil(days * draft_days_factor(draft)))


def _add_months(date: SimDate, months: int, months_per_year: int) -> SimDate:
    index = date.year * months_per_year + (date.month - 1) + months
    year, month_index = divmod(index, months_per_year)
    return SimDate(year, month_index + 1, date.day)


def _route_entries(world: World, pack: Pack) -> list[str]:
    """Клетки входа маршрута воза: всё, что идёт ПОСЛЕ origin.

    `Pack.route` воза включает origin (как `send_march`); фураж и риск
    считаются по входам, то есть по клеткам после origin. Фильтр по id делает
    помощник устойчивым и к маршрутам без origin (речной воз, `engine/river.py`).
    """
    return [tile_id for tile_id in pack.route if tile_id != pack.origin_tile_id]


def _route_days(world: World, pack: Pack) -> float:
    """Честные СУТКИ пути по часам маршрута (ADR 0078), не старые «дни на клетку».

    Часы — сумма `entry_hours` по входам после origin (те же числа, что видит
    `find_path`); сутки = `hours / HOURS_PER_DAY`. Речной маршрут считаем
    водным якорем (`water_raft`/`water_boat` по профилю), если в нём есть вода.
    """
    from ..engine.terrain import HOURS_PER_DAY, entry_hours, trail_level_for

    profile = "caravan"
    if any(
        (tile := world.tiles.get(tid)) is not None and tile.terrain == "water"
        for tid in pack.route
    ):
        profile = "water_boat"
    total_hours = 0.0
    for tile_id in _route_entries(world, pack):
        tile = world.tiles.get(tile_id)
        if tile is None:
            continue
        total_hours += entry_hours(
            profile,
            tile.terrain,
            tile.road,
            tile.ford,
            tile.bridge,
            trail_level_for(tile.trail_wear),
        )
    return total_hours / float(HOURS_PER_DAY)


def _is_edible(world: World, good: str) -> bool:
    """Съедобен ли товар по каталогу: тогда фураж можно взять из самого груза."""
    rule = world.catalogs.goods.get(good)
    return bool(rule is not None and rule.edible)


def _load_good(
    world: World,
    origin: str,
    good: str,
    amount: float,
    cargo_stock: Stock,
    reason: str,
    date: SimDate,
) -> float:
    """Перевести до `amount` товара со стоков origin в воз; вернуть погруженное."""
    remaining = amount
    loaded = 0.0
    for source in caravan_source_stocks(world, origin):
        if remaining <= EPSILON:
            break
        take = min(source.amounts.get(good, 0.0), remaining)
        if take <= EPSILON:
            continue
        world.ledger.transfer(source, cargo_stock, good, take, reason, date)
        remaining -= take
        loaded += take
    return loaded


def _fodder_amount(rule: SpawnRule, days: float) -> float:
    """Фураж на путь: `fodder_per_day` правила × честные дни маршрута.

    Имя параметра старое (`per_day`), счёт новый (допуск ADR 0041, вариант B):
    мощёный маршрут экономит дни и фураж, немощёный платит полностью.
    """
    per_day = float(rule.params.get("fodder_per_day", DEFAULT_FODDER_PER_DAY) or 0.0)
    return max(0.0, per_day) * max(0.0, days)


def _pack_draft(world: World, pack: Pack) -> str:
    """Тягло origin воза (`horse`/`donkey`/`none`) для фуража порции тягла."""
    tile = world.tiles.get(pack.origin_tile_id)
    if tile is None or tile.settlement_id is None:
        return "none"
    return origin_draft(world, tile.settlement_id)


def barter_key(origin: str, destination: str, good: str) -> str:
    """Ключ памяти сделки: поселение-origin, поселение-назначение и товар.

    Память — не цена: ключ описывает конкретный маршрут груза, а не «курс»
    товара на рынке.
    """
    return f"{origin}->{destination}:{good}"


def _empty_memory(date: SimDate) -> dict:
    """Пустая запись памяти: сделки ещё не было, только причина пропуска."""
    return {
        "date": str(date),
        "carried": 0.0,
        "delivered": 0.0,
        "ratio": None,
        "lost": 0.0,
    }


def _memory_blocks(world: World, key: str, min_ratio: float) -> bool:
    """Запрещает ли память сделки новый рейс: последняя дошла хуже `min_ratio`.

    Полная гибель воза (катастрофа, `delivered == 0`) — не «плохая сделка», а
    случайность пути: такая запись рейс не запрещает, иначе маршрут умер бы
    навсегда после первой катастрофы. Блокирует только частичная потеря.
    """
    memory = world.barter_memory.get(key)
    if not memory:
        return False
    ratio = memory.get("ratio")
    if ratio is None:
        return False
    if float(memory.get("delivered", 0.0)) <= EPSILON:
        return False
    return float(ratio) < min_ratio - EPSILON


def _months_between(earlier: SimDate, later: SimDate, months_per_year: int) -> int:
    """Число месяцев от `earlier` до `later` (может быть отрицательным)."""
    return (later.year * months_per_year + later.month) - (
        earlier.year * months_per_year + earlier.month
    )


def _surplus_fact_key(good: str) -> str:
    """Ключ факта отчёта об избытке товара: `grain` → `grain_approx`."""
    return f"{good}_approx"


def _destination_reported_surplus(
    world: World, rule: SpawnRule, destination_tile_id: str, cargo: str, date: SimDate
) -> bool:
    """Доехал ли Report о том, что у назначения избыток везённого товара.

    Смотрятся ТОЛЬКО доставленные `Report` (`delivery_date <= date`) о клетке
    назначения, не старше `report_window_months`; берётся факт по везённому
    товару (`grain_approx` для зерна, `salt_approx` для соли). Истина мира при
    этом не читается: стоки и клетки назначения для решения недоступны.
    """
    key = _surplus_fact_key(cargo)
    window = int(
        rule.params.get(REPORT_WINDOW_PARAM, DEFAULT_REPORT_WINDOW_MONTHS) or 0
    )
    surplus = float(rule.params.get(SKIP_SURPLUS_PARAM, DEFAULT_SKIP_SURPLUS) or 0.0)
    for report in world.reports:
        if report.subject_id != destination_tile_id:
            continue
        if report.delivery_date > date:
            continue
        age = _months_between(report.delivery_date, date, world.clock.months_per_year)
        if age < 0 or age > window:
            continue
        value = report.facts.get(key)
        if isinstance(value, (int, float)) and float(value) > surplus:
            return True
    return False


def _rule_for_pack(world: World, pack: Pack) -> SpawnRule | None:
    """Правило воза по его id `caravan_{rule_id}_{год:04d}_{месяц:02d}`."""
    parts = pack.id.rsplit("_", 2)
    if len(parts) != 3 or not parts[0].startswith(PACK_ID_PREFIX):
        return None
    return world.catalogs.spawn_rules.get(parts[0][len(PACK_ID_PREFIX) :])


def fodder_for_pack(world: World, pack: Pack) -> float:
    """Фураж воза: порция правила × честные СУТКИ маршрута (ADR 0075/0078).

    Сутки = часы/24 (`_route_days`), порция — 4.5/сутки обоза + 2.75/сутки
    тягла (ADR 0075 п.6); на воде — только за ночёвку (>6 ч пути).
    """
    from .water import NIGHT_FODDER_HOURS, PORTION_PER_DAY, is_water_route

    days = _route_days(world, pack)
    draft = _pack_draft(world, pack)
    if is_water_route(world, pack):
        if days * 24.0 <= NIGHT_FODDER_HOURS:
            return 0.0
        portion = PORTION_PER_DAY["caravan"]
        if draft in ("donkey", "horse"):
            portion += PORTION_PER_DAY["draft"]
        return portion * days
    rule = _rule_for_pack(world, pack)
    per_day = (
        float(rule.params.get("fodder_per_day", DEFAULT_FODDER_PER_DAY) or 0.0)
        if rule is not None
        else DEFAULT_FODDER_PER_DAY
    )
    if draft in ("donkey", "horse"):
        per_day += PORTION_PER_DAY["draft"]
    return max(0.0, per_day) * max(0.0, days)


def _is_port_tile(world: World, tile) -> bool:
    """Порт = поселение у воды: клетка поселения или его `works_tiles` — вода."""
    if tile is None or tile.settlement_id is None:
        return False
    settlement = world.settlements.get(tile.settlement_id)
    if settlement is None:
        return False
    if tile.terrain == "water":
        return True
    for tid in settlement.works_tiles:
        near = world.tiles.get(tid)
        if near is not None and near.terrain == "water":
            return True
    return False


def dispatch_caravans(world: World, date: SimDate) -> list[Pack]:
    """Отправить обозы по всем правилам обоза; вернуть созданные Pack'и.

    Каждое правило живёт по своей каденции `every_months`; груз берётся из
    стоков origin по порядку (живые дворы по id, затем склад поселения) до
    ёмкости (`cargo_amount`, с `cart_bonus` — с поправкой на телегу origin).
    Если поселение origin/destination не существует в мире,
    правило молча пропускается. Для несъедобного груза воз везёт фураж сеном
    со стоков origin (`caravan_fodder`); если сена нет — идёт без фуража.
    Если не загружено ничего — воз не создаётся, счётчик `caravan_empty`.
    """
    dispatched: list[Pack] = []
    for rule in caravan_rules(world):
        pack = _dispatch_rule(world, date, rule)
        if pack is not None:
            dispatched.append(pack)
    return dispatched


def _dispatch_rule(world: World, date: SimDate, rule: SpawnRule) -> Pack | None:
    """Собрать и отправить один воз по правилу; None — правило пропущено."""
    params = rule.params
    origin = params.get("origin")
    destination = params.get("destination")
    cargo = params.get("cargo")
    every_months = int(params.get("every_months", 0) or 0)
    if not origin or not destination or not cargo or every_months <= 0:
        return None
    if world.clock.month % every_months != 0:
        return None
    origin_settlement = world.settlements.get(origin)
    destination_settlement = world.settlements.get(destination)
    if origin_settlement is None or destination_settlement is None:
        return None
    origin_tile = _tile_id(*origin_settlement.coord)
    destination_tile = _tile_id(*destination_settlement.coord)
    pack_id = f"{PACK_ID_PREFIX}{rule.id}_{date.year:04d}_{date.month:02d}"
    if pack_id in world.packs:
        return None
    if origin_tile not in world.tiles or destination_tile not in world.tiles:
        return None
    # Возницы землю знают (`known=None`): маршрут считается по миру, а не по
    # известиям игрока. Нет пути (река без брода) — воз не отправляется.
    found = find_path(world, origin_tile, destination_tile, "caravan")
    if found is None:
        return None
    route, path_hours = found
    # Фураж/ETA — от часов по ADR 0078: сутки = hours/24 (не старые «дни на
    # клетку» и не месячный квант). Порт/тягло учтены в порции фуража.
    from ..engine.terrain import HOURS_PER_DAY

    path_days = path_hours / float(HOURS_PER_DAY)
    key = barter_key(origin, destination, cargo)
    min_ratio = float(params.get(MIN_RATIO_PARAM, DEFAULT_MIN_RATIO) or 0.0)
    memory = world.barter_memory.get(key)
    if _memory_blocks(world, key, min_ratio):
        memory["last_skip_reason"] = SKIP_BAD_DEAL
        world.bump(SKIP_BAD_DEAL)
        return None
    if _destination_reported_surplus(world, rule, destination_tile, cargo, date):
        entry = world.barter_memory.setdefault(key, _empty_memory(date))
        entry["last_skip_reason"] = SKIP_SURPLUS
        world.bump(SKIP_SURPLUS)
        return None
    cart_bonus = bool(params.get(CART_BONUS_PARAM))
    break_below = float(
        params.get(CART_BREAK_BELOW_PARAM, CART_BREAK_BELOW) or 0.0
    )
    has_cart = (
        origin_has_cart(world, origin, break_below) if cart_bonus else True
    )
    # Тягло origin (economy/livestock.py): осёл/конь везут иначе, чем пеший;
    # телега при этом по-прежнему нужна — множители перемножаются, а не
    # заменяют друг друга. Тягло остаётся на месте, в воз не грузится.
    draft = origin_draft(world, origin)
    capacity = caravan_capacity(rule, has_cart, draft)
    load_target = capacity
    if _is_edible(world, cargo):
        buffer = origin_food_buffer(world, rule, origin)
        available = origin_amount(world, origin, cargo)
        if available - buffer <= EPSILON:
            world.bump(BELOW_BUFFER)
            return None
        load_target = min(capacity, available - buffer)
    cargo_stock = Stock(
        id=f"pack:{pack_id}", owner_kind="pack", owner_id=pack_id, amounts={}
    )
    loaded = _load_good(
        world, origin, cargo, load_target, cargo_stock, "caravan_load", date
    )
    if loaded <= EPSILON:
        world.bump("caravan_empty")
        return None
    if cart_bonus and has_cart:
        wear_cart(world, origin, rule, date)
    if not _is_edible(world, cargo):
        from .water import PORTION_PER_DAY

        fodder = _fodder_amount(rule, path_days)
        if draft in ("donkey", "horse"):
            fodder += PORTION_PER_DAY["draft"] * max(0.0, path_days)
        if fodder > EPSILON:
            _load_good(
                world, origin, FODDER_GOOD, fodder, cargo_stock, "caravan_fodder", date
            )
    days = caravan_days(path_days, has_cart, draft)
    eta_months = max(1, math.ceil(days / 30.0))
    world.bump("caravan_travel_hours", path_hours)
    pack = Pack(
        id=pack_id,
        kind=CARAVAN_KIND,
        origin_tile_id=origin_tile,
        destination_tile_id=destination_tile,
        route=route,
        member_ids=[],
        cargo=cargo_stock,
        departed_date=date,
        eta_date=date.advance_days(days, DAYS_PER_MONTH, world.clock.months_per_year),
        status="in_transit",
    )
    world.stocks[cargo_stock.id] = cargo_stock
    world.packs[pack_id] = pack
    world.bump("caravan_sent")
    return pack


def _eat_fodder(world: World, pack: Pack, date: SimDate) -> float:
    """Съесть фураж воза: из съедобного груза или из везённого сена.

    Материя не создаётся: `Ledger.transfer` из стока воза в `sink:eaten` с
    reason `fodder`. Если корма в возе нет (сена в origin не нашлось), воз
    идёт пустым по фуражу — ничего не выдумывается.
    """
    fodder = fodder_for_pack(world, pack)
    if fodder <= EPSILON:
        return 0.0
    rule = _rule_for_pack(world, pack)
    cargo_good = rule.params.get("cargo") if rule is not None else None
    feed_good: str | None = None
    if cargo_good is not None and _is_edible(world, cargo_good):
        feed_good = str(cargo_good)
    elif pack.cargo.amounts.get(FODDER_GOOD, 0.0) > EPSILON:
        feed_good = FODDER_GOOD
    elif cargo_good is None:
        feed_good = next(
            (
                good
                for good in sorted(pack.cargo.amounts)
                if _is_edible(world, good)
            ),
            None,
        )
    if feed_good is None:
        return 0.0
    amount = min(fodder, pack.cargo.amounts.get(feed_good, 0.0))
    if amount <= EPSILON:
        return 0.0
    world.ledger.transfer(
        pack.cargo, world.get_stock(EATEN_STOCK_ID), feed_good, amount, "fodder", date
    )
    world.bump("caravan_fodder_eaten", amount)
    return amount


def _lose_pack(world: World, pack: Pack, date: SimDate) -> dict[str, float]:
    """Погубить воз на катастрофе: весь груз — в стоячий сток клетки.

    Груз снимается до вызова `resolve_pack_loss` (он переводит весь сток воза),
    чтобы вернуть рассеянное по товарам для памяти сделки. Материя не
    исчезает: `resolve_pack_loss` кладёт её в `standing_stock_id` назначения.
    """
    scattered = {
        good: amount
        for good, amount in sorted(pack.cargo.amounts.items())
        if amount > EPSILON
    }
    lost_total = sum(scattered.values())
    resolve_pack_loss(world, pack, date)
    world.bump(CARAVAN_LOST)
    if lost_total > EPSILON:
        world.bump("caravan_scattered", lost_total)
    return scattered


def _roll_route_loss(
    world: World, pack: Pack, destination_tile, date: SimDate
) -> dict[str, float]:
    """Розыгрыш риска пути: доля груза оседает в стоке клетки назначения.

    Шанс — `min(0.75, risk_per_hazard_tile × число клеток маршрута с активной
    опасностью)`. При провале:
    - с вероятностью `catastrophe_chance` (0.25) воз гибнет целиком —
      `hazards.travel.resolve_pack_loss` кладёт весь груз в стоячий сток
      клетки (`scattered`), ставит `lost` и рождает `silence`; возвращается
      весь груз как рассеянное;
    - иначе `loss_share` каждого товара уходит в стоячий сток (`scattered`) и
      рождается `Report` с `source=silence`; воз не гибнет: частичная потеря,
      статус `arrived`, остаток доезжает.
    Возвращает рассеянное по товарам (для памяти сделки). Катастрофа
    оставляет `pack.status="lost"` — разгрузка его не трогает.
    """
    rule = _rule_for_pack(world, pack)
    params = rule.params if rule is not None else {}
    hazard_tiles = [
        tile_id
        for tile_id in _route_entries(world, pack)
        if strongest_hazard(world, tile_id) is not None
    ]
    if not hazard_tiles:
        return {}
    risk_per_tile = float(
        params.get("risk_per_hazard_tile", DEFAULT_RISK_PER_HAZARD_TILE) or 0.0
    )
    risk = min(MAX_ROUTE_RISK, risk_per_tile * len(hazard_tiles))
    if risk <= EPSILON or world.rng.hazard.random() >= risk:
        return {}
    catastrophe_chance = float(
        params.get(CATASTROPHE_CHANCE_PARAM, DEFAULT_CATASTROPHE_CHANCE) or 0.0
    )
    if catastrophe_chance > EPSILON and world.rng.world.random() < catastrophe_chance:
        return _lose_pack(world, pack, date)
    loss_share = float(params.get("loss_share", DEFAULT_LOSS_SHARE) or 0.0)
    standing = world.get_stock(destination_tile.standing_stock_id)
    scattered: dict[str, float] = {}
    for good in sorted(pack.cargo.amounts):
        amount = pack.cargo.amounts.get(good, 0.0) * loss_share
        if amount <= EPSILON:
            continue
        world.ledger.transfer(pack.cargo, standing, good, amount, "scattered", date)
        world.bump("caravan_scattered", amount)
        scattered[good] = scattered.get(good, 0.0) + amount
    make_report(
        world,
        SILENCE,
        "tile",
        destination_tile.id,
        "Обоз дошёл не весь: часть груза рассеялась у места.",
        {},
        date,
        0,
        0.3,
        distorted=False,
    )
    return scattered


def _unload_cargo(
    world: World, pack: Pack, destination_stock: Stock, date: SimDate
) -> dict[str, float]:
    """Перевести остаток груза в сток поселения; вернуть доставленное по товарам."""
    delivered: dict[str, float] = {}
    for good in sorted(pack.cargo.amounts):
        amount = pack.cargo.amounts.get(good, 0.0)
        if amount <= EPSILON:
            continue
        world.ledger.transfer(
            pack.cargo, destination_stock, good, amount, "caravan_unload", date
        )
        delivered[good] = delivered.get(good, 0.0) + amount
    return delivered


def resolve_caravans(world: World, date: SimDate) -> list[Pack]:
    """Разгрузить дошедшие обозы: фураж, риск пути, сток назначения.

    Берутся обозы `kind="caravan"` со сроком `date >= eta_date`. Сначала воз
    съедает фураж, затем разыгрывается риск опасных клеток маршрута, остаток
    уходит в `stores_stock_id` поселения (`caravan_unload`), статус становится
    `arrived`. Если дневной контур пути уже пометил воз `arrived`, но груз
    остался в стоке воза, разгрузка всё равно делается: иначе груз застыл бы
    в пути навсегда. По разгрузке пишется память сделки (G4): сколько груза
    ушло, сколько доехало и какая это доля; это доля груза, а не цена.
    """
    resolved: list[Pack] = []
    for pack_id in sorted(world.packs):
        pack = world.packs[pack_id]
        if pack.kind != CARAVAN_KIND or pack.status == "lost":
            continue
        if date < pack.eta_date or pack.cargo.total() <= EPSILON:
            continue
        if pack.status not in ("in_transit", "arrived"):
            continue
        destination_tile = world.tiles.get(pack.destination_tile_id)
        destination = None
        if destination_tile is not None and destination_tile.settlement_id:
            destination = world.settlements.get(destination_tile.settlement_id)
        if destination is None:
            continue
        rule = _rule_for_pack(world, pack)
        cargo_good = (
            str(rule.params["cargo"])
            if rule is not None and rule.params.get("cargo")
            else None
        )
        carried = (
            pack.cargo.amounts.get(cargo_good, 0.0) if cargo_good is not None else 0.0
        )
        _eat_fodder(world, pack, date)
        lost_by_good = _roll_route_loss(world, pack, destination_tile, date)
        if pack.status == "lost":
            delivered_by_good: dict[str, float] = {}
        else:
            destination_stock = world.get_stock(destination.stores_stock_id)
            # Порт = поселение у водного гекса назначения: 10 % съедобного
            # груза натурой в его склад (ADR 0075 п.4–5). Соль пошлину не платит.
            if _is_port_tile(world, destination_tile):
                from .water import port_toll

                port_toll(world, pack, destination_stock, date)
            delivered_by_good = _unload_cargo(world, pack, destination_stock, date)
            pack.status = "arrived"
            world.bump("caravan_dispatched")
        delivered_salt = delivered_by_good.get("salt", 0.0)
        if delivered_salt > EPSILON:
            world.bump("caravan_salt_delivered", delivered_salt)
        if cargo_good is not None and carried > EPSILON:
            delivered = delivered_by_good.get(cargo_good, 0.0)
            lost = lost_by_good.get(cargo_good, 0.0)
            world.barter_memory[
                barter_key(
                    str(rule.params["origin"]),
                    str(rule.params["destination"]),
                    cargo_good,
                )
            ] = {
                "date": str(date),
                "carried": carried,
                "delivered": delivered,
                "ratio": delivered / carried,
                "lost": lost,
            }
            world.bump("caravan_barter_recorded")
        resolved.append(pack)
    return resolved
