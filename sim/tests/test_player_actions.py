"""Действия игрока v0: пишут лог, рождают Report и не двигают материю."""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.legal.actions import add_obligation, grant_tenure, send_party
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"


class TestPlayerActions(unittest.TestCase):
    """Три действия v0 меняют право/повинность/посылку и будят известие."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO)

    def test_actions_log_reports_and_keep_matter(self) -> None:
        world = self.world
        before = world.total_matter()

        right = grant_tenure(world, "hh_03", "t_05_02", rent_share=0.1)
        self.assertIn(right.id, world.rights)
        grant = world.player_actions[-1]
        self.assertEqual(grant["date"], "Y1-M01")
        self.assertEqual(grant["month"], 1)
        self.assertEqual(grant["action"], "grant_tenure")
        self.assertTrue(
            any(
                r.source == "eye_from_hill" and r.subject_id == right.id
                for r in world.reports
            ),
            "grant_tenure не породил Report",
        )

        obligation = add_obligation(world, "hh_03", "fixed_rent", "grain", 0.5)
        self.assertIn(obligation.id, world.obligations)
        add = world.player_actions[-1]
        self.assertEqual(add["date"], "Y1-M01")
        self.assertEqual(add["action"], "add_obligation")
        self.assertTrue(
            any(
                r.source == "eye_from_hill" and r.subject_id == obligation.id
                for r in world.reports
            ),
            "add_obligation не породил Report",
        )

        household = world.households["hh_retinue"]
        adults = list(household.member_ids)
        pack = send_party(world, "hh_retinue", adults, "t_00_01")
        report = next(
            (
                r
                for r in world.reports
                if r.source == "messenger" and r.subject_id == pack.id
            ),
            None,
        )
        self.assertIsNotNone(report, "send_party не породил Report гонца")
        self.assertEqual(report.facts["destination"], "t_00_01")
        sent = world.player_actions[-1]
        self.assertEqual(sent["date"], "Y1-M01")
        self.assertEqual(sent["action"], "send_party")

        self.assertAlmostEqual(world.total_matter(), before, places=6)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
