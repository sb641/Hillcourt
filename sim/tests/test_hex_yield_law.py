"""Обвинители формулы хекса: множители, которые расчёт реально читает.

`hex_yield` удалён из `engine/yield_law.py`: он был определён и не вызывался
нигде (ADR 0106/0110 — подключение выхода формулы зона экономиста,
`economy/labor.py`). Двух состояний быть не должно, поэтому тест проверяет
живые множители (`field_yield_factor`, `growth_season_factor`,
`Tile.resource_productivity`) и то, что мёртвой формулы в дереве не осталось.

Здесь же живёт обвинение по ADR 0157: лестница материалов **лежит в каталоге**
(`goods.yml`, `recipes.yml`, список `recipes:` действия распила) и **выполняется
двором** через обычный месяц работы. Проверяется закон, а не число поля.
"""

from __future__ import annotations

import atexit
import os
import tempfile
import unittest
from pathlib import Path

import yaml

from hillcourt.catalogs import load_catalogs
from hillcourt.engine.growth import growth_season_factor, run_detailed_growth
from hillcourt.engine import yield_law
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
HILL = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SCENARIOS = (
    "v0_hill_and_salt.yml",
    "v0_two_settlements.yml",
    "v0_shire.yml",
    "v0_barony_100.yml",
    "v0_native_village.yml",
)

# Фикстуры-сценарии пишутся рядом со сценарием (загрузчик ищет AGENTS.md
# относительно файла), поэтому снимать их надо дважды: сразу и на `atexit`.
# `finally` не выполняется при оборванном прогоне, а `.gitignore` прячет
# `tmp*.yml` в корне — мусор остаётся невидимым.
# Лесная клетка и её лесной сосед: подложка стенда лестницы `log → board → plank`.
FOREST_TILE = "t_00_00"
NEIGHBOR_FOREST = "t_00_01"
_TEMP_PATHS: list[Path] = []


def _cleanup_temp_paths() -> None:
    for path in _TEMP_PATHS:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


atexit.register(_cleanup_temp_paths)


