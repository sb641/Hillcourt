"""Проверка И-3 для вести о смене занятия двора."""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.news.actions import report_action_changes
from hillcourt.news.views import build_player_view
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"


class TestActionChangeNews(unittest.TestCase):
    """Смена занятия раскрывается только доставленной вестью."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO, seed=17)
        self.event = {
            "kind": "household_action_changed",
            "household_id": "hh_court",
            "old_action": "work_plot",
            "new_action": "make_cheese",
            "date": self.world.clock.date,
            "observer_id": "neighbor_salt",
        }

    def test_change_report_is_delayed(self) -> None:
        report = report_action_changes(self.world, [self.event])[0]
        self.assertEqual(report.observer_id, "neighbor_salt")
        self.assertEqual(report.source, "adjacent_daily")
        self.assertEqual(
            report.delivery_date.to_day_index(),
            self.event["date"].to_day_index() + 3,
        )
        self.assertEqual(report.facts["change"], "action_changed")
        self.assertEqual(report.facts["new_action"], "make_cheese")

    def test_undelivered_change_is_not_knowledge(self) -> None:
        report = report_action_changes(self.world, [self.event])[0]
        before = build_player_view(self.world, self.world.clock.date)
        after = build_player_view(self.world, report.delivery_date)
        self.assertNotIn(report.id, {entry.id for entry in before.entries})
        self.assertIn(report.id, {entry.id for entry in after.entries})

    def test_same_seed_is_deterministic(self) -> None:
        first = load_scenario(SCENARIO, seed=17)
        second = load_scenario(SCENARIO, seed=17)
        first_report = report_action_changes(first, [self.event])[0]
        second_report = report_action_changes(second, [self.event])[0]
        self.assertEqual(first_report.content, second_report.content)
        self.assertEqual(first_report.facts, second_report.facts)
        self.assertEqual(first_report.delivery_date, second_report.delivery_date)


if __name__ == "__main__":
    unittest.main()
