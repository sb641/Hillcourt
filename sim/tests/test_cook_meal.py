"""Тесты готовки одного смешанного приёма пищи."""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy.cooking import (
    COOK_LABOR_DAYS,
    can_cook,
    cook_meal,
    phase_cook_meal,
)
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"


class TestCookMeal(unittest.TestCase):
    """Готовка смешивает продукты, сохраняет материю и тратит труд."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO, seed=1729)
        self.household = next(iter(self.world.households.values()))
        self.stock = self.world.get_stock(self.household.stock_id)
        self.stock.amounts.clear()
        self.world.ledger.capture_initial(self.world.total_matter())

    def input_nutrition(self, goods: dict[str, float]) -> float:
        return sum(
            amount * self.world.catalogs.goods[good].nutrition
            for good, amount in goods.items()
        )

    def test_mixed_meal_is_more_nourishing_than_inputs(self) -> None:
        goods = {"grain": 1.0, "roots": 0.5, "milk": 0.5}
        self.stock.amounts.update(goods)
        nutrition = cook_meal(self.world, self.household, self.world.clock.date)
        self.assertIsNotNone(nutrition)
        self.assertGreater(nutrition, self.input_nutrition(goods))
        self.assertEqual(self.world.stats["meals_cooked"], 1.0)
        self.assertNotIn("meal", self.stock.amounts)

    def test_fire_loss_labor_and_matter_are_measured(self) -> None:
        self.stock.amounts.update({"flour": 1.0, "cheese": 0.2})
        self.world.ledger.capture_initial(self.world.total_matter())
        before_labor = self.household.labor_days
        cook_meal(self.world, self.household, self.world.clock.date)
        self.assertEqual(
            self.household.labor_days, before_labor - COOK_LABOR_DAYS
        )
        loss = self.world.get_stock("sink:waste")
        self.assertAlmostEqual(loss.amounts["flour"], 0.08)
        self.assertAlmostEqual(loss.amounts["cheese"], 0.016)
        self.assertAlmostEqual(
            self.world.ledger.delta(self.world.total_matter()), 0.0, places=6
        )

    def test_no_staple_does_not_cook_or_post(self) -> None:
        self.stock.amounts.update({"roots": 1.0, "milk": 1.0})
        self.assertFalse(can_cook(self.world, self.household))
        before = len(self.world.ledger.entries)
        self.assertIsNone(cook_meal(self.world, self.household, self.world.clock.date))
        self.assertEqual(len(self.world.ledger.entries), before)
        self.assertGreater(self.household.labor_days, 0.0)

    def test_phase_cooks_only_when_selected(self) -> None:
        self.stock.amounts.update({"grain": 1.0, "eggs": 0.25})
        self.household.main_action = "cook_meal"
        phase_cook_meal(self.world)
        self.assertEqual(self.world.stats["meals_cooked"], 1.0)
        self.assertTrue(
            all(
                entry.reason == "cook_meal"
                for entry in self.world.ledger.entries
                if entry.good in {"grain", "eggs"}
                and entry.date == self.world.clock.date
            )
        )

    def test_deterministic_result(self) -> None:
        results = []
        for _ in range(2):
            world = load_scenario(SCENARIO, seed=1729)
            household = next(iter(world.households.values()))
            stock = world.get_stock(household.stock_id)
            stock.amounts.clear()
            stock.amounts.update({"grain": 1.0, "greens": 0.5, "eggs": 0.25})
            nutrition = cook_meal(world, household, world.clock.date)
            results.append((nutrition, dict(stock.amounts), household.labor_days))
        self.assertEqual(results[0], results[1])


if __name__ == "__main__":
    unittest.main()
