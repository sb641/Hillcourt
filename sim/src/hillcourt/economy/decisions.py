"""Решения двора: главное и мелкое действие на месяц.

Двор не всеведущ: он видит свою клетку и соседние, знает свои запасы, свой труд
и собственный страх (слухи о ближних опасностях). Он не читает мир игрока и не
ходит по всей карте — только на соседнюю клетку и только если интерес перевесил
страх и цену рук на поле.
"""

from __future__ import annotations

from ..engine.manor import manor_of_household, manor_stock
from ..ontology import Household, Tile
from ..world import World
from ..engine.hexgrid import neighbor_ids
from ..legal.manor import hire_out_allowed
from .cooking import can_cook
from .labor import _feeding_tiles
from .livestock import DAIRY_GOODS, EGG_GOODS, draft_goods_for
from .needs import ADULT_LABOR_DAYS, food_months, food_shortfall, monthly_food_need

EPSILON = 1e-9


def _tile_id(x: int, y: int) -> str:
    return f"t_{x:02d}_{y:02d}"


def _trait(world: World, household: Household, attr: str) -> float:
    values = [
        getattr(person, attr)
        for pid in household.member_ids
        if (person := world.persons.get(pid)) is not None
    ]
    return max(values) if values else 0.0


def adjacent_tiles(world: World, household: Household) -> list[Tile]:
    """Соседние клетки двора без pathfinding (только шаг на одну клетку).

    Соседство — общий хелпер гекс-сетки `engine/hexgrid.py` (ADR 0071, именное
    разрешение владельца): шесть направлений, локальных переборов здесь нет.
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


def tile_risk(world: World, tile: Tile) -> float:
    """Суммарная интенсивность активных опасностей клетки."""
    return float(
        sum(
            world.hazards[hid].intensity
            for hid in tile.hazard_ids
            if hid in world.hazards and world.hazards[hid].active
        )
    )


def compute_adventurism(world: World, household: Household) -> float:
    """Интерес минус страх и цена рук на поле. 0..1.

    Голодный двор не уходит в чащу: руки нужны на поле, поэтому food_pressure
    снижает авантюризм. Любопытство тянет, страх и слух держат.
    """
    interest = _trait(world, household, "curiosity")
    dread = _trait(world, household, "fear")
    months = food_months(world, household)
    food_pressure = max(0.0, min(1.0, 1.0 - months / 2.0))
    value = interest - 0.5 * dread - 0.4 * food_pressure
    return max(0.0, min(1.0, value))


def update_rumor_fear(world: World, household: Household) -> float:
    """Страх по своим слухам: ближние опасности плюс недавний голод."""
    near_risk = max(
        (tile_risk(world, tile) for tile in adjacent_tiles(world, household)),
        default=0.0,
    )
    own_risk = tile_risk(world, world.tiles[household.current_tile_id]) if (
        household.current_tile_id in world.tiles
    ) else 0.0
    signal = 0.7 * max(near_risk, own_risk) + 0.3 * (1.0 if household.hunger_days > 0 else 0.0)
    return max(0.0, min(1.0, 0.5 * household.rumor_fear + 0.5 * signal))


def should_venture(world: World, household: Household, tile: Tile) -> bool:
    """Идти ли на клетку: страх клетки против авантюризма и слухов."""
    risk = tile_risk(world, tile)
    if risk <= EPSILON:
        return True
    threshold = risk * (1.0 + household.rumor_fear)
    return household.adventurism + EPSILON >= threshold


def _best_forage_target(world: World, household: Household) -> Tile | None:
    """Ближайшая соседняя клетка, пригодная для сбора и не пугающая двор."""
    for tile in adjacent_tiles(world, household):
        if tile.terrain not in ("forest", "heath", "marsh"):
            continue
        if should_venture(world, household, tile):
            return tile
    return None


def _own_book_grain(world: World, household: Household) -> float | None:
    """Зерно в амбаре книги САМОГО двора (или None, если двора нет в книге).

    Двор решает про `request_relief` по своему столу, не по замку: корневой
    двор читает замок (его книга — корневой манор), двор тэна — `manor:<id>`.
    Соляной держатель (`manor_id None`, вне книги) подмоги не просит.
    """
    book = manor_of_household(world, household.id)
    if book is None:
        return None
    source = manor_stock(world, book)
    if source is None:
        return None
    return source.amounts.get("grain", 0.0)


def _has_tool(world: World, household: Household) -> bool:
    stock = world.get_stock(household.stock_id)
    return stock.amounts.get("axe", 0.0) >= 1.0 - EPSILON


def _surplus_hands(world: World, household: Household) -> bool:
    """Хватает ли рук сверх прокорма: лишние руки можно отдать на риск.

    Считаем по вместимости (взрослые × 20), а не по остатку `labor_days`:
    к моменту решения остаток уже потрачен в `phase_labor`.
    """
    need = monthly_food_need(world, household)
    food_labor = need * (20.0 / 5.0)
    capacity = ADULT_LABOR_DAYS * sum(
        1
        for pid in household.member_ids
        if (p := world.persons.get(pid)) is not None and p.age_class == "adult"
    )
    return capacity > max(food_labor * 1.5, ADULT_LABOR_DAYS)


def allowed_action_ids(world: World, household: Household) -> set[str]:
    """Какие действия доступны двору по его правовому пресету (один выборщик, разные наборы)."""
    preset = world.catalogs.legal_statuses.get(household.legal_status_id)
    base = {"request_relief", "idle_repair", "hide_stores", "travel_adjacent", "cook_meal"}
    if preset is None:
        return base | {"work_plot", "forage_adjacent", "cut_wood_if_allowed", "tend_animals", "milk_animal", "make_cheese", "make_butter"}
    if preset.personal_status == "slave":
        return {"demesne_labor", "idle_repair"}
    if preset.land_relation == "landless":
        # Безземельный двор — экономический агент на своём гексе (ADR 0113 п. 1):
        # работает кормящие клетки, где стоит, добывает топливо и пасёт своё.
        # `hire_out` остаётся честным уходом, когда работы нет или предприятие
        # не платит (ADR 0097), но он не единственный и не первый выбор.
        return base | {"work_plot", "forage_adjacent", "cut_wood_if_allowed", "tend_animals"} | ({"hire_out"} if hire_out_allowed(world, household) else set())
    if not preset.ploughs:
        # Лорд/тэн сам не пашет. Исключение — соляная деревня: там и «держатель»
        # вываривает соль со своего надела (иначе соль не едет).
        settlement = world.settlements.get(household.settlement_id or "")
        if settlement is not None and settlement.kind == "salt_village":
            return base | {"work_plot", "forage_adjacent", "cut_wood_if_allowed", "tend_animals", "milk_animal", "make_cheese", "make_butter"}
        return base | {"oversee"}
    return base | {"work_plot", "forage_adjacent", "cut_wood_if_allowed", "tend_animals", "milk_animal", "make_cheese", "make_butter"}


def _saltworks_job(world: World, household: Household) -> str | None:
    """Работа на солеварне по потребности: топливо → выварка; None — не работник.

    Только НАЁМНЫЙ работник (безземельный) предприятия (ADR 0092/0097): держатель
    с собственным соляным наделом — хозяин у себя, его работа не «наём хозяина»,
    и приоритет нужды ему не диктуется.

    - предприятие не платит (склад без зерна) → `None`: честный `hire_out`,
      вина хозяина, а не «не мог» двора;
    - на руках топлива меньше одной партии выварки → `cut_wood_if_allowed`:
      без торфа выварки нет, следовательно нет и соли, и жалованья;
    - топливо есть → `work_plot`: выварка на соляных гексах предприятия.
    """
    settlement = world.settlements.get(household.settlement_id or "")
    if settlement is None or settlement.kind != "salt_village":
        return None
    preset = world.catalogs.legal_statuses.get(household.legal_status_id)
    if preset is None or preset.land_relation != "landless":
        return None
    stores = world.stocks.get(settlement.stores_stock_id)
    if stores is None or stores.amounts.get("grain", 0.0) <= 1e-9:
        return None
    recipe = world.catalogs.recipes.get("boil_salt")
    fuel_need = 0.0
    if recipe is not None:
        fuel_need = sum(recipe.inputs.values())
    stock = world.get_stock(household.stock_id)
    if stock.amounts.get("peat", 0.0) + 1e-9 < fuel_need:
        return "cut_wood_if_allowed"
    return "work_plot"


def _own_hex_has_crop(world: World, household: Household) -> bool:
    """Есть ли на гексе двора что собирать: стоячая материя на кормящих клетках.

    Голодный двор ест со своего гекса, если там что-то выросло (ADR 0113 п. 1:
    «хекс × люди = еда»). Считать это нужно по данным, а не по наличию клетки:
    пустое поле и пустой выпас не дают еды, и тогда двор правда идёт собирать
    у соседей или наниматься.
    """
    for tile in _feeding_tiles(world, household):
        standing = world.get_stock(tile.standing_stock_id)
        for good, amount in standing.amounts.items():
            rule = world.catalogs.goods.get(good)
            if amount > EPSILON and rule is not None and rule.edible:
                return True
    return False


def _clamp(action: str, allowed: set[str], fallbacks: tuple[str, ...]) -> str:
    if action in allowed:
        return action
    for fallback in fallbacks:
        if fallback in allowed:
            return fallback
    return "idle_repair"


def choose_actions(world: World, household: Household) -> tuple[str, str]:
    """Выбрать (главное, мелкое) действие на следующий месяц.

    Один выборщик на все пресеты: разница — в наборе допустимых действий
    (`allowed_action_ids`) и в том, чем двор кормится. Порядок приоритетов:
    подмога при затяжном голоде → еда → зимнее топливо → починка → скот → риск.
    """
    allowed = allowed_action_ids(world, household)
    if world.needs is None:
        return _clamp("work_plot", allowed, ("work_plot",)), "idle_repair"
    if household.personal_status == "slave" or "demesne_labor" in allowed:
        return "demesne_labor", "idle_repair"
    stock = world.get_stock(household.stock_id)
    months = food_months(world, household)
    winter = world.clock.month in world.needs.winter_months
    fuel = stock.amounts.get("firewood", 0.0) + stock.amounts.get("peat", 0.0)
    fuel_need = world.needs.firewood_per_adult_winter_month * max(
        1, len(household.member_ids)
    )
    has_tool = _has_tool(world, household)
    surplus = _surplus_hands(world, household)
    forage_target = _best_forage_target(world, household)
    book_grain = _own_book_grain(world, household)

    main = "work_plot"
    works_job = _saltworks_job(world, household)
    if works_job is not None:
        # Солеварня платит (склад предприятия не пуст) → двор работает на варне:
        # зимой/при нехватке топлива — топливо, иначе выварка. Не платит →
        # честный уход в наём (ADR 0097), а не работа вслепую.
        main = works_job
    elif (
        food_shortfall(world, household) > EPSILON
        and book_grain is not None
        and book_grain >= world.needs.relief_min_court_grain
    ):
        # Помощь предлагается в том же месяце, в каком двор не покрыл нужду
        # (ADR 0114 п. 2): ждать двух месяцев голода — это и был хронический голод.
        # Пашня при этом не брошена: см. минорный слот ниже — двор, у которого на
        # своём кормящем гексе стоит урожай, просит подачу И работает поле.
        main = "request_relief"
    elif months < 1.0:
        if can_cook(world, household):
            main = "cook_meal"
        elif _own_hex_has_crop(world, household):
            # Свой гекс кормит первым (ADR 0113): пока на нём есть что собрать,
            # двор не тратит труд на пустой сбор у соседей.
            main = "work_plot"
        elif household.hunger_days > 0 and forage_target is not None and surplus:
            main = "forage_adjacent"
        else:
            main = "work_plot"
    elif winter and fuel < fuel_need:
        main = "cut_wood_if_allowed"
    elif surplus and household.adventurism > 0.55 and forage_target is not None:
        if household.adventurism > 0.7 and months > 2.0:
            explore = world.rng.economy.random() < (household.adventurism - 0.7) * 2.0
            main = "travel_adjacent" if explore else "forage_adjacent"
        else:
            main = "forage_adjacent"
    else:
        main = "work_plot"
    main = _clamp(main, allowed, ("work_plot", "hire_out", "oversee", "forage_adjacent"))

    minor = "idle_repair"
    if (
        main == "request_relief"
        and _own_hex_has_crop(world, household)
    ):
        # ADR 0113 п. 1 рядом с ADR 0114 п. 2: подача не отменяет пашню, иначе
        # двор замыкается сам на себя — не пахал → не собрал → недобор → подача →
        # пашня не сорвана → недобор. Замер `start_stand` (24 мес, сид 1729): все
        # пять `INITIAL_FAMILIES` получили `harvest_grain` 0.000 при стоящем зерне
        # 8.0 на своём наделе; `hh_wave_01`, пришедший с едой в стоке, выбрал
        # `work_plot` и собрал 6.4516. Ровно это проверяет
        # `test_personal_holding_feeds_the_household_and_not_the_court`.
        #
        # Именно минорный слот, а не главный: главный выбор «подача или пашня» —
        # это ложная развилка, оба закона правы, и двор вправе делать и то и
        # другое. Проверка «хватает ли урожая на месяц» здесь была бы ошибкой:
        # стоячая материя пред жатвой (6.2 → 5.0 зерна), и её нельзя сравнивать с
        # потребностью напрямую — двор с 1.2 зерна при нужде 2.7 работал бы впустую
        # и получил один голодный день вместо подачи.
        minor = "work_plot"
    elif has_tool and household.tool_wear >= world.needs.axe_break_below:
        minor = "idle_repair"
    elif any(
        stock.amounts.get(good, 0.0) > 0
        for good in tuple(DAIRY_GOODS.values()) + tuple(EGG_GOODS.values())
    ):
        minor = "milk_animal"
    elif stock.amounts.get("pig", 0.0) > 0 or stock.amounts.get("wool", 0.0) > 0:
        minor = "tend_animals"
    elif any(stock.amounts.get(good, 0.0) > 0 for good in draft_goods_for(world, household)):
        minor = "tend_animals"
    elif winter and fuel < fuel_need:
        minor = "cut_wood_if_allowed"
    elif surplus and household.adventurism > 0.6 and forage_target is not None:
        minor = "forage_adjacent"
    else:
        minor = "idle_repair"
    minor = _clamp(minor, allowed, ("idle_repair",))
    return main, minor


def plan_next_month(world: World) -> None:
    """Обновить авантюризм, страх и действия всех живых дворов на следующий месяц."""
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None:
            continue
        household.rumor_fear = update_rumor_fear(world, household)
        household.adventurism = compute_adventurism(world, household)
        household.main_action, household.minor_action = choose_actions(world, household)
