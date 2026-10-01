"""Тест-обвинитель корзины и бережливого порядка расхода еды."""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy.needs import monthly_food_need
from hillcourt.engine.tick import phase_consume
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"


class TestFoodBasket(unittest.TestCase):
    """Питание закрывает смешанный запас, а мясо остаётся резервом."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO, seed=1729)
        self.household = next(iter(self.world.households.values()))
        self.stock = self.world.get_stock(self.household.stock_id)
        self.stock.amounts.clear()

    def feed_amount(self, need: float, good: str) -> float:
        nutrition = self.world.catalogs.goods[good].nutrition
        return need / nutrition

    def test_mixed_baskets_feed_households(self) -> None:
        baskets = (("grain", "milk"), ("eggs", "cheese"))
        for first, second in baskets:
            with self.subTest(basket=(first, second)):
                need = monthly_food_need(self.world, self.household)
                self.stock.amounts[first] = self.feed_amount(need, first) * 0.6
                self.stock.amounts[second] = self.feed_amount(need, second) * 0.6
                phase_consume(self.world)
                self.assertEqual(self.household.hunger_days, 0)
                self.stock.amounts.clear()

    def test_meat_is_eaten_last(self) -> None:
        need = monthly_food_need(self.world, self.household)
        self.stock.amounts["grain"] = self.feed_amount(need * 0.8, "grain")
        self.stock.amounts["meat"] = self.feed_amount(need, "meat")
        phase_consume(self.world)
        self.assertGreater(self.stock.amounts["meat"], 0.0)

    def test_balanced_stock_does_not_starve(self) -> None:
        need = monthly_food_need(self.world, self.household)
        for good in ("milk", "eggs", "cheese", "butter", "grain", "flour", "meat"):
            self.stock.amounts[good] = self.feed_amount(need, good) * 0.2
        phase_consume(self.world)
        self.assertEqual(self.household.hunger_days, 0)

    def test_matter_delta_is_zero(self) -> None:
        need = monthly_food_need(self.world, self.household)
        self.stock.amounts["grain"] = self.feed_amount(need, "grain") * 0.6
        self.stock.amounts["milk"] = self.feed_amount(need, "milk") * 0.6
        self.world.ledger.capture_initial(self.world.total_matter())
        phase_consume(self.world)
        self.assertAlmostEqual(self.world.ledger.delta(self.world.total_matter()), 0.0, places=6)

    def test_consumption_is_deterministic_without_rng(self) -> None:
        results = []
        states = []
        for _ in range(2):
            world = load_scenario(SCENARIO, seed=1729)
            household = next(iter(world.households.values()))
            stock = world.get_stock(household.stock_id)
            need = monthly_food_need(world, household)
            stock.amounts.clear()
            stock.amounts["grain"] = need
            stock.amounts["milk"] = need
            before = {name: stream.getstate() for name, stream in vars(world.rng).items()}
            phase_consume(world)
            after = {name: stream.getstate() for name, stream in vars(world.rng).items()}
            states.append((before, after))
            results.append((household.hunger_days, dict(stock.amounts)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(states[0], states[1])
        self.assertEqual(states[0][0], states[0][1])


if __name__ == "__main__":
    unittest.main()
