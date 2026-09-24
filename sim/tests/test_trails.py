"""Этап троп: уровни от ходьбы, затухание, веса, память грунта, метки.

Механика (`engine/trails.py` + `Tile.trail_wear`): пороги 3.0 (тропа) / 12.0
(грунтовка), затухание 0.5/мес, вес прохода на воз (caravan 4.0,
party/household_move 2.0) и на ходока (1.0), река 0; строеная дорога и вода
вне системы; стройка дороги — только `work_road`; возницы помнят грунт как
дорогу; метки рождения для Info (`world.stats`). И-2: вес на возу, не на
человеке; И-4: месячный контур, не дневной pathfinding.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.path import remembered_road_tiles
from hillcourt.engine.terrain import (
    MOVEMENT_PROFILES,
    TRAIL_WEAR_DIRT,
    TRAIL_WEAR_TRAIL,
    entry_cost,
    trail_level_for,
)
from hillcourt.engine.trails import (
    TRAIL_DECAY_PER_MONTH,
    decay_trails,
    pack_tread_weight,
    tread_arrivals,
    tread_tile,
    tread_travelers,
    trail_summary,
)
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SHIRE = ROOT / "design" / "scenarios" / "v0_shire.yml"
FIELD_TILE = "t_02_02"


class TestTrailLevels(unittest.TestCase):
    """Пороги, затухание и честные скидки входа."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO, seed=1729)

    def test_thresholds_and_decay(self) -> None:
        tile = self.world.tiles[FIELD_TILE]
        self.assertEqual(trail_level_for(0.0), 0)
        self.assertEqual(trail_level_for(TRAIL_WEAR_TRAIL - 0.01), 0)
        self.assertEqual(trail_level_for(TRAIL_WEAR_TRAIL), 1)
        self.assertEqual(trail_level_for(TRAIL_WEAR_DIRT - 0.01), 1)
        self.assertEqual(trail_level_for(TRAIL_WEAR_DIRT), 2)
        tile.trail_wear = 1.5
        decay_trails(self.world)
        self.assertAlmostEqual(tile.trail_wear, 1.0, places=9)
        decay_trails(self.world)
        self.assertAlmostEqual(tile.trail_wear, 0.5, places=9)
        decay_trails(self.world)
        self.assertAlmostEqual(tile.trail_wear, 0.0, places=9)
        decay_trails(self.world)
        self.assertAlmostEqual(tile.trail_wear, 0.0, places=9, msg="Износ ушёл в минус")

    def test_trail_cheapens_entry_but_stays_under_road(self) -> None:
        for terrain in ("field", "forest", "heath"):
            plain = entry_cost("caravan", terrain, False, False, False, 0)
            trail = entry_cost("caravan", terrain, False, False, False, 1)
            dirt = entry_cost("caravan", terrain, False, False, False, 2)
            self.assertLess(trail, plain, terrain)
            self.assertLess(dirt, trail, terrain)
        # Мостовая дешевле грунтовой всюду, где целина дорога не равна полю.
        for terrain in ("forest", "marsh", "heath"):
            road = entry_cost("caravan", terrain, True, False, False, 0)
            dirt = entry_cost("caravan", terrain, False, False, False, 2)
            self.assertLess(road, dirt, "Грунтовка не должна быть дешевле мостовой")

    def test_water_and_road_are_outside_trail_system(self) -> None:
        water = self.world.tiles[FIELD_TILE]
        water.terrain = "water"
        road_tile = self.world.tiles["t_02_05"]
        road_tile.road = True
        self.assertIsNone(tread_tile(self.world, water.id, 4.0))
        self.assertAlmostEqual(water.trail_wear, 0.0)
        self.assertIsNone(tread_tile(self.world, FIELD_TILE, 4.0))
        self.assertAlmostEqual(road_tile.trail_wear, 0.0)
        road_tile.trail_wear = 9.0
        decay_trails(self.world)
        self.assertAlmostEqual(road_tile.trail_wear, 0.0, msg="Дорога вне системы")

    def test_built_road_only_by_action(self) -> None:
        self.assertFalse(self.world.tiles[FIELD_TILE].road)
        for _ in range(6):
            tread_tile(self.world, FIELD_TILE, 4.0)
        self.assertFalse(
            self.world.tiles[FIELD_TILE].road, "Ходьба построила дорогу сама"
        )


