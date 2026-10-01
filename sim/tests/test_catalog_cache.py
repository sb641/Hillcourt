from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hillcourt.catalogs import load_catalogs
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_native_village.yml"


class TestCatalogCache(unittest.TestCase):
    def test_cached_worlds_have_independent_mutable_catalogs(self) -> None:
        load_scenario(SCENARIO, seed=1729)
        first = load_scenario(SCENARIO, seed=1729)
        second = load_scenario(SCENARIO, seed=1729)

        first_good = first.catalogs.goods["grain"]
        second_good = second.catalogs.goods["grain"]
        first_recipe = first.catalogs.recipes["harvest_grain"]
        second_recipe = second.catalogs.recipes["harvest_grain"]
        first_rule = first.catalogs.spawn_rules["grow_grain"]
        second_rule = second.catalogs.spawn_rules["grow_grain"]

        self.assertIsNot(first.catalogs, second.catalogs)
        self.assertIsNot(first_good, second_good)
        self.assertIsNot(first_recipe, second_recipe)
        self.assertIsNot(first_rule, second_rule)

        original_price = second_good.price_silver
        original_edible = second_good.edible
        original_input = second_recipe.draws_standing["grain"]
        original_terrain = list(second_recipe.requires_terrain)
        original_amount = second_rule.params["amount"]

        first_good.price_silver = original_price + 101.0
        first_good.edible = not original_edible
        first_recipe.draws_standing["grain"] = original_input + 101.0
        first_recipe.requires_terrain.append("marsh")
        first_rule.params["amount"] = original_amount + 101.0

        self.assertEqual(second_good.price_silver, original_price)
        self.assertEqual(second_good.edible, original_edible)
        self.assertEqual(second_recipe.draws_standing["grain"], original_input)
        self.assertEqual(second_recipe.requires_terrain, original_terrain)
        self.assertEqual(second_rule.params["amount"], original_amount)

        third = load_scenario(SCENARIO, seed=1729)
        self.assertEqual(third.catalogs.goods["grain"].price_silver, original_price)
        self.assertEqual(third.catalogs.goods["grain"].edible, original_edible)
        self.assertEqual(
            third.catalogs.recipes["harvest_grain"].draws_standing["grain"],
            original_input,
        )
        self.assertEqual(
            third.catalogs.spawn_rules["grow_grain"].params["amount"],
            original_amount,
        )

    def test_cache_key_tracks_catalog_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path = root / "goods.yml"
            catalog_path.write_text(
                "version: 1\n"
                "goods:\n"
                "  - id: grain\n"
                "    name: Grain\n"
                "    category: food\n"
                "    storage: granary\n"
                "    price_silver: 1\n",
                encoding="utf-8",
            )
            mapping = {"goods": catalog_path.name}
            first = load_catalogs(root, mapping)
            self.assertEqual(first.goods["grain"].price_silver, 1.0)

            catalog_path.write_text(
                "version: 1\n"
                "goods:\n"
                "  - id: grain\n"
                "    name: Grain\n"
                "    category: food\n"
                "    storage: granary\n"
                "    price_silver: 2\n",
                encoding="utf-8",
            )
            second = load_catalogs(root, mapping)
            self.assertEqual(second.goods["grain"].price_silver, 2.0)


if __name__ == "__main__":
    unittest.main()
