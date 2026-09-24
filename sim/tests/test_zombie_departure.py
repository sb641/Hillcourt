"""G1: зомби-уход — дошедший двор вне книг и не живёт по старой книге.

После прибытия `left_at`-двор снимается со ВСЕХ книг маноров
(`manor.household_ids`, `household.manor_id`), получает мирный
messenger-Report о прибытии и больше не участвует в board/gafol/relief/
барщине/урожае ни по одной книге. Его сток остаётся его собственным,
материя не создаётся. Прикреплённый двор (`can_leave=False`) не уходит.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.tick import phase_migrate, run_month
from hillcourt.runner import run
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SEED = 1729
BOOK_REASONS = {
    "board",
    "gafol",
    "geld",
    "relief",
    "corvee",
    "rent",
    "hire",
    "sow_demesne",
    "boon",
}


def _matter_delta(world) -> float:
    return abs(world.ledger.delta(world.total_matter()))


class TestZombieDeparture(unittest.TestCase):
    """Ушедший двор не остаётся на книге и не пашет/ест по ней."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO, seed=SEED)

    def _run_to_departure(self):
        """Прокрутить до первого ухода; вернуть (двор, воз, клетка до тика)."""
        world = self.world
        for _ in range(24):
            before = {hid: hh.current_tile_id for hid, hh in world.households.items()}
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
                return household, pack, before[hid]
        return None, None, None

    def _finish_migration(self, pack, limit: int = 6):
        """Довести Pack до статуса arrived/lost; вернуть статус."""
        for _ in range(limit):
            run_month(self.world)
            if pack.status in ("arrived", "lost"):
                return pack.status
        return pack.status

    def test_departure_uses_route_not_teleport(self) -> None:
        world = self.world
        household, pack, origin = self._run_to_departure()
        self.assertIsNotNone(household, "За 24 месяца ни один двор не ушёл")
        self.assertIsNotNone(pack, "Уход не породил Pack household_move")
        self.assertEqual(pack.status, "in_transit")
        self.assertEqual(pack.origin_tile_id, origin)
        self.assertEqual(
            household.current_tile_id,
            origin,
            "Клетка сменилась в тик ухода (телепорт)",
        )
        self.assertEqual(pack.route[0], origin)
        self.assertNotEqual(pack.route[-1], origin)
        self.assertGreater(pack.cargo.total(), 0.0, "Воз ушёл пустым")
        self.assertLess(_matter_delta(world), 1e-6)

    def test_arrival_detaches_household_from_all_manors(self) -> None:
        world = self.world
        household, pack, _ = self._run_to_departure()
        self.assertIsNotNone(pack, "Уход не породил Pack household_move")
        self.assertEqual(self._finish_migration(pack), "arrived", "Уход не дошёл")
        destination = pack.destination_tile_id

        self.assertEqual(household.current_tile_id, destination)
        self.assertIsNotNone(household.left_at, "left_at снят у ушедшего двора")
        self.assertIsNone(household.settlement_id)
        self.assertIsNone(household.manor_id, "Ушедший двор остался на книге манора")
        for manor_id in sorted(world.manors):
            self.assertNotIn(
                household.id,
                world.manors[manor_id].household_ids,
                f"Ушедший двор остался в книге '{manor_id}'",
            )

        reports = [
            r
            for r in world.reports
            if r.source == "messenger"
            and r.subject_id == destination
            and household.id in r.content
            and destination in r.content
        ]
        self.assertTrue(reports, "Прибытие не породило messenger-Report")
        self.assertEqual(reports[-1].facts.get("members"), len(household.member_ids))
        self.assertEqual(reports[-1].facts.get("household_id"), household.id)
        self.assertEqual(reports[-1].facts.get("tile"), destination)

        for pid in household.member_ids:
            person = world.persons[pid]
            self.assertGreater(person.health, 0.0, "Человек дошёл мёртвым")
            self.assertEqual(person.location_tile_id, destination)

        self.assertLess(_matter_delta(world), 1e-6)

    def test_arrived_household_has_no_book_entries(self) -> None:
        world = self.world
        household, pack, _ = self._run_to_departure()
        self.assertIsNotNone(pack, "Уход не породил Pack household_move")
        self.assertEqual(self._finish_migration(pack), "arrived", "Уход не дошёл")
        left_at = household.left_at
        stock_id = household.stock_id
        hidden_id = f"household:{household.id}:hidden"

        def involves(entry) -> bool:
            return stock_id in (entry.src_id, entry.dst_id) or hidden_id in (
                entry.src_id,
                entry.dst_id,
            )

        offenders = [
            entry
            for entry in world.ledger.entries
            if entry.date > left_at
            and involves(entry)
            and (entry.reason in BOOK_REASONS or entry.reason.startswith("harvest"))
        ]
        self.assertEqual(
            offenders,
            [],
            "Дошедший двор участвует в переводах по старой книге",
        )
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

    def test_same_seed_same_state_hash(self) -> None:
        first = run(SCENARIO, 18, seed=SEED)
        second = run(SCENARIO, 18, seed=SEED)
        self.assertEqual(
            first.state_hash,
            second.state_hash,
            "Повторный прогон того же сида дал другой state_hash",
        )


if __name__ == "__main__":
    unittest.main()
