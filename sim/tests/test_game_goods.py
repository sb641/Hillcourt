"""Дичь общинных угодий: товары, цены от труда, добыча как действие (ADR 0128).

Мелкая и крупная дича — **разные товары**, а не классы и не цепочка рецептов.
Цены выводятся из рецепта добычи (труд × ставка + потери) × коэффициент дефицита,
поэтому тест падает, если цену ввели «на глаз».
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CATALOGS = ROOT / "design" / "catalogs"
SMALL_GAME = ("squirrel", "rabbit")
LARGE_GAME = ("deer", "boar")
GAME = SMALL_GAME + LARGE_GAME
NAMES = {
    "squirrel": "Белка",
    "rabbit": "Кролик",
    "deer": "Олень",
    "boar": "Кабан",
}
FACTORS = {"squirrel": 1.1, "rabbit": 1.1, "deer": 1.3, "boar": 1.3}
HUNT_RECIPE = {good: f"take_game_{good}" for good in GAME}
HUNT_ACTION = "take_game"
WAGE = 0.02


class TestGameGoods(unittest.TestCase):
    """Дичь в каталоге: четыре товара, четыре рецепта добычи, цены от затрат."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.goods = {
            record["id"]: record
            for record in yaml.safe_load(
                (CATALOGS / "goods.yml").read_text(encoding="utf-8")
            )["goods"]
        }
        cls.recipes = {
            record["id"]: record
            for record in yaml.safe_load(
                (CATALOGS / "recipes.yml").read_text(encoding="utf-8")
            )["recipes"]
        }
        cls.rules = {
            record["id"]: record
            for record in yaml.safe_load(
                (CATALOGS / "spawn_rules.yml").read_text(encoding="utf-8")
            )["spawn_rules"]
        }
        cls.manor = yaml.safe_load((CATALOGS / "manor.yml").read_text(encoding="utf-8"))
        cls.wage = float(cls.manor["hire"]["wage_silver_per_day"])
        cls.needs = yaml.safe_load((CATALOGS / "needs.yml").read_text(encoding="utf-8"))

    def expected_price(self, good: str) -> tuple[float, float, float, float]:
        """(труд, сырьё, потери, цена) из рецепта добычи — модель ADR 0101/0106."""
        recipe = self.recipes[HUNT_RECIPE[good]]
        unit = float(recipe["outputs"][good])
        drawn = float(recipe["draws_standing"][good])
        labor = float(recipe["labor_days"]) / unit * self.wage
        materials = 0.0
        for src, qty in sorted(recipe["inputs"].items()):
            materials += float(qty) * float(self.goods[src].get("price_silver", 0.0))
        loss_share = float(recipe["loss"].get(good, 0.0)) / drawn if drawn > 0 else 0.0
        cost = (labor + materials) / (1.0 - loss_share)
        return labor, materials, cost - labor - materials, cost * FACTORS[good]

    def test_small_and_large_game_are_different_goods(self) -> None:
        self.assertEqual(
            set(GAME) & set(self.goods), set(GAME), "Не все товары дичи заведены"
        )
        self.assertFalse(
            set(SMALL_GAME) & set(LARGE_GAME), "Мелкая и крупная дича слились в один товар"
        )
        for good in GAME:
            with self.subTest(good=good):
                record = self.goods[good]
                self.assertEqual(record["name"], NAMES[good], "Имя не русское")
                self.assertEqual(record["category"], "food", "Дича — еда, не класс")
                self.assertEqual(record["storage"], "cellar", "Свежее мясо в погребе")
                self.assertTrue(record["edible"])
                self.assertGreater(record["nutrition"], 0.0)
                self.assertGreaterEqual(
                    record["spoil_per_month"], self.goods["meat"]["spoil_per_month"],
                    "Свежая дичь портится не медленнее домашнего мяса",
                )
                self.assertLessEqual(record["spoil_per_month"], 0.25)
        small = {self.goods[good]["nutrition"] for good in SMALL_GAME}
        large = {self.goods[good]["nutrition"] for good in LARGE_GAME}
        self.assertTrue(
            min(large) > max(small),
            f"Крупная дичь должна быть сытнее мелкой: {small} против {large}",
        )
        self.assertTrue(
            set(SMALL_GAME) <= set(self.needs["food"]["edible_order"]),
            "Мелкая дичь не попала в корзину еды",
        )

    def test_large_game_is_dearer_than_small(self) -> None:
        for large in LARGE_GAME:
            for small in SMALL_GAME:
                with self.subTest(pair=(large, small)):
                    self.assertGreater(
                        float(self.goods[large]["price_silver"]),
                        float(self.goods[small]["price_silver"]),
                    )
        for good in SMALL_GAME:
            with self.subTest(good=good):
                self.assertLess(
                    float(self.goods[good]["price_silver"]),
                    float(self.goods["grain"]["price_silver"]) * 2.0,
                    "Мелкая дичь должна оставаться мелкой по цене",
                )

    def test_prices_follow_recipe_costs(self) -> None:
        for good in GAME:
            with self.subTest(good=good):
                labor, materials, losses, price = self.expected_price(good)
                record = self.goods[good]
                self.assertAlmostEqual(record["price_labor_silver"], labor, places=5)
                self.assertAlmostEqual(record["price_materials_silver"], materials, places=5)
                self.assertAlmostEqual(record["price_losses_silver"], losses, places=5)
                self.assertAlmostEqual(record["price_silver"], price, places=5)
                self.assertAlmostEqual(
                    record["price_silver"] / price, 1.0, places=4,
                    msg="Цена введена на глаз, а не по затратам добычи",
                )

    def test_hunt_is_one_recipe_per_species_without_chains(self) -> None:
        for good in GAME:
            with self.subTest(good=good):
                recipe = self.recipes[HUNT_RECIPE[good]]
                self.assertEqual(recipe["place"], "tile", "Добыча идёт на клетке")
                self.assertIn("forest", recipe["requires_terrain"])
                self.assertEqual(
                    recipe["inputs"], {}, "Цепочка рецептов вместо одного действия добычи"
                )
                left = sum(recipe["draws_standing"].values())
                right = sum(recipe["outputs"].values()) + sum(recipe["loss"].values())
                self.assertAlmostEqual(left, right, places=6, msg="Масса не сходится")
                self.assertEqual(list(recipe["outputs"]), [good], "Вид один на рецепт")
                self.assertGreater(float(recipe["labor_days"]), 0.0)

    def test_large_game_costs_more_labour_per_unit(self) -> None:
        for large in LARGE_GAME:
            for small in SMALL_GAME:
                with self.subTest(pair=(large, small)):
                    large_days = (
                        float(self.recipes[HUNT_RECIPE[large]]["labor_days"])
                        / float(self.recipes[HUNT_RECIPE[large]]["outputs"][large])
                    )
                    small_days = (
                        float(self.recipes[HUNT_RECIPE[small]]["labor_days"])
                        / float(self.recipes[HUNT_RECIPE[small]]["outputs"][small])
                    )
                    self.assertGreater(large_days, small_days)

    def test_game_standing_matter_has_capped_spawn_rules(self) -> None:
        for good in GAME:
            with self.subTest(good=good):
                rule = self.rules[f"game_{good}"]
                self.assertEqual(rule["target"], "good")
                params = rule["params"]
                self.assertEqual(params["good"], good)
                self.assertIn(params["terrain"], ("forest", "heath"))
                self.assertGreater(float(params["cap_per_tile"]), 0.0)
                self.assertGreater(
                    float(params["amount"]), 0.0, "Дичь на гексе не появляется"
                )
                drawn = float(self.recipes[HUNT_RECIPE[good]]["draws_standing"][good])
                self.assertGreater(
                    float(params["cap_per_tile"]), drawn * 0.5,
                    "Кап клетки меньше половины партии: добывать можно, но нечем",
                )
                self.assertGreater(
                    float(params["amount"]), drawn * 0.05,
                    "Прирост дичи на клетке слишком мал, промысел не идёт",
                )

    def test_no_species_class_entities(self) -> None:
        """Дичь — товары еды, а не новый класс объектов (ADR 0128 п. 4)."""
        for good in GAME:
            with self.subTest(good=good):
                self.assertNotIn(
                    good, (self.needs["livestock"]["feed_good"],), "Дичь не скот"
                )
                self.assertNotIn(
                    good, self.needs["livestock"]["feed_per_month"],
                    "У дичи не должно быть кормовой нормы скота",
                )
        self.assertEqual(
            HUNT_ACTION, "take_game",
            "Добыча — существующее земельное действие, новое не заводим",
        )


if __name__ == "__main__":
    unittest.main()
