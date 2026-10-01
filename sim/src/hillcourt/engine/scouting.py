"""Физическое наблюдение разведки; знание игрока здесь не расширяется."""

from __future__ import annotations

from ..economy.needs import ADULT_LABOR_DAYS, monthly_food_need
from ..ontology import Household, Pack, Person, Settlement, SimDate, Stock
from ..world import World
from .hexgrid import axial_to_offset, neighbor_ids

SCOUT_OBSERVE = "scout_observe"
SCOUT_SOURCE = "scout"
DISCOVERY_MARKS = ("hermitage", "smoke", "lost_caravan")
DISCOVERY_MATTER_REASON = "scout_discovery"
DISCOVERY_MATTER_RULE = "ruin_site"
RUMOR_CONFIDENCE = 0.5
PROFILE_CHANCE: dict[str, float] = {
    "hunters": 0.85,
    "forester": 0.75,
    "party": 0.55,
}
STRONG_COVER_TERRAINS: frozenset[str] = frozenset(("forest", "marsh"))
WEAK_COVER_TERRAINS: frozenset[str] = frozenset(("field", "pasture"))


def observation_chance(pack: Pack, world: World, tile_id: str) -> float:
    """Вернуть шанс заметить соседнюю клетку по профилю, покрову и угрозе."""
    tile = world.tiles.get(tile_id)
    if tile is None:
        return 0.0
    if tile.terrain in STRONG_COVER_TERRAINS:
        cover = 1.0
    elif tile.terrain in WEAK_COVER_TERRAINS:
        cover = 0.5
    else:
        cover = 0.75
    active_intensity = max(
        (
            world.hazards[hazard_id].intensity
            for hazard_id in tile.hazard_ids
            if hazard_id in world.hazards and world.hazards[hazard_id].active
        ),
        default=0.0,
    )
    hazard = 1.0 - min(0.75, max(0.0, active_intensity) * 0.5)
    return min(1.0, max(0.0, PROFILE_CHANCE[pack.profile_id] * cover * hazard))


def _location_tile_id(world: World, pack: Pack) -> str | None:
    """Вернуть общую клетку живых участников Pack или None."""
    locations = {
        world.persons[person_id].location_tile_id
        for person_id in pack.member_ids
        if person_id in world.persons and world.persons[person_id].health > 0.0
    }
    if len(locations) != 1:
        return None
    location = next(iter(locations))
    return location if location in world.tiles else None


def _ring(world: World, location_id: str, radius: int = 2) -> set[str]:
    """Собрать существующие клетки кольца заданного радиуса без обхода карты."""
    found = {location_id}
    frontier = {location_id}
    for _ in range(radius):
        following: set[str] = set()
        for tile_id in sorted(frontier):
            following.update(neighbor_ids(world, world.tiles[tile_id]))
        following -= found
        found.update(following)
        frontier = following
    return found


def _discovery_kind(world: World, tile_id: str) -> str | None:
    tile = world.tiles.get(tile_id)
    if tile is None or tile.settlement_id is not None:
        return None
    for mark in DISCOVERY_MARKS:
        if getattr(tile, mark, False):
            return mark
    if tile.terrain == "ruin" or tile.ruin_id:
        return "ruin"
    return None


def _event(
    pack: Pack,
    tile_id: str,
    date: SimDate,
    fact: bool,
    confidence: float,
    rumor: bool = False,
    discovery: str | None = None,
) -> dict:
    """Собрать событие физического наблюдения по контракту Info."""
    event = {
        "kind": SCOUT_OBSERVE,
        "tile_id": tile_id,
        "fact": fact,
        "rumor": rumor,
        "confidence": confidence,
        "date": date,
        "observer_id": pack.id,
        "source": SCOUT_SOURCE,
    }
    if discovery is not None:
        event["discovery"] = discovery
    return event


def _pack_events(world: World, pack: Pack, date: SimDate) -> list[dict]:
    """Собрать наблюдения одной разведки, не читая и не меняя знание."""
    location_id = _location_tile_id(world, pack)
    if location_id is None or world.rng is None:
        return []
    route = {tile_id for tile_id in pack.route if tile_id in world.tiles}
    route.add(location_id)
    near = _ring(world, location_id, radius=1)
    ring = _ring(world, location_id, radius=2)
    events = [
        _event(
            pack,
            tile_id,
            date,
            True,
            1.0,
            discovery=_discovery_kind(world, tile_id),
        )
        for tile_id in sorted(route)
    ]
    for tile_id in sorted(ring - route):
        if tile_id in near:
            if world.rng.world.random() < observation_chance(pack, world, tile_id):
                events.append(
                    _event(
                        pack,
                        tile_id,
                        date,
                        True,
                        0.8,
                        discovery=_discovery_kind(world, tile_id),
                    )
                )
        else:
            events.append(
                _event(pack, tile_id, date, False, RUMOR_CONFIDENCE, rumor=True)
            )
    return events


