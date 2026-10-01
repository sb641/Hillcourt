"""Солончак — производственная клетка соли, а не пашня (ADR 0108, 0124).

Соляная ванна не сеет зерно: жатва её не берёт по рельефу
(`harvest_grain.requires_terrain = [field]`), и клетка с режимом «кормит» без
съедобного рецепта поля наделом двора не считается. Остаток труда солевара,
покрывшего месячную нужду, уходит в выварку, а не в лишнюю жатву (ADR 0095).
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy.labor import (
    _feeding_tiles,
    _tiles_for_recipe,
    own_tiles,
    tile_feeds_household,
    work_month,
)
from hillcourt.engine.tick import run_month
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SALTBOILER = "hh_salt_01"
VILLEIN = "hh_02"
COTTER = "hh_03"
MONTHS = 12


def _year():
    world = load_scenario(SCENARIO, seed=1729)
    world.ledger.capture_initial(world.total_matter())
    for _ in range(MONTHS):
        run_month(world)
    return world


def _entries(world, reason, good, dst=None):
    return [
        e
        for e in world.ledger.entries
        if e.reason == reason
        and e.good == good
        and (dst is None or e.dst_id == dst)
    ]


class TestSaltPanNotArable(unittest.TestCase):
    """Соляная ванна кормит соль, а не зерном; солевар варит, виллан пашет."""

    def test_salt_pan_is_not_arable_and_not_a_holding(self) -> None:
        world = load_scenario(SCENARIO, seed=1729)
        pans = [t for t in world.tiles.values() if t.terrain == "salt_flat"]
        self.assertTrue(pans, "В мире нет солончака")
        for pan in pans:
            with self.subTest(tile=pan.id):
                self.assertFalse(
                    tile_feeds_household(world, pan),
                    "Солончак не сеет зерно: он производственная клетка",
                )
        household = world.households[SALTBOILER]
        harvest = world.catalogs.recipes["harvest_grain"]
        self.assertNotIn("salt_flat", harvest.requires_terrain)
        for tile in _tiles_for_recipe(world, household, harvest):
            with self.subTest(tile=tile.id):
                self.assertNotEqual(tile.terrain, "salt_flat", "Жатва по солончаку")
        feeding = {tile.id for tile in _feeding_tiles(world, household)}
        for tile in own_tiles(world, household):
            if tile.terrain == "salt_flat":
                self.assertNotIn(tile.id, feeding, "Солончак числится наделом")

    def test_boiling_runs_and_salt_appears(self) -> None:
        world = _year()
        boiled = _entries(world, "boil_salt", "salt")
        self.assertTrue(boiled, "Выварки соли не было ни одной партии")
        self.assertGreater(sum(e.amount for e in boiled), 0.0)
        salt = sum(
            stock.amounts.get("salt", 0.0) for stock in world.stocks.values()
        )
        self.assertGreater(salt, 0.0, "Соли нет ни в одном стоке мира")

    def test_tenant_and_cotter_still_harvest_their_holdings(self) -> None:
        world = _year()
        for household_id, floor in ((VILLEIN, 12), (COTTER, 12)):
            with self.subTest(household=household_id):
                batches = _entries(
                    world,
                    "harvest_grain",
                    "grain",
                    dst=world.households[household_id].stock_id,
                )
                self.assertGreaterEqual(
                    len(batches), floor, "Виллан/коттер перестал собирать зерно"
                )
                holding = [
                    tile
                    for tile in own_tiles(world, world.households[household_id])
                    if tile.terrain == "field"
                ]
                self.assertTrue(holding, "У двора нет пашни")

    def test_saltboiler_stops_farming_beyond_its_need(self) -> None:
        world = _year()
        stock = world.get_stock(world.households[SALTBOILER].stock_id)
        need = 4 * 1.0 + 2 * 0.7
        self.assertLessEqual(
            sum(stock.amounts.values()), need * 12.0,
            "Солевар по-прежнему забивает амбар зерном вместо соли",
        )
        self.assertTrue(_entries(world, "boil_salt", "salt"), "Выварки нет")

    def test_matter_delta_is_zero(self) -> None:
        world = _year()
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_single_month_labour_splits_between_food_and_salt(self) -> None:
        world = load_scenario(SCENARIO, seed=1729)
        household = world.households[SALTBOILER]
        world.households = {household.id: household}
        world.ledger.capture_initial(world.total_matter())
        for _ in range(3):
            run_month(world)
        boiled = _entries(world, "boil_salt", "salt")
        harvested = _entries(
            world, "harvest_grain", "grain", dst=household.stock_id
        )
        self.assertTrue(harvested or boiled, "Ни жатвы, ни выварки")
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
