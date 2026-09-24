"""Экономика: труд двора, рецепты и износ инструмента.

Производство идёт только через рецепты каталога. Действие двора (`actions_household.yml`)
разрешает набор рецептов; двор тратит труд, пока хватает стоячей материи и входов.
Материя не берётся из ничего: всё либо переводится, либо входит по правилу появления.
"""

from __future__ import annotations

from ..engine.hexgrid import neighbor_ids
from ..legal.regimes import has_access_to_communal_tile, is_communal_collection_tile
from ..ontology import Household, Recipe, SimDate, Stock, Tile
from ..world import World
from .livestock import PLOUGH_RECIPES, can_hold_horse, draft_plough_factor
from .needs import food_months
from .seasons import plot_yield

PROCESSING_STOCK_ID = "sink:processing"
WASTE_STOCK_ID = "sink:waste"
EPSILON = 1e-9
FORAGE_TERRAIN = ("forest", "heath", "marsh")
FORAGE_RECIPE = "forage_wild"

NO_TOOL_YIELD = 1.0
TOOL_YIELD_FACTORS = {"wooden_plough": 1.05, "iron_share": 1.15}
TOOL_PRIORITY = ("iron_share", "wooden_plough")


def tool_yield_factor(world: World, household: Household) -> float:
    """Множитель выхода от инструмента, лежащего в стоке двора.

    Нет инструмента — 1.0 (базовая линия не меняется); деревянный плуг даёт
    прибавку меньше железного лемеха. Если лежит и то, и другое, берётся
    лучший по `TOOL_PRIORITY`. Инструмент — материя: появиться он может только
    переводом (`grant_tool`) или рецептом, не из воздуха.
    """
    stock = world.get_stock(household.stock_id)
    for good in TOOL_PRIORITY:
        if stock.amounts.get(good, 0.0) > EPSILON:
            return TOOL_YIELD_FACTORS[good]
    return NO_TOOL_YIELD


def _recipe_yield_factor(
    world: World, household: Household, recipe: Recipe, season_yield: float
) -> float:
    """Итоговый множитель рецепта: сезон надела × инструмент, только для сбора.

    Множатся и стоячая материя, и выход (но не труд) — только у рецептов без
    входов, которые черпают с земли (`draws_standing`). У превращений с
    `inputs` множитель всегда 1.0, иначе масса перестала бы сходиться.
    """
    if recipe.inputs or not recipe.draws_standing:
        return 1.0
    return season_yield * tool_yield_factor(world, household)


def apply_recipe(
    world: World,
    household: Household,
    tile: Tile,
    recipe: Recipe,
    scale: float,
    date: SimDate,
    output_stock: Stock | None = None,
    yield_factor: float = 1.0,
) -> None:
    """Провести партию рецепта через sink:processing без создания материи.

    `yield_factor` — сезонная урожайность; вместе с инструментом двора она
    множит стоячую материю и выход (но не труд), поэтому выход на 1 трудодень
    меняется, а массовый баланс держится. Применяется только к рецептам без
    `inputs` (чистый сбор с земли).
    """
    processing = world.get_stock(PROCESSING_STOCK_ID)
    waste = world.get_stock(WASTE_STOCK_ID)
    household_stock = world.get_stock(household.stock_id)
    tile_stock = world.get_stock(tile.standing_stock_id)
    dst = output_stock if output_stock is not None else household_stock
    factor = _recipe_yield_factor(world, household, recipe, yield_factor)

    for good in sorted(recipe.inputs):
        amount = recipe.inputs[good] * scale
        if amount > 0:
            world.ledger.transfer(
                household_stock, processing, good, amount, recipe.id, date
            )

    for good in sorted(recipe.draws_standing):
        amount = recipe.draws_standing[good] * scale * factor
        if amount > 0:
            world.ledger.transfer(
                tile_stock, processing, good, amount, recipe.id, date
            )

    allowed = set(recipe.outputs) | set(recipe.loss)
    for good in sorted(recipe.outputs):
        amount = recipe.outputs[good] * scale * factor
        if amount > 0:
            world.ledger.emit(
                processing, dst, good, amount, recipe.id, date, allowed
            )

    for good in sorted(recipe.loss):
        amount = recipe.loss[good] * scale * factor
        if amount > 0:
            world.ledger.emit(processing, waste, good, amount, recipe.id, date, allowed)

    if abs(processing.total()) >= 1e-6:
        raise AssertionError(
            f"Рецепт '{recipe.id}' не сбалансирован: остаток обработки "
            f"{processing.total()}"
        )


