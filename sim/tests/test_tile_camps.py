"""Новые тайлы вида: поле господина, переправа, железница, лагеря, резерв.

Вид следует из состояния, экономики не касается: лагерь — воз в пути,
переправа — брод/мост, поле господина — чистый деменский клин,
железница — топь (болотное железо: mine_iron/grow_iron). Каменоломня —
резерв (камня в экономике нет). Городов по-прежнему нет.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.tile_view import (
    FORMS,
    camp_form,
    is_crossing,
    is_lord_field,
    tile_form,
    tile_view,
)
from hillcourt.engine.tick import run_month
from hillcourt.legal.actions import send_party
from hillcourt.ontology import Pack, Stock
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"

LORD_TILE = "t_06_02"  # деменский клин холма, пустое поле
MARSH_TILE = "t_02_03"  # топь без дворов
PASTURE_TILE = "t_04_00"  # пустой выпас
ROOT_TILE = "t_01_01"


def _pack(world, pid: str, kind: str, dest: str, members: list[str]):
    """Воз в пути к клетке (только для вида; материя не движется)."""
    cargo = Stock(id=f"pack:{pid}", owner_kind="pack", owner_id=pid, amounts={})
    pack = Pack(
        id=pid,
        kind=kind,
        origin_tile_id="t_00_00",
        destination_tile_id=dest,
        route=["t_00_00", dest],
        member_ids=list(members),
        cargo=cargo,
        departed_date=world.clock.date,
        eta_date=world.clock.date.advance(world.clock.months_per_year),
        status="in_transit",
    )
    world.packs[pid] = pack
    return pack


class TestLordField(unittest.TestCase):
    """Поле господина: чистый деменский клин, не seat, без жителей."""

    def test_empty_demesne_field_is_lord_field(self) -> None:
        world = load_scenario(SCENARIO)
        tile = world.tiles[LORD_TILE]
        self.assertEqual(tile.regime_id, "demesne")
        self.assertEqual(tile.terrain, "field")
        self.assertTrue(is_lord_field(world, LORD_TILE))
        self.assertEqual(tile_form(world, LORD_TILE), "lord_field")

    def test_settled_demesne_is_homestead_not_field(self) -> None:
        """С жителем вид уже не «чистое поле», а двор."""
        from hillcourt.engine.tile_view import settle_household

        world = load_scenario(SCENARIO)
        settle_household(world, "hh_01", LORD_TILE)
        self.assertFalse(is_lord_field(world, LORD_TILE))
        self.assertEqual(tile_form(world, LORD_TILE), "single_homestead")


class TestCrossing(unittest.TestCase):
    """Переправа: брод/мост; зал корня важнее."""

    def test_ford_turns_tile_into_crossing(self) -> None:
        world = load_scenario(SCENARIO)
        world.tiles[PASTURE_TILE].ford = True
        self.assertTrue(is_crossing(world, PASTURE_TILE))
        self.assertEqual(tile_form(world, PASTURE_TILE), "crossing")

    def test_seat_beats_bridge(self) -> None:
        world = load_scenario(SCENARIO)
        world.tiles[ROOT_TILE].bridge = True
        self.assertEqual(tile_form(world, ROOT_TILE), "hall_on_hill")


class TestBogIronAndQuarry(unittest.TestCase):
    """Железница — топь; каменоломня — резерв без триггера."""

    def test_empty_marsh_is_bog_iron(self) -> None:
        world = load_scenario(SCENARIO)
        self.assertEqual(world.tiles[MARSH_TILE].terrain, "marsh")
        self.assertEqual(tile_form(world, MARSH_TILE), "bog_iron")

    def test_quarry_reserved_never_shown(self) -> None:
        self.assertIn("quarry", FORMS)
        world = load_scenario(SCENARIO)
        forms = {tile_form(world, tid) for tid in world.tiles}
        self.assertNotIn("quarry", forms)
        self.assertNotIn("city", FORMS)
        self.assertNotIn("town", FORMS)


class TestCamps(unittest.TestCase):
    """Лагеря: род воза и сила отряда; только in_transit."""

    def test_foot_pot_camp_dozor(self) -> None:
        world = load_scenario(SCENARIO)
        send_party(world, "hh_04", ["hh_04_p1"], LORD_TILE)
        self.assertEqual(tile_form(world, LORD_TILE), "foot_pot_camp")

    def test_campfire_camp_three(self) -> None:
        world = load_scenario(SCENARIO)
        send_party(world, "hh_09", ["hh_09_p1", "hh_09_p2", "hh_09_p3"], LORD_TILE)
        self.assertEqual(tile_form(world, LORD_TILE), "campfire_camp")

    def test_fortified_camp_big_host(self) -> None:
        world = load_scenario(SCENARIO)
        send_party(
            world,
            "hh_09",
            ["hh_09_p1", "hh_09_p2", "hh_09_p3", "hh_09_p4"],
            LORD_TILE,
        )
        self.assertEqual(tile_form(world, LORD_TILE), "fortified_camp")

    def test_pavilion_camp_thegn(self) -> None:
        world = load_scenario(SCENARIO)
        send_party(
            world, "hh_retinue", ["hh_retinue_p1", "hh_retinue_p2"], "t_02_01"
        )
        self.assertEqual(tile_form(world, "t_02_01"), "pavilion_camp")

    def test_wagon_camp_caravan(self) -> None:
        world = load_scenario(SCENARIO)
        _pack(world, "cv_001", "caravan", PASTURE_TILE, [])
        self.assertEqual(tile_form(world, PASTURE_TILE), "wagon_camp")

    def test_tent_camp_migrants(self) -> None:
        world = load_scenario(SCENARIO)
        _pack(world, "mv_001", "household_move", PASTURE_TILE, ["a", "b"])
        self.assertEqual(tile_form(world, PASTURE_TILE), "tent_camp")

    def test_caravan_wins_tie_deterministic(self) -> None:
        world = load_scenario(SCENARIO)
        _pack(world, "zz_party", "party", PASTURE_TILE, ["a"])
        _pack(world, "aa_cart", "caravan", PASTURE_TILE, [])
        self.assertEqual(tile_form(world, PASTURE_TILE), "wagon_camp")

    def test_arrived_pack_leaves_no_camp(self) -> None:
        world = load_scenario(SCENARIO)
        pack = _pack(world, "cv_002", "caravan", PASTURE_TILE, [])
        pack.status = "arrived"
        self.assertIsNone(camp_form(world, PASTURE_TILE))
        self.assertEqual(tile_form(world, PASTURE_TILE), "open_field")

    def test_camp_moves_no_matter(self) -> None:
        world = load_scenario(SCENARIO)
        before = world.total_matter()
        send_party(world, "hh_04", ["hh_04_p1"], LORD_TILE)
        _pack(world, "cv_003", "caravan", PASTURE_TILE, [])
        self.assertAlmostEqual(world.total_matter(), before, places=9)
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )

    def test_month_with_new_forms_conserves(self) -> None:
        world = load_scenario(SCENARIO)
        self.assertEqual(tile_form(world, LORD_TILE), "lord_field")
        self.assertEqual(tile_form(world, MARSH_TILE), "bog_iron")
        run_month(world)
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )


if __name__ == "__main__":
    unittest.main()
