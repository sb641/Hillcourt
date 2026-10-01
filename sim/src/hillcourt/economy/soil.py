"""Прибавки урожайности пашни: плуг и тягло, навоз, смена полей.

Три улучшения хозяина — **множители выхода гекса**, а не потолки и не особые
режимы клетки. Все числа лежат в каталоге (`design/catalogs/spawn_rules.yml`,
правило `grow_grain`, поле `params`); модуль их только читает:

1. **Пахота с буйволом и плугами.** `tool_yield` — плуг/лемех в стоке двора
   (ADR 0110, `tool_multiplier`); `draft_yield_per_head` — **прибавка за голову
   тягла**, и прибавку даёт буйвол/бык (`ox`), который стоит в каталоге отдельной
   строкой от прочих. Голова — стойловая единица по норме
   `needs.yml.livestock.feed_per_month` (вол 1.2, корова 1.0), и считаются только
   взрослые: телята в плуг не идут. Двор без плуга и без тягла получает ровно 1.0.
2. **Удобрение.** Навоз — **материал по лестнице** (рецепт `make_manure` из
   сена, `goods.yml::manure`), а не «особый режим». Разложенный навоз лежит
   стоячей материей клетки; множитель `manure_*` **считается из числа скота**,
   который пашет/кормит у этой клетки, по стойловой норме
   `needs.yml.livestock.feed_per_month` (вол 1.2, корова 1.0 сена/мес), и
   потолка `manure_yield_cap` не имеет.
3. **Смена полей.** Единственный множитель, зависящий от времени: клетка,
   сменившая поле, получает прибавку **через год**, а не в год смены. Состояние
   клетки — одна метка года в `world.stats` (тот же приём, что `graze_used` в
   `livestock.py`): детерминировано, входит в `state_hash`, без LLM и без
   обхода карты.

Норма выхода гекса **домена** — не прибавка, а размен: `demesne_base_yield`
(`grow_grain.params`) держит 300–350 зерна в год при полном спросе, и читают его
две функции с разных сторон — `engine/growth.py` (стоячая материя) и
`tile_yield_factor` (выход). Наделы двора держат 1.0: их числа (ADR 0137 п. 1)
другая норма.

`cell_yield_factor`/`tile_yield_factor` множатся в `labor._recipe_yield_factor`
и в `manor._work_demesne` — то есть и на выход, и на снимаемую стоячую материю
(иначе спишется больше, чем вырастет).
"""

from __future__ import annotations

import weakref

from ..engine.yield_law import demesne_field_factor
from ..ontology import Household, SimDate, Tile
from ..world import World
from .livestock import DRAFT_GOODS, FEMALE, MALE, SPECIES, YOUNG, can_hold_horse

EPSILON = 1e-9
GROW_RULE = "grow_grain"
MANURE_GOOD = "manure"
WASTE_STOCK_ID = "sink:waste"
ROTATED_KEY = "field_rotated:"

# Множитель 1.0 — «прибавки нет». Дублей чисел каталога в модуле нет (ADR 0157
# п. 5): все числа прибавок лежат в `spawn_rules.yml::grow_grain.params`, и
# отсутствие ключа — ошибка загрузки каталога (`catalogs.GROW_RULE_PARAMS`), а не
# повод подставить своё. Поэтому у `_number` и `_table` нет второго аргумента.
NO_YIELD = 1.0

# Инструмент по убыванию прибавки: железный лемех лучше деревянного плуга.
TOOL_PRIORITY: tuple[str, ...] = ("iron_share", "wooden_plough")


def _params(world: World) -> dict:
    """`grow_grain.params` — единственный источник прибавок урожайности пашни."""
    rule = world.catalogs.spawn_rules.get(GROW_RULE)
    if rule is None:
        raise ValueError(
            f"Правило появления '{GROW_RULE}' в каталоге отсутствует: прибавки "
            "пашни негде взять, а подставлять числа из кода запрещено (ADR 0157 п. 5)"
        )
    return dict(rule.params)


