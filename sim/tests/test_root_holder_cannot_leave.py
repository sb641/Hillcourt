"""J1: корневой держатель не уходит, пока жив корневой манор (зеркало I3).

Контракт (дыра 3 из ADR 0016):
  - `can_leave` возвращает False двору, в котором числится держатель ЛЮБОГО
    существующего манора: и вложенного (I3), и корневого (эта задача);
  - пресет `holder` сам по себе уход разрешает, поэтому проверка идёт на
    `hh_court`: держит именно корневой Manor, а не ярлык;
  - I3 не сломан: после `grant_thegn` двор держателя тэна прикреплён;
  - свободный не-держатель (`hh_06`) уходит по голоду, виллан (`hh_02`) — нет;
  - 60 мес `v0_two_settlements` seed 42: `hh_court` остаётся едоком замка
    (`board` из замка + `eat` из его стока), материя цела, replay даёт один
    `state_hash`.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.manor import grant_thegn
from hillcourt.engine.tick import phase_migrate, run_month
from hillcourt.legal.regimes import can_leave
from hillcourt.runner import _apply_script_entry
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
TWO_SETTLEMENTS = ROOT / "design" / "scenarios" / "v0_two_settlements.yml"

ROOT_HOUSEHOLD = "hh_court"
PERSON = "hh_retinue_p1"
TILES = ["t_03_01", "t_05_02"]
HOUSEHOLD = "hh_02"
HOLDER_HOUSEHOLD = "hh_retinue"
FREE_HOUSEHOLD = "hh_06"
TIED_HOUSEHOLD = "hh_02"
SEED = 1729
TWO_SETTLEMENTS_SEED = 42
OLD_DEPARTURE = (3, 7)  # Y3-M07: с этого месяца держатель раньше уходил


def _make_hungry(world, household) -> None:
    """Голод + недоимка сверх порога ухода (как test_zombie_departure)."""
    household.hunger_days = 5
    for oid in household.obligation_ids:
        obligation = world.obligations.get(oid)
        if obligation is not None and obligation.due_amount > 0:
            obligation.arrears = 100.0 * obligation.due_amount


class TestRootHolderCannotLeave(unittest.TestCase):
    """Корневой манор держит своего держателя, как фьеф — тэна."""

    def test_root_holder_can_leave_false_and_migrate_noop(self) -> None:
        """(a) hh_court: пресет уход разрешает, корневой манор — нет."""
        world = load_scenario(SCENARIO, seed=SEED)
        holder = world.households[ROOT_HOUSEHOLD]
        self.assertTrue(
            world.catalogs.legal_statuses["holder"].can_leave,
            "Пресет holder не разрешает уход — тест проверял бы не манор",
        )
        self.assertFalse(can_leave(world, holder), "Корневой держатель может уйти")

        _make_hungry(world, holder)
        origin = holder.current_tile_id
        packs_before = set(world.packs)

        phase_migrate(world)

        self.assertIsNone(holder.left_at, "Корневой держатель ушёл")
        self.assertEqual(holder.current_tile_id, origin)
        self.assertEqual(set(world.packs), packs_before, "Появился новый Pack")

    def test_nested_fief_holder_still_cannot_leave(self) -> None:
        """(b) I3 не сломан: hh_retinue после grant_thegn остаётся прикреплён."""
        world = load_scenario(SCENARIO, seed=SEED)
        grant_thegn(world, PERSON, list(TILES), [HOUSEHOLD])
        holder = world.households[HOLDER_HOUSEHOLD]
        self.assertFalse(can_leave(world, holder), "Держатель тэна может уйти")

        _make_hungry(world, holder)
        origin = holder.current_tile_id
        packs_before = set(world.packs)

        phase_migrate(world)

        self.assertIsNone(holder.left_at, "Держатель вложенного манора ушёл")
        self.assertEqual(holder.current_tile_id, origin)
        self.assertEqual(set(world.packs), packs_before)

    def test_free_non_holder_still_leaves(self) -> None:
        """(c) Контроль: свободный не-держатель hh_06 уходит по голоду."""
        world = load_scenario(SCENARIO, seed=SEED)
        household = world.households[FREE_HOUSEHOLD]
        self.assertTrue(can_leave(world, household), "Свободному двору закрыт уход")

        _make_hungry(world, household)
        phase_migrate(world)

        self.assertIsNotNone(household.left_at, "Свободный двор не ушёл")

    def test_tied_household_still_cannot_leave(self) -> None:
        """(d) Виллан hh_02 прикреплён своим пресетом, как и раньше."""
        world = load_scenario(SCENARIO, seed=SEED)
        household = world.households[TIED_HOUSEHOLD]
        self.assertFalse(can_leave(world, household))

        _make_hungry(world, household)
        phase_migrate(world)

        self.assertIsNone(household.left_at, "Виллан ушёл")

    def _run_two_settlements(self):
        """60 мес v0_two_settlements seed 42 со script (порядок как в runner)."""
        world = load_scenario(TWO_SETTLEMENTS, seed=TWO_SETTLEMENTS_SEED)
        script = list(world.script)
        for month_index in range(1, 61):
            for entry in script:
                if int(entry.get("at_month", 0)) == month_index:
                    _apply_script_entry(world, entry)
            run_month(world)
        return world

    def test_two_settlements_root_holder_stays_fed_and_replays(self) -> None:
        """(e) 60 мес: держатель остаётся едоком замка, replay сходится."""
        first = self._run_two_settlements()
        second = self._run_two_settlements()
        self.assertEqual(
            first.state_hash(),
            second.state_hash(),
            "state_hash не воспроизводится",
        )

        holder = first.households[ROOT_HOUSEHOLD]
        self.assertIsNone(holder.left_at, "Корневой держатель ушёл за 60 мес")
        self.assertLess(
            abs(first.ledger.delta(first.total_matter())),
            1e-6,
            "Материя не сохранилась",
        )

        def from_old_departure(entry) -> bool:
            return (entry.date.year, entry.date.month) >= OLD_DEPARTURE

        board = [
            entry
            for entry in first.ledger.entries
            if entry.reason == "board"
            and entry.dst_id == holder.stock_id
            and from_old_departure(entry)
        ]
        eat = [
            entry
            for entry in first.ledger.entries
            if entry.reason == "eat"
            and entry.src_id == holder.stock_id
            and from_old_departure(entry)
        ]
        self.assertTrue(board, "Замок не кормил оставшегося корневого держателя")
        self.assertTrue(eat, "Оставшийся корневой держатель не ел")


if __name__ == "__main__":
    unittest.main()
