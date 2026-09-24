"""Стройка пути: дорога/брод/мост как проект барщины, а не рисунок карты.

Зонды приёмки зоны:
  до/после работы дни пути обоза по клетке разные (профиль `caravan`);
  работа без рук не завершается;
  пока проект не завершён, cost/tag не новые;
  действие пишется в `player_actions`;
  материя сходится; соляной маршрут жив.
Чужое не тронуто: служба тэна, скот, кап пяти дворов, yield.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.path import find_path
from hillcourt.engine.roadworks import (
    MATERIAL_AMOUNT,
    MATERIAL_GOOD,
    MONTHLY_LABOR_CAP,
    REQUIRED_LABOR_DAYS,
    advance_roadworks,
    start_work,
)
from hillcourt.engine.terrain import entry_cost
from hillcourt.engine.tile_view import TILE_MAX_HOUSEHOLDS
from hillcourt.engine.tick import run_month
from hillcourt.runner import ACTION_HANDLERS
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"

FOREST_TILE = "t_00_00"
FIELD_TILE = "t_05_02"


def _with_logs(world, amount: float = 20.0):
    """Положить брёвна в амбар корня через внешний приход (не из воздуха).

    Прямая подкрутка `amounts` ломала бы дельту материи (И-1): приход идёт
    через `Ledger.external_in`, поэтому `delta` остаётся нулевой, а материал
    честно появляется по правилу `test_seed`.
    """
    castle = world.get_stock("settlement:hill_court")
    world.ledger.external_in(
        castle, MATERIAL_GOOD, amount, "test_seed", "test_seed", world.clock.date
    )
    return castle


class TestStartWork(unittest.TestCase):
    """Действие начать работу: лог, материал, старые tag до завершения."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO)

    def test_work_road_logs_and_keeps_old_tag(self) -> None:
        world = self.world
        before = entry_cost("caravan", "forest", False, False, False)
        _with_logs(world)
        work = start_work(world, FOREST_TILE, "road")
        self.assertEqual(work["kind"], "road")
        self.assertEqual(world.tiles[FOREST_TILE].road, False)
        after = entry_cost(
            "caravan",
            world.tiles[FOREST_TILE].terrain,
            world.tiles[FOREST_TILE].road,
            False,
            False,
        )
        self.assertAlmostEqual(after, before, places=6)
        records = [r for r in world.player_actions if r.get("action") == "work_road"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["tile"], FOREST_TILE)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_runner_knows_work_actions(self) -> None:
        self.assertIn("work_road", ACTION_HANDLERS)
        self.assertIn("work_ford", ACTION_HANDLERS)
        self.assertIn("work_bridge", ACTION_HANDLERS)

    def test_road_on_water_refused(self) -> None:
        world = self.world
        world.tiles[FIELD_TILE].terrain = "water"
        _with_logs(world)
        with self.assertRaises(ValueError):
            start_work(world, FIELD_TILE, "road")

    def test_ford_off_water_refused(self) -> None:
        world = self.world
        _with_logs(world)
        with self.assertRaises(ValueError):
            start_work(world, FIELD_TILE, "ford")

    def test_no_material_no_work(self) -> None:
        world = self.world
        castle = world.get_stock("settlement:hill_court")
        castle.amounts.pop(MATERIAL_GOOD, None)
        with self.assertRaises(ValueError):
            start_work(world, FOREST_TILE, "road")
        self.assertNotIn(FOREST_TILE, getattr(world, "roadworks", {}))

    def test_thegn_tile_needs_revoke(self) -> None:
        from hillcourt.engine.manor import grant_thegn

        world = self.world
        _with_logs(world)
        person = next(
            pid
            for pid in world.households["hh_05"].member_ids
            if world.persons[pid].age_class == "adult"
        )
        manor = grant_thegn(world, person, [FIELD_TILE], ["hh_05"])
        self.assertIsNotNone(manor)
        with self.assertRaises(PermissionError):
            start_work(world, FIELD_TILE, "road")


