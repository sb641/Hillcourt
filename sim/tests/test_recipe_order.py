"""Порядок рецептов двора: еда → топливо → прочее (ADR 0095, ADR 0161 п. 1).

Порядок не может быть алфавитным: `make_board`/`make_plank` вставали в очередь
раньше `split_logs` (буква `m` < `s`), и двор в месяц лесозаготовки пилил бревно
вместо того, чтобы колоть дрова. Замерено на `start_stand`, 12 месяцев, сид 4242:
`split_logs` давал **0.0000** дров, после правки — **16.9722**.

Три проверки, и каждая ловит свой класс поломки:
  * порядок из `_recipe_order` — еда, потом топливо, потом прочее;
  * класс топлива выводится из каталога, и не-едовые производители топлива —
    ровно `cut_firewood`, `cut_peat`, `split_logs` (список ADR 0161 п. 1);
  * конец-в-начало: в месяц лесозаготовки двор действительно колет дрова, лестница
    бревно → доска → планка остаётся достижимой, материя не появляется.

Мутации, которые обязаны ронять эти тесты (ADR 0155):
  * вычеркнуть из `_recipe_order` ветку топлива (снова алфавитный порядок);
  * исключить `split_logs` из класса топлива.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy import labor
from hillcourt.engine.tick import run_month
from hillcourt.runner import _apply_script_entry
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "start_stand.yml"
SEED = 4242
MONTHS = 12
# Не-едовые производители топлива по ADR 0161 п. 1.
FUEL_RECIPES = ("cut_firewood", "cut_peat", "split_logs")
# Что должно обгонять лестницу материала: дрова и торф важнее досок и планок.
FUEL_BEFORE_MATERIALS = (("split_logs", "make_board"), ("split_logs", "make_plank"))


class TestRecipeOrder(unittest.TestCase):
    """`_recipe_order` — еда, топливо, прочее."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO, seed=SEED)

    def order(self) -> list[str]:
        allowed = set(self.world.catalogs.recipes)
        return [r.id for r in labor._recipe_order(self.world, allowed)]

    def test_food_recipes_stay_first(self) -> None:
        """ADR 0095: еда всегда первая — это правило правка не трогает."""
        order = self.order()
        food = [rid for rid in order if labor._produces_food(self.world, self.world.catalogs.recipes[rid])]
        self.assertTrue(food, "В каталоге нет ни одного едового рецепта")
        self.assertEqual(order[: len(food)], food, "Едовые рецепты разошлись по очереди")

    def test_fuel_precedes_building_materials(self) -> None:
        """Топливо раньше стройматериалов: каждая пара «дрова раньше лестницы»."""
        order = self.order()
        for fuel_id, material_id in FUEL_BEFORE_MATERIALS:
            with self.subTest(fuel=fuel_id, material=material_id):
                self.assertIn(fuel_id, order)
                self.assertIn(material_id, order)
                self.assertLess(
                    order.index(fuel_id), order.index(material_id),
                    f"{fuel_id} встал после {material_id}: двор будет пилить вместо дров",
                )

    def test_fuel_class_is_exactly_the_adr_list(self) -> None:
        """Класс топлива выводится из `goods.yml::category: fuel` и совпадает с ADR."""
        fuel = sorted(
            rid for rid, recipe in self.world.catalogs.recipes.items()
            if labor._produces_fuel(self.world, recipe)
            and not labor._produces_food(self.world, recipe)
        )
        self.assertEqual(fuel, sorted(FUEL_RECIPES))

    def test_order_is_deterministic(self) -> None:
        """Один и тот же набор — один и тот же порядок (И-6)."""
        self.assertEqual(self.order(), self.order())


