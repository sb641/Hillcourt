"""Право `Right`: общинный доступ не становится индивидуальным наделом."""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy.labor import _held_tile_ids, own_tiles
from hillcourt.engine.hexgrid import is_neighbor
from hillcourt.legal.regimes import has_access_to_communal_tile
from hillcourt.ontology import Right
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_native_village.yml"
FAR_TILE = "t_00_00"
NEIGHBOR_TILE = "t_04_01"
COMMON_TILE = "t_05_03"
TRIBE_HOUSEHOLD = "hh_tribe_01"
LORD_HOUSEHOLD = "hh_court"


class TestCommonRightAccess(unittest.TestCase):
    """Геометрия соседства не даёт communal-доступа."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO, seed=1729)

    def test_common_right_grants_access_without_holding(self) -> None:
        """Общинное право открывает дальнюю клетку, но не надел и не кап."""
        household = self.world.households[TRIBE_HOUSEHOLD]
        household.current_tile_id = FAR_TILE
        tile = self.world.tiles[COMMON_TILE]
        self.assertTrue(has_access_to_communal_tile(self.world, household, tile))
        self.assertNotIn(COMMON_TILE, {held.id for held in own_tiles(self.world, household)})
        self.assertNotIn(COMMON_TILE, _held_tile_ids(self.world, household))

    def test_works_tiles_grant_access_without_common_right(self) -> None:
        """Клетка из `works_tiles` даёт доступ даже без `Right.common`."""
        household = self.world.households[TRIBE_HOUSEHOLD]
        household.current_tile_id = FAR_TILE
        tile = self.world.tiles["t_05_01"]
        self.assertIn("t_05_01", self.world.settlements[household.settlement_id].works_tiles)
        self.assertTrue(has_access_to_communal_tile(self.world, household, tile))

    def test_neighbor_without_right_or_works_has_no_access(self) -> None:
        """Соседняя клетка без права доступа не даёт communal-сбора."""
        household = self.world.households[TRIBE_HOUSEHOLD]
        current = self.world.tiles[household.current_tile_id]
        tile = self.world.tiles[NEIGHBOR_TILE]
        settlement = self.world.settlements[household.settlement_id]
        self.assertTrue(is_neighbor(current, tile))
        self.assertNotIn(NEIGHBOR_TILE, settlement.works_tiles)
        self.assertFalse(has_access_to_communal_tile(self.world, household, tile))

    def test_tenure_keeps_holding_without_communal_access(self) -> None:
        """`tenure` остаётся индивидуальным наделом, но не communal-доступом."""
        household = self.world.households[TRIBE_HOUSEHOLD]
        household.current_tile_id = FAR_TILE
        tile = self.world.tiles["t_06_01"]
        right = Right(
            id="right_tenure_access_probe",
            holder_household_id=household.id,
            tile_id=tile.id,
            kind="tenure",
            granted_date=self.world.clock.date,
            rent_share=0.1,
        )
        self.world.rights[right.id] = right
        self.assertFalse(has_access_to_communal_tile(self.world, household, tile))
        self.assertIn("t_06_01", {held.id for held in own_tiles(self.world, household)})
        self.assertIn("t_06_01", _held_tile_ids(self.world, household))

    def test_grazing_keeps_holding_without_common_access(self) -> None:
        """`grazing` остаётся личным наделом и не становится общинным доступом."""
        household = self.world.households[LORD_HOUSEHOLD]
        household.current_tile_id = FAR_TILE
        tile = self.world.tiles[COMMON_TILE]
        self.assertFalse(has_access_to_communal_tile(self.world, household, tile))
        self.assertIn(COMMON_TILE, {held.id for held in own_tiles(self.world, household)})
        self.assertIn(COMMON_TILE, _held_tile_ids(self.world, household))


if __name__ == "__main__":
    unittest.main()
