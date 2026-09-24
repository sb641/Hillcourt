"""Местность и путь: профили, обход чащи, брод, туман, приказ send_march.

Коридор зонда (v0_hill_and_salt, ряд y=2 — поля):
  origin t_03_02 → dest t_07_02; прямая идёт через t_05_02 (в тесте — чаща),
  обход — рядом y=1 (поле/выпас/пустошь, на 2 клетки длиннее).
Живые обозы (`economy/caravan.py`, манхэттен) не тронуты: зонды бьют в
`engine/path.py` профилем `caravan` и в приказ `engine/march.py`.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine import march as march_module
from hillcourt.engine.path import (
    find_path,
    known_tiles,
    remembered_road_tiles,
    travel_months,
)
from hillcourt.engine.terrain import (
    MOVEMENT_PROFILES,
    TERRAINS,
    entry_cost,
    profile_ids,
)
from hillcourt.news.propagation import make_report
from hillcourt.runner import ACTION_HANDLERS
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"

ORIGIN = "t_03_02"
DEST = "t_07_02"
FOREST_TILE = "t_05_02"
BYPASS_TILES = ("t_03_01", "t_04_01", "t_05_01", "t_06_01", "t_07_01")
DIRECT_TILES = ("t_04_02", "t_05_02", "t_06_02")


def _corridor(world):
    """Превратить коридор в зонд: прямая через чащу, рядом — поля."""
    world.tiles[FOREST_TILE].terrain = "forest"
    return world


def _report_about(world, tile_id: str) -> None:
    """Положить игроку доставленное известие о клетке (задержка 0)."""
    make_report(
        world,
        source="messenger",
        subject_kind="tile",
        subject_id=tile_id,
        content=f"Весть о клетке '{tile_id}'",
        facts={},
        event_date=world.clock.date,
        delay_months=0,
        confidence=0.5,
    )


class TestTerrainProfiles(unittest.TestCase):
    """Террейн — закрытый список; профилей 4, цены разные на тех же клетках."""

    def test_terrains_are_closed_list(self) -> None:
        self.assertEqual(len(TERRAINS), 9)
        self.assertIn("forest", TERRAINS)
        self.assertNotIn("steppe", TERRAINS)
        with self.assertRaises(ValueError):
            entry_cost("foot", "steppe", False, False, False)

    def test_eight_profiles_with_different_costs(self) -> None:
        self.assertEqual(
            profile_ids(),
            [
                "arms",
                "caravan",
                "foot",
                "hunters",
                "mounted",
                "rider",
                "water_boat",
                "water_raft",
            ],
        )
        forest = {
            pid: entry_cost(pid, "forest", False, False, False) for pid in profile_ids()
        }
        self.assertAlmostEqual(forest["caravan"], 8.0)
        self.assertAlmostEqual(forest["hunters"], 1.5)
        self.assertGreater(forest["caravan"], forest["foot"])
        self.assertGreater(forest["foot"], forest["hunters"])
        # Река — дорога с другим судном: по чаще плот не идёт, по воде — дёшево.
        self.assertAlmostEqual(forest["water_raft"], 10.0)
        self.assertAlmostEqual(forest["water_boat"], 10.0)
        self.assertAlmostEqual(
            entry_cost("water_raft", "water", False, False, False), 1.0
        )
        self.assertAlmostEqual(
            entry_cost("water_boat", "water", False, False, False), 0.8
        )
        marsh = {
            pid: entry_cost(pid, "marsh", False, False, False) for pid in profile_ids()
        }
        self.assertAlmostEqual(marsh["mounted"], 8.0)
        self.assertGreater(marsh["mounted"], marsh["foot"])

    def test_water_needs_ford_or_bridge(self) -> None:
        for pid in ("caravan", "foot", "hunters", "mounted"):
            cost = entry_cost(pid, "water", False, False, False)
            self.assertEqual(cost, float("inf"), f"Профиль {pid} плывёт без брода")
            self.assertLess(entry_cost(pid, "water", False, True, False), float("inf"))
            self.assertLess(entry_cost(pid, "water", False, False, True), float("inf"))
        # Речные профили идут рекой без брода (судно), брод им не нужен.
        for pid in ("water_raft", "water_boat"):
            self.assertLess(
                entry_cost(pid, "water", False, False, False), float("inf")
            )

    def test_road_cheapens_entry(self) -> None:
        plain = entry_cost("caravan", "forest", False, False, False)
        road = entry_cost("caravan", "forest", True, False, False)
        self.assertLess(road, plain)
        self.assertAlmostEqual(road, MOVEMENT_PROFILES["caravan"]["road_cost"])


class TestPathfinder(unittest.TestCase):
    """Маршрут — список клеток по стоимости, не прямая."""

    def setUp(self) -> None:
        self.world = _corridor(load_scenario(SCENARIO))

    def test_caravan_bypasses_forest(self) -> None:
        found = find_path(self.world, ORIGIN, DEST, "caravan")
        self.assertIsNotNone(found, "Обоз не нашёл обхода чащи")
        route, hours = found
        self.assertNotIn(FOREST_TILE, route, "Обоз полез в чащу при обходе")
        self.assertEqual(route[0], ORIGIN)
        self.assertEqual(route[-1], DEST)
        self.assertGreater(len(route), len(DIRECT_TILES) + 1, "Маршрут — прямая")
        # Часы (ADR 0071): гекс-коридор обоза — 5 входов × 0.85 ч/поле.
        self.assertAlmostEqual(hours, 4.25, places=6)
        again = find_path(self.world, ORIGIN, DEST, "caravan")
        self.assertEqual(again, found, "Маршрут не детерминирован")

    def test_hunters_take_forest_when_detour_longer(self) -> None:
        found = find_path(self.world, ORIGIN, DEST, "hunters")
        self.assertIsNotNone(found)
        route, hours = found
        self.assertIn(FOREST_TILE, route, "Охотники обошли чащу, хотя обход длиннее")
        # Охотник: 3×1.0 (поля) + 1.5 (чаща) по 0.65 ч = 2.925 ч.
        self.assertAlmostEqual(hours, 2.925, places=6)

    def test_river_without_ford_cuts_path(self) -> None:
        world = self.world
        for y in range(7):
            world.tiles[f"t_05_{y:02d}"].terrain = "water"
        self.assertIsNone(find_path(world, ORIGIN, DEST, "foot"))
        self.assertIsNone(find_path(world, ORIGIN, DEST, "caravan"))

    def test_ford_tile_carries_path_over_river(self) -> None:
        world = self.world
        for y in range(7):
            world.tiles[f"t_05_{y:02d}"].terrain = "water"
        world.tiles["t_05_02"].ford = True
        found = find_path(world, ORIGIN, DEST, "foot")
        self.assertIsNotNone(found, "Брод не пропустил пеших через реку")
        self.assertIn("t_05_02", found[0])

    def test_unknown_terrain_raises(self) -> None:
        self.world.tiles["t_04_02"].terrain = "steppe"
        with self.assertRaises(ValueError):
            find_path(self.world, ORIGIN, DEST, "foot")


class TestKnowledgeLimitsPath(unittest.TestCase):
    """Игрок не строит лучший путь по невидимой земле."""

    def setUp(self) -> None:
        self.world = _corridor(load_scenario(SCENARIO))

    def test_known_path_ignores_unknown_bypass(self) -> None:
        world = self.world
        for tile_id in (*DIRECT_TILES, DEST):
            _report_about(world, tile_id)
        known = known_tiles(world)
        self.assertIn(FOREST_TILE, known)
        for tile_id in BYPASS_TILES:
            self.assertNotIn(tile_id, known, f"Обход {tile_id} известен без вести")
        limited = find_path(world, ORIGIN, DEST, "caravan", known=known)
        self.assertIsNotNone(limited, "По известному пути обоз не прошёл")
        self.assertIn(FOREST_TILE, limited[0])
        omniscient = find_path(world, ORIGIN, DEST, "caravan", known=None)
        self.assertIsNotNone(omniscient)
        self.assertNotIn(FOREST_TILE, omniscient[0])
        self.assertGreater(
            limited[1], omniscient[1], "Туман не удержал игрока от невидимого пути"
        )

    def test_march_into_unknown_fails_not_teleports(self) -> None:
        world = self.world
        household = world.households["hh_retinue"]
        adults = list(household.member_ids)[:1]
        with self.assertRaises(ValueError):
            march_module.send_march(world, "hh_retinue", adults, "t_08_05", "foot")
        self.assertFalse(
            [p for p in world.packs.values() if p.owner_household_id == "hh_retinue"],
            "Воз создан без пути",
        )


class TestSendMarch(unittest.TestCase):
    """Приказ send_march: Pack, маршрут, месяцы и профиль в логе."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO)

    def test_march_creates_pack_and_logs_route(self) -> None:
        world = self.world
        for tile_id in ("t_02_01", "t_03_01"):
            _report_about(world, tile_id)
        household = world.households["hh_retinue"]
        adults = list(household.member_ids)[:1]
        before = list(household.member_ids)
        pack = march_module.send_march(world, "hh_retinue", adults, "t_03_01", "foot")
        self.assertEqual(pack.kind, "party")
        self.assertEqual(pack.route[0], "t_01_01")
        self.assertEqual(pack.route[-1], "t_03_01")
        self.assertEqual(pack.status, "in_transit")
        self.assertEqual(len(household.member_ids), len(before) - 1)
        records = [r for r in world.player_actions if r.get("action") == "send_march"]
        self.assertEqual(len(records), 1, "Приказ не попал в лог")
        record = records[0]
        self.assertEqual(record["profile"], "foot")
        self.assertEqual(record["route"], pack.route)
        self.assertEqual(record["months"], 1)
        # ADR 0071: лог игроку несёт часы (истина) и производные сутки/месяц.
        self.assertAlmostEqual(record["travel_hours"], 1.3, places=6)
        self.assertEqual(record["days"], 1)
        self.assertNotEqual(pack.eta_date.day, 1, "ETA не с точностью до дня")
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_march_travel_months_grow_with_distance(self) -> None:
        self.assertEqual(travel_months(2.0), 1)
        self.assertEqual(travel_months(30.0), 1)
        self.assertEqual(travel_months(31.0), 2)

    def test_runner_knows_send_march(self) -> None:
        self.assertIn("send_march", ACTION_HANDLERS)

    def test_remembered_roads_from_arrived_packs(self) -> None:
        world = self.world
        world.tiles["t_02_01"].road = True
        from hillcourt.ontology import Pack as PackCls

        world.packs["pack_mem"] = PackCls(
            id="pack_mem",
            kind="party",
            origin_tile_id="t_01_01",
            destination_tile_id="t_02_01",
            route=["t_01_01", "t_02_01"],
            member_ids=[],
            cargo=world.get_stock("settlement:hill_court"),
            departed_date=world.clock.date,
            eta_date=world.clock.date,
            status="arrived",
        )
        self.assertIn("t_02_01", remembered_road_tiles(world))
        self.assertNotIn("t_03_01", remembered_road_tiles(world))


if __name__ == "__main__":
    unittest.main()
