"""Цикл H, пункт D: замер коридора соли — дорога и река, канон не тронут.

Только замер в тестовом мире (`v0_shire` на диске не меняется): коридор
соли `find_path`-днями до/после дороги (`work_road` в тестовом мире) и
`send_river` с плотом (плот — craft/переводом в тесте, не из воздуха) по
речному коридору. Честное ROI: дни/месяцы до/после, цена log+трудодни.
Итог замера: месяцы не двигаются — не окупилось (так и записано).
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy.labor import apply_recipe
from hillcourt.engine.path import find_path, travel_days, travel_months
from hillcourt.engine.river import has_vessel, route_has_water, send_river_pack
from hillcourt.engine.roadworks import (
    MATERIAL_AMOUNT,
    MATERIAL_GOOD,
    MONTHLY_LABOR_CAP,
    REQUIRED_LABOR_DAYS,
    start_work,
)
from hillcourt.engine.tick import run_month
from hillcourt.news.propagation import make_report
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SHIRE = ROOT / "design" / "scenarios" / "v0_shire.yml"
SEED = 1729
SALT_ORIGIN = "t_08_05"
SALT_DEST = "t_01_01"
# Вторая клетка соляного коридора (salt_flat 2.0 → дорога 0.8): origin в цену
# пути не входит, поэтому строится не origin, а следующий шаг. В книге корня.
ROAD_TILE = "t_08_06"
RIVER_HOUSEHOLD = "hh_retinue"
RIVER_DEST = "t_06_03"


def _seed(world, stock_id: str, good: str, amount: float, rule_id: str) -> None:
    """Честный приход в тестовом мире: `Ledger.external_in` по правилу."""
    world.ledger.external_in(
        world.get_stock(stock_id), good, amount, "test_seed", rule_id,
        world.clock.date,
    )


def _know_all(world) -> None:
    for tile_id in sorted(world.tiles):
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


def _craft_raft_for(world, household_id: str) -> None:
    household = world.households[household_id]
    stock = world.get_stock(household.stock_id)
    if stock.amounts.get("raft", 0.0) < 1.0 - 1e-9:
        _seed(world, household.stock_id, "log", 3.0, "grow_log")
        household.labor_days = max(household.labor_days, 20.0)
        tile = world.tiles[household.current_tile_id]
        apply_recipe(
            world, household, tile, world.catalogs.recipes["craft_raft"],
            1.0, world.clock.date,
        )


class TestSaltRoadCorridorRoi(unittest.TestCase):
    """Дорога на соляном коридоре: дни до/после, цена, честный вердикт."""

    def test_road_days_before_after_and_price(self) -> None:
        world = load_scenario(SHIRE, seed=SEED)
        before = find_path(world, SALT_ORIGIN, SALT_DEST, "caravan", known=None)
        self.assertIsNotNone(before)
        days_before = before[1]
        months_before = travel_months(days_before)
        _seed(world, "settlement:hill_court", MATERIAL_GOOD,
              MATERIAL_AMOUNT["road"], "grow_log")
        start_work(world, ROAD_TILE, "road")
        for _ in range(8):
            run_month(world)
            if world.tiles[ROAD_TILE].road:
                break
        self.assertTrue(world.tiles[ROAD_TILE].road)
        after = find_path(world, SALT_ORIGIN, SALT_DEST, "caravan", known=None)
        self.assertIsNotNone(after)
        days_after = after[1]
        months_after = travel_months(days_after)
        self.assertLess(days_after, days_before)
        price_log = MATERIAL_AMOUNT["road"]
        price_labor = REQUIRED_LABOR_DAYS["road"]
        print(
            f"road ROI: days {round(days_before, 2)} -> {round(days_after, 2)} "
            f"(save {round(days_before - days_after, 2)}), "
            f"months {months_before} -> {months_after}, "
            f"price log {price_log} + labor {price_labor}d "
            f"(cap {MONTHLY_LABOR_CAP}d/mo, ADR 0067) -> cell ≤1mo; "
            f"months unchanged -> schedule payback NONE"
        )
        self.assertEqual(
            months_after, months_before,
            "Месяцы пути изменились: дорога окупилась бы расписанием",
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


class TestRiverCorridorRoi(unittest.TestCase):
    """Река с плотом: дни против суши тем же коридором, цена, вердикт."""

    def test_river_with_crafted_raft_vs_land(self) -> None:
        world = load_scenario(SHIRE, seed=SEED)
        _know_all(world)
        _craft_raft_for(world, RIVER_HOUSEHOLD)
        self.assertTrue(has_vessel(world, RIVER_HOUSEHOLD, "water_raft"))
        _seed(
            world, world.households[RIVER_HOUSEHOLD].stock_id, "grain", 10.0,
            "grow_grain",
        )
        pack = send_river_pack(
            world, RIVER_HOUSEHOLD, [], RIVER_DEST, "water_raft", {"grain": 4.0}
        )
        self.assertTrue(route_has_water(world, pack.route))
        record = next(
            r for r in world.player_actions if r.get("action") == "send_river"
        )
        river_days = int(record["days"])
        river_hours = float(record["travel_hours"])
        river_months = int(record["months"])
        land = find_path(
            world,
            world.households[RIVER_HOUSEHOLD].current_tile_id,
            RIVER_DEST, "caravan", known=None,
        )
        self.assertIsNotNone(land)
        land_hours = land[1]
        land_days = travel_days(land_hours)
        print(
            f"river ROI: river {round(river_hours, 2)}h/{river_days}d/{river_months}mo "
            f"vs land {round(land_hours, 2)}h/{land_days}d, "
            f"price log 3.0 + labor 12d (craft_raft); "
            f"river faster in hours, equal in days -> verdict reviewed"
        )
        # В часах плот по воде быстрее сухопутного коридора; в сутках (округление
        # до целых) разница пока не видна — вердикт зонда пересмотрен честно.
        self.assertLess(
            river_hours, land_hours, "Плот медленнее суши: вердикт бы пересмотреть"
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
