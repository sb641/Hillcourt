"""Правовой контур общинной охоты: мелкая дичь доступна, крупная — баронская."""

from __future__ import annotations

import unittest
from dataclasses import fields
from pathlib import Path

from hillcourt.legal import regimes
from hillcourt.legal.regimes import (
    LARGE_GAME_GOODS,
    SMALL_GAME_GOODS,
    allowed_actions,
    can_take_game,
    hunt_destination_stock_id,
    take_game,
)
from hillcourt.ontology import Obligation, Right
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
COMMON_TILE = "t_00_00"
SMALL_GAME = "rabbit"
LARGE_GAME = "deer"
COMMON_HOUSEHOLD = "hh_01"
NO_RIGHT_HOUSEHOLD = "hh_02"
BARON = "hh_court"
RETINUE = "hh_retinue"


class TestCommonGameRight(unittest.TestCase):
    """Общинное право открывает только мелкую дичь."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO, seed=1729)
        self.tile = self.world.tiles[COMMON_TILE]

    def _grant_common(self, household_id: str) -> None:
        household = self.world.households[household_id]
        right = Right(
            id=f"right_test_common_{household_id}",
            holder_household_id=household.id,
            tile_id=self.tile.id,
            kind="common",
            granted_date=self.world.clock.date,
            rent_share=0.0,
        )
        self.world.rights[right.id] = right

    def _seed_game(self, good_id: str, amount: float = 4.0) -> None:
        stock = self.world.get_stock(self.tile.standing_stock_id)
        self.world.ledger.external_in(
            stock,
            good_id,
            amount,
            "test_game_spawn",
            "test_game_spawn",
            self.world.clock.date,
        )

    def test_common_right_takes_small_game_into_household_stock(self) -> None:
        household = self.world.households[COMMON_HOUSEHOLD]
        self._grant_common(household.id)
        self._seed_game(SMALL_GAME)
        source = self.world.get_stock(self.tile.standing_stock_id)
        destination = self.world.get_stock(household.stock_id)

        self.assertTrue(can_take_game(self.world, household, self.tile, SMALL_GAME))
        self.assertIn("take_game", allowed_actions(self.world, household, self.tile))
        self.assertEqual(
            hunt_destination_stock_id(self.world, household, self.tile, SMALL_GAME),
            household.stock_id,
        )
        take_game(self.world, household, self.tile, SMALL_GAME, amount=1.0)

        self.assertEqual(destination.amounts[SMALL_GAME], 1.0)
        self.assertEqual(source.amounts[SMALL_GAME], 3.0)
        self.assertTrue(
            any(report.facts.get("event") == "hunt" for report in self.world.reports)
        )
        self.assertAlmostEqual(
            self.world.ledger.delta(self.world.total_matter()), 0.0, places=6
        )

    def test_common_right_rejects_large_game(self) -> None:
        household = self.world.households[COMMON_HOUSEHOLD]
        self._grant_common(household.id)
        self._seed_game(LARGE_GAME)
        source = self.world.get_stock(self.tile.standing_stock_id)
        before = source.amounts[LARGE_GAME]

        self.assertFalse(can_take_game(self.world, household, self.tile, LARGE_GAME))
        with self.assertRaises(PermissionError):
            take_game(self.world, household, self.tile, LARGE_GAME, amount=1.0)

        self.assertEqual(source.amounts[LARGE_GAME], before)
        self.assertFalse(
            any(report.facts.get("event") == "hunt" for report in self.world.reports)
        )

    def test_baron_and_retinue_can_take_small_and_large_game(self) -> None:
        self._seed_game(SMALL_GAME, amount=8.0)
        self._seed_game(LARGE_GAME, amount=8.0)
        manor_stock_id = self.world.manors[self.world.player_manor_id].stock_id
        manor_stock = self.world.get_stock(manor_stock_id)

        for household_id in (BARON, RETINUE):
            household = self.world.households[household_id]
            for good_id in (SMALL_GAME, LARGE_GAME):
                self.assertTrue(
                    can_take_game(self.world, household, self.tile, good_id)
                )
                before = manor_stock.amounts.get(good_id, 0.0)
                take_game(self.world, household, self.tile, good_id, amount=1.0)
                self.assertEqual(manor_stock.amounts[good_id], before + 1.0)

    def test_household_without_common_right_cannot_take_game(self) -> None:
        household = self.world.households[NO_RIGHT_HOUSEHOLD]
        self._seed_game(SMALL_GAME)
        self._seed_game(LARGE_GAME)
        source = self.world.get_stock(self.tile.standing_stock_id)
        before = dict(source.amounts)

        for good_id in (SMALL_GAME, LARGE_GAME):
            self.assertFalse(can_take_game(self.world, household, self.tile, good_id))
            with self.assertRaises(PermissionError):
                take_game(self.world, household, self.tile, good_id, amount=1.0)

        self.assertEqual(source.amounts, before)
        self.assertNotIn("take_game", allowed_actions(self.world, household, self.tile))

    def test_hunt_report_omits_internal_regime_and_stock_ids(self) -> None:
        household = self.world.households[COMMON_HOUSEHOLD]
        self._grant_common(household.id)
        self._seed_game(SMALL_GAME)
        take_game(self.world, household, self.tile, SMALL_GAME, amount=1.0)
        report = next(
            report for report in self.world.reports
            if report.facts.get("event") == "hunt"
        )
        self.assertNotIn("regime", report.facts)
        self.assertNotIn("destination_stock_id", report.facts)
        self.assertNotIn("stock_id", report.facts)

    def test_hunt_has_one_report_path_without_zero_noise_duplicate(self) -> None:
        source = Path(regimes.__file__).read_text(encoding="utf-8")
        self.assertNotIn("noise=0.0", source)
        self.assertNotIn("make_report(", source)
        self.assertIn("report_hunt(", source)

    def test_hunt_determinism_keeps_state_hash_equal(self) -> None:
        hashes = []
        for _ in range(2):
            world = load_scenario(SCENARIO, seed=1729)
            household = world.households[COMMON_HOUSEHOLD]
            right = Right(
                id="right_test_determinism",
                holder_household_id=household.id,
                tile_id=COMMON_TILE,
                kind="common",
                granted_date=world.clock.date,
                rent_share=0.0,
            )
            world.rights[right.id] = right
            tile = world.tiles[COMMON_TILE]
            world.ledger.external_in(
                world.get_stock(tile.standing_stock_id),
                SMALL_GAME,
                4.0,
                "test_game_spawn",
                "test_game_spawn",
                world.clock.date,
            )
            take_game(world, household, tile, SMALL_GAME, amount=1.0)
            hashes.append(world.state_hash())
        self.assertEqual(hashes[0], hashes[1])

        forbidden = {"crime", "punishment", "punishments", "sentence"}
        for entity in (Right, Obligation):
            names = {field.name for field in fields(entity)}
            self.assertTrue(names.isdisjoint(forbidden))
        self.assertTrue(
            all("punish" not in name.lower() for name in dir(regimes))
        )
        self.assertTrue(SMALL_GAME in SMALL_GAME_GOODS)
        self.assertTrue(LARGE_GAME in LARGE_GAME_GOODS)


if __name__ == "__main__":
    unittest.main()
