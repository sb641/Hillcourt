"""Труд на пожалованной полосе: `Right` двора даёт ему клетку в `own_tiles`.

Фаза C: действие `grant_tenement` вырезает полосу из домена в надел двора и
ставит `Right`, но экономика права не видела: `labor.own_tiles` возвращал
только клетку поселения и `works_tiles`, поэтому пожалованную полосу двор не
пахал — право есть, труда нет. Правка: клетки, на которые у двора есть `Right`
(`world.rights`), входят в `own_tiles` без дублей и в порядке id клетки.

Проверки:
  * до grant полоса вне `own_tiles`/`_feeding_tiles`, режим `demesne`;
  * после grant есть `Right`, режим `cotter_plot`, полоса в `own_tiles`,
    `_feeding_tiles` и `_tiles_for_recipe(harvest_grain)`, в логе действие;
  * два мира (с grant на M6 и без): спрос домена корня после grant меньше
    baseline того же месяца, дельта материи ≈ 0;
  * изоляция: если у двора нет иного кормящего надела, пожалованная полоса
    действительно обрабатывается — зерно на ней убывает, сток двора растёт.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy.labor import (
    _feeding_tiles,
    _held_tile_ids,
    _tiles_for_recipe,
    own_tiles,
)
from hillcourt.engine.manor import grant_tenement, root_manor
from hillcourt.engine.tick import run_month
from hillcourt.ontology import Right
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
COTTER = "hh_03"
PLOT = "t_06_02"
HOME = "t_05_01"
HARVEST = "harvest_grain"
GRANT_MONTH = 6
MONTHS = 12
EPSILON = 1e-9


class TestTenementLabor(unittest.TestCase):
    """Пожалованная полоса видна экономике труда как надел двора."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO)

    def test_own_tiles_follows_right_without_duplicates(self) -> None:
        world = self.world
        household = world.households[COTTER]

        self.assertNotIn(
            PLOT, [tile.id for tile in own_tiles(world, household)]
        )
        self.assertEqual(world.tiles[PLOT].regime_id, "demesne")

        rights = grant_tenement(world, COTTER, [PLOT])
        self.assertTrue(rights)
        right = rights[0]
        self.assertEqual(right.holder_household_id, COTTER)
        self.assertEqual(right.tile_id, PLOT)
        self.assertIn(right.id, world.rights)

        self.assertEqual(world.tiles[PLOT].regime_id, "cotter_plot")
        own_ids = [tile.id for tile in own_tiles(world, household)]
        self.assertEqual(own_ids.count(PLOT), 1, "Полоса попала в own_tiles дважды")
        self.assertIn(PLOT, own_ids)
        self.assertEqual(own_ids, sorted(own_ids, key=lambda tid: tid))
        self.assertIn(PLOT, [tile.id for tile in _feeding_tiles(world, household)])

        recipe = world.catalogs.recipes[HARVEST]
        self.assertIn(
            PLOT, [tile.id for tile in _tiles_for_recipe(world, household, recipe)]
        )
        self.assertTrue(
            any(
                action.get("action") == "grant_tenement"
                and action.get("household") == COTTER
                and PLOT in action.get("tiles", [])
                for action in world.player_actions
            )
        )

    def test_demesne_demand_drops_after_grant(self) -> None:
        granted = load_scenario(SCENARIO)
        baseline = load_scenario(SCENARIO)
        for _ in range(GRANT_MONTH - 1):
            run_month(granted)
            run_month(baseline)
        self.assertEqual(granted.clock.month, GRANT_MONTH)

        grant_tenement(granted, COTTER, [PLOT])

        for _ in range(MONTHS - (GRANT_MONTH - 1)):
            month = granted.clock.month
            run_month(granted)
            run_month(baseline)
            self.assertLess(
                root_manor(granted).demesne_labor_demand_this_month,
                root_manor(baseline).demesne_labor_demand_this_month,
                f"M{month}: спрос домена не упал после нарезки полосы",
            )

        self.assertIn(
            PLOT, [tile.id for tile in own_tiles(granted, granted.households[COTTER])]
        )
        self.assertAlmostEqual(
            granted.ledger.delta(granted.total_matter()), 0.0, places=6
        )
        self.assertAlmostEqual(
            baseline.ledger.delta(baseline.total_matter()), 0.0, places=6
        )

    def test_granted_plot_is_worked_by_household(self) -> None:
        """Изоляция: у двора нет иного кормящего надела — работает полоса по праву.

        Оба мира получают одну и ту же нарезку (режим `cotter_plot`), но в
        контрольном мире `Right` снят — это поведение до правки. Разница мира
        только в праве, поэтому наблюдаемый труд — следствие `Right`.
        """
        working = load_scenario(SCENARIO)
        control = load_scenario(SCENARIO)
        for world in (working, control):
            grant_tenement(world, COTTER, [PLOT])
            world.tiles[HOME].regime_id = "reserved_wood"  # нет иного надела
        right = next(
            r for r in control.rights.values() if r.holder_household_id == COTTER
        )
        control.rights.pop(right.id)

        household_w = working.households[COTTER]
        household_c = control.households[COTTER]
        self.assertIn(PLOT, [tile.id for tile in _feeding_tiles(working, household_w)])
        self.assertNotIn(
            PLOT, [tile.id for tile in _feeding_tiles(control, household_c)]
        )

        for _ in range(2):
            run_month(working)
            run_month(control)

        tile_w = working.get_stock(working.tiles[PLOT].standing_stock_id)
        tile_c = control.get_stock(control.tiles[PLOT].standing_stock_id)
        self.assertLess(
            tile_w.amounts.get("grain", 0.0),
            tile_c.amounts.get("grain", 0.0),
            "Зерно пожалованной полосы не убывает от работы двора",
        )
        stock_w = working.get_stock(household_w.stock_id)
        stock_c = control.get_stock(household_c.stock_id)
        self.assertGreater(
            stock_w.amounts.get("grain", 0.0),
            stock_c.amounts.get("grain", 0.0),
            "Сток двора не получил зерно с пожалованной полосы",
        )
        self.assertAlmostEqual(
            working.ledger.delta(working.total_matter()), 0.0, places=6
        )
        self.assertAlmostEqual(
            control.ledger.delta(control.total_matter()), 0.0, places=6
        )


