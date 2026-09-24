"""Земля баронства: вид и данные без бонусов (видение, не закон v0).

Пины: новые формы каталога; маркеры видения (без разметки вид побайтово
старый — весь v0_shire только из 17 прежних форм); кормовая ёмкость пастбищ —
чистые данные (материя/труд стоят, тик не читает); тропа vs строеная;
стадии роста (legacy-6 — village, 10+ — large_village); городов нет.
"""

from __future__ import annotations

import math
import unittest
from pathlib import Path

from hillcourt.engine.terrain import entry_cost
from hillcourt.engine.tile_view import (
    FORMS,
    LAND_MARKS,
    LARGE_VILLAGE_MIN_HOUSEHOLDS,
    PASTURE_FORAGE_CAPACITY,
    coarse_map_view,
    dwelling_form,
    household_count,
    mine_sites,
    pasture_forage_capacity,
    tile_form,
    tile_view,
)
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SHIRE = ROOT / "design" / "scenarios" / "v0_shire.yml"
HILL = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"

OLD_FORMS = {
    "open_field",
    "forest",
    "waste",
    "single_homestead",
    "village",
    "thegn_estate",
    "hall_on_hill",
    "lord_field",
    "crossing",
    "bog_iron",
    "quarry",
    "foot_pot_camp",
    "campfire_camp",
    "tent_camp",
    "pavilion_camp",
    "wagon_camp",
    "fortified_camp",
}

NEW_FORMS = {
    "pier",
    "trail",
    "dirt_road",
    "built_road",
    "waystation",
    "tavern_site",
    "iron_mine",
    "large_village",
    "tribal_village",
    "baron_castle",
    "tent_earth_homestead",
    "timber_house",
    "multi_storey_house",
    "salt_settlement",
    "ruin_site",
    "hermitage",
    "smoke_site",
    "lost_caravan",
    "city_quarter",
}


def _mark(world, tile_id: str, mark: str):
    """Поставить маркер видения in-memory (сценарий видения — полем клетки)."""
    assert mark in LAND_MARKS, f"Маркер '{mark}' вне {LAND_MARKS}"
    setattr(world.tiles[tile_id], mark, True)


class TestVisionCatalog(unittest.TestCase):
    """Каталог вида: новые формы именованы по-русски, с правилом."""

    def test_new_forms_listed_with_rules(self) -> None:
        for fid in sorted(NEW_FORMS):
            self.assertIn(fid, FORMS, f"Нет формы '{fid}'")
            self.assertTrue(FORMS[fid]["name"], f"У '{fid}' нет имени")
            self.assertTrue(FORMS[fid]["rule"], f"У '{fid}' нет правила")

    def test_no_city_still(self) -> None:
        self.assertNotIn("city", FORMS)
        self.assertNotIn("town", FORMS)


class TestMarksKeepOldView(unittest.TestCase):
    """Без разметки меняется только стадия роста: ясень 16 дворов — большая."""

    def test_shire_without_marks_only_old_forms_plus_growth(self) -> None:
        world = load_scenario(SHIRE)
        forms = {tile_form(world, tid) for tid in world.tiles}
        self.assertTrue(
            forms <= OLD_FORMS | {"large_village", "salt_settlement", "ruin_site"},
            f"Неожиданные формы без разметки: {forms - OLD_FORMS - {'large_village', 'salt_settlement', 'ruin_site'}}",
        )
        self.assertEqual(tile_form(world, "t_02_07"), "large_village")

    def test_mine_sites_empty_without_marks(self) -> None:
        world = load_scenario(SHIRE)
        self.assertEqual(mine_sites(world), [])


