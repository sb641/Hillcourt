"""Соляная цепочка: топливо → рапа (правило) → соль (рецепт) → обоз (Pack).

Сценарий `v0_barony_100.yml` (зона Implementer-2) читается, но не меняется:
стенд подкладывает **данные** (стартовый торф в солеварных дворах) ту же правку,
которую владелец должен развести в сценарии, и проверяет честную цепочку:
выварка из стоячей рапы (правило `grow_brine`, И-1) → соль → `caravan_load` →
`caravan_unload`, дельта 0, детерминизм.
"""

from __future__ import annotations

import unittest

from hillcourt.economy import caravan as C
from hillcourt.economy.decisions import allowed_action_ids
from hillcourt.economy.labor import _tiles_for_recipe, apply_recipe, own_tiles, work_month
from hillcourt.engine.tick import run_month
from hillcourt.legal.calendar import seasonal_labor_days

try:
    from .salt_test_support import clone_salt_world, load_salt_template
except ImportError:
    from salt_test_support import clone_salt_world, load_salt_template

MONTHS = 12
PEAT_PER_SALTHOLDER = 6.0


def _stand(template, *, fuel_cells: bool = True):
    world = clone_salt_world(template)
    settlement = world.settlements["salt_village"]
    for hid in settlement.household_ids:
        household = world.households[hid]
        world.get_stock(f"household:{hid}").amounts["peat"] = PEAT_PER_SALTHOLDER
        household.main_action = "work_plot"
        household.minor_action = "idle_repair"
        household.labor_days = 400.0
    if not fuel_cells:
        settlement.works_tiles = [
            tid
            for tid in settlement.works_tiles
            if world.tiles[tid].terrain not in ("marsh", "forest")
        ]
        world.catalogs.spawn_rules.pop("grow_peat", None)
    world.ledger.capture_initial(world.total_matter())
    return world


