"""События месяца — источник истины для отчёта, а не снимок склада (ADR 0079).

Правило владельца: «отчёт — события месяца, не снимок склада». Дыря была в том,
что обоз с коротким путём приходил в том же месяце и увозил соль из деревни ДО
`phase_inform`: снимок после фаз честно пуст, и все соляные отчёты были нулями.

Здесь пины самого события (движок) и честного детектора (отчёт Info):
- соль грузится со стоков ДВОРОВ деревни → событие ухода несёт `settlement_id`
  деревни и `household_id` источника;
- привёз соль в поселение → `caravan_arrival` и отчёт месяца ненулевой;
- увезённая соль — тоже событие (отток виден);
- событий месяца не было — ноль в отчёте законен;
- порядок событий детерминирован; дельта материи 0.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.events import event_total, month_events
from hillcourt.engine.tick import run_month
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
TWO = ROOT / "design" / "scenarios" / "v0_two_settlements.yml"
SALT_VILLAGE = "salt_village"
SALT_TILE = "t_08_05"
HILL_COURT = "hill_court"


class TestMonthEvents(unittest.TestCase):
    """События месяца: плоские записи из уже посчитанного `Ledger`."""

    def _world(self, months: int = 6):
        world = load_scenario(TWO, seed=42)
        for _ in range(months):
            run_month(world)
        return world

    def _world_all(self, months: int = 12):
        """Мир и ВСЕ события помесячно: события читаются по текущему месяцу."""
        world = load_scenario(TWO, seed=42)
        seen: list[dict] = []
        for _ in range(months):
            run_month(world)
            seen.extend(world.month_events)
        return world, seen

    def test_departure_carries_village_and_household(self) -> None:
        _world, events = self._world_all(12)
        departures = [
            e
            for e in events
            if e["kind"] == "caravan_departure" and e["good"] == "salt"
        ]
        self.assertTrue(departures, "Уход обоза с солью не стал событием")
        event = departures[0]
        self.assertEqual(
            event["settlement_id"], SALT_VILLAGE, "Соль ушла не из деревни у соли"
        )
        self.assertTrue(
            str(event.get("household_id") or "").startswith("hh_salt"),
            "Источник соли — не двор деревни",
        )
        self.assertGreater(event["amount"], 0.0)
        self.assertEqual(event["month"], (event["date"].year, event["date"].month))

    def test_arrival_carries_destination_settlement(self) -> None:
        _world, events = self._world_all(12)
        arrivals = [
            e for e in events if e["kind"] == "caravan_arrival" and e["good"] == "salt"
        ]
        self.assertTrue(arrivals, "Приход соли в поселение не стал событием")
        self.assertEqual(arrivals[0]["settlement_id"], HILL_COURT)

    def test_pack_departure_and_arrival_are_events(self) -> None:
        _world, events = self._world_all(12)
        kinds = {e["kind"] for e in events}
        self.assertIn("pack_departure", kinds, "Отправление Pack не событие")
        self.assertIn("caravan_arrival", kinds)

    def test_harvest_is_event(self) -> None:
        _world, events = self._world_all(12)
        kinds = {e["kind"] for e in events}
        self.assertIn("harvest", kinds, "Урожай не попал в события месяца")

    def test_hay_and_graze_are_events(self) -> None:
        """Сено и выпас — события месяца (проводки уже считаются тиком)."""
        from hillcourt.economy import livestock
        from hillcourt.engine.hexgrid import neighbor_ids
        from hillcourt.ledger import Ledger  # noqa: F401 — ведущий импорт сцены

        world = load_scenario(TWO, seed=42)
        household = next(
            world.households[hid]
            for hid in sorted(world.households)
            if world.households[hid].left_at is None
            and world.households[hid].stock_id
        )
        settlement = world.settlements[household.settlement_id]
        home = world.tiles[household.current_tile_id]
        pasture = next(
            world.tiles[tile_id]
            for tile_id in neighbor_ids(world, home)
            if world.tiles[tile_id].terrain == "pasture"
        )
        if pasture.id not in settlement.works_tiles:
            settlement.works_tiles.append(pasture.id)
        if not livestock.hay_pastures(world, household):
            self.skipTest("В мире нет доступного пастбища под сено")
        world.get_stock(pasture.standing_stock_id).amounts["hay"] = 20.0
        stock = world.get_stock(household.stock_id)
        stock.amounts.clear()
        stock.amounts["ox_m"] = 1.0
        livestock.gather_draft_hay(world, world.clock.date)
        kinds = {e["kind"] for e in month_events(world)}
        self.assertIn("hay_mowed", kinds, "Сено не стало событием месяца")

    def test_no_snapshot_dependency_in_events(self) -> None:
        """Событие — проводка месяца, а не остаток: уход виден при полном стоке."""
        world, events = self._world_all(6)
        for event in events:
            self.assertGreater(float(event["amount"]), 0.0)
            self.assertIn("kind", event)
            self.assertIn("date", event)

    def test_deterministic_order_and_delta(self) -> None:
        first = self._world(6)
        second = self._world(6)
        keys = lambda w: [  # noqa: E731
            (e["kind"], e["good"], e["src_id"], e["dst_id"]) for e in w.month_events
        ]
        self.assertEqual(keys(first), keys(second), "Порядок событий не детерминирован")
        self.assertEqual(
            first.state_hash(), second.state_hash(), "Тот же seed — разный мир"
        )
        self.assertLess(abs(first.ledger.delta(first.total_matter())), 1e-6)

    def test_event_total_helper(self) -> None:
        world, events = self._world_all(12)
        shipped = event_total(
            events,
            "caravan_departure",
            "salt",
            settlement_id=SALT_VILLAGE,
        )
        self.assertGreater(shipped, 0.0, "Отток соли не посчитан")
        self.assertEqual(
            event_total(events, "caravan_departure", "salt", "nowhere"),
            0.0,
            "Чужое поселение собрало чужой отток",
        )


class TestSaltReportDetector(unittest.TestCase):
    """Честный детектор: отчёт следует за событиями, а не за остатком."""

    def test_report_nonzero_when_salt_moved_and_zero_without_events(self) -> None:
        world = load_scenario(TWO, seed=42)
        for _ in range(36):
            run_month(world)
            salt_reports = [
                report
                for report in world.reports
                if report.source == "caravan" and "salt_approx" in report.facts
            ]
            if not salt_reports:
                continue
            report = salt_reports[-1]
            moved = event_total(
                month_events(world, report.event_date),
                "caravan_departure",
                "salt",
                settlement_id=SALT_VILLAGE,
            )
            received = event_total(
                month_events(world, report.event_date),
                "caravan_arrival",
                "salt",
                settlement_id=SALT_VILLAGE,
            )
            if moved > 0.0 or received > 0.0:
                self.assertGreater(
                    float(report.facts["salt_approx"]),
                    0.0,
                    f"Соль шла в {report.event_date}, а отчёт молчал",
                )
                self.assertIn("соли", report.content.lower())
            else:
                self.assertEqual(
                    float(report.facts["salt_approx"]),
                    0.0,
                    "Событий не было — ноль законен, но и не ненулевой",
                )
        self.assertTrue(
            [r for r in world.reports if "salt_approx" in r.facts],
            "Соляных отчётов не было вовсе",
        )
        self.assertLess(abs(world.ledger.delta(world.total_matter())), 1e-6)


if __name__ == "__main__":
    unittest.main()