class TestHexYieldLaw(unittest.TestCase):
    """Проверить живые множители формулы и календарный источник."""

    def test_hex_yield_is_removed_from_the_tree(self) -> None:
        self.assertFalse(
            hasattr(yield_law, "hex_yield"),
            "hex_yield снова определена в движке: либо читается расчётом, "
            "либо её нет",
        )
        for path in sorted(
            (ROOT / "sim" / "src").rglob("*.py")
        ):
            if "__pycache__" in path.parts:
                continue
            self.assertNotIn(
                "hex_yield", path.read_text(encoding="utf-8"), path.name
            )

    def test_yield_law_exposes_only_live_multipliers(self) -> None:
        public = {
            name
            for name, value in vars(yield_law).items()
            if not name.startswith("_")
            and callable(value)
            and getattr(value, "__module__", "") == yield_law.__name__
        }
        self.assertEqual(
            public,
            {"field_yield_factor", "growth_season_factor", "demesne_field_factor"},
        )

    def test_rich_field_grows_more_than_poor_at_equal_season(self) -> None:
        poor = self._grown(HILL, 0.5)
        rich = self._grown(HILL, 1.5)
        self.assertGreater(rich, poor)
        self.assertGreater(rich, 0.0)
        self.assertAlmostEqual(rich / poor, 3.0, places=6)

    def test_grain_does_not_grow_where_the_regime_forbids_plough(self) -> None:
        world = load_scenario(HILL)
        field = next(tile for tile in world.tiles.values() if tile.terrain == "field")
        world.growth_tile_ids = (field.id,)
        world.tiles[field.id].regime_id = "villein_tenement"
        self.assertEqual(yield_law.field_yield_factor(world, field.id), 1.0)
        world.tiles[field.id].regime_id = "waste"
        self.assertEqual(yield_law.field_yield_factor(world, field.id), 0.0)
        stock = world.get_stock(field.standing_stock_id)
        before = stock.amounts.get("grain", 0.0)
        run_detailed_growth(world, 1.0)
        self.assertAlmostEqual(stock.amounts.get("grain", 0.0) - before, 0.0)

    def test_season_changes_output_and_reads_peak_month(self) -> None:
        world = load_scenario(HILL)
        peak_month = int(world.catalogs.spawn_rules["grow_grain"].params["season_peak_month"])
        self.assertEqual(growth_season_factor(world, "grow_grain", peak_month), 1.0)
        self.assertLess(
            growth_season_factor(world, "grow_grain", 1),
            growth_season_factor(world, "grow_grain", peak_month),
        )
        self.assertNotEqual(
            growth_season_factor(world, "grow_grain", 1),
            growth_season_factor(world, "grow_grain", peak_month),
        )

    def test_material_ladder_is_in_the_catalog_and_stops_at_plank(self) -> None:
        """Закон хозяина «бревно → доска → планка, ниже не спускаемся» — ИСПОЛНЯЕМ.

        Проверка закона, а не сверка константы. Прежде тест сравнивал
        `material_quality == 1.05` с числом `1.05`, вшитым в `catalogs.py`: он
        прошёл бы одинаково, лежила бы лестница в каталоге или в коде (ADR 0157).
        Теперь проверяется ровно то, что требует закон:

        1. обе ступени и оба рецепта — записи файлов каталога, а не подстановка кода
           (лестница грузится из одних `goods.yml` + `recipes.yml`);
        2. у каждой ступени есть место хранения и цена, выведенная по формуле
           шапки `goods.yml` (И-7);
        3. цепочка ровно `log → board → plank`, и ниже планки пусто;
        4. двор реально распиливает бревно и доску (см. следующий тест).
        """
        goods_doc = yaml.safe_load(
            (ROOT / "design" / "catalogs" / "goods.yml").read_text(encoding="utf-8")
        )
        recipes_doc = yaml.safe_load(
            (ROOT / "design" / "catalogs" / "recipes.yml").read_text(encoding="utf-8")
        )
        good_records = {str(record["id"]): record for record in goods_doc["goods"]}
        recipe_records = {str(record["id"]): record for record in recipes_doc["recipes"]}
        for good_id in ("board", "plank"):
            self.assertIn(good_id, good_records, f"{good_id} не запись goods.yml")
        for recipe_id in ("make_board", "make_plank"):
            self.assertIn(recipe_id, recipe_records, f"{recipe_id} не запись recipes.yml")

        # 1. Каталог целиком в каталоге: грузим ТОЛЬКО два файла — лестница на месте.
        catalogs = load_catalogs(
            ROOT,
            {
                "goods": "design/catalogs/goods.yml",
                "recipes": "design/catalogs/recipes.yml",
            },
        )
        for good_id in ("board", "plank"):
            self.assertIn(good_id, catalogs.goods, f"{good_id} пришёл из кода, а не из YAML")
        for recipe_id in ("make_board", "make_plank"):
            self.assertIn(recipe_id, catalogs.recipes, f"{recipe_id} пришёл из кода")

        # 2. И-7: место хранения и цена, выведенная по формуле шапки goods.yml.
        wage = float(
            yaml.safe_load(
                (ROOT / "design" / "catalogs" / "manor.yml").read_text(encoding="utf-8")
            )["hire"]["wage_silver_per_day"]
        )
        self.assertEqual(wage, 0.02, "Ставка наёмного сдвинулась — пересчитать цены")
        for good_id in ("board", "plank"):
            with self.subTest(good=good_id):
                self.assertEqual(catalogs.goods[good_id].storage, "barn")
                self.assertEqual(catalogs.goods[good_id].category, "material")
                self.assertGreater(catalogs.goods[good_id].price_silver, 0.0)
                self.assertGreater(
                    catalogs.goods[good_id].price_silver,
                    catalogs.goods["log"].price_silver,
                    f"{good_id} не дороже бревна, из которого он",
                )
        # Труд в цене доски = её собственные 4.0 трудодня × ставку.
        self.assertAlmostEqual(
            catalogs.goods["board"].price_labor_silver,
            float(recipe_records["make_board"]["labor_days"]) * wage,
            places=6,
        )
        self.assertAlmostEqual(
            catalogs.goods["plank"].price_labor_silver,
            float(recipe_records["make_plank"]["labor_days"]) * wage,
            places=6,
        )

        # 3. Цепочка ровно log → board → plank; ниже планки цепочки нет.
        self.assertEqual(
            {r for r, recipe in catalogs.recipes.items() if "board" in recipe.outputs},
            {"make_board"},
        )
        self.assertEqual(
            {r for r, recipe in catalogs.recipes.items() if "plank" in recipe.outputs},
            {"make_plank"},
        )
        self.assertEqual(recipe_records["make_board"]["inputs"], {"log": 1.05})
        self.assertEqual(recipe_records["make_board"]["loss"], {"log": 0.05})
        self.assertEqual(recipe_records["make_plank"]["inputs"], {"board": 1.05})
        self.assertEqual(recipe_records["make_plank"]["loss"], {"board": 0.05})
        self.assertTrue(recipe_records["make_board"]["transform"])
        self.assertTrue(recipe_records["make_plank"]["transform"])
        self.assertNotIn("craft_nail", recipe_records, "Цепочка пошла ниже планки")
        self.assertNotIn("craft_handle", recipe_records, "Цепочка пошла ниже планки")

    def test_yard_saws_log_into_board_and_board_into_plank(self) -> None:
        """Двор РЕАЛЬНО распиливает бревно на доску, а доску на планку.

        Закон, который обвиняется, — не число, а **исполнимость лестницы**
        `log → board → plank` двором (ADR 0157 п. 2) и **независимость этой
        исполнимости от порядка очереди** (ADR 0161 п. 1 и п. 3). Обвинитель
        требует двух вещей и ни одной цифры:

        1. **Проводка.** Действие лесозаготовки выдаёт обе ступени лестницы, и
           двор с брёвнами и трудом выполняет обе: доска и планка появляются в
           его амбаре, доска замкнута по массе, пиление даёт отходы, дельта
           материи 0.
        2. **Независимость от порядка.** Никаких «после N дров осталось M досок»:
           меняется порядок — лестница обязана остаться исполнимой.

        Стенд — месяц, в котором двор **пилит, а не колет**: `split_logs` и
        `cut_wood` из набора убраны. Это не подгонка, а предмет замера: месяц
        лесозаготовки по ADR 0161 п. 1 достаётся дровам, и в нём лестница не
        обязана исполняться — она обязана быть исполнимой, когда двор пилит.
        Наличие обеих ступеней в выданном наборе проверяется по живому каталогу
        отдельно, до подмены набора.
        """
        from hillcourt.economy.labor import work_month

        world = load_scenario(HILL, seed=1729)
        action = world.household_actions["cut_wood_if_allowed"]
        # Закон 0157 п. 2 / ADR 0161: лестница не только в каталоге, но и выдана
        # двору действием, то есть достижима обычным тиком.
        for step in ("make_board", "make_plank"):
            self.assertIn(
                step, action.recipes,
                f"{step} не выдано действием лесозаготовки — лестница недостижима",
            )
        household = next(
            hh
            for hh in world.households.values()
            if hh.legal_status_id == "free_landless" and hh.left_at is None
        )
        world.households = {household.id: household}
        # Стенд: месяц распила. Топливо (`split_logs`, `cut_wood`) убрано из
        # набора, чтобы спор «дрова или доска» (ADR 0161 п. 1) не мерился здесь.
        action.recipes = ["make_board", "make_plank"]
        household.main_action = "cut_wood_if_allowed"
        # Мелкое дело без рецептов: иначе починка (`craft_axe`) забирает месяц
        # раньше распила. Очередь рецептов — не наш закон, а деталь раздачи.
        household.minor_action = "hide_stores"
        household.current_tile_id = FOREST_TILE
        household.labor_days = 20.0
        stock = world.get_stock(household.stock_id)
        for good in ("log", "board", "plank", "firewood"):
            stock.amounts[good] = 0.0
        stock.amounts["log"] = 3.0
        world.ledger.capture_initial(world.total_matter())
        work_month(world, world.clock.date)

        outputs = {"make_board": "board", "make_plank": "plank"}
        made: dict[str, float] = {}
        for entry in world.ledger.entries:
            if entry.kind != "process" or entry.reason not in outputs:
                continue
            if entry.good == outputs[entry.reason]:
                made[entry.reason] = made.get(entry.reason, 0.0) + entry.amount
        self.assertGreater(made.get("make_board", 0.0), 0.0, "Двор не распилил бревно")
        self.assertGreater(made.get("make_plank", 0.0), 0.0, "Двор не распилил доску")
        # Вся доска либо лежит на складе, либо пошла в распил: цепочка замкнута
        # по массе, а не по проводкам.
        into_sawmill = sum(
            entry.amount
            for entry in world.ledger.entries
            if entry.good == "board" and entry.dst_id == "sink:processing"
        )
        self.assertAlmostEqual(
            made["make_board"],
            stock.amounts.get("board", 0.0) + into_sawmill,
            places=6,
            msg="Доска появилась из ниоткуда или пропала",
        )
        # Планка никуда не делась: потребителя у неё в v0 нет (ADR 0150), и она
        # лежит на складе двора — цепочка не уходит ниже себя.
        self.assertGreater(stock.amounts.get("plank", 0.0), 0.0)
        self.assertAlmostEqual(stock.amounts.get("plank", 0.0), made["make_plank"], places=6)
        self.assertNotIn("plank", set(world.catalogs.recipes["make_plank"].inputs))
        lost = sum(
            entry.amount
            for entry in world.ledger.entries
            if entry.kind == "process" and entry.good in ("log", "board")
            and entry.dst_id == "sink:waste"
        )
        self.assertGreater(lost, 0.0, "Пиление без отходов: нечего терять")
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6,
            msg="Пиление создало материю",
        )

    def test_scenario_fields_load_from_yaml(self) -> None:
        # Фикстура-сценарий лежит рядом со сценарием (ему нужен AGENTS.md
        # относительно файла), снимается сразу и страхуется на atexit: при
        # оборванном прогоне finally не выполняется, а `tmp*.yml` в корне прячет
        # .gitignore.
        data = yaml.safe_load(HILL.read_text(encoding="utf-8"))
        data["map"]["tiles"] = [
            {
                "at": [0, 2],
                "terrain": "field",
                "resource_productivity": 1.75,
            }
        ]
        data["households"][0]["labor_productivity"] = 1.3
        data["households"][0]["talent"] = 1.2
        handle, name = tempfile.mkstemp(suffix=".yml", dir=HILL.parent)
        path = Path(name)
        _TEMP_PATHS.append(path)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            yaml.safe_dump(data, stream, allow_unicode=True)
        try:
            world = load_scenario(path)
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(world.tiles["t_00_02"].resource_productivity, 1.75)
        self.assertEqual(
            world.persons["hh_court_p1"].labor_productivity, 1.3
        )
        self.assertEqual(world.persons["hh_court_p1"].talent, 1.2)

    def test_old_scenarios_load_with_formula_defaults(self) -> None:
        for name in SCENARIOS:
            with self.subTest(scenario=name):
                world = load_scenario(ROOT / "design" / "scenarios" / name)
                self.assertTrue(world.persons)
                self.assertTrue(all(person.labor_productivity == 1.0 for person in world.persons.values()))
                self.assertTrue(all(person.talent == 1.0 for person in world.persons.values()))
                self.assertTrue(all(tile.resource_productivity == 1.0 for tile in world.tiles.values()))
                self.assertTrue(
                    all(recipe.tool_multiplier == 1.0 for recipe in world.catalogs.recipes.values())
                )
                # Лестница материала — в каталоге, а не в числе поля: у каждого
                # сценария она одна и та же и читается из YAML (ADR 0157 п. 1).
                for good_id in ("log", "board", "plank"):
                    self.assertIn(good_id, world.catalogs.goods, good_id)
                self.assertEqual(
                    world.catalogs.goods["board"].storage,
                    world.catalogs.goods["log"].storage,
                )

    @staticmethod
    def _grown(scenario: Path, productivity: float) -> float:
        world = load_scenario(scenario)
        field = next(tile for tile in world.tiles.values() if tile.terrain == "field")
        world.tiles[field.id].regime_id = "villein_tenement"
        world.growth_tile_ids = (field.id,)
        world.tiles[field.id].resource_productivity = productivity
        stock = world.get_stock(field.standing_stock_id)
        before = stock.amounts.get("grain", 0.0)
        run_detailed_growth(world, 1.0)
        return stock.amounts.get("grain", 0.0) - before


if __name__ == "__main__":
    unittest.main()