def _number(params: dict, key: str) -> float:
    """Число каталога `params[key]`; ключа нет — падаем, а не выдумываем."""
    if key not in params:
        raise ValueError(
            f"grow_grain.params: нет ключа '{key}' — урожай пашни посчитан быть не может "
            "(ADR 0157 п. 5)"
        )
    try:
        return float(params[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"grow_grain.params['{key}'] = {params[key]!r} — не число"
        ) from exc


def _table(params: dict, key: str) -> dict[str, float]:
    """Таблица каталога `params[key]`; ключа нет или значения не числа — падаем."""
    if key not in params:
        raise ValueError(
            f"grow_grain.params: нет ключа '{key}' — прибавка посчитана быть не может "
            "(ADR 0157 п. 5)"
        )
    raw = params[key]
    if not isinstance(raw, dict):
        raise ValueError(
            f"grow_grain.params['{key}'] = {raw!r} — нужна таблица вид → число"
        )
    out: dict[str, float] = {}
    for name in sorted(raw):
        try:
            out[str(name)] = float(raw[name])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"grow_grain.params['{key}']['{name}'] = {raw[name]!r} — не число"
            ) from exc
    if not out:
        raise ValueError(f"grow_grain.params['{key}'] — таблица пустая")
    return out


def tool_table(world: World) -> dict[str, float]:
    """Множители выхода от инструмента двора — из каталога."""
    return _table(_params(world), "tool_yield")


def draft_table(world: World) -> dict[str, float]:
    """Прибавка выхода за голову тягла по виду — из каталога (буйвол отдельно)."""
    return _table(_params(world), "draft_yield_per_head")


def draft_cap(world: World) -> float:
    """Потолок прибавки тягла — `grow_grain.params.draft_yield_cap`."""
    return _number(_params(world), "draft_yield_cap")


def tool_yield_factor(world: World, household: Household) -> float:
    """Множитель выхода от инструмента, лежащего в стоке двора.

    Нет инструмента — 1.0 (базовая линия не меняется); железный лемех даёт
    прибавку больше деревянного плуга. Если лежит и то, и другое, берётся лучший
    по `TOOL_PRIORITY`. Инструмент — материя: появиться он может только переводом
    (`grant_tool`) или рецептом, не из воздуха.
    """
    stock = world.get_stock(household.stock_id)
    table = tool_table(world)
    for good in TOOL_PRIORITY:
        if stock.amounts.get(good, 0.0) > EPSILON:
            return table.get(good, NO_YIELD)
    return NO_YIELD


def draft_heads(world: World, stock, species: str) -> float:
    """Стойловые головы ВЗРОСЛОГО тягла вида `species` в стоке `stock`.

    Голова — единица по норме `needs.yml.livestock.feed_per_month` (вол 1.2,
    корова 1.0, конь 1.0, осёл 0.7): это законная ставка рта, а не придумка
    модуля. Молодняк не считается — телёнок плуг не тянет.
    """
    needs = world.needs
    if needs is None:
        return 0.0
    total = 0.0
    for good in (MALE[species], FEMALE[species]):
        rate = float(needs.feed_per_month.get(good, 0.0))
        amount = stock.amounts.get(good, 0.0)
        if rate > EPSILON and amount > EPSILON:
            total += amount * rate
    return total


def draft_yield_factor(world: World, household: Household) -> float:
    """Множитель выхода гекса от тягла двора; буйвол/бык — своя строка каталога.

    Пахота с буйволом и плугами (слово хозяина) — **повышающий коэффициент**,
    и величина его **считается от числа скота**: стойловые головы `draft_heads`
    по норме `needs.yml` умножаются на прибавку за голову из каталога, срезаются
    потолком `draft_yield_cap` и складываются с базовой 1.0. Одна пара волов (1.2
    + 1.2) даёт 1.144, четыре (4.8 головы) — 1.288.

    Плуг тянет одна упряжка, поэтому берётся лучший вид, а не сумма видов; вид без
    строки в каталоге прибавки не даёт. Считается тягло, которое двор вправе
    держать (`can_hold_horse`: коня tied-двора не пашут).
    """
    stock = world.get_stock(household.stock_id)
    table = draft_table(world)
    cap = draft_cap(world)
    best = NO_YIELD
    for species in sorted(SPECIES):
        rate = float(table.get(species, 0.0))
        if rate <= EPSILON:
            continue
        if species == "horse" and not can_hold_horse(world, household):
            continue
        heads = draft_heads(world, stock, species)
        if heads > EPSILON:
            best = max(best, min(cap, NO_YIELD + rate * heads))
    return best