class TestVisionMarks(unittest.TestCase):
    """Маркеры переключают вид; живое бьёт размеченное."""

    def test_trail_and_built_road_on_empty(self) -> None:
        world = load_scenario(HILL)
        empty = next(
            tid
            for tid in sorted(world.tiles)
            if household_count(world, tid) == 0
            and world.tiles[tid].terrain == "field"
            and not world.tiles[tid].road
        )
        world.tiles[empty].trail_wear = 3.0
        self.assertEqual(tile_form(world, empty), "trail")
        world.tiles[empty].trail_wear = 12.0
        self.assertEqual(tile_form(world, empty), "dirt_road")
        world.tiles[empty].road = True
        self.assertEqual(tile_form(world, empty), "built_road")

    def test_dwelling_markers_choose_single_home_form(self) -> None:
        for level, form in (
            ("tent_earth", "tent_earth_homestead"),
            ("house", "timber_house"),
            ("multi_storey", "multi_storey_house"),
        ):
            probe = load_scenario(HILL)
            empty = next(
                tid
                for tid in sorted(probe.tiles)
                if household_count(probe, tid) == 0
            )
            probe.tiles[empty].dwelling = level
            probe.households["hh_01"].current_tile_id = empty
            self.assertEqual(household_count(probe, empty), 1)
            self.assertEqual(tile_form(probe, empty), form)

    def test_mixed_dwelling_levels_stay_village(self) -> None:
        probe = load_scenario(HILL)
        empty = next(
            tid for tid in sorted(probe.tiles) if household_count(probe, tid) == 0
        )
        probe.tiles[empty].dwelling = "multi_storey"
        probe.households["hh_01"].current_tile_id = empty
        probe.households["hh_02"].current_tile_id = empty
        self.assertEqual(household_count(probe, empty), 2)
        self.assertEqual(tile_form(probe, empty), "village")

    def test_unknown_dwelling_value_rejected(self) -> None:
        probe = load_scenario(HILL)
        probe.tiles["t_02_00"].dwelling = "mansion"
        with self.assertRaises(ValueError):
            dwelling_form(probe, "t_02_00")

    def test_inhabited_road_shows_housing(self) -> None:
        world = load_scenario(HILL)
        home = world.households["hh_01"].current_tile_id
        before = tile_form(world, home)
        self.assertGreaterEqual(household_count(world, home), 1)
        world.tiles[home].road = True
        self.assertEqual(tile_form(world, home), before)
        self.assertNotEqual(tile_form(world, home), "built_road")

    def test_pier_needs_crossing(self) -> None:
        world = load_scenario(SHIRE)
        ford_tile = next(tid for tid in sorted(world.tiles) if world.tiles[tid].ford)
        self.assertEqual(tile_form(world, ford_tile), "crossing")
        _mark(world, ford_tile, "mooring")
        self.assertEqual(tile_form(world, ford_tile), "pier")

    def test_port_and_crossings_are_view_only(self) -> None:
        world = load_scenario(SHIRE)
        water = next(
            tid for tid in sorted(world.tiles) if world.tiles[tid].terrain == "water"
        )
        before = entry_cost("caravan", "water", False, False, False)
        _mark(world, water, "mooring")
        self.assertEqual(tile_form(world, water), "pier")
        self.assertEqual(
            entry_cost("caravan", "water", False, False, False), before
        )
        self.assertTrue(math.isinf(before))
        ford_tile = next(tid for tid in sorted(world.tiles) if world.tiles[tid].ford)
        self.assertEqual(tile_form(world, ford_tile), "crossing")
        bridge_tile = next(
            tid
            for tid in sorted(world.tiles)
            if tid != water
            and world.tiles[tid].terrain == "water"
            and not world.tiles[tid].ford
        )
        world.tiles[bridge_tile].bridge = True
        self.assertEqual(tile_form(world, bridge_tile), "crossing")

    def test_salt_ruin_and_city_quarter_forms(self) -> None:
        world = load_scenario(HILL)
        salt_home = next(
            hh.current_tile_id
            for hh in world.households.values()
            if world.tiles[hh.current_tile_id].settlement_id == "salt_village"
        )
        self.assertEqual(tile_form(world, salt_home), "salt_settlement")
        ruin = next(
            tid for tid in sorted(world.tiles) if world.tiles[tid].terrain == "ruin"
        )
        self.assertEqual(tile_form(world, ruin), "ruin_site")
        city = load_scenario(HILL)
        city_home = next(
            tid for tid in sorted(city.tiles) if household_count(city, tid) == 0
        )
        _mark(city, city_home, "urban")
        city.households["hh_01"].current_tile_id = city_home
        self.assertEqual(tile_form(city, city_home), "city_quarter")

    def test_wilds_markers_do_not_change_known_tiles(self) -> None:
        world = load_scenario(HILL)
        empty = next(
            tid for tid in sorted(world.tiles) if household_count(world, tid) == 0
        )
        before = list(world.reports)
        _mark(world, empty, "hermitage")
        _mark(world, empty, "smoke")
        self.assertEqual(tile_form(world, empty), "smoke_site")
        delattr(world.tiles[empty], "smoke")
        self.assertEqual(tile_form(world, empty), "hermitage")
        delattr(world.tiles[empty], "hermitage")
        _mark(world, empty, "lost_caravan")
        self.assertEqual(tile_form(world, empty), "lost_caravan")
        self.assertEqual(list(world.reports), before)
        self.assertEqual(tile_form(world, empty), tile_form(world, empty))

    def test_waystation_tavern_mine_castle(self) -> None:
        world = load_scenario(HILL)
        empty = next(
            tid
            for tid in sorted(world.tiles)
            if household_count(world, tid) == 0
            and world.tiles[tid].terrain == "heath"
        )
        for mark, form in (
            ("waystation", "waystation"),
            ("tavern", "tavern_site"),
            ("mine", "iron_mine"),
            ("castle", "baron_castle"),
        ):
            probe = load_scenario(HILL)
            _mark(probe, empty, mark)
            self.assertEqual(tile_form(probe, empty), form)
            if mark == "mine":
                self.assertEqual(mine_sites(probe), [empty])

    def test_tribal_village(self) -> None:
        world = load_scenario(HILL)
        home = next(
            hh.current_tile_id
            for hid, hh in sorted(world.households.items())
            if hh.left_at is None and hh.current_tile_id != "t_01_01"
        )
        settlement_id = world.tiles[home].settlement_id
        self.assertIsNotNone(settlement_id)
        world.settlements[settlement_id].kind = "native_village"
        self.assertEqual(tile_form(world, home), "tribal_village")

    def test_empty_household_has_no_living_form_or_seat(self) -> None:
        world = load_scenario(HILL)
        root = "t_01_01"
        for household in world.households.values():
            if household.current_tile_id == root:
                household.member_ids = []
        view = tile_view(world, root)
        self.assertEqual(view.form, "open_field")
        self.assertEqual(world.tiles[root].terrain, "hill")
        self.assertFalse(view.has_seat)
        self.assertFalse(view.has_thegn_hall)
        self.assertEqual(household_count(world, root), 0)


