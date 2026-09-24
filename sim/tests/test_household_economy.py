"""Проверка агентной экономики двора: голод, рента, скот, труд, страх.

Пять обязательных сценариев v0 (задача Economist):
  1. нет еды и нет труда → голод, а не магический хлеб;
  2. рента переводит зерно из стока двора в сток замка;
  3. свинья не появляется без купли или приплода;
  4. travel_adjacent снимает руки с поля на этот месяц;
  5. при высокой опасности и низком авантюризме двор не идёт в чащу.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy import decisions
from hillcourt.economy.labor import work_month
from hillcourt.engine.tick import (
    phase_consume,
    phase_growth,
    phase_obligations,
    run_month,
)
from hillcourt.ontology import Hazard
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
EPSILON = 1e-9


class TestHouseholdEconomy(unittest.TestCase):
    """Двор — автономный агент: сам решает, работает, ест и платит."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO)

    def _isolate(self, hid: str):
        """Оставить в мире один двор, чтобы обмен с соседями не смазывал тест."""
        household = self.world.households[hid]
        self.world.households = {hid: household}
        return household

    def test_no_food_no_work_gives_hunger_not_bread(self) -> None:
        household = self._isolate("hh_08")
        stock = self.world.get_stock(household.stock_id)
        for good in ("grain", "flour", "meat"):
            stock.amounts[good] = 0.0
        household.labor_days = 0.0
        household.main_action = "work_plot"
        household.minor_action = "idle_repair"
        grain_before = stock.amounts.get("grain", 0.0)

        work_month(self.world, self.world.clock.date)
        phase_consume(self.world)

        self.assertGreaterEqual(household.hunger_days, 1, "Голод не наступил")
        self.assertLessEqual(
            stock.amounts.get("grain", 0.0),
            grain_before + EPSILON,
            "Зерно появилось без работы и еды — магический хлеб",
        )

    def test_rent_moves_grain_from_household_to_court(self) -> None:
        household = self._isolate("hh_01")
        stock = self.world.get_stock(household.stock_id)
        stock.amounts["grain"] = 10.0
        court = self.world.settlements[self.world.player.court_settlement_id]
        court_stock = self.world.get_stock(court.stores_stock_id)
        before = court_stock.amounts.get("grain", 0.0)
        self.assertTrue(household.obligation_ids, "У двора нет повинности")

        phase_obligations(self.world)

        due = sum(
            self.world.obligations[oid].due_amount
            for oid in household.obligation_ids
            if self.world.obligations[oid].due_good == "grain"
            and self.world.obligations[oid].due_amount > 0
        )
        self.assertAlmostEqual(stock.amounts["grain"], 10.0 - due, places=6)
        self.assertAlmostEqual(
            court_stock.amounts.get("grain", 0.0), before + due, places=6
        )

    def test_pig_needs_purchase_or_birth(self) -> None:
        household = self._isolate("hh_03")
        stock = self.world.get_stock(household.stock_id)
        self.assertEqual(stock.amounts.get("pig", 0.0), 0.0)
        self.assertEqual(stock.amounts.get("silver", 0.0), 0.0)

        for _ in range(24):
            run_month(self.world)

        self.assertEqual(
            stock.amounts.get("pig", 0.0),
            0.0,
            "Свинья появилась без купли и без приплода",
        )

    def test_farrow_requires_breeding_pair(self) -> None:
        household = self._isolate("hh_06")
        stock = self.world.get_stock(household.stock_id)
        self.assertGreaterEqual(stock.amounts.get("pig", 0.0), 2.0)
        stock.amounts["hay"] = 20.0
        household.labor_days = 40.0
        household.main_action = "tend_animals"
        household.minor_action = "tend_animals"

        work_month(self.world, self.world.clock.date)

        self.assertGreater(
            stock.amounts.get("pig", 0.0), 2.0, "Приплод не случился при паре и сене"
        )

    def test_travel_removes_hands_from_field(self) -> None:
        household = self._isolate("hh_01")
        tile = self.world.tiles[household.current_tile_id]
        tile_stock = self.world.get_stock(tile.standing_stock_id)
        stock = self.world.get_stock(household.stock_id)
        household.labor_days = 60.0

        tile_stock.amounts["grain"] = 50.0
        household.main_action = "work_plot"
        household.minor_action = "idle_repair"
        work_month(self.world, self.world.clock.date)
        harvested = stock.amounts.get("grain", 0.0)
        self.assertGreater(harvested, 0.0, "На своей земле двор не собрал зерно")

        stock.amounts["grain"] = 0.0
        tile_stock.amounts["grain"] = 50.0
        household.main_action = "travel_adjacent"
        work_month(self.world, self.world.clock.date)

        self.assertTrue(household.traveling, "Двор не отмечен как ушедший")
        self.assertEqual(
            stock.amounts.get("grain", 0.0),
            0.0,
            "Двор ушёл, но всё равно работал на поле",
        )

    def test_fear_keeps_household_out_of_dangerous_wood(self) -> None:
        household = self.world.households["hh_court"]
        forest = self.world.tiles["t_00_01"]
        self.assertTrue(forest.hazard_ids, "Лес у холма должен быть опасен")
        self.world.clock.month = 6
        self.world.get_stock(household.stock_id).amounts["grain"] = 0.0
        household.hunger_days = 1
        # Второй соседний лес тоже опасен, чтобы «безопасного» сбора не осталось.
        safe_forest = self.world.tiles["t_01_00"]
        safe_forest.hazard_ids.append("haz_test")
        self.world.hazards["haz_test"] = Hazard(
            id="haz_test", kind="wolves", tile_id=safe_forest.id, intensity=0.6
        )

        for pid in household.member_ids:
            self.world.persons[pid].curiosity = 0.0
            self.world.persons[pid].fear = 1.0
        household.rumor_fear = 1.0
        household.adventurism = decisions.compute_adventurism(self.world, household)
        self.assertFalse(
            decisions.should_venture(self.world, household, forest),
            "Робкий двор пошёл в опасный лес",
        )
        main, _ = decisions.choose_actions(self.world, household)
        self.assertNotEqual(main, "forage_adjacent")

        for pid in household.member_ids:
            self.world.persons[pid].curiosity = 1.0
            self.world.persons[pid].fear = 0.0
        household.rumor_fear = 0.0
        household.adventurism = decisions.compute_adventurism(self.world, household)
        self.assertTrue(
            decisions.should_venture(self.world, household, forest),
            "Любопытный двор напрасно боится леса",
        )

    def test_nature_is_capped(self) -> None:
        self.world.clock.month = 7
        tile = self.world.tiles["t_02_01"]
        self.assertEqual(tile.terrain, "field")
        tile_stock = self.world.get_stock(tile.standing_stock_id)
        tile_stock.amounts["grain"] = 39.0

        phase_growth(self.world)

        self.assertLessEqual(
            tile_stock.amounts["grain"],
            40.0 + EPSILON,
            "Природа превысила cap_per_tile — бездонный кран",
        )

    def test_pig_does_not_multiply_without_feed(self) -> None:
        household = self._isolate("hh_06")
        stock = self.world.get_stock(household.stock_id)
        stock.amounts["pig"] = 2.0
        stock.amounts["hay"] = 0.0
        household.main_action = "tend_animals"
        household.minor_action = "tend_animals"

        work_month(self.world, self.world.clock.date)

        self.assertEqual(
            stock.amounts.get("pig", 0.0), 2.0, "Приплод без сена — свинья из ничего"
        )


