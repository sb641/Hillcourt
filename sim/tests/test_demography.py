"""Демография: сытый двор растит семью, дети становятся руками.

Проверки (задача Economist):
  1. излишек еды несколько месяцев подряд → рождение ребёнка;
  2. голод → родов нет;
  3. детерминизм: один seed — одни роды;
  4. недоросль входит в тягло и даёт руки;
  5. материя сходится (люди — не материя, Ledger не трогаем).
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy.demography import demography_month
from hillcourt.economy.needs import member_counts
from hillcourt.engine.tick import run_month
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"


class TestDemography(unittest.TestCase):
    """Больше детей = больше рук: роды от излишка, руки от недорослей."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO)

    def _isolate(self, hid: str):
        household = self.world.households[hid]
        self.world.households = {hid: household}
        return household

    def test_surplus_bears_child(self) -> None:
        household = self._isolate("hh_court")
        stock = self.world.get_stock(household.stock_id)
        stock.amounts["grain"] = 200.0
        before = len(household.member_ids)
        for _ in range(9):
            demography_month(self.world, self.world.clock.date)
        self.assertGreater(len(household.member_ids), before, "Сытый двор не родил")
        newborn = self.world.persons[household.member_ids[-1]]
        self.assertEqual(newborn.age_class, "child")
        self.assertEqual(newborn.household_id, household.id)

    def test_hunger_bears_nothing(self) -> None:
        household = self._isolate("hh_08")
        stock = self.world.get_stock(household.stock_id)
        for good in ("grain", "flour", "meat"):
            stock.amounts[good] = 0.0
        before = len(household.member_ids)
        for _ in range(6):
            demography_month(self.world, self.world.clock.date)
        self.assertEqual(len(household.member_ids), before, "Голодный двор родил")

    def test_births_are_deterministic(self) -> None:
        first = load_scenario(SCENARIO)
        second = load_scenario(SCENARIO)
        for _ in range(12):
            run_month(first)
            run_month(second)
        self.assertEqual(first.state_hash(), second.state_hash())
        self.assertEqual(
            sorted(first.persons), sorted(second.persons), "Роды разошлись по сиду"
        )

    def test_mortality_removes_mouths_without_matter(self) -> None:
        household = self._isolate("hh_01")
        before = len(household.member_ids)
        self.assertGreater(before, 0)
        self.world.needs.death_adult_per_month = 1.0
        self.world.needs.death_child_per_month = 1.0
        self.world.needs.death_elder_per_month = 1.0
        before_total = self.world.total_matter()
        demography_month(self.world, self.world.clock.date)
        self.assertLess(len(household.member_ids), before, "Никто не умер")
        self.assertAlmostEqual(
            self.world.total_matter(), before_total, places=6,
            msg="Смерть создала/уничтожила материю",
        )

    def test_matured_child_gives_hands(self) -> None:
        household = self._isolate("hh_01")
        adults_before, _, _ = member_counts(self.world, household)
        child_pid = next(
            pid
            for pid in household.member_ids
            if self.world.persons[pid].age_class == "child"
        )
        child = self.world.persons[child_pid]
        child.age_months = self.world.needs.birth_maturity_months - 1
        demography_month(self.world, self.world.clock.date)
        self.assertEqual(child.age_class, "adult", "Недоросль не вошёл в тягло")
        adults_after, _, _ = member_counts(self.world, household)
        self.assertEqual(adults_after, adults_before + 1)

    def test_matter_still_zero(self) -> None:
        for _ in range(36):
            run_month(self.world)
        self.assertAlmostEqual(
            self.world.ledger.delta(self.world.total_matter()), 0.0, places=6
        )


if __name__ == "__main__":
    unittest.main()
