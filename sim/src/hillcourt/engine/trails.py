"""Тропы от ходьбы: износ пути, уровни, затухание, топтание, метки.

Этап троп (проект World, ADR 0054 п.1): жители и возы топчут землю физикой
ходьбы, не приказом (И-2: вес на возу/отряде, не на человеке). Строеная дорога
— только `tile.road` через стройку (`engine/roadworks.py`), автоматом никогда;
вода и `tile.road` вне системы (износ там всегда 0).

Состояние — `Tile.trail_wear` (поле онтологии, владелец Implementer):
пороги `TRAIL_WEAR_TRAIL` (3.0 — тропа) / `TRAIL_WEAR_DIRT` (12.0 — грунтовка),
затухание `TRAIL_DECAY_PER_MONTH` (0.5/мес без ходьбы). Топтание материю
не двигает (числа пути, не зерно): дельта 0. Дневного контура нет (И-4):
воз топчет маршрут разом по прибытии (телепорт `eta` — завершённая ходьба),
житель — свою клетку в месяц флага `traveling`.

Каждое рождение тропы/грунтовки метится для Info (весть `eye_from_hill` —
не здесь, её рождает Info своим кодом): счётчики `trails_born`/`dirt_born`
и штампы месяца `trail_born_<tile>`/`dirt_born_<tile>` в `world.stats`
(`year * 12 + month`). Без пересечения порога метки нет.
"""

from __future__ import annotations

from ..world import World
from .terrain import (
    TRAIL_COST_MULT,
    TRAIL_WEAR_DIRT,
    TRAIL_WEAR_TRAIL,
    trail_level_for,
)

TRAIL_DECAY_PER_MONTH: float = 0.5

TRAVELER_TREAD_WEIGHT: float = 1.0

PACK_TREAD_WEIGHTS: dict[str, float] = {
    "caravan": 4.0,
    "party": 2.0,
    "household_move": 2.0,
}


def _month_stamp(world: World) -> float:
    """Штамп текущего месяца для меток (`year * 12 + month`)."""
    return float(world.clock.year * 12 + world.clock.month)


def _excluded(world: World, tile_id: str) -> bool:
    """Клетка вне системы троп: вода или строеная дорога."""
    tile = world.tiles.get(tile_id)
    if tile is None:
        return True
    return tile.terrain == "water" or tile.road


def tread_tile(world: World, tile_id: str, weight: float) -> int | None:
    """Притоптать клетку весом; вернуть выросший уровень (1/2) или None.

    Вода и строеная дорога не топчутся. Без пересечения порога вверх —
    тихое накопление без метки.
    """
    if weight <= 0.0 or _excluded(world, tile_id):
        return None
    tile = world.tiles[tile_id]
    before = trail_level_for(tile.trail_wear)
    tile.trail_wear = tile.trail_wear + weight
    after = trail_level_for(tile.trail_wear)
    if after <= before:
        return None
    stamp = _month_stamp(world)
    if after >= 1 and before < 1:
        world.bump("trails_born")
        world.stats[f"trail_born_{tile_id}"] = stamp
    if after >= 2 and before < 2:
        world.bump("dirt_born")
        world.stats[f"dirt_born_{tile_id}"] = stamp
    return after


def decay_trails(world: World) -> None:
    """Затухание троп: −0.5 износа всем нетоптанным; вода/дорога — в ноль.

    Меток не ставит (затухание — не событие) и ничего не рождает.
    """
    for tile_id in sorted(world.tiles):
        tile = world.tiles[tile_id]
        if tile.terrain == "water" or tile.road:
            tile.trail_wear = 0.0
        elif tile.trail_wear > 0.0:
            tile.trail_wear = max(0.0, tile.trail_wear - TRAIL_DECAY_PER_MONTH)


def pack_tread_weight(pack) -> float:
    """Вес прохода воза/отряда по виду; река — 0.

    Речной воз (`send_river`) идёт водой: конвенция — origin в `route`
    не входит (см. `docs/03_ontology.md`, поле `Pack.route`), такой воз
    землю не топчет. Неизвестный вид — 0 (не выдумываем).
    """
    route = list(pack.route or [])
    if pack.origin_tile_id not in route:
        return 0.0
    return float(PACK_TREAD_WEIGHTS.get(pack.kind, 0.0))


def tread_arrivals(world: World, pack_ids: list[str]) -> int:
    """Притоптать маршруты сошедших с пути возов; вернуть число проходов.

    Воз, ушедший из `in_transit` (дошёл или сгинул — шёл всё равно),
    топчет каждую клетку своего маршрута весом вида. Вода и строеные
    дороги пропускаются (`tread_tile`).
    """
    passes = 0
    for pack_id in sorted(pack_ids):
        pack = world.packs.get(pack_id)
        if pack is None or pack.status == "in_transit":
            continue
        weight = pack_tread_weight(pack)
        if weight <= 0.0:
            continue
        for tile_id in pack.route:
            if tile_id in world.tiles:
                tread_tile(world, tile_id, weight)
        passes += 1
    return passes


def tread_travelers(world: World) -> int:
    """Притоптать клетки жителей-ходоков (`traveling`, вес 1.0).

    Флаг ставит труд (`economy/labor.py`: `travel_adjacent` — ходьба вместо
    поля); здесь только физика: живая ходьба топчет свою клетку. Приказы
    (И-2) тут ни при чём — ни один приказ не читается.
    """
    trodden = 0
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None or not household.traveling:
            continue
        tread_tile(world, household.current_tile_id, TRAVELER_TREAD_WEIGHT)
        trodden += 1
    return trodden


def trail_summary(world: World) -> dict[str, int]:
    """Сколько клеток на каждом уровне пути (для сводки runner и тестов)."""
    summary = {"none": 0, "trail": 0, "dirt": 0}
    for tile in world.tiles.values():
        level = trail_level_for(tile.trail_wear)
        if level >= 2:
            summary["dirt"] += 1
        elif level == 1:
            summary["trail"] += 1
        else:
            summary["none"] += 1
    return summary
