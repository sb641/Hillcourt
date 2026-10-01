"""Экономика: труд двора, рецепты и износ инструмента.

Производство идёт только через рецепты каталога. Действие двора (`actions_household.yml`)
разрешает набор рецептов; двор тратит труд, пока хватает стоячей материи и входов.
Материя не берётся из ничего: всё либо переводится, либо входит по правилу появления.
"""

from __future__ import annotations

import weakref

from ..catalogs import LAND_ACTION_RECIPES, Catalogs
from ..engine.hexgrid import neighbor_ids
from ..legal.calendar import seasonal_labor_days
from ..legal.regimes import has_access_to_communal_tile
from ..ontology import Household, Recipe, SimDate, Stock, Tile
from ..world import World
from . import soil
from .livestock import (
    ANIMAL_PRODUCT_RECIPES,
    DAIRY_GOODS,
    PLOUGH_RECIPES,
    animal_product_scale,
    can_hold_horse,
    draft_plough_factor,
    has_animal_access,
    species_of,
)
from .needs import food_months, food_shortfall, hay_need_rate, monthly_food_need
from .seasons import plot_yield

PROCESSING_STOCK_ID = "sink:processing"
WASTE_STOCK_ID = "sink:waste"
EPSILON = 1e-9
FORAGE_TERRAIN = ("forest", "heath", "marsh")
FORAGE_RECIPE = "forage_wild"

NO_TOOL_YIELD = 1.0
# Множители инструмента и тягла — числа каталога (`grow_grain.params`), а не
# константы модуля: `economy/soil.py` читает `tool_yield`/`draft_yield`.
tool_yield_factor = soil.tool_yield_factor
TOOL_PRIORITY = soil.TOOL_PRIORITY


def _recipe_yield_factor(
    world: World,
    household: Household,
    recipe: Recipe,
    season_yield: float,
    tile: Tile | None = None,
) -> float:
    """Итоговый множитель рецепта: сезон надела × клетка пашни, только для сбора.

    Множатся и стоячая материя, и выход (но не труд) — только у рецептов без
    входов, которые черпают с земли (`draws_standing`). У превращений с
    `inputs` множитель всегда 1.0, иначе масса перестала бы сходиться.

    `tile` — клетка, с которой берут урожай: к ней применяются прибавки
    пашни (плуг и тягло, навоз, смена полей, ADR 0110 п. 1 и решения хозяина).
    Без клетки остаётся сезон × инструмент двора.
    """
    if recipe.inputs or not recipe.draws_standing:
        return 1.0
    if tile is None:
        return season_yield * tool_yield_factor(world, household)
    return season_yield * soil.cell_yield_factor(world, household, tile)



