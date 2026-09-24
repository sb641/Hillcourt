"""Право v0: режимы земли, статусы, уход по статусу, сосед-не-вассал."""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.tick import phase_migrate, phase_obligations
from hillcourt.hazards.travel import resolve_packs
from hillcourt.legal.actions import grant_tenure, send_party
from hillcourt.legal.regimes import (
    allowed_actions,
    can_be_sent,
    can_leave,
    effective_regime_id,
    vassalage_allowed,
)
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"


class TestLegalRegimes(unittest.TestCase):
    """Что двор МОЖЕТ на клетке и кто может уйти/быть послан."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO)

    def test_forest_is_reserved_wood(self) -> None:
        forest = next(
            t for t in self.world.tiles.values() if t.terrain == "forest"
        )
        self.assertEqual(forest.regime_id, "reserved_wood")

    def test_free_holding_allows_plough_and_leave(self) -> None:
        hh = self.world.households["hh_01"]
        tile = self.world.tiles[hh.current_tile_id]
        self.assertEqual(tile.regime_id, "free_holding")
        actions = allowed_actions(self.world, hh, tile)
        self.assertIn("plough", actions)
        self.assertIn("leave", actions)

    def test_reserved_wood_forbids_brushwood(self) -> None:
        hh = self.world.households["hh_01"]
        forest = next(
            t for t in self.world.tiles.values() if t.terrain == "forest"
        )
        actions = allowed_actions(self.world, hh, forest)
        self.assertNotIn("gather_brushwood", actions)
        self.assertIn("take_game", actions)

    def test_foreign_tile_only_leave(self) -> None:
        hh = self.world.households["hh_01"]
        salt = self.world.settlements["salt_village"]
        salt_tile = self.world.tiles[f"t_{salt.coord[0]:02d}_{salt.coord[1]:02d}"]
        self.assertEqual(effective_regime_id(self.world, hh, salt_tile), "foreign")
        self.assertEqual(allowed_actions(self.world, hh, salt_tile), {"leave"})

    def test_villein_cannot_leave_legally(self) -> None:
        hh = self.world.households["hh_02"]
        self.assertEqual(hh.legal_status_id, "villein")
        self.assertEqual(hh.personal_status, "tied")
        self.assertFalse(can_leave(self.world, hh))
        hh.hunger_days = 5
        phase_migrate(self.world)
        self.assertIsNone(hh.left_at, "Виллан ушёл легально")

    def test_sokeman_can_leave(self) -> None:
        hh = self.world.households["hh_01"]
        self.assertEqual(hh.legal_status_id, "sokeman")
        self.assertEqual(hh.personal_status, "free")
        self.assertTrue(can_leave(self.world, hh))
        hh.hunger_days = 5
        phase_migrate(self.world)
        self.assertIsNotNone(hh.left_at, "Сокмен не смог уйти")

    def test_send_requires_status(self) -> None:
        self.assertFalse(can_be_sent(self.world, self.world.households["hh_01"]))
        self.assertTrue(can_be_sent(self.world, self.world.households["hh_retinue"]))

    def test_neighbour_holder_is_equal_not_vassal(self) -> None:
        self.assertFalse(vassalage_allowed())
        for hid in ("hh_salt_01", "hh_salt_02"):
            hh = self.world.households[hid]
            self.assertEqual(hh.legal_status_id, "holder")
            rent = [
                self.world.obligations[oid]
                for oid in hh.obligation_ids
                if self.world.obligations[oid].kind == "rent"
            ]
            self.assertFalse(rent, f"{hid}: держатель платит ренту соседу")

    def test_offices_travel_no_reports(self) -> None:
        for office_id in ("sheriff", "huntsman"):
            office = self.world.catalogs.offices[office_id]
            self.assertTrue(office.travel)

    def test_grant_tenure_registers_right(self) -> None:
        right = grant_tenure(self.world, "hh_01", "t_00_00", rent_share=0.2)
        self.assertIn(right.id, self.world.rights)
        self.assertEqual(self.world.tiles["t_00_00"].regime_id, "tenement")

    def test_corvee_accrues_on_period_only(self) -> None:
        hh = self.world.households["hh_02"]
        oid = next(
            oid
            for oid in hh.obligation_ids
            if self.world.obligations[oid].basis == "duty"
        )
        obligation = self.world.obligations[oid]
        obligation.period_months = 3  # проверяем именно период, а не месячный бандл
        self.world.clock.month = 3
        phase_obligations(self.world)
        accrued = obligation.corvee_days
        self.assertGreater(accrued, 0.0, "Повинность не начислена в свой период")
        self.world.clock.month = 4
        phase_obligations(self.world)
        self.assertEqual(obligation.corvee_days, accrued, "Повинность идёт каждый месяц")

    def test_send_party_detaches_and_returns(self) -> None:
        hh = self.world.households["hh_retinue"]
        before = sorted(hh.member_ids)
        pack = send_party(self.world, "hh_retinue", before, "t_02_01")
        self.assertEqual(hh.member_ids, [], "Посланные остались в своём дворе")
        resolve_packs(self.world, pack.eta_date)
        self.assertEqual(sorted(hh.member_ids), before, "Уцелевшие не вернулись")


if __name__ == "__main__":
    unittest.main()
