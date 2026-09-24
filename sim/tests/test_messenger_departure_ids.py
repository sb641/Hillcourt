"""Info-стык от Table: ушедший двор виден в messenger-отчёте с id и клеткой.

Без новых каналов, задержек и сущностей: тот же `messenger`-Report прибытия
(`hazards/travel.resolve_migrations`) несёт `household_id` и `tile` в
`content`/`facts`, чтобы держатель мог ответить своим рычагом.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.tick import run_month
from hillcourt.hazards.travel import resolve_migrations
from hillcourt.news.views import build_player_view
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SEED = 1729


class _SeqRng:
    """Детерминированная заглушка потока опасности: первый гибнет, остальные целы."""

    def __init__(self, values: list) -> None:
        self._values = list(values)

    def random(self) -> float:
        if self._values:
            return self._values.pop(0)
        return 0.99

    def uniform(self, _a: float, _b: float) -> float:
        return 0.0


def _first_departure(world):
    """Прокрутить до первого ухода; вернуть (двор, воз)."""
    for _ in range(24):
        run_month(world)
        for hid in sorted(world.households):
            household = world.households[hid]
            if household.left_at is None:
                continue
            pack = next(
                (
                    p
                    for p in world.packs.values()
                    if p.kind == "household_move"
                    and p.owner_household_id == hid
                ),
                None,
            )
            if pack is not None:
                return household, pack
    return None, None


class TestMessengerDepartureIds(unittest.TestCase):
    """messenger-отчёт об уходе содержит id двора и клетку."""

    def test_departed_household_visible_with_id_and_tile(self) -> None:
        world = load_scenario(SCENARIO, seed=SEED)
        household = None
        pack = None
        for _ in range(24):
            run_month(world)
            for hid in sorted(world.households):
                candidate = world.households[hid]
                if candidate.left_at is None:
                    continue
                found = next(
                    (
                        p
                        for p in world.packs.values()
                        if p.kind == "household_move"
                        and p.owner_household_id == hid
                    ),
                    None,
                )
                if found is not None:
                    household, pack = candidate, found
                    break
            if household is not None:
                break
        self.assertIsNotNone(household, "За 24 месяца ни один двор не ушёл")
        assert household is not None and pack is not None
        for _ in range(6):
            run_month(world)
            if pack.status in ("arrived", "lost"):
                break
        self.assertEqual(pack.status, "arrived", "Уход не дошёл")
        destination = pack.destination_tile_id

        reports = [
            r
            for r in world.reports
            if r.source == "messenger" and r.subject_id == destination
        ]
        self.assertTrue(reports, "Нет messenger-отчёта о прибытии двора")
        report = reports[-1]
        self.assertEqual(report.facts.get("household_id"), household.id)
        self.assertEqual(report.facts.get("tile"), destination)
        self.assertIn(household.id, report.content)
        self.assertIn(destination, report.content)

        view = build_player_view(world, world.clock.date)
        entries = [e for e in view.entries if e.id == report.id]
        self.assertTrue(entries, "Отчёт не дошёл до знания игрока")
        self.assertIn(household.id, entries[0].content)
        self.assertEqual(entries[0].facts.get("household_id"), household.id)
        self.assertEqual(entries[0].facts.get("tile"), destination)

        self.assertLess(abs(world.ledger.delta(world.total_matter())), 1e-6)

    def test_rejected_arrival_pins_household_id_and_tile_about(self) -> None:
        """Ветка тесноты: `tile` = about (destination), двор физически на origin."""
        world = load_scenario(SCENARIO, seed=SEED)
        household, pack = _first_departure(world)
        self.assertIsNotNone(household, "За 24 месяца ни один двор не ушёл")
        assert household is not None and pack is not None
        destination = pack.destination_tile_id
        origin = pack.origin_tile_id
        self.assertNotEqual(destination, origin)
        for hid in list(world.tiles[destination].hazard_ids):
            world.hazards[hid].active = False
        world.tiles[destination].max_households = 0  # кап полон: гонка возов

        resolve_migrations(world, pack.eta_date)

        self.assertEqual(pack.status, "arrived", "Теснота не дала ветку rejected")
        self.assertEqual(
            household.current_tile_id,
            origin,
            "При тесноте двор должен физически остаться на origin",
        )
        reports = [
            r
            for r in world.reports
            if r.source == "messenger"
            and r.subject_id == destination
            and r.facts.get("household_id") == household.id
        ]
        self.assertTrue(reports, "Нет messenger-отчёта о тесноте")
        report = reports[-1]
        self.assertEqual(report.facts.get("tile"), destination)
        self.assertIn(household.id, report.content)
        self.assertIn(destination, report.content)
        self.assertLess(abs(world.ledger.delta(world.total_matter())), 1e-6)

    def test_partial_loss_arrival_pins_household_id_tile_and_lost(self) -> None:
        """Ветка частичной потери: `tile` = about, `lost` = число погибших."""
        world = load_scenario(SCENARIO, seed=SEED)
        household, pack = _first_departure(world)
        self.assertIsNotNone(household, "За 24 месяца ни один двор не ушёл")
        assert household is not None and pack is not None
        self.assertGreaterEqual(
            len(pack.member_ids), 2, "Для частичной потери нужно ≥2 человек"
        )
        destination = pack.destination_tile_id
        for hid in list(world.tiles[destination].hazard_ids):
            world.hazards[hid].active = False
        wolves = world.hazards["haz_001"]
        if wolves.id in world.tiles[wolves.tile_id].hazard_ids:
            world.tiles[wolves.tile_id].hazard_ids.remove(wolves.id)
        wolves.tile_id = destination
        wolves.active = True
        wolves.population = 10.0
        wolves.satiety = 0.0
        world.tiles[destination].hazard_ids.append(wolves.id)
        world.rng.hazard = _SeqRng([0.0] + [0.99] * (len(pack.member_ids) - 1))

        resolve_migrations(world, pack.eta_date)

        self.assertEqual(pack.status, "arrived", "Частичная потеря не дошла")
        self.assertEqual(household.current_tile_id, destination)
        reports = [
            r
            for r in world.reports
            if r.source == "messenger"
            and r.subject_id == destination
            and r.facts.get("household_id") == household.id
        ]
        self.assertTrue(reports, "Нет messenger-отчёта о частичной потере")
        report = reports[-1]
        self.assertEqual(report.facts.get("lost"), 1)
        self.assertEqual(report.facts.get("household_id"), household.id)
        self.assertEqual(report.facts.get("tile"), destination)
        self.assertIn(household.id, report.content)
        self.assertIn(destination, report.content)
        self.assertLess(abs(world.ledger.delta(world.total_matter())), 1e-6)


if __name__ == "__main__":
    unittest.main()