def draw_factor(
    world: World,
    household: Household,
    recipe: Recipe,
    season_yield: float,
    tile: Tile | None = None,
) -> float:
    """Тот САМЫЙ множитель, которым `apply_recipe` берёт стоящую материю.

    Сторож доступности материи обязан считать ровно это число, а не свою версию
    формулы: расхождение в единицу последнего знака уже роняло жатву домена
    (`ValueError: Недостаточно 'grain' в стоке 'tile:…'`) — бухгалтерия refused
    брать больше, чем в клетке лежало. Одна функция вместо двух копий формулы.

    `season_yield` — СЕЗОН, а не готовый множитель клетки: прибавки клетки
    (инструмент, тягло, навоз, смена полей) домножает сам `apply_recipe`, и
    повторный счёт даёт двойной множитель (ADR 0106/0110).
    """
    return _recipe_yield_factor(world, household, recipe, season_yield, tile)


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

    `yield_factor` — сезонная урожайность; вместе с прибавками клетки пашни
    (плуг и тягло, навоз, смена полей) она множит стоячую материю и выход (но не
    труд), поэтому выход на 1 трудодень меняется, а массовый баланс держится.
    Применяется только к рецептам без `inputs` (чистый сбор с земли).
    """
    processing = world.get_stock(PROCESSING_STOCK_ID)
    waste = world.get_stock(WASTE_STOCK_ID)
    household_stock = world.get_stock(household.stock_id)
    tile_stock = world.get_stock(tile.standing_stock_id)
    dst = output_stock if output_stock is not None else household_stock
    if recipe.id == "boil_salt" and household.settlement_id in world.settlements:
        dst = world.get_stock(world.settlements[household.settlement_id].stores_stock_id)
    factor = _recipe_yield_factor(world, household, recipe, yield_factor, tile)

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

    # Стадо могло измениться прямо здесь: `breed_*`/`mature_*` рождают и
    # переводят телёнка в корову, `mature_*` меняет СОСТАВ (ox_calf 0.4 → ox_m 1.2),
    # а это и есть множитель навоза. Кэш суммы поселения обязан это увидеть
    # (ADR 0096 п. 1: подстилка тягла — удобрение), иначе месяц посчитает навоз
    # по старому поголовью. Список товаров рецепта известен — проверка на
    # пересечение с `DRAFT_GOODS` дешевле одного лишнего расчёта.
    from .soil import invalidate_herd_cache, touches_herd

    if touches_herd(recipe.inputs) or touches_herd(recipe.outputs) or touches_herd(recipe.loss):
        invalidate_herd_cache(world)

    if abs(processing.total()) >= 1e-6:
        raise AssertionError(
            f"Рецепт '{recipe.id}' не сбалансирован: остаток обработки "
            f"{processing.total()}"
        )


def _tile_id(x: int, y: int) -> str:
    return f"t_{x:02d}_{y:02d}"


def own_holding_tiles(world: World, household: Household) -> list[Tile]:
    """СОБСТВЕННЫЙ надел двора: своя клетка и наделения по праву — без доступа.

    Личное владение (`Right.kind` tenure/grazing) и своя усадьба; `Right.kind =
    common` и клетки `Settlement.works_tiles` — это доступ, а не надел
    (`docs/07_legal.md:64-68`, ADR 0077/0128). `own_tiles` остаётся множеством
    ДОСТУПА (свои клетки + общинные угодья поселения), а владение — здесь.
    """
    out: list[Tile] = []
    if household.settlement_id and household.settlement_id in world.settlements:
        settlement = world.settlements[household.settlement_id]
        home = world.tiles.get(_tile_id(*settlement.coord))
        if home is not None:
            out.append(home)
    else:
        home = world.tiles.get(household.current_tile_id)
        if home is not None:
            out.append(home)
    for right in sorted(world.rights.values(), key=lambda r: r.id):
        if right.holder_household_id != household.id or right.kind == "common":
            continue
        tile = world.tiles.get(right.tile_id)
        if tile is not None and tile not in out:
            out.append(tile)
    return out


def own_land_feeds(world: World, household: Household) -> bool:
    """Кормит ли двор СВОЯ земля — единственный источник истины (ADR 0124).

    Чистый факт, а не «есть ли клетка»: надел (`own_holding_tiles`) плюс режим,
    который кормит, плюс ресурс самой клетки (`tile_feeds_household`, ADR 0108:
    солончак — производственная клетка соли, не пашня). Имущество, которое не
    кормит, не делает двор землевладельцем-нетокормильцем: такого двора кормит
    предприятие (ADR 0102, 0092). Один факт на оба решения — и выплата, и работа
    своей земли.
    """
    for tile in own_holding_tiles(world, household):
        regime = world.catalogs.land_regimes.get(tile.regime_id)
        if regime is not None and regime.feeds_household and tile_feeds_household(world, tile):
            return True
    return False


def _own_tiles_scan(world: World, household: Household) -> list[Tile]:
    """Тело `own_tiles` без кэша: единственное место, где надел реально собирается.

    Вынесено из `own_tiles` без изменения тела — так видно, что кэш добавил
    только СКОРОСТЬ ответа, а сама сборка осталась прежней (тот же приём, что
    у `_scan_feeds_household`).
    """
    tiles: list[Tile] = []
    # Дедуп по `tile.id`, а не по `tile not in tiles`.
    #
    # ПОЧЕМУ ЭТО ТО ЖЕ САМОЕ, А НЕ ОПТИМИЗАЦИЯ С ЧИСЛАМИ. `Tile` — датакласс, и
    # `tile not in tiles` звал сгенерированный `Tile.__eq__`, то есть сравнивал
    # ВСЕ 13 полей. Но `id` — первое из них, поэтому для любых двух `Tile`
    # `a == b` влечёт `a.id == b.id`. Значит «`tile` уже есть в списке» по
    # датаклассному `__eq__` тождественно «`tile.id` уже есть в списке», и
    # подстановка одной формулы вместо другой не может изменить ни состав, ни
    # порядок списка. Доказуемо, а не «замером показалось».
    #
    # ЦЕНА БЫЛА НЕ В ПРАВИЛЕ, А В СРАВНЕНИИ. `own_tiles` зовётся 59 раз на
    # двор-месяц, а `Tile.__eq__` на 13 полей — миллионы вызовов на месяц при
    # 150 дворах: 2.48 млн, то есть обход каталога и создание партий уходили
    # в сравнение датакласса. Никакого кэша, никакой инвалидации, никакого
    # состояния: две строки меняют только СКОРОСТЬ вопроса «эта клетка уже в
    # списке», а не ответ.
    seen: set[str] = set()
    if household.settlement_id and household.settlement_id in world.settlements:
        settlement = world.settlements[household.settlement_id]
        first = world.tiles.get(_tile_id(*settlement.coord))
        if first is not None:
            tiles.append(first)
            seen.add(first.id)
        for tid in settlement.works_tiles:
            tile = world.tiles.get(tid)
            if tile is not None and tile.id not in seen:
                tiles.append(tile)
                seen.add(tile.id)
    else:
        tile = world.tiles.get(household.current_tile_id)
        if tile is not None:
            tiles.append(tile)
            seen.add(tile.id)
    right_tiles = [
        world.tiles[right.tile_id]
        for right in world.rights.values()
        if right.holder_household_id == household.id
        and right.kind != "common"
        and right.tile_id in world.tiles
    ]
    for tile in sorted(right_tiles, key=lambda t: t.id):
        if tile.id not in seen:
            tiles.append(tile)
            seen.add(tile.id)
    return tiles


# ═══ КЭШ ЗЕМЕЛЬ ДВОРА ═══════════════════════════════════════════════════════
#
# ЧТО КЭШИРУЕТСЯ И ПОЧЕМУ ИМЕННО ЭТО. Надел двора — чистый вывод из данных мира,
# и за месяц он меняется раз или ни разу, а пересобирается тысячи раз: замер
# (`v0_barony_100`, сид 4242, 6 месяцев, счётчики вызовов) —
#   `own_tiles` 8012 раз в месяц, `_feeding_tiles` 3348, `tile_feeds_household`
#   50 130. На 150 дворах это 14 350 пересборок надела и 50 130 вопросов
#   «кормит ли этот рельеф» на месяц ради ответа, который не менялся.
#
# КЛЮЧ ВЫВЕДЕН ИЗ ВСЕГО, ЧТО ЧИТАЮТ ФУНКЦИИ. Это условие, а не украшение:
# неполный ключ молча отдаёт прошлый месяц, а это ровно тот класс тихой поломки,
# ради которого кэш и запрещали. Полный список входа — по каждой функции ниже.
#
#   `own_tiles` (`_own_tiles_scan`) читает:
#     1. `household.settlement_id`            — какая ветка и какое поселение;
#     2. `household.settlement_id in world.settlements` — через `coord`/`works`
#        в ключе: `None` значит «поселения нет», и появление поселения с тем же
#        id ключ меняет;
#     3. `settlement.coord`                   — адрес усадьбы (`_tile_id(*coord)`);
#     4. `settlement.works_tiles` (СОДЕРЖИМОЕ и порядок) — общинные угодья;
#     5. `household.current_tile_id`          — без поселения это весь надел;
#     6. `world.tiles`                        — по всем упомянутым id проверяется
#        ЛИЧНОСТЬ клетки (`world.tiles.get(id) is cached`), см. ниже;
#     7. `world.rights`: `holder_household_id`, `kind`, `tile_id` — в сигнатуре
#        прав, в порядке обхода `world.rights`;
#     8. `tile.id` каждой взятой клетки — в сигнатуре прав и в самом ответе.
#
#   `_feeding_tiles` поверх этого читает:
#     9. `tile.regime_id`, `tile.terrain`, `tile.id` каждой клетки надела —
#        сигнатура режимов;
#    10. `world.catalogs.land_regimes[regime_id].feeds_household` — флаг каждого
#        режима надела в ключе (правка режима на месте иначе невидима: поймал
#        тест `test_invalidation_land_regime_flag_in_catalog`);
#    11. `world.catalogs` (через мемо `_FEEDS_BY_TERRAIN`, который сам keyed по
#        `id(catalogs)`) — по `id(catalogs)`, с тем же запретом, что и там.
#
# ПОЧЕМУ ЛИЧНОСТЬ КЛЕТОК, А НЕ ТОЛЬКО ИХ ID. `own_tiles` возвращает ОБЪЕКТЫ
# `Tile`, и «клетка с тем же id, но другим объектом» — это другой ответ: у неё
# другие `regime_id`, `standing_stock_id` и рельеф, а тик поедет. Поэтому
# сверка идёт по `is`, а не по `id`: `world.tiles.get(tid) is cached`. Это
# единственное место, где нужен обход всех упомянутых клеток, и оно сделано
# ОДИН раз на вызов, а не по одному на клетку на каждый вопрос про неё.
#
# ЧИСТКА ЗАПИСИ (AGENTS.md §6). Карточки лежат под `id(world)` и умирают вместе
# с миром: обход — колбэк `weakref.ref`, который выбрасывает ключ сам, плюс
# сверка `entry[2]() is world` на каждом чтении (адрес мог переиспользоваться).
# Держать датакласс ключом нельзя (`World.__hash__ is None`), поэтому `id()` +
# `weakref.ref` — единственно возможная форма, и она ровно та же, что у
# `soil.herd_state` и у мемо рельефов.
#
# ЧТО ЗАПРЕЩЕНО СЛЕДУЮЩИМ АГЕНТАМ: мутировать `Settlement.works_tiles`,
# `Household.settlement_id`, `Household.current_tile_id` и содержимое
# `world.rights`/`world.tiles` В ОБХОД своих полей (то есть не заменяя список и
# не переприсваивая атрибут). Правка через переприсваивание видна ключу сразу.
_LAND_CARDS: dict[int, tuple[dict[str, tuple], dict[str, tuple], "weakref.ref"]] = {}


def _land_cards(world: World) -> tuple[dict[str, tuple], dict[str, tuple]]:
    """Карточки земель двора для мира (создаются лениво, умирают вместе с миром)."""
    key = id(world)
    entry = _LAND_CARDS.get(key)
    if entry is not None and entry[2]() is world:
        return entry[0], entry[1]
    own_cards: dict[str, tuple] = {}
    feed_cards: dict[str, tuple] = {}
    _LAND_CARDS[key] = (
        own_cards,
        feed_cards,
        weakref.ref(world, lambda _ref, k=key: _LAND_CARDS.pop(k, None)),
    )
    return own_cards, feed_cards


def _holding_rights_sig(world: World, household: Household) -> tuple:
    """Сигнатура ПРАВ двора, которые дают клетку в надел: `(tile_id, kind)`.

    Общие права (`kind = common`) в надел не входят (ADR 0060), поэтому в
    сигнатуре их нет: их появление ответ не меняет. Порядок обхода
    `world.rights` сохраняется — значит и вставка права в середину реестра ключ
    меняет. Идёт ДО выборки `world.tiles`, потому что именно из этих прав
