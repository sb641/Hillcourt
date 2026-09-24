"""Задача H2: обоз доносит не только «сколько лежит», но и «вёз / доехало».

До H2 игрок не знал о рейсе: мир возил соль и зерно, но Report обоза либо
докладывал сток деревни, либо молчал о самом рейсе. Тест требует Report с
фактами `carried`/`delivered`/`ratio`/`lost` на месяц прибытия (`eta_date`),
нулевой задержкой и согласованностью с памятью сделки. Отдельно закреплено:
соль в дворах не превращается в «0», а `ReportView` не светит истиной мира.
"""

from __future__ import annotations

import unittest
from dataclasses import fields
from pathlib import Path

from hillcourt.engine.tick import run_month
from hillcourt.info.briefing import make_month_reports
from hillcourt.news.views import ReportView
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_two_settlements.yml"
MONTHS = 36
SALT_STOCKS = (
    "household:hh_salt_01",
    "household:hh_salt_02",
    "settlement:salt_village",
)


def _arrival_reports(world):
    """Report обоза, а не рассказ о стоке: у прибытия есть факт `carried`."""
    return [
        report
        for report in world.reports
        if report.source == "caravan" and "carried" in report.facts
    ]


class TestCaravanArrivalReport(unittest.TestCase):
    """Report обоза: вёз, доехало, доля — память сделки, а не истина стока."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = load_scenario(SCENARIO)
        for _ in range(MONTHS):
            run_month(cls.world)

    def test_arrival_report_shape(self) -> None:
        arrivals = _arrival_reports(self.world)
        self.assertTrue(arrivals, "За 36 месяцев ни одного Report о прибытии воза")
        for report in arrivals:
            facts = report.facts
            self.assertGreater(
                facts["carried"], 0.0, "Обоз вёз не больше нуля — отчёт выдуман"
            )
            self.assertGreaterEqual(facts["delivered"], 0.0)
            self.assertGreaterEqual(facts["ratio"], 0.0)
            self.assertLessEqual(facts["ratio"], 1.0)
            self.assertEqual(facts["lost"], 0, "У обоза нет людей, потерь быть не может")
            self.assertNotEqual(report.content.strip(), "")
            self.assertEqual(
                report.delivery_date,
                report.event_date,
                "Отчёт о прибытии пришёл с задержкой, а не в месяц прибытия",
            )

    def test_ratio_matches_barter_memory(self) -> None:
        arrivals = _arrival_reports(self.world)
        self.assertTrue(arrivals, "Нет Report о прибытии — нечего сверять с памятью")
        memory = {
            (round(entry["carried"], 9), round(entry["delivered"], 9)): entry
            for entry in self.world.barter_memory.values()
            if entry.get("ratio") is not None and entry.get("carried", 0.0) > 0.0
        }
        self.assertTrue(memory, "За 36 месяцев память сделок обоза пуста")
        matched = 0
        for report in arrivals:
            facts = report.facts
            self.assertGreater(facts["carried"], 0.0)
            self.assertAlmostEqual(
                facts["ratio"],
                facts["delivered"] / facts["carried"],
                places=9,
                msg="Доля в отчёте не равна delivered/carried",
            )
            entry = memory.get(
                (round(facts["carried"], 9), round(facts["delivered"], 9))
            )
            if entry is not None:
                matched += 1
                self.assertAlmostEqual(entry["ratio"], facts["ratio"], places=9)
        self.assertGreater(matched, 0, "Ни один отчёт не сверен с памятью сделки")

    def test_salt_report_not_zero_when_month_event_has_salt(self) -> None:
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

        salt_reports = [
            report
            for report in world.reports
            if report.source == "caravan"
            and "salt_approx" in report.facts
            and report.subject_id == "t_08_05"
        ]
        self.assertTrue(salt_reports)
        self.assertGreater(salt_reports[0].facts["salt_approx"], 0.0)
        self.assertIn("соли", salt_reports[0].content)

    def test_report_view_has_no_world_truth_fields(self) -> None:
        names = {field.name for field in fields(ReportView)}
        for forbidden in ("tile", "household", "stock", "amounts", "world", "truth"):
            self.assertNotIn(forbidden, names, f"ReportView светит истиной: {forbidden}")


if __name__ == "__main__":
    unittest.main()
