"""Цикл H, пункт E: прототип `smith_iron_share` БЕЗ записи в каталог.

Предложение экономисту (точные числа — в отчёте): лемех куётся из сырого
железа с дровами, масса сходится, `iron_bloom` не тронут (только `smith_kit`
его ест). Прототип проверяет баланс и проводки на объекте в памяти;
каталог не меняется (тест ловит появление рецепта как провал «уже применён»).
Тест пункта 1 — после применения рецепта экономистом, не сейчас; сюит
обязан оставаться зелёным.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy.labor import apply_recipe
from hillcourt.ontology import Recipe
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SHIRE = ROOT / "design" / "scenarios" / "v0_shire.yml"
SEED = 1729

# Предложение: inputs iron 0.7 + firewood 0.4 = 1.1;
# outputs iron_share 1.0 + loss iron 0.1 = 1.1; труд 8 (< топора 10).
PROTO = Recipe(
    id="smith_iron_share",
    name="Ковка лемеха (прототип)",
    place="settlement",
    requires_terrain=[],
    inputs={"iron": 0.7, "firewood": 0.4},
    draws_standing={},
    outputs={"iron_share": 1.0},
    loss={"iron": 0.1},
    labor_days=8.0,
    transform=True,
)


class TestIronSharePrototype(unittest.TestCase):
    """Прототип сходится массой и движет материю переводом/рецептом."""

    def test_balance_and_transform(self) -> None:
        left = sum(PROTO.inputs.values()) + sum(PROTO.draws_standing.values())
        right = sum(PROTO.outputs.values()) + sum(PROTO.loss.values())
        self.assertAlmostEqual(left, right, places=6)
        self.assertTrue(PROTO.transform)
        self.assertGreater(PROTO.labor_days, 0.0)
        self.assertIn("iron_share", PROTO.outputs)

    def test_bloom_untouched_only_kit_eats_it(self) -> None:
        world = load_scenario(SHIRE, seed=SEED)
        eaters = [
            rid
            for rid, recipe in world.catalogs.recipes.items()
            if "iron_bloom" in recipe.inputs
        ]
        self.assertEqual(eaters, ["smith_kit"])
        self.assertNotIn("iron_bloom", PROTO.inputs)
        self.assertNotIn("iron_bloom", PROTO.outputs)

    def test_prototype_moves_matter_no_creation(self) -> None:
        world = load_scenario(SHIRE, seed=SEED)
        household = world.households["hh_04"]
        stock = world.get_stock(household.stock_id)
        stock.amounts["iron"] = 0.7
        stock.amounts["firewood"] = 0.4
        household.labor_days = 20.0
        tile = world.tiles[household.current_tile_id]
        world.ledger.capture_initial(world.total_matter())
        apply_recipe(world, household, tile, PROTO, 1.0, world.clock.date)
        self.assertAlmostEqual(stock.amounts.get("iron_share", 0.0), 1.0, places=6)
        self.assertAlmostEqual(stock.amounts.get("iron", 0.0), 0.0, places=6)
        self.assertAlmostEqual(stock.amounts.get("firewood", 0.0), 0.0, places=6)
        waste = world.get_stock("sink:waste").amounts.get("iron", 0.0)
        self.assertAlmostEqual(waste, 0.1, places=6)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_smith_iron_share_official(self) -> None:
        world = load_scenario(SHIRE, seed=SEED)
        recipe = world.catalogs.recipes["smith_iron_share"]
        self.assertEqual(recipe.inputs, {"iron": 1.2, "firewood": 0.6})
        self.assertEqual(recipe.outputs, {"iron_share": 1.0})
        self.assertEqual(recipe.loss, {"iron": 0.2, "firewood": 0.6})
        self.assertAlmostEqual(recipe.labor_days, 8.0, places=6)
        self.assertTrue(recipe.transform)
        left = sum(recipe.inputs.values()) + sum(recipe.draws_standing.values())
        right = sum(recipe.outputs.values()) + sum(recipe.loss.values())
        self.assertAlmostEqual(left, 1.8, places=6)
        self.assertAlmostEqual(right, 1.8, places=6)


if __name__ == "__main__":
    unittest.main()
