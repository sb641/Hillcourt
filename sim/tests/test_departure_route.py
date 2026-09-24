"""A3: уход двора — путь через Pack, а не мгновенный телепорт и не delete.

Двор уходит только по прибытии: в тик решения груз переезжает в сток воза
(`Pack kind="household_move"`), `current_tile_id` не меняется. По `eta_date`
опасность клетки назначения может унести людей; уцелевшие возвращают груз в
сток двора, а при полной гибели груз ложится в стоячий сток клетки — материя
не исчезает.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.tick import _start_departure, phase_migrate, run_month
from hillcourt.hazards.travel import resolve_migrations
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
WOLVES_TILE = "t_00_01"


class _ZeroRng:
    """Заглушка потока опасности: любой бросок выпадает в 0 — гибнут все."""

    def random(self) -> float:
        return 0.0

    def choice(self, seq):
        return seq[0]


def _matter_delta(world) -> float:
    return abs(world.ledger.delta(world.total_matter()))


class TestDepartureRoute(unittest.TestCase):
    """Уход идёт маршрутом, а не телепортом; груз не исчезает."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO)

    def _first_departure(self):
        """Прокрутить до первого ухода; вернуть (двор, воз) или (None, None)."""
        world = self.world
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
                return household, pack
        return None, None

    def test_departure_creates_route_and_moves_cargo(self) -> None:
        world = self.world
        household, pack = self._first_departure()
        self.assertIsNotNone(household, "За 24 месяца ни один двор не ушёл")
        self.assertIsNotNone(pack, "Уход не породил Pack household_move")
        self.assertEqual(pack.status, "in_transit")
        self.assertGreaterEqual(len(pack.route), 2, "Маршрут из одной клетки")
        self.assertEqual(pack.route[0], pack.origin_tile_id)
        self.assertEqual(pack.route[-1], pack.destination_tile_id)
        self.assertGreater(pack.cargo.total(), 0.0, "Воз ушёл пустым")
        self.assertAlmostEqual(
            world.get_stock(household.stock_id).total(), 0.0, places=9
        )
        self.assertEqual(household.current_tile_id, pack.origin_tile_id)
        self.assertEqual(household.member_ids, [])
        self.assertEqual(household.intent, "leave")
        self.assertIsNone(household.settlement_id)
        self.assertLess(_matter_delta(world), 1e-6)

    def test_arrival_moves_household_and_returns_cargo(self) -> None:
        world = self.world
        household, pack = self._first_departure()
        self.assertIsNotNone(pack, "Уход не породил Pack household_move")
        destination = pack.destination_tile_id
        for _ in range(4):
            run_month(world)
            if pack.status == "arrived":
                break
        self.assertEqual(pack.status, "arrived", "Уход не дошёл за 4 месяца")
        self.assertEqual(household.current_tile_id, destination)
        self.assertGreater(world.get_stock(household.stock_id).total(), 0.0)
        self.assertTrue(household.member_ids, "Дошедший двор остался без людей")
        for pid in household.member_ids:
            self.assertEqual(world.persons[pid].location_tile_id, destination)
        self.assertLess(_matter_delta(world), 1e-6)

    def test_tied_household_does_not_leave(self) -> None:
        world = self.world
        household = world.households["hh_02"]
        household.hunger_days = 5
        for oid in household.obligation_ids:
            obligation = world.obligations.get(oid)
            if obligation is not None and obligation.due_amount > 0:
                obligation.arrears = 100.0 * obligation.due_amount
        origin = household.current_tile_id
        packs_before = set(world.packs)

        phase_migrate(world)

        self.assertIsNone(household.left_at, "Прикреплённый двор ушёл сам")
        self.assertEqual(household.current_tile_id, origin)
        self.assertEqual(set(world.packs), packs_before)

    def test_lost_migration_scatters_cargo_not_deletes(self) -> None:
        world = self.world
        household = world.households["hh_01"]
        origin = household.current_tile_id
        destination = "t_03_01"
        wolves = world.hazards["haz_001"]
        self.assertEqual(wolves.tile_id, WOLVES_TILE)
        world.tiles[WOLVES_TILE].hazard_ids.remove("haz_001")
        wolves.tile_id = destination
        world.tiles[destination].hazard_ids.append("haz_001")

        cargo_before = world.get_stock(household.stock_id).total()
        self.assertGreater(cargo_before, 0.0)
        dest_stock = world.get_stock(world.tiles[destination].standing_stock_id)
        dest_before = dest_stock.total()

        pack = _start_departure(world, household, destination, world.clock.date)
        self.assertEqual(pack.cargo.total(), cargo_before)
        self.assertEqual(pack.origin_tile_id, origin)
        self.assertLess(_matter_delta(world), 1e-6)

        world.rng.hazard = _ZeroRng()
        members = list(pack.member_ids)
        resolve_migrations(world, pack.eta_date)

        self.assertEqual(pack.status, "lost")
        self.assertEqual(pack.member_ids, [])
        self.assertAlmostEqual(pack.cargo.total(), 0.0, places=9)
        self.assertAlmostEqual(
            dest_stock.total(), dest_before + cargo_before, places=6
        )
        for pid in members:
            self.assertEqual(world.persons[pid].health, 0.0)
        silences = [
            r
            for r in world.reports
            if r.source == "silence" and r.subject_id == destination
        ]
        self.assertTrue(silences, "Полная гибель ухода не породила молчания")
        self.assertLess(_matter_delta(world), 1e-6)


if __name__ == "__main__":
    unittest.main()