class TestCommonRightNotHolding(unittest.TestCase):
    """ADR 0060: `Right.kind=common` — общий доступ, не индивидуальный надел.

    Община, выданная как `common`, не должна становиться наделом и ёмкостью
    первого двора: `own_tiles`/`_held_tile_ids` игнорируют такие `Right`,
    индивидуальный `tenure` — работает как прежде.
    """

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO)
        self.household = self.world.households[COTTER]
        self.common = Right(
            id="right_common_probe",
            holder_household_id=COTTER,
            tile_id=PLOT,
            kind="common",
            granted_date=self.world.clock.date,
            rent_share=0.0,
        )
        self.tenure = Right(
            id="right_tenure_probe",
            holder_household_id=COTTER,
            tile_id="t_06_03",
            kind="tenure",
            granted_date=self.world.clock.date,
            rent_share=0.1,
        )

    def test_common_right_gives_no_plot(self) -> None:
        world = self.world
        world.rights[self.common.id] = self.common
        ids = [tile.id for tile in own_tiles(world, self.household)]
        self.assertNotIn(PLOT, ids, "common стал наделом первого двора")
        self.assertNotIn(PLOT, _held_tile_ids(world, self.household),
                         "common попал в кап индивидуального держания")

    def test_tenure_right_still_gives_plot(self) -> None:
        world = self.world
        world.rights[self.tenure.id] = self.tenure
        ids = [tile.id for tile in own_tiles(world, self.household)]
        self.assertIn("t_06_03", ids, "Индивидуальный tenure перестал давать надел")
        self.assertIn("t_06_03", _held_tile_ids(world, self.household))


if __name__ == "__main__":
    unittest.main()