def demesne_base_factor(world: World, tile: Tile) -> float:
    """Множитель НОРМЫ домена (`demesne_base_yield`) — без прибавок самой клетки.

    Нужен стану манера для одной честной вещи: `apply_recipe` получает от стану
    СЕЗОН и сам домножает на `cell_yield_factor` (прибавки клетки: инструмент,
    тягло, навоз, смена полей). Если отдать ему уже полный множитель клетки, то
    прибавки считаются ДВАЖДЫ — а это и есть двойной счёт, из-за которого жатва
    домена брала из клетки больше стоящей материи, чем в ней было, и бухгалтерия
    отказывала (ADR 0106/0110: каждый множитель — ровно один раз).

    Наделам (`cell_yield_factor`) норма домена не полагается: `tile_yield_factor`
    = `demesne_base_factor` × `cell_yield_factor`, и именно это произведение —
    полный множитель клетки домена.
    """
    return demesne_field_factor(world, tile.id, _params(world))


def livestock_units(world: World, stock) -> float:
    """Стойловые головы ТЯГЛА одного стока по норме `needs.yml.livestock`.

    Учитываются только три вида, которые пашут и возят (`livestock.SPECIES`:
    вол/корова, осёл, конь) — их подстилка и есть полевое удобрение. Норма
    зимней стойловой потребности на голову (unit сена/мес) читается из каталога
    законов: вол 1.2, корова 1.0. Множитель навоза поэтому считается ОДНИМ
    числом по каталогу, а не частными приписками по видам; мелкий скот в счёт не
    идёт — его подстилка не удобряет пашню.
    """
    needs = world.needs
    if needs is None:
        return 0.0
    total = 0.0
    for species in SPECIES:
        for good in (MALE[species], FEMALE[species], YOUNG[species]):
            rate = float(needs.feed_per_month.get(good, 0.0))
            amount = stock.amounts.get(good, 0.0)
            if rate > EPSILON and amount > EPSILON:
                total += amount * rate
    return total


# --- Кэш суммы стойловых голов поселения -------------------------------------
#
# Сумма нужна `cell_yield_factor` на КАЖДУЮ клетку-кандидат каждого рецепта
# каждого двора: замер `v0_barony_100` — 24 565 вызовов `settlement_livestock_units`
# и 1 362 392 вызова `livestock_units` на один месяц. Пересчитывать её 24 565 раз
# незачем: внутри месяца сумма меняется в конечном числе мест, и все они в
# `economy/`, а значит могут позвать инвалидацию сами.
#
# Инвалидация ЧИСЛОВАЯ и трёхчастная, все части обязаны совпасть:
#   * `generation` — счётчик, который поднимают места изменения стада
#     (`touches_herd` ниже): рождение и взросление (`apply_recipe`), смерть от
#     голода (`starved`) и падёж (`died`);
#   * `len(household_ids)` — приход/исход двоя в поселении меняет состав;
#   * число ушедших дворов (`left_at`) — ушедший двор из суммы выпадает, а
#     список `household_ids` при этом не меняется.
# Смена месяца обнуляет всё: кэш живёт внутри мира (`WeakKeyDictionary`), новый
# месяц начинается с чистого состояния.
# `World` — датакласс без `__hash__`, в качестве ключа он не годится, а
# `WeakKeyDictionary` его не принимает. Поэтому ключ — `id(world)`, а рядом
# лежит слабая ссылка: она и защищает от переиспользования `id` после сборки
# мира, и убирает запись сама, когда мир умер.
_HERD_STATE: dict[int, tuple[dict, "weakref.ref[World]"]] = {}


def herd_state(world: World) -> dict:
    """Состояние кэша стада для мира (создаётся лениво)."""
    key = id(world)
    entry = _HERD_STATE.get(key)
    if entry is not None and entry[1]() is world:
        return entry[0]
    state = {"generation": 0, "sums": {}}
    _HERD_STATE[key] = (
        state,
        weakref.ref(world, lambda _ref, k=key: _HERD_STATE.pop(k, None)),
    )
    return state


def touches_herd(goods) -> bool:
    """Задевает ли набор товаров стойловое стадо (нужно для инвалидации кэша)."""
    return not DRAFT_GOODS.isdisjoint(goods)