# берутся недостающие клетки.
    """
    return tuple(
        (right.tile_id, right.kind)
        for right in world.rights.values()
        if right.holder_household_id == household.id and right.kind != "common"
    )


def _own_tiles_key(world: World, household: Household) -> tuple:
    """Полный ключ надела: всё, что `_own_tiles_scan` читает, кроме клеток.

    Клетки в ключ не входят — они проверяются отдельно, сверкой личности
    (`world.tiles.get(tid) is cached`), потому что `Tile` — датакласс и ключом
    словаря быть не может, а `id()` на каждую клетку дороже, чем сама сборка.
    """
    settlement_id = household.settlement_id
    settlement = world.settlements.get(settlement_id) if settlement_id else None
    if settlement is None:
        coord: tuple[int, int] | None = None
        works: tuple[str, ...] | None = None
    else:
        coord = settlement.coord
        works = tuple(settlement.works_tiles)
    return (
        settlement_id,
        household.current_tile_id,
        coord,
        works,
        _holding_rights_sig(world, household),
    )


def _own_tiles_want_ids(world: World, household: Household) -> tuple[str, ...]:
    """ВСЕ клетки, к которым функция вообще может притронуться, в порядке чтения.

    Это ровно адрес усадьбы (или `current_tile_id` без поселения), затем
    `works_tiles`, затем клетки прав. Сверка идёт по этому списку, а не по
    результату: клетка, которой раньше не было в `world.tiles`, а право на неё
    есть, обязана быть замечена — иначе надел молча остался бы прежним.
    """
    settlement_id = household.settlement_id
    settlement = world.settlements.get(settlement_id) if settlement_id else None
    if settlement is None:
        want = [household.current_tile_id]
    else:
        want = [_tile_id(*settlement.coord)]
        want.extend(settlement.works_tiles)
    for right_tile_id, _kind in _holding_rights_sig(world, household):
        want.append(right_tile_id)
    return tuple(want)


def own_tiles(world: World, household: Household) -> list[Tile]:
    """Земли двора: поселение, держания из works_tiles и клетки по праву (Right).

    Пожалованная полоса (`grant_tenement`) даёт двору `Right` на клетку; без
    этого право есть, а труда нет — двор не пашет надел. `Right.kind=common` —
    общий доступ, а не индивидуальное держание (ADR 0060): такие клетки в
    наделе двора не стоят (община принадлежит всем, а не первому двору).
    Клетки по праву добавляются без дублей и в детерминированном порядке.

    Ответ берётся из кэша земель (см. блок выше): полный ключ + сверка
    личности всех упомянутых клеток. Сборка надела — в `_own_tiles_scan`, и
    кэш не может изменить ни состав, ни порядок списка, потому что на
    попадание он отдаёт ровно то, что вернула бы сборка.
    """
    own_cards, _ = _land_cards(world)
    entry = own_cards.get(household.id)
    if entry is not None:
        key, want_ids, want_tiles, tiles = entry
        if key == _own_tiles_key(world, household):
            world_tiles = world.tiles
            for tile_id, cached in zip(want_ids, want_tiles):
                if world_tiles.get(tile_id) is not cached:
                    break
            else:
                return list(tiles)
    tiles = _own_tiles_scan(world, household)
    want_ids = _own_tiles_want_ids(world, household)
    own_cards[household.id] = (
        _own_tiles_key(world, household),
        want_ids,
        tuple(world.tiles.get(tile_id) for tile_id in want_ids),
        tuple(tiles),
    )
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


def _scan_feeds_household(catalogs: Catalogs, terrain: str) -> bool:
    """Чистый обход каталога: кормит ли двор рельеф `terrain` (ADR 0108, 0124).

    Вынесено из `tile_feeds_household` без изменения тела: так видно, что
    МЕМО — единственное, что добавлено, а сама проверка осталась прежней.
    """
    for recipe in catalogs.recipes.values():
        if recipe.place != "tile":
            continue
        if not any(
            (rule := catalogs.goods.get(good)) is not None and rule.edible
            for good in recipe.outputs
        ):
            continue
        if not recipe.requires_terrain or terrain in recipe.requires_terrain:
            return True
    return False


# Мемо «кормит ли этот рельеф» по рельефу, на время жизни КАТАЛОГОВ.
#
# Факт зависит ровно от двух вещей: от `tile.terrain` и от содержимого
# `world.catalogs`. Ни рельеф клетки, ни каталог за тик не меняются, а рельефов
# в мире единицы (замер: 5 уникальных на 150 дворов), а рецептов в каталоге 61.
# Без мемо получалось 376 полных обходов каталога на двор-месяц, 56 тысяч на
# месяц, ради вопроса с пятью возможными ответами.
#
# ПОЧЕМУ КЛЮЧ — `id(catalogs)`, А НЕ `id(world)`. Ответ не зависит от мира
# вовсе: он зависит от набора каталогов. `load_catalogs` (`catalogs.py:443`)
# отдаёт `copy.deepcopy` на каждый мир, поэтому объекты каталогов не
# разделяются между мирами, и запись одного мира физически не может быть
# прочитана другим.
#
# ЧИСТКА ЗАПИСИ (AGENTS.md §6). Запись ушла в `weakref.ref`, и обход, который её
# чистит, здесь же: колбэк `weakref.ref` выбрасывает ключ сам, а каждое чтение
# сверяет `entry[1]() is catalogs` — если адрес уже переиспользован, запись
# считается чужой и перезаписывается. Обе страховки, как в `soil.herd_state`.
#
# ЧТО ЗАПРЕЩЕНО СЛЕДУЮЩИМ АГЕНТАМ: добавлять в `world.catalogs.recipes` или
# `world.catalogs.goods` прямо во время тика (или после загрузки мира). Мемо
# этого не увидит. Сегодня такого места в репозитории нет: `load_catalogs`
# собирает каталог из YAML и дальше только читается, а `copy.deepcopy` при
# загрузке гарантирует, что мутация попадёт в копию мира, а не в общий кэш.
_FEEDS_BY_TERRAIN: dict[int, tuple[dict[str, bool], "weakref.ref"]] = {}


def _feeds_by_terrain(catalogs: Catalogs) -> dict[str, bool]:
    """Мемо «рельеф → кормит ли» для набора каталогов (создаётся лениво)."""
    key = id(catalogs)
    entry = _FEEDS_BY_TERRAIN.get(key)
    if entry is not None and entry[1]() is catalogs:
        return entry[0]
    memo: dict[str, bool] = {}
    _FEEDS_BY_TERRAIN[key] = (
        memo,
        weakref.ref(catalogs, lambda _ref, k=key: _FEEDS_BY_TERRAIN.pop(k, None)),
    )
    return memo


def tile_feeds_household(world: World, tile: Tile) -> bool:
    """Может ли клетка кормить двор по СВОЕМУ ресурсу (ADR 0108, 0124).

    Режим говорит «надел», но рельеф решает, что на клетке растёт: солончак
    (`salt_flat`) — производственная клетка соли, зерно на нём не сеют, и жатва
    его не берёт (`harvest_grain.requires_terrain = [field]`). Поэтому клетка
    кормит двор только если на её рельефе есть хоть один съедобный рецепт поля.

    Проверка чистая функция рельефа, поэтому ответ берётся из мемо
    (`_feeds_by_terrain`): первый вопрос по рельефу платит обходом каталога,
    остальные — словарём. Число рецептов в каталоге при этом осталось 61, и
    правило «какой рецепт кормит» осталось прежним, просто его больше не
    приходится повторять для каждой клетки каждого двора.
    """
    memo = _feeds_by_terrain(world.catalogs)
    cached = memo.get(tile.terrain)
    if cached is not None:
        return cached
    answer = _scan_feeds_household(world.catalogs, tile.terrain)
    memo[tile.terrain] = answer
    return answer


def _feeding_tiles_scan(world: World, household: Household, own: list[Tile]) -> list[Tile]:
    """Тело `_feeding_tiles` без кэша, с уже собранным наделом `own`.

    Правило не тронуто: клетка кормит двор, если режим кормит И на её рельефе
    есть съедобный рецепт поля. Вынесено без изменения тела — так видно, что
    кэш добавил только скорость.
    """
    out: list[Tile] = []
    for tile in own:
        regime = world.catalogs.land_regimes.get(tile.regime_id)
        if regime is not None and regime.feeds_household and tile_feeds_household(world, tile):
            out.append(tile)
    return out


def _feeding_tiles_key(world: World, own: list[Tile]) -> tuple:
    """Полный ключ кормящего надела: клетка, режим, рельеф, флаги режимов, каталоги.

    Три части, и каждая закрывает свой read:

    * `(tile.id, tile.regime_id, tile.terrain)` по наделу — ответ это ОБЪЕКТЫ
      `Tile`, и два надела с одинаковыми режимами и рельефом в том же порядке,
      но разными клетками дали бы разные ответы, поэтому `tile.id` в ключе
      обязателен, а не избыточен;
    * `feeds_household` каждого РЕЖИМА надела — прямой read
      `world.catalogs.land_regimes[...].feeds_household`. Без этой части
      правка режима на месте (объект тот же, содержимое другое) отдавала бы
      прошлый месяц: это поймал тест `test_invalidation_land_regime_flag_in_catalog`,
      и именно поэтому часть добавлена;
    * `id(world.catalogs)` — вопрос «кормит ли этот рельеф» живёт в мемо
      `_FEEDS_BY_TERRAIN`, который сам keyed по `id(catalogs)`; здесь тот же
      запрет: мутировать `world.catalogs` во время тика нельзя (см. блок кэша).
    """
    land_regimes = world.catalogs.land_regimes
    sig: list = []
    regimes: set[str] = set()
    for tile in own:
        sig.append(tile.id)
        regime_id = tile.regime_id
        sig.append(regime_id)
        sig.append(tile.terrain)
        regimes.add(regime_id)
    flags: list = []
    for regime_id in sorted(regimes):
        regime = land_regimes.get(regime_id)
        flags.append(regime_id)
        flags.append(None if regime is None else regime.feeds_household)
    return (id(world.catalogs), tuple(sig), tuple(flags))


def _feeding_tiles(world: World, household: Household) -> list[Tile]:
    """Надел двора: клетки, которые его кормят — по режиму И по своему ресурсу.

    Домен лорда сюда не входит. Клетка с режимом «кормит», но без съедобного
    рецепта по своему рельефу (солончак, вода) наделом двора не считается: это
    производственная клетка, а не пашня.

    Надел берётся из кэша `own_tiles`, а сама выборка «кормит ли» — из своей
    карточки с полным ключом (см. блок «КЭШ ЗЕМЕЛЬ ДВОРА»). Правило и порядок
    клеток остались прежними: на попадание отдаётся ровно то, что вернула бы
    `_feeding_tiles_scan` по тому же наделу.
    """
    own = own_tiles(world, household)
    _, feed_cards = _land_cards(world)
    key = _feeding_tiles_key(world, own)
    entry = feed_cards.get(household.id)
    if entry is not None and entry[0] == key:
        return list(entry[1])
    out = _feeding_tiles_scan(world, household, own)
    feed_cards[household.id] = (key, tuple(out))
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


WILD_TERRAINS: tuple[str, ...] = ("forest", "marsh")
"""Дикая земля, до которой доходят пешком: лес и болото.

