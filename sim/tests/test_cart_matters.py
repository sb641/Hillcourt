"""E-minimal: телега — материя и множитель обоза, а не флаг из воздуха.

`craft_cart` переводит брёвна и дрова двора в благо `cart` (потери — в
`sink:waste`), двор вправе сделать телегу мелким действием `idle_repair`.
Правило обоза с `cart_bonus` считает телегу у поселения-origin (дворы плюс
склад): с телегой ёмкость ×1.5 и дни пути как есть, без неё ёмкость ×0.6,
дни ×1.5 (округление вверх). Материя при этом сохраняется.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy import caravan
from hillcourt.economy.labor import apply_recipe
from hillcourt.engine.tick import run_month
from hillcourt.ontology import SpawnRule
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_two_settlements.yml"
MONTHS = 36
HILL = "hill_court"
HILL_STORES = "settlement:hill_court"
GRAIN_RULE = "caravan_grain_to_ash"
SALT_RULE = "caravan_visit"


def _run_months(world, months: int = MONTHS) -> None:
    for _ in range(months):
        run_month(world)


def _hill_stocks(world) -> list:
    """Стоки origin холма: живые дворы поселения плюс склад."""
    stocks = [
        world.get_stock(hh.stock_id)
        for _, hh in sorted(world.households.items())
        if hh.settlement_id == HILL
    ]
    stocks.append(world.get_stock(HILL_STORES))
    return stocks


def _set_hill_cart(world, amount: float) -> None:
    """Положить телегу холму или убрать её отовсюду — без правки сценария."""
    for stock in _hill_stocks(world):
        if amount > 0.0:
            stock.amounts["cart"] = amount
        else:
            stock.amounts.pop("cart", None)


def _loaded_grain(world) -> float:
    """Сумма погрузки зерна в возы (`caravan_load`) за прогон."""
    return sum(
        entry.amount
        for entry in world.ledger.entries
        if entry.kind == "transfer"
        and entry.good == "grain"
        and entry.reason == "caravan_load"
    )


class TestCartMatters(unittest.TestCase):
    """Телега — рецепт, множители ёмкости/пути и 36-месячный прогон обоза."""

    def test_craft_cart_spends_logs_and_conserves_matter(self) -> None:
        world = load_scenario(SCENARIO)
        household = world.households["hh_court"]
        stock = world.get_stock(household.stock_id)
        stock.amounts["log"] = 4.0
        stock.amounts["firewood"] = 1.0
        world.ledger.capture_initial(world.total_matter())

        recipe = world.catalogs.recipes["craft_cart"]
        tile = world.tiles[household.current_tile_id]
        apply_recipe(world, household, tile, recipe, 1.0, world.clock.date)

        self.assertAlmostEqual(stock.amounts.get("cart", 0.0), 1.0, places=6)
        self.assertAlmostEqual(stock.amounts.get("log", 0.0), 0.0, places=6)
        self.assertAlmostEqual(stock.amounts.get("firewood", 0.0), 0.0, places=6)
        self.assertAlmostEqual(
            world.get_stock("sink:waste").amounts.get("log", 0.0), 4.0, places=6
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_catalog_lists_cart_recipe_and_action(self) -> None:
        world = load_scenario(SCENARIO)
        self.assertIn("cart", world.catalogs.goods)
        self.assertEqual(world.catalogs.goods["cart"].storage, "barn")
        recipe = world.catalogs.recipes["craft_cart"]
        left = sum(recipe.inputs.values()) + sum(recipe.draws_standing.values())
        right = sum(recipe.outputs.values()) + sum(recipe.loss.values())
        self.assertAlmostEqual(left, right, places=6)
        self.assertIn("craft_cart", world.household_actions["idle_repair"].recipes)

    def test_capacity_and_days_multipliers(self) -> None:
        world = load_scenario(SCENARIO)
        rule = world.catalogs.spawn_rules[GRAIN_RULE]
        self.assertTrue(rule.params.get("cart_bonus"), "У правила зерна нет cart_bonus")
        base = float(rule.params["cargo_amount"])
        self.assertAlmostEqual(
            caravan.caravan_capacity(rule, True), base * 1.5, places=6
        )
        self.assertAlmostEqual(
            caravan.caravan_capacity(rule, False), base * 0.6, places=6
        )
        self.assertAlmostEqual(caravan.caravan_days(7, True), 7.0, places=6)
        self.assertAlmostEqual(caravan.caravan_days(7, False), 11.0, places=6)
        self.assertAlmostEqual(caravan.caravan_days(4, False), 6.0, places=6)
        self.assertTrue(
            world.catalogs.spawn_rules[SALT_RULE].params.get("cart_bonus"),
            "У соляного правила нет cart_bonus",
        )

        plain = SpawnRule(
            id="caravan_plain",
            name="Обоз без тележного правила",
            target="pack",
            kind="calendric",
            params={"kind": "caravan", "cargo": "grain", "cargo_amount": 2.0},
        )
        self.assertAlmostEqual(caravan.caravan_capacity(plain, True), 2.0, places=6)
        self.assertAlmostEqual(caravan.caravan_capacity(plain, False), 2.0, places=6)

    def test_cart_boosts_grain_loading_over_36_months(self) -> None:
        with_cart = load_scenario(SCENARIO)
        _set_hill_cart(with_cart, 1.0)
        self.assertTrue(caravan.origin_has_cart(with_cart, HILL))
        with_cart.ledger.capture_initial(with_cart.total_matter())
        _run_months(with_cart)

        without_cart = load_scenario(SCENARIO)
        _set_hill_cart(without_cart, 0.0)
        self.assertFalse(caravan.origin_has_cart(without_cart, HILL))
        without_cart.ledger.capture_initial(without_cart.total_matter())
        _run_months(without_cart)

        loaded_with = _loaded_grain(with_cart)
        loaded_without = _loaded_grain(without_cart)
        self.assertGreater(loaded_with, 0.0, "С телегой зерно не грузилось")
        self.assertGreater(loaded_without, 0.0, "Без телеги зерно не грузилось")
        self.assertGreater(
            loaded_with,
            loaded_without,
            f"Телега не увеличила загрузку: {loaded_with} против {loaded_without}",
        )
        self.assertAlmostEqual(
            with_cart.ledger.delta(with_cart.total_matter()), 0.0, places=6
        )
        self.assertAlmostEqual(
            without_cart.ledger.delta(without_cart.total_matter()), 0.0, places=6
        )


if __name__ == "__main__":
    unittest.main()
