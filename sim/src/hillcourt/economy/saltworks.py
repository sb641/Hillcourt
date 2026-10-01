"""Солеварня как предприятие: топливо — условие, оплата — из склада (ADR 0092).

Солончак — собственность хозяина; работник солеварни (двор без пашни) живёт
**харчем и малой соляной долей** от предприятия, а не наделами. Всё честно:

- **Топливо — условие производства.** `boil_salt` уже требует `peat 3.0`; добыча
  торфа — действие `cut_peat` из топливного действия двора. Нет торфа → выварки
  нет → нет соли → нет и зарплаты. Нехватка топлива — вина хозяина (предприятие
  не снабжено), не «не мог» двора.
- **Оплата — transfer из склада предприятия** (`Settlement.stores` солеварни):
  харч зерном по `monthly_food_need` двора (сколько есть), соляная доля — 0.2 на
  взрослого, но не более 10 % соли, вываренной в прошлом месяце, на всех
  работников поселения поровну (иначе доля съедает склад предприятия: при 50
  работниках и выварке 2–8 кг/мес выплата в 10 % склада обнуляла его за
  месяцы — замерено). Хозяин не наполнил склад — дворы голодают честно (рычаг
  игрока, а не баг).
- Никаких новых товаров/валют/сущностей; деньги не создаются; дельта 0.
"""

from __future__ import annotations

from ..ontology import SimDate
from ..world import World
from .needs import member_counts, monthly_food_need

EPSILON = 1e-9
SALT_SETTLEMENT_KIND = "salt_village"
SALT_PER_ADULT = 0.2
SALT_WORKER_SHARE = 0.10
SALTWORKS = "saltworks_pay"


def _has_feeding_land(world: World, household) -> bool:
    """Есть ли у двора СВОЙ кормящий надел (тогда он не работник варны).

    Единый факт из `labor.own_land_feeds`: владение (`own_holding_tiles`: своя
    усадьба и наделения по праву) И ресурс клетки. Общинный доступ
    `Right.kind = common` и клетки `Settlement.works_tiles` — угодья поселения,
    а не надел двора (`docs/07_legal.md:64-68`, ADR 0077/0128); клетка, которая
    не кормит (солончак), надела-кормильца тоже не создаёт (ADR 0108, 0124).
    """
    from .labor import own_land_feeds

    return own_land_feeds(world, household)


