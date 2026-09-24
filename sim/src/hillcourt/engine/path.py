"""Маршрут по стоимости клеток (Дейкстра, только для приказов и обозов-задела).

Ход по карте — не прямая и не телепорт: `find_path` возвращает список клеток
от origin (включительно) до цели (включительно) и стоимость в ЧАСАХ
(`entry_hours`, ADR 0071/0070: тайл — место, движение — часы, сутки = 24 ч).
Вызывается на приказ (`engine/march.py`), а не каждый день на каждого человека
(И-4, И-10): дневной контур есть только у `Pack` в пути, месячный тик идёт как
шёл.

Знание ≠ истина клетки (И-3): `known=None` — полный расчёт по миру (свои
возницы землю знают); `known=<множество>` — приказ игрока: неизвестные клетки
непроходимы (кроме самой цели — в неизвестное идут наугад, ценой террейна),
поэтому «лучший путь» по невидимой земле игроку недоступно. Память дорог —
`memory_roads`: клетки-броды/дороги из уже дошедших `Pack` (их `route`
запомнен), они проходимы и вне `known`. Почта (`info/`, `news/`) не тронута:
`known_tiles` читает только доставленные `Report`, а не `Tile`/`Stock`.
"""

from __future__ import annotations

import heapq
import math

from ..news.propagation import delivered_reports
from ..world import World
from .hexgrid import neighbor_ids, tile_id_at
from .terrain import (
    DAYS_PER_MONTH,
    HOURS_PER_DAY,
    IMPASSABLE,
    entry_hours,
    entry_cost,
    get_profile,
    trail_level_for,
)

TILE_KIND = "tile"
ROUTE_KIND = "route"


def _coord_index(world: World) -> dict[tuple[int, int], str]:
    """Карта-решетка: аксиальная координата → id клетки (топология, не истина)."""
    return {
        (int(tile.coord[0]), int(tile.coord[1])): tile.id
        for tile in world.tiles.values()
    }


def neighbours(world: World, tile_id: str) -> list[str]:
    """Соседи клетки по гекс-сетке (6 направлений), существующие в мире, по id.

    Соседство — только `engine/hexgrid.py` (ADR 0071): локальных переборов
    координат в коде нет. Порядок детерминированный — по `HEX_DIRECTIONS`.
    """
    tile = world.tiles.get(tile_id)
    if tile is None:
        return []
    return neighbor_ids(world, tile)


def known_tiles(world: World, as_of=None) -> set[str]:
    """Клетки, о которых у игрока есть доставленные известия.

    Читаются только `Report` (`delivery_date ≤ as_of`): `subject_kind="tile"`
    даёт саму клетку, `subject_kind="route"` вида `"<a>-><b>"` — концы
    запомненного маршрута. Плюс клетка двора игрока (свой двор виден
    напрямую, `eye_from_hill`). Состояния клеток и стоков не читаются.
    """
    date = as_of or world.clock.date
    known: set[str] = set()
    for report in delivered_reports(world, date):
        if report.subject_kind == TILE_KIND and report.subject_id in world.tiles:
            known.add(report.subject_id)
        elif report.subject_kind == ROUTE_KIND:
            for end in str(report.subject_id).split("->"):
                if end in world.tiles:
                    known.add(end)
    court = world.settlements.get(world.player.court_settlement_id)
    if court is not None:
        home = world.tiles.get(tile_id_at(int(court.coord[0]), int(court.coord[1])))
        if home is not None:
            known.add(home.id)
    return known


def remembered_road_tiles(world: World) -> set[str]:
    """Клетки из маршрутов уже дошедших `Pack`: дороги из памяти.

    Приказ игрока может вести по запомненной дороге и через неизвестное:
    возницы уже ходили этим путём. Читаются только `route` дошедших
    (`status="arrived"`) и на клетках их маршрута — строеная дорога
    (`tile.road`) либо грунтовка, вытоптанная до `TRAIL_WEAR_DIRT`: возница
    помнит грунт как дорогу. Тропа (ниже порога) не помнится — помнится
    только то, по чему ездили. Террейны, стоки и опасности не читаются.
    """
    remembered: set[str] = set()
    for pack in world.packs.values():
        if pack.status != "arrived":
            continue
        for tile_id in pack.route:
            tile = world.tiles.get(tile_id)
            if tile is None:
                continue
            if tile.road or trail_level_for(tile.trail_wear) >= 2:
                remembered.add(tile_id)
    return remembered


