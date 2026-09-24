"""Сценарий баронства 100×100: река, раскладка дворов и ленивый рост."""

from __future__ import annotations

import time
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import yaml

from hillcourt.engine.growth import index_growth_tiles, run_detailed_growth
from hillcourt.engine.hexgrid import axial_is_neighbor, offset_to_axial
from hillcourt.engine.tick import phase_growth, run_month
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_barony_100.yml"


def _data() -> dict:
    with SCENARIO.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _river_cells(data: dict) -> tuple[set[str], dict[str, list[tuple[int, int]]]]:
    flows = {
        str(river["id"]): [(int(point[0]), int(point[1])) for point in river["flow_order"]]
        for river in data["rivers"]
    }
    cells = {f"t_{x:02d}_{y:02d}" for flow in flows.values() for x, y in flow}
    return cells, flows


class TestBaronyMap(unittest.TestCase):
    """Скелет, река и точки мира являются сценарными данными."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = load_scenario(SCENARIO, seed=1729)
        cls.data = _data()

    def test_grid_has_ten_thousand_hexes(self) -> None:
        self.assertEqual(len(self.world.tiles), 10_000)
        self.assertEqual(len(self.world.tiles), len(self.world.stocks) - len(self.world.settlements) - len(self.world.households) - 3)

    def test_river_is_six_connected_metadata(self) -> None:
        cells, flows = _river_cells(self.data)
        self.assertEqual({name: len(flow) for name, flow in flows.items()}, {"main_river": 126, "north_tributary": 41, "south_tributary": 41})
        self.assertEqual(len(cells), 199)
        for flow in flows.values():
            for before, after in zip(flow, flow[1:]):
                self.assertTrue(axial_is_neighbor(offset_to_axial(*before), offset_to_axial(*after)))
        self.assertTrue(all(self.world.tiles[tid].terrain == "water" for tid in cells))
        self.assertEqual(self.world.tiles["t_05_95"].terrain, "water")
        self.assertNotIn("t_05_95", cells)

    def test_crossings_and_port_are_data_only(self) -> None:
        for point in self.data["fords"]:
            tile = self.world.tiles[f"t_{point[0]:02d}_{point[1]:02d}"]
            self.assertEqual(tile.terrain, "water")
            self.assertTrue(tile.ford)
        for point in self.data["bridges"]:
            tile = self.world.tiles[f"t_{point[0]:02d}_{point[1]:02d}"]
            self.assertEqual(tile.terrain, "water")
            self.assertTrue(tile.bridge)
        port = self.world.settlements["river_port"]
        self.assertEqual(port.coord, (55, 95))
        self.assertEqual(port.household_ids, [])
        self.assertTrue(getattr(self.world.tiles["t_55_95"], "mooring", False))
        self.assertNotIn("Port", type(port).__name__)

    def test_forests_pastures_wilds_and_marks(self) -> None:
        terrains = Counter(tile.terrain for tile in self.world.tiles.values())
        forests = [region for region in self.data["map"]["regions"] if region["terrain"] == "forest"]
        self.assertEqual(len(forests), 3)
        for region in forests:
            left, top, right, bottom = region["rect"]
            self.assertTrue(300 <= (right - left + 1) * (bottom - top + 1) <= 500)
        self.assertGreater(terrains["pasture"], 0)
        self.assertGreater(terrains["heath"], 0)
        for mark, point in (
            ("hermitage", (15, 65)),
            ("waystation", (80, 15)),
            ("smoke", (35, 60)),
            ("smoke", (60, 20)),
            ("lost_caravan", (60, 85)),
            ("mine", (25, 75)),
        ):
            self.assertTrue(getattr(self.world.tiles[f"t_{point[0]:02d}_{point[1]:02d}"], mark, False))
        self.assertNotIn("t_25_75", {tile.id for tile in self.world.tiles.values() if tile.settlement_id})


class TestBaronyPlacement(unittest.TestCase):
    """Группы дворов разворачиваются по settlement-сцене без новых полей мира."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = load_scenario(SCENARIO, seed=1729)

    def test_city_uses_five_full_quarters_and_keeps_three_reserve(self) -> None:
        city = self.world.settlements["hill_court"]
        self.assertEqual(len(city.household_ids), 100)
        counts = Counter(self.world.households[hid].current_tile_id for hid in city.household_ids)
        self.assertEqual(len(counts), 5)
        self.assertEqual(set(counts.values()), {20})
        self.assertEqual(
            set(counts),
            {"t_45_50", "t_46_50", "t_47_50", "t_44_51", "t_45_51"},
        )
        quarter_tiles = {
            f"t_{point[0]:02d}_{point[1]:02d}"
            for point in self.data_entry("hill_court")["quarter_tiles"]
        }
        self.assertEqual(len(quarter_tiles), 8)
        self.assertTrue(quarter_tiles - set(counts))

    def test_eight_villages_have_fifty_households_on_ten_hexes(self) -> None:
        villages = [sid for sid, settlement in self.world.settlements.items() if settlement.kind == "village" and settlement.household_ids and sid != "river_port"]
        self.assertEqual(len(villages), 8)
        for sid in villages:
            settlement = self.world.settlements[sid]
            self.assertEqual(len(settlement.household_ids), 50)
            counts = Counter(self.world.households[hid].current_tile_id for hid in settlement.household_ids)
            self.assertEqual(len(counts), 10)
            self.assertEqual(set(counts.values()), {5})

    def test_native_salt_and_mine_site(self) -> None:
        self.assertEqual(len(self.world.settlements["native_village"].household_ids), 50)
        self.assertEqual(len(self.world.settlements["salt_village"].household_ids), 50)
        self.assertEqual(len(self.world.households), 600)
        self.assertNotIn("t_25_75", {tile.id for tile in self.world.tiles.values() if tile.settlement_id})

    def data_entry(self, settlement_id: str) -> dict:
        return next(entry for entry in _data()["settlements"] if entry["id"] == settlement_id)