class TestCompletionCheapens(unittest.TestCase):
    """После завершения обоз по клетке идёт дешевле (профиль caravan)."""

    def test_road_completion_cheapens_caravan(self) -> None:
        world = load_scenario(SCENARIO)
        _with_logs(world)
        tile = world.tiles[FOREST_TILE]
        self.assertEqual(tile.terrain, "forest")
        before = entry_cost("caravan", "forest", False, False, False)
        self.assertAlmostEqual(before, 8.0, places=6)
        start_work(world, FOREST_TILE, "road")
        for _ in range(6):
            run_month(world)
            if world.tiles[FOREST_TILE].road:
                break
        self.assertTrue(world.tiles[FOREST_TILE].road)
        after = entry_cost(
            "caravan", "forest", True, False, False
        )
        self.assertLess(after, before)
        self.assertAlmostEqual(after, 0.8, places=6)
        done = [r for r in world.player_actions if r.get("action") == "work_done"]
        self.assertTrue(any(r.get("tile") == FOREST_TILE for r in done))
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_path_days_drop_after_road(self) -> None:
        world = load_scenario(SCENARIO)
        _with_logs(world)
        world.tiles["t_05_02"].terrain = "forest"
        origin, dest = "t_03_02", "t_07_02"
        before = find_path(world, origin, dest, "caravan")
        self.assertIsNotNone(before)
        start_work(world, "t_05_02", "road")
        for _ in range(6):
            run_month(world)
            if world.tiles["t_05_02"].road:
                break
        self.assertTrue(world.tiles["t_05_02"].road)
        after = find_path(world, origin, dest, "caravan")
        self.assertIsNotNone(after)
        self.assertLess(after[1], before[1])

    def test_ford_opens_water_for_land(self) -> None:
        world = load_scenario(SCENARIO)
        _with_logs(world)
        target = "t_05_02"
        for y in range(7):
            world.tiles[f"t_05_{y:02d}"].terrain = "water"
        self.assertIsNone(find_path(world, "t_03_02", "t_07_02", "caravan"))
        start_work(world, target, "ford")
        for _ in range(6):
            run_month(world)
            if world.tiles[target].ford:
                break
        self.assertTrue(world.tiles[target].ford)
        found = find_path(world, "t_03_02", "t_07_02", "caravan")
        self.assertIsNotNone(found)
        self.assertIn(target, found[0])
        self.assertLess(
            entry_cost("caravan", "water", False, True, False), float("inf")
        )

    def test_bridge_sets_bridge_tag(self) -> None:
        world = load_scenario(SCENARIO)
        _with_logs(world)
        target = "t_04_02"
        world.tiles[target].terrain = "water"
        start_work(world, target, "bridge")
        for _ in range(8):
            run_month(world)
            if world.tiles[target].bridge:
                break
        self.assertTrue(world.tiles[target].bridge)
        self.assertFalse(world.tiles[target].ford)
        self.assertLess(
            entry_cost("foot", "water", False, False, True), float("inf")
        )


class TestNoHandsNoCompletion(unittest.TestCase):
    """Работа без рук не завершается."""

    def test_no_labor_no_progress(self) -> None:
        world = load_scenario(SCENARIO)
        _with_logs(world)
        start_work(world, FOREST_TILE, "road")
        for household in world.households.values():
            household.labor_days = 0.0
        progressed = advance_roadworks(world, world.clock.date)
        self.assertAlmostEqual(progressed.get(FOREST_TILE, 0.0), 0.0, places=6)
        self.assertFalse(world.tiles[FOREST_TILE].road)
        self.assertEqual(world.roadworks[FOREST_TILE]["status"], "active")

    def test_field_loses_hands_while_building(self) -> None:
        world = load_scenario(SCENARIO)
        _with_logs(world)
        start_work(world, FOREST_TILE, "road")
        # Руки стройки — из остатка после барщины: прямой добор режет labor_days.
        world.households["hh_02"].labor_days = 20.0
        before = sum(
            hh.labor_days for hh in world.households.values() if hh.left_at is None
        )
        progressed = advance_roadworks(world, world.clock.date)
        after = sum(
            hh.labor_days for hh in world.households.values() if hh.left_at is None
        )
        taken = before - after
        self.assertGreater(taken, 0.0)
        self.assertAlmostEqual(progressed.get(FOREST_TILE, 0.0), taken, places=6)
        self.assertGreater(float(world.stats.get("roadwork_days", 0.0)), 0.0)


