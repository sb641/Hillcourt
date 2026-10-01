"""Пищевая ценность и потребление молочных продуктов и яиц."""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy.needs import monthly_food_need
from hillcourt.engine.tick import phase_consume
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"


class TestNeedsFoodChain(unittest.TestCase):
    """Каждый новый продукт утоляет голод по общей норме рот-единиц."""

    def test_animal_foods_feed_household(self) -> None:
        for good in ("milk", "eggs", "cheese"):
            with self.subTest(good=good):
                world = load_scenario(SCENARIO, seed=1729)
                household = next(iter(world.households.values()))
                stock = world.get_stock(household.stock_id)
                need = monthly_food_need(world, household)
                stock.amounts.clear()
                stock.amounts[good] = need / world.catalogs.goods[good].nutrition + 0.01
                world.ledger.capture_initial(world.total_matter())
                phase_consume(world)
                self.assertEqual(household.hunger_days, 0)
                self.assertLessEqual(stock.amounts.get(good, 0.0), need * 0.6 + 1e-9)
                self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_edible_order_follows_thrift_rules(self) -> None:
        """Порядок по правилам ADR 0098, а не по списку в коде.

        Правила: (1) хвост порядка = `food.eat_last` из каталога, и мясо в нём
        (п. 4 — мясо не норма); (2) до хвоста порча не возрастает: скоропортящееся
        раньше стойкого; (3) в списке весь съедобный состав goods.yml — новое
        съедобное не может молча выпасть из корзины.
        """
        world = load_scenario(SCENARIO, seed=1729)
        order = list(world.needs.edible_order)
        eat_last = list(getattr(world.needs, "eat_last", ()))
        self.assertTrue(eat_last, "Правило «мясо последним» вычеркнуто из needs.yml")
        self.assertIn("meat", eat_last, "Мясо обязано быть в хвосте (ADR 0098 п. 4)")
        self.assertEqual(order[-len(eat_last):], eat_last, "Хвост порядка ≠ food.eat_last")
        self.assertEqual(order[: len(order) - len(eat_last)], [
            good for good in order if good not in set(eat_last)
        ], "Мясо стоит в середине порядка")
        spoil = {
            good: world.catalogs.goods[good].spoil_per_month
            for good in order
            if good in world.catalogs.goods
        }
        head = [good for good in order if good not in set(eat_last)]
        for earlier, later in zip(head, head[1:]):
            with self.subTest(pair=(earlier, later)):
                self.assertGreaterEqual(
                    spoil[earlier],
                    spoil[later],
                    f"{earlier} портится быстрее {later}, но съедается позже",
                )
        edible = {
            good_id
            for good_id, rule in world.catalogs.goods.items()
            if rule.edible
        }
        self.assertEqual(set(order), edible, "Съедобный состав goods ≠ edible_order")

    def test_perishable_food_is_eaten_before_grain(self) -> None:
        """Поведение: скоропортящееся съедается первым, зерно не гниёт в амбаре."""
        world = load_scenario(SCENARIO, seed=1729)
        household = next(iter(world.households.values()))
        stock = world.get_stock(household.stock_id)
        stock.amounts.clear()
        stock.amounts["greens"] = 0.4
        stock.amounts["grain"] = 40.0
        phase_consume(world)
        self.assertEqual(stock.amounts.get("greens", 0.0), 0.0, "Зелень не съедена первой")
        self.assertGreater(stock.amounts.get("grain", 0.0), 30.0, "Зерно съедено раньше зелени")

    def test_cheese_is_more_nutritious_than_grain(self) -> None:
        catalogs = load_scenario(SCENARIO, seed=1729).catalogs
        self.assertGreater(catalogs.goods["cheese"].nutrition, catalogs.goods["grain"].nutrition)
        self.assertLess(catalogs.goods["cheese"].nutrition, 3.0)


if __name__ == "__main__":
    unittest.main()