class TestFirewoodInWoodcuttingMonth(unittest.TestCase):
    """Конец-в-начало: в месяц лесозаготовки двор колет дрова, а не пилит."""

    def _run(self) -> object:
        world = load_scenario(SCENARIO, seed=SEED)
        for month in range(1, MONTHS + 1):
            for entry in world.script:
                if int(entry.get("at_month", 0)) == month:
                    _apply_script_entry(world, entry)
            run_month(world)
        return world

    def _woodcutting_month(self):
        """Один месяц, в котором двор РЕАЛЬНО выбрал лесозаготовку.

        **Дефект стенда, найден 2026-09 (Implementer).** Прежняя версия ждала,
        что `choose_actions` (`economy/decisions.py`, зона Economist) на
        `start_stand` сам выберет `cut_wood_if_allowed`, и 12 месяцев подряд
        ждала напрасно: пять `INITIAL_FAMILIES` тратят весь труд на сено и
        пашню, и ни разу за 12 месяцев лесозаготовка не была выбрана — замер:
        `cut_wood`/`split_logs`/`make_board` = 0 проводок за 12 месяцев. Проверка
        утверждала не закон, а ПРИОРИТЕТ выбора действий, который ей не принадлежит.

        Закон этого файла — ПОРЯДОК рецептов внутри месяца (ADR 0095, ADR 0161
        п. 1, ADR 0155), и он проверяется двумя способами:
          * `TestRecipeOrder` — порядок из `_recipe_order` (едовые первыми,
            `split_logs` раньше `make_board`/`make_plank`);
          * этот тест — конец-в-начало: двор, КОТОРЫЙ ВЫБРАЛ лесозаготовку,
            действительно колет дрова раньше, чем пилит доску.

        Поэтому стенд теперь сам создаёт условие: двор с лесом и бревном, и
        действие лесозаготовки выбрано приказом (`main`/`minor`). Всё остальное —
        очередь, приоритеты внутри месяца и баланс материи — остаётся кодом.
        """
        world = load_scenario(SCENARIO, seed=SEED)
        # Двор с собственным лесом: `_feeding_tiles`/`own_tiles` дают ему клетку.
        court = next(
            world.households[hid]
            for hid in sorted(world.households)
            if any(
                tile.terrain == "forest" for tile in labor.own_tiles(world, world.households[hid])
            )
        )
        world.households = {court.id: court}
        # Бревно на кормящей лесной клетке — иначе `split_logs`/`make_board`
        # нечего перерабатывать, и проверка доказывала бы пустоту.
        forest = next(
            tile
            for tile in labor.own_tiles(world, court)
            if tile.terrain == "forest"
        )
        world.get_stock(forest.standing_stock_id).amounts["log"] = 30.0
        world.get_stock(court.stock_id).amounts["log"] = 30.0
        court.main_action = "cut_wood_if_allowed"
        court.minor_action = "cut_wood_if_allowed"
        return world, court

    @staticmethod
    def _moved(world, reason: str, good: str, src: str | None = None) -> float:
        return sum(
            entry.amount
            for entry in world.ledger.entries
            if entry.reason == reason and entry.good == good
            and (src is None or entry.src_id == src)
        )

    def test_court_splits_firewood_and_ladder_still_reachable(self) -> None:
        """Дрова есть, лестница достижима, дельта 0 — числами, а не константой."""
        world, court = self._woodcutting_month()
        world.ledger.capture_initial(world.total_matter())
        run_month(world)
        firewood = self._moved(world, "split_logs", "firewood")
        self.assertGreater(
            firewood, 0.0,
            "Двор не колол дрова в месяц лесозаготовки: труд ушёл в распил",
        )
        boards = self._moved(world, "make_board", "board")
        self.assertGreater(boards, 0.0, "Лестница бревно → доска стала недостижимой")
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6,
            msg="Колка и распил создали материю",
        )
        # Порядок виден и в одном месяце: дрова идут раньше досок.
        self.assertGreater(
            self._moved(world, "split_logs", "firewood"),
            self._moved(world, "make_board", "board"),
            "Дров меньше, чем досок: топливо снова ушло в распил (ADR 0161 п. 1)",
        )


if __name__ == "__main__":
    unittest.main()