def _tile_id(x: int, y: int) -> str:
    return f"t_{x:02d}_{y:02d}"


def own_tiles(world: World, household: Household) -> list[Tile]:
    """Земли двора: поселение, держания из works_tiles и клетки по праву (Right).

    Пожалованная полоса (`grant_tenement`) даёт двору `Right` на клетку; без
    этого право есть, а труда нет — двор не пашет надел. `Right.kind=common` —
    общий доступ, а не индивидуальное держание (ADR 0060): такие клетки в
    наделе двора не стоят (община принадлежит всем, а не первому двору).
    Клетки по праву добавляются без дублей и в детерминированном порядке.
    """
    tiles: list[Tile] = []
    if household.settlement_id and household.settlement_id in world.settlements:
        settlement = world.settlements[household.settlement_id]
        first = world.tiles.get(_tile_id(*settlement.coord))
        if first is not None:
            tiles.append(first)
        for tid in settlement.works_tiles:
            tile = world.tiles.get(tid)
            if tile is not None and tile not in tiles:
                tiles.append(tile)
    else:
        tile = world.tiles.get(household.current_tile_id)
        if tile is not None:
            tiles.append(tile)
    right_tiles = [
        world.tiles[right.tile_id]
        for right in world.rights.values()
        if right.holder_household_id == household.id
        and right.kind != "common"
        and right.tile_id in world.tiles
    ]
    for tile in sorted(right_tiles, key=lambda t: t.id):
        if tile not in tiles:
            tiles.append(tile)
    return tiles


def homestead_tiles(world: World, household: Household) -> list[Tile]:
    """Двор (усадьба): где стоят рецепты `place: settlement` (мельница, свинарник)."""
    if household.settlement_id and household.settlement_id in world.settlements:
        settlement = world.settlements[household.settlement_id]
        tile = world.tiles.get(_tile_id(*settlement.coord))
        return [tile] if tile is not None else []
    tile = world.tiles.get(household.current_tile_id)
    return [tile] if tile is not None else []


def _held_tile_ids(world: World, household: Household) -> set[str]:
    """Клетки, которые двор ДЕРЖИТ: усадьба/домашний надел и клетки по праву.

    Только эти клетки двор делит с соседями по ёмкости: у каждой свой
    `Right` (или это его усадьба). `Right.kind=common` — общий доступ, не
    держание (ADR 0060): ни в надел, ни в кап. Общинные `works_tiles` поселения
    сюда не входят — их дворы обрабатывают сообща, но поштучного держания нет.
    """
    held: set[str] = set()
    if household.settlement_id and household.settlement_id in world.settlements:
        settlement = world.settlements[household.settlement_id]
        held.add(_tile_id(*settlement.coord))
    else:
        held.add(household.current_tile_id)
    for right in world.rights.values():
        if right.holder_household_id == household.id and right.kind != "common":
            held.add(right.tile_id)
    return held


def _feeding_tiles(world: World, household: Household) -> list[Tile]:
    """Надел двора: только клетки, которые его кормят (не домен лорда)."""
    out: list[Tile] = []
    for tile in own_tiles(world, household):
        regime = world.catalogs.land_regimes.get(tile.regime_id)
        if regime is not None and regime.feeds_household:
            out.append(tile)
    return out


