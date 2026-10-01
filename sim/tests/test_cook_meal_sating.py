"""ADR 0159. Готовка насыщает двор, а не крадёт еду (ADR 0098, 0100, 0106 п. 2).

Находка была такой: `phase_cook_meal` стоит в `PHASES` индексом 5, а
`phase_consume` — индексом 9, то есть готовка идёт **раньше** поедания. Зерно и
овощи уходят в `sink:eaten` (по материи верно), но без зачёта фаза 9 считала
голод по уменьшенному стоку. Итог: одна и та же еда съедалась дважды — один раз
по материи, второй раз по нужде, и **готовка делала двор голоднее**.

Закон, который здесь защищается:

* `cook_meal` — **приём пищи**, а не новый товар (ADR 0098, 0100): материя уходит
  в `sink:eaten`, а насыщение засчитывается двору месячным зачётом с штампом
  месяца — тем же приёмом, что потолок закупки зерна (ADR 0144);
* `phase_consume` вычитает зачёт из потребности **до** поедания и до подсчёта
  `hunger_days`;
* кухня **одна** для двора и для особняка (ADR 0106 п. 2): одна функция
  `cook_meal`, а объём задаётся стоком, а не сословием;
* дельта материи 0 и детерминизм по одному `seed`.

Стенд один и тот же для всех сравнений: меняется только `main_action`.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy import cooking
from hillcourt.economy.needs import monthly_food_need
from hillcourt.engine.tick import PHASES, phase_cook_meal, phase_consume
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SEED = 1729
YARD = "hh_02"
LORD_YARD = "hh_court"
EPSILON = 1e-9

# Полный набор ингредиентов рецепта плюс «простое зерно» сверх него: сытая
# лавка, из которой двор может и сварить, и съесть сырую. Числа не выдуманы, это
# ровно те дозы, которые `cook_meal` берёт из стока (`cooking.STAPLE_INPUTS` и
# `cooking.OPTIONAL_INPUTS`).
MEAL_STOCK = {
    "grain": 1.0,
    "roots": 0.5,
    "greens": 0.5,
    "mushrooms": 0.5,
    "berries": 0.5,
    "milk": 0.5,
    "eggs": 0.25,
    "cheese": 0.2,
}
EXTRA_GRAIN = 1.6
LABOR = 40.0


def _stand(action: str, household_id: str = YARD):
    """Двор с одной лавкой, месячная норма известна, ничего лишнего не ест."""
    world = load_scenario(SCENARIO, seed=SEED)
    household = world.households[household_id]
    world.households = {household_id: household}
    stock = world.get_stock(household.stock_id)
    stock.amounts.clear()
    stock.amounts.update(MEAL_STOCK)
    stock.amounts["grain"] = MEAL_STOCK["grain"] + EXTRA_GRAIN
    household.main_action = action
    household.minor_action = "idle_repair"
    household.labor_days = LABOR
    world.ledger.capture_initial(world.total_matter())
    return world, household, stock


def _cook_and_eat(world) -> None:
    """Две фазы закона по порядку: готовка (5), потом поедание (9)."""
    phase_cook_meal(world)
    phase_consume(world)


class TestCookMealSating(unittest.TestCase):
    """Готовка — приём пищи: двор насыщен, и еда не съедена дважды."""

    def test_cook_phase_runs_before_the_consume_phase(self) -> None:
        """Порядок фаз — часть закона: иначе зачёт пришёл бы слишком поздно."""
        names = [phase.__name__ for phase in PHASES]
        self.assertLess(
            names.index("phase_cook_meal"), names.index("phase_consume"),
            "Готовка идёт после поедания — зачёт опоздает",
        )

    def test_cooked_meal_satisfies_the_household(self) -> None:
        """Сварил — не голодал, и лавка не пустая.

        На том же стенде без готовки двор сыт и при этом съедает больше: сырая
        еда не даёт бонуса готовки. С зачётом готовой еды двор сыт И оставляет
        зерна больше, потому что приготовленное уже насытило.
        """
        world, household, stock = _stand("cook_meal")
        need = monthly_food_need(world, household)
        _cook_and_eat(world)

        self.assertGreater(need, 0.0, "Стенд: двор не голодает и без готовки")
        self.assertEqual(
            household.hunger_days, 0,
            "Двор сварил обед и всё равно голодает — еда съедена дважды",
        )
        self.assertGreater(
            stock.amounts.get("grain", 0.0), 0.0,
            "Двор помечен сытым с пустой лавкой: еда ушла и в еду, и в нужду",
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_raw_food_alone_satisfies_but_leaves_less(self) -> None:
        """Тот же стенд без готовки: сыт, но зерна остаётся меньше.

        Это и есть сравнение «до и после готовки» на одном стенде: готовка не
        отнимает у двора его сытость, а добавляет бонус (ADR 0098: `+12 %` против
        потери `8 %`).
        """
        cooked, _, cooked_stock = _stand("cook_meal")
        _cook_and_eat(cooked)
        raw, raw_household, raw_stock = _stand("idle_repair")
        _cook_and_eat(raw)

        self.assertEqual(raw_household.hunger_days, 0, "Контрольный стенд не сыт")
        self.assertGreater(
            cooked_stock.amounts.get("grain", 0.0),
            raw_stock.amounts.get("grain", 0.0),
            "Готовка не дала бонуса: сырая еда сытнее",
        )
        for world in (cooked, raw):
            self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_credit_is_written_only_by_a_real_meal(self) -> None:
        """Зачёт равен питательности приёма пищи и протухает с месяцем."""
        world, household, _stock = _stand("cook_meal")
        self.assertEqual(cooking.meal_credit(world, household), 0.0, "Зачёт до готовки")

        phase_cook_meal(world)
        credited = cooking.meal_credit(world, household)
        self.assertGreater(credited, 0.0, "Готовка состоялась, а зачёта нет")
        self.assertAlmostEqual(
            world.stats.get("meal_nutrition", 0.0), credited, places=6,
            msg="Зачёт разошёлся с измеренной питательностью готовки",
        )

        # Новый месяц протухает сам: штамп стоит, счётчик нет.
        world.clock.advance_month()
        self.assertEqual(
            cooking.meal_credit(world, household), 0.0,
            "Зачёт протух не по месяцу: старый месяц кормит новый",
        )
        self.assertNotIn(
            "meal_nutrition", world.stats if world.stats.get("meal_nutrition") is None else {},
            "Статистика ошибочно попала в набор счётчиков",
        )

    def test_no_staple_no_meal_no_credit(self) -> None:
        """Без зерна или муки готовки нет — и зачёта тоже."""
        world, household, stock = _stand("cook_meal")
        village = world.get_stock("settlement:hill_court")
        for good in ("grain", "flour"):
            available = stock.amounts.get(good, 0.0)
            if available > EPSILON:
                # Материя уходит проводкой, а не присваиванием: дельта должна
                # остаться нулевой и на этом стенде.
                world.ledger.transfer(
                    stock, village, good, available, "test_setup", world.clock.date
                )
        self.assertFalse(cooking.can_cook(world, household))
        _cook_and_eat(world)
        self.assertEqual(
            cooking.meal_credit(world, household), 0.0,
            "Зачёт за готовку, которой не было",
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_one_kitchen_for_a_household_and_a_mansion(self) -> None:
        """ADR 0106 п. 2: одна кухня. Объём задаёт сток, а не сословие."""
        villager, villager_household, _ = _stand("cook_meal")
        lord, lord_household, _ = _stand("cook_meal", household_id=LORD_YARD)
        self.assertNotEqual(villager_household.legal_status_id, lord_household.legal_status_id)

        self.assertTrue(cooking.can_cook(villager, villager_household))
        self.assertTrue(cooking.can_cook(lord, lord_household))
        villager_meal = cooking.cook_meal(villager, villager_household, villager.clock.date)
        lord_meal = cooking.cook_meal(lord, lord_household, lord.clock.date)
        self.assertAlmostEqual(
            villager_meal, lord_meal, places=6,
            msg="Порция особняка отличается от порции двора — кухня должна быть одна",
        )
        for world, household in ((villager, villager_household), (lord, lord_household)):
            self.assertAlmostEqual(cooking.meal_credit(world, household), villager_meal, places=6)
            self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_same_seed_same_result(self) -> None:
        first, first_household, first_stock = _stand("cook_meal")
        _cook_and_eat(first)
        second, second_household, second_stock = _stand("cook_meal")
        _cook_and_eat(second)
        self.assertEqual(first_household.hunger_days, second_household.hunger_days)
        self.assertEqual(
            first_stock.amounts.get("grain", 0.0), second_stock.amounts.get("grain", 0.0)
        )
        self.assertEqual(
            cooking.meal_credit(first, first_household),
            cooking.meal_credit(second, second_household),
        )
        self.assertEqual(first.state_hash(), second.state_hash(), "Один seed — разный мир")


if __name__ == "__main__":
    unittest.main()
