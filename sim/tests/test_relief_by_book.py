"""Доводка G2: `request_relief` решается по книге двора, не по замку.

`choose_actions` просит подмогу только если зерно есть в амбаре СВОЕЙ книги
(`manor_of_household` → `manor_stock`): корневой двор читает замок, двор тэна —
`manor:<id>`, соляной держатель (`manor_id None`, вне книги) не просит вовсе.
Материя — перевод, дельта остаётся нулевой.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy import decisions, exchange
from hillcourt.engine.manor import grant_thegn
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
CASTLE = "settlement:hill_court"
WASTE = "sink:waste"
THEGN_PERSON = "hh_retinue_p1"
EPSILON = 1e-9


class TestReliefByBook(unittest.TestCase):
    """Подмога просится по своему столу и идёт из него же."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO)

    def _set_grain(self, stock, target: float) -> None:
        """Выставить зерно стока, не двигая баланс (внешний приход или отход)."""
        current = stock.amounts.get("grain", 0.0)
        change = target - current
        if change > EPSILON:
            self.world.ledger.external_in(
                stock, "grain", change, "test_setup", None, self.world.clock.date
            )
        elif change < -EPSILON:
            self.world.ledger.transfer(
                stock, self.world.get_stock(WASTE), "grain", -change, "test_setup",
                self.world.clock.date,
            )
        self.assertAlmostEqual(stock.amounts.get("grain", 0.0), target, places=6)

    def _quiet_actions(self) -> None:
        for household in self.world.households.values():
            household.main_action, household.minor_action = "idle_repair", "idle_repair"

    def test_thegn_requests_relief_by_own_barn(self) -> None:
        world = self.world
        thegn = grant_thegn(world, THEGN_PERSON, ["t_03_01", "t_05_02"], ["hh_02"])
        self.assertIsNotNone(thegn)
        holder = world.households[world.persons[THEGN_PERSON].household_id]
        barn = world.get_stock(thegn.stock_id)
        self._set_grain(barn, 10.0)
        self._set_grain(world.get_stock(CASTLE), 0.0)
        holder.hunger_days = 2

        main, _ = decisions.choose_actions(world, holder)
        self.assertEqual(
            main, "request_relief",
            "Двор тэна не просит подмогу при полном СВОЁМ амбаре и пустом замке",
        )

        self._quiet_actions()
        holder.main_action = main
        n0 = len(world.ledger.entries)
        exchange.apply_relief(world, world.clock.date)
        relief = [
            entry
            for entry in world.ledger.entries[n0:]
            if entry.reason == "relief" and entry.dst_id == holder.stock_id
        ]
        self.assertTrue(relief, "Двор тэна не получил relief")
        self.assertTrue(
            all(entry.src_id == thegn.stock_id for entry in relief),
            "Relief двору тэна пришёл не из manor:<id>",
        )
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6,
            msg="Подмога создала или сожгла материю",
        )

    def test_salt_household_does_not_request_relief(self) -> None:
        world = self.world
        salt = world.households["hh_salt_01"]
        self.assertIsNone(salt.manor_id, "Соляной двор не должен числиться в книге")
        self._set_grain(world.get_stock(CASTLE), 999.0)
        salt.hunger_days = 2

        main, _ = decisions.choose_actions(world, salt)
        self.assertNotEqual(
            main, "request_relief",
            "Соляной двор без книги просит подмогу у замка",
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_root_household_requests_relief_from_castle(self) -> None:
        world = self.world
        root_hh = world.households["hh_01"]
        self.assertEqual(root_hh.manor_id, "manor_hill")
        self._set_grain(world.get_stock(CASTLE), 10.0)
        root_hh.hunger_days = 2

        main, _ = decisions.choose_actions(world, root_hh)
        self.assertEqual(
            main, "request_relief",
            "Корневой двор не просит подмогу при зерне в замке",
        )

        self._quiet_actions()
        root_hh.main_action = main
        n0 = len(world.ledger.entries)
        exchange.apply_relief(world, world.clock.date)
        relief = [
            entry
            for entry in world.ledger.entries[n0:]
            if entry.reason == "relief" and entry.dst_id == root_hh.stock_id
        ]
        self.assertTrue(relief, "Корневой двор не получил relief")
        self.assertTrue(
            all(entry.src_id == CASTLE for entry in relief),
            "Relief корневого двора пришёл не из замка",
        )
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6,
            msg="Подмога создала или сожгла материю",
        )


if __name__ == "__main__":
    unittest.main()