def _adjacent_tiles(world: World, household: Household) -> list[Tile]:
    """Соседние клетки двора (шесть направлений гекс-сетки, ADR 0071).

    Соседство — общий хелпер `engine/hexgrid.py`; локальных переборов нет.
    """
    tile = world.tiles.get(household.current_tile_id)
    if tile is None:
        return []
    out: list[Tile] = [
        world.tiles[tile_id]
        for tile_id in neighbor_ids(world, tile)
        if tile_id in world.tiles
    ]
    return sorted(out, key=lambda t: t.id)


def _tiles_for_recipe(world: World, household: Household, recipe: Recipe) -> list[Tile]:
    """Клетки, где двор вправе применить рецепт.

    Сбор (`forage_wild`) — только с соседних клеток. Рецепты двора (`place:
    settlement`) — на усадьбе. Рецепты поля (`place: tile`) — только на своём
    наделе, который двор кормит (домен лорда сюда не входит). Рубка леса
    (`cut_firewood`, `cut_wood`) — на своём наделе или в соседнем лесу: бревно
    должно быть достижимо без спавна из воздуха. Соседние клетки сбора/рубки —
    явное исключение механики «рука в своём наделе», а не communal-доступ:
    право на общинный сбор дают только `works_tiles`/`Right.kind=common`
    (ADR 0077). Пустой `requires_terrain` — «любая подходящая клетка».
    """
    if recipe.id == FORAGE_RECIPE:
        candidates = _adjacent_tiles(world, household)
    elif recipe.place == "settlement":
        candidates = homestead_tiles(world, household)
    elif recipe.id in ("cut_firewood", "cut_wood", "mine_iron", "cut_peat"):
        candidates = _feeding_tiles(world, household)
        for tile in _adjacent_tiles(world, household):
            if tile.terrain in ("forest", "marsh") and tile not in candidates:
                candidates.append(tile)
    else:
        candidates = _feeding_tiles(world, household)
    if not recipe.requires_terrain:
        return candidates
    return [tile for tile in candidates if tile.terrain in recipe.requires_terrain]


def _communal_collection_shared(
    world: World,
    household: Household,
    recipe: Recipe,
    tile: Tile,
    held: set[str],
) -> bool:
    """    Делит ли двор ёмкость ОБЩИННОЙ клетки сбора по праву доступа (J3).

    Сбор с общинной клетки (режим `waste`/`reserved_wood`, не держимой двором)
    ведёт общий счёт партий НА КЛЕТКУ: ёмкость (`_tile_batch_cap`, 4) делят
    все дворы с доступом, а не каждый берёт полную виргату. Держимые клетки
    сюда не входят — их ёмкость делится ветвью G3. Право доступа — только
    `works_tiles` поселения либо `Right.kind=common` (ADR 0077: соседство
    само по себе правом не является). Материю правило не создаёт — только
    лимит труда.
    """
    if tile.id in held:
        return False
    if recipe.inputs or not recipe.draws_standing:
        return False
    if not is_communal_collection_tile(tile):
        return False
    return has_access_to_communal_tile(world, household, tile)


def _ratio(demands: dict[str, float], stock_amounts: dict[str, float]) -> float:
    best = 1.0
    for good in sorted(demands):
        needed = demands[good]
        if needed <= 0:
            continue
        available = stock_amounts.get(good, 0.0)
        best = min(best, available / needed)
    return best


def _recipe_order(world: World, allowed: set[str]) -> list[Recipe]:
    """Порядок рецептов: сначала еда, затем прочее; внутри — по id."""

    def produces_food(recipe: Recipe) -> int:
        for good in recipe.outputs:
            rule = world.catalogs.goods.get(good)
            if rule is not None and rule.edible:
                return 0
        return 1

    return sorted(
        (r for r in world.catalogs.recipes.values() if r.id in allowed),
        key=lambda r: (produces_food(r), r.id),
    )


