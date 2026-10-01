"""Месячная дойка: поголовье и корм дают молоко, корм пары не трогается."""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy.labor import work_month
from hillcourt.economy.needs import hay_need_rate
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"


class TestDairyChain(unittest.TestCase):
    """Дойка ограничена животным и сеном СВЕРХ корма пары (ADR 0094, 0108)."""

    def _run(self, animal: str | None, hay: float, labor: float = 40.0):
        world = load_scenario(SCENARIO, seed=1729)
        household = world.households["hh_02"]
        world.households = {household.id: household}
        stock = world.get_stock(household.stock_id)
        stock.amounts.clear()
        if animal is not None:
            stock.amounts[animal] = 1.0
        stock.amounts["hay"] = hay
        household.labor_days = labor
        household.main_action = "idle_repair"
        household.minor_action = "milk_animal"
        world.clock.month = 1
        world.ledger.capture_initial(world.total_matter())
        work_month(world, world.clock.date)
        return (
            stock.amounts.get("milk", 0.0),
            stock.amounts.get("hay", 0.0),
            world.ledger.delta(world.total_matter()),
        )

    def test_milking_never_eats_the_pairs_ration(self) -> None:
        """Сена ровно на рацион пары — дойки нет; сверх рациона — молоко, рацион цел."""
        cases = (
            ("ox_f", 1.0, 1.6, 2.0),
            ("goat", 0.35, 0.7, 0.7),
            ("sheep", 0.45, 0.9, 0.9),
        )
        for animal, ration, per_batch, milk in cases:
            with self.subTest(animal=animal):
                produced, hay_left, delta = self._run(animal, ration)
                self.assertAlmostEqual(produced, 0.0, places=6, msg="Сена пары съедено на дойку")
                self.assertAlmostEqual(hay_left, ration, places=6, msg="Рацион не в стоке")
                self.assertAlmostEqual(delta, 0.0, places=6)
                surplus = ration + per_batch * 2
                produced, hay_left, delta = self._run(animal, surplus)
                self.assertAlmostEqual(produced, milk, places=6)
                self.assertAlmostEqual(hay_left, ration, places=6, msg="Рацион съедено на дойку")
                self.assertAlmostEqual(delta, 0.0, places=6)

    def test_ration_follows_the_season(self) -> None:
        """Летом резерв меньше (graze_hay_fraction), дойка берёт сена больше."""
        world = load_scenario(SCENARIO, seed=1729)
        winter = hay_need_rate(world, "ox_f", 1)
        summer = hay_need_rate(world, "ox_f", 6)
        self.assertLess(summer, winter)

    def test_no_feed_or_livestock_means_no_milk(self) -> None:
        for animal, hay in (("ox_f", 0.0), (None, 2.0)):
            with self.subTest(animal=animal, hay=hay):
                milk, _, delta = self._run(animal, hay)
                self.assertAlmostEqual(milk, 0.0, places=6)
                self.assertAlmostEqual(delta, 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
