"""Прогон тика: дата, еда, детерминизм, рента и известия."""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.tick import run_month
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"


class TestTickRuns(unittest.TestCase):
    """Проверка того, что месячный тик исполняется и детерминирован."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO)

    def test_twelve_months_and_date(self) -> None:
        for _ in range(12):
            run_month(self.world)
        self.assertEqual(str(self.world.clock.date), "Y2-M01")

    def test_every_household_has_grain_key(self) -> None:
        for _ in range(12):
            run_month(self.world)
        for hid in sorted(self.world.households):
            stock = self.world.get_stock(self.world.households[hid].stock_id)
            self.assertIn("grain", stock.amounts, f"У {hid} нет ключа grain")

    def test_determinism_same_seed(self) -> None:
        world_a = load_scenario(SCENARIO)
        world_b = load_scenario(SCENARIO)
        for _ in range(12):
            run_month(world_a)
            run_month(world_b)
        self.assertEqual(world_a.state_hash(), world_b.state_hash())

    def test_rent_is_paid_or_in_default(self) -> None:
        for _ in range(12):
            run_month(self.world)
        self.assertTrue(
            any(
                o.paid_total > 0 or o.arrears > 0
                for o in self.world.obligations.values()
            ),
            "Ни одна повинность не оплачена и не в недоимке",
        )

    def test_reports_are_well_formed(self) -> None:
        for _ in range(12):
            run_month(self.world)
        self.assertTrue(self.world.reports, "Отчётов нет")
        for report in self.world.reports:
            self.assertTrue(report.source)
            self.assertIsNotNone(report.event_date)
            self.assertIsNotNone(report.delivery_date)
            self.assertGreaterEqual(report.confidence, 0.0)
            self.assertLessEqual(report.confidence, 1.0)


if __name__ == "__main__":
    unittest.main()
