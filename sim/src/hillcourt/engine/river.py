"""Река как дорога: плот и малая лодка везут груз по воде.

Замысел зоны ВОДА: река — дорога с другим профилем и другим судном.
Плот (`raft`) и малая лодка (`boat`) — обычные блага из уже существующих
брёвен и труда (`craft_raft`/`craft_boat`), не из воздуха. Без судна большой
груз по воде не едет (или идёт как отчаянный пеший брод через `ford`).

Подключение к суше, а не форк (ЧУЖОЕ сессии):
  маршрут — существующий `engine/path.py::find_path` по профилю
  (`water_raft`/`water_boat` из `engine/terrain.py`);
  риск — существующий hazard-контур (`strongest_hazard`/`base_risk_for`
  через `economy/caravan.py::resolve_caravans` для `kind="caravan"`);
  живые обозы (`caravan_visit`, `caravan_grain_to_ash`, манхэттен) не тронуты;
  служба тэна, скот, вид тайла и yield не тронуты; флота нет.
Течения нет: вверх/вниз одинаково (первая итерация). Брод (`Tile.ford`) —
клетка воды, где суша пересекает реку без судна: дорого (`crossing_cost`)
и под тем же hazard-контуром на месте назначения.

Порядок `send_river_pack` повторяет `send_march`: приказ игрока строится по
его известиям (`known_tiles` + запомненные дороги), а не по истине мира (И-3);
прямого приказа `Person` нет (И-2): приказ создаёт `Pack`, идут взрослые
двора или груз идёт как обоз без людей. Материя только переводится
(`Ledger.transfer`), судно в пути не расходуется (как телега «в инструменте»);
рецепт плота уже снял брёвна из стока при постройке.
"""

from __future__ import annotations

from ..info.sources import CHANNELS, MESSENGER
from ..legal.actions import world_pack_count
from ..legal.regimes import can_be_sent
from ..news.propagation import make_report
from ..ontology import Pack, Stock
from ..world import World
from .path import (
    find_path,
    known_tiles,
    remembered_road_tiles,
    travel_days,
    travel_months,
)
from .terrain import DAYS_PER_MONTH, get_profile, is_water_profile

VESSEL_FOR_PROFILE: dict[str, str] = {
    "water_raft": "raft",
    "water_boat": "boat",
}

WITHOUT_VESSEL_FREE_KG: float = 1.0
VESSEL_MIN_MASS: float = 1.0
EPSILON: float = 1e-9


def required_vessel(profile_id: str) -> str:
    """Какое благо-судно требует речной профиль; суша — `ValueError`."""
    get_profile(profile_id)
    try:
        return VESSEL_FOR_PROFILE[profile_id]
    except KeyError as exc:
        raise ValueError(
            f"Профиль '{profile_id}' не речной: судно нужно только "
            f"для {sorted(VESSEL_FOR_PROFILE)}"
        ) from exc


def origin_stocks(world: World, household_id: str) -> list[Stock]:
    """Стоки origin для судна и груза: сток двора, затем склад его поселения.

    Порядок детерминирован (сначала двор, потом склад). Чужие дворы поселения
    не трогаем: речной приказ — дело одного двора, а не всего origin (в отличие
    от поселенческого обоза `caravan_source_stocks`).
    """
    household = world.households.get(household_id)
    if household is None:
        return []
    out: list[Stock] = []
    stock = world.stocks.get(household.stock_id)
    if stock is not None:
        out.append(stock)
    settlement = world.settlements.get(household.settlement_id or "")
    if settlement is not None:
        stores = world.stocks.get(settlement.stores_stock_id)
        if stores is not None and stores not in out:
            out.append(stores)
    return out


def vessel_mass(world: World, household_id: str, vessel_good: str) -> float:
    """Сколько судна лежит в стоках origin (двор + склад поселения)."""
    return sum(
        stock.amounts.get(vessel_good, 0.0)
        for stock in origin_stocks(world, household_id)
    )