class TestTrailMarks(unittest.TestCase):
    """Метки рождения для Info — только при пересечении порога."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO, seed=1729)

    def test_mark_only_on_threshold_crossing(self) -> None:
        self.assertIsNone(tread_tile(self.world, FIELD_TILE, 1.0))
        self.assertNotIn(f"trail_born_{FIELD_TILE}", self.world.stats)
        self.assertEqual(tread_tile(self.world, FIELD_TILE, 2.0), 1)
        self.assertEqual(self.world.stats.get("trails_born"), 1.0)
        self.assertIn(f"trail_born_{FIELD_TILE}", self.world.stats)
        self.assertEqual(
            self.world.stats[f"trail_born_{FIELD_TILE}"],
            float(self.world.clock.year * 12 + self.world.clock.month),
        )
        self.assertIsNone(tread_tile(self.world, FIELD_TILE, 2.0), "Повтор без порога")
        self.assertEqual(self.world.stats.get("trails_born"), 1.0)
        self.assertEqual(tread_tile(self.world, FIELD_TILE, 8.0), 2)
        self.assertEqual(self.world.stats.get("dirt_born"), 1.0)
        self.assertIn(f"dirt_born_{FIELD_TILE}", self.world.stats)
        self.assertAlmostEqual(
            self.world.ledger.delta(self.world.total_matter()), 0.0, places=6
        )


class TestTreadWeights(unittest.TestCase):
    """Вес на возу/отряде, река 0, жители топчут сами."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO, seed=1729)

    def test_pack_weights_by_kind_and_river_zero(self) -> None:
        from hillcourt.ontology import Pack as PackCls

        cargo = self.world.get_stock("settlement:hill_court")
        def make(kind: str, with_origin: bool = True):
            route = ["t_01_01", FIELD_TILE]
            if not with_origin:
                route = [FIELD_TILE]
            return PackCls(
                id=f"p_{kind}_{with_origin}",
                kind=kind,
                origin_tile_id="t_01_01",
                destination_tile_id=FIELD_TILE,
                route=route,
                member_ids=[],
                cargo=cargo,
                departed_date=self.world.clock.date,
                eta_date=self.world.clock.date,
                status="in_transit",
            )

        self.assertAlmostEqual(pack_tread_weight(make("caravan")), 4.0)
        self.assertAlmostEqual(pack_tread_weight(make("party")), 2.0)
        self.assertAlmostEqual(pack_tread_weight(make("household_move")), 2.0)
        self.assertAlmostEqual(
            pack_tread_weight(make("caravan", with_origin=False)), 0.0, places=9
        )
        self.assertAlmostEqual(pack_tread_weight(make("pack")), 0.0)

    def test_travelers_tread_their_own_tile(self) -> None:
        household = self.world.households["hh_01"]
        household.traveling = True
        before = household.current_tile_id
        self.assertEqual(tread_travelers(self.world), 1)
        self.assertGreater(self.world.tiles[before].trail_wear, 0.0)
        household.traveling = False
        self.assertEqual(tread_travelers(self.world), 0)

    def test_arrivals_tread_route_once(self) -> None:
        from hillcourt.ontology import Pack as PackCls

        cargo = self.world.get_stock("settlement:hill_court")
        pack = PackCls(
            id="pack_tread",
            kind="caravan",
            origin_tile_id="t_01_01",
            destination_tile_id=FIELD_TILE,
            route=["t_01_01", FIELD_TILE],
            member_ids=[],
            cargo=cargo,
            departed_date=self.world.clock.date,
            eta_date=self.world.clock.date,
            status="in_transit",
        )
        self.world.packs[pack.id] = pack
        self.assertEqual(
            tread_arrivals(self.world, [pack.id]), 0, "В пути ещё не топтали"
        )
        self.assertAlmostEqual(self.world.tiles[FIELD_TILE].trail_wear, 0.0)
        pack.status = "arrived"
        worn = self.world.tiles[FIELD_TILE].trail_wear
        self.assertEqual(tread_arrivals(self.world, [pack.id]), 1)
        self.assertAlmostEqual(self.world.tiles[FIELD_TILE].trail_wear, worn + 4.0)
        self.assertAlmostEqual(
            self.world.ledger.delta(self.world.total_matter()), 0.0, places=6
        )


