"""Проверка приёма событий месяца для отчётов обоза."""

from __future__ import annotations

import unittest

from hillcourt.news.month_events import event_amount, reportable_events


class TestMonthEventInput(unittest.TestCase):
    """Событие месяца отбирается по виду и товару, не читая склад."""

    def test_unknown_and_incomplete_events_are_not_reportable(self) -> None:
        events = [
            {"kind": "caravan_departure", "good": "salt", "amount": 3.0},
            {"kind": "unknown", "good": "salt", "amount": 99.0},
            {"kind": "caravan_arrival", "good": "salt"},
        ]
        self.assertEqual(len(reportable_events(events)), 1)
        self.assertEqual(event_amount(events, "caravan_departure", "salt"), 3.0)

    def test_settlement_filter_keeps_only_that_flow(self) -> None:
        events = [
            {
                "kind": "caravan_departure",
                "good": "salt",
                "amount": 2.0,
                "settlement_id": "salt_village",
            },
            {
                "kind": "caravan_departure",
                "good": "salt",
                "amount": 8.0,
                "settlement_id": "other_village",
            },
        ]
        self.assertEqual(
            event_amount(events, "caravan_departure", "salt", "salt_village"), 2.0
        )


if __name__ == "__main__":
    unittest.main()