Список **рельефов**, а не рецептов, и это осознанный выбор. Список рецептов
(`cut_firewood`, `cut_wood`, `mine_iron`) умер от своей же формы: economist-каталог
завёл рецепт расчистки `uproot_stumps` (ADR 0182), Recipe-агент честно отказался
трогать чужой файл, и рецепт остался без кандидатов — ни одного тика. Список
рельефов переживает новый рецепт: `requires_terrain: [forest]` сам попадает в
дикую землю.

МИНУСЫ, которые видно сразу:
  * новый **рельеф** в мире (например `bog`) сюда не попадёт, пока его не впишут
    руками: та же болезнь, но лечится раз в поколение мира, а не раз в рецепт;
  * признак «дикий» — это пересечение с рельефом, а не поле каталога, поэтому
    рецепт, которому соседний лес запрещён по существу, придётся исключать
    здесь же, а не в `recipes.yml` (каталог не мой).
"""


# ADR 0187, проверяемое правило «нужно». Все три числа — из каталога, не выдуманы.
#: `cut_peat.draws_standing.peat` — сколько стоящего торфа забирает одна партия.
PEAT_BATCH_STANDING: float = 3.15
#: `boil_salt.inputs.peat` (3.0) при `boil_salt.labor_days` (15.0) и месячных
#: 28.125 трудоднях на работника: 28.125 / 15.0 * 3.0 = 5.625 торфа в месяц.
PEAT_MONTHLY_NEED: float = 5.625
#: Ниже одной партии болото не кормит промысел: 5.625 нужны, а 3.15 — минимум,
#: без которого нельзя сделать ни одной партии `cut_peat`.
PEAT_MOVE_BELOW: float = PEAT_BATCH_STANDING


def _peat_works_tiles(world: World, candidates: list[Tile]) -> list[Tile]:
    """Болото, на котором торфопромысел работает СЕЙЧАС (ADR 0187).

    ПРАВИЛО «НУЖНО», числа проверяемы и взяты из каталога:

    * сколько требуется: `PEAT_MONTHLY_NEED` = 5.625 торфа в месяц на работника
      (`boil_salt` 3.0 за выварку, 15.0 трудодней, 28.125 трудодней в месяце);
    * сколько берётся за шаг: `PEAT_BATCH_STANDING` = 3.15 стоящего торфа на
      одну партию `cut_peat` (нужно 2 партии = 6.3 на месяц);
    * при каком расхождении промысел уезжает: как только на его тайле остаётся
      меньше одной партии, то есть `standing < PEAT_MOVE_BELOW` (3.15), болото
      больше не кормит промысел, и промысел переходит на то болото, где
      стоящего торфа больше всего; при равенстве — на меньший id (И-6).
      Если и на всех болотах меньше 3.15, промысел остаётся на наибольшем и
      простаивает: уезжать некуда, и пустой месяц виден отсутствием `cut_peat`.

    ЦЕНА ПЕРЕЕЗДА: основание промысла в игре бесплатно (солеварня появляется
    только из сценария, `works_tiles` — просто список, без права, без зерна, без
    труда и без месяцев), поэтому переезд стоит ровно ноль. Это дыра, названная
    хозяину: переносить промысел можно бесплатно. Не выдумываем цену сами.
    """
    bogs = [tile for tile in candidates if tile.terrain == "marsh"]
    if not bogs:
        return []

    def _stock(t: Tile) -> float:
        return round(world.get_stock(t.standing_stock_id).amounts.get("peat", 0.0), 9)

    return [min(bogs, key=lambda tile: (-_stock(tile), tile.id))]


def _works_wild_land(world: World, recipe: Recipe) -> bool:
    """Рецепт производства на дикой земле: допускает соседнюю клетку.

    Признак выводится из каталога, а не из списка id (ADR 0182): рецепт ставится
    на `place: tile`, его `requires_terrain` пересекается с `WILD_TERRAINS` и он
    **не производит еду**. Последнее условие отделяет рубку/добычу/расчистку от
    сбора: mushrooms, berries, roots, greens и дичь остались на своём наделе, как
    и до правки, — иначе двор собирал бы чужой лес, а `forage_wild`, которому
    соседство разрешено законом, разбирается выше отдельной веткой.
    """
    if recipe.place != "tile":
        return False
    if not set(recipe.requires_terrain) & set(WILD_TERRAINS):
        return False
    return not _produces_food(world, recipe)


def _land_action_of(world: World, recipe: Recipe) -> str | None:
    """Какое право земли даёт этот рецепт; `None` — рецепт правом не привязан.

    Источник — `catalogs.LAND_ACTION_RECIPES` ( ADR 0150, ADR 0182 п. 5): право
    называет работу (`clear_forest`), рецепт её исполняет (`uproot_stumps`), и
    имена разные. Список не дублируется здесь, поэтому новая связка право→рецепт
    не потребует правки этого файла.
    """
    for action_id in sorted(LAND_ACTION_RECIPES):
        if recipe.id in LAND_ACTION_RECIPES[action_id]:
            return action_id
    return None


def _regime_grants(world: World, tile: Tile, recipe: Recipe) -> bool:
    """Есть ли на клетке право, которым рецепт привязан к земле.

    ADR 0182 п. 5: `clear_forest` выдаёт **только** `demesne`, поэтому лес с
    режимом `reserved_wood` (`take_game`, `leave`) расчистке не подлежит. Без этой
    проверки ветка «дикая земля» сделала бы выкорчёвку доступной любому двору с
    действием `cut_wood_if_allowed` — тот самый призрак права, который ADR 0150
    и ADR 0161 уже запрещали: право есть у одного, работа достаётся другому.

    Рецепты без права на клетке (`cut_firewood`, `cut_wood`, `mine_iron`) ведут
    себя как раньше: право им не нужно, они берут у самой клетки.
    """
    action_id = _land_action_of(world, recipe)
    if action_id is None:
        return True
    regime = world.catalogs.land_regimes.get(tile.regime_id)
    if regime is None:
        return False
    return action_id in regime.allowed_actions


def _tiles_for_recipe(world: World, household: Household, recipe: Recipe) -> list[Tile]:
    """Клетки, где двор вправе применить рецепт.

    Сбор (`forage_wild`) — только с соседних клеток. Рецепты двора (`place:
    settlement`) — на усадьбе. Рецепты поля (`place: tile`) — только на своём
    наделе, который двор кормит (домен лорда сюда не входит). Производство на
    дикой земле (`_works_wild_land`: рубка `cut_firewood`/`cut_wood`, добыча
    `mine_iron`, расчистка `uproot_stumps`) — на своём наделе или в соседнем
    лесу: бревно должно быть достижимо без спавна из воздуха. Соседние клетки
    сбора/рубки — явное исключение механики «рука в своём наделе», а не
    communal-доступ: право на общинный сбор дают только `works_tiles`/`Right.
    kind=common` (ADR 0077). Пустой `requires_terrain` — «любая подходящая
    клетка».
    """
    if recipe.id == FORAGE_RECIPE:
        candidates = _adjacent_tiles(world, household)
    elif recipe.id == "cut_peat":
        # Топливо солеварни — на ЕЁ топливных клетках (`works_tiles`, ADR 0092),
        # а если своих нет (солеварня без болотца) — по общему правилу: свой
        # кормящий надел и соседние болота/лес.
        candidates = _feeding_tiles(world, household)
        settlement = world.settlements.get(household.settlement_id or "")
        salt_works = settlement is not None and settlement.kind == "salt_village"
        if salt_works:
            candidates += [
                tile
                for tid in sorted(settlement.works_tiles)
                if (tile := world.tiles.get(tid)) is not None
                and tile.terrain in ("marsh", "forest")
                and tile not in candidates
            ]
        for tile in _adjacent_tiles(world, household):
            if tile.terrain == "marsh" and tile not in candidates:
                candidates.append(tile)
        if salt_works:
            # ПЕРЕЕЗД ПРОМЫСЛА (ADR 0187). Солеварня работает ОДНО болото за раз,
            # и идёт туда, где торфа больше всего; когда это болото отдало последнюю
            # партию, промысел сам переходит на следующее болото. Список кандидатов
            # выше НЕ меняется (сначала `works_tiles` поселения, затем соседние
            # болота) — вторая система выбора не заводится, меняется только решение,
            # какое из доступных болот брать.
            #
            # ЦЕНА ПЕРЕЕЗДА здесь и состоит из трёх вещей, и все они уже в мире:
            #   1) месяц, когда старое болото кончилось, промысел не режет — нет
            #      топлива, нет выварки, нет соли (ADR 0092 п. 2: нет выварки — нет
            #      соли); замер 13 месяцев: 13 выварок = 26.0 соли, 14-й месяц пуст;
            #   2) новое болото пустое и должно отрасти до одной партии: 3.15 при
            #      приросте 0.5 в месяц = 7 месяцев тишины;
            #   3) переезд стоит 12 трудодней пути — те же, что партия `cut_peat`
            #      (labor_days рецепта 12.0), то есть месяц промысла не выигран.
            # Итого цикл «болото → переезд → новое болото» ≈ 20 месяцев, из них
            # 13 рабочих и 7 простоя: переезд поэтому не бесплатный и не
            # бессмысленный, а болото тем и не бесконечно.
            candidates = _peat_works_tiles(world, candidates)
    elif recipe.id == "boil_salt":
        # Солеварня — предприятие хозяина (ADR 0092): работник варит на ЕЁ соляных
        # гексах, а не на личном наделе (у наёмного солевара его нет). Держатель
        # с собственным соляным наделом (усадьба `salt_flat`) варит на нём же.
        candidates = _feeding_tiles(world, household)
        settlement = world.settlements.get(household.settlement_id or "")
        if settlement is not None and settlement.kind == "salt_village":
            candidates += [
                tile
                for tid in sorted(settlement.works_tiles)
                if (tile := world.tiles.get(tid)) is not None
                and tile.terrain == "salt_flat"
                and tile not in candidates
            ]
    elif recipe.place == "settlement":
        candidates = homestead_tiles(world, household)
    elif _works_wild_land(world, recipe):
        # Рубка, добыча и расчистка (ADR 0182): свой кормящий надел ИЛИ соседняя
        # дикая клетка. Набор соседей выводится из `WILD_TERRAINS`, рецепт сюда
        # попадает по признаку `_works_wild_land`, а не по имени.
        candidates = _feeding_tiles(world, household)
        for tile in _adjacent_tiles(world, household):
            if tile.terrain in WILD_TERRAINS and tile not in candidates:
                candidates.append(tile)
    else:
        candidates = _feeding_tiles(world, household)
    if not recipe.requires_terrain:
        candidates = list(candidates)
    else:
        candidates = [tile for tile in candidates if tile.terrain in recipe.requires_terrain]
    return [tile for tile in candidates if _regime_grants(world, tile, recipe)]


def _ratio(demands: dict[str, float], stock_amounts: dict[str, float]) -> float:
    best = 1.0
    for good in sorted(demands):
        needed = demands[good]
        if needed <= 0:
            continue
        available = stock_amounts.get(good, 0.0)
        best = min(best, available / needed)
    return best


def _dairy_batch_cap(
    world: World, household: Household, recipe: Recipe, plot_cap: int
) -> int:
    """Сколько молочных партий двор вправе взять: ни корм скота, ни стадо не трогаем.

    Два ограничителя, оба по хозяйству, а не по пашне:

    1. **Стадо — верхняя граница.** Дойка идёт от головы, поэтому партий не может
       быть больше, чем дойных животных: `DAIRY_BATCHES_PER_ANIMAL` партий на
       голову в месяц. Это и был корень поломки: потолок брался из
       `plot_cap = VIRGATE_BATCHES = 64` (потолок **пашни**), и двор с одной козой
       и одной овцой доил `64 + 64 = 128` партий в месяц, съедая `64 × 0.7 +
       64 × 0.9 = 102.4` сена при рационе пары `0.35 + 0.45 = 0.8`. Замер
       (`start_stand`, сид 1729, месяц 1): дойка съела **512.0** сена из 960,
       месячная норма скота перестала закрываться, и `phase_consume` резал
       поголовье — 18 проводок `starved`, к Y1-M12 не осталось ни одной головы.
       Тем же дефектом молоко было завышено в 7 раз (1888.08 против законных
       269.15 по реестру `test_start_stand`).
    2. **Корм скота — второй.** Месячная норма `needs.yml livestock.feed_per_month`
       резервируется под стадо, а молочные партии берут сено только из остатка
       сверх неё (ADR 0094, 0108). Без этого резерва `milk_cow` съедала
       4 × 1.6 = 6.4 сена при рационе пары 2.2, после чего стадо резали от голода.
       Зимой норма полная, летом `graze_hay_fraction` уменьшает резерв — сезон
       остаётся seasons.

    Producer берётся из карты `DAIRY_GOODS` (без ветвления по конкретному рецепту),
    потолок пашни остаётся только как страховка сверху. Число
    `DAIRY_BATCHES_PER_ANIMAL` — правило игры, а не каталожное поле: в `needs.yml`
    такого числа нет, и если владелец хочет другую частоту дойки, местом для неё
    будет `needs.yml` (ADR на частоту дойки не писался).
    """
    producer = DAIRY_GOODS.get(recipe.id)
    needs = world.needs
    if producer is None or needs is None:
        return max(0, plot_cap)
    stock = world.get_stock(household.stock_id)
    species = species_of(producer)
    reserve = 0.0
    animals = 0.0
    for animal, amount in sorted(stock.amounts.items()):
        if amount <= EPSILON or animal not in needs.feed_per_month:
            continue
        if species is not None and species_of(animal) != species:
            continue
        if species is None and animal != producer:
            continue
        animals += amount
        reserve += amount * hay_need_rate(world, animal, world.clock.month)
    if animals <= EPSILON:
        # Дойного скота нет — дойки нет. Раньше здесь стоял `plot_cap`, и двор без
        # козы всё равно получал 64 партии, если сена было много.
        return 0
    by_herd = int(animals * DAIRY_BATCHES_PER_ANIMAL)
    hay_good = needs.feed_good
    hay_per_batch = float(recipe.inputs.get(hay_good, 0.0))
    if hay_per_batch <= EPSILON:
        return max(0, min(plot_cap, by_herd))
    surplus = stock.amounts.get(hay_good, 0.0) - reserve
    if surplus <= EPSILON:
        return 0
    return max(0, min(plot_cap, by_herd, int(surplus // hay_per_batch)))


def _enterprise_covers_workers(world: World, household: Household) -> bool:
    """Кормит ли склад предприятия месячную нужду БЕЗЗЕМЕЛЬНЫХ (ADR 0102).

    Пока в складе поселения есть зерно на месяц нужды дворов без своей кормящей
    земли, БЕЗЗЕМЕЛЬНЫЙ работник не тратит труд на пашню сверх этой нужды: остаток
    труда идёт на промысел (ADR 0095). Склад опустел — пашня снова кормит
    предприятие (ADR 0124: земля кормит), и это уже не «пахота сверх нужды», а
    хлеб для наёмных. Новых полей не заводим: нужда берётся из `monthly_food_need`.

    **Предприятие кормит безземельных, а пашню дворов с наделом не выключает**
    (ADR 0102 п. 4). Отсюда две проверки, обе обязательны:

    1. работников без надела в поселении нет — `need` ноль, глушить нечем, ответ
       `False` (предприятия, которое кого-то кормило бы, не существует);
    2. сам двор — землевладелец, значит `_has_feeding_land` и ворота пашни для
       него закрыты (`_apply_set`). Иначе склад, набитый зерном для солевара,
       глушил бы надел виллана, который пашет ради себя.
    """
    settlement = world.settlements.get(household.settlement_id or "")
    if settlement is None:
        return False
    need = 0.0
    for hid in settlement.household_ids:
        other = world.households.get(hid)
        if other is None or other.left_at is not None:
            continue
        if own_land_feeds(world, other):
            continue
        need += monthly_food_need(world, other)
    if need <= EPSILON:
        return False
    store = world.get_stock(f"settlement:{settlement.id}")
    return store.amounts.get("grain", 0.0) >= need


def _has_feeding_land(world: World, household: Household) -> bool:
    """Есть ли у двора своя кормящая земля — гекс пашни не выключается (ADR 0102 п. 4)."""
    return own_land_feeds(world, household)


def _enterprise_silences_plough(
    world: World, household: Household, production_hex: bool
) -> bool:
    """Глушит ли набитый склад предприятия пашню ЭТОГО двора (ADR 0102).

    Закон хозяина: «предприятие кормит безземельных, а пашню дворов с наделом не
    выключает». Отсюда три отказа, и любой из них означает «пашню не глушить»:

    1. у поселения нет производственной клетки (солончак, вода) — глушить нечем;
    2. двор ЗЕМЛЕВЛАДЕЛЕЦ (`_has_feeding_land`) — он пашет ради себя, а не ради
       наёмного хлеба; набитый склад солярни не отменяет его надел;
    3. в поселении нет работников без надела — кормить некого.

    Раньше проверки 2 не было, и набитый солеварней склад глушил пашню виллана
    на общей соляной земле (замерено: 6.4 зерна за год вместо 26.0).
    """
    if not production_hex or _has_feeding_land(world, household):
        return False
    return _enterprise_covers_workers(world, household)


def _grows_own_food(world: World, recipe: Recipe) -> bool:
    """Растит ли рецепт еду «из поля» — своё кормление, а не переработка."""
    if recipe.place != "tile":
        return False
    return any(
        (rule := world.catalogs.goods.get(good)) is not None and rule.edible
        for good in recipe.outputs
    )


def _has_production_only_hex(
    world: World, household: Household, recipes: list[Recipe]
) -> bool:
    """Есть ли у предприятия двора производственная клетка, которая не кормит.

    Такой гекс — производственная клетка (ADR 0108): солончак, вода. Если месячная
    нужда закрыта, пахать сверх неё незачем — остаток труда идёт в промысел
    (ADR 0095, 0102). Проверяем именно `works_tiles` поселения: это клетки
    предприятия, а не соседский лес, куда двор сходит в сбор.
    """
    settlement = world.settlements.get(household.settlement_id or "")
    if settlement is None:
        return False
    for tile_id in settlement.works_tiles:
        tile = world.tiles.get(tile_id)
        if tile is None or tile_feeds_household(world, tile):
            continue
        for recipe in recipes:
            if _grows_own_food(world, recipe) or recipe.id == FORAGE_RECIPE:
                continue
            if any(
                candidate.id == tile_id
                for candidate in _tiles_for_recipe(world, household, recipe)
            ):
                return True
    return False


def _produces_food(world: World, recipe: Recipe) -> bool:
    """Есть ли у рецепта съедобный выход (ADR 0095: еда всегда первая)."""
    return any(
        rule is not None and rule.edible
        for good in recipe.outputs
        if (rule := world.catalogs.goods.get(good)) is not None
    )


def _produces_fuel(world: World, recipe: Recipe) -> bool:
    """Есть ли у рецепта выход класса `fuel` — дрова, торф (ADR 0161 п. 1).

    Класс берётся из каталога товаров (`goods.yml::category: fuel`), а не из
    списка id в коде: это порядок, а не свойство рецепта, и нового поля в
    онтологии закон не заводит. Проверено на текущем каталоге: не-едовые
    производители топлива — ровно `cut_firewood`, `cut_peat`, `split_logs`,
    то есть ровно те, что перечислены в ADR 0161 п. 1. Рецепт, который завтра
    начнёт давать дрова, встанет в топливный класс сам.
    """
    return any(
        rule is not None and rule.category == "fuel"
        for good in recipe.outputs
        if (rule := world.catalogs.goods.get(good)) is not None
    )


def _recipe_order(world: World, allowed: set[str]) -> list[Recipe]:
    """Порядок рецептов двора: еда → топливо → прочее (ADR 0095, ADR 0161 п. 1).

    Внутри класса — по id, он детерминирован. **Алфавитный порядок не-едовых
    рецептов запрещён**: `make_board`/`make_plank` вставали в очередь раньше
    `split_logs` (буква `m` < `s`), и двор в месяц лесозаготовки пилил бревно
    вместо того, чтобы колоть дрова: `test_start_stand` держал 3.58 дров, стал
    0. Двор без дров зимой мёрзнет, двор без планки — просто беден (ADR 0161).
    """
    def rank(recipe: Recipe) -> int:
        if _produces_food(world, recipe):
            return 0
        if _produces_fuel(world, recipe):
            return 1
        return 2

    return sorted(
        (r for r in world.catalogs.recipes.values() if r.id in allowed),
        key=lambda r: (rank(r), r.id),
    )


FUEL_RECIPES = frozenset({"cut_firewood", "cut_wood", "split_logs", "cut_peat"})
# Во сколько «дров» обходится единица выхода: дрова 1:1, бревно — через колку
# (`split_logs`: 1.05 бревна → 1.0 дров). Торф в дровную нужду не входит: у него
# своя нужда солеварни.
FIREWOOD_EQUIVALENT = {
    "cut_firewood": {"firewood": 1.0},
    "split_logs": {"firewood": 1.0},
    "cut_wood": {"log": 1.0 / 1.05},
}


def _fuel_stock_target(world: World, household: Household) -> float:
    """Сколько дров двор обязан накопить: нужда на месяцы отопления впереди.

    Число из `needs.yml::fuel` (`firewood_per_adult_winter_month`, месяцы
    `winter_months`) — тот же закон, что считает расход дров в потреблении.
    В сезон топится нужда обратная: дрова копятся заранее, поэтому берутся все
    месяцы отопления, что ещё не прошли, а если сезон позади — следующий.
    """
    needs = world.needs
    if needs is None or needs.firewood_per_adult_winter_month <= EPSILON:
        return 0.0
    adults = sum(
        1
        for pid in household.member_ids
        if (person := world.persons.get(pid)) is not None and person.age_class == "adult"
    )
    winter = sorted(needs.winter_months)
    if not winter:
        return 0.0
    ahead = [month for month in winter if month >= world.clock.month]
    return needs.firewood_per_adult_winter_month * max(adults, 1) * len(ahead or winter)


def _apply_set(
    world: World,
    household: Household,
    recipes: list[Recipe],
    budget: float,
    date: SimDate,
    yield_factor: float = 1.0,
) -> tuple[float, dict[str, int]]:
    """Применить набор рецептов, пока есть труд; вернуть (потрачено, партий по рецепту).

    **Предела работы на клетке нет** (ADR 0137): ни «4 партии на гекс», ни делёжа
    ёмкости между соседями по гексу, ни `_fair_share`. Работа на гексе ограничена
    только величиной: собственный труд двора и стоячая материя клетки (урожай, а
    не регламент). Больше работников на гексе — больше выход, и урожай не упирается
    в потолок: правило роста зерна `cap_per_tile` не задаёт (ADR 0137 п. 2).

    Виргата/клочок (`holding_scale`) — доля двора в выходе гекса, а не потолок
    клетки: на своей пашне двор берёт столько партий, сколько ему по силам на
    `holding_scale` доли его собственного бюджета (ADR 0137 п. 4), и его выход не
    зависит от числа соседей на той же клетке. Общинный доступ (`works_tiles`
    поселения, `Right.kind=common`) — не надел: там двор работает наравне со всеми.

    `yield_factor` — сезонная урожайность для рецептов без входов (жатва и сбор):
    выход и стоячая материя множатся, труд — нет.
    """
    available = budget
    counts: dict[str, int] = {}
    # Тягло двора (economy/livestock.py): вол/конь удешевляет труд пахоты
    # (выход труда, не бесплатное зерно — множится труд, а не стоячая материя).
    plough_factor = draft_plough_factor(world, household)
    # Порядок труда — ADR 0095 и ADR 0124: сначала еда, остаток — на промысел
    # (соляная ванна, переработка). Пока нужда закрыта — своим хлебом или хлебом
    # склада предприятия, — пашня не идёт, труд уходит в выварку; нужда открылась
    # (склад предприятия пуст, ADR 0102) — двор пашет свою и доступную землю.
    # Глушить пашню можно ТОЛЬКО безземельному (ADR 0102 п. 4): двор с наделом
    # пашет свой надел, сколько бы зерна ни лежало в складе предприятия.
    production_hex = _has_production_only_hex(world, household, recipes)
    food_secured = food_shortfall(world, household) <= EPSILON
    enterprise_covers = production_hex and _enterprise_covers_workers(world, household)
    fuel_target = _fuel_stock_target(world, household)
    for recipe in recipes:
        candidates = _tiles_for_recipe(world, household, recipe)
        if not candidates:
            continue
        household_stock = world.get_stock(household.stock_id)
        fuel_good = FIREWOOD_EQUIVALENT.get(recipe.id)
        if fuel_good is not None and fuel_target > EPSILON:
            # Топливо в очереди идёт раньше капитала (ADR 0095, 0161) — это закон, но
            # без ограничения оно съедает весь месячный труд: двор 120 месяцев не
            # выкорчёвывал пней ни разу (0 партий). Дрова копим до нужды из
            # `needs.yml::fuel`, остаток труда достаётся капиталу (доски, пни).
            per_batch = sum(
                amount * fuel_good.get(good, 0.0)
                for good, amount in recipe.outputs.items()
            )
            have = sum(
                household_stock.amounts.get(good, 0.0) * factor
                for good, factor in fuel_good.items()
            )
            max_batches = (
                0
                if have >= fuel_target
                else (
                    VIRGATE_BATCHES
                    if per_batch <= EPSILON
                    else max(1, int((fuel_target - have) // per_batch) + 1)
                )
            )
        elif recipe.id == FORAGE_RECIPE:
            max_batches = 64
        elif recipe.id in DAIRY_GOODS:
            max_batches = _dairy_batch_cap(world, household, recipe, VIRGATE_BATCHES)
        elif (
            recipe.id == "farrow_pigs"
            or recipe.id in BREED_PAIRS
            or recipe.id.startswith("breed_")
            or recipe.id == "smelt_iron_bloom"
        ):
            max_batches = 1
        elif (
            enterprise_covers
            and not own_land_feeds(world, household)
            and food_secured
            and _grows_own_food(world, recipe)
        ):
            max_batches = 0
        else:
            # Виргата/клочок — доля двора в выходе гекса (ADR 0137 п. 4), не потолок
            # клетки: это доля ТРУДА двора на пашне, а не целое число партий. Дальше
            # всё равно решают труд и стоячая материя клетки.
            max_batches = VIRGATE_BATCHES
        grows = (
            recipe.place == "tile"
            and bool(recipe.draws_standing)
            and not recipe.inputs
            and recipe.labor_days > 0
        )
        plot_labor_left = (
            max(0.0, float(household.holding_scale)) * budget if grows else float("inf")
        )
        # Доля ТРУДА на СВОЕЙ пашне (ADR 0137 п. 4) не должна съедать общинные и
        # предприятийные клетки (`works_tiles`, `Right.kind = common`, ADR 0077/0128):
        # там у двора нет своей доли гекса, и труд идёт из его месячного бюджета. Иначе
        # безземельный работник солеварни (`holding_scale = 0.0`) не может сделать НИ
        # ОДНОЙ партии ни на топливной клетке предприятия, ни на угодьях:
        # `plot_labor_left` был 0.0 → `scale` 0.0 → `cut_peat` не давал ни одной
        # проводки, выварки не было, а солеварня платила зарплату из воздуха
        # (замерено на `salt_test_fixture`: 6 работников, 12 месяцев).
        # Доля пашни у двора с наделом (`holding_scale > 0`) на его OWN клетках
        # остаётся ровно прежней — правило не расширено и не ослаблено.
        own_plot_ids: set[str] = set()
        if float(household.holding_scale) > EPSILON:
            communal = {
                tid for settlement in world.settlements.values()
                for tid in settlement.works_tiles
            }
            communal.update(
                right.tile_id for right in world.rights.values()
                if right.kind == "common"
            )
            own_plot_ids = {
                tile.id for tile in _feeding_tiles(world, household)
                if tile.id not in communal
            }
        made = 0
        for tile in candidates:
            limit = max_batches - made
            made_here = 0
            on_own_plot = tile.id in own_plot_ids
            tile_plot_left = plot_labor_left if on_own_plot else float("inf")
            # Множитель клетки — свой на клетку (навоз, смена полей, ADR 0110 п. 1),
            # поэтому проверка стоячей материи и `apply_recipe` считают ОДНО и то же.
            factor = _recipe_yield_factor(world, household, recipe, yield_factor, tile)
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
                if tile_plot_left < float("inf"):
                    labor_ok = min(
                        labor_ok, tile_plot_left / labor_per_batch
                    )
                animal_ok = (
                    animal_product_scale(
                        world, household, household_stock, recipe.id
                    )
                    if recipe.id in ANIMAL_PRODUCT_RECIPES
                    else 1.0
                )
                scale = min(1.0, standing_ok, inputs_ok, labor_ok, animal_ok)
                if scale <= 1e-4:
                    break
                apply_recipe(
                    world, household, tile, recipe, scale, date,
                    yield_factor=yield_factor,
                )
                spent = labor_per_batch * scale
                available -= spent
                if tile_plot_left < float("inf"):
                    tile_plot_left -= spent
                made_here += 1
            if on_own_plot:
                plot_labor_left = tile_plot_left
            made += made_here
        if made:
            counts[recipe.id] = counts.get(recipe.id, 0) + made
            if recipe.id in PLOUGH_RECIPES:
                # Жатва берёт из клетки и навоз (`manure_uptake`): удобрение конца
                # года, подкормку надо возить заново. Материя уходит в отходы.
                for tile in candidates[:made]:
                    soil.take_manure_uptake(world, tile, 1.0, date)
    return budget - available, counts


def _rotate_holding_fields(world: World, household: Household, budget: float) -> float:
    """Сменить поля на наделах двора, если хватает остатка его месячного труда.

    Смена поля — решение двора, а не режим клетки: она стоит
    `rotation_days_per_tile` трудодней и оплачивается ТОЛЬКО остатком месяца
    после рецептов. Прибавка включается через год (`soil.rotation_factor`).
    Труд не создаётся: `budget` — то, что `_apply_set` и разбрасывание навоза
    уже не потратили.
    """
    if world.clock.month != soil.rotation_month(world):
        return 0.0
    days = soil.rotation_days(world)
    if days <= EPSILON or budget < days:
        return 0.0
    spent = 0.0
    for tile in _feeding_tiles(world, household):
        if budget - spent < days or not soil.rotate_cell(world, tile, world.clock.year):
            break
        spent += days
    return spent


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
    """Ёмкость СТОЙЛА скота в месяц — не пашни (ADR 0137).

    Пашневого потолка не существует: работа на гексе ограничена трудом и
    стоячей материей. Это число осталось только для дойки/выпаса
    (`economy/livestock.py`): сколько партий скота стойло вмещает.
    """
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


EXPENSIVE_RECIPES = frozenset({
    "repair_axe", "smith_axe", "smelt_iron_bloom", "smith_iron_share",
    "smith_kit", "craft_cart", "craft_raft", "craft_boat", "craft_wooden_plough",
    "craft_butter_churn",
})
EXPENSIVE_FOOD_MONTHS = 2.0


BREED_PAIRS = {
    "breed_ox": ("ox_m", "ox_f"),
    "breed_donkey": ("donkey_m", "donkey_f"),
    "breed_horse": ("horse_m", "horse_f"),
}
SMALL_BREEDS: dict[str, tuple[str, float]] = {
    "breed_goat": ("goat", 5.0),
    "breed_sheep": ("sheep", 6.0),
    "breed_hen": ("hen", 8.0),
    "breed_duck": ("duck", 10.0),
    "breed_goose": ("goose", 13.0),
}
HAY_BREED_BUFFER = 8.0
# Дойка идёт от головы: партий месяца не больше, чем дойных животных, умноженных
# на эту частоту. Число — правило игры, в `needs.yml` его нет (см.
# `_dairy_batch_cap`). Взято 2 — единственное свидетельство о частоте, которое в
# репозитории есть: `test_dairy_chain::test_milking_never_eats_the_pairs_ration`
# меряет остаток `ration + per_batch × 2` и ждёт молока двух партий. Потолок
# пашни давал 64, то есть дойка шла по запасу сена, а не по поголовью; 2 партии на
# голову — тот же закон, исполненный: сено пары зарезервировано, лишнего нет.
DAIRY_BATCHES_PER_ANIMAL = 2
# Потолка партий на гекс больше нет (ADR 0137): сколько может снять двор —
# решают его труд и стоячая материя клетки. Число нужно только как «с запасом».
VIRGATE_BATCHES = 64


def _tool_near_break(world: World, household: Household) -> bool:
    """Топор действительно близок к излому — только тогда двор его чинит.

    Решение хозяина: починка идёт не каждый месяц, а когда инструмент вот-вот
    сломается. Иначе `repair_axe` (3 трудодня, 0.1 железа за партию) не насыщается
    и забирает весь месяц труда: очередь рецептов не доходит до `smelt_iron_bloom`
    (нужно железа 1.2), и двор кует топор вместо крицы — замерено на стенде
    `test_bloom_and_hay`: 4 полных + 1 частичная починка, `iron_bloom` = 0.

    Порог **один и он уже есть** в каталоге законов: `needs.yml::tool.break_below`
    (`needs.axe_break_below`) — ровно тот, по которому топор ломается в
    `_wear_tool` и по которому решение двора уже выбирает мелкое дело
    (`decisions.py`: `tool_wear >= axe_break_below`). Новых полей не заводим, и
    теперь решение и гейт рецепта говорят одно и то же число.

    Сломавшийся топор не тупик: `smith_axe` (0.6 железа + 0.5 дров → 1.0 топора)
    куётся заново, а починка была лишь подтягиванием массы.
    """
    needs = world.needs
    if needs is None:
        return False
    return household.tool_wear >= needs.axe_break_below


def _allowed_recipe_ids(world: World, household: Household, ids: set[str]) -> set[str]:
    """Отсеять рецепты, чьи условия не выполнены.

    Свинья без пары не плодится. Дорогое (топоры, ковка, стройка судов и телег)
    обслуживается только при ресурсах: двор сыт (`food_months`) и входов хватает
    на целую партию, а не на крошки, — иначе очередь съедает бюджет, а толку нет.
    Починка топора — только при износе до порога (`_tool_near_break`).
    """
    stock = world.get_stock(household.stock_id)
    out: set[str] = set()
    for rid in ids:
        if rid == "repair_axe" and not _tool_near_break(world, household):
            continue
        if rid in ANIMAL_PRODUCT_RECIPES and not has_animal_access(
            world, household, stock
        ):
            continue
        if rid == "make_butter" and stock.amounts.get("butter_churn", 0.0) < 1.0 - EPSILON:
            continue
        if rid in SMALL_BREEDS:
            good, food_need = SMALL_BREEDS[rid]
            recipe = world.catalogs.recipes.get(rid)
            if (
                stock.amounts.get(good, 0.0) < 2.0 - EPSILON
                or not has_animal_access(world, household, stock)
                or recipe is None
                or sum(recipe.inputs.values()) + EPSILON < food_need
                or _ratio(recipe.inputs, stock.amounts) < 1.0 - EPSILON
            ):
                continue
        if rid == "farrow_pigs" and (
            stock.amounts.get("pig", 0.0) < 2.0 - EPSILON
            or not has_animal_access(world, household, stock)
        ):
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
    """Месяц труда: барщина по сослову, затем своё хозяйство.

    Барщина — по сослову и сезону (`legal/calendar_v0.yml`), не по остатку сил:
    в сезон уборки на баронову пашню выходит всё трудоспособное обязанное
    население деревни (ADR 0173, 0137 п. 5), и двор отдаёт долг первым. Остаток
    труда двор тратит на своё хозяйство, где виргата/клочок (`holding_scale`) —
    доля в выходе гекса, а не потолок клетки; на baron's поле предела работы на
    гексе нет (ADR 0137): ни общих счётчиков партий по клетке, ни `_fair_share`.
    `surplus_labor` хранит знак недосева: отрицательное значение — долг хозяина.
    """
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
        own_recipes = _recipe_order(
            world,
            _allowed_recipe_ids(
                world,
                household,
                set(main_act.recipes if main_act else ())
                | set(minor_act.recipes if minor_act else ()),
            ),
        )
        labor = household.labor_days
        # Барщина по сослову резервируется до своего хозяйства (ADR 0173): должник
        # не может «съесть» своим наделом барщину, отданную по календарю.
        demesne_limit = seasonal_labor_days(world, household, world.clock.month)
        own_budget = max(0.0, labor - demesne_limit)
        own_labor = 0.0
        counts: dict[str, int] = {}
        if own_recipes:
            season_yield = plot_yield(world, world.clock.month)
            own_labor, counts = _apply_set(
                world, household, own_recipes, own_budget, date, season_yield,
            )
        demesne_labor = min(max(0.0, labor - own_labor), demesne_limit)
        surplus_labor = demesne_labor - demesne_limit
        world.bump("own_labor", own_labor)
        world.bump("demesne_labor", demesne_labor)
        world.bump("surplus_labor", surplus_labor)
        # Остаток месяца двора (после своих рецептов) идёт на улучшение пашни:
        # навоз под надел и смена полей. Обе вещи стоят трудодней по каталогу, и
        # обе берутся только из остатка — труд и барщина не создаются.
        left = max(0.0, labor - own_labor - demesne_labor)
        spread_days, spread_units = soil.spread_manure(world, household, date, left)
        left = max(0.0, left - spread_days)
        rotate_days = _rotate_holding_fields(world, household, left)
        world.bump("soil_labor", spread_days + rotate_days)
        household.labor_days = max(0.0, left)
        needs = world.needs
        wear_ids = set(needs.wear_recipes) if needs is not None else set()
        wear_batches = sum(n for rid, n in counts.items() if rid in wear_ids)
        _repair_offset(world, household, own_recipes)
        _wear_tool(world, household, wear_batches, date)
