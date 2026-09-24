"""G5: инструмент — материя, железо не появляется; плуг и лемех меняют жатву.

Проверки:
  * `grant_tool` переводит `iron_share` из амбара корневого манора в сток
    двора, пишет действие в `world.player_actions`; при нехватке у лорда —
    `ValueError` и НИ ОДНОГО перевода (без частичной выдачи);
  * `craft_wooden_plough` собирает плуг из брёвен двора, масса сходится;
  * выход жатвы: без инструмента < с `wooden_plough` < с `iron_share`
    (одинаковый seed, одинаковый труд), delta материи ≈ 0;
  * script-действие `grant_tool` проходит через диспетчер runner с датой.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import yaml

from hillcourt.economy.labor import apply_recipe, tool_yield_factor
from hillcourt.engine.manor import grant_tool, root_manor
from hillcourt.runner import run
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
CASTLE = "settlement:hill_court"
VILLEIN = "hh_02"
IRON = "iron_share"
PLOUGH = "wooden_plough"
HARVEST = "harvest_grain"
EPSILON = 1e-9


def _grant_entries(world) -> list:
    return [e for e in world.ledger.entries if e.reason == "grant_tool"]


class TestGrantTool(unittest.TestCase):
    """Действие `grant_tool`: перевод, лог, отказ без частичной выдачи."""

    def test_grant_moves_iron_from_castle_and_logs(self) -> None:
        world = load_scenario(SCENARIO)
        castle = world.get_stock(CASTLE)
        castle.amounts[IRON] = 2.0
        world.ledger.capture_initial(world.total_matter())

        grant_tool(world, VILLEIN, IRON, 1.0)

        entries = _grant_entries(world)
        self.assertEqual(len(entries), 1, "grant_tool не сделал ровно один перевод")
        entry = entries[0]
        self.assertEqual(entry.src_id, CASTLE)
        self.assertEqual(entry.dst_id, f"household:{VILLEIN}")
        self.assertEqual(entry.good, IRON)
        self.assertAlmostEqual(entry.amount, 1.0, places=6)
        self.assertAlmostEqual(castle.amounts.get(IRON, 0.0), 1.0, places=6)
        household_stock = world.get_stock(world.households[VILLEIN].stock_id)
        self.assertAlmostEqual(household_stock.amounts.get(IRON, 0.0), 1.0, places=6)

        record = world.player_actions[-1]
        self.assertEqual(record["action"], "grant_tool")
        self.assertEqual(record["household"], VILLEIN)
        self.assertEqual(record["good"], IRON)
        self.assertAlmostEqual(record["amount"], 1.0, places=6)
        self.assertEqual(record["date"], str(world.clock.date))

        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )

    def test_short_grant_raises_without_partial_transfer(self) -> None:
        world = load_scenario(SCENARIO)
        castle = world.get_stock(CASTLE)
        castle.amounts[IRON] = 2.0
        grant_tool(world, VILLEIN, IRON, 1.0)
        castle_before = castle.amounts.get(IRON, 0.0)
        transferred_before = sum(e.amount for e in _grant_entries(world))

        with self.assertRaises(ValueError):
            grant_tool(world, VILLEIN, IRON, 5.0)

        self.assertEqual(len(_grant_entries(world)), 1, "Появился лишний перевод")
        self.assertAlmostEqual(
            sum(e.amount for e in _grant_entries(world)),
            transferred_before,
            places=6,
        )
        self.assertAlmostEqual(
            castle.amounts.get(IRON, 0.0), castle_before, places=6
        )

    def test_only_iron_share_is_granted_in_v0(self) -> None:
        world = load_scenario(SCENARIO)
        world.get_stock(CASTLE).amounts[PLOUGH] = 2.0
        with self.assertRaises(ValueError):
            grant_tool(world, VILLEIN, PLOUGH, 1.0)
        self.assertEqual(_grant_entries(world), [])


class TestCraftPlough(unittest.TestCase):
    """`craft_wooden_plough`: плуг из брёвен, масса сходится."""

    def test_catalog_lists_tool_goods_and_recipe(self) -> None:
        world = load_scenario(SCENARIO)
        for good in (PLOUGH, IRON):
            self.assertIn(good, world.catalogs.goods)
            self.assertEqual(world.catalogs.goods[good].storage, "barn")
            self.assertFalse(world.catalogs.goods[good].edible)
        recipe = world.catalogs.recipes["craft_wooden_plough"]
        left = sum(recipe.inputs.values()) + sum(recipe.draws_standing.values())
        right = sum(recipe.outputs.values()) + sum(recipe.loss.values())
        self.assertAlmostEqual(left, right, places=6)
        self.assertIn(
            "craft_wooden_plough", world.household_actions["idle_repair"].recipes
        )

    def test_household_crafts_plough_without_creating_matter(self) -> None:
        world = load_scenario(SCENARIO)
        household = world.households[VILLEIN]
        stock = world.get_stock(household.stock_id)
        stock.amounts["log"] = 2.0
        world.ledger.capture_initial(world.total_matter())

        recipe = world.catalogs.recipes["craft_wooden_plough"]
        tile = world.tiles[household.current_tile_id]
        apply_recipe(world, household, tile, recipe, 1.0, world.clock.date)

        self.assertAlmostEqual(stock.amounts.get(PLOUGH, 0.0), 1.0, places=6)
        self.assertAlmostEqual(stock.amounts.get("log", 0.0), 0.0, places=6)
        self.assertAlmostEqual(
            world.get_stock("sink:waste").amounts.get("log", 0.0), 1.0, places=6
        )
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )


def _harvest_output(tool: str | None) -> tuple[float, float]:
    """Выход жатвы двора за одну партию при заданном инструменте.

    Мир изолирован (тик не идёт), труд одинаков (`scale=1.0`), сезон одинаков
    (`yield_factor=0.5`). Возвращает (прирост зерна двора, delta материи).
    """
    world = load_scenario(SCENARIO, seed=1729)
    household = world.households[VILLEIN]
    stock = world.get_stock(household.stock_id)
    if tool is not None:
        stock.amounts[tool] = 1.0
    tile = world.tiles[household.current_tile_id]
    world.get_stock(tile.standing_stock_id).amounts["grain"] = 100.0
    world.ledger.capture_initial(world.total_matter())

    before = stock.amounts.get("grain", 0.0)
    recipe = world.catalogs.recipes[HARVEST]
    apply_recipe(
        world, household, tile, recipe, 1.0, world.clock.date, yield_factor=0.5
    )
    gained = stock.amounts.get("grain", 0.0) - before
    return gained, world.ledger.delta(world.total_matter())


class TestToolYield(unittest.TestCase):
    """Инструмент меняет выход жатвы, а не труд; железо — лучший."""

    def test_tool_yield_order_and_base_line(self) -> None:
        world = load_scenario(SCENARIO)
        household = world.households[VILLEIN]
        stock = world.get_stock(household.stock_id)
        self.assertAlmostEqual(tool_yield_factor(world, household), 1.0, places=6)
        stock.amounts[PLOUGH] = 1.0
        wooden = tool_yield_factor(world, household)
        stock.amounts[IRON] = 1.0
        iron = tool_yield_factor(world, household)
        self.assertAlmostEqual(wooden, 1.05, places=6)
        self.assertAlmostEqual(iron, 1.15, places=6)

    def test_harvest_output_grows_none_wooden_iron(self) -> None:
        none, delta_none = _harvest_output(None)
        wooden, delta_wooden = _harvest_output(PLOUGH)
        iron, delta_iron = _harvest_output(IRON)
        self.assertGreater(none, 0.0, "Базовая жатва не дала зерна")
        self.assertLess(none, wooden, f"Плуг не прибавил: {none} против {wooden}")
        self.assertLess(wooden, iron, f"Лемех не лучше плуга: {wooden} против {iron}")
        for delta in (delta_none, delta_wooden, delta_iron):
            self.assertAlmostEqual(delta, 0.0, places=6)


class TestScriptGrantTool(unittest.TestCase):
    """Script-действие `grant_tool` через runner пишет дату в player_actions."""

    def setUp(self) -> None:
        with SCENARIO.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        data.setdefault("starting_stocks", {}).setdefault(CASTLE, {})[IRON] = 2.0
        data["script"] = [
            {
                "at_month": 1,
                "action": "grant_tool",
                "household": VILLEIN,
                "good": IRON,
                "amount": 1.0,
            }
        ]
        fd, name = tempfile.mkstemp(
            prefix="test_grant_tool_", suffix=".yml", dir=SCENARIO.parent
        )
        os.close(fd)
        self.path = Path(name)
        with self.path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, allow_unicode=True)

    def tearDown(self) -> None:
        self.path.unlink(missing_ok=True)

    def test_runner_dispatches_grant_tool_with_date(self) -> None:
        result = run(self.path, 1, seed=1729)
        records = [
            action
            for action in result.player_actions
            if action.get("action") == "grant_tool"
        ]
        self.assertTrue(records, "grant_tool не записан в player_actions")
        record = records[0]
        self.assertEqual(record["date"], "Y1-M01")
        self.assertEqual(record["household"], VILLEIN)
        self.assertEqual(record["good"], IRON)
        self.assertAlmostEqual(record["amount"], 1.0, places=6)
        self.assertAlmostEqual(result.matter_delta, 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