def has_vessel(world: World, household_id: str, profile_id: str) -> bool:
    """Есть ли у origin судно массой не ниже 1.0 для профиля."""
    vessel = required_vessel(profile_id)
    return vessel_mass(world, household_id, vessel) >= VESSEL_MIN_MASS - EPSILON


def route_has_water(world: World, route: list[str]) -> bool:
    """Есть ли в маршруте хоть одна клетка воды (река задействована)."""
    for tile_id in route:
        tile = world.tiles.get(tile_id)
        if tile is not None and tile.terrain == "water":
            return True
    return False


def _log(world: World, action: str, **fields) -> dict:
    """Записать приказ игрока в лог (`World.player_actions`)."""
    record = {"date": str(world.clock.date), "month": world.clock.month, "action": action}
    record.update(fields)
    world.player_actions.append(record)
    return record


def _add_months(date, months: int, months_per_year: int):
    """Сдвинуть дату на N месяцев вперёд (срок речного пути)."""
    result = date
    for _ in range(max(0, months)):
        result = result.advance(months_per_year)
    return result


def _load_cargo(
    world: World,
    household_id: str,
    cargo: dict[str, float],
    pack_stock: Stock,
    date,
) -> float:
    """Перевести груз со стоков origin в сток воза; вернуть погруженное.

    Грузится по товарам в алфавитном порядке, внутри товара — сначала сток
    двора, потом склад поселения. Материя не создаётся: только
    `Ledger.transfer` (`caravan_load` — та же причина, что у сухопутного воза,
    чтобы соль шла `household → pack → settlement`, а не телепортом).
    """
    loaded = 0.0
    sources = origin_stocks(world, household_id)
    for good in sorted(cargo):
        need = max(0.0, float(cargo[good] or 0.0))
        if need <= EPSILON:
            continue
        for source in sources:
            if need <= EPSILON:
                break
            take = min(source.amounts.get(good, 0.0), need)
            if take <= EPSILON:
                continue
            world.ledger.transfer(source, pack_stock, good, take, "caravan_load", date)
            need -= take
            loaded += take
    return loaded


