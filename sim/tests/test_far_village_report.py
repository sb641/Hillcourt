"""Задача D4: дальняя деревня видна только через Report, а не напрямую.

`ash_village` (`kind: village`) существует в мире, но игрок не читает её
зерно/соль из `Tile`/`Household`: раз в 3 месяца обоз приносит Report с датой
и источником (или молчание). Тест фиксирует отсутствие всеведения до 2-го
месяца, доставку отчёта за 36 месяцев и согласованность `grain_approx` с
фактическим зерном дворов в пределах шума канала.
"""

from __future__ import annotations

import unittest
from dataclasses import fields
from pathlib import Path

from hillcourt.engine.tick import run_month
from hillcourt.info.briefing import make_month_reports
from hillcourt.news.views import ReportView, build_player_view
from hillcourt.ontology import SimDate
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_two_settlements.yml"
ASH_TILE = "t_02_07"


def _set_ash_grain(world, total: float) -> None:
    """Обнулить зерно ВСЕМ дворам ash_village и выдать total одному из них.

    Состав дворов берётся из мира, а не списком av_01..av_08: иначе новые
    дворы (av_09+) остаются со стартовым зерном и фактическая сумма не равна
    заданной.
    """
    stock_ids = [
        f"household:{hid}"
        for hid in sorted(world.settlements["ash_village"].household_ids)
    ]
    for stock_id in stock_ids:
        world.get_stock(stock_id).amounts["grain"] = 0.0
    world.get_stock(stock_ids[0]).amounts["grain"] = total


class TestFarVillageReport(unittest.TestCase):
    """Дальняя деревня — рассказ обоза с датой, источником и шумом, а не истина."""

    def test_player_is_blind_before_second_month(self) -> None:
        world = load_scenario(SCENARIO)
        view = build_player_view(world, SimDate(1, 1))
        self.assertEqual(
            [e for e in view.entries if e.subject_id == ASH_TILE],
            [],
            "Игрок знает о дальней деревне раньше, чем дошёл вестник",
        )

    def test_caravan_report_delivered_over_36_months(self) -> None:
        world = load_scenario(SCENARIO)
        for _ in range(36):
            run_month(world)
        view = build_player_view(world, world.clock.date)
        delivered = [
            e
            for e in view.entries
            if e.subject_id == ASH_TILE and e.source == "caravan"
        ]
        self.assertTrue(delivered, "За 36 месяцев обоз не принёс вести о дальней деревне")
        for entry in delivered:
            self.assertGreaterEqual(entry.facts["grain_approx"], 0.0)
            self.assertNotEqual(entry.content.strip(), "")
            self.assertIsInstance(entry.delivery_date, SimDate)
            self.assertGreater(entry.delivery_date, entry.event_date)

    def test_report_view_has_only_story_fields(self) -> None:
        names = {field.name for field in fields(ReportView)}
        for forbidden in ("tile", "household", "stock", "amounts", "world", "truth"):
            self.assertNotIn(forbidden, names, f"ReportView светит истиной: {forbidden}")

    def test_grain_approx_matches_households_within_noise(self) -> None:
        world = load_scenario(SCENARIO)
        _set_ash_grain(world, 40.0)
        observed = None
        seen = {report.id for report in world.reports}
        for month in range(3, 3 + 3 * 40, 3):
            world.clock.month = month
            make_month_reports(world)
            fresh = [
                r
                for r in world.reports
                if r.id not in seen
                and r.source == "caravan"
                and r.subject_id == ASH_TILE
            ]
            seen = {report.id for report in world.reports}
            if fresh:
                observed = fresh[0]
                break
        self.assertIsNotNone(observed, "Ни разу не пришёл обоз с зерном дальней деревни")
        reported = observed.facts["grain_approx"]
        self.assertGreaterEqual(reported, 24.0 - 1e-9)
        self.assertLessEqual(reported, 56.0 + 1e-9)

    def test_silence_does_not_leak_truth(self) -> None:
        world = load_scenario(SCENARIO)
        _set_ash_grain(world, 40.0)
        seen = {report.id for report in world.reports}
        silences = []
        for month in range(3, 3 + 3 * 40, 3):
            world.clock.month = month
            make_month_reports(world)
            for report in world.reports:
                if report.id in seen:
                    continue
                if report.subject_id == ASH_TILE and report.source == "silence":
                    silences.append(report)
            seen = {report.id for report in world.reports}
            if silences:
                break
        self.assertTrue(silences, "Молчание о дальней деревне недостижимо")
        self.assertEqual(silences[0].facts, {})


if __name__ == "__main__":
    unittest.main()
