"""Вергельд: без него надел не выдаётся (ADR 0193).

Закон падает без механики: тест сам ставит двору пресет `slave` (`wergeld: false`)
и требует отказа. Проверка не зелёная на пустоте — она сравнивает раба с вольным
на одном и том же вызове, поэтому «запрета нет» и «запрет есть» различимы.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.legal.actions import grant_tenure
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
HILL = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"


class TestWergeldHoldsNoLand(unittest.TestCase):
    def test_canon_declares_wergeld_on_every_status(self) -> None:
        """Канон различает раба и вольного именно вергельдом."""
        world = load_scenario(HILL, seed=1729)
        statuses = world.catalogs.legal_statuses
        self.assertIn("slave", statuses)
        self.assertFalse(statuses["slave"].wergeld, "У раба должен быть вергельд")
        self.assertTrue(statuses["villein"].wergeld, "Виллан вергельд имеет")
        holders = sum(1 for h in statuses.values() if h.wergeld)
        self.assertGreater(holders, 1, "Вергельд не различает никого")

    def test_slave_is_refused_a_holding(self) -> None:
        """ADR 0193: надел — право, за которое отвечают телом, вергельда нет."""
        world = load_scenario(HILL, seed=1729)
        household = world.households["hh_02"]
        household.legal_status_id = "slave"
        with self.assertRaises(PermissionError) as caught:
            grant_tenure(world, household.id, "t_06_01", rent_share=0.1, status_id="villein")
        self.assertIn("вергельд", str(caught.exception))
        self.assertFalse(
            any(right.tile_id == "t_06_01" for right in world.rights.values()),
            "Рабу всё же выдали надел, хотя вызов был отклонён",
        )

    def test_free_holder_still_gets_the_land(self) -> None:
        """Тот же вызов вольному проходит: запрет не вселенский."""
        world = load_scenario(HILL, seed=1729)
        household = world.households["hh_02"]
        household.legal_status_id = "villein"
        right = grant_tenure(
            world, household.id, "t_06_01", rent_share=0.1, status_id="villein"
        )
        self.assertEqual(right.tile_id, "t_06_01")
        self.assertEqual(household.legal_status_id, "villein")


if __name__ == "__main__":
    unittest.main()