class TestLazyGrowth(unittest.TestCase):
    """Индекс роста зависит от поселений, а не от размера coarse-карты."""

    def test_index_contains_only_settlements_and_works(self) -> None:
        world = load_scenario(SCENARIO, seed=1729)
        expected = {
            f"t_{settlement.coord[0]:02d}_{settlement.coord[1]:02d}"
            for settlement in world.settlements.values()
        }
        expected.update(tile_id for settlement in world.settlements.values() for tile_id in settlement.works_tiles)
        self.assertEqual(set(world.growth_tile_ids), expected)
        self.assertEqual(world.growth_tile_ids, index_growth_tiles(world))

    def test_growth_phase_does_not_scan_coarse_tiles(self) -> None:
        world = load_scenario(SCENARIO, seed=1729)
        small = SimpleNamespace(
            tiles={tid: world.tiles[tid] for tid in world.growth_tile_ids},
            growth_tile_ids=world.growth_tile_ids,
            catalogs=world.catalogs,
            ledger=world.ledger,
            clock=world.clock,
            get_stock=world.get_stock,
        )
        iterations = 30
        started = time.perf_counter()
        for _ in range(iterations):
            run_detailed_growth(small, 0.000001)
        small_seconds = time.perf_counter() - started
        started = time.perf_counter()
        for _ in range(iterations):
            run_detailed_growth(world, 0.000001)
        large_seconds = time.perf_counter() - started
        self.assertLessEqual(large_seconds, small_seconds * 5.0 + 0.02)

    def test_only_indexed_field_receives_nature(self) -> None:
        world = load_scenario(SCENARIO, seed=1729)
        indexed_field = next(tid for tid in world.growth_tile_ids if world.tiles[tid].terrain == "field")
        coarse_forest = next(tid for tid, tile in world.tiles.items() if tile.terrain == "forest" and tid not in world.growth_tile_ids)
        before_indexed = world.get_stock(world.tiles[indexed_field].standing_stock_id).amounts.get("grain", 0.0)
        before_coarse = world.get_stock(world.tiles[coarse_forest].standing_stock_id).amounts.get("firewood", 0.0)
        phase_growth(world)
        self.assertGreater(world.get_stock(world.tiles[indexed_field].standing_stock_id).amounts.get("grain", 0.0), before_indexed)
        self.assertEqual(world.get_stock(world.tiles[coarse_forest].standing_stock_id).amounts.get("firewood", 0.0), before_coarse)


class TestBaronyRun(unittest.TestCase):
    """Новый мир имеет честный детерминированный стартовый контур."""

    def test_twelve_months_delta_and_replay(self) -> None:
        first = load_scenario(SCENARIO, seed=1729)
        second = load_scenario(SCENARIO, seed=1729)
        for _ in range(12):
            run_month(first)
            run_month(second)
        self.assertAlmostEqual(first.ledger.delta(first.total_matter()), 0.0, places=6)
        self.assertEqual(first.state_hash(), second.state_hash())


if __name__ == "__main__":
    unittest.main()
