"""Детальный рост стоячей материи по индексу ОБРАБАТЫВАЕМОЙ земли.

Индекс — не «где стоят поселения», а **где реально работают**. Раньше он строился
из координат поселений и их ``works_tiles``, и на ``start_stand`` это давало
``('t_00_00', 't_01_00', 't_03_00', 't_03_01')``: из семи пашен в индексе была
одна — домен ``t_01_00``. Шесть наделов, которые дворы пашут каждую весну, не
росли никогда: надел ``t_02_00`` — 8.0 зерна стартового запаса, съеденных за
зиму при ``plot_yield`` 0.15, и с четвёртого месяца клетка пуста навеки, тогда как
домен копит 788.9 зерна к 120-му месяцу. Правильный ход игрока (выдать надел,
отдать клетку двору, сменить режим) не давал ровно ничего.

Индекс выводится из данных мира и **пересчитывается каждый месяц**
(``engine/tick.py::phase_growth``), поэтому смена того, кто и что обрабатывает,
попадает в рост без чьего-либо invalidation: забыть нельзя, потому что выводить
нечего — состояния индекса в мире нет, есть только вывод.

Четыре основания, все из данных (ADR 0132: сценарий — данные, а не особый код):

1. **Место поселения и его ``works_tiles``** — община: жернова, соляные и
   болотные угодья предприятия. Было и осталось.
2. **Индивидуальный надел по праву** (``Right.kind != "common"``) — то, чего не
   было. Надел без гекса в индексе не пашется ничем: `economy/labor.py`
   `_feeding_tiles` отдаёт двору его надел, а расти на клетке нечему.
3. **Домен в книге манора** — клетка, режим которой работает за трудодни
   (``land_regimes.yml::requires_labor_days``), то есть ровно те, что пашет пул
   домена. Книга корня на `start_stand` — это все 12 гексов, поэтому основание
   должно быть **режимом**, а не «наличием в книге», иначе индекс стал бы всей
   картой.
4. **Дикая земля под рукой у обрабатываемой** (лес и топь, соседи по шести
   направлениям) — `cut_firewood`/`cut_wood`/`cut_peat`/`mine_iron` работают
   СОСЕДНЮЮ дикую клетку, а свой надел двора диким не является. Лес, до
   которого не дойти, не обрабатывается и расти не должен: иначе природа
   набивает клетку, из которой никто никогда не возьмёт ничего.

Грубая (coarse) карта по-прежнему не обходится: индекс растёт от числа
обрабатываемых гексов, а не от размера карты — на `v0_barony_100` это 253 клетки
из 10 000 (было 226). Проверяется `test_barony_100.TestLazyGrowth`.

**Цена пересчёта, замерено, чтобы её не выдавали за бесплатную.** Старый вывод
стоил 0.111 мс на `v0_barony_100`, новый — **24.9 мс** на тот же мир: основание
«домен» 5.6 мс (одна сборка книги в множество и один проход по клеткам; вложенный
обход `книга × клетка` стоил 16.07 мс и заменён), основание «дикая земля» 13.8 мс
(это общий хелпер соседства `engine/hexgrid.py`, 225 клеток × 6 направлений). То
есть около 2.5 % секунды на месячный тик самого большого мира. Старый код был
быстрым потому, что был неправильным: он не смотрел на наделы. Соседство
раздваивать нельзя (ADR 0177, `engine/hexgrid.py` — единственный источник
смежности), и оптимизировать дальше нечем, не меняя закон.
"""

from __future__ import annotations

from ..world import World
from .hexgrid import neighbor_ids
from .terrain import WILD_TERRAINS
from .yield_law import demesne_field_factor, field_yield_factor, growth_season_factor