def invalidate_herd_cache(world: World, goods=()) -> None:
    """Сказать «стадо изменилось» — числами, а не флагом.

    `generation` растёт, и следующий расчёт обязан его увидеть. Места вызова:
    `labor.apply_recipe` (рождение/взросление и любой оборот тягла), `livestock`
    при `starved` и `died`. Внешние места (приход и уход двоя) ловятся
    структурной частью ключа — длиной списка и числом ушедших.
    """
    if goods and not touches_herd(goods):
        return
    herd_state(world)["generation"] += 1


def settlement_livestock_units(world: World, settlement_id: str | None) -> float:
    """Стойловые головы поселения: склады живых дворов плюс склад поселения."""
    if not settlement_id:
        return 0.0
    settlement = world.settlements.get(settlement_id)
    if settlement is None:
        return 0.0
    state = herd_state(world)
    members = sorted(settlement.household_ids)
    left = sum(
        1
        for hid in members
        if (h := world.households.get(hid)) is None or h.left_at is not None
    )
    key = (world.clock.date, state["generation"], len(members), left)
    cached = state["sums"].get(settlement_id)
    if cached is not None and cached[0] == key:
        return cached[1]
    total = _settlement_livestock_units_scan(world, settlement, members)
    state["sums"][settlement_id] = (key, total)
    return total


def _settlement_livestock_units_scan(world: World, settlement, members) -> float:
    """Пересчёт суммы «как есть» — эталон, с которым сверяется кэш.

    Эталон держится отдельно и намеренно не кэшируется: на нём построены
    проверки `test_draft_livestock` (сумма кэша == перебор на семи сценариях) и
    мутация «сломать инвалидацию».
    """
    total = 0.0
    for hid in members:
        household = world.households.get(hid)
        if household is None or household.left_at is not None:
            continue
        if household.settlement_id != settlement.id:
            # Двор ушёл из поселения, а список ещё не починен — не считаем его тут.
            continue
        stock = world.stocks.get(household.stock_id)
        if stock is not None:
            total += livestock_units(world, stock)
    store = world.stocks.get(settlement.stores_stock_id)
    if store is not None:
        total += livestock_units(world, store)
    return total


def settlement_livestock_units_uncached(world: World, settlement_id: str | None) -> float:
    """Та же сумма без кэша — эталон для проверок (медленно, зато честно)."""
    if not settlement_id:
        return 0.0
    settlement = world.settlements.get(settlement_id)
    if settlement is None:
        return 0.0
    return _settlement_livestock_units_scan(
        world, settlement, sorted(settlement.household_ids)
    )



    """Стойловые головы поселения: склады живых дворов плюс склад поселения.

    Дворы берутся из `Settlement.household_ids`, а **не** перебором всех дворов
    мира. Функция зовётся из `cell_yield_factor` на КАЖДУЮ клетку-кандидат
    каждого рецепта каждого двора, то есть десятки тысяч раз в месяц, и старый
    перебор `world.households` делал её O(все дворы) на вызов: замер
    `v0_barony_100` (600 дворов, 12 поселений) — 24 565 вызовов в месяц,
    1 362 392 вызова `livestock_units` и 52 секунды на месяц только на навоз.
    Индекс `household_ids` даёт то же множество (сверено на всех семи сценариях:
    рассинхрона 0) за O(дворы поселения) — 12 раз меньше на этом мире.
    """
    if not settlement_id:
        return 0.0
    settlement = world.settlements.get(settlement_id)
    if settlement is None:
        return 0.0


def worker_livestock_units(world: World, household: Household) -> float:
    """Стойловые головы двора-работника: его сток плюс склад его поселения."""
    total = livestock_units(world, world.get_stock(household.stock_id))
    return total + settlement_livestock_units(world, household.settlement_id)


def manure_yield_factor(world: World, heads: float) -> float:
    """Множитель урожайности от навоза при `heads` стойловых головах.

    Линейный по головам, срезан потолком `manure_yield_cap`: без скота и без
    навоза множитель ровно 1.0, с большим стадом — не выше потолка.
    """
    if heads <= EPSILON:
        return NO_YIELD
    params = _params(world)
    per_head = _number(params, "manure_yield_per_head")
    cap = _number(params, "manure_yield_cap")
    return min(cap, 1.0 + per_head * heads)


def tile_manure_factor(world: World, tile: Tile, heads: float) -> float:
    """Множитель клетки от ЕЁ навоза, посчитанный от числа скота `heads`.

    Навоз на клетке — условие применения (материал лежит стоячей материей гекса,
    пока жатва его не взяла). Сама величина прибавки — от числа голов: больше
    скота при том же навозе — выше множитель.
    """
    return _tile_manure_factor_lazy(world, tile, lambda: heads)