def settle_reported_discoveries(world: World) -> list[Settlement]:
    """Создать поселения только из доставленных фактов разведки.

    Доставленный `Report` — единственный разрешающий сигнал. Слух без `fact`,
    наблюдение без `Report` и сценарный маркер сами по себе не создают двор.
    """
    delivered = sorted(
        (
            report
            for report in world.reports
            if report.source == SCOUT_SOURCE
            and report.subject_kind == "tile"
            and report.delivery_date <= world.clock.date
            and report.facts.get("fact") is True
        ),
        key=lambda report: (report.delivery_date, report.id),
    )
    created: list[Settlement] = []
    for report in delivered:
        tile = world.tiles.get(report.subject_id)
        if tile is None or tile.settlement_id is not None:
            continue
        if _discovery_kind(world, tile.id) is None:
            continue
        settlement_id = f"discovered_settlement_{tile.id.removeprefix('t_')}"
        household_id = f"discovered_household_{tile.id.removeprefix('t_')}"
        if settlement_id in world.settlements or household_id in world.households:
            continue
        settlement_stock = Stock(
            id=f"settlement:{settlement_id}",
            owner_kind="settlement",
            owner_id=settlement_id,
        )
        household_stock = Stock(
            id=f"household:{household_id}",
            owner_kind="household",
            owner_id=household_id,
        )
        adult_count = max(
            1,
            int(world.needs.birth_adults_required) if world.needs is not None else 1,
        )
        member_ids = [f"{household_id}_p{index}" for index in range(1, adult_count + 1)]
        for person_id in member_ids:
            world.persons[person_id] = Person(
                id=person_id,
                name=person_id,
                household_id=household_id,
                age_class="adult",
                curiosity=0.0,
                fear=0.0,
                health=1.0,
                location_tile_id=tile.id,
                age_months=240,
            )
        settlement = Settlement(
            id=settlement_id,
            name=f"Поселение {tile.id}",
            kind="farmstead",
            coord=axial_to_offset(int(tile.coord[0]), int(tile.coord[1])),
            household_ids=[household_id],
            stores_stock_id=settlement_stock.id,
            works_tiles=[],
        )
        household = Household(
            id=household_id,
            name=f"Двор {tile.id}",
            settlement_id=settlement_id,
            member_ids=member_ids,
            stock_id=household_stock.id,
            labor_days=float(adult_count) * ADULT_LABOR_DAYS,
            obligation_ids=[],
            hunger_days=0,
            arrears_days=0,
            mood=0.7,
            intent="stay",
            current_tile_id=tile.id,
        )
        world.stocks[settlement_stock.id] = settlement_stock
        world.stocks[household_stock.id] = household_stock
        world.settlements[settlement_id] = settlement
        world.households[household_id] = household
        tile.settlement_id = settlement_id
        date = world.clock.date
        world.ledger.external_in(
            household_stock,
            "grain",
            monthly_food_need(world, household),
            DISCOVERY_MATTER_REASON,
            DISCOVERY_MATTER_RULE,
            date,
        )
        world.ledger.external_in(
            household_stock,
            "axe",
            float(adult_count),
            DISCOVERY_MATTER_REASON,
            DISCOVERY_MATTER_RULE,
            date,
        )
        world.bump("rare_place_discoveries")
        created.append(settlement)
    return created


def observe_scout_packs(world: World, date: SimDate | None = None) -> list[dict]:
    """Наблюдать только активными `Pack purpose="scout"` и вернуть события месяца."""
    observed_on = date or world.clock.date
    events: list[dict] = []
    seen: set[tuple[str, str, int]] = set()
    for pack_id in sorted(world.packs):
        pack = world.packs[pack_id]
        if pack.status != "in_transit" or pack.purpose != "scout":
            continue
        for event in _pack_events(world, pack, observed_on):
            key = (
                event["observer_id"],
                event["tile_id"],
                event["date"].to_day_index(),
            )
            if key in seen:
                continue
            seen.add(key)
            events.append(event)
    events.sort(
        key=lambda event: (
            event["date"].to_day_index(),
            event["observer_id"],
            event["tile_id"],
        )
    )
    return events
