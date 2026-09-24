"""I3: фьеф держит человека — держатель вложенного манора не уходит сам.

Контракт (дыра B/G2 из ADR 0013/0014):
  - `can_leave` возвращает False двору, в котором числится держатель
    существующего вложенного манора (`parent_manor_id is not None`);
  - корневой манор игрока никого не держит: обычным дворам уход не закрыт;
  - `revoke_thegn` возвращает держателя в корень — право уйти снова True;
  - длинный горизонт: оба тэна v0_shire остаются в своих книгах, едят из
    своего амбара и хотя бы раз после второго пожалования mustered.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.manor import (
    grant_thegn,
    nested_manors,
    revoke_thegn,
    root_manor,
)
from hillcourt.engine.tick import phase_migrate, run_month
from hillcourt.legal.regimes import can_leave
from hillcourt.runner import _apply_script_entry
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SHIRE = ROOT / "design" / "scenarios" / "v0_shire.yml"

PERSON = "hh_retinue_p1"
TILES = ["t_03_01", "t_05_02"]
HOUSEHOLD = "hh_02"
HOLDER_HOUSEHOLD = "hh_retinue"
FREE_HOUSEHOLD = "hh_06"
SEED = 1729


def _granted_world():
    """Мир v0_hill_and_salt с одним пожалованием тэна (M6-подобное)."""
    world = load_scenario(SCENARIO, seed=SEED)
    manor = grant_thegn(world, PERSON, list(TILES), [HOUSEHOLD])
    return world, manor


def _make_hungry(world, household) -> None:
    """Голод + недоимка сверх порога ухода (как test_zombie_departure)."""
    household.hunger_days = 5
    for oid in household.obligation_ids:
        obligation = world.obligations.get(oid)
        if obligation is not None and obligation.due_amount > 0:
            obligation.arrears = 100.0 * obligation.due_amount


class TestFiefHolderCannotLeave(unittest.TestCase):
    """Держатель вложенного манора прикреплён к фьефу до revoke_thegn."""

    def test_holder_can_leave_false_and_migrate_noop(self) -> None:
        """(a) Держатель не уходит: can_leave False, phase_migrate — no-op."""
        world, manor = _granted_world()
        holder = world.households[HOLDER_HOUSEHOLD]
        self.assertEqual(holder.manor_id, manor.id)
        self.assertIn(HOLDER_HOUSEHOLD, manor.household_ids)
        self.assertFalse(can_leave(world, holder), "Держатель тэна может уйти")

        _make_hungry(world, holder)
        origin = holder.current_tile_id
        packs_before = set(world.packs)

        phase_migrate(world)

        self.assertIsNone(holder.left_at, "Держатель вложенного манора ушёл")
        self.assertEqual(holder.current_tile_id, origin)
        self.assertEqual(set(world.packs), packs_before, "Появился новый Pack")

    def test_free_household_still_leaves(self) -> None:
        """(b) Контроль: свободный двор с теми же условиями уходит."""
        world, _ = _granted_world()
        household = world.households[FREE_HOUSEHOLD]
        self.assertTrue(can_leave(world, household), "Свободному двору закрыт уход")

        _make_hungry(world, household)
        phase_migrate(world)

        self.assertIsNotNone(household.left_at, "Свободный двор не ушёл")
        self.assertIsNone(world.households[HOLDER_HOUSEHOLD].left_at)

    def test_revoke_restores_right_to_leave(self) -> None:
        """(c) revoke_thegn возвращает держателя в корень — can_leave снова True."""
        world, manor = _granted_world()
        holder = world.households[HOLDER_HOUSEHOLD]
        self.assertFalse(can_leave(world, holder))

        self.assertTrue(revoke_thegn(world, manor.id))
        root = root_manor(world)
        self.assertEqual(holder.manor_id, root.id)
        self.assertIn(HOLDER_HOUSEHOLD, root.household_ids)
        self.assertTrue(can_leave(world, holder), "Право уйти не вернулось")

    def test_shire_holders_stay_fed_and_mustered(self) -> None:
        """(d) v0_shire 60 мес: оба тэна в своих книгах, едят из амбара."""
        world = load_scenario(SHIRE, seed=SEED)
        script = list(world.script)
        fed_after_grant2: list[str] = []
        for month_index in range(1, 61):
            for entry in script:
                if int(entry.get("at_month", 0)) == month_index:
                    _apply_script_entry(world, entry)
            run_month(world)
            for manor in nested_manors(world):
                person = world.persons[manor.holder_person_id]
                holder = world.households[person.household_id]
                if month_index > 18 and holder.hunger_days == 0 and manor.mustered:
                    fed_after_grant2.append(manor.id)

        self.assertEqual(len(nested_manors(world)), 2, "Не два тэна на шире")
        for manor in nested_manors(world):
            person = world.persons[manor.holder_person_id]
            holder = world.households[person.household_id]
            self.assertIsNone(holder.left_at, f"Держатель '{manor.id}' ушёл")
            self.assertEqual(holder.manor_id, manor.id)
            self.assertIn(person.household_id, manor.household_ids)
            board_from_barn = [
                entry
                for entry in world.ledger.entries
                if entry.reason == "board" and entry.src_id == manor.stock_id
            ]
            self.assertTrue(
                board_from_barn,
                f"У держателя '{manor.id}' не было board из своего амбара",
            )

        self.assertTrue(
            fed_after_grant2,
            "Ни один тэн не был сыт и mustered после второго пожалования",
        )
        self.assertLess(world.ledger.delta(world.total_matter()), 1e-6)


if __name__ == "__main__":
    unittest.main()
