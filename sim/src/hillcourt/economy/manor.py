"""Манор: чем кормится право (домен, барщина, паёк, гэфоль, найм, посев).

Legal задаёт, кто МОЖЕТ и что ДОЛЖЕН (пресеты, бандлы, режимы земли, календарь
барщины `calendar_v0.yml`). Этот модуль переводит их условия в месячный тик:
сезонные трудодни барщины уходят в пул домена, домен снимает урожай в сток
замка, замок кормит пайки, дворы платят фиксированный гэфоль и засевают
гэфоль-акры, а свободный без земли нанимается за еду/пенс.

Трудодень не берётся из воздуха: списывается не больше, чем у двора есть.
Сезонную урожайность (выход на 1 оставшийся трудодень) задаёт `seasons.yml`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ..engine.manor import eased_days, manor_of_household, manor_stock, root_manor
from ..legal.calendar import (
    SLAVE_MONTH_DAYS,
    hire_demand,
    seasonal_labor_days,
    take_boon,
)
from ..ontology import Household, SimDate, Stock, Tile
from ..world import World
from .labor import apply_recipe, tool_yield_factor
from .needs import ADULT_LABOR_DAYS, monthly_food_need
from .seasons import demesne_yield

EPSILON = 1e-9
GRAIN = "grain"
SILVER = "silver"
MARTINMAS_MONTH = 11
MICHAELMAS_MONTH = 9
SOW_MONTHS = (10, 11)


@dataclass
class ManorConfig:
    """Параметры манора: наделы, спрос домена по сезонам, паёк, найм, посев."""

    holding_tiles_by_land_kind: dict[str, float]
    plot_batch_cap_per_tile: float
    demand_days_per_tile: dict[int, float]
    harvest_recipe: str
    board_grain_per_adult: dict[str, float]
    hire_wage_grain_per_day: float
    hire_wage_silver_per_day: float
    hire_min_castle_grain: float
    sow_grain_per_acre: float


def load_manor(path: str | Path) -> ManorConfig:
    """Прочитать manor.yml в строгий ManorConfig."""
    with Path(path).open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"manor.yml '{path}': ожидался словарь верхнего уровня")
    unknown = sorted(set(data) - {"version", "plots", "demesne", "board", "hire", "sow"})
    if unknown:
        raise ValueError(f"manor.yml '{path}': неизвестные разделы {unknown}")
    plots = data.get("plots") or {}
    demesne = data.get("demesne") or {}
    board = data.get("board") or {}
    hire = data.get("hire") or {}
    sow = data.get("sow") or {}
    return ManorConfig(
        holding_tiles_by_land_kind={
            str(k): float(v)
            for k, v in (plots.get("holding_tiles_by_land_kind") or {}).items()
        },
        plot_batch_cap_per_tile=float(plots.get("plot_batch_cap_per_tile", 4)),
        demand_days_per_tile={
            int(k): float(v) for k, v in (demesne.get("demand_days_per_tile") or {}).items()
        },
        harvest_recipe=str(demesne.get("harvest_recipe", "harvest_grain")),
        board_grain_per_adult={
            str(k): float(v)
            for k, v in (board.get("grain_per_adult_by_status") or {}).items()
        },
        hire_wage_grain_per_day=float(hire.get("wage_grain_per_day", 0.0)),
        hire_wage_silver_per_day=float(hire.get("wage_silver_per_day", 0.0)),
        hire_min_castle_grain=float(hire.get("min_castle_grain", 0.0)),
        sow_grain_per_acre=float(sow.get("grain_per_acre", 1.0)),
    )


def castle_stock(world: World) -> Stock:
    """Сток замка — лордов амбар (куда идёт гэфоль и урожай корня)."""
    return world.get_stock("settlement:" + world.player.court_settlement_id)


def _manor_demesne_fields(world: World, manor) -> list[Tile]:
    """Полевые клетки домена в книге конкретного манора."""
    out: list[Tile] = []
    for tid in manor.tile_ids:
        tile = world.tiles.get(tid)
        if tile is not None and tile.regime_id == "demesne" and tile.terrain == "field":
            out.append(tile)
    return sorted(out, key=lambda t: t.id)


def demesne_tiles(world: World) -> list[Tile]:
    """Клетки домена корневого манора (fallback — все доменные клетки мира)."""
    root = root_manor(world)
    if root is not None:
        source = [world.tiles[t] for t in root.tile_ids if t in world.tiles]
    else:
        source = list(world.tiles.values())
    return sorted((t for t in source if t.regime_id == "demesne"), key=lambda t: t.id)


def demesne_field_tiles(world: World) -> list[Tile]:
    """Пашня домена корня: только полевые клетки (усадьба-холм не пашется)."""
    return [t for t in demesne_tiles(world) if t.terrain == "field"]


def _preset(world: World, household: Household):
    return world.catalogs.legal_statuses.get(household.legal_status_id)


def _adults(world: World, household: Household) -> int:
    return sum(
        1
        for pid in household.member_ids
        if (p := world.persons.get(pid)) is not None and p.age_class == "adult"
    )


def _is_slave(world: World, household: Household) -> bool:
    preset = _preset(world, household)
    return bool(preset and preset.personal_status == "slave")


def _slave_days(world: World, household: Household) -> float:
    """Трудодни рабов внутри двора (раб — рот и руки лорда, без своего надела)."""
    slaves = sum(
        1
        for pid in household.member_ids
        if (p := world.persons.get(pid)) is not None and p.personal_status == "slave"
    )
    return float(slaves) * SLAVE_MONTH_DAYS


def _on_manor(world: World, household: Household) -> bool:
    """Двор числится в книге манора, а не в дальней деревне (у той свой уклад)."""
    settlement = world.settlements.get(household.settlement_id or "")
    if settlement is None or settlement.kind == "salt_village":
        return False
    return manor_of_household(world, household.id) is not None


def _is_root_household(world: World, household_id: str) -> bool:
    """Двор числится в корневом маноре (не у вложенного тэна)."""
    manor = manor_of_household(world, household_id)
    return manor is not None and manor.parent_manor_id is None


def _bundle(world: World, household: Household):
    preset = _preset(world, household)
    if preset is None:
        return None
    return world.catalogs.bundles.get(preset.obligation_bundle)


def duty_days_for(world: World, household: Household) -> float:
    """Сколько трудодней барщины двор должен в этот месяц (сезонный календарь Legal).

    Действие игрока `ease_week_work` урезает долг пика (не больше долга).
    """
    days = seasonal_labor_days(world, household, world.clock.month)
    if not _is_root_household(world, household.id):
        return days
    return max(0.0, days - eased_days(world, world.clock.month))


def _demand(world: World, month: int) -> float:
    manor = world.manor
    per_tile = manor.demand_days_per_tile.get(month, 0.0) if manor else 0.0
    return per_tile * len(demesne_field_tiles(world))


def _render_pool(world: World, caps: dict[str, float] | None = None) -> float:
    """Барщина и труд рабов → пул КАЖДОГО манора; вернуть пул корня.

    Дни двора идут в пул того манора, в чьей книге он числится: после
    пожалования тэна те же дни видны у тэна и исчезают из корня. Если задан
    `caps[manor.id]`, больше спроса не списываем — излишек остаётся двору на
    свой надел, а не уходит в `wasted_labor_days`.
    """
    pools: dict[str, float] = {}
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None or not _on_manor(world, household):
            continue
        manor = manor_of_household(world, hid)
        if manor is None:
            continue
        days = 0.0
        if _is_slave(world, household):
            days = household.labor_days
            household.labor_days = 0.0
            world.bump("slave_labor_days", days)
        else:
            slave_days = _slave_days(world, household)
            if slave_days > EPSILON:
                diverted = min(household.labor_days, slave_days)
                household.labor_days -= diverted
                days += diverted
                world.bump("slave_labor_days", diverted)
            duty = duty_days_for(world, household)
            if duty > EPSILON:
                if caps is not None and pools.get(manor.id, 0.0) >= caps.get(
                    manor.id, float("inf")
                ):
                    pass
                else:
                    rendered = min(household.labor_days, duty)
                    household.labor_days -= rendered
                    days += rendered
                    world.bump("corvee_days", rendered)
        pools[manor.id] = pools.get(manor.id, 0.0) + days
    for manor in world.manors.values():
        manor.demesne_labor_filled = pools.get(manor.id, 0.0)
    root = root_manor(world)
    return pools.get(root.id, 0.0) if root is not None else 0.0


def _render_boon(world: World, shortfall: float, force: bool = False) -> float:
    """Помочи (bene): лорд зовёт лишние дни в страду, до годового cap бандла.

    Только если домен не закрыт барщиной; `force` — игрок сам крикнул помочь
    (`engine/manor.call_boon`), тогда дни берутся и без недобора. Это и делает
    август «полной барщиной плюс помочами» — богатый домен и худой амбар виллана.
    """
    if shortfall <= EPSILON and not force:
        return 0.0
    pool = 0.0
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None or not _on_manor(world, household):
            continue
        if not _is_root_household(world, hid):
            continue
        preset = _preset(world, household)
        if preset is None or preset.personal_status == "slave":
            continue
        extra = take_boon(world, household, world.clock.month)
        if extra <= EPSILON:
            continue
        rendered = min(household.labor_days, extra)
        household.labor_days -= rendered
        pool += rendered
        world.bump("boon_days", rendered)
    return pool


def _spoil_buffer(world: World) -> float:
    """Множитель пайка против месячной порчи зерна (порядок фаз spoil→consume).

    Паёк приходит в сток двора в `phase_manor`, порча списывает часть до еды
    (`phase_spoil` идёт раньше `phase_consume`). Без буфера двор с полным
    амбаром получал бы ровно нужду и голодал на порчу. Буфер покрывает ставку
    порчи зерна, но не ниже 1.02; ставка пайка (unit/взрослого) не меняется.
    """
    rule = world.catalogs.goods.get(GRAIN)
    spoil = rule.spoil_per_month if rule is not None else 0.0
    return max(1.0 + spoil, 1.02)


def _board(world: World, date: SimDate) -> None:
    """Паёк раба/лорда из амбара ЕГО манора в сток двора (перевод, не создание).

    Корень кормит из замка, тэн — из `manor:<id>`; двор вне книги (соляной
    держатель) пайка не получает. Стол держателя покрывает весь его двор
    (взрослые + дети), а не только взрослых ртов: иначе двор лорда голодает
    при полном амбаре (гейт G2). Паёк идёт с буфером на порчу
    (`_spoil_buffer`), ставка `board_grain_per_adult` — на «рот-единицу».
    """
    manor = world.manor
    buffer = _spoil_buffer(world)
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None:
            continue
        book = manor_of_household(world, hid)
        if book is None:
            continue
        source = manor_stock(world, book)
        if source is None:
            continue
        per = manor.board_grain_per_adult.get(household.legal_status_id, 0.0)
        if per <= EPSILON:
            continue
        need = per * monthly_food_need(world, household) * buffer
        available = source.amounts.get(GRAIN, 0.0)
        give = min(available, need)
        if give <= EPSILON:
            continue
        world.ledger.transfer(
            source, world.get_stock(household.stock_id), GRAIN, give, "board", date
        )
        world.bump("board_grain", give)


def _hire(world: World, manor, pool: float, shortfall: float, date: SimDate) -> float:
    """Нанять свободных без земли (и лишние руки коттеров) за еду/пенс.

    Найм — по манору двора: корень платит из замка, тэн — из `manor:<id>`.
    Только если домен не закрыт барщиной и у амбара манора есть зерно/серебро.
    """
    config = world.manor
    source = manor_stock(world, manor)
    if source is None:
        return pool
    if hire_demand(world) == "low":
        shortfall = shortfall * 0.5
    for hid in sorted(world.households):
        if shortfall <= EPSILON:
            break
        household = world.households[hid]
        if household.left_at is not None:
            continue
        if manor_of_household(world, hid) is not manor:
            continue
        preset = _preset(world, household)
        if preset is None or _is_slave(world, household):
            continue
        if preset.land_relation == "landless":
            capacity = household.labor_days
        elif preset.land_kind == "cotter_plot":
            capacity = household.labor_days * 0.5
        else:
            continue
        days = min(capacity, shortfall)
        if days <= EPSILON:
            continue
        wage_grain = days * config.hire_wage_grain_per_day
        wage_silver = days * config.hire_wage_silver_per_day
        if source.amounts.get(GRAIN, 0.0) >= max(config.hire_min_castle_grain, wage_grain):
            world.ledger.transfer(
                source, world.get_stock(household.stock_id), GRAIN, wage_grain,
                "hire", date,
            )
        elif source.amounts.get(SILVER, 0.0) >= wage_silver:
            world.ledger.transfer(
                source, world.get_stock(household.stock_id), SILVER, wage_silver,
                "hire", date,
            )
        else:
            break
        household.labor_days -= days
        pool += days
        shortfall -= days
        world.bump("hired_days", days)
    return pool


def _gafol(world: World, date: SimDate) -> None:
    """Фиксированный гэфоль из бандла: натура на Мартинов день, пенс на Михайлов.

    Это не «доля всего добытого»: мера фиксирована бандлом Legal. Платят в амбар
    СВОЕГО манора: виллан корня — в замок, виллан тэна — в `manor:<id>`.
    """
    month = world.clock.month
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None:
            continue
        book = manor_of_household(world, hid)
        if book is None:
            continue
        dest = manor_stock(world, book)
        if dest is None:
            continue
        bundle = _bundle(world, household)
        if bundle is None:
            continue
        stock = world.get_stock(household.stock_id)
        if month == MARTINMAS_MONTH:
            term = bundle.terms.get("in_kind_martinmas")
            if term:
                amount = float(term.get("value", 0.0))
                take = min(stock.amounts.get(GRAIN, 0.0), amount)
                if take > EPSILON:
                    world.ledger.transfer(stock, dest, GRAIN, take, "gafol", date)
                    world.bump("gafol_grain", take)
        if month == MICHAELMAS_MONTH:
            term = bundle.terms.get("geld_michaelmas")
            if term:
                amount = float(term.get("value", 0.0))
                take = min(stock.amounts.get(SILVER, 0.0), amount)
                if take > EPSILON:
                    world.ledger.transfer(stock, dest, SILVER, take, "geld", date)
                    world.bump("geld_silver", take)


def _sow_demesne(world: World, date: SimDate) -> None:
    """Гэфоль-пахота: зерно из амбара виллана едет на поле ЕГО домена (transfer).

    Материя едет, не создаётся. Сеет в домен того манора, которому двор должен:
    виллан корня — в корневой домен, виллан тэна — в домен тэна.
    """
    if world.clock.month not in SOW_MONTHS:
        return
    config = world.manor
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None:
            continue
        book = manor_of_household(world, hid)
        if book is None:
            continue
        tiles = _manor_demesne_fields(world, book)
        if not tiles:
            continue
        bundle = _bundle(world, household)
        if bundle is None or "sow_demesne_acres" not in bundle.terms:
            continue
        acres = float(bundle.terms["sow_demesne_acres"].get("value", 0.0))
        need = acres * config.sow_grain_per_acre
        stock = world.get_stock(household.stock_id)
        take = min(stock.amounts.get(GRAIN, 0.0), need)
        if take <= EPSILON:
            continue
        target = tiles[0]
        world.ledger.transfer(
            stock, world.get_stock(target.standing_stock_id), GRAIN, take,
            "sow_demesne", date,
        )
        world.bump("sow_grain", take)


def _manor_worker(world: World, manor):
    """Двор, чьим инструментом пашется домен манора: корень — двор игрока, тэн — свой."""
    if manor.parent_manor_id is None:
        return world.households.get(world.player.household_id)
    person = world.persons.get(manor.holder_person_id)
    if person is None:
        return None
    return world.households.get(person.household_id)


def _work_demesne(world: World, date: SimDate) -> dict[str, float]:
    """Каждый манор пашет СВОЙ домен своим пулом; урожай — в свой амбар.

    Трудодни манора пашут только его клетки `regime:demesne`. Недобор режет
    урожай этого домена, а не чужого. Инструмент работника (плуг/лемех) множит
    и стоячую материю, и выход — проверка доступности обязана считать тем же
    множителем, что `apply_recipe`, иначе спишется больше, чем есть. Если дни
    есть, а доменных клеток нет — `wasted_labor_days` в статистике.
    """
    config = world.manor
    recipe = world.catalogs.recipes.get(config.harvest_recipe) if config else None
    worked_by: dict[str, float] = {}
    if recipe is None:
        return worked_by
    factor = demesne_yield(world, world.clock.month)
    for manor in sorted(world.manors.values(), key=lambda m: m.id):
        worked_by[manor.id] = 0.0
        worker = _manor_worker(world, manor)
        fields = _manor_demesne_fields(world, manor)
        stock = manor_stock(world, manor)
        pool = float(manor.demesne_labor_filled)
        if stock is None:
            continue
        if not fields:
            if pool > EPSILON:
                world.bump("wasted_labor_days", pool)
            continue
        if worker is None:
            continue
        combined = factor * tool_yield_factor(world, worker)
        demand = float(manor.demesne_labor_demand_this_month)
        budget = min(pool, demand)
        for tile in fields:
            tile_stock = world.get_stock(tile.standing_stock_id)
            while budget > EPSILON:
                need = recipe.draws_standing.get(GRAIN, 0.0) * combined
                standing = tile_stock.amounts.get(GRAIN, 0.0)
                standing_ok = 1.0 if need <= 0 else standing / need
                labor_ok = budget / recipe.labor_days if recipe.labor_days > 0 else 1.0
                scale = min(1.0, standing_ok, labor_ok)
                if scale <= 1e-4:
                    break
                before = stock.amounts.get(GRAIN, 0.0)
                apply_recipe(
                    world, worker, tile, recipe, scale, date,
                    output_stock=stock, yield_factor=factor,
                )
                used = recipe.labor_days * scale
                budget -= used
                worked_by[manor.id] += used
                world.bump("demesne_grain", stock.amounts.get(GRAIN, 0.0) - before)
    return worked_by


def _replenish_labor(world: World) -> None:
    """Вернуть двору месячный запас труда: взрослые и рабы дают по 20 трудодней.

    Без этого трудодни — не поток месяца, а исчерпаемый запас: барщина выедает
    его раз и навсегда. Труд не создаётся из статуса — он равен числу взрослых.
    """
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None:
            continue
        household.labor_days = float(_adults(world, household)) * ADULT_LABOR_DAYS


def _demand_per_tile(world: World, month: int) -> float:
    config = world.manor
    return config.demand_days_per_tile.get(month, 0.0) if config else 0.0


def manor_month(world: World, date: SimDate) -> None:
    """Один месяц манора: труд → барщина → паёк → найм → гэфоль/посев → урожай.

    Труд, спрос, найм и книга — по КАЖДОМУ манору: корень и тэн не смешивают
    стоки времени. Помочи — только корень (`ease_week_work`/`call_boon` тоже).
    """
    # Оброк племени (ADR 0064) — до фазы повинностей того же месяца: повинности
    # заводит/держит экономика племени, оплату и `arrears` делает
    # `phase_obligations` (движок не тронут).
    from .tribe import tribe_tribute_month

    tribe_tribute_month(world, date)
    if world.manor is None:
        return
    month = world.clock.month
    _replenish_labor(world)
    root = root_manor(world)
    demands: dict[str, float] = {}
    for manor in sorted(world.manors.values(), key=lambda m: m.id):
        demand = _demand_per_tile(world, month) * len(_manor_demesne_fields(world, manor))
        manor.demesne_labor_demand_this_month = demand
        demands[manor.id] = demand
    corvee_pool = _render_pool(world, caps=demands)  # пул корня до помочи и найма
    hired_before = world.stats.get("hired_days", 0.0)
    force_boon = world.stats.get("boon_called_month", 0.0) == float(month)
    root_pool = 0.0
    for manor in sorted(world.manors.values(), key=lambda m: m.id):
        demand = demands[manor.id]
        pool = float(manor.demesne_labor_filled)
        if root is not None and manor.id == root.id:
            pool += _render_boon(world, max(0.0, demand - pool), force=force_boon)
        shortfall = max(0.0, demand - pool)
        pool = _hire(world, manor, pool, shortfall, date)
        manor.demesne_labor_filled = pool
        if root is not None and manor.id == root.id:
            root_pool = pool
    world.stats["boon_called_month"] = 0.0
    _gafol(world, date)
    _sow_demesne(world, date)
    worked_by = _work_demesne(world, date)
    _board(world, date)  # кормим после урожая: амбар тэна уже наполнен
    root_demand = root.demesne_labor_demand_this_month if root is not None else 0.0
    root_worked = worked_by.get(root.id, 0.0) if root is not None else 0.0
    world.manor_log.append(
        {
            "date": str(date),
            "demand": root_demand,
            "corvee_pool": corvee_pool,
            "pool": root_pool,
            "worked": root_worked,
            "shortfall": max(0.0, root_demand - root_worked),
            "hired": world.stats.get("hired_days", 0.0),
            "hired_month": world.stats.get("hired_days", 0.0) - hired_before,
            "wasted": world.stats.get("wasted_labor_days", 0.0),
        }
    )
