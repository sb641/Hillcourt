"""Охота: земельное действие, рецепты, стоки и доставленная весть."""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.tick import phase_labor
from hillcourt.news.views import build_player_view
from hillcourt.ontology import Right
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SMALL_GAME = ("squirrel", "rabbit")
LARGE_GAME = ("deer", "boar")


class TestGameAction(unittest.TestCase):
    """Действие применяет рецепт и не раскрывает весть до доставки."""

    def _world(self, household_id: str):
        world = load_scenario(SCENARIO, seed=1729)
        household = world.households[household_id]
        world.households = {household.id: household}
        return world, household

    def _common_hunt_world(self, good_id: str):
        world, household = self._world("hh_01")
        tile = world.tiles["t_00_00"]
        world.rights["right_test_game"] = Right(
            id="right_test_game",
            holder_household_id=household.id,
            tile_id=tile.id,
            kind="common",
            granted_date=world.clock.date,
            rent_share=0.0,
        )
        recipe = world.catalogs.recipes[f"take_game_{good_id}"]
        world.ledger.capture_initial(world.total_matter())
        world.ledger.external_in(
            world.get_stock(tile.standing_stock_id),
            good_id,
            recipe.draws_standing[good_id] * 2.0,
            "test_game_seed",
            f"game_{good_id}",
            world.clock.date,
        )
        household.labor_days = recipe.labor_days
        household.main_action = "take_game"
        household.minor_action = "idle_repair"
        return world, household, tile, recipe

    def _baron_hunt_world(self, good_id: str):
        world, household = self._world("hh_court")
        tile = world.tiles["t_00_00"]
        household.current_tile_id = tile.id
        recipe = world.catalogs.recipes[f"take_game_{good_id}"]
        world.ledger.capture_initial(world.total_matter())
        world.ledger.external_in(
            world.get_stock(tile.standing_stock_id),
            good_id,
            recipe.draws_standing[good_id] * 2.0,
            "test_game_seed",
            f"game_{good_id}",
            world.clock.date,
        )
        household.labor_days = recipe.labor_days
        household.main_action = "take_game"
        household.minor_action = "idle_repair"
        return world, household, tile, recipe

    def test_small_game_uses_its_recipe_in_executor_stock(self) -> None:
        for good_id in SMALL_GAME:
            with self.subTest(good=good_id):
                world, household, tile, recipe = self._common_hunt_world(good_id)
                before = world.get_stock(household.stock_id).amounts.get(good_id, 0.0)
                phase_labor(world)
                after = world.get_stock(household.stock_id).amounts.get(good_id, 0.0)
                self.assertAlmostEqual(after - before, recipe.outputs[good_id], places=6)
                self.assertAlmostEqual(
                    world.get_stock(tile.standing_stock_id).amounts[good_id],
                    recipe.draws_standing[good_id],
                    places=6,
                )

    def test_large_game_goes_to_baron_stock(self) -> None:
        for good_id in LARGE_GAME:
            with self.subTest(good=good_id):
                world, household, tile, recipe = self._baron_hunt_world(good_id)
                manor_stock_id = world.manors[world.player_manor_id].stock_id
                manor_before = world.get_stock(manor_stock_id).amounts.get(good_id, 0.0)
                household_before = world.get_stock(household.stock_id).amounts.get(good_id, 0.0)
                phase_labor(world)
                self.assertAlmostEqual(
                    world.get_stock(manor_stock_id).amounts.get(good_id, 0.0) - manor_before,
                    recipe.outputs[good_id],
                    places=6,
                )
                self.assertAlmostEqual(
                    world.get_stock(household.stock_id).amounts.get(good_id, 0.0),
                    household_before,
                    places=6,
                )

    def test_hunt_report_is_delayed_and_is_the_only_knowledge(self) -> None:
        world, _, _, _ = self._common_hunt_world("rabbit")
        phase_labor(world)
        report = next(r for r in world.reports if r.facts.get("event") == "hunt")
        before = build_player_view(world, report.event_date)
        after = build_player_view(world, report.delivery_date)
        self.assertNotIn(report.id, {entry.id for entry in before.entries})
        self.assertIn(report.id, {entry.id for entry in after.entries})
        self.assertGreater(report.delivery_date, report.event_date)
        self.assertEqual(report.facts["good"], "rabbit")
        self.assertEqual(report.facts["recipe_id"], "take_game_rabbit")

    def test_hunt_preserves_matter(self) -> None:
        world, _, _, _ = self._common_hunt_world("rabbit")
        phase_labor(world)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_hunt_is_deterministic_by_state_hash(self) -> None:
        first, _, _, _ = self._baron_hunt_world("deer")
        second, _, _, _ = self._baron_hunt_world("deer")
        phase_labor(first)
        phase_labor(second)
        self.assertEqual(first.state_hash(), second.state_hash())


if __name__ == "__main__":
    unittest.main()
