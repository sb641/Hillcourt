"""Ленивый детальный рост стоячей материи по индексу поселений.

Wilds, лес и поля вне ``works_tiles`` остаются coarse-данными. Фаза обходится
только ``World.growth_tile_ids``; подключение к ``engine/tick.py`` выполняется
отдельной строкой после freeze текущей фазы.
"""

from __future__ import annotations

from ..world import World


def index_growth_tiles(world: World) -> tuple[str, ...]:
    """Построить детерминированный индекс гексов детального роста.

    В индекс входят координаты поселений и их ``works_tiles``. Порядок зависит
    только от содержимого мира, поэтому повторная загрузка даёт тот же индекс.
    """
    tile_ids = {
        tile_id
        for settlement in world.settlements.values()
        for tile_id in (
            f"t_{settlement.coord[0]:02d}_{settlement.coord[1]:02d}",
            *settlement.works_tiles,
        )
    }
    missing = sorted(tile_id for tile_id in tile_ids if tile_id not in world.tiles)
    if missing:
        raise ValueError(f"Индекс роста ссылается на отсутствующие гексы: {missing}")
    return tuple(sorted(tile_ids))


def run_detailed_growth(world: World, season_multiplier: float) -> int:
    """Внести календарный прирост только в индексированные гексы.

    Возвращает число получивших материю гексов. Правила и ограничения те же,
    что у полного календарного роста; меняется только множество обходимых
    гексов. Материя по-прежнему проходит только через ``external_in``.
    """
    changed_tiles: set[str] = set()
    for rule_id in sorted(world.catalogs.spawn_rules):
        rule = world.catalogs.spawn_rules[rule_id]
        if rule.kind != "calendric" or rule.target != "good":
            continue
        terrain = rule.params.get("terrain")
        good = rule.params.get("good")
        if good is None:
            continue
        amount = float(rule.params.get("amount", 0.0)) * float(season_multiplier)
        cap = rule.params.get("cap_per_tile")
        for tile_id in world.growth_tile_ids:
            tile = world.tiles[tile_id]
            if terrain is not None and tile.terrain != terrain:
                continue
            grown = amount
            if cap is not None:
                current = world.get_stock(tile.standing_stock_id).amounts.get(good, 0.0)
                grown = min(grown, max(0.0, float(cap) - current))
            if grown <= 0.0:
                continue
            world.ledger.external_in(
                world.get_stock(tile.standing_stock_id),
                str(good),
                grown,
                reason=rule_id,
                rule_id=rule_id,
                date=world.clock.date,
            )
            changed_tiles.add(tile_id)
    return len(changed_tiles)