def _enterable(
    world: World,
    tile_id: str,
    profile_id: str,
    known: set[str] | None,
    memory_roads: set[str],
    destination_id: str,
) -> float:
    """Цена входа в клетку с учётом тумана; `IMPASSABLE` — хода нет."""
    if known is not None:
        if tile_id != destination_id and tile_id not in known and tile_id not in memory_roads:
            return IMPASSABLE
    tile = world.tiles[tile_id]
    return entry_hours(
        profile_id,
        tile.terrain,
        tile.road,
        tile.ford,
        tile.bridge,
        trail_level_for(tile.trail_wear),
    )


def find_path(
    world: World,
    origin_id: str,
    destination_id: str,
    profile_id: str = "foot",
    known: set[str] | None = None,
    memory_roads: set[str] | None = None,
) -> tuple[list[str], float] | None:
    """Дешёвый маршрут от клетки до клетки для профиля; None — пути нет.

    Возвращает `(route, hours)`: `route` — список id от origin (включительно)
    до цели (включительно), `hours` — сумма ЧАСОВ входа (`entry_hours`, ADR
    0071). Нет пути (река без брода, стена тумана) — None, а не прямая.
    Детерминизм: очередь `(часы, id)`, соседи по возрастанию id — один мир на
    один seed (И-6). Алгоритм Дейкстра прежний; сменилась единица стоимости,
    порядок маршрутов не изменился (часы = дни × константа профиля).
    """
    get_profile(profile_id)
    if origin_id not in world.tiles or destination_id not in world.tiles:
        raise ValueError(f"Нет клетки '{origin_id}' или '{destination_id}'")
    if origin_id == destination_id:
        return [origin_id], 0.0
    roads = set(memory_roads) if memory_roads is not None else set()
    best: dict[str, float] = {origin_id: 0.0}
    prev: dict[str, str] = {}
    queue: list[tuple[float, str]] = [(0.0, origin_id)]
    while queue:
        hours, current = heapq.heappop(queue)
        if hours > best[current]:
            continue
        if current == destination_id:
            break
        for nxt in neighbours(world, current):
            step = _enterable(world, nxt, profile_id, known, roads, destination_id)
            if step >= IMPASSABLE:
                continue
            total = hours + step
            if total < best.get(nxt, math.inf):
                best[nxt] = total
                prev[nxt] = current
                heapq.heappush(queue, (total, nxt))
    if destination_id not in best:
        return None
    route = [destination_id]
    while route[-1] != origin_id:
        route.append(prev[route[-1]])
    route.reverse()
    return route, best[destination_id]


def travel_days(hours: float) -> int:
    """Срок похода в СУТКАХ от часов: сутки = 24 часа, минимум 1 (ADR 0071).

    Часы плавающие, сутки целые: `ceil(hours/24)`. 100 гексов поля пешком —
    65 часов → 3 суток; месяц (30 суток) — производная величина для логов.
    """
    return max(1, math.ceil(float(hours) / HOURS_PER_DAY))


def travel_hours(days: float) -> float:
    """Обратный перевод: сутки в часы (для фуража/расчётов по дням)."""
    return float(days) * HOURS_PER_DAY


def travel_months(days: float) -> int:
    """Срок похода в месяцах, производная от суток: месяц — 30 суток, мин. 1.

    В логах игроку остаётся как производная «срок/месяц» рядом с часами и
    сутками; единственной истиной времени она больше не является (ADR 0071).
    """
    return max(1, math.ceil(float(days) / DAYS_PER_MONTH))
