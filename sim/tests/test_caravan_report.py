"""Отчёт обоза проверяет события месяца, а не остаток склада (ADR 0079)."""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.info.briefing import make_month_reports
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SALT_TILE = "t_08_05"


def _caravan_reports(world):
    return [r for r in world.reports if r.source == "caravan"]


class TestCaravanReport(unittest.TestCase):
    """Отчёт обоза получает поток месяца и допускает честный ноль без событий."""

    def test_salt_arrival_is_nonzero_event_report(self) -> None:
        world = load_scenario(SCENARIO)
        world.clock.month = 3
        world.month_events = [
            {
                "kind": "caravan_departure",
                "settlement_id": "salt_village",
                "good": "salt",
                "amount": 7.5,
            }
        ]
        make_month_reports(world)

        report = next(r for r in _caravan_reports(world) if r.facts.get("salt_approx") is not None)
        self.assertEqual(report.subject_id, SALT_TILE)
        self.assertIn("сол", report.content)
        self.assertGreater(report.facts["salt_approx"], 0.0)
        self.assertEqual(report.facts["salt_received"], 0.0)
        self.assertEqual(report.confidence, 0.4)
        self.assertEqual(report.noise, 0.4)
        self.assertEqual(
            report.delivery_date,
            report.event_date.advance(world.clock.months_per_year).advance(
                world.clock.months_per_year
            ),
        )

    def test_no_events_allows_zero(self) -> None:
        world = load_scenario(SCENARIO)
        world.clock.month = 3
        world.month_events = []
        make_month_reports(world)

        reports = [
            report
            for report in _caravan_reports(world)
            if report.subject_id == SALT_TILE and "salt_approx" in report.facts
        ]
        self.assertTrue(reports)
        self.assertTrue(all(report.facts["salt_approx"] == 0.0 for report in reports))
        self.assertTrue(all(report.facts["salt_received"] == 0.0 for report in reports))

    def test_departure_is_visible_as_outflow(self) -> None:
        world = load_scenario(SCENARIO)
        world.clock.month = 3
        world.month_events = [
            {
                "kind": "caravan_departure",
                "settlement_id": "salt_village",
                "good": "salt",
                "amount": 4.0,
            }
        ]
        make_month_reports(world)

        report = next(r for r in _caravan_reports(world) if r.facts.get("salt_approx") is not None)
        self.assertGreater(report.facts["salt_approx"], 0.0)
        self.assertIn("соли", report.content)


if __name__ == "__main__":
    unittest.main()