class TestMatterAndSaltAlive(unittest.TestCase):
    """Материя сходится; соляной маршрут жив при стройке."""

    def test_matter_zero_with_active_work(self) -> None:
        world = load_scenario(SCENARIO)
        _with_logs(world)
        start_work(world, FOREST_TILE, "road")
        for _ in range(6):
            run_month(world)
            self.assertAlmostEqual(
                world.ledger.delta(world.total_matter()), 0.0, places=6
            )

    def test_salt_route_alive_during_works(self) -> None:
        world = load_scenario(SCENARIO)
        _with_logs(world, 40.0)
        start_work(world, FOREST_TILE, "road")
        for _ in range(12):
            run_month(world)
        kinds = {e.reason for e in world.ledger.entries if e.good == "salt"}
        self.assertIn("caravan_load", kinds)
        self.assertIn("caravan_unload", kinds)
        self.assertFalse(
            any(e.kind == "external_in" and e.good == "salt" for e in world.ledger.entries),
            "Соль из ничего при стройке",
        )

    def test_tile_view_cap_untouched(self) -> None:
        self.assertEqual(TILE_MAX_HOUSEHOLDS, 5)

    def test_material_amounts_declared(self) -> None:
        for kind in ("road", "ford", "bridge"):
            self.assertGreater(float(MATERIAL_AMOUNT[kind]), 0.0)


class TestMonthlyLaborCap(unittest.TestCase):
    """Допуск рук №1 (ADR 0067): кап 40 дн/мес, клетка за ≤1 месяц.

    Дни и материалы клетки (ADR 0039) не тронуты: 20/14/28 и log 3/2/4 стоят.
    """

    def test_cap_is_forty_and_cell_costs_untouched(self) -> None:
        self.assertEqual(MONTHLY_LABOR_CAP, 40.0)
        self.assertEqual(
            REQUIRED_LABOR_DAYS,
            {"road": 20.0, "ford": 14.0, "bridge": 28.0},
            "Цены клетки поехали (ADR 0039)",
        )
        self.assertEqual(
            MATERIAL_AMOUNT,
            {"road": 3.0, "ford": 2.0, "bridge": 4.0},
            "Материалы клетки поехали (ADR 0039)",
        )

    def test_each_cell_built_within_one_month(self) -> None:
        for kind, tile_id, terrain in (
            ("road", "t_00_00", None),
            ("ford", "t_02_04", "water"),
            ("bridge", "t_03_04", "water"),
        ):
            world = load_scenario(SCENARIO)
            _with_logs(world, 8.0)
            if terrain is not None:
                world.tiles[tile_id].terrain = terrain
            start_work(world, tile_id, kind)
            for _ in range(12):
                run_month(world)
                work = world.roadworks[tile_id]
                if work["status"] == "done":
                    break
            self.assertEqual(world.roadworks[tile_id]["status"], "done", kind)
            months = world.clock.month - 1
            self.assertLessEqual(months, 1, f"{kind}: клетка встала за {months} мес")
            self.assertAlmostEqual(
                world.roadworks[tile_id]["done_days"],
                REQUIRED_LABOR_DAYS[kind],
                places=6,
            )
            self.assertAlmostEqual(
                world.ledger.delta(world.total_matter()), 0.0, places=6
            )

    def test_cap_limits_budget_per_project(self) -> None:
        world = load_scenario(SCENARIO)
        _with_logs(world)
        start_work(world, FOREST_TILE, "road")
        progressed = advance_roadworks(world, world.clock.date)
        self.assertLessEqual(
            progressed.get(FOREST_TILE, 0.0), MONTHLY_LABOR_CAP, "Кап не держит"
        )


if __name__ == "__main__":
    unittest.main()
