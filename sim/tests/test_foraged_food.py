"""Лесные и полевые дары: путь от клетки до еды (ADR 0099)."""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy.labor import work_month
from hillcourt.engine.tick import phase_consume
from hillcourt.ontology import Right
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
FORAGED = ("roots", "greens", "mushrooms", "berries")


class TestForagedFood(unittest.TestCase):
    """Сбор стоит труда, зимой пуст, найденное едят и хранят."""

    def _world(self, month: int, goods: tuple[str, ...] = FORAGED):
        """Стенд сбора: у каждого товара своя клетка и своё право держания.

        `goods` ограничивает, что подсеивается: месяц двора — один, а рецепты сбора
        конкурируют за труд и стоячую материю, поэтому «собрать все четыре сразу» не
        законно, а законно «собрать то, до чего дошла очередь».
        """
        world = load_scenario(SCENARIO, seed=1729)
        household = world.households["hh_01"]
        world.households = {household.id: household}
        feeding_regime = next(
            regime.id
            for regime in world.catalogs.land_regimes.values()
            if regime.feeds_household
        )
        targets = {}
        for good, terrain in (
            ("roots", "field"),
            ("greens", "pasture"),
            ("mushrooms", "forest"),
            ("berries", "heath"),
        ):
            tile = next(
                tile
                for tile in sorted(world.tiles.values(), key=lambda item: item.id)
                if tile.terrain == terrain and tile.id not in targets.values()
            )
            tile.regime_id = feeding_regime
            right_id = f"right_test_forage_{good}"
            world.rights[right_id] = Right(
                id=right_id,
                holder_household_id=household.id,
                tile_id=tile.id,
                kind="tenure",
                granted_date=world.clock.date,
                rent_share=0.0,
            )
            targets[good] = tile.id
            if good in goods:
                world.get_stock(tile.standing_stock_id).amounts[good] = 4.0
        household.main_action = "forage_adjacent"
        household.minor_action = "idle_repair"
        world.clock.month = month
        return world, household, targets

    def test_goods_have_complete_food_path(self) -> None:
        world, household, _ = self._world(8)
        stock = world.get_stock(household.stock_id)
        for good in FORAGED:
            rule = world.catalogs.goods[good]
            self.assertTrue(rule.edible)
            self.assertEqual(rule.storage, "cellar")
            self.assertGreater(rule.nutrition, 0.0)
            self.assertLess(rule.nutrition, world.catalogs.goods["grain"].nutrition)
            self.assertLess(rule.nutrition, world.catalogs.goods["milk"].nutrition)
            self.assertIn(good, world.needs.edible_order)

    def test_gathering_costs_labor_and_matter_holds(self) -> None:
        """Каждый дар собирается за труд, который стоит по каталогу.

        Проверяется по одному товару: месяц двора один, и рецепты сбора конкурируют
        за труд и стоячую материю — «собрать все четыре сразу» невозможно и не
        требуется. Число трудодней не выдумано: месяц равен `labor_days` того
        рецепта, который собираем.
        """
        for good in FORAGED:
            with self.subTest(good=good):
                world, household, _targets = self._world(8, goods=(good,))
                recipe = next(
                    world.catalogs.recipes[rid]
                    for rid in world.household_actions["forage_adjacent"].recipes
                    if good in world.catalogs.recipes[rid].draws_standing
                )
                household.labor_days = float(recipe.labor_days)
                stock = world.get_stock(household.stock_id)
                world.ledger.capture_initial(world.total_matter())

                work_month(world, world.clock.date)

                self.assertGreater(
                    stock.amounts.get(good, 0.0), 0.0, f"{good}: сбор пуст"
                )
                self.assertAlmostEqual(
                    household.labor_days, 0.0, places=6, msg="Месяц не израсходован"
                )
                self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_labor_shortfall_leaves_the_queue_short(self) -> None:
        """Мало труда — мало сбора: месяц делится по порядку, а не «всем по немного».

        Тот же стенд, но месяц обрезан до одной партии: собирается только первая
        по порядку (`id`) рецептура, остальные не начинаются. Это закон труда
        (ADR 0106), а не дыра каталога.
        """
        world, household, _targets = self._world(8)
        first = world.catalogs.recipes["gather_mushrooms"]
        household.labor_days = float(first.labor_days)
        stock = world.get_stock(household.stock_id)

        work_month(world, world.clock.date)

        self.assertGreater(stock.amounts.get("mushrooms", 0.0), 0.0, "Первая по порядку не собрана")
        for good in ("roots", "greens", "berries"):
            self.assertEqual(
                stock.amounts.get(good, 0.0), 0.0,
                f"{good}: собран без трудодней",
            )

    def test_winter_gathering_is_empty_and_deterministic(self) -> None:
        first, first_household, _ = self._world(1)
        second, second_household, _ = self._world(1)
        for world in (first, second):
            for tile in world.tiles.values():
                stock = world.get_stock(tile.standing_stock_id)
                for good in FORAGED:
                    stock.amounts.pop(good, None)
        first.ledger.capture_initial(first.total_matter())
        second.ledger.capture_initial(second.total_matter())
        work_month(first, first.clock.date)
        work_month(second, second.clock.date)
        first_stock = first.get_stock(first_household.stock_id)
        second_stock = second.get_stock(second_household.stock_id)
        for good in FORAGED:
            self.assertAlmostEqual(first_stock.amounts.get(good, 0.0), 0.0, places=6)
        self.assertEqual(dict(first_stock.amounts), dict(second_stock.amounts))
        self.assertAlmostEqual(first.ledger.delta(first.total_matter()), 0.0, places=6)
        self.assertAlmostEqual(second.ledger.delta(second.total_matter()), 0.0, places=6)

    def test_foraged_food_is_eaten(self) -> None:
        world, household, _ = self._world(8)
        stock = world.get_stock(household.stock_id)
        stock.amounts.clear()
        for good in FORAGED:
            stock.amounts[good] = 1.0
        world.ledger.capture_initial(world.total_matter())
        phase_consume(world)
        for good in FORAGED:
            self.assertLess(stock.amounts.get(good, 1.0), 1.0, f"{good}: не съеден")
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
