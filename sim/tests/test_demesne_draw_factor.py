"""Жатва домена: множитель клетки считается РОВНО ОДИН РАЗ (ADR 0106/0110).

Долг, который был не закрыт: `manor._work_demesne` отдавал `apply_recipe` готовый
множитель клетки (сезон × `soil.tile_yield_factor`), а `apply_recipe` домножал
прибавки клетки (`soil.cell_yield_factor`) ещё раз. Итог — прибавки клетки
(инструмент, тягло, навоз, смена полей) в двойном множителе, жатва брала из гекса
больше стоящей материи, чем в нём было, и бухгалтерия отказывала:
`ValueError: Недостаточно 'grain' в стоке 'tile:t_41_52': есть 1.1407, нужно
1.2320` (`v0_barony_100`, 13-й месяц; отношение 1.2320/1.1407 = 1.08 = `rotation_yield`).

Тест ставит на гекс ровно ту стоячую материю, которую честный множитель берёт
одной партией, и требует, чтобы жатва её сняла, а клетка не ушла в минус. С
двойным счётом списание в 1.132 раза больше остатка, и бухгалтерия роняет прогон.

Мутация (ADR 0155): вернуть в `manor._work_demesne` `yield_factor=cell` вместо
`season_and_base` — тест обязан упасть.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy import labor, manor, soil
from hillcourt.scenario import load_scenario
from hillcourt.economy.seasons import demesne_yield

ROOT = Path(__file__).resolve().parents[2]
SHIRE = ROOT / "design" / "scenarios" / "v0_shire.yml"
GRAIN = "grain"
SEED = 1729


class TestDemesneDrawFactor(unittest.TestCase):
    """Один множитель клетки на одну партию жатвы."""

    def setUp(self) -> None:
        self.world = load_scenario(SHIRE, seed=SEED)
        self.manor_id = sorted(self.world.manors)[0]
        self.book = self.world.manors[self.manor_id]
        self.worker = manor._manor_worker(self.world, self.book)
        self.assertIsNotNone(self.worker, "У манора нет работника")
        fields = manor._manor_demesne_fields(self.world, self.book)
        self.assertTrue(fields, "У манора нет доменных пашен")
        self.tile = fields[0]
        # Клетка С ПРИБАВКОЙ, иначе двойной счёт неотличим от правильного: в
        # `v0_shire` без инструмента `cell_yield_factor` = 1.0000, умножение на
        # единицу ничего не меняет. Лемех даёт инструментальную прибавку 1.15, и
        # каждый множитель обязан попасть в жатву ровно один раз.
        self.world.get_stock(self.worker.stock_id).amounts["iron_share"] = 1.0
        self.recipe = self.world.catalogs.recipes[self.world.manor.harvest_recipe]
        self.season = demesne_yield(self.world, self.world.clock.month)
        # Сезон × норма домена — то, что получает `apply_recipe`.
        self.season_and_base = self.season * soil.demesne_base_factor(self.world, self.tile)
        # Полный множитель — то, чем реально берётся стоячая материя.
        self.draw = labor.draw_factor(
            self.world, self.worker, self.recipe, self.season_and_base, self.tile
        )
        self.assertGreater(
            soil.cell_yield_factor(self.world, self.worker, self.tile), 1.0,
            "Стенд без прибавок клетки: двойной счёт в нём неотличим",
        )

    def test_demesne_base_and_cell_factor_multiply_once(self) -> None:
        """Норма домена × прибавки клетки = полный множитель, и ровно один раз.

        `tile_yield_factor` = `demesne_base_factor` × `cell_yield_factor` — тождество
        каталога. Если бы прибавки считались дважды, множитель жатвы был бы в
        `cell_yield_factor` раз больше, и это видно здесь сразу.
        """
        cell = soil.cell_yield_factor(self.world, self.worker, self.tile)
        self.assertAlmostEqual(
            self.season * soil.demesne_base_factor(self.world, self.tile) * cell,
            self.draw, places=12,
            msg="Множитель жатвы не равен произведению базы домена на прибавки клетки",
        )
        self.assertAlmostEqual(
            self.draw,
            self.season * soil.tile_yield_factor(self.world, self.tile, self.worker),
            places=12, msg="Сторож и списание считают разные множители",
        )

    def test_harvest_takes_exactly_what_the_factor_says(self) -> None:
        """Гекс снят под ноль, амбар получил выход той же партии, дельта 0.

        Стоящей материи на гексе ровно столько, сколько берёт одна партия по
        честному множителю. Двойной счёт запросил бы в 1.132 раза больше, и
        бухгалтерия отказала бы — прогон упал бы с `ValueError`.
        """
        per_batch = self.recipe.draws_standing.get(GRAIN, 0.0) * self.draw
        self.assertGreater(per_batch, 0.0, "Жатва не берёт стоячего зерна")
        tile_stock = self.world.get_stock(self.tile.standing_stock_id)
        tile_stock.amounts.clear()
        tile_stock.amounts[GRAIN] = per_batch
        barn = manor.manor_stock(self.world, self.book)
        before_barn = float(barn.amounts.get(GRAIN, 0.0))
        self.book.demesne_labor_filled = 4.0 * float(self.recipe.labor_days)
        self.world.ledger.capture_initial(self.world.total_matter())

        worked_by = manor._work_demesne(self.world, self.world.clock.date)

        self.assertGreater(
            worked_by.get(self.manor_id, 0.0), 0.0, "Домен не отработал ни дня"
        )
        self.assertAlmostEqual(
            tile_stock.amounts.get(GRAIN, 0.0), 0.0, places=9,
            msg="Гекс не снят под ноль — жатва взяла больше честной партии",
        )
        self.assertGreater(
            float(barn.amounts.get(GRAIN, 0.0)), before_barn,
            "Амбар манора не получил урожай домена",
        )
        self.assertAlmostEqual(
            self.world.ledger.delta(self.world.total_matter()), 0.0, places=6,
            msg="Жатва домена создала материю",
        )

    def test_thin_hex_yields_less_but_never_negative(self) -> None:
        """Стоячей материи меньше партии — жатва берёт часть, а не уходит в минус."""
        per_batch = self.recipe.draws_standing.get(GRAIN, 0.0) * self.draw
        tile_stock = self.world.get_stock(self.tile.standing_stock_id)
        tile_stock.amounts.clear()
        tile_stock.amounts[GRAIN] = per_batch * 0.5
        self.book.demesne_labor_filled = 4.0 * float(self.recipe.labor_days)
        self.world.ledger.capture_initial(self.world.total_matter())

        manor._work_demesne(self.world, self.world.clock.date)

        self.assertAlmostEqual(
            tile_stock.amounts.get(GRAIN, 0.0), 0.0, places=9,
            msg="Клетка ушла в минус — стоячей материи больше не взять",
        )
        self.assertAlmostEqual(
            self.world.ledger.delta(self.world.total_matter()), 0.0, places=6
        )


if __name__ == "__main__":
    unittest.main()
