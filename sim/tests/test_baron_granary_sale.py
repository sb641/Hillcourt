"""Амбар барона продаётся: ОДИН канал, цена только из каталога (ADR 0139/0153).

Живой путь ровно один: `economy/manor.py::_offer_surplus` (после пайка, подмоги и
посева, ADR 0148) → `exchange.offer_manor_surplus` → `phase_exchange` →
`exchange.sell_listed_grain`. Прямой продажи «барон назвал цену» больше нет, и
параметра цены в продаже нет вовсе: цена зерна — `goods.yml::grain.price_silver`
(ADR 0101), обойти её снаружи нечем.

Обвинения числами:
  * зерно в амбаре барона уходит ровно на проданное, двор получает ровно столько же;
  * прилавок продаёт **по мере надобности**: сытый двор с полным амбаром и полным
    карманом не покупает ничего (ADR 0139 п. 1, ADR 0124);
  * при нуле серебра у двора подача голодному всё равно идёт, а покупка — нет:
    подача и продажа — разные вещи (ADR 0139 п. 5);
  * при нуле серебра работает натуральный обмен (ADR 0139 п. 3);
  * месячный потолок закупки: двор берёт не больше `monthly_food_need`, счётчик
    один, и новый месяц его обнуляет (ADR 0144);
  * дельта материи 0;
  * цена продажи — число каталога, и её смена двигает цену;
  * продажа вызывается ТИКОМ, а не только тестом (ADR 0148) — `TestTickWiring`.

Числа сценария `v0_shire`: `manor_hill`, амбар `settlement:hill_court`; двор
`hh_11` (3 взрослых + 2 ребёнка) — недобор 4.4 зерна; цена зерна в каталоге
0.128165 серебра за единицу, дров — 0.107253.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy import exchange, manor
from hillcourt.economy.needs import food_months, monthly_food_need
from hillcourt.engine.tick import phase_exchange, run_month
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SHIRE = ROOT / "design" / "scenarios" / "v0_shire.yml"
GRAIN = "grain"
MANOR = "manor_hill"
NEED = 4.4  # 3 взрослых × 1.0 + 2 ребёнка × 0.7 рот-единицы в месяц


class BaronSaleCase(unittest.TestCase):
    """Общий каркас: корень с амбаром, один двор-покупатель и его деньги."""

    def setUp(self) -> None:
        self.world = load_scenario(SHIRE, seed=1729)
        self.price = self.world.catalogs.goods[GRAIN].price_silver
        self.assertAlmostEqual(self.price, 0.128165, places=6, msg="Цена зерна в каталоге")
        self.manor = self.world.manors[MANOR]
        self.barn = self.world.get_stock(self.manor.stock_id)
        self.buyer = self.world.households["hh_11"]
        self.buyer_stock = self.world.get_stock(self.buyer.stock_id)
        # Один двор в мире: соседский обмен не должен подмешиваться под продажу.
        self.world.households = {self.buyer.id: self.buyer}
        self.buyer.current_tile_id = "t_00_09"
        self.buyer.main_action = "work_plot"
        self.buyer.minor_action = "idle_repair"
        self.give(self.barn, grain=40.0)
        self.give(self.buyer_stock)
        self.assertAlmostEqual(monthly_food_need(self.world, self.buyer), NEED, places=9)

    def give(self, stock, **goods) -> None:
        """Собрать сток под сценку и пересобрать баланс материи.

        Деньги и товары кладутся мимо бухгалтерии — это подложка теста, а не тик,
        поэтому баланс после подложки и есть нулевой.
        """
        stock.amounts.clear()
        for good, amount in sorted(goods.items()):
            stock.amounts[good] = amount
        self.world.ledger.capture_initial(self.world.total_matter())

    def grain_of(self, stock) -> float:
        return float(stock.amounts.get(GRAIN, 0.0))

    def silver_of(self, stock) -> float:
        return float(stock.amounts.get("silver", 0.0))

    def delta(self) -> float:
        return self.world.ledger.delta(self.world.total_matter())

    def sold_to_buyer(self, since: int = 0) -> float:
        """Сколько зерна прилавок продал этому двору (проводки `sell_grain_market`)."""
        return sum(
            entry.amount
            for entry in self.world.ledger.entries[since:]
            if entry.kind == "transfer"
            and entry.good == GRAIN
            and entry.reason == "sell_grain_market"
            and entry.dst_id == self.buyer.stock_id
        )

    def market_step(self) -> float:
        """Живой месячный шаг целиком: остаток на прилавок, затем продажа.

        Возвращает продано **за этот шаг** (не накопительно), иначе второй шаг в том
        же месяце показал бы сумму предыдущего и тест врал бы сам себе.
        """
        mark = len(self.world.ledger.entries)
        exchange.offer_manor_surplus(self.world, MANOR)
        exchange.sell_listed_grain(self.world, self.world.clock.date)
        return self.sold_to_buyer(mark)


class TestSingleChannelSale(BaronSaleCase):
    """Канал один: прилавок, цена каталога, оплата из кармана."""

    def test_grain_leaves_the_granary_and_reaches_the_court_exactly(self) -> None:
        """Амбар минус недобор, двор плюс недобор, серебро минус недобор × цена."""
        self.give(self.buyer_stock, silver=2.0)
        sold = self.market_step()
        self.assertAlmostEqual(sold, NEED, places=9, msg="Прилавок не продал недобор")
        self.assertAlmostEqual(self.grain_of(self.barn), 40.0 - NEED, places=9)
        self.assertAlmostEqual(self.grain_of(self.buyer_stock), NEED, places=9)
        self.assertAlmostEqual(self.silver_of(self.buyer_stock), 2.0 - NEED * self.price, places=9)
        self.assertAlmostEqual(self.silver_of(self.barn), NEED * self.price, places=9)
        self.assertAlmostEqual(
            float(self.world.stats.get("manor_offer_grain_manor_hill", 0.0)),
            40.0 - NEED, places=9, msg="Прилавок не уменьшился на проданное",
        )
        self.assertAlmostEqual(self.delta(), 0.0, places=9)

    def test_sated_court_buys_nothing_from_a_full_granary(self) -> None:
        """«По мере надобности» (ADR 0139 п. 1): сытый двор не закупается про запас."""
        self.give(self.buyer_stock, silver=100.0, grain=60.0)
        self.assertGreaterEqual(food_months(self.world, self.buyer), 1.0)
        self.assertEqual(self.market_step(), 0.0, "Сытый двор купил зерно про запас")
        self.assertAlmostEqual(self.grain_of(self.barn), 40.0, places=9)
        self.assertAlmostEqual(self.silver_of(self.buyer_stock), 100.0, places=9)
        self.assertAlmostEqual(self.delta(), 0.0, places=9)

    def test_zero_silver_buys_nothing(self) -> None:
        """Серебра нет — покупки нет, как бы велик ни был амбар (ADR 0144 п. 4)."""
        self.give(self.buyer_stock, silver=0.0)
        self.assertEqual(self.market_step(), 0.0)
        self.assertAlmostEqual(self.grain_of(self.barn), 40.0, places=9)
        self.assertAlmostEqual(
            exchange.manor_grain_bought(self.world, self.buyer), 0.0, places=9
        )
        self.assertAlmostEqual(self.delta(), 0.0, places=9)

    def test_no_payment_no_sale_and_granary_untouched(self) -> None:
        """Нечем платить — сделки нет (всё или ничего), амбар не тронут."""
        self.give(self.buyer_stock, silver=0.0, straw=20.0)
        self.assertEqual(self.market_step(), 0.0, "Сделка прошла без платежа")
        self.assertAlmostEqual(self.grain_of(self.barn), 40.0, places=9)
        self.assertAlmostEqual(self.delta(), 0.0, places=9)

    def test_granary_shorter_than_the_shortfall_sells_only_what_it_holds(self) -> None:
        """Амбар меньше недобора: двор получает ровно остаток амбара, а не больше.

        Частичная продажа законна (берём, что есть), но отрицательного остатка не
        бывает: двор не может получить больше, чем лежало в амбаре (И-1, дельта 0).
        """
        self.give(self.barn, grain=2.0)
        self.give(self.buyer_stock, silver=5.0)
        self.assertAlmostEqual(self.market_step(), 2.0, places=9)
        self.assertAlmostEqual(self.grain_of(self.barn), 0.0, places=9)
        self.assertAlmostEqual(self.grain_of(self.buyer_stock), 2.0, places=9)
        self.assertAlmostEqual(self.silver_of(self.buyer_stock), 5.0 - 2.0 * self.price, places=9)
        self.assertAlmostEqual(self.delta(), 0.0, places=9)


class TestPurchaseCeiling(BaronSaleCase):
    """Месячный потолок закупки: месячная норма двора (ADR 0144)."""

    def test_purchase_never_exceeds_the_monthly_need(self) -> None:
        """За месяц из амбара — не больше `monthly_food_need`, и счётчик это видит."""
        self.give(self.buyer_stock, silver=100.0)
        sold = self.market_step()
        self.assertAlmostEqual(sold, NEED, places=9)
        self.assertLessEqual(sold, monthly_food_need(self.world, self.buyer) + 1e-9)
        self.assertAlmostEqual(
            exchange.manor_grain_bought(self.world, self.buyer), sold, places=9,
            msg="Счётчик закупки разошёлся с бухгалтерией",
        )

    def test_second_offer_in_the_same_month_adds_nothing(self) -> None:
        """Прилавок не позволяет накопить: второй ход в том же месяце — пусто."""
        self.give(self.buyer_stock, silver=100.0)
        first = self.market_step()
        self.give(self.barn, grain=40.0)  # хозяин добрал зерна в амбар
        second = self.market_step()
        self.assertAlmostEqual(first, NEED, places=9)
        self.assertEqual(second, 0.0, "Двор докупил сверх месячной нормы")
        self.assertAlmostEqual(self.grain_of(self.buyer_stock), NEED, places=9)
        self.assertAlmostEqual(self.delta(), 0.0, places=9)

    def test_counter_resets_with_the_month(self) -> None:
        """Счётчик месячный: в новом месяце двор может купить снова."""
        self.give(self.buyer_stock, silver=100.0)
        self.market_step()
        self.assertAlmostEqual(
            exchange.manor_grain_bought(self.world, self.buyer), NEED, places=9
        )
        self.give(self.buyer_stock, silver=100.0)  # новый месяц: двор снова голоден
        self.give(self.barn, grain=40.0)
        self.world.clock.advance_month()
        self.assertAlmostEqual(
            exchange.manor_grain_bought(self.world, self.buyer), 0.0, places=9,
            msg="Счётчик закупки не протух вместе с месяцем",
        )
        self.assertAlmostEqual(self.market_step(), NEED, places=9)
        self.assertAlmostEqual(self.delta(), 0.0, places=9)


class TestReliefIsNotSale(BaronSaleCase):
    """Подача голодному и продажа — разные вещи (ADR 0158 п. 3 против ADR 0139 п. 5)."""

    def _make_asking(self) -> None:
        """Двор без еды и без серебра, просящий подачу."""
        self.buyer.main_action = "request_relief"
        self.buyer.minor_action = "request_relief"
        self.buyer.hunger_days = 2
        self.give(self.buyer_stock)

    def test_relief_goes_through_at_zero_silver_and_buys_nothing(self) -> None:
        """Серебра 0: подача 4.4 идёт, покупка не идёт, прилавок не тронут."""
        self._make_asking()
        self.assertLess(food_months(self.world, self.buyer), 1.0)
        exchange.offer_manor_surplus(self.world, MANOR)
        phase_exchange(self.world)
        self.assertAlmostEqual(
            self.grain_of(self.buyer_stock), NEED, places=9,
            msg="Подача голодному не дошла при нуле серебра или не равна недобору",
        )
        self.assertEqual(self.silver_of(self.buyer_stock), 0.0, "Двор заплатил без серебра")
        self.assertAlmostEqual(self.grain_of(self.barn), 40.0 - NEED, places=9)
        self.assertEqual(self.silver_of(self.barn), 0.0, "Амбар получил серебро")
        reasons = {e.reason for e in self.world.ledger.entries if e.good == GRAIN}
        self.assertEqual(reasons, {"relief"}, "Подача и продажа перепутаны")
        self.assertAlmostEqual(
            float(self.world.stats.get("manor_offer_grain_manor_hill", 0.0)), 40.0, places=9,
            msg="Подача съела прилавок — это разные вещи",
        )
        self.assertAlmostEqual(
            exchange.manor_grain_bought(self.world, self.buyer), 0.0, places=9,
            msg="Подача потратила лимит закупки (ADR 0144 п. 2)",
        )
        self.assertAlmostEqual(self.delta(), 0.0, places=9)

    def test_silver_buys_instead_of_relief(self) -> None:
        """Тот же двор с серебром покупает сам: подача не идёт, сделка идёт."""
        self.buyer.main_action = "work_plot"
        self.give(self.buyer_stock, silver=1.0)
        exchange.offer_manor_surplus(self.world, MANOR)
        phase_exchange(self.world)
        self.assertAlmostEqual(self.grain_of(self.buyer_stock), NEED, places=9)
        self.assertAlmostEqual(
            self.silver_of(self.buyer_stock), 1.0 - NEED * self.price, places=9
        )
        reasons = {e.reason for e in self.world.ledger.entries if e.good == GRAIN}
        self.assertEqual(reasons, {"sell_grain_market"})
        self.assertAlmostEqual(self.delta(), 0.0, places=9)


class TestNaturalExchange(BaronSaleCase):
    """Серебра в обращении нет — натуральный обмен (ADR 0139 п. 3)."""

    def test_board_paid_in_goods_at_catalog_prices(self) -> None:
        """Серебра 0, дров 40: двор платит товаром по каталожной цене, зерно получает."""
        price_firewood = self.world.catalogs.goods["firewood"].price_silver
        due = NEED * self.price
        self.assertGreater(40.0 * price_firewood, due, "Дров не хватает на недобор")
        self.give(self.buyer_stock, firewood=40.0, silver=0.0)
        sold = self.market_step()
        paid = due / price_firewood
        self.assertAlmostEqual(sold, NEED, places=9)
        self.assertAlmostEqual(
            self.buyer_stock.amounts["firewood"], 40.0 - paid, places=9
        )
        self.assertAlmostEqual(self.barn.amounts["firewood"], paid, places=9)
        self.assertAlmostEqual(self.grain_of(self.barn), 40.0 - NEED, places=9)
        self.assertAlmostEqual(self.silver_of(self.buyer_stock), 0.0, places=9)
        self.assertAlmostEqual(
            exchange.manor_grain_bought(self.world, self.buyer), NEED, places=9,
            msg="Натуральный обмен не записан в счётчик закупки",
        )
        self.assertAlmostEqual(self.delta(), 0.0, places=9)

    def test_paid_partly_in_silver_partly_in_goods(self) -> None:
        """Серебра 0.05 на 4.4 зерна (0.564), остальное дровами: сделка состоялась."""
        price_firewood = self.world.catalogs.goods["firewood"].price_silver
        due = NEED * self.price
        self.give(self.buyer_stock, silver=0.05, firewood=8.0)
        self.assertLess(0.05, due, "Серебра хватает на всю цену — кейс не тот")
        self.assertGreater(0.05 + 8.0 * price_firewood, due, "Дрова не покрывают остаток")
        self.assertAlmostEqual(self.market_step(), NEED, places=9)
        self.assertAlmostEqual(self.silver_of(self.buyer_stock), 0.0, places=9)
        self.assertAlmostEqual(self.barn.amounts["silver"], 0.05, places=9)
        self.assertAlmostEqual(
            self.barn.amounts["firewood"], (due - 0.05) / price_firewood, places=9
        )
        self.assertAlmostEqual(self.delta(), 0.0, places=9)


class TestPriceComesFromCatalog(BaronSaleCase):
    """Цена — каталог (ADR 0101/0153), а не константа и не параметр продажи."""

    def test_single_channel_takes_no_price_argument(self) -> None:
        """ADR 0153: параметра цены в продаже нет, оба прежних входа удалены."""
        import inspect

        params = list(inspect.signature(exchange.sell_listed_grain).parameters)
        self.assertEqual(params, ["world", "date"], "У продажи появился параметр цены")
        for gone in ("sell_manor_grain", "list_manor_grain"):
            self.assertFalse(
                hasattr(exchange, gone), f"Второй канал {gone} снова в модуле"
            )

    def test_sale_price_is_not_a_constant_in_the_module(self) -> None:
        """Цена продажи не совпадает ни с одной константой модуля — только каталог."""
        numbers: set[float] = set()
        for value in vars(exchange).values():
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                numbers.add(round(float(value), 9))
            elif isinstance(value, dict):
                numbers.update(
                    round(float(item), 9)
                    for item in value.values()
                    if isinstance(item, (int, float)) and not isinstance(item, bool)
                )
        self.assertAlmostEqual(
            exchange.price_of(self.world, GRAIN), self.price, places=9
        )
        self.assertNotIn(
            round(self.price, 9), numbers, "Цена продажи сидит константой в коде"
        )

    def test_catalog_price_change_moves_the_sale_price(self) -> None:
        """Цена зерна в каталоге 0.5: прилавок считает по ней, а не по константе."""
        self.world.catalogs.goods[GRAIN].price_silver = 0.5
        self.assertAlmostEqual(exchange.price_of(self.world, GRAIN), 0.5, places=9)
        self.give(self.buyer_stock, silver=5.0)
        self.assertAlmostEqual(self.market_step(), NEED, places=9)
        self.assertAlmostEqual(self.silver_of(self.buyer_stock), 5.0 - NEED * 0.5, places=9)
        self.assertAlmostEqual(self.silver_of(self.barn), NEED * 0.5, places=9)
        paid = [
            e for e in self.world.ledger.entries
            if e.reason == "sell_grain_market" and e.good == "silver"
        ]
        self.assertEqual(len(paid), 1, "Серебро за прилавок одной проводкой")
        self.assertAlmostEqual(paid[0].amount, NEED * 0.5, places=9)
        self.assertAlmostEqual(self.delta(), 0.0, places=9)


class TestTickWiring(BaronSaleCase):
    """Проводка продажи в тик (ADR 0148): не «модуль есть», а «тик зовёт».

    Тест ловит именно болезнь из ADR 0148: продажа может быть зелёной и при этом
    не вызываться никем — тогда амбар лежит, а голодный двор не может купить. Здесь
    гоняется настоящий `run_month`, и счётчик `manor_grain_bought_<hid>` обязан
    стать непустым. Отрицательный контроль в том же тесте (проводка выключена →
    продаж ноль) доказывает, что тест действительно различает «проводка есть» и
    «проводки нет», а не просто повторяет зелёный путь.
    """

    def setUp(self) -> None:
        super().setUp()
        # Заведомо избыточный амбар и двор, которому есть чего хотеть и за что платить:
        # паёк виллану не положен (`board.grain_per_adult_by_status` только
        # slave/holder/thegn), поэтому недобор двора — его полная месячная норма.
        self.give(self.barn, grain=200.0)
        self.give(self.buyer_stock, silver=5.0)
        self.assertLess(food_months(self.world, self.buyer), 1.0)

    def _run_one_month(self, world, wired: bool) -> float:
        """Прогнать месяц с включённой или выключенной проводкой; вернуть продано."""
        real = manor._offer_surplus
        if not wired:
            manor._offer_surplus = lambda w: None
        try:
            run_month(world)
        finally:
            manor._offer_surplus = real
        return sum(
            entry.amount
            for entry in world.ledger.entries
            if entry.kind == "transfer" and entry.good == GRAIN
            and entry.reason == "sell_grain_market"
            and entry.dst_id == self.buyer.stock_id
        )

    def test_tick_offers_the_granary_surplus_and_the_court_buys_it(self) -> None:
        """Тик выставляет остаток амбара, двор покупает, счётчик непуст, дельта 0."""
        sold = self._run_one_month(self.world, wired=True)
        self.assertGreater(
            sold, 0.0, "Тик не позвал продажу амбара: счётчик закупки остался пустым"
        )
        counter = self.world.stats.get(exchange._bought_key(self.buyer.id), 0.0)
        self.assertGreater(
            counter, 0.0, "В world.stats нет непустого manor_grain_bought_<hid>"
        )
        self.assertAlmostEqual(counter, sold, places=6, msg="Счётчик разошёлся с бухгалтерией")
        self.assertGreater(self.world.stats.get("manor_grain_sold_market", 0.0), 0.0)
        self.assertLessEqual(
            counter, monthly_food_need(self.world, self.buyer) + 1e-9,
            msg="Покупка прошла мимо потолка закупки (ADR 0144 п. 2)",
        )
        self.assertLess(
            self.grain_of(self.barn), 200.0, "Амбар не отдал зерно двору"
        )
        self.assertAlmostEqual(
            self.silver_of(self.buyer_stock) + sold * self.price,
            5.0, places=6, msg="Двор заплатил не каталожную цену",
        )
        self.assertAlmostEqual(self.delta(), 0.0, places=6)

    def test_without_the_wiring_nothing_is_sold(self) -> None:
        """Отрицательный контроль: проводки нет — продаж нет (тест не зелёный вхолостую)."""
        world = load_scenario(SHIRE, seed=1729)
        barn = world.get_stock(world.manors[MANOR].stock_id)
        buyer = world.households["hh_11"]
        stock = world.get_stock(buyer.stock_id)
        world.households = {buyer.id: buyer}
        stock.amounts.clear()
        stock.amounts["silver"] = 5.0
        barn.amounts.clear()
        barn.amounts["grain"] = 200.0
        world.ledger.capture_initial(world.total_matter())
        self.assertEqual(self._run_one_month(world, wired=False), 0.0)
        self.assertNotIn(exchange._bought_key(buyer.id), world.stats)

    def test_wired_runs_are_deterministic(self) -> None:
        """И-6: два прогона с одним seed и одной фикстурой дают один `state_hash`."""
        hashes = []
        sold_first = 0.0
        for _ in range(2):
            world = load_scenario(SHIRE, seed=1729)
            barn = world.get_stock(world.manors[MANOR].stock_id)
            buyer = world.households["hh_11"]
            stock = world.get_stock(buyer.stock_id)
            world.households = {buyer.id: buyer}
            stock.amounts.clear()
            stock.amounts["silver"] = 5.0
            barn.amounts.clear()
            barn.amounts["grain"] = 200.0
            world.ledger.capture_initial(world.total_matter())
            sold = self._run_one_month(world, wired=True)
            hashes.append(world.state_hash())
            if not sold_first:
                sold_first = sold
            self.assertAlmostEqual(
                sold, sold_first, places=9, msg="Продажа не детерминирована"
            )
        # Детерминизм не должен быть зелёным вхолостую: если продажи нет, сравнивать
        # нечего, и такой тест закрыл бы дыру ADR 0148 вместо того, чтобы её ловить.
        self.assertGreater(sold_first, 0.0, "Продажи не было — сравнивать нечего")
        self.assertEqual(hashes[0], hashes[1], "Один seed дал два разных мира")


if __name__ == "__main__":
    unittest.main()