def _apply_set(
    world: World,
    household: Household,
    recipes: list[Recipe],
    budget: float,
    date: SimDate,
    plot_cap: int = 64,
    yield_factor: float = 1.0,
    tile_batches: dict[str, int] | None = None,
) -> tuple[float, dict[str, int]]:
    """Применить набор рецептов, пока есть труд; вернуть (потрачено, партий по рецепту).

    `plot_cap` — потолок партий ОДНОГО двора с ОДНОЙ клетки надела. Для рецептов
    поля (`place: tile`) на клетках, которые двор ДЕРЖИТ (`_held_tile_ids`:
    усадьба или `Right`), `tile_batches` — общий на весь `work_month` счётчик
    `{tile_id: партий}`: ёмкость клетки (`plot_batch_cap_per_tile`) делят все её
    держатели, поэтому два двора на одной клетке не дают двойного выхода.
    Сбор с ОБЩИННОЙ клетки (режим `waste`/`reserved_wood`, не держимой) делит
    ёмкость так же: партии считаются на клетку среди всех дворов с правом
    доступа (`has_access_to_communal_tile`), а не по полной виргате каждому.
    Общинные `works_tiles` поселения (полевой `tenement`) считаются как раньше —
    лимит на двор: общинные поля по-новому не шарится. Рецепты усадьбы
    (`place: settlement`) ёмкость клетки не делят: это не надел, а один
    двор-место (мельница, выварка соли).

    `yield_factor` — сезонная урожайность для рецептов без входов (жатва и сбор):
    выход и стоячая материя множатся, труд — нет. Так рука на домене не молотит
    свой надел, а сезон меняет выход с 1 оставшегося трудодня.
    """
    available = budget
    counts: dict[str, int] = {}
    tile_cap = _tile_batch_cap(world)
    held = _held_tile_ids(world, household)
    # Тягло двора (economy/livestock.py): вол/конь удешевляют труд пахоты
    # (выход труда, не бесплатное зерно — множится труд, а не стоячая материя).
    plough_factor = draft_plough_factor(world, household)
    for recipe in recipes:
        candidates = _tiles_for_recipe(world, household, recipe)
        if not candidates:
            continue
        household_stock = world.get_stock(household.stock_id)
        if recipe.id == FORAGE_RECIPE:
            max_batches = 64
            share_plot = False
        elif recipe.id == "farrow_pigs" or recipe.id in BREED_PAIRS or recipe.id == "smelt_iron_bloom":
            max_batches = 1
            share_plot = False
        else:
            max_batches = max(0, plot_cap)
            share_plot = recipe.place == "tile"
        factor = _recipe_yield_factor(world, household, recipe, yield_factor)
        made = 0
        unshared_made = 0
        for tile in candidates:
            shared = share_plot and tile.id in held
            if not shared and _communal_collection_shared(
                world, household, recipe, tile, held
            ):
                shared = True
            if shared:
                limit = max_batches
                if tile_batches is not None:
                    remaining = tile_cap - tile_batches.get(tile.id, 0)
                    limit = min(limit, max(0, remaining))
            else:
                limit = max_batches - unshared_made
            made_here = 0
            labor_per_batch = recipe.labor_days * (
                plough_factor if recipe.id in PLOUGH_RECIPES else 1.0
            )
            while available > EPSILON and made_here < limit:
                tile_stock = world.get_stock(tile.standing_stock_id)
                effective = {
                    good: value * factor
                    for good, value in recipe.draws_standing.items()
                }
                standing_ok = _ratio(effective, tile_stock.amounts)
                inputs_ok = _ratio(recipe.inputs, household_stock.amounts)
                labor_ok = (
                    available / labor_per_batch if labor_per_batch > 0 else 1.0
                )
                scale = min(1.0, standing_ok, inputs_ok, labor_ok)
                if scale <= 1e-4:
                    break
                apply_recipe(
                    world, household, tile, recipe, scale, date,
                    yield_factor=yield_factor,
                )
                available -= labor_per_batch * scale
                made_here += 1
            if made_here and shared and tile_batches is not None:
                tile_batches[tile.id] = tile_batches.get(tile.id, 0) + made_here
            if made_here and not shared:
                unshared_made += made_here
            made += made_here
        if made:
            counts[recipe.id] = counts.get(recipe.id, 0) + made
    return budget - available, counts