class TestNeighborPricing(unittest.TestCase):
    """Соседский торг (ADR 0057): кап зерна, цена носителя — с богатой стороны.

    Чужая сделка (разные клетки): зерно ≤ 2.0, покупатель платит чистую цену,
    продавец даёт сверх — укус носильщика 0.25 в отход (голодного сбором не
    облагать). Без серебра на чистую цену — платной сделки нет (дар — отдельный
    путь неимущих). Только `transfer`/потери, дельта 0. Детерминированно.
    """

    SHIRE = ROOT / "design" / "scenarios" / "v0_shire.yml"

    def _pair(self):
        from hillcourt.economy import exchange

        world = load_scenario(self.SHIRE, seed=1729)
        buyer = world.households["hh_11"]  # t_00_09, голодный покупатель
        seller = world.households["hh_12"]  # t_01_09, сытый продавец
        self.assertNotEqual(buyer.current_tile_id, seller.current_tile_id)
        bstock = world.get_stock(buyer.stock_id)
        sstock = world.get_stock(seller.stock_id)
        bstock.amounts.clear()
        sstock.amounts.clear()
        return world, buyer, seller, bstock, sstock, exchange

    def test_cross_grain_fee_and_cap(self) -> None:
        world, buyer, seller, bstock, sstock, exchange = self._pair()
        bstock.amounts.update({"silver": 10.0})
        sstock.amounts.update({"grain": 20.0})
        world.ledger.capture_initial(world.total_matter())
        exchange._trade_grain(world, [buyer, seller], world.clock.date, cross=True)
        grain_wires = [
            e for e in world.ledger.entries
            if e.reason == "buy_grain" and e.good == "grain"
        ]
        silver_wires = [
            e for e in world.ledger.entries
            if e.reason == "buy_grain" and e.good == "silver"
        ]
        self.assertEqual(len(grain_wires), 1, "Чужая сделка не состоялась")
        self.assertAlmostEqual(grain_wires[0].amount, 2.0, places=6,
                               msg="Зерно через клетку — не кап 2.0")
        self.assertEqual(len(silver_wires), 1)
        self.assertAlmostEqual(silver_wires[0].amount, 2.0 * 0.5, places=6,
                               msg="Покупатель переплатил: цена не чистая")
        bite_wires = [
            e for e in world.ledger.entries
            if e.reason == "porter_loss" and e.good == "grain"
        ]
        self.assertEqual(len(bite_wires), 1, "Укус носильщика не списан")
        self.assertAlmostEqual(bite_wires[0].amount, 0.25, places=6)
        self.assertAlmostEqual(bstock.amounts.get("grain", 0.0), 2.0, places=6)
        self.assertAlmostEqual(sstock.amounts.get("grain", 0.0), 20.0 - 2.25, places=6)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_cross_partial_fill_clean_price(self) -> None:
        world, buyer, seller, bstock, sstock, exchange = self._pair()
        bstock.amounts.update({"silver": 0.9})  # хватает на 1.8 зерна, не на кап
        sstock.amounts.update({"grain": 20.0})
        world.ledger.capture_initial(world.total_matter())
        exchange._trade_grain(world, [buyer, seller], world.clock.date, cross=True)
        grain_got = sum(
            e.amount for e in world.ledger.entries
            if e.reason == "buy_grain" and e.good == "grain"
            and e.dst_id == buyer.stock_id
        )
        silver_paid = sum(
            e.amount for e in world.ledger.entries
            if e.reason == "buy_grain" and e.good == "silver"
        )
        self.assertAlmostEqual(grain_got, 1.8, places=6)
        self.assertAlmostEqual(silver_paid, 0.9, places=6,
                               msg="Цена не чистая: сбор с голодного")
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
