"""Проверка И-3: игрок видит известие, а не истину клетки."""

from __future__ import annotations

import unittest
from dataclasses import fields
from pathlib import Path

from hillcourt.engine.tick import run_month
from hillcourt.news.views import PlayerView, ReportView, build_player_view
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"


class TestNewsNoOmniscience(unittest.TestCase):
    """Известия запаздывают, врут, иногда молчат; знание игрока — только из них."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = load_scenario(SCENARIO)
        for _ in range(36):
            run_month(cls.world)

    def test_distortion_and_silence_exist(self) -> None:
        self.assertTrue(
            any(report.distorted for report in self.world.reports),
            "Ни одно известие не искажено — игрок читает истину",
        )
        self.assertTrue(
            any(report.source == "silence" for report in self.world.reports),
            "Молчание недостижимо — модель известий неполна",
        )

    def test_neighbour_facts_are_approximate(self) -> None:
        neighbours = [r for r in self.world.reports if r.source == "adjacent_daily"]
        self.assertTrue(neighbours, "Нет соседских известий")
        for report in neighbours:
            self.assertIn("grain_approx", report.facts)
            self.assertNotIn(
                "grain", report.facts, "Сосед передаёт точное зерно, а не рассказ"
            )

    def test_source_vocabulary_is_v0_set(self) -> None:
        allowed = {"eye_from_hill", "adjacent_daily", "messenger", "caravan", "silence"}
        sources = {r.source for r in self.world.reports}
        self.assertTrue(sources.issubset(allowed), f"Лишние источники: {sources - allowed}")

    def test_distortion_is_recorded_as_noise(self) -> None:
        self.assertTrue(
            any(r.noise > 0 for r in self.world.reports),
            "Ни один канал не несёт шума — известие выглядит как истина",
        )

    def test_player_view_is_built_from_reports_only(self) -> None:
        final_date = self.world.clock.date
        view = build_player_view(self.world, final_date)
        self.assertIsInstance(view, PlayerView)
        delivered = {
            report.id
            for report in self.world.reports
            if report.delivery_date <= final_date
        }
        self.assertEqual({entry.id for entry in view.entries}, delivered)

    def test_report_view_has_no_world_truth_fields(self) -> None:
        names = {field.name for field in fields(ReportView)}
        for forbidden in ("tile", "household", "stock", "world"):
            self.assertNotIn(forbidden, names, f"ReportView светит истиной: {forbidden}")

    def test_staleness_is_marked(self) -> None:
        view = build_player_view(self.world, self.world.clock.date)
        self.assertTrue(
            any(entry.stale for entry in view.entries),
            "Старые известия не помечаются устаревшими",
        )
        for entry in view.entries:
            self.assertIsInstance(entry.stale, bool)


if __name__ == "__main__":
    unittest.main()