class TestCoarseMapView(unittest.TestCase):
    """Coarse-карта отображает только существующие данные клеток."""

    def test_grid_has_terrain_forms_and_no_reports(self) -> None:
        world = load_scenario(HILL)
        before = list(world.reports)
        view = coarse_map_view(world)
        self.assertGreater(view.width, 0)
        self.assertGreater(view.height, 0)
        self.assertEqual(len(view.cells), len(world.tiles))
        self.assertEqual(view.cells, coarse_map_view(world).cells)
        self.assertTrue(all("terrain" in cell and "form" in cell for cell in view.cells))
        self.assertEqual(list(world.reports), before)

    def test_coarse_view_tracks_water_and_wilds_forms(self) -> None:
        world = load_scenario(HILL)
        view = coarse_map_view(world)
        forms = {cell["form"] for cell in view.cells}
        self.assertIn("waste", forms)
        self.assertTrue({"forest", "open_field"} & forms)

    def test_coarse_view_handles_100_by_100_grid(self) -> None:
        from hillcourt.ontology import Tile

        class EmptyWorld:
            pass

        world = EmptyWorld()
        world.manors = {}
        world.households = {}
        world.packs = {}
        world.reports = []
        world.tiles = {
            f"t_{x:02d}_{y:02d}": Tile(
                id=f"t_{x:02d}_{y:02d}",
                coord=(x, y),
                terrain="forest" if x < 50 else "water",
                standing_stock_id="standing:empty",
                hazard_ids=[],
            )
            for x in range(100)
            for y in range(100)
        }
        view = coarse_map_view(world)
        self.assertEqual((view.width, view.height), (100, 100))
        self.assertEqual(len(view.cells), 10000)


class TestGrowthStages(unittest.TestCase):
    """Стадии роста видом: 1 — двор, 2–9 — деревня, 10+ — большая."""

    def test_thresholds(self) -> None:
        self.assertEqual(LARGE_VILLAGE_MIN_HOUSEHOLDS, 10)
        world = load_scenario(SHIRE)
        ash = "t_02_07"
        self.assertGreaterEqual(household_count(world, ash), 10)
        self.assertEqual(tile_form(world, ash), "large_village")

    def test_legacy_six_stays_village(self) -> None:
        world = load_scenario(HILL)
        target = "t_02_00"
        for hid in ["hh_01", "hh_02", "hh_03", "hh_04", "hh_05"]:
            world.households[hid].current_tile_id = target
        world.households["hh_06"].current_tile_id = target
        self.assertEqual(household_count(world, target), 6)
        self.assertEqual(tile_form(world, target), "village")


class TestPastureData(unittest.TestCase):
    """Кормовая ёмкость — данные: чиста, детерминирована, тиком не читается."""

    def test_pasture_base_from_grow_hay(self) -> None:
        world = load_scenario(SHIRE)
        pasture = next(tid for tid in sorted(world.tiles) if world.tiles[tid].terrain == "pasture")
        self.assertAlmostEqual(pasture_forage_capacity(world, pasture), 2.0)

    def test_non_forage_terrains_zero(self) -> None:
        world = load_scenario(SHIRE)
        for terrain in ("forest", "marsh", "water", "salt_flat"):
            tid = next(tid for tid in sorted(world.tiles) if world.tiles[tid].terrain == terrain)
            self.assertAlmostEqual(pasture_forage_capacity(world, tid), 0.0)

    def test_capacity_is_pure(self) -> None:
        world = load_scenario(SHIRE)
        matter_before = world.total_matter()
        labor_before = {hid: hh.labor_days for hid, hh in world.households.items()}
        first = [pasture_forage_capacity(world, tid) for tid in sorted(world.tiles)]
        second = [pasture_forage_capacity(world, tid) for tid in sorted(world.tiles)]
        self.assertEqual(first, second)
        self.assertAlmostEqual(world.total_matter(), matter_before, places=9)
        for hid, hh in world.households.items():
            self.assertAlmostEqual(hh.labor_days, labor_before[hid], places=9)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=9)

    def test_capacity_covers_all_terrains(self) -> None:
        self.assertEqual(set(PASTURE_FORAGE_CAPACITY), {
            "hill", "field", "pasture", "forest", "marsh",
            "heath", "salt_flat", "ruin", "water",
        })

    def test_unknown_tile_raises(self) -> None:
        world = load_scenario(SHIRE)
        with self.assertRaises(ValueError):
            pasture_forage_capacity(world, "t_99_99")


if __name__ == "__main__":
    unittest.main()