def _wear_tool(world: World, household: Household, batches: int, date: SimDate) -> None:
    """Топор тупится от работы: масса уходит в отходы, ниже порога — ломается."""
    needs = world.needs
    if needs is None or batches <= 0:
        return
    stock = world.get_stock(household.stock_id)
    axe = stock.amounts.get("axe", 0.0)
    if axe <= EPSILON:
        return
    wear = min(axe, batches * needs.axe_wear_per_batch)
    if wear <= EPSILON:
        return
    world.ledger.transfer(stock, world.get_stock(WASTE_STOCK_ID), "axe", wear, "wear", date)
    household.tool_wear += wear
    left = stock.amounts.get("axe", 0.0)
    if left < needs.axe_break_below:
        if left > EPSILON:
            world.ledger.transfer(
                stock, world.get_stock(WASTE_STOCK_ID), "axe", left, "break", date
            )
        household.tool_wear = 0.0


def _repair_offset(world: World, household: Household, recipes: list[Recipe]) -> None:
    """Починка/ковка сбрасывает часть накопленного износа топора."""
    repaired = any(r.id in ("repair_axe", "smith_axe") for r in recipes)
    if repaired:
        household.tool_wear = max(0.0, household.tool_wear - 0.1)


def _tile_batch_cap(world: World) -> int:
    """Общая ёмкость ОДНОЙ клетки в месяц: её делят все дворы, кто на ней пашет."""
    manor = getattr(world, "manor", None)
    if manor is None:
        return 64
    return max(0, int(manor.plot_batch_cap_per_tile))


def _minor_actionable(world: World, household: Household, minor_recipes: list) -> bool:
    """Есть ли мелкому делу что делать: хоть один рецепт с входами в стоке.

    Без этого солевары и пахари отдают 40% труда пустой кузне (нет железа) —
    и соль/зерно падает. Резерв — только под реальную работу.
    """
    stock = world.get_stock(household.stock_id)
    for recipe in minor_recipes:
        if not recipe.inputs:
            return True
        if _ratio(recipe.inputs, stock.amounts) > 0.05:
            return True
    return False


def _plot_cap(world: World, household: Household) -> int:
    """Сколько партий в месяц двор вправе снять с ОДНОЙ клетки надела.

    Это доля клетки (`holding_scale`), а не лимит всего двора: двор с двумя
    кормящими клетками пашет обе, но ёмкость каждой клетки общая на всех.
    """
    manor = getattr(world, "manor", None)
    if manor is None:
        return 64
    return max(0, int(manor.plot_batch_cap_per_tile * household.holding_scale))


EXPENSIVE_RECIPES = frozenset({
    "repair_axe", "smith_axe", "smelt_iron_bloom", "smith_iron_share",
    "smith_kit", "craft_cart", "craft_raft", "craft_boat", "craft_wooden_plough",
})
EXPENSIVE_FOOD_MONTHS = 2.0


BREED_PAIRS = {
    "breed_ox": ("ox_m", "ox_f"),
    "breed_donkey": ("donkey_m", "donkey_f"),
    "breed_horse": ("horse_m", "horse_f"),
}
HAY_BREED_BUFFER = 8.0


