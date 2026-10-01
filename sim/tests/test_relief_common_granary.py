"""Подача двора: единственный источник — амбар книги сеньора (ADR 0133).

Маршрут проверяется не на глаз, а по проводкам: кто платит, кого платят раньше
(нужда) и что суммарная материя не меняется (И-1).

**Что здесь НЕ закон и больше не проверяется.** ADR 0111 «общая кладовая
поселения — первый источник подачи» и ADR 0112 «общинных кладовых не заводим»
удалены как выдумки прошлого директора (`docs/decisions/0133`, таблица
«Удалены как мои выдумки»). Живой код это уже признал: `relief_sources`
(`economy/exchange.py:156-173`) возвращает ровно один источник — амбар книги
сеньора, а сток поселения в списке не значится. Поэтому этот модуль больше НЕ
утверждает, что общая кладовая кормит, и проверяет живое правило: склад
поселения источником подачи не является.

Что осталось законом и проверяется здесь:
  * (a) источник подачи — только книга сеньора, и склад поселения в списке не
    значится (снятие ADR 0111/0112);
  * (b) выплата равна недобору, но не выше каталогового потолка
    (`needs.yml::relief.cap_grain_per_month`) — это число каталога, не константа
    теста;
  * (c) покрывший нужду двор не получает ничего;
  * (d) порядок выплат — по нужде (`hunger_days` по убыванию, ADR 0097): при
    нехватке зерна в книге первым берёт тот, кто без помощи уйдёт;
  * (e) при пустой книге подачи нет вовсе, даже когда склад поселения полон;
  * (f) склад промысла (солеварни) подачей не кормит (ADR 0092, 0102);
  * (g) двор лорда ест из доменного амбара — цепочка месяцами, дельта 0.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy.decisions import choose_actions
from hillcourt.economy.exchange import apply_relief, relief_sources
from hillcourt.economy.manor import manor_month
from hillcourt.economy.needs import food_shortfall, monthly_food_need
from hillcourt.engine.manor import grant_tenement, manor_of_household, manor_stock
from hillcourt.engine.tick import phase_consume
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
TENANT = "hh_02"
FARMSTEAD = "fs_02"
DEMESNE_FIELD = "t_05_02"
NEIGHBOUR = "hh_03"
LORD = "hh_court"
SALT_HOLDER = "hh_salt_01"
EPSILON = 1e-6


def _stand():
    world = load_scenario(SCENARIO, seed=1729)
    grant_tenement(world, TENANT, [DEMESNE_FIELD])
    tenant = world.households[TENANT]
    tenant.main_action = "request_relief"
    tenant.minor_action = "idle_repair"
    tenant.hunger_days = 3
    world.get_stock(tenant.stock_id).amounts.clear()
    return world, tenant


def _common(world, settlement_id: str = FARMSTEAD):
    """Склад ПОСЕЛЕНИЯ. Источником подачи он не является (ADR 0133) — стенд нужен,
    чтобы это утверждение проверялось, а не assumed."""
    return world.get_stock(world.settlements[settlement_id].stores_stock_id)


def _book(world, household_id: str = TENANT):
    manor = manor_of_household(world, household_id)
    return manor_stock(world, manor)


def _payments(world, reason: str = "relief"):
    return [e for e in world.ledger.entries if e.reason == reason and e.good == "grain"]


def _in_same_book(world, household_id: str, holder_id: str = TENANT) -> None:
    """Записать двор в чужую книгу: подача идёт из одного амбара на всех."""
    household = world.households[household_id]
    book = manor_of_household(world, holder_id)
    household.manor_id = book.id
    if household.id not in book.household_ids:
        book.household_ids.append(household.id)


class TestReliefRoute(unittest.TestCase):
    """Подача идёт из книги сеньора; порядок — по нужде."""

    def test_only_the_seniors_book_is_a_source(self) -> None:
        """(a) Источник один — книга сеньора; склад поселения источником не был и не стал."""
        world, tenant = _stand()
        book = _book(world)
        settlement_store = _common(world)
        self.assertNotEqual(
            settlement_store.id, book.id, "Стенд не различает кладовую и книгу"
        )
        self.assertEqual(
            [stock.id for stock in relief_sources(world, tenant)],
            [book.id],
            "Источником подачи стал кто-то кроме книги сеньора",
        )
        settlement_store.amounts["grain"] = 500.0
        self.assertNotIn(
            settlement_store.id,
            [stock.id for stock in relief_sources(world, tenant)],
            "Склад поселения снова стал источником подачи (ADR 0111/0112 удалены)",
        )

    def test_relief_equals_actual_shortfall(self) -> None:
        """(b) Выплата равна недобору, но не выше каталогового потолка."""
        world, tenant = _stand()
        book = _book(world)
        shortfall = food_shortfall(world, tenant)
        self.assertGreater(shortfall, 0.0, "Стенд без недобора")
        book.amounts["grain"] = max(shortfall, world.needs.relief_amount) * 10.0
        world.ledger.capture_initial(world.total_matter())
        apply_relief(world, world.clock.date)
        paid = sum(e.amount for e in _payments(world))
        self.assertAlmostEqual(
            paid,
            min(shortfall, world.needs.relief_amount),
            places=6,
            msg="Выплата не равна недобору под потолком каталога",
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_relief_is_capped_by_the_catalog(self) -> None:
        """(b) Потолок выплаты — число каталога `relief.cap_grain_per_month`."""
        world, tenant = _stand()
        book = _book(world)
        book.amounts["grain"] = 1000.0
        world.ledger.capture_initial(world.total_matter())
        apply_relief(world, world.clock.date)
        cap = world.needs.relief_amount
        self.assertGreater(cap, 0.0, "У подачи нет потолка в каталоге")
        for payment in _payments(world):
            self.assertLessEqual(
                payment.amount, cap + EPSILON, "Выплата выше каталожного потолка"
            )

    def test_covered_household_gets_nothing(self) -> None:
        """(c) Двор, покрывающий нужду, помощи не получает."""
        world, tenant = _stand()
        _book(world).amounts["grain"] = 50.0
        need = monthly_food_need(world, tenant)
        world.get_stock(tenant.stock_id).amounts["grain"] = need + 1.0
        self.assertLessEqual(food_shortfall(world, tenant), 0.0)
        world.ledger.capture_initial(world.total_matter())
        apply_relief(world, world.clock.date)
        self.assertEqual(_payments(world), [], "Помощь выдана покрывшему нужду двору")
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_hungry_household_asks_in_the_same_month(self) -> None:
        """Двор с недобором и полной книгой просит подмогу в том же месяце."""
        world, tenant = _stand()
        _book(world).amounts["grain"] = 50.0
        tenant.hunger_days = 0
        self.assertGreater(food_shortfall(world, tenant), 0.0)
        main, _minor = choose_actions(world, tenant)
        self.assertEqual(
            main,
            "request_relief",
            "Двор с недобором ждёт два месяца голода вместо подачи",
        )

    def test_empty_book_means_no_relief_even_with_full_settlement(self) -> None:
        """(e) Пустая книга — подавать нечего, полный склад поселения не спасёт.

        Обратная сторона снятия ADR 0111: источник один, и если его нет, то
        подачи нет. Раньше этот стенд кормил двор из общей кладовой.
        """
        world, tenant = _stand()
        book = _book(world)
        book.amounts.clear()
        _common(world).amounts["grain"] = 500.0
        world.ledger.capture_initial(world.total_matter())
        apply_relief(world, world.clock.date)
        self.assertEqual(
            _payments(world),
            [],
            "Подача пришла без зерна в книге сеньора",
        )
        self.assertAlmostEqual(
            _common(world).amounts.get("grain", 0.0), 500.0, places=6,
            msg="Склад поселения отдал зерно в подачу",
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_relief_order_is_by_need(self) -> None:
        """(d) Первым получает тот, кто без помощи уйдёт (нужда выше).

        Обе выплаты идут из одной книги, зерна в ней ровно на одну выплату, поэтому
        порядок виден по проводкам: одна выплата, и она — соседу с `hunger_days` 5
        против 3 у первого двора.
        """
        world, tenant = _stand()
        neighbour = world.households[NEIGHBOUR]
        _in_same_book(world, NEIGHBOUR)
        neighbour.main_action = "request_relief"
        neighbour.minor_action = "idle_repair"
        neighbour.hunger_days = 5
        world.get_stock(neighbour.stock_id).amounts.clear()
        book = _book(world)
        book.amounts.clear()
        # Зерна в книге ровно на одну выплату — на недобор соседа. Тогда первым
        # получает именно он, а второй двор остаётся ни с чем: порядок виден по
        # проводкам, а не по подсказке теста.
        nutrition = world.catalogs.goods["grain"].nutrition
        neighbour_want = food_shortfall(world, neighbour) / nutrition
        self.assertGreater(
            neighbour_want, world.needs.relief_min_court_grain,
            "Недобор соседа ниже порога подачи — стенд не различает",
        )
        book.amounts["grain"] = neighbour_want
        apply_relief(world, world.clock.date)
        payments = _payments(world)
        self.assertEqual(len(payments), 1, "Зерна книги хватило не на одну выплату")
        self.assertEqual(
            payments[0].dst_id, neighbour.stock_id,
            "Первым должен получить тот, кто без помощи уйдёт (hunger_days выше)",
        )
        self.assertEqual(
            payments[0].src_id, book.id, "Подача пришла не из книги сеньора"
        )

    def test_relief_does_not_touch_enterprise_store(self) -> None:
        """(f) Склад промысла подачей не кормит (ADR 0092, 0102)."""
        world, _ = _stand()
        holder = world.households[SALT_HOLDER]
        holder.main_action = "request_relief"
        holder.minor_action = "idle_repair"
        holder.hunger_days = 4
        world.get_stock(holder.stock_id).amounts.clear()
        enterprise = world.get_stock(world.settlements["salt_village"].stores_stock_id)
        enterprise.amounts["grain"] = 30.0
        world.ledger.capture_initial(world.total_matter())
        apply_relief(world, world.clock.date)
        self.assertNotIn(
            enterprise.id, [e.src_id for e in _payments(world)],
            "Склад промысла не должен кормить подмогой (ADR 0092, 0102)",
        )
        self.assertNotIn(
            enterprise.id, [s.id for s in relief_sources(world, holder)],
            "Склад предприятия не должен быть источником подачи",
        )
        self.assertEqual(enterprise.amounts.get("grain", 0.0), 30.0)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_lord_household_is_fed_from_demesne_granary(self) -> None:
        """(g) Двор лорда ест из доменного амбара: цепочка месяцами, дельта 0."""
        world = load_scenario(SCENARIO, seed=1729)
        lord = world.households[LORD]
        world.get_stock(lord.stock_id).amounts.clear()
        manor = manor_of_household(world, LORD)
        self.assertIsNotNone(manor, "Двор лорда вне книги манора")
        stock = manor_stock(world, manor)
        self.assertIsNotNone(stock, "Доменной кладовой нет")
        stock.amounts["grain"] = 500.0
        world.ledger.capture_initial(world.total_matter())
        for _ in range(3):
            manor_month(world, world.clock.date)
            phase_consume(world)
            world.clock.advance_month()
        self.assertEqual(lord.hunger_days, 0, "Двор лорда голодает при непустом домене")
        self.assertGreater(world.get_stock(lord.stock_id).amounts.get("grain", 0.0), 0.0)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
