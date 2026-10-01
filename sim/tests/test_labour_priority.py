"""Приоритет труда двора: своё хозяйство получает труд раньше домена.

Живой закон этого модуля: **труд не создаётся и не делится пополам сменой**.
`work_month` сначала отдаёт двору его собственный бюджет
(`own_budget = labor − demesne_limit`, `economy/labor.py:842-843`), и только
остаток уходит в барщину домена; `surplus_labor` хранит знак недосева. Первые
три теста держат эту арифметику, подменив `_apply_set` (сшивка для труда
своего хозяйства) — так арифметика проверяется без урожая.

Что здесь больше не проверяется: **манориальный кап участка `_plot_cap`**.
Символ вырезан ADR 0137 вместе с капом партий на клетке (у пашни потолка нет,
ёмкость растёт от числа работников — `test_communal_capacity`). Живая часть
того же закона (ADR 0113 п. 1: безземельный двор работает гекс, где стоит, и
`work_plot`/`hire_out` ему доступны) осталась в тестах ниже, уже без обращения к
мёртвому символу.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from hillcourt.economy.decisions import allowed_action_ids
from hillcourt.economy.labor import _feeding_tiles, work_month
from hillcourt.legal.calendar import seasonal_labor_days
from hillcourt.engine.tick import run_month
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
BARONY = ROOT / "design" / "scenarios" / "v0_barony_100.yml"


class TestLaborPriority(unittest.TestCase):
    """Остаток после работы на своё хозяйство уходит на домен без тихого голода."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO)
        self.world.clock.month = 1
        self.household = self.world.households["hh_02"]
        self.household.main_action = "work_plot"
        self.household.minor_action = "tend_animals"
        self.world.households = {self.household.id: self.household}

    def run_work(self, own_labor: float):
        """Запустить месяц с заданным расходом труда на своё хозяйство.

        `_apply_set` — приватная сшивка: подменяем её, чтобы труд своего
        хозяйства был заданным числом, а не зависел от урожая клетки. Список
        рецептов ищем по всем аргументам вызова, а не по номеру позиции: позиция
        — часть приватной подписи и сегодня уже ломала другой тест.
        """
        with patch(
            "hillcourt.economy.labor._apply_set",
            return_value=(own_labor, {"harvest_grain": 1}),
        ) as apply_set:
            work_month(self.world, self.world.clock.date)
        return apply_set

    def _recipe_ids(self, apply_set) -> list[str]:
        """id рецептов из вызова `_apply_set`: ищем список, а не позицию.

        Позиция аргумента — часть приватной подписи; жёсткий индекс однажды уже
        сломал другой тест того же модуля.
        """
        for argument in apply_set.call_args.args:
            if isinstance(argument, (list, tuple)) and argument:
                ids = [getattr(item, "id", None) for item in argument]
                if all(ids):
                    return [item for item in ids if item]
        return []

    def test_own_farm_receives_labor_before_demesne(self) -> None:
        apply_set = self.run_work(4.0)

        self.assertEqual(apply_set.call_count, 1)
        recipe_ids = self._recipe_ids(apply_set)
        self.assertIn("harvest_grain", recipe_ids)
        self.assertIn("gather_hay", recipe_ids)
        self.assertEqual(self.world.stats["own_labor"], 4.0)

    def test_demesne_receives_only_surplus(self) -> None:
        """В домен уходит остаток, но не больше каталожной нормы месяца.

        Нормы в тесте нет: она читается из календаря (`seasonal_labor_days`),
        поэтому проверка держит закон, а не сегодняшнее число. Двор потратил весь
        месяц (своё хозяйство + барщина), остатка не остаётся, а `surplus_labor`
        отрицателен — это долг хозяину, а не тихий голод.
        """
        self.household.labor_days = 10.0
        duty = seasonal_labor_days(self.world, self.household, self.world.clock.month)
        self.assertGreater(duty, 0.0, "У виллана нет барщины — стенд пустой")

        self.run_work(4.0)

        self.assertEqual(self.world.stats["own_labor"], 4.0)
        self.assertEqual(
            self.world.stats["demesne_labor"],
            min(10.0 - 4.0, duty),
            "В домен ушло не столько, сколько осталось после своего и не больше нормы",
        )
        self.assertEqual(
            self.household.labor_days, 0.0,
            "Месяц не распределён: своё хозяйство и барщина вместе взяли труд",
        )
        self.assertLess(self.world.stats["surplus_labor"], 0.0)

    def test_customary_limit_comes_from_calendar(self) -> None:
        self.household.labor_days = 20.0

        self.run_work(4.0)

        self.assertEqual(self.world.stats["demesne_labor"], 8.0)
        self.assertEqual(self.world.stats["surplus_labor"], 0.0)

        self.world.stats.clear()
        self.household.legal_status_id = "cotter"
        self.run_work(4.0)
        self.assertEqual(self.world.stats["demesne_labor"], 4.0)
        self.assertEqual(self.world.stats["surplus_labor"], 0.0)