def _tile_manure_factor_lazy(world: World, tile: Tile, heads_of) -> float:
    """`tile_manure_factor`, где число голов ДОСТАЁТСЯ ТОЛЬКО ПРИ НАВОЗЕ.

    Закон навоза без изменений: нет навоза на клетке — множитель ровно `NO_YIELD`
    и число голов НЕ ЧИТАЕТСЯ. Значит `heads_of` (а это `worker_livestock_units`:
    сумма по всем дворам поселения) не зовётся вовсе.

    ПОЧЕМУ ЭТО НЕ «ПОДГОНКА ПОД СЦЕНАРИЙ». Правило не про наличие навоза в мире
    «сегодня», а про единственного потребителя числа голов: `manure_yield_factor`.
    Если навоза нет — число голов не имеет НИ ОДНОГО наблюдаемого последствия,
    и ленивое вычисление даёт тот же самый ответ. На сцене, где навоз есть
    (подкормленный гекс), `heads_of()` зовётся ровно как раньше — оптимизация
    никогда не хуже исходной, она лишь не платит за неиспользуемый расчёт.

    ЧИСТОТА ЧИСЕЛ. `worker_livestock_units` — ЧТЕНИЕ: стоки, `world.needs` и
    мемо суммы поселения. Ни проводки, ни броска RNG, ни мутации мира (кроме
    самого мемо, который при возврате того же значения кладёт то же же).
    Поэтому «не посчитал» и «посчитал и не использовал» неразличимы — И-6
    соблюдена по построению, а не по проверке.
    """
    standing = world.get_stock(tile.standing_stock_id)
    if standing.amounts.get(MANURE_GOOD, 0.0) <= EPSILON:
        return NO_YIELD
    return manure_yield_factor(world, heads_of())


def rotation_year(world: World, tile: Tile) -> float:
    """В каком году клетка последний раз меняла поле (0 — не меняла)."""
    return float(world.stats.get(f"{ROTATED_KEY}{tile.id}", 0.0))


def rotation_factor(world: World, tile: Tile) -> float:
    """Прибавка смены полей — только на СЛЕДУЮЩИЙ год после смены.

    Единственный множитель, зависящий от времени: в год смены он ещё 1.0,
    через год — `rotation_yield`. Одно число на клетку: ни LLM, ни pathfinding.
    """
    rotated = rotation_year(world, tile)
    if rotated <= 0.0 or rotated >= float(world.clock.year):
        return NO_YIELD
    return _number(_params(world), "rotation_yield")


def cell_yield_factor(
    world: World, household: Household, tile: Tile, heads: float | None = None
) -> float:
    """Итоговый множитель клетки: инструмент × тягло × навоз × смена полей.

    `heads` — готовое число голов; `None` значит «посчитать самому», но и тогда
    оно считается ЛЕНИВО, через `_tile_manure_factor_lazy`: навоз есть не на
    каждой клетке, а сумма голов по поселению — самая дорогая величина в этой
    функции (замер: 13 % месяца, `settlement_livestock_units` O(члены поселения)).
    Навоз на месте — число голов считается как раньше.
    """
    if heads is None:
        manure = _tile_manure_factor_lazy(
            world, tile, lambda: worker_livestock_units(world, household)
        )
    else:
        manure = tile_manure_factor(world, tile, heads)
    return (
        tool_yield_factor(world, household)
        * draft_yield_factor(world, household)
        * manure
        * rotation_factor(world, tile)
    )


def tile_yield_factor(
    world: World, tile: Tile, worker: Household | None
) -> float:
    """Множитель клетки домена: норма домена, навоз и смена полей, плюс
    инструмент/тягло работника.

    Это ДОМЕН, а не надел двора: к нему прибавляется `demesne_base_yield`
    (`grow_grain.params`) — норма, при которой гекс при полном спросе даёт
    300–350 зерна в год. Наделы (`cell_yield_factor`) её не берут.
    """
    base = demesne_field_factor(world, tile.id, _params(world))
    # Лениво, как в `cell_yield_factor`: без навоза на гексе число голов не
    # читается. `worker is None` — heads ровно 0.0, и читать нечего.
    if worker is None:
        soil_part = tile_manure_factor(world, tile, 0.0) * rotation_factor(world, tile)
    else:
        soil_part = _tile_manure_factor_lazy(
            world, tile, lambda: worker_livestock_units(world, worker)
        ) * rotation_factor(world, tile)
    if worker is None:
        return base * soil_part
    return (
        base
        * tool_yield_factor(world, worker)
        * draft_yield_factor(world, worker)
        * soil_part
    )


