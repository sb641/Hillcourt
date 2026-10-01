"""Испытания канала разведки ADR 0074 на границе И-3."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from hillcourt.engine.path import known_tiles
import hillcourt.news.scouting as scouting
from hillcourt.news.scouting import RUMORS, make_scout_report, report_scout_observations
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"


class TestScoutingReports(unittest.TestCase):
    """Наблюдение раскрывает гекс только доставленным отчётом партии."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO)
        self.tile_id = "t_08_05"
        self.date = self.world.clock.date

    def test_fact_report_has_party_observer(self) -> None:
        event = {
            "kind": "scout_observe",
            "tile_id": self.tile_id,
            "confidence": 1.0,
            "fact": True,
            "rumor": None,
            "observer_id": "pack_scout_01",
            "date": self.date,
        }
        report = make_scout_report(self.world, event)
        self.assertIsNotNone(report)
        self.assertEqual(report.source, "scout")
        self.assertEqual(report.observer_id, "pack_scout_01")
        self.assertTrue(report.facts["fact"])
        self.assertIn(self.tile_id, report.content)
        self.assertEqual(report.delivery_date, self.date)
        self.assertIn(self.tile_id, known_tiles(self.world, self.date))

    def test_ring_two_is_rumor_not_fact(self) -> None:
        event = {
            "kind": "scout_observe",
            "tile_id": self.tile_id,
            "confidence": 0.5,
            "fact": False,
            "rumor": True,
            "observer_id": "pack_scout_01",
            "date": self.date,
        }
        report = make_scout_report(self.world, event)
        self.assertIsNotNone(report)
        self.assertFalse(report.facts["fact"])
        self.assertIn(report.facts["rumor"], RUMORS)
        self.assertIn("слух", report.content)

    def test_old_string_marker_is_ignored(self) -> None:
        event = {
            "kind": "scout_observe",
            "tile_id": self.tile_id,
            "confidence": 0.5,
            "fact": False,
            "rumor": "совсем другой текст",
            "observer_id": "pack_scout_01",
            "date": self.date,
        }
        report = make_scout_report(self.world, event)
        self.assertIsNotNone(report)
        self.assertFalse(report.facts["fact"])
        self.assertIn(report.facts["rumor"], RUMORS)
        self.assertNotIn("совсем другой текст", report.content)

    def test_rumor_wording_does_not_change_observation(self) -> None:
        event = {
            "kind": "scout_observe",
            "tile_id": self.tile_id,
            "confidence": 0.5,
            "fact": False,
            "rumor": True,
            "observer_id": "pack_scout_01",
            "date": self.date,
        }
        first = load_scenario(SCENARIO)
        second = load_scenario(SCENARIO)
        with patch.object(scouting, "RUMORS", RUMORS):
            first_reports = report_scout_observations(first, [event])
        with patch.object(scouting, "RUMORS", tuple(reversed(RUMORS))):
            second_reports = report_scout_observations(second, [event])
        first_events = [
            (r.source, r.subject_id, r.event_date, r.confidence, r.facts["fact"])
            for r in first_reports
        ]
        second_events = [
            (r.source, r.subject_id, r.event_date, r.confidence, r.facts["fact"])
            for r in second_reports
        ]
        self.assertEqual(first_events, second_events)
        self.assertEqual(first.state_hash(), second.state_hash())
        self.assertEqual(known_tiles(first), known_tiles(second))

    def test_undelivered_event_does_not_open_tile(self) -> None:
        event = {
            "kind": "scout_observe",
            "tile_id": self.tile_id,
            "confidence": 1.0,
            "fact": True,
            "rumor": None,
            "observer_id": "pack_scout_01",
            "date": self.date.advance(self.world.clock.months_per_year),
        }
        report = make_scout_report(self.world, event)
        self.assertIsNotNone(report)
        self.assertNotIn(self.tile_id, known_tiles(self.world, self.date))
        self.assertIn(self.tile_id, known_tiles(self.world, report.delivery_date))

    def test_no_event_or_non_scout_event_creates_no_report(self) -> None:
        self.world.month_events = []
        self.assertEqual(report_scout_observations(self.world), [])
        self.world.month_events = [
            {
                "kind": "harvest",
                "tile_id": self.tile_id,
                "confidence": 1.0,
                "fact": True,
                "rumor": None,
                "observer_id": "pack_scout_01",
                "date": self.date,
            }
        ]
        self.assertEqual(report_scout_observations(self.world), [])


if __name__ == "__main__":
    unittest.main()