class TestLandlessOnOwnHex(unittest.TestCase):
    """ADR 0113: хекс × люди = еда — безземельный двор работает гекс, где стоит."""

    def _stand(self):
        world = load_scenario(BARONY, seed=4242)
        settlement = world.settlements["native_village"]
        household = world.households[settlement.household_ids[0]]
        self.assertEqual(household.legal_status_id, "free_landless")
        self.assertEqual(household.holding_scale, 0.0)
        world.households = {household.id: household}
        world.get_stock(household.stock_id).amounts.clear()
        household.labor_days = 40.0
        return world, household

    def test_landless_may_work_its_own_hex(self) -> None:
        """ADR 0113 п. 1: безземельный двор работает гекс, где стоит.

        Прежняя проверка спрашивала `_plot_cap(world, household) >= 1` — мёртвый
        символ (ADR 0137 вырезал кап партий на клетке; потолка пашни нет,
        ёмкость растёт от работников — `test_communal_capacity`). Живой смысл тот
        же: труд безземельного не обнулён регламентом, и он сам решает, куда его
        деть. `hire_out` — не по статусу, а по книге (`legal/manor.py:53`): племя
        вне книги барона (ADR 0116) поденно не нанимается, двор в книге — может.
        """
        world, household = self._stand()
        allowed = allowed_action_ids(world, household)
        self.assertIn("work_plot", allowed, "Безземельному закрыт труд на своём гексе")
        self.assertTrue(
            _feeding_tiles(world, household),
            "Гекс, на котором стоит безземельный, не кормит его",
        )
        self.assertNotIn(
            "hire_out", allowed,
            "Племя вне книги манора получило подённый наём (ADR 0116)",
        )
        root = world.manors[world.player_manor_id]
        household.manor_id = root.id
        if household.id not in root.household_ids:
            root.household_ids.append(household.id)
        self.assertIn(
            "hire_out", allowed_action_ids(world, household),
            "Двор в книге манора не может наняться поденно (ADR 0120)",
        )
    def test_landless_harvests_its_own_communal_field(self) -> None:
        world, household = self._stand()
        field = next(
            tile for tile in _feeding_tiles(world, household) if tile.terrain == "field"
        )
        world.get_stock(field.standing_stock_id).amounts["grain"] = 40.0
        household.main_action = "work_plot"
        household.minor_action = "idle_repair"
        world.ledger.capture_initial(world.total_matter())
        work_month(world, world.clock.date)
        self.assertTrue(
            [e for e in world.ledger.entries if e.reason == "harvest_grain"],
            "Жатвы не было вовсе",
        )
        stock = world.get_stock(household.stock_id)
        food = sum(
            amount * (world.catalogs.goods[good].nutrition if good in world.catalogs.goods else 0.0)
            for good, amount in stock.amounts.items()
        )
        self.assertGreater(
            food, 0.0,
            "Безземельный двор не собрал еду со своей клетки",
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_landless_covers_its_own_mouth(self) -> None:
        world, household = self._stand()
        for _ in range(6):
            run_month(world)
        self.assertEqual(household.hunger_days, 0, "Племенной двор голодает на своей клетке")
        self.assertGreater(world.get_stock(household.stock_id).amounts.get("grain", 0.0), 0.0)


if __name__ == "__main__":
    unittest.main()
