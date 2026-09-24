"""Сборка мира из сценария: карта, дворы, склады, повинности.

Секции YAML: карта/поселения/дворы/склады/опасности/`rights`/`tribes`/`marks`/
скрипт. Вид поселения (`Settlement.kind`, в т.ч. `native_village` — ADR 0060) и
права (`Right`, в т.ч. `kind=common` — общий доступ) берутся из данных сценария,
без ручных атрибутов в коде. Маркеры вида (`marks:`) — только данные (ADR 0065):
тик их не пишет и не читает. Племенные дворы (`free_landless`) в книгу
корневого манора не входят: `manor_id` остаётся None.

Карта сценария остаётся прямоугольной легендой (`col`/`row`, как в данных);
`Tile.coord` — аксиальная пара `(q, r)` гекс-сетки (ADR 0071, перевод в
`engine/hexgrid.py`), а `tile.id` — прежний `t_<col>_<row>`, чтобы YAML и
тесты не ехали. `Settlement.coord` хранит `[col, row]` карты.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

import yaml

from .catalogs import load_catalogs
from .economy.actions import load_actions
from .economy.decisions import plan_next_month
from .economy.manor import load_manor
from .economy.needs import load_needs, monthly_food_need
from .economy.seasons import load_seasons
from .engine.clock import Clock
from .engine.growth import index_growth_tiles
from .engine.hexgrid import axial_is_neighbor, offset_to_axial
from .engine.rng import RngStreams
from .ledger import Ledger
from .legal.bundles import materialize_obligations
from .legal.calendar import load_calendar
from .ontology import (
    TRIBE_STANCES,
    Hazard,
    Household,
    Manor,
    Obligation,
    Person,
    Right,
    Settlement,
    Stock,
    Tile,
    Tribe,
)
from .world import World


@dataclass
class Player:
    """Игрок как держатель права: без прямых приказов людям (И-2)."""

    court_settlement_id: str
    household_id: str
    rent_share: float = 0.1


def _find_repo_root(scenario_path: Path) -> Path:
    """Подняться от файла сценария до каталога с AGENTS.md."""
    current = scenario_path.resolve().parent
    while True:
        if (current / "AGENTS.md").exists():
            return current
        if current.parent == current:
            raise ValueError(
                f"Не найден корень репозитория (AGENTS.md) от {scenario_path}"
            )
        current = current.parent


def _tile_id(x: int, y: int) -> str:
    return f"t_{x:02d}_{y:02d}"


_TERRAINS = frozenset(
    {"hill", "field", "pasture", "forest", "marsh", "heath", "salt_flat", "ruin", "water"}
)
_RURAL_HOUSEHOLD_CAP = 5
_CITY_HOUSEHOLD_CAP = 20


def _point(value: object, label: str) -> tuple[int, int]:
    """Разобрать сценарную точку ``[col, row]``."""
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{label}: точка должна быть [col, row], получено {value!r}")
    return int(value[0]), int(value[1])


def _point_many(values: object, label: str) -> list[tuple[int, int]]:
    """Разобрать список сценарных точек ``[col, row]``."""
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError(f"{label}: ожидался список точек")
    return [_point(value, label) for value in values]


def _map_cells(map_data: dict) -> list[list[str]]:
    """Развернуть прямоугольную карту или её декларативные области в сетку."""
    if "rows" in map_data:
        legend = map_data.get("legend") or {}
        rows = map_data.get("rows") or []
        if not rows:
            raise ValueError("Карта: rows пуст")
        width = len(rows[0])
        for y, row in enumerate(rows):
            if not isinstance(row, str) or len(row) != width:
                raise ValueError(
                    f"Карта: строка {y} длиной {len(row)}, а строка 0 — {width}: "
                    "сетка должна быть прямоугольной"
                )
        cells: list[list[str]] = []
        for y, row in enumerate(rows):
            decoded: list[str] = []
            for x, char in enumerate(row):
                if char not in legend:
                    raise ValueError(
                        f"Карта: знак '{char}' в строке {y} не входит в legend "
                        f"{sorted(legend)}"
                    )
                terrain = str(legend[char])
                if terrain not in _TERRAINS:
                    raise ValueError(f"Карта: неизвестный terrain '{terrain}'")
                decoded.append(terrain)
            cells.append(decoded)
        return cells

    try:
        width = int(map_data["width"])
        height = int(map_data["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Карта без rows требует width и height") from exc
    default = str(map_data.get("default", "heath"))
    if width <= 0 or height <= 0 or default not in _TERRAINS:
        raise ValueError(f"Карта: некорректный размер или default={default!r}")
    cells = [[default for _ in range(width)] for _ in range(height)]
    for region in map_data.get("regions") or []:
        terrain = str(region["terrain"])
        if terrain not in _TERRAINS:
            raise ValueError(f"Карта: неизвестный terrain '{terrain}'")
        left, top, right, bottom = (int(value) for value in region["rect"])
        if not (0 <= left <= right < width and 0 <= top <= bottom < height):
            raise ValueError(f"Карта: прямоугольник вне карты {region['rect']}")
        for y in range(top, bottom + 1):
            for x in range(left, right + 1):
                cells[y][x] = terrain
    for entry in map_data.get("tiles") or []:
        x, y = _point(entry.get("at"), "Карта: tiles.at")
        terrain = str(entry["terrain"])
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError(f"Карта: точка {entry['at']} вне карты")
        if terrain not in _TERRAINS:
            raise ValueError(f"Карта: неизвестный terrain '{terrain}'")
        cells[y][x] = terrain
    return cells


def load_river_network(data: dict, tiles: dict[str, Tile]) -> set[str]:
    """Проверить ``rivers`` и превратить её ``flow_order`` в terrain ``water``.

    Метаданные остаются данными сценария и в мир не копируются. Проверяются
    порядок, реальное шестисоседство, исток, устье, притоки и выход наружу.
    """
    rivers = list(data.get("rivers") or [])
    if not rivers:
        return set()
    by_id: dict[str, dict] = {}
    network: set[str] = set()
    for river in rivers:
        rid = str(river["id"])
        if rid in by_id:
            raise ValueError(f"Река '{rid}' объявлена дважды")
        by_id[rid] = river
        flow = [_point(value, f"Река '{rid}': flow_order") for value in river["flow_order"]]
        if len(flow) < 2:
            raise ValueError(f"Река '{rid}': flow_order короче двух точек")
        if len(set(flow)) != len(flow):
            raise ValueError(f"Река '{rid}': flow_order содержит повтор")
        if _point(river["source"], f"Река '{rid}': source") != flow[0]:
            raise ValueError(f"Река '{rid}': source не совпадает с началом потока")
        if _point(river["mouth"], f"Река '{rid}': mouth") != flow[-1]:
            raise ValueError(f"Река '{rid}': mouth не совпадает с концом потока")
        for index, (before, after) in enumerate(zip(flow, flow[1:]), start=1):
            before_axial = offset_to_axial(*before)
            after_axial = offset_to_axial(*after)
            if not axial_is_neighbor(before_axial, after_axial):
                raise ValueError(
                    f"Река '{rid}', шаг {index}: {list(before)} → {list(after)} "
                    "не является соседством шестиугольной сетки"
                )
        exit_data = river.get("exit") or {}
        if exit_data:
            if _point(exit_data.get("from"), f"Река '{rid}': exit.from") != flow[-1]:
                raise ValueError(f"Река '{rid}': exit.from не совпадает с устьем")
            if not str(exit_data.get("destination", "")):
                raise ValueError(f"Река '{rid}': пустой выход к соседнему баронству")
        for x, y in flow:
            tid = _tile_id(x, y)
            if tid not in tiles:
                raise ValueError(f"Река '{rid}': нет клетки '{tid}'")
            network.add(tid)
    for rid, river in by_id.items():
        for tributary_id in river.get("tributaries") or []:
            tributary_id = str(tributary_id)
            if tributary_id not in by_id:
                raise ValueError(f"Река '{rid}': нет притока '{tributary_id}'")
            junction = _point(by_id[tributary_id]["mouth"], f"Приток '{tributary_id}': mouth")
            if junction not in {
                _point(value, f"Река '{rid}': flow_order") for value in river["flow_order"]
            }:
                raise ValueError(
                    f"Приток '{tributary_id}': устье {list(junction)} не входит в поток '{rid}'"
                )
    for tid in network:
        tiles[tid].terrain = "water"
    for tid in network:
        if tiles[tid].terrain != "water":
            raise ValueError(f"Река: клетка '{tid}' не стала water")
    return network


def _expand_household_groups(data: dict) -> list[dict]:
    """Развернуть компактные группы дворов в полный список сценарных записей."""
    entries = [dict(entry) for entry in (data.get("households") or [])]
    group_known = {
        "id_prefix",
        "settlement_id",
        "name",
        "count",
        "adults",
        "children",
        "elders",
        "slaves",
        "legal_status",
        "starting_food_months",
        "livestock_every",
        "livestock",
    }
    for group in data.get("household_groups") or []:
        unknown = sorted(set(group) - group_known)
        if unknown:
            raise ValueError(f"Household group: неизвестные поля {unknown}")
        count = int(group["count"])
        prefix = str(group["id_prefix"])
        if count <= 0:
            raise ValueError(f"Household group '{prefix}': count должен быть положительным")
        for index in range(1, count + 1):
            entry = {
                key: value
                for key, value in group.items()
                if key not in {"id_prefix", "count", "name", "starting_food_months", "livestock_every", "livestock"}
            }
            entry["id"] = f"{prefix}_{index:03d}"
            entry["name"] = f"{group.get('name', 'Двор')} {index:03d}"
            entry["_starting_food_months"] = float(group.get("starting_food_months", 0.0))
            entry["_livestock_every"] = int(group.get("livestock_every", 0))
            entry["_livestock"] = dict(group.get("livestock") or {})
            entries.append(entry)
    ids = [str(entry["id"]) for entry in entries]
    if len(ids) != len(set(ids)):
        duplicates = sorted({hid for hid in ids if ids.count(hid) > 1})
        raise ValueError(f"Дворы объявлены дважды: {duplicates}")
    return entries


def _household_tile_ids(settlements: list[dict]) -> dict[str, list[str]]:
    """Проверить списки жилых гексов поселений и перевести их в id."""
    layouts: dict[str, list[str]] = {}
    for entry in settlements:
        sid = str(entry["id"])
        points = entry.get("household_tiles") or []
        tile_ids = [_tile_id(*_point(point, f"Поселение '{sid}': household_tiles")) for point in points]
        if len(tile_ids) != len(set(tile_ids)):
            raise ValueError(f"Поселение '{sid}': household_tiles содержит повтор")
        layouts[sid] = tile_ids
    return layouts


def _household_placements(
    settlements: list[dict], household_entries: list[dict]
) -> dict[str, str]:
    """Распределить дворы по ``household_tiles`` с проверкой городского и сельского капа."""
    layouts = _household_tile_ids(settlements)
    kinds = {str(entry["id"]): str(entry["kind"]) for entry in settlements}
    counts: dict[str, int] = {sid: 0 for sid in kinds}
    placements: dict[str, str] = {}
    for entry in household_entries:
        sid = str(entry["settlement_id"])
        if sid not in kinds:
            raise ValueError(f"Двор '{entry['id']}': нет поселения '{sid}'")
        tile_ids = layouts[sid]
        if not tile_ids:
            at = _point(next(s for s in settlements if str(s["id"]) == sid)["at"], f"Поселение '{sid}': at")
            tile_ids = [_tile_id(*at)]
        placements[str(entry["id"])] = tile_ids[counts[sid] % len(tile_ids)]
        counts[sid] += 1
    for entry in settlements:
        sid = str(entry["id"])
        tile_ids = layouts[sid]
        if not tile_ids:
            continue
        cap = _CITY_HOUSEHOLD_CAP if kinds[sid] == "hill_court" else _RURAL_HOUSEHOLD_CAP
        if counts[sid] > cap * len(tile_ids):
            raise ValueError(
                f"Поселение '{sid}': {counts[sid]} дворов не помещаются в "
                f"{len(tile_ids)} гексов по капу {cap}"
            )
    return placements


def _new_stock(stock_id: str, owner_kind: str, owner_id: str) -> Stock:
    return Stock(id=stock_id, owner_kind=owner_kind, owner_id=owner_id, amounts={})


def _load_tribes(data: dict, settlements: dict[str, Settlement]) -> dict[str, Tribe]:
    """Загрузить племена из YAML с биекцией на `native_village` (ADR 0064).

    Ровно один `Tribe` на одну `Settlement.kind=native_village` и ни одного
    племени без такой деревни: два племени на деревню или деревня без племени —
    отказ загрузки. `tribute_grain`/`muster_kits` — данные сценария с дефолтом
    0.0 (числа заполняет Economist, здесь не выдумываются). `stance` проверяется
    по закрытому списку `TRIBE_STANCES`.
    """
    tribes: dict[str, Tribe] = {}
    native_ids = {sid for sid, s in settlements.items() if s.kind == "native_village"}
    claimed: dict[str, str] = {}
    for entry in data.get("tribes") or []:
        tid = str(entry["id"])
        sid = str(entry["settlement_id"])
        if sid not in settlements:
            raise ValueError(f"Племя '{tid}': нет поселения '{sid}'")
        if settlements[sid].kind != "native_village":
            raise ValueError(
                f"Племя '{tid}': поселение '{sid}' не native_village "
                f"(kind={settlements[sid].kind})"
            )
        if sid in claimed:
            raise ValueError(
                f"Два племени на деревню '{sid}': '{claimed[sid]}' и '{tid}'"
            )
        stance = str(entry.get("stance", "independent"))
        if stance not in TRIBE_STANCES:
            raise ValueError(f"Племя '{tid}': стойка '{stance}' вне {list(TRIBE_STANCES)}")
        claimed[sid] = tid
        tribes[tid] = Tribe(
            id=tid,
            name=str(entry["name"]),
            stance=stance,
            settlement_id=sid,
            tribute_grain=float(entry.get("tribute_grain", 0.0)),
            muster_kits=float(entry.get("muster_kits", 0.0)),
        )
    missing = sorted(native_ids - set(claimed))
    if missing:
        raise ValueError(f"Деревня native_village без племени: {missing}")
    return tribes


def load_scenario(path: str | Path, seed: int | None = None) -> World:
    """Загрузить сценарий и собрать готовый к прогону World.

    `seed` переопределяет `seed` сценария: эффективный seed идёт ВЕЗДЕ —
    в черты людей (`random.Random`), в потоки RNG и в `World.seed`. Иначе
    `--seed 42` давал бы гибрид: двор от одного seed, случайность от другого.
    """
    scenario_path = Path(path)
    repo_root = _find_repo_root(scenario_path)
    with scenario_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    effective_seed = int(seed) if seed is not None else int(data["seed"])

    catalog_map = dict(data.get("catalogs") or {})
    base_map = {
        key: value
        for key, value in catalog_map.items()
        if key not in ("needs", "actions_household", "manor", "calendar", "seasons")
    }
    catalogs = load_catalogs(repo_root, base_map or None)

    needs_path = repo_root / catalog_map.get("needs", "design/catalogs/needs.yml")
    actions_path = repo_root / catalog_map.get(
        "actions_household", "design/catalogs/actions_household.yml"
    )
    manor_path = repo_root / catalog_map.get("manor", "design/catalogs/manor.yml")
    needs = load_needs(needs_path)
    household_actions = load_actions(actions_path)
    calendar_path = repo_root / catalog_map.get(
        "calendar", "design/catalogs/calendar_v0.yml"
    )
    calendar = load_calendar(calendar_path)
    manor = load_manor(manor_path)
    seasons_path = repo_root / catalog_map.get(
        "seasons", "design/catalogs/seasons.yml"
    )
    seasons = load_seasons(seasons_path)

    stocks: dict[str, Stock] = {}
    for sink_id in ("waste", "eaten", "processing"):
        stocks[f"sink:{sink_id}"] = _new_stock(f"sink:{sink_id}", "sink", sink_id)

    map_data = data["map"]
    cells = _map_cells(map_data)
    tiles: dict[str, Tile] = {}
    for y, row in enumerate(cells):
        for x, terrain in enumerate(row):
            tid = _tile_id(x, y)
            stock = _new_stock(f"tile:{tid}", "tile", tid)
            stocks[stock.id] = stock
            regime = "reserved_wood" if terrain == "forest" else "waste"
            tiles[tid] = Tile(
                id=tid,
                coord=offset_to_axial(x, y),
                terrain=terrain,
                standing_stock_id=stock.id,
                hazard_ids=[],
                regime_id=regime,
            )

    load_river_network(data, tiles)

    # Переправы и дороги поверх карты (зона ВОДА): `fords`/`bridges`/`roads` —
    # списки [x, y]. Брод — клетка воды, где сухая переправа пересекает реку.
    for key, attr in (("fords", "ford"), ("bridges", "bridge"), ("roads", "road")):
        for point in data.get(key) or []:
            tid = _tile_id(int(point[0]), int(point[1]))
            if tid not in tiles:
                raise ValueError(f"Секция '{key}': нет клетки '{tid}'")
            setattr(tiles[tid], attr, True)

    # Маркеры земли баронства из YAML (ADR 0065, вид только, И-5): поля на `Tile`
    # под них нет — данные стоят через `getattr`, тик маркеры не пишет и не читает.
    # Булевы маркеры (`LAND_MARKS`) → True; строковый `dwelling` → значение из
    # `DWELLING_FORMS` (уровень дома: tent_earth/house/multi_storey).
    from .engine.tile_view import DWELLING_FORMS, LAND_MARKS

    valued_marks = ("dwelling",)
    for entry in data.get("marks") or []:
        tid = _tile_id(int(entry["at"][0]), int(entry["at"][1]))
        if tid not in tiles:
            raise ValueError(f"Маркер: нет клетки '{tid}'")
        mark = str(entry["mark"])
        if mark in valued_marks:
            value = str(entry.get("value", ""))
            if value not in DWELLING_FORMS:
                raise ValueError(
                    f"Маркер '{mark}' на '{tid}': значение '{value}' вне "
                    f"{sorted(DWELLING_FORMS)}"
                )
            setattr(tiles[tid], mark, value)
            continue
        if mark not in LAND_MARKS:
            raise ValueError(
                f"Маркер '{mark}' на '{tid}' вне {list(LAND_MARKS)} и не {list(valued_marks)}"
            )
        if "value" in entry:
            raise ValueError(
                f"Маркер '{mark}' на '{tid}': булево — value не принимается"
            )
        setattr(tiles[tid], mark, True)

    settlements: dict[str, Settlement] = {}
    settlement_layouts = _household_tile_ids(data["settlements"])
    for entry in data["settlements"]:
        coord = _point(entry.get("at"), f"Поселение '{entry['id']}': at")
        sid = str(entry["id"])
        home_tile = _tile_id(*coord)
        if home_tile not in tiles:
            raise ValueError(f"Поселение '{sid}': нет клетки '{home_tile}'")
        stock = _new_stock(f"settlement:{sid}", "settlement", sid)
        stocks[stock.id] = stock
        works_tiles = [
            _tile_id(*_point(point, f"Поселение '{sid}': works_tiles"))
            for point in (entry.get("works_tiles") or [])
        ]
        if len(works_tiles) != len(set(works_tiles)):
            raise ValueError(f"Поселение '{sid}': works_tiles содержит повтор")
        missing = [tid for tid in works_tiles if tid not in tiles]
        if missing:
            raise ValueError(f"Поселение '{sid}': нет клеток works_tiles {missing}")
        home_terrain_value = entry.get("home_terrain")
        home_terrain = str(home_terrain_value) if home_terrain_value is not None else None
        if home_terrain is not None and home_terrain not in _TERRAINS:
            raise ValueError(f"Поселение '{sid}': неизвестный home_terrain '{home_terrain}'")
        layout_tiles = settlement_layouts[sid]
        quarter_tiles = [
            _tile_id(*point)
            for point in _point_many(
                entry.get("quarter_tiles"), f"Поселение '{sid}': quarter_tiles"
            )
        ]
        for tid in [home_tile, *layout_tiles, *quarter_tiles]:
            if tid not in tiles:
                raise ValueError(f"Поселение '{sid}': нет клетки '{tid}'")
            if home_terrain is not None and (tid != home_tile or home_terrain != "water"):
                tiles[tid].terrain = home_terrain
        settlements[sid] = Settlement(
            id=sid,
            name=entry["name"],
            kind=entry["kind"],
            coord=coord,
            household_ids=[],
            stores_stock_id=stock.id,
            works_tiles=works_tiles,
        )
        for tid in [home_tile, *layout_tiles]:
            tiles[tid].settlement_id = sid
            tiles[tid].regime_id = (
                "demesne" if entry["kind"] == "hill_court" else "tenement"
            )
        for works_tile in works_tiles:
            tiles[works_tile].regime_id = (
                "demesne" if entry["kind"] == "hill_court" else "tenement"
            )

    household_entries = _expand_household_groups(data)
    household_placements = _household_placements(data["settlements"], household_entries)
    seed = effective_seed
    persons: dict[str, Person] = {}
    households: dict[str, Household] = {}
    for entry in household_entries:
        hid = str(entry["id"])
        sid = str(entry["settlement_id"])
        settlement = settlements[sid]
        tile_id = household_placements[hid]
        stock = _new_stock(f"household:{hid}", "household", hid)
        stocks[stock.id] = stock

        adults = int(entry.get("adults", 0))
        children = int(entry.get("children", 0))
        slaves = int(entry.get("slaves", 0))
        status_id = str(entry.get("legal_status", "free_landless"))
        preset = catalogs.legal_statuses.get(status_id)
        member_ids: list[str] = []
        prng = random.Random(f"{seed}:persons:{hid}")
        elders = int(entry.get("elders", 0))
        total = adults + children + elders
        for n in range(1, total + 1):
            pid = f"{hid}_p{n}"
            if n <= adults:
                age_class = "adult"
                age_months = 240
            elif n <= adults + children:
                age_class = "child"
                age_months = 12
            else:
                age_class = "elder"
                age_months = 600
            persons[pid] = Person(
                id=pid,
                name=pid,
                household_id=hid,
                age_class=age_class,
                curiosity=float(entry.get("curiosity", prng.random())),
                fear=float(entry.get("fear", prng.random())),
                health=1.0,
                location_tile_id=tile_id,
                age_months=age_months,
            )
            member_ids.append(pid)

        # Раб не имеет своего двора-агента: это рот и руки при дворе лорда.
        for n in range(1, slaves + 1):
            pid = f"{hid}_s{n}"
            persons[pid] = Person(
                id=pid,
                name=pid,
                household_id=hid,
                age_class="adult",
                curiosity=prng.random(),
                fear=prng.random(),
                health=1.0,
                location_tile_id=tile_id,
                personal_status="slave",
                land_relation="landless",
                obligation_bundle="slave_ration",
                age_months=240,
            )
            member_ids.append(pid)

        households[hid] = Household(
            id=hid,
            name=entry["name"],
            settlement_id=sid,
            member_ids=member_ids,
            stock_id=stock.id,
            labor_days=float(adults + slaves) * 20.0,
            obligation_ids=[],
            hunger_days=0,
            arrears_days=0,
            mood=float(entry.get("mood", 0.7)),
            intent="stay",
            current_tile_id=tile_id,
            main_action=str(entry.get("main_action", "work_plot")),
            minor_action=str(entry.get("minor_action", "idle_repair")),
            legal_status_id=status_id,
            personal_status=preset.personal_status if preset else "free",
            land_relation=preset.land_relation if preset else "landless",
            obligation_bundle=preset.obligation_bundle if preset else None,
            holding_scale=manor.holding_tiles_by_land_kind.get(
                preset.land_kind if preset else "waste", 1.0
            ),
        )
        if settlement.kind != "hill_court" and preset is not None:
            tiles[tile_id].regime_id = preset.land_kind
        settlement.household_ids.append(hid)

    for key, amounts in (data.get("starting_stocks") or {}).items():
        if key not in stocks:
            raise ValueError(f"Стартовый склад '{key}' не соответствует стоку")
        for good, amount in amounts.items():
            if amount:
                stocks[key].add(good, float(amount))

    hazards: dict[str, Hazard] = {}
    for n, entry in enumerate(data.get("initial_hazards") or [], start=1):
        coord = (int(entry["at"][0]), int(entry["at"][1]))
        tid = _tile_id(*coord)
        hazard_id = f"haz_{n:03d}"
        kind = entry["kind"]
        hazards[hazard_id] = Hazard(
            id=hazard_id,
            kind=kind,
            tile_id=tid,
            intensity=float(entry["intensity"]),
            active=True,
            # Стартовые опасности — условие сценария, не спавн по правилу:
            # вида `kind` в каталоге `spawn_rules` нет, метка честно пуста.
            spawn_rule_id=None,
            population=float(entry.get("population", entry["intensity"])),
            satiety=float(entry.get("satiety", 0.4)),
        )
        tiles[tid].hazard_ids.append(hazard_id)

    player_data = data.get("player") or {}
    player = Player(
        court_settlement_id=player_data.get("court_settlement_id", "hill_court"),
        household_id=player_data.get("household_id", "hh_court"),
        rent_share=float(player_data.get("rent_share", 0.1)),
    )

    obligations: dict[str, Obligation] = {}

    clock_data = data.get("clock") or {}
    clock = Clock(
        year=int(clock_data.get("start_year", 1)),
        month=int(clock_data.get("start_month", 1)),
        day=1,
        months_per_year=int(clock_data.get("months_per_year", 12)),
    )

    # Права из YAML: существующая `Right`, без ручных атрибутов в коде (ADR 0060).
    # Носитель — `holder_household_id` (двор) либо `holder_settlement_id`
    # (община: id поселения в поле `holder_household_id` — отдельного поля группы
    # в онтологии нет, а `Right.kind=common` общим доступом не является
    # индивидуальным держанием ни для кого). Вид права (`kind`) любой из
    # каталога: `common` — общий доступ, `tenure`/`grazing` — индивидуальные.
    rights: dict[str, Right] = {}
    for entry in data.get("rights") or []:
        rid = str(entry["id"])
        if rid in rights:
            raise ValueError(f"Право '{rid}' объявлено дважды")
        tile_id = _tile_id(int(entry["at"][0]), int(entry["at"][1]))
        if tile_id not in tiles:
            raise ValueError(f"Право '{rid}': нет клетки '{tile_id}'")
        holder = entry.get("holder_household_id") or entry.get("holder_settlement_id")
        if not holder:
            raise ValueError(
                f"Право '{rid}': нужен holder_household_id или holder_settlement_id"
            )
        rights[rid] = Right(
            id=rid,
            holder_household_id=str(holder),
            tile_id=tile_id,
            kind=str(entry["kind"]),
            granted_date=clock.date,
            rent_share=float(entry.get("rent_share", 0.0)),
        )

    world = World(
        scenario_id=data["id"],
        seed=seed,
        clock=clock,
        tiles=tiles,
        settlements=settlements,
        households=households,
        persons=persons,
        stocks=stocks,
        ledger=Ledger(),
        hazards=hazards,
        packs={},
        reports=[],
        player=player,
        catalogs=catalogs,
        initial_matter=0.0,
        obligations=obligations,
        rng=RngStreams.from_seed(seed),
        needs=needs,
        household_actions=household_actions,
        stats={},
        calendar=calendar,
        manor=manor,
        seasons=seasons,
        script=list(data.get("script") or []),
        rights=rights,
        tribes=_load_tribes(data, settlements),
    )
    world.growth_tile_ids = index_growth_tiles(world)
    for entry in household_entries:
        household = world.households[str(entry["id"])]
        food_months = float(entry.get("_starting_food_months", 0.0))
        if food_months > 0.0:
            world.get_stock(household.stock_id).add(
                "grain", monthly_food_need(world, household) * food_months
            )
        every = int(entry.get("_livestock_every", 0))
        if every > 0 and int(str(entry["id"]).rsplit("_", 1)[-1]) % every == 0:
            stock = world.get_stock(household.stock_id)
            for good, amount in (entry.get("_livestock") or {}).items():
                stock.add(str(good), float(amount))
    # Корневой манор игрока: книга земли. Соль — клетки того же манора, не фьеф;
    # соляные держатели — равные, в число тяглых дворов книги не входят.
    # Племенная деревня (`Settlement.kind=native_village`, ADR 0060) — не книга
    # лорда: её дворы `free_landless`, `manor_id` остаётся None.
    non_root_settlements = {"salt_village", "native_village"}
    court_household = world.households.get(player.household_id)
    holder_person_id = next(
        (
            pid
            for pid in (court_household.member_ids if court_household else [])
            if world.persons[pid].age_class == "adult"
        ),
        player.household_id,
    )
    root_manor = Manor(
        id="manor_hill",
        holder_person_id=holder_person_id,
        stock_id="settlement:" + player.court_settlement_id,
        parent_manor_id=None,
        upward_bundle="crown_stub",
        tile_ids=sorted(world.tiles),
        tile_regimes={tid: world.tiles[tid].regime_id for tid in sorted(world.tiles)},
        household_ids=sorted(
            hid
            for hid, hh in world.households.items()
            if (settlements.get(hh.settlement_id or "") is not None)
            and settlements[hh.settlement_id].kind not in non_root_settlements
        ),
    )
    world.manors[root_manor.id] = root_manor
    world.player_manor_id = root_manor.id
    for hid in root_manor.household_ids:
        world.households[hid].manor_id = root_manor.id
    # Место стола корня: зал + ближние клетки под холмом (engine/seat.py).
    # Дальние выселки местом не становятся; у тэна место пусто.
    try:
        from .engine.seat import init_seat_for_root

        init_seat_for_root(world)
    except ImportError:
        pass
    limits = player_data.get("thegn_grant_limit") or {}
    world.stats["thegn_limit_tiles"] = float(limits.get("tiles", 3))
    world.stats["thegn_limit_households"] = float(limits.get("households", 3))
    world.stats["thegn_limit_grants"] = float(limits.get("grants", 1))

    for hid in sorted(world.households):
        materialize_obligations(world, world.households[hid])
    world.ledger.capture_initial(world.total_matter())
    world.initial_matter = world.total_matter()
    plan_next_month(world)
    return world
