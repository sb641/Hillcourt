"""Готовка смешанной еды из запасов двора без создания нового товара.

Готовка — **приём пищи**, а не новый товар (ADR 0098, 0100): зерно и прочее уходят
в `sink:eaten`, и двор обязан считать это насыщением. Без зачёта фаза 9
(`phase_consume`) посчитала бы голод по уменьшенному стоку, и готовка делала бы
двор **голоднее** — еда была бы съедена дважды, один раз по материи и второй раз
по нужде.

Зачёт ведётся тем же приёмом, что и потолок закупки зерна (ADR 0144, ADR 0159):
счётчик в `world.stats` плюс штамп месяца, поэтому новый месяц протухает сам, а
метка пишется **только в месяц реальной готовки** — иначе `state_hash` дёргался бы
каждый месяц для каждого двора. В `state_hash` статистика не входит вовсе
(`world.py::state_hash`), так что счётчик на детерминизм мира не влияет, но приём
оставлен тот же, чтобы два месячных счётчика читались одинаково.
"""

from __future__ import annotations

from ..ontology import Household, SimDate
from ..world import World

COOK_REASON = "cook_meal"
COOK_LABOR_DAYS = 2.0
LOSS_SHARE = 0.08
NUTRITION_BONUS_SHARE = 0.12
STAPLE_INPUTS = {"grain": 1.0, "flour": 1.0}
OPTIONAL_INPUTS = {
    "roots": 0.5,
    "greens": 0.5,
    "mushrooms": 0.5,
    "berries": 0.5,
    "milk": 0.5,
    "eggs": 0.25,
    "cheese": 0.2,
}
WASTE_STOCK_ID = "sink:waste"
EATEN_STOCK_ID = "sink:eaten"
EPSILON = 1e-9


def _month_stamp(world: World) -> float:
    """Штамп месяца для месячных счётчиков: целое число, сравнение точное (И-6)."""
    return float(world.clock.year * 12 + world.clock.month)


def _credit_key(household_id: str) -> str:
    """Ключ месячного зачёта готовки: сколько питательности двор уже съел."""
    return f"meal_credit_{household_id}"


def _credit_month_key(household_id: str) -> str:
    """Ключ штампа месяца зачёта: счётчик протухает вместе с месяцем."""
    return f"meal_credit_month_{household_id}"


def meal_credit(world: World, household: Household) -> float:
    """Питательность, уже съеденная двором за готовку в этом месяце.

    `phase_consume` вычитает её из месячной потребности ДО поедания, поэтому
    «сварил — не голодал» работает и по материи, и по нужде.
    """
    if float(world.stats.get(_credit_month_key(household.id), -1.0)) != _month_stamp(world):
        return 0.0
    return max(0.0, float(world.stats.get(_credit_key(household.id), 0.0)))


def credit_meal(world: World, household: Household, nutrition: float) -> None:
    """Записать питательность приёма пищи в месячный зачёт двора."""
    if nutrition <= EPSILON:
        return
    world.stats[_credit_key(household.id)] = (
        meal_credit(world, household) + nutrition
    )
    world.stats[_credit_month_key(household.id)] = _month_stamp(world)


def can_cook(world: World, household: Household) -> bool:
    """Есть ли зерно или мука и хотя бы один дополнительный ингредиент."""
    stock = world.get_stock(household.stock_id)
    staple = any(stock.amounts.get(good, 0.0) > EPSILON for good in STAPLE_INPUTS)
    extra = any(stock.amounts.get(good, 0.0) > EPSILON for good in OPTIONAL_INPUTS)
    return staple and extra


def cook_meal(
    world: World, household: Household, date: SimDate
) -> float | None:
    """Сварить один приём пищи и вернуть его питательность.

    Из стока списывается зерно или мука, затем доступные овощи, грибы, ягоды,
    молоко, яйца или сыр. Часть массы уходит в отходы, остальная — в
    `sink:eaten`. Итоговая питательность учитывает измеренный бонус готовки,
    но не создаёт товар или материю. Приготовленное **засчитывается двору как
    насыщение** (`credit_meal`), иначе фаза 9 сочла бы ту же еду дважды.
    """
    if household.left_at is not None or household.labor_days < COOK_LABOR_DAYS:
        return None
    if not can_cook(world, household):
        return None

    stock = world.get_stock(household.stock_id)
    waste = world.get_stock(WASTE_STOCK_ID)
    eaten = world.get_stock(EATEN_STOCK_ID)
    selected: dict[str, float] = {}
    for good, target in STAPLE_INPUTS.items():
        available = stock.amounts.get(good, 0.0)
        if available > EPSILON:
            selected[good] = min(target, available)
            break
    for good, target in OPTIONAL_INPUTS.items():
        available = stock.amounts.get(good, 0.0)
        if available > EPSILON:
            selected[good] = min(target, available)

    input_nutrition = sum(
        amount * world.catalogs.goods[good].nutrition
        for good, amount in selected.items()
    )
    if input_nutrition <= EPSILON:
        return None
    for good, amount in selected.items():
        loss = amount * LOSS_SHARE
        consumed = amount - loss
        if loss > EPSILON:
            world.ledger.transfer(
                stock, waste, good, loss, COOK_REASON, date
            )
        if consumed > EPSILON:
            world.ledger.transfer(
                stock, eaten, good, consumed, COOK_REASON, date
            )

    meal_nutrition = input_nutrition - input_nutrition * LOSS_SHARE
    meal_nutrition += input_nutrition * NUTRITION_BONUS_SHARE
    household.labor_days = max(0.0, household.labor_days - COOK_LABOR_DAYS)
    credit_meal(world, household, meal_nutrition)
    world.bump("own_labor", COOK_LABOR_DAYS)
    world.bump("meals_cooked")
    world.bump("meal_nutrition", meal_nutrition)
    world.bump("cook_loss_nutrition", input_nutrition * LOSS_SHARE)
    return meal_nutrition


def phase_cook_meal(world: World) -> None:
    """Выполнить явно выбранное дворами `cook_meal` до остальных работ."""
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.main_action == COOK_REASON:
            cook_meal(world, household, world.clock.date)