def take_manure_uptake(world: World, tile: Tile, batches: float, date: SimDate) -> float:
    """Сколько навоза клетка взяла из своего standing за `batches` партий жатвы.

    Материя уходит в `sink:waste` (взята растением), поэтому баланс сходится и
    удобрение не копится вечно: подкормку надо возить каждый год.
    """
    if batches <= EPSILON:
        return 0.0
    standing = world.get_stock(tile.standing_stock_id)
    have = standing.amounts.get(MANURE_GOOD, 0.0)
    if have <= EPSILON:
        return 0.0
    uptake = _number(_params(world), "manure_uptake")
    take = min(have, uptake * batches)
    if take <= EPSILON:
        return 0.0
    world.ledger.transfer(
        standing, world.get_stock(WASTE_STOCK_ID), MANURE_GOOD, take, "crop_uptake", date
    )
    world.bump("manure_taken_up", take)
    return take


def spread_manure(
    world: World, household: Household, date: SimDate, budget: float
) -> tuple[float, float]:
    """Разложить навоз двора по его пашне; вернуть (трудодни, навоз).

    Труд берётся из `budget` — остатка месяца после рецептов, поэтому ни своего
    труда, ни барщины не создаётся. Навоз — перевод сток-двор → сток-клетка:
    материя не появляется и не пропадает. Месячная норма — `spread_units_per_month`,
    стоимость трудодней на единицу — `spread_days_per_unit`.
    """
    stock = world.get_stock(household.stock_id)
    have = stock.amounts.get(MANURE_GOOD, 0.0)
    if have <= EPSILON or budget <= EPSILON:
        return 0.0, 0.0
    params = _params(world)
    per_unit = _number(params, "spread_days_per_unit")
    if per_unit <= EPSILON:
        return 0.0, 0.0
    from .labor import _feeding_tiles

    quota = _number(params, "spread_units_per_month")
    spent = 0.0
    spread = 0.0
    for tile in _feeding_tiles(world, household):
        if quota <= EPSILON or have <= EPSILON or budget <= EPSILON:
            break
        units = min(quota, have, budget / per_unit)
        if units <= EPSILON:
            break
        world.ledger.transfer(
            stock,
            world.get_stock(tile.standing_stock_id),
            MANURE_GOOD,
            units,
            "dress_field",
            date,
        )
        have -= units
        quota -= units
        spent += units * per_unit
        budget -= units * per_unit
        spread += units
        world.bump("manure_spread", units)
    return spent, spread


def rotation_month(world: World) -> int:
    """Месяц смены полей (после жатвы, до сева): 11 по каталогу."""
    return int(_number(_params(world), "rotation_month"))


def rotation_days(world: World) -> float:
    """Сколько трудодней стоит смена полей на одной клетке за год."""
    return _number(_params(world), "rotation_days_per_tile")


def rotate_cell(world: World, tile: Tile, year: int) -> bool:
    """Пометить клетку сменившей поле в `year`; True, если смена новая.

    Метка года и есть состояние клетки (`world.stats`): детерминировано,
    входит в `state_hash`, переживает смену месяца.
    """
    if rotation_year(world, tile) >= float(year):
        return False
    world.stats[f"{ROTATED_KEY}{tile.id}"] = float(year)
    world.bump("fields_rotated")
    return True


__all__ = [
    "GROW_RULE",
    "MANURE_GOOD",
    "ROTATED_KEY",
    "TOOL_PRIORITY",
    "cell_yield_factor",
    "draft_cap",
    "draft_heads",
    "draft_table",
    "draft_yield_factor",
    "livestock_units",
    "manure_yield_factor",
    "rotate_cell",
    "rotation_days",
    "rotation_factor",
    "rotation_month",
    "rotation_year",
    "settlement_livestock_units",
    "spread_manure",
    "take_manure_uptake",
    "tile_manure_factor",
    "tile_yield_factor",
    "tool_table",
    "tool_yield_factor",
    "worker_livestock_units",
]
