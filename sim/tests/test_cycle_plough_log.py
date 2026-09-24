"""Цикл H, пункт A: бревно в обороте — деревянный плуг достижим легально.

Канон: `craft_wooden_plough` требует `log` 2.0, но стартового `log` не было
ни у кого (log 0), а `cut_wood` в `work_plot` не входит — автономно двор
жжёт бревно в дрова (`split_logs`, наблюдается в M12). Поэтому минимально:
стартовый `log` 2.0 у одного двора вне караванных поселений (`hh_04`,
`fs_04`) — ровно на один плуг (на плот/телегу/лодку 2.0 не хватает, так что
конкуренты из `idle_repair` недоступны). Тест собирает плуг легальным
рецептом из стартового стока (перевод/рецепт, не из воздуха) и держит окно
36 мес: проводки в леджере, дельта 0.

Доступ `cut_wood` в `work_plot` НЕ правится — числа предложены в отчёте.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy.labor import apply_recipe
from hillcourt.engine.tick import run_month
from hillcourt.runner import _apply_script_entry
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SHIRE = ROOT / "design" / "scenarios" / "v0_shire.yml"
SEED = 1729
HOUSEHOLD = "hh_04"
CARAVAN_SETTLEMENTS = {"salt_village", "hill_court", "ash_village"}


def _run_months(world, months: int) -> None:
    for month_index in range(1, months + 1):
        for entry in world.script:
            if int(entry.get("at_month", 0)) == month_index:
                _apply_script_entry(world, entry)
        run_month(world)


class TestPloughLogStart(unittest.TestCase):
    """Стартовый запас: ровно 2.0 бревна у одного двора вне караванов."""

    def setUp(self) -> None:
        self.world = load_scenario(SHIRE, seed=SEED)

    def test_log_outside_caravan_settlements(self) -> None:
        household = self.world.households[HOUSEHOLD]
        self.assertNotIn(
            household.settlement_id,
            CARAVAN_SETTLEMENTS,
            f"{HOUSEHOLD} стоит в караванном поселении",
        )
        stock = self.world.get_stock(f"household:{HOUSEHOLD}")
        self.assertAlmostEqual(stock.amounts.get("log", 0.0), 2.0, places=6)

    def test_log_is_exactly_one_plough(self) -> None:
        """2.0 — минимум: плуг (2.0) доступен, плот (3.0) и телега (4.0) — нет."""
        recipe = self.world.catalogs.recipes["craft_wooden_plough"]
        self.assertAlmostEqual(recipe.inputs.get("log", 0.0), 2.0, places=6)
        for rid in ("craft_raft", "craft_cart", "craft_boat"):
            need = self.world.catalogs.recipes[rid].inputs.get("log", 0.0)
            self.assertGreater(need, 2.0, f"{rid} конкурирует за стартовый запас")


class TestPloughCraftIn36Months(unittest.TestCase):
    """За 36 мес ≥1 плуг собран переводом/рецептом из стартового бревна."""

    def test_craft_from_starting_stock_and_hold_36_months(self) -> None:
        world = load_scenario(SHIRE, seed=SEED)
        household = world.households[HOUSEHOLD]
        stock = world.get_stock(household.stock_id)
        tile = world.tiles[household.current_tile_id]
        household.labor_days = 20.0
        recipe = world.catalogs.recipes["craft_wooden_plough"]
        apply_recipe(world, household, tile, recipe, 1.0, world.clock.date)
        self.assertAlmostEqual(
            stock.amounts.get("wooden_plough", 0.0), 1.0, places=6
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)
        _run_months(world, 36)
        wires = [
            e
            for e in world.ledger.entries
            if e.reason == "craft_wooden_plough"
        ]
        self.assertTrue(wires, "Нет проводок сборки плуга за 36 мес")
        by_kind = {(e.kind, e.good) for e in wires}
        self.assertIn(("transfer", "log"), by_kind)
        self.assertIn(("process", "wooden_plough"), by_kind)
        total = sum(
            e.amount for e in wires if e.kind == "process" and e.good == "wooden_plough"
        )
        self.assertGreaterEqual(total, 1.0, "Меньше одного плуга за 36 мес")
        print(
            f"plough wires: {[(e.kind, e.reason, e.good, round(e.amount, 2), str(e.date)) for e in wires]}"
        )
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )


if __name__ == "__main__":
    unittest.main()
