"""Кандидат миграции — только пригодный гекс (ADR 0080)."""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.hexgrid import neighbor_ids
from hillcourt.engine.tile_view import can_settle
from hillcourt.engine.tick import phase_migrate
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_barony_100.yml"
TEST_TILE = "t_11_17"


class TestMigrationCandidate(unittest.TestCase):
    """Вода, руина без жилья и режим без dwelling не являются кандидатами."""

    def test_water_and_ruin_without_dwelling_are_rejected(self) -> None:
        world = load_scenario(SCENARIO, seed=1729)
        tile = world.tiles[TEST_TILE]
        tile.terrain = "water"
        self.assertFalse(can_settle(world, TEST_TILE))
        tile.terrain = "ruin"
        tile.ruin_id = "ruin_test"
        self.assertFalse(can_settle(world, TEST_TILE))
        tile.dwelling = "house"
        self.assertTrue(can_settle(world, TEST_TILE))
        tile.dwelling = None
        tile.terrain = "field"
        tile.regime_id = "waste"
        self.assertFalse(can_settle(world, TEST_TILE))

    def test_six_occupied_neighbors_leave_household_in_place(self) -> None:
        world = load_scenario(SCENARIO, seed=1729)
        moving = world.households["hh_city_001"]
        origin = moving.current_tile_id
        neighbours = neighbor_ids(world, world.tiles[origin])
        self.assertEqual(len(neighbours), 6)
        available = [
            world.households[hid]
            for hid in sorted(world.households)
            if hid != moving.id
        ]
        for tile_id in neighbours:
            for household in available[:20]:
                household.current_tile_id = tile_id
            available = available[20:]
        moving.hunger_days = 3
        phase_migrate(world)
        self.assertEqual(moving.current_tile_id, origin)
        self.assertNotIn("pack:move_0001_01_hh_city_001", world.stocks)


if __name__ == "__main__":
    unittest.main()
