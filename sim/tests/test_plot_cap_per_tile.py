"""Ёмкость труда на клетку, а не на двор (backlog G3 / ADR 0011, дыра C).

`_plot_cap` раньше был лимитом партий НА ДВОР (`holding_scale`), а `_apply_set`
считал `made` суммарно по всем клеткам двора: коттер с домашним наделом и второй
нарезанной полосой пахал только первую клетку, вторая простаивала. Правка:
лимит — на клетку. Двор вправе снять с КАЖДОЙ своей клетки свою долю
(`plot_batch_cap_per_tile × holding_scale`), а ёмкость клетки
(`plot_batch_cap_per_tile`) делят между собой её ДЕРЖАТЕЛИ (клетка по `Right`
или усадьба двора): два двора на одной такой клетке делят 4 партии, а не
получают по 4.

Известное ограничение (кандидат в backlog, не эта смена): общинные
`works_tiles` поселения, не держимые по праву, ёмкость НЕ делят — считаются как
раньше, лимит на двор. Так соль A1 на сиде 99 остаётся зелёной; шарить и
общинные клетки можно только вместе с пересмотром соли (ADR 0011).

Проверки:
  * (a) двор `hh_03` с домашним наделом и `grant_tenement` на `t_06_02` пашет
    ОБЕ клетки за один месяц: суммарно партий больше старого лимита двора, с
    каждой клетки — не больше её ёмкости;
  * (b) два двора с держанием на одной клетке дают с неё не больше ёмкости и
    строго меньше, чем один двор с двумя клетками; второй не удваивает урожай;
  * (c) дельта материи ≈ 0 в обоих прогонах;
  * (d) 12 мес `v0_hill_and_salt` не падают, дельта ≈ 0;
  * (e) известное ограничение: общинная `works_tile` поселения не шарится.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy.labor import (
    _held_tile_ids,
    _plot_cap,
    _tile_batch_cap,
    work_month,
)
from hillcourt.engine.manor import grant_tenement
from hillcourt.engine.tick import run_month
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"

COTTER = "hh_03"
HOME = "t_05_01"
PLOT = "t_06_02"

VILLEIN_A = "hh_02"
VILLEIN_B = "hh_05"
HOME_A = "t_03_01"
HOME_B = "t_01_02"
PLOT_ALT = "t_05_02"
HARVEST = "harvest_grain"
EPSILON = 1e-6

SALT_A = "hh_salt_01"
SALT_B = "hh_salt_02"
COMMUNAL = "t_06_05"  # полевой works_tile соляной деревни, держания по праву нет


def _batches(world, tile_id: str, reason: str = HARVEST) -> int:
    """Сколько партий рецепта снято с клетки (по проводкам со стоячего стока)."""
    stock_id = world.tiles[tile_id].standing_stock_id
    return sum(
        1
        for entry in world.ledger.entries
        if entry.kind == "transfer" and entry.src_id == stock_id and entry.reason == reason
    )


def _seed_standing(world, tile_id: str, amount: float = 1000.0) -> None:
    """Засеять стоячую материю клетки через внешний приход (дельта остаётся 0)."""
    stock = world.get_stock(world.tiles[tile_id].standing_stock_id)
    world.ledger.external_in(
        stock, "grain", amount, "test_seed_standing", None, world.clock.date
    )


class TestPlotCapPerTile(unittest.TestCase):
    """Партии считаются на клетку: доля двора — на каждой, ёмкость — общая."""

    def test_two_feeding_tiles_worked_in_one_month(self) -> None:
        """(a)+(c): вторая полоса не простаивает, но и клетка не перепахана."""
        world = load_scenario(SCENARIO)
        household = world.households[COTTER]
        grant_tenement(world, COTTER, [PLOT])

        old_household_limit = int(
            world.manor.plot_batch_cap_per_tile * household.holding_scale
        )
        tile_cap = _tile_batch_cap(world)
        self.assertEqual(old_household_limit, 2)
        self.assertEqual(tile_cap, 4)

        run_month(world)

        home = _batches(world, HOME)
        plot = _batches(world, PLOT)
        self.assertGreaterEqual(home, 1, "Домашний надел не обработан")
        self.assertGreaterEqual(plot, 1, "Пожалованная полоса не обработана")
        self.assertGreater(
            home + plot,
            old_household_limit,
            "Обе клетки вместе не перекрыли старый лимит НА ДВОР",
        )
        self.assertLessEqual(home, tile_cap, "Домашний надел перепахан сверх ёмкости клетки")
        self.assertLessEqual(plot, tile_cap, "Полоса перепахана сверх ёмкости клетки")
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )

    def test_two_households_share_tile_capacity(self) -> None:
        """(b)+(c): два двора на клетке не дают двойного выхода."""
        shared = self._one_month(
            grants={VILLEIN_A: [PLOT], VILLEIN_B: [PLOT]},
            nonfeeding=(HOME_A, HOME_B),
        )
        single = self._one_month(
            grants={VILLEIN_A: [PLOT]},
            nonfeeding=(HOME_A, HOME_B),
        )
        two_cells = self._one_month(
            grants={VILLEIN_A: [PLOT, PLOT_ALT]},
            nonfeeding=(HOME_A, HOME_B),
        )

        tile_cap = _tile_batch_cap(shared)
        shared_plot = _batches(shared, PLOT)
        single_plot = _batches(single, PLOT)
        two_cells_total = _batches(two_cells, PLOT) + _batches(two_cells, PLOT_ALT)

        self.assertGreaterEqual(single_plot, 1)
        self.assertLessEqual(shared_plot, tile_cap, "С клетки снято больше её ёмкости")
        self.assertLess(
            shared_plot,
            two_cells_total,
            "Два двора на одной клетке не должны обходить один двор с двумя клетками",
        )
        self.assertLess(
            shared_plot,
            2 * single_plot,
            "Второй двор удвоил урожай клетки",
        )
        for world in (shared, single, two_cells):
            self.assertAlmostEqual(
                world.ledger.delta(world.total_matter()), 0.0, places=6
            )

    def test_scenario_survives_twelve_months(self) -> None:
        """(d): год сценария идёт без падения, дельта ≈ 0."""
        world = load_scenario(SCENARIO)
        for _ in range(12):
            run_month(world)
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )

    def test_communal_works_tile_is_not_shared_yet(self) -> None:
        """(e) Известное ограничение: общинная works_tile ёмкость не делит.

        `works_tiles` соляной деревни — общее поле двух дворов, но держания по
        праву у них нет, поэтому каждый берёт свою долю (4), а не делит 4 на
        двоих. Так соль A1 остаётся зелёной; шаринг общинных клеток — отдельная
        задача с пересмотром соли (ADR 0011).
        """
        world = load_scenario(SCENARIO)
        for household in world.households.values():
            household.labor_days = 0.0
        for hid in (SALT_A, SALT_B):
            household = world.households[hid]
            self.assertNotIn(
                COMMUNAL,
                _held_tile_ids(world, household),
                "Общинная клетка вдруг стала держанием по праву",
            )
            self.assertEqual(_plot_cap(world, household), 4)
            household.labor_days = 400.0
        _seed_standing(world, COMMUNAL)

        work_month(world, world.clock.date)

        tile_cap = _tile_batch_cap(world)
        total = _batches(world, COMMUNAL)
        self.assertGreater(
            total,
            tile_cap,
            "Общинная клетка начала шариться — меняется соль A1, нужен ADR",
        )
        self.assertEqual(total, 2 * tile_cap)
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )

    def _one_month(self, grants: dict[str, list[str]], nonfeeding: tuple[str, ...]):
        """Мир с заданными держаниями; один месяц труда при полном запасе рук."""
        world = load_scenario(SCENARIO)
        for household_id, tile_ids in grants.items():
            grant_tenement(world, household_id, tile_ids)
        for tile_id in nonfeeding:
            world.tiles[tile_id].regime_id = "reserved_wood"
        for tile_id in (PLOT, PLOT_ALT):
            _seed_standing(world, tile_id)
        for household_id in grants:
            world.households[household_id].labor_days = 400.0
        work_month(world, world.clock.date)
        return world


if __name__ == "__main__":
    unittest.main()
