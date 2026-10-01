"""Проверки поведения: голод без топлива не даёт халявной рубки; подача и зарплата
раньше еды; детерминизм.

Модуль назывался `test_adr0105.py`, класс — `TestADR0105`, и оба ссылались на
**несуществующий** ADR 0105: файла с таким номером в реестре нет, номер запрещён
`STATUS.md`. Ссылка на пустоту оставалась в репозитории как образец, хотя проверяемые
свойства вполне живые, поэтому ADR 0181 требует переименовать по свойству, а не по
номеру.

Основание поведения: порядок фаз и правило подачи — `engine/tick.py::PHASES` и
`economy/exchange.py::apply_relief`, подача по недобору как закон — ADR 0158 п. 3.
Номер 0105 в этом файле — **только историческое упоминание**: он отозван, и ссылаться
на него как на основание нельзя (ADR 0181 §3).
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.hexgrid import neighbor_ids
from hillcourt.engine.tick import PHASES, run_month
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"


class TestNoFreeFuelAndReliefFirst(unittest.TestCase):
    """Три свойства: нет холостой рубки, помощь и зарплата раньше еды, детерминизм."""

    def _world_without_fuel(self):
        world = load_scenario(SCENARIO, seed=4242)
        household = world.households["hh_02"]
        world.households = {household.id: household}
        world.rights = {
            rid: right
            for rid, right in world.rights.items()
            if right.holder_household_id != household.id
        }
        home = world.tiles[household.current_tile_id]
        home.terrain = "field"
        for tile_id in neighbor_ids(world, home):
            world.tiles[tile_id].terrain = "field"
        world.get_stock(home.standing_stock_id).amounts["grain"] = 10_000.0
        return world, household

    def test_winter_without_fuel_never_cuts_and_works_own_labor(self) -> None:
        world, household = self._world_without_fuel()
        world.clock.month = 1

        for _ in range(12):
            household.main_action = "cut_wood_if_allowed"
            household.minor_action = "cut_wood_if_allowed"
            run_month(world)
            self.assertNotIn(household.main_action, {"cut_wood_if_allowed", "cut_peat"})
            self.assertNotIn(household.minor_action, {"cut_wood_if_allowed", "cut_peat"})

        fuel_entries = [
            entry
            for entry in world.ledger.entries
            if entry.reason in {"cut_firewood", "cut_wood", "cut_peat"}
        ]
        self.assertEqual(fuel_entries, [])
        self.assertGreater(world.stats.get("own_labor", 0.0), 0.0)

    def test_relief_is_ledgered_before_consumption(self) -> None:
        names = [phase.__name__ for phase in PHASES]
        self.assertLess(names.index("phase_exchange"), names.index("phase_consume"))
        world = load_scenario(SCENARIO, seed=4242)
        household = world.households["hh_06"]
        world.households = {household.id: household}
        root = world.manors["manor_hill"]
        root.household_ids = [household.id]
        root.tile_ids = []
        household.manor_id = root.id
        household.main_action = "request_relief"
        household.minor_action = "idle_repair"
        household.hunger_days = 2
        stock = world.get_stock(household.stock_id)
        stock.amounts.clear()
        world.get_stock(root.stock_id).amounts["grain"] = 10.0
        start = len(world.ledger.entries)

        run_month(world)

        relief = [
            index
            for index, entry in enumerate(world.ledger.entries[start:], start)
            if entry.reason == "relief" and entry.dst_id == stock.id
        ]
        eaten = [
            index
            for index, entry in enumerate(world.ledger.entries[start:], start)
            if entry.reason == "eat" and entry.src_id == stock.id
        ]
        self.assertTrue(relief)
        self.assertTrue(eaten)
        self.assertLess(relief[0], eaten[0])

    def test_hire_wage_is_ledgered_before_consumption(self) -> None:
        world = load_scenario(SCENARIO, seed=4242)
        household = world.households["hh_06"]
        world.households = {household.id: household}
        root = world.manors["manor_hill"]
        root.household_ids = [household.id]
        household.manor_id = root.id
        field = next(
            tile
            for tile in world.tiles.values()
            if tile.terrain == "field" and tile.regime_id == "demesne"
        )
        root.tile_ids = [field.id]
        world.get_stock(field.standing_stock_id).amounts["grain"] = 1_000.0
        world.get_stock(root.stock_id).amounts["grain"] = 10.0
        household.main_action = "hire_out"
        household.minor_action = "idle_repair"
        stock = world.get_stock(household.stock_id)
        stock.amounts.clear()
        start = len(world.ledger.entries)

        run_month(world)

        hired = [
            index
            for index, entry in enumerate(world.ledger.entries[start:], start)
            if entry.reason == "hire" and entry.dst_id == stock.id
        ]
        eaten = [
            index
            for index, entry in enumerate(world.ledger.entries[start:], start)
            if entry.reason == "eat" and entry.src_id == stock.id
        ]
        self.assertTrue(hired)
        self.assertTrue(eaten)
        self.assertLess(hired[0], eaten[0])

    def test_same_seed_has_same_state_hash(self) -> None:
        def replay() -> str:
            world = load_scenario(SCENARIO, seed=4242)
            for _ in range(12):
                run_month(world)
            return world.state_hash()

        self.assertEqual(replay(), replay())


if __name__ == "__main__":
    unittest.main()