def _allowed_recipe_ids(world: World, household: Household, ids: set[str]) -> set[str]:
    """Отсеять рецепты, чьи условия не выполнены.

    Свинья без пары не плодится. Дорогое (топоры, ковка, стройка судов и телег)
    обслуживается только при ресурсах: двор сыт (`food_months`) и входов хватает
    на целую партию, а не на крошки, — иначе очередь съедает бюджет, а толку нет.
    """
    stock = world.get_stock(household.stock_id)
    out: set[str] = set()
    for rid in ids:
        if rid == "farrow_pigs" and stock.amounts.get("pig", 0.0) < 2.0 - EPSILON:
            continue
        if rid in BREED_PAIRS:
            male, female = BREED_PAIRS[rid]
            if stock.amounts.get(male, 0.0) < 1.0 - EPSILON:
                continue
            if stock.amounts.get(female, 0.0) < 1.0 - EPSILON:
                continue
            if rid == "breed_horse" and not can_hold_horse(world, household):
                continue
            if stock.amounts.get("hay", 0.0) < HAY_BREED_BUFFER:
                continue
        if rid.startswith("mature_"):
            if stock.amounts.get("hay", 0.0) < HAY_BREED_BUFFER:
                continue
        if rid in EXPENSIVE_RECIPES:
            if food_months(world, household) < EXPENSIVE_FOOD_MONTHS:
                continue
        out.add(rid)
    return out


def work_month(world: World, date: SimDate) -> None:
    """Месяц труда: каждое живое домохозяйство отрабатывает своё решение.

    Счётчик партий по клеткам (`tile_batches`) общий на весь месяц: ёмкость
    клетки делят все дворы, кто на ней работает, а не каждый получает её целиком.
    """
    tile_batches: dict[str, int] = {}
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None:
            continue
        if household.main_action == "travel_adjacent":
            household.traveling = True
            world.bump("travel_events")
            continue
        household.traveling = False
        actions = world.household_actions
        main_act = actions.get(household.main_action)
        minor_act = actions.get(household.minor_action)
        main_recipes = _recipe_order(
            world,
            _allowed_recipe_ids(
                world, household, set(main_act.recipes) if main_act else set()
            ),
        )
        minor_recipes = _recipe_order(
            world,
            _allowed_recipe_ids(
                world, household, set(minor_act.recipes) if minor_act else set()
            ),
        )
        if not main_recipes and not minor_recipes:
            continue
        needs = world.needs
        labor = household.labor_days
        main_share = main_act.labor_share if main_act else 1.0
        minor_share = minor_act.labor_share if minor_act else 0.0
        # Сытый двор резервирует долю труда под мелкое дело (кузня/скот) заранее;
        # голодный отдаёт всё полю. Дорогое чинится только при ресурсах.
        # Косьба — ранняя фаза `phase_hay` до поля (единственная, ADR 0050):
        # резерву под поздний добор нет, поле ест все руки.
        secure = food_months(world, household) >= 3.0
        free_labor = labor
        minor_has_work = _minor_actionable(world, household, minor_recipes)
        minor_reserved = (
            free_labor * minor_share
            if (minor_recipes and secure and minor_has_work)
            else 0.0
        )
        if minor_recipes:
            if secure:
                main_budget = min(free_labor * main_share, free_labor - minor_reserved)
            else:
                main_budget = free_labor * main_share
        else:
            main_budget = free_labor
        plot_cap = _plot_cap(world, household)
        season_yield = plot_yield(world, world.clock.month)
        spent, counts = _apply_set(
            world, household, main_recipes, main_budget, date, plot_cap,
            season_yield, tile_batches,
        )
        if minor_recipes:
            if secure:
                minor_budget = minor_reserved
            else:
                minor_budget = min(free_labor * minor_share, max(0.0, free_labor - spent))
            spent2, counts2 = _apply_set(
                world, household, minor_recipes, minor_budget, date, plot_cap,
                season_yield, tile_batches,
            )
            spent += spent2
            for rid, n in counts2.items():
                counts[rid] = counts.get(rid, 0) + n
        household.labor_days = max(0.0, labor - spent)
        wear_ids = set(needs.wear_recipes) if needs is not None else set()
        wear_batches = sum(n for rid, n in counts.items() if rid in wear_ids)
        _repair_offset(world, household, main_recipes + minor_recipes)
        _wear_tool(world, household, wear_batches, date)