def worked_land_tile_ids(world: World) -> set[str]:
    """Гексы, которые реально обрабатывают: вывод оснований 1-4 по порядку.

    Промежуточная величина, отдельная от индекса: нужна и `index_growth_tiles`
    (порядок и проверка ссылок), и основанию 4 (соседи найденных гексов).
    Публичная — чтобы тест и следующий агент проверяли одно и то же множество, а
    не два независимо посчитанных.
    """
    worked: set[str] = set()
    # 1. Место поселения и общинные угодья.
    for settlement in world.settlements.values():
        worked.add(f"t_{settlement.coord[0]:02d}_{settlement.coord[1]:02d}")
        worked.update(settlement.works_tiles)
    # 2. Индивидуальный надел по праву. `kind == "common"` — общий доступ, а не
    #    держание (ADR 0060): он не в надел ни для кого и работу не открывает.
    for right in world.rights.values():
        if right.kind != "common":
            worked.add(right.tile_id)
    # 3. Домен: клетка, чей режим работает за трудодни. Основание — РЕЖИМ, а не
    #    книга манора: книга корня по построению содержит всю карту.
    #    Книга собирается в множество ОДИН раз, а потом один проход по клеткам:
    #    так тот же закон стоит втрое дешевле, чем вложенный обход
    #    `книга × клетка` (замерено на `v0_barony_100`: 16.07 мс против 4.98 мс на
    #    построение индекса при 10 000 гексов).
    regimes = getattr(world.catalogs, "land_regimes", {})
    labour_regimes = {
        regime_id
        for regime_id, regime in regimes.items()
        if getattr(regime, "requires_labor_days", False)
    }
    if labour_regimes:
        book: set[str] = set()
        for manor in world.manors.values():
            book.update(manor.tile_ids)
        worked.update(
            tile_id
            for tile_id, tile in world.tiles.items()
            if tile_id in book and tile.regime_id in labour_regimes
        )
    # 4. Дикая земля под рукой: рецепты дикой земли берут СОСЕДНЮЮ клетку.
    #    Именно соседнюю с ОБРАБАТЫВАЕМОЙ, а не «соседнюю с дикой»: дивая
    #    клетка ничьим наделом не является, значит работать из неё некому, и шаг
    #    «лес окрается в лес» не открывает ничего. Один шаг от не-диких клеток.
    for tile_id in sorted(worked):
        tile = world.tiles.get(tile_id)
        if tile is None or tile.terrain in WILD_TERRAINS:
            continue
        for neighbour in neighbor_ids(world, tile):
            if world.tiles[neighbour].terrain in WILD_TERRAINS:
                worked.add(neighbour)
    return worked


def index_growth_tiles(world: World) -> tuple[str, ...]:
    """Построить детерминированный индекс гексов детального роста.

    В индекс входит **обрабатываемая земля** (`worked_land_tile_ids`): место и
    угодья поселений, индивидуальные наделы по праву, домен и дикая земля под
    рукой у них. Порядок зависит только от содержимого мира, поэтому повторная
    загрузка даёт тот же индекс, а одинаковый мир — одинаковый результат (И-6).
    """
    tile_ids = worked_land_tile_ids(world)
    missing = sorted(tile_id for tile_id in tile_ids if tile_id not in world.tiles)
    if missing:
        raise ValueError(f"Индекс роста ссылается на отсутствующие гексы: {missing}")
    return tuple(sorted(tile_ids))


def run_detailed_growth(world: World, season_multiplier: float) -> int:
    """Внести календарный прирост только в индексированные гексы.

    Возвращает число получивших материю гексов. Правила и ограничения те же,
    что у полного календарного роста; меняется только множество обходимых
    гексов.     Материя по-прежнему проходит только через ``external_in``.
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
        season = growth_season_factor(
            world, rule_id, world.clock.month, float(season_multiplier)
        )
        cap = rule.params.get("cap_per_tile")
        for tile_id in world.growth_tile_ids:
            tile = world.tiles[tile_id]
            if terrain is not None and tile.terrain != terrain:
                continue
            section_factor = (
                field_yield_factor(world, tile_id)
                if good == "grain" and terrain == "field"
                else 1.0
            )
            if good == "grain" and terrain == "field":
                # Норма выхода клетки домена (`grow_grain.params.demesne_base_yield`)
                # множится и в стоячую материю, и в выход: harvest > growth, иначе
                # снятый потолок урожая стал бы потолком посева (ADR 0137 п. 2).
                section_factor *= demesne_field_factor(world, tile_id, rule.params)
            amount = (
                float(rule.params.get("amount", 0.0))
                * season
                * float(getattr(tile, "resource_productivity", 1.0))
                * section_factor
            )
            grown = amount
            if cap is not None:
                current = world.get_stock(tile.standing_stock_id).amounts.get(good, 0.0)
                cap_amount = float(cap) * section_factor
                grown = min(grown, max(0.0, cap_amount - current))
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
