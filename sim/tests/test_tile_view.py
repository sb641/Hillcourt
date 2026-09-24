"""Наполнение тайла и контракт вида: кап 5, form из состояния, дамп для игрока.

Кап пашни (`plot_batch_cap_per_tile`, G3) живёт отдельно и здесь не ломается:
здесь кап — про место жительства (`current_tile_id`), а не про партии жатвы.
Legacy-переполнение сценария (16 дворов ясеня на одной клетке) видом называется
`village` и новых прав не даёт; новые посадки сверх капа — явный отказ.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.manor import grant_thegn
from hillcourt.engine.tile_view import (
    FORMS,
    TILE_MAX_HOUSEHOLDS,
    can_settle,
    dump_tile_views,
    format_tile_view,
    household_count,
    is_root_seat,
    is_thegn_seat,
    settle_household,
    tile_form,
    tile_max_households,
    tile_view,
)
from hillcourt.engine.tick import run_month
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"

# Пустые клетки v0_hill_and_salt (занятые — см. settlements/households сценария).
EMPTY_FOREST = "t_00_00"  # F
EMPTY_HEATH = "t_02_00"  # .
EMPTY_FIELD = "t_03_02"  # f, но занята fs_07? не использовать для пустоты
EMPTY_PASTURE = "t_04_00"  # p
ROOT_TILE = "t_01_01"  # hill_court
DEMESNE_SEAT = "t_05_02"  # домен холма -> seat тэна после grant
FIEF_LAND = "t_03_01"  # вторая клетка гранта (не seat, угодье)


class TestTileCap(unittest.TestCase):
    """Зонд капа: tile.max_households действует, шестой двор не садится."""

    def test_cap_is_five(self) -> None:
        world = load_scenario(SCENARIO)
        self.assertEqual(TILE_MAX_HOUSEHOLDS, 5)
        self.assertEqual(tile_max_households(world, EMPTY_FOREST), 5)

    def test_sixth_household_refused(self) -> None:
        """5 садятся, 6-й — PermissionError + settle_rejected, счёт стоит."""
        world = load_scenario(SCENARIO)
        before_matter = world.total_matter()
        movers = ["hh_01", "hh_02", "hh_03", "hh_04", "hh_05"]
        for hid in movers:
            settle_household(world, hid, EMPTY_FOREST)
        self.assertEqual(household_count(world, EMPTY_FOREST), 5)
        self.assertFalse(can_settle(world, EMPTY_FOREST))
        with self.assertRaises(PermissionError):
            settle_household(world, "hh_06", EMPTY_FOREST)
        self.assertEqual(
            household_count(world, EMPTY_FOREST), 5, "Шестой двор просочился"
        )
        rejected = [
            r
            for r in world.player_actions
            if r.get("action") == "settle_rejected" and r.get("tile") == EMPTY_FOREST
        ]
        self.assertTrue(rejected, "Отказ не записан в лог игрока")
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )
        self.assertAlmostEqual(world.total_matter(), before_matter, places=6)

    def test_settle_moves_only_residence(self) -> None:
        """Посадка двигает место, а не материю/труд/режимы."""
        world = load_scenario(SCENARIO)
        hh = world.households["hh_01"]
        labor_before = hh.labor_days
        matter_before = world.total_matter()
        regime_before = world.tiles[EMPTY_FOREST].regime_id
        settle_household(world, "hh_01", EMPTY_FOREST)
        self.assertEqual(hh.current_tile_id, EMPTY_FOREST)
        for pid in hh.member_ids:
            self.assertEqual(world.persons[pid].location_tile_id, EMPTY_FOREST)
        self.assertAlmostEqual(hh.labor_days, labor_before, places=9)
        self.assertEqual(world.tiles[EMPTY_FOREST].regime_id, regime_before)
        self.assertAlmostEqual(world.total_matter(), matter_before, places=9)


class TestTileForms(unittest.TestCase):
    """Каталог форм: вид следует из состояния, городов нет."""

    def test_catalog_has_no_city(self) -> None:
        self.assertNotIn("city", FORMS)
        self.assertNotIn("town", FORMS)
        for fid in (
            "open_field",
            "forest",
            "waste",
            "single_homestead",
            "village",
            "thegn_estate",
            "hall_on_hill",
        ):
            self.assertIn(fid, FORMS, f"Нет формы '{fid}'")

    def test_empty_forms_follow_terrain(self) -> None:
        world = load_scenario(SCENARIO)
        self.assertEqual(world.tiles[EMPTY_FOREST].terrain, "forest")
        self.assertEqual(tile_form(world, EMPTY_FOREST), "forest")
        self.assertEqual(world.tiles[EMPTY_HEATH].terrain, "heath")
        self.assertEqual(tile_form(world, EMPTY_HEATH), "waste")
        self.assertEqual(world.tiles[EMPTY_PASTURE].terrain, "pasture")
        self.assertEqual(tile_form(world, EMPTY_PASTURE), "open_field")

    def test_one_and_many_give_different_forms(self) -> None:
        """Два мира с разным числом дворов — разный form на той же клетке."""
        world_one = load_scenario(SCENARIO)
        settle_household(world_one, "hh_01", EMPTY_FOREST)
        world_many = load_scenario(SCENARIO)
        for hid in ["hh_01", "hh_02", "hh_03", "hh_04"]:
            settle_household(world_many, hid, EMPTY_FOREST)
        self.assertEqual(tile_form(world_one, EMPTY_FOREST), "single_homestead")
        self.assertEqual(tile_form(world_many, EMPTY_FOREST), "village")
        self.assertNotEqual(
            tile_form(world_one, EMPTY_FOREST),
            tile_form(world_many, EMPTY_FOREST),
        )

    def test_root_seat_is_hall(self) -> None:
        world = load_scenario(SCENARIO)
        view = tile_view(world, ROOT_TILE)
        self.assertTrue(view.has_seat)
        self.assertFalse(view.has_thegn_hall)
        self.assertEqual(view.form, "hall_on_hill")
        self.assertEqual(view.terrain, "hill")
        # На холме два двора, но это зал, а не деревня.
        self.assertGreaterEqual(view.household_count, 2)

    def test_thegn_grant_turns_seat_into_estate(self) -> None:
        """Пожаловали тэна — seat стал усадьбой, а не ещё одним хутором."""
        world = load_scenario(SCENARIO)
        grant_thegn(world, "hh_retinue_p1", [FIEF_LAND, DEMESNE_SEAT], ["hh_02"])
        seat = tile_view(world, DEMESNE_SEAT)
        self.assertTrue(seat.has_thegn_hall, "Seat тэна не помечен")
        self.assertFalse(seat.has_seat)
        self.assertEqual(seat.form, "thegn_estate")
        land = tile_view(world, FIEF_LAND)
        self.assertFalse(
            land.has_thegn_hall, "Угодье фьефа стало второй усадьбой"
        )
        self.assertNotEqual(land.form, "thegn_estate")

    def test_legacy_overfull_is_village_not_error(self) -> None:
        """Сценарий старше капа: переполненная клетка — village, вид честный."""
        world = load_scenario(SCENARIO)
        for hid in ["hh_01", "hh_02", "hh_03", "hh_04", "hh_05", "hh_06"]:
            settle_household(world, hid, EMPTY_HEATH) if household_count(
                world, EMPTY_HEATH
            ) < 5 else None
        # Досаживаем шестой обходным путём (напрямую местом, как legacy-загрузка):
        extra = world.households["hh_07"]
        extra.current_tile_id = EMPTY_HEATH
        self.assertGreater(household_count(world, EMPTY_HEATH), 5)
        self.assertEqual(tile_form(world, EMPTY_HEATH), "village")
        self.assertFalse(can_settle(world, EMPTY_HEATH))


class TestTileDump(unittest.TestCase):
    """Игрок видит form в логе/дампе без RimWorld-слоя; form нематериален."""

    def test_dump_shows_required_fields(self) -> None:
        world = load_scenario(SCENARIO)
        settle_household(world, "hh_01", EMPTY_FOREST)
        grant_thegn(world, "hh_retinue_p1", [FIEF_LAND, DEMESNE_SEAT], ["hh_02"])
        dump = {row["tile"]: row for row in dump_tile_views(world)}
        for tid in (EMPTY_HEATH, EMPTY_FOREST, ROOT_TILE, DEMESNE_SEAT):
            row = dump[tid]
            for key in (
                "terrain",
                "form",
                "household_count",
                "has_seat",
                "has_thegn_hall",
            ):
                self.assertIn(key, row, f"В дампе {tid} нет '{key}'")
        self.assertEqual(dump[EMPTY_HEATH]["form"], "waste")
        self.assertEqual(dump[EMPTY_FOREST]["form"], "single_homestead")
        self.assertEqual(dump[ROOT_TILE]["form"], "hall_on_hill")
        self.assertEqual(dump[DEMESNE_SEAT]["form"], "thegn_estate")
        line = format_tile_view(dump[DEMESNE_SEAT])
        self.assertIn("thegn_estate", line)
        self.assertIn(DEMESNE_SEAT, line)

    def test_view_is_pure_no_matter_no_labor(self) -> None:
        """Снимок не двигает материю и труд (form бонуса не даёт)."""
        world = load_scenario(SCENARIO)
        matter_before = world.total_matter()
        labor_before = {
            hid: hh.labor_days for hid, hh in world.households.items()
        }
        for _ in range(2):
            dump_tile_views(world)
            for tid in sorted(world.tiles):
                tile_view(world, tid)
        self.assertAlmostEqual(world.total_matter(), matter_before, places=9)
        for hid, hh in world.households.items():
            self.assertAlmostEqual(hh.labor_days, labor_before[hid], places=9)

    def test_month_runs_with_cap_and_matter_holds(self) -> None:
        """Тик с капом идёт, дельта 0, form не кормит."""
        world = load_scenario(SCENARIO)
        for hid in ["hh_01", "hh_02", "hh_03", "hh_04"]:
            settle_household(world, hid, EMPTY_PASTURE)
        self.assertEqual(tile_form(world, EMPTY_PASTURE), "village")
        run_month(world)
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )

    def test_root_and_thegn_flags(self) -> None:
        world = load_scenario(SCENARIO)
        self.assertTrue(is_root_seat(world, ROOT_TILE))
        self.assertFalse(is_thegn_seat(world, ROOT_TILE))
        grant_thegn(world, "hh_retinue_p1", [FIEF_LAND, DEMESNE_SEAT], ["hh_02"])
        self.assertTrue(is_thegn_seat(world, DEMESNE_SEAT))
        self.assertFalse(is_root_seat(world, DEMESNE_SEAT))


if __name__ == "__main__":
    unittest.main()