class TestSaltChain(unittest.TestCase):
    """Топливо + рапа → соль → обоз; без соли и рапы в стартовых стоках сценария."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.template = load_salt_template()

    def test_scenario_has_no_free_salt(self) -> None:
        world = clone_salt_world(self.template)
        self.assertEqual(C.salt_in_village(world), 0.0, "Соль выдана на старте")
        self.assertEqual(
            sum(
                1
                for e in (world.catalogs.spawn_rules.values())
                if e.params.get("good") == "salt"
            ),
            0,
            "Правило появления соли — соль должна идти рецептом",
        )

    def test_salt_produced_and_shipped(self) -> None:
        world = _stand(self.template)
        settlement = world.settlements["salt_village"]
        household_id = settlement.household_ids[0]
        household = world.households[household_id]
        tile = next(
            world.tiles[tid] for tid in settlement.works_tiles if world.tiles[tid].terrain == "salt_flat"
        )
        recipe = world.catalogs.recipes["boil_salt"]
        world.ledger.external_in(
            world.get_stock(tile.standing_stock_id),
            "brine",
            3.0,
            "test_seed_brine",
            None,
            world.clock.date,
        )
        apply_recipe(
            world, household, tile, recipe, 0.5, world.clock.date, yield_factor=1.0
        )
        for _ in range(MONTHS):
            C.dispatch_caravans(world, world.clock.date)
            C.resolve_caravans(world, world.clock.date)
            world.clock.advance_month()
        boil = [e for e in world.ledger.entries if e.reason == "boil_salt"]
        loads = [e for e in world.ledger.entries if e.reason == "caravan_load"]
        unloads = [e for e in world.ledger.entries if e.reason == "caravan_unload"]
        self.assertTrue(boil, "Солевары не выварили соль из рапы")
        self.assertTrue(loads, "Обоз с солью не погружен")
        self.assertTrue(unloads, "Соль не доехала до амбара")
        self.assertGreater(
            world.get_stock("settlement:hill_court").amounts.get("salt", 0.0), 0.0,
            "В замке соли ноль",
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_hired_saltboiler_cuts_peat_on_settlement_marsh(self) -> None:
        """Наёмный солевар рубит торф на болотце предприятия — через публичный тик.

        Раньше тест звал приватную `_apply_set` по подписи из трёх аргументов
        сверху (`world, household, recipes, budget, date, cap, factor, ...`): сегодня
        у неё шесть параметров, и тест падал с `TypeError`. Проверять работу
        рецептов через приватную функцию нельзя — её подпись не закон. Здесь идёт
        обычный `work_month`, а труд задаётся так, чтобы месяца хватило и на
        барщину, и на одну партию `cut_peat`.
        """
        world = clone_salt_world(self.template)
        settlement = world.settlements["salt_village"]
        household = world.households[settlement.household_ids[0]]
        world.households = {household.id: household}
        world.clock.month = 9
        household.main_action = "cut_wood_if_allowed"
        household.minor_action = "idle_repair"
        stock = world.get_stock(household.stock_id)
        peat_before = stock.amounts.get("peat", 0.0)
        recipe = world.catalogs.recipes["cut_peat"]
        marsh = next(
            tile
            for tile in _tiles_for_recipe(world, household, recipe)
            if tile.terrain == "marsh"
        )
        world.ledger.capture_initial(world.total_matter())
        world.ledger.external_in(
            world.get_stock(marsh.standing_stock_id), "peat",
            float(recipe.draws_standing["peat"]),
            "test_seed_peat", None, world.clock.date,
        )
        household.labor_days = (
            seasonal_labor_days(world, household, world.clock.month)
            + float(recipe.labor_days)
        )

        work_month(world, world.clock.date)

        self.assertIn("cut_wood_if_allowed", allowed_action_ids(world, household))
        self.assertGreater(
            stock.amounts.get("peat", 0.0), peat_before,
            "Солевар не добыл торф на топливной клетке предприятия",
        )
        self.assertTrue(
            [e for e in world.ledger.entries if e.reason == "cut_peat"],
            "Нет проводки резки торфа",
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_hired_saltboiler_cannot_cut_forest_outside_works_tiles(self) -> None:
        world = clone_salt_world(self.template)
        settlement = world.settlements["salt_village"]
        household = world.households[settlement.household_ids[0]]
        recipe = world.catalogs.recipes["cut_peat"]
        allowed = _tiles_for_recipe(world, household, recipe)
        self.assertTrue(allowed)
        self.assertTrue(all(tile.id in settlement.works_tiles for tile in allowed))
        self.assertTrue(all(tile.terrain in ("marsh", "forest") for tile in allowed))
        outside_forest = next(
            tile for tile in world.tiles.values()
            if tile.terrain == "forest" and tile.id not in settlement.works_tiles
        )
        self.assertNotIn(outside_forest, allowed)

    def test_landless_worker_may_cut_fuel_only_on_its_own_cells(self) -> None:
        """ADR 0113 п. 1: безземельный двор — агент на своём гексе, топливо он
        добывать **может**. Прежний тест требовал обратного («рубка заперта»);
        теперь закон в том, что граница не запрет, а граница КЛЕТОК.

        `allowed_action_ids` даёт безземельному `cut_wood_if_allowed`
        (`economy/decisions.py:156-161`), но клетки под топливо — только свои и
        соседние: чужое болото или чужой лес в список не попадают. На этом стенде
        у двора на холме вокруг вообще нет ни леса, ни болота, поэтому кандидатов
        нет — и это тоже законно: право есть, клеток нет.
        """
        world = clone_salt_world(self.template)
        household = next(
            hh for hh in world.households.values()
            if hh.legal_status_id == "free_landless"
            and hh.settlement_id != "salt_village"
            and hh.left_at is None
        )
        self.assertIn(
            "cut_wood_if_allowed", allowed_action_ids(world, household),
            "Безземельному снова заперли заготовку топлива (ADR 0113 п. 1)",
        )
        salt_works = set(world.settlements["salt_village"].works_tiles)
        own_settlement = world.settlements[household.settlement_id]
        own_cells = {
            tile.id for tile in own_tiles(world, household)
        } | set(own_settlement.works_tiles)
        for recipe_id in ("cut_wood", "cut_firewood", "cut_peat"):
            with self.subTest(recipe=recipe_id):
                candidates = {
                    tile.id
                    for tile in _tiles_for_recipe(
                        world, household, world.catalogs.recipes[recipe_id]
                    )
                }
                self.assertFalse(
                    candidates & salt_works,
                    f"{recipe_id}: безземельный рубит топливо на чужом предприятии",
                )
                self.assertTrue(
                    candidates <= own_cells,
                    f"{recipe_id}: клетки за пределами своего гекса и поселения",
                )

    def test_salt_recipe_consumes_fuel_and_brine(self) -> None:
        """Выварка: топливо (торф) + рапа → соль; баланс материи сходится."""
        world = clone_salt_world(self.template)
        recipe = world.catalogs.recipes["boil_salt"]
        self.assertEqual(recipe.inputs, {"peat": 3.0}, "Топливо выварки убрано из рецепта")
        self.assertEqual(recipe.draws_standing, {"brine": 6.0})
        self.assertEqual(recipe.outputs, {"salt": 2.0})
        self.assertEqual(recipe.loss, {"peat": 3.0, "brine": 4.0})
        self.assertTrue(recipe.transform)
        self.assertAlmostEqual(
            sum(recipe.inputs.values()) + sum(recipe.draws_standing.values()),
            sum(recipe.outputs.values()) + sum(recipe.loss.values()),
            places=6,
        )

    def test_no_peat_no_salt(self) -> None:
        """Ни своих запасов, ни топливных клеток — выварки нет."""
        world = _stand(self.template, fuel_cells=False)
        for hid in world.settlements["salt_village"].household_ids:
            world.get_stock(f"household:{hid}").amounts["peat"] = 0.0
        for _ in range(6):
            run_month(world)
        self.assertEqual(
            [e for e in world.ledger.entries if e.reason == "boil_salt"], [],
            "Соль выварилась без торфа",
        )