def send_river_pack(
    world: World,
    household_id: str,
    member_ids: list[str],
    destination_tile_id: str,
    profile_id: str = "water_raft",
    cargo: dict[str, float] | None = None,
) -> Pack:
    """Послать груз (и/или людей) речным профилем: по воде, не по чаще.

    Маршрут — `find_path` по профилю `water_raft`/`water_boat` (вода дёшева,
    суша дорога). В маршруте обязана быть хоть одна клетка воды, иначе это
    суша и надо звать `send_march`. Без судна (`raft`/`boat` массой ≥ 1.0
    в стоках origin) большой груз (> 1.0) не едет — `ValueError`, а не
    телепорт; ручная ноша ≤ 1.0 считается отчаянным бродом и пропускается.
    Брод (`ford`) для речного профиля не нужен: судно идёт рекой.

    Воз — `Pack kind="caravan"` (тот же разбор, что у сухопутного воза:
    `economy/caravan.py::resolve_caravans` — фураж, риск опасных клеток
    маршрута через `strongest_hazard`, разгрузка в склад поселения
    назначения; отдельного флота и отдельного резолва нет). Если на клетках
    воды нет опасности, проход свободен — это честный отказ «паводка нет»,
    а не скрытый риск. Срок — сутки от часов пути (`travel_days`, ADR 0071),
    `eta_date` с точностью до дня; месяцы — производная для лога.
    """
    get_profile(profile_id)
    if not is_water_profile(profile_id):
        raise ValueError(
            f"Профиль '{profile_id}' сухопутный: рекой идут "
            f"только {sorted(VESSEL_FOR_PROFILE)}"
        )
    vessel = required_vessel(profile_id)
    household = world.households.get(household_id)
    if household is None:
        raise ValueError(f"Нет двора '{household_id}'")
    if not can_be_sent(world, household):
        raise PermissionError(
            f"Двор '{household_id}' ({household.legal_status_id}) нельзя послать"
        )
    origin = household.current_tile_id
    if destination_tile_id not in world.tiles:
        raise ValueError(f"Нет клетки '{destination_tile_id}'")
    if destination_tile_id == origin:
        raise ValueError("Цель речного пути — своя же клетка: посылать некого")

    known = known_tiles(world)
    known.add(origin)
    found = find_path(
        world,
        origin,
        destination_tile_id,
        profile_id,
        known=known,
        memory_roads=remembered_road_tiles(world),
    )
    if found is None:
        raise ValueError(
            f"От '{origin}' до '{destination_tile_id}' профилем '{profile_id}' "
            f"пути нет (неизвестная земля или река не ведёт)"
        )
    full_route, hours = found
    if not route_has_water(world, full_route):
        raise ValueError(
            f"Маршрут {full_route} без воды: речной профиль не нужен, зови send_march"
        )
    days = travel_days(hours)
    months = travel_months(days)

    wanted: dict[str, float] = dict(cargo or {})
    wanted_total = sum(max(0.0, float(v or 0.0)) for v in wanted.values())
    if wanted_total > WITHOUT_VESSEL_FREE_KG + EPSILON and not has_vessel(
        world, household_id, profile_id
    ):
        raise ValueError(
            f"Без судна '{vessel}' большой груз ({wanted_total}) по воде не едет: "
            f"построй плот ({vessel} ≥ 1.0) или иди бродом вплавь с ношей ≤ 1.0"
        )

    adults = {
        pid
        for pid in household.member_ids
        if world.persons.get(pid) is not None and world.persons[pid].age_class == "adult"
    }
    going = [pid for pid in (member_ids or []) if pid in adults]

    date = world.clock.date
    number = world_pack_count(world) + 1
    pack_id = f"river_{number:04d}"
    cargo_stock = Stock(
        id=f"pack:{pack_id}",
        owner_kind="pack",
        owner_id=pack_id,
        amounts={},
    )
    world.add_stock(cargo_stock)
    loaded = _load_cargo(world, household_id, wanted, cargo_stock, date)
    if wanted_total > EPSILON and loaded <= EPSILON:
        raise ValueError(
            f"У origin двора '{household_id}' нет груза {sorted(wanted)}: грузить нечего"
        )

    # Конвенция речного воза: origin в route не входит (сухопутный воз
    # и приказы хранят route с origin, см. docs/03_ontology.md, поле Pack.route).
    route = [tid for tid in full_route if tid != origin] or list(full_route)
    pack = Pack(
        id=pack_id,
        kind="caravan",
        origin_tile_id=origin,
        destination_tile_id=destination_tile_id,
        route=list(route),
        member_ids=list(going),
        cargo=cargo_stock,
        departed_date=date,
        eta_date=date.advance_days(days, DAYS_PER_MONTH, world.clock.months_per_year),
        status="in_transit",
        owner_household_id=household_id,
    )
    world.packs[pack.id] = pack

    for pid in going:
        household.member_ids.remove(pid)
        world.persons[pid].location_tile_id = destination_tile_id
    if going:
        household.labor_days = max(0.0, household.labor_days - 20.0 * len(going))
    _log(
        world,
        "send_river",
        household=household_id,
        destination=destination_tile_id,
        members=list(going),
        pack=pack.id,
        profile=profile_id,
        vessel=vessel,
        vessel_mass=round(vessel_mass(world, household_id, vessel), 3),
        route=list(route),
        travel_hours=round(hours, 3),
        days=days,
        months=months,
        cargo={k: round(float(v), 3) for k, v in sorted(cargo_stock.amounts.items())},
    )
    channel = CHANNELS[MESSENGER]
    make_report(
        world,
        source=MESSENGER,
        subject_kind="pack",
        subject_id=pack.id,
        content=(
            f"Речной воз послан из '{origin}' на клетку "
            f"'{destination_tile_id}' ({profile_id} на '{vessel}', {months} мес)"
        ),
        facts={"destination": destination_tile_id, "members": list(going)},
        event_date=world.clock.date,
        delay_months=channel.delay_months,
        confidence=channel.confidence,
        noise=channel.noise,
    )
    return pack