def saltworks_month(world: World, date: SimDate) -> dict[str, float]:
    """Месячная оплата работников солеварни; вернуть числа месяца.

    Платит только солеварное поселение (`kind=salt_village`) и только дворам без
    своей кормящей земли.

    **ХАРЧ — КАЖДЫЙ МЕСЯЦ, ПОКА ДВОР НАНЯТ** (ADR 0186, закон хозяина: «платить,
    если нанят и простаивает»). Разделение, которое было в коде, оказалось не тем,
    что обещал докстринг: гейт «выварка за прошлый месяц» резал и ХАРЧ, хотя
    нижестоящим абзацем тот же докстринг писал «харч — каждый месяц», а
    ADR 0102 п. 1 и 5 говорят «предприятие кормит работников из своего склада по
    нужде». Простой по вине топлива — вина хозяина, а не повод не кормить нанятого
    работника: замер 24 месяцев `v0_hill_and_salt`, сид 1729 — склад солеварни
    268.6 зерна, торф кончается, выварки нет, и 6 работников не получали ничего.

    **СОЛЯНАЯ ДОЛЯ — ТОЛЬКО ПО ФАКТУ ВЫВАРКИ** (ADR 0092 п. 2: нет выварки — нет
    соли и нет соляной доли), не более 10 % вываренного на всех работников
    поселения поровну. Это плата за ПРОИЗВОДСТВО, и она отвязана от харча.

    Чем ограничен харч, если он идёт всегда: **самим складом предприятия** —
    `grain = min(monthly_food_need, stores)`. Никакого потолка по месяцам не
    заводим: пока в амбаре есть зерно, нанятый работник ест; когда амбар пуст,
    платить нечем, и это уже честный голод (счётчик `short_households`). Замер
    стенда: 6 работников × 2.0 зерна = 12.0 в месяц, 268.6 в амбаре = **22 месяца**
    полного харча, дальше склад пустеет сам.
    """
    totals = {"grain_paid": 0.0, "salt_paid": 0.0, "short_households": 0.0}
    for sid in sorted(world.settlements):
        settlement = world.settlements[sid]
        if settlement.kind != SALT_SETTLEMENT_KIND:
            continue
        stores = world.stocks.get(settlement.stores_stock_id)
        if stores is None:
            continue
        previous_month = date.month - 1 or 12
        previous_year = date.year if date.month > 1 else date.year - 1
        production_date = SimDate(previous_year, previous_month, date.day)
        produced = sum(
            e.amount
            for e in world.ledger.entries
            if e.reason == "boil_salt" and e.good == "salt" and e.date == production_date
        )
        # ХАРЧ БЕЗ ГЕЙТА ПО ВЫВАРКЕ (ADR 0186): работник нанят — работник ест,
        # простаивает предприятие или нет. Гейт «выварка за прошлый месяц» остался
        # только на СОЛЯНОЙ ДОЛЮ ниже: там он и был прав (ADR 0092 п. 2 — нет выварки,
        # нет соли). Раньше гейт стоял выше и резал оба платежа; замер падения
        # `test_board_follows_need_when_enterprise_is_filled` (5 != 12) показывает,
        # что резал он и харч: 6 работников × 2.0 зерна, а месяцев с выплатой — 5
        # при лаге ровно в месяц. Зерно по-прежнему берётся из склада предприятия
        # (transfer, дельта 0) — «из воздуха» не платим: при нуле зерна в складе
        # платить нечем, и это считается как честный голод двора.
        workers = [
            world.households[hid]
            for hid in sorted(settlement.household_ids)
            if (household := world.households.get(hid)) is not None
            and household.left_at is None
            and not _has_feeding_land(world, household)
        ]
        if not workers:
            continue
        # Соляная доля — единственный платёж, привязанный к выварке (ADR 0092 п. 2).
        salt_cap = SALT_WORKER_SHARE * produced if produced > EPSILON else 0.0
        for household in workers:
            need = monthly_food_need(world, household)
            grain = min(need, stores.amounts.get("grain", 0.0))
            if grain > EPSILON:
                world.ledger.transfer(
                    stores, world.get_stock(household.stock_id), "grain", grain,
                    SALTWORKS, date,
                )
                totals["grain_paid"] += grain
            adults, _, _ = member_counts(world, household)
            salt_want = min(
                SALT_PER_ADULT * max(adults, 1),
                salt_cap / len(workers),
                stores.amounts.get("salt", 0.0),
            )
            if salt_want > EPSILON:
                world.ledger.transfer(
                    stores, world.get_stock(household.stock_id), "salt", salt_want,
                    SALTWORKS, date,
                )
                totals["salt_paid"] += salt_want
            if grain + EPSILON < need:
                totals["short_households"] += 1.0
    if totals["grain_paid"] or totals["salt_paid"] or totals["short_households"]:
        world.bump("saltworks_grain_paid", totals["grain_paid"])
        world.bump("saltworks_salt_paid", totals["salt_paid"])
        world.bump("saltworks_short", totals["short_households"])
    return totals


def saltworks_fuel_world(world: World, household) -> bool:
    """Есть ли у двора доступ к топливной клетке своей солеварни (marsh/лес)."""
    settlement = world.settlements.get(household.settlement_id or "")
    if settlement is None or settlement.kind != SALT_SETTLEMENT_KIND:
        return False
    return any(
        (tile := world.tiles.get(tid)) is not None
        and tile.terrain in ("marsh", "forest")
        for tid in settlement.works_tiles
    )