class TestRememberedGroundRoad(unittest.TestCase):
    """Возницы помнят грунт как дорогу, тропу — нет."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO, seed=1729)

    def _arrived(self, tile_id: str) -> None:
        from hillcourt.ontology import Pack as PackCls

        self.world.packs["pack_mem_trail"] = PackCls(
            id="pack_mem_trail",
            kind="caravan",
            origin_tile_id="t_01_01",
            destination_tile_id=tile_id,
            route=["t_01_01", tile_id],
            member_ids=[],
            cargo=self.world.get_stock("settlement:hill_court"),
            departed_date=self.world.clock.date,
            eta_date=self.world.clock.date,
            status="arrived",
        )

    def test_dirt_remembered_trail_not(self) -> None:
        trail_tile = FIELD_TILE
        dirt_tile = "t_02_05"
        self.world.tiles[trail_tile].trail_wear = TRAIL_WEAR_TRAIL
        self.world.tiles[dirt_tile].trail_wear = TRAIL_WEAR_DIRT
        self._arrived(trail_tile)
        self._arrived(dirt_tile)
        remembered = remembered_road_tiles(self.world)
        self.assertNotIn(trail_tile, remembered, "Тропа не помнится дорогой")
        self.assertIn(dirt_tile, remembered, "Грунт возница помнит")


class TestNewProfiles(unittest.TestCase):
    """Профили строками: arms (пеший ×1.25) и rider (поле/чаща/топь/брод)."""

    def test_arms_is_foot_times_1_25(self) -> None:
        for terrain in ("hill", "field", "pasture", "forest", "marsh", "heath"):
            self.assertAlmostEqual(
                entry_cost("arms", terrain, False, False, False),
                entry_cost("foot", terrain, False, False, False) * 1.25,
                places=9,
                msg=terrain,
            )
        self.assertIn("arms", MOVEMENT_PROFILES)

    def test_rider_costs(self) -> None:
        self.assertAlmostEqual(entry_cost("rider", "field", False, False, False), 0.6)
        self.assertAlmostEqual(entry_cost("rider", "forest", False, False, False), 4.0)
        self.assertAlmostEqual(entry_cost("rider", "marsh", False, False, False), 6.0)
        self.assertAlmostEqual(entry_cost("rider", "water", False, True, False), 4.0)
        self.assertEqual(entry_cost("rider", "water", False, False, False), float("inf"))
        self.assertIn("rider", MOVEMENT_PROFILES)


class TestTrailsInWorldRun(unittest.TestCase):
    """Живой тик: тропы появляются от ходьбы, детерминизм и дельта 0."""

    def test_trails_grow_and_deterministic(self) -> None:
        def run(seed: int):
            world = load_scenario(SHIRE, seed=seed)
            for _ in range(12):
                from hillcourt.engine.tick import run_month

                run_month(world)
            return (
                trail_summary(world),
                world.state_hash(),
                round(world.ledger.delta(world.total_matter()), 9),
            )

        first = run(1729)
        self.assertEqual(first, run(1729), "Тот же seed — разный результат")
        self.assertEqual(first[2], 0.0)
        self.assertGreater(first[0]["trail"] + first[0]["dirt"], 0, "Троп не родился")


class TestTrailReportWiring(unittest.TestCase):
    """Проводка вести о тропе в живом тике: топтание → весть в `PlayerView`.

    Тексты/канал/радиус глаза — зона Info (`news/trails.py`); здесь пин связки:
    живой месяц с рождением тропы в глазу даёт весть `eye_from_hill` с
    `facts['trail']`; без тропы — тишина; повторный `phase_inform` — без дублей.
    """

    def _trail_entries(self, world, view):
        from hillcourt.info.sources import EYE_FROM_HILL

        return [
            entry
            for entry in view.entries
            if entry.source == EYE_FROM_HILL and entry.facts.get("trail")
        ]

    def _force_trail_in_eye(self, world) -> str:
        """Заставить топтание в клетке глаза и вернуть её id."""
        from hillcourt.engine.seat import eye_tiles

        eye = sorted(eye_tiles(world))
        self.assertTrue(eye, "У корня нет глазных клеток")
        tile_id = eye[0]
        world.tiles[tile_id].road = False
        world.tiles[tile_id].trail_wear = 0.0
        world.catalogs.spawn_rules["wolves_den"].params["base_prob"] = 0.0
        from hillcourt.engine.trails import tread_tile

        # 2.0 ходьбы: 0 → тропа не пересекает порог, 4.0 — пересекает.
        tread_tile(world, tile_id, 2.0)
        self.assertNotIn(f"trail_born_{tile_id}", world.stats)
        tread_tile(world, tile_id, 2.0)
        self.assertIn(f"trail_born_{tile_id}", world.stats, "Метка не родилась")
        return tile_id

    def test_live_tick_topup_reports_trail_in_player_view(self) -> None:
        from hillcourt.news.views import build_player_view

        world = load_scenario(SCENARIO, seed=1729)
        tile_id = self._force_trail_in_eye(world)
        from hillcourt.engine.tick import phase_inform

        phase_inform(world)
        view = build_player_view(world, world.clock.date)
        entries = self._trail_entries(world, view)
        self.assertEqual(len(entries), 1, "Тропа промолчала в живом тике")
        self.assertEqual(entries[0].subject_id, tile_id)
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )

    def test_live_month_without_trail_is_silent(self) -> None:
        from hillcourt.engine.tick import run_month
        from hillcourt.news.views import build_player_view

        world = load_scenario(SCENARIO, seed=1729)
        run_month(world)
        self.assertFalse(
            [key for key in world.stats if key.startswith("trail_born_")],
            "Тропа родилась без ходьбы",
        )
        view = build_player_view(world, world.clock.date)
        self.assertEqual(self._trail_entries(world, view), [], "Весть без тропы")

    def test_reentered_inform_does_not_duplicate(self) -> None:
        from hillcourt.engine.tick import phase_inform
        from hillcourt.news.views import build_player_view

        world = load_scenario(SCENARIO, seed=1729)
        self._force_trail_in_eye(world)
        phase_inform(world)
        phase_inform(world)
        view = build_player_view(world, world.clock.date)
        self.assertEqual(
            len(self._trail_entries(world, view)), 1, "Повторный заход задублировал весть"
        )


if __name__ == "__main__":
    unittest.main()
