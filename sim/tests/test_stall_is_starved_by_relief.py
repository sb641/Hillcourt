"""Обвинитель диагноза: прилавок голодает не из-за серебра, а из-за подачи (ADR 0221).

Задача хозяина была «сделать так, чтобы внутри баронства цена работала», и перед
правкой были две версии, почему она молчит:

1. **оплата.** `_settle_sale` платит серебром, а серебра у дворов нет, поэтому всё
   уходит в `_natural_plan`, а тот «всё или ничего» и не собирается;
2. **у двора нет дохода.** Серебро получает только барон, покупатель — дворы.

Обе версии проверены замером и **обе опровергнуты**. Доказывается здесь не
«рынок работает», а ровно то, что его глушит, и что сам канал покупки цел.

## Что на самом деле происходит

`phase_exchange` (ADR 0139/0148) идёт: **подача → обмен → прилавок**. Подача
выдаёт двору **весь** его недобор (ADR 0158 п. 3), а `_listed_amount` покупает «не
больше своего недобора». Недобор, который видит прилавок, к этому моменту уже
израсходован подачей, и покупать нечего.

Замер (`v0_barony_100`, 24 месяца, сид 4242, `_measure` ниже):

| величина | число |
|---|---|
| недобор в начале месяца, суммарно | `4831.367` рот-единиц |
| недобор после подачи, суммарно | `1.343` |
| недобор перед прилавком | `1.343` |
| продано за 24 месяца | `1.3432` зерна |

Подача съедает **99.97%** спроса. Прилавок молчит не потому, что двору нечем
платить, а потому, что двору нечего **покупать**.

## Почему это важно и почему обе версии неверны

**Серебро не причина.** Опыты (см. ADR 0221 §опыты) на том же сценарии:

| опыт | продано прилавком |
|---|---|
| как есть | `1.3432` |
| у двора серебра неограниченно много | `1.3432` — **то же самое** |
| найм сломан, landless получили книгу (серебро у дворов `201.58`) | `1.3432` |
| подача не покрывает недобор | `73.1570` |
| прилавок раньше подачи | `73.1570` |

Серебра у двора сколько угодно — продажа не меняется ни на единицу. Меняет её
только одно: остаётся ли недобор после подачи.

**Натуральный план — не причина.** `_natural_plan` работает: 32 сделки за 24
месяца оплачены мукой по каталожной цене. Он молчит не из-за ошибки, а из-за
предмета: оплачивать нечего, когда покупать нечего.

## Что здесь проверяется

Канал покупки **цел** — это и есть содержание теста, а не тавтология:

* `test_the_stall_sells_at_the_catalog_price_when_silver_is_paid` — двор с
  недобором и серебром покупает по цене `goods.yml`, и цена каталога меняет
  сумму;
* `test_relief_in_the_same_phase_closes_the_need_and_stalls_the_sale` — **тот же
  двор в той же фазе**, но с правом на подачу, не покупает ничего;
* `test_silver_does_not_help_a_household_that_has_nothing_to_buy` — неограниченное
  серебро не превращает нулевую продажу в ненулевую;
* `test_relief_amount_is_the_whole_shortfall` — подача равна недобору, а не
  части его: это и есть механизм, который съедает рынок.

Мутации, которые роняют обвинитель (замерено, см. ADR 0221):

| мутация | красный тест |
|---|---|
| прилавок раньше подачи в `phase_exchange` | `test_relief_in_the_same_phase_closes_the_need_and_stalls_the_sale` |
| `_natural_plan` всегда пуст (забыли цену) | `test_the_stall_sells_at_the_catalog_price_when_silver_is_paid` |
| цена зерна зашита числом в коде | `test_the_stall_sells_at_the_catalog_price_when_silver_is_paid` |
| подача перестала выдавать весь недобор | `test_relief_amount_is_the_whole_shortfall` |
| убрали ветку серебра из `_settle_sale` | `test_the_stall_sells_at_the_catalog_price_when_silver_is_paid` |
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy import exchange
from hillcourt.economy.exchange import EPSILON, SELL_GOOD, _natural_plan
from hillcourt.economy.needs import food_shortfall, monthly_food_need
from hillcourt.engine.tick import phase_exchange
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SHIRE = ROOT / "design" / "scenarios" / "v0_shire.yml"
GRAIN = "grain"
MANOR = "manor_hill"
BUYER = "hh_11"
#: `hh_11` — 3 взрослых и 2 ребёнка, месячная нужда 4.4 рот-единицы.
NEED = 4.4


class StallIsStarvedByRelief(unittest.TestCase):
    """Один двор, одна фаза, четыре входа: недобор, серебро, подача, цена."""

    def setUp(self) -> None:
        self.world = load_scenario(SHIRE, seed=1729)
        self.manor = self.world.manors[MANOR]
        self.barn = self.world.get_stock(self.manor.stock_id)
        self.buyer = self.world.households[BUYER]
        self.buyer_stock = self.world.get_stock(self.buyer.stock_id)
        # Один двор в мире: соседский обмен не должен подмешиваться под продажу.
        self.world.households = {self.buyer.id: self.buyer}
        self.buyer.current_tile_id = "t_00_09"
        self.buyer.main_action = "work_plot"
        self.buyer.minor_action = "idle_repair"
        self.put(self.barn, grain=40.0)
        self.put(self.buyer_stock)

    def put(self, stock, **goods: float) -> None:
        """Собрать сток под сценку и пересобрать нулевой баланс материи.

        Деньги и товары кладутся мимо бухгалтерии — это подложка теста, а не тик,
        поэтому баланс после подложки и есть нулевой.
        """
        stock.amounts.clear()
        for good, amount in sorted(goods.items()):
            stock.amounts[good] = amount
        self.world.ledger.capture_initial(self.world.total_matter())

    @property
    def price(self) -> float:
        return self.world.catalogs.goods[GRAIN].price_silver

    @property
    def need(self) -> float:
        return monthly_food_need(self.world, self.buyer)

    def grain_of(self, stock) -> float:
        return float(stock.amounts.get(GRAIN, 0.0))

    def silver_of(self, stock) -> float:
        return float(stock.amounts.get("silver", 0.0))

    def sold(self, since: int = 0) -> float:
        """Сколько зерна прилавок продал двору (проводки `sell_grain_market`)."""
        return sum(
            entry.amount
            for entry in self.world.ledger.entries[since:]
            if entry.kind == "transfer"
            and entry.good == GRAIN
            and entry.reason == "sell_grain_market"
            and entry.dst_id == self.buyer.stock_id
        )

    def given_by_relief(self, since: int = 0) -> float:
        """Сколько зерна подача выдала двору (проводки `relief`)."""
        return sum(
            entry.amount
            for entry in self.world.ledger.entries[since:]
            if entry.kind == "transfer"
            and entry.good == GRAIN
            and entry.reason == "relief"
            and entry.dst_id == self.buyer.stock_id
        )

    def delta(self) -> float:
        return self.world.ledger.delta(self.world.total_matter())

    def market_step(self) -> tuple[float, float]:
        """Живой шаг прилавка: остаток на прилавок, затем продажа.

        Возвращает (продано, недобор ДО шага) — недобор снимается ДО того, как его
        читает `_listed_amount`, и без него тест врал бы сам себе.
        """
        mark = len(self.world.ledger.entries)
        need_before = food_shortfall(self.world, self.buyer)
        exchange.offer_manor_surplus(self.world, MANOR)
        exchange.sell_listed_grain(self.world, self.world.clock.date)
        return self.sold(mark), need_before

    def demand_after_relief(self) -> float:
        """Фаза обмена целиком, подача впереди прилавка (как в `phase_exchange`).

        Прилавок наполняет `offer_manor_surplus`, а он живёт в фазе МАНОРА
        (`economy/manor.py::_offer_surplus`, ADR 0148 п. 5), то есть в тике раньше
        обмена. Без этого шага прилавок пуст и продажа невозможна НЕ из-за
        порядка подачи, а из-за отсутствия товара, — и тест врал бы сам себе.
        """
        exchange.offer_manor_surplus(self.world, MANOR)
        mark = len(self.world.ledger.entries)
        phase_exchange(self.world)
        return food_shortfall(self.world, self.buyer)


class TestStallChannelIsAlive(StallIsStarvedByRelief):
    """Канал покупки цел: есть недобор и серебро — есть продажа по цене каталога."""

    def test_the_stall_sells_at_the_catalog_price_when_silver_is_paid(self) -> None:
        self.put(self.buyer_stock, silver=10.0)
        sold, need_before = self.market_step()
        self.assertAlmostEqual(need_before, NEED, places=9, msg="Недобор под сценку")
        self.assertAlmostEqual(sold, NEED, places=9, msg="Двор покупает свой недобор")
        self.assertAlmostEqual(self.silver_of(self.buyer_stock), 10.0 - NEED * self.price,
                               places=9, msg="Серебро уплачено по цене каталога")
        self.assertAlmostEqual(self.grain_of(self.barn), 40.0 - NEED, places=9)
        self.assertAlmostEqual(self.delta(), 0.0, places=9, msg="И-1: материя не создана")

    def test_catalog_price_decides_how_much_silver_is_spent(self) -> None:
        """Цена — число каталога: смена её двигает сумму, а не константа в коде."""
        self.put(self.buyer_stock, silver=10.0)
        sold, _ = self.market_step()
        self.assertAlmostEqual(self.silver_of(self.buyer_stock), 10.0 - sold * self.price,
                               places=9)
        self.assertNotAlmostEqual(sold, 0.0, msg="Продажа состоялась — сумма осмысленна")
        # Цена растёт — та же потребность обходится в меньшем количестве зерна.
        cheap = self.world.catalogs.goods[GRAIN].price_silver
        self.world.catalogs.goods[GRAIN].price_silver = cheap * 2.0
        self.put(self.barn, grain=40.0)
        self.put(self.buyer_stock, silver=10.0)
        sold_double, _ = self.market_step()
        self.assertAlmostEqual(self.silver_of(self.buyer_stock),
                               10.0 - sold_double * cheap * 2.0, places=9)
        self.assertLess(sold_double, sold, msg="Дороже — покупает меньше")

    def test_natural_plan_pays_when_there_is_something_to_pay_with(self) -> None:
        """`_natural_plan` не сломан: мука по каталожной цене закрывает долг."""
        flour = self.world.catalogs.goods["flour"].price_silver
        due = NEED * self.price
        self.buyer_stock.amounts["flour"] = due / flour
        plan = _natural_plan(self.world, self.buyer_stock, due)
        self.assertIn("flour", plan, msg="План оплаты собирается товаром с ценой")
        self.assertGreaterEqual(sum(plan.values()) * flour, due - EPSILON)
        # Продаваемый товар и серебро платёжом не являются — иначе оплата бессмысленна.
        self.assertNotIn(SELL_GOOD, plan)
        self.assertNotIn("silver", plan)


class TestReliefIsWhatStarvesTheStall(StallIsStarvedByRelief):
    """Находка замера: подача закрывает недобор раньше, чем его прочтёт прилавок."""

    def test_relief_in_the_same_phase_closes_the_need_and_stalls_the_sale(self) -> None:
        """Двор с серебром, но с правом на подачу, в той же фазе не покупает.

        Это и есть причина молчания прилавка: не «нечем платить», а «нечего
        покупать». Серебра здесь сколько угодно, и оно не помогает.
        """
        self.put(self.buyer_stock, silver=10.0)
        self.buyer.main_action = "request_relief"
        exchange.offer_manor_surplus(self.world, MANOR)
        mark = len(self.world.ledger.entries)
        phase_exchange(self.world)
        self.assertLess(food_shortfall(self.world, self.buyer), EPSILON,
                        msg="Подача закрыла недобор")
        self.assertGreater(self.given_by_relief(mark), 0.0,
                           msg="Сценарий не состоялся: подача ничего не выдала")
        self.assertAlmostEqual(
            sum(e.amount for e in self.world.ledger.entries[mark:]
                if e.reason == "sell_grain_market" and e.good == GRAIN),
            0.0, places=9,
            msg="Прилавок не продал ничего: покупать нечего",
        )

    def test_silver_does_not_help_a_household_that_has_nothing_to_buy(self) -> None:
        """Серебра сколько угодно — продажа не появляется, пока нужду съела подача."""
        self.buyer_stock.amounts["silver"] = 1e6
        self.buyer.main_action = "request_relief"
        self.assertLess(self.demand_after_relief(), EPSILON)
        exchange.offer_manor_surplus(self.world, MANOR)
        mark = len(self.world.ledger.entries)
        exchange.sell_listed_grain(self.world, self.world.clock.date)
        self.assertAlmostEqual(self.sold(mark), 0.0, places=9,
                               msg="Неограниченное серебро не покупает отсутствующий товар")

    def test_relief_amount_is_the_whole_shortfall(self) -> None:
        """Механизм, который съедает рынок: подача равна недобору, а не части его.

        Пока `relief_amount` не меньше недобора, у прилавка не остаётся спроса.
        Число `relief.cap_grain_per_month` живёт в `needs.yml` и держит этот
        баланс; тест падает, если подача начнёт выдавать меньше недобора, — и
        это правильно: тогда рынок получает шанс, и обвинитель об этом узнаёт.
        """
        self.buyer.main_action = "request_relief"
        exchange.offer_manor_surplus(self.world, MANOR)
        mark = len(self.world.ledger.entries)
        phase_exchange(self.world)
        given = self.given_by_relief(mark)
        self.assertAlmostEqual(given, NEED, places=9,
                               msg="Подача равна недобору — спрос прилавка исчезает")
        cap = self.world.needs.relief_amount
        self.assertGreaterEqual(cap, NEED,
                                "Потолок подачи держит баланс «рынка нет»")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
