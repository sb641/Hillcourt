"""Проверка данных каталогов: id, ссылки, массовый баланс, числа по закону."""

from __future__ import annotations

import ast
import re
import tempfile
import unittest
from pathlib import Path

import yaml

from hillcourt.catalogs import (
    GROW_RULE_PARAMS,
    LAND_ACTIONS,
    LAND_ACTION_RECIPES,
    load_catalogs,
)

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "sim" / "src"
SOIL_PY = SRC / "hillcourt" / "economy" / "soil.py"
# Единственный файл, конструирующий `SpawnRule` во всём дереве. Исключается из
# подсчёта чтений поля: там ключ читается, чтобы ЗАПОЛНИТЬ поле, а не потребить.
SPAWN_RULE_CTOR = "catalogs.py"
SPAWN_RULES = ROOT / "design" / "catalogs" / "spawn_rules.yml"
GOODS_YML = ROOT / "design" / "catalogs" / "goods.yml"
RECIPES_YML = ROOT / "design" / "catalogs" / "recipes.yml"
ACTIONS_YML = ROOT / "design" / "catalogs" / "actions_household.yml"
MANOR_YML = ROOT / "design" / "catalogs" / "manor.yml"
# Коэффициент дефицита камня: класс материала местного изобилия (ADR 0161 п. 5,
# ADR 0177). Соль и железо — 1.3, зерно и топор — 1.2, камень — 1.1.
STONE_DEFICIT = 1.1
# Лестница материала: бревно → доска → планка, ниже не спускаемся (ADR 0106 п. 3).
LADDER = ("log", "board", "plank")
# Коэффициент дефицита: доска и планка — материал местного изобилия, как
# `log`/`firewood`/`peat`/`hay`, а не соль или железо.
LADDER_DEFICIT = 1.1
# Право земли, выданное режимом, но не исполняемое НИЧЕМ: ни рецептом двора, ни
# `LAND_ACTION_RECIPES`, ни веткой в коде. Объявление снято (ADR 0161 п. 2), но
# право ухода честно не исполняет ничего: `legal/regimes.py:242` его только
# ВЫБРАСЫВАЕТ, а предикатом никто не пользуется. Это не наш долг и не наше
# право — снимать или достраивать право ухода решает новый ADR. Список точный:
# новый призрак тест поймает (см. `test_granted_rights_are_enforceable`).
PENDING_UNENFORCEABLE: tuple[str, ...] = ("leave",)
# Множество имён, по которым движок реально ветвится вместе с `allowed_actions`.
# Проба: литерал действия, следом оператор проверки вхождения (`in` / `not in`).
# Слабость метода честно: он доказывает РАЗБОР по имени, но не достижимость
# самой ветки.
_MEMBERSHIP_PROBE = re.compile(r'"(?P<action>[a-z_]+)"\s+(?:not\s+)?in\s')

VALID_CATEGORY = {"food", "fuel", "material", "lux", "livestock"}
VALID_STORAGE = {"granary", "cellar", "barn", "pack", "open"}
VALID_TERRAIN = {
    "hill", "field", "pasture", "forest", "marsh", "heath", "salt_flat", "ruin", "water",
}
VALID_TARGET = {"hazard", "pack", "good", "ruin"}
PRICE_REQUIRED = {
    "grain", "flour", "salt", "milk", "cheese", "butter", "eggs", "meat",
    "hide", "wool", "roots", "greens", "mushrooms", "berries", "hay",
    "firewood", "peat", "iron", "axe", "butter_churn",
}


class TestCatalogRules(unittest.TestCase):
    """DoD каталога: id/name, storage, баланс рецептов, ссылки, руина."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.catalogs = load_catalogs(ROOT)

    def test_goods(self) -> None:
        self.assertTrue(self.catalogs.goods, "Каталог товаров пуст")
        for gid in sorted(self.catalogs.goods):
            good = self.catalogs.goods[gid]
            self.assertTrue(good.id, "У товара нет id")
            self.assertTrue(good.name, f"У товара {gid} нет name")
            self.assertIn(good.storage, VALID_STORAGE, f"{gid}: плохой storage")
            self.assertIn(good.category, VALID_CATEGORY, f"{gid}: плохая category")

    def test_good_prices_follow_full_path(self) -> None:
        for good_id in sorted(PRICE_REQUIRED):
            with self.subTest(good=good_id):
                self.assertGreater(self.catalogs.goods[good_id].price_silver, 0.0)

    def test_good_without_price_path_has_zero_price(self) -> None:
        good = self.catalogs.goods["straw"]
        self.assertEqual(good.price_silver, 0.0)
        self.assertEqual(good.price_labor_silver, 0.0)
        self.assertEqual(good.price_materials_silver, 0.0)
        self.assertEqual(good.price_losses_silver, 0.0)

    def test_silver_measure_does_not_recurse(self) -> None:
        silver = self.catalogs.goods["silver"]
        self.assertEqual(silver.price_silver, 1.0)
        self.assertEqual(silver.price_labor_silver, 0.0)
        self.assertEqual(silver.price_materials_silver, 0.0)
        self.assertEqual(silver.price_losses_silver, 0.0)

    def test_recipes(self) -> None:
        self.assertTrue(self.catalogs.recipes, "Каталог рецептов пуст")
        for rid in sorted(self.catalogs.recipes):
            recipe = self.catalogs.recipes[rid]
            left = sum(recipe.inputs.values()) + sum(recipe.draws_standing.values())
            right = sum(recipe.outputs.values()) + sum(recipe.loss.values())
            self.assertAlmostEqual(left, right, places=6, msg=f"{rid}: нет баланса")
            self.assertGreater(recipe.labor_days, 0.0, f"{rid}: labor_days <= 0")
            for good in (
                list(recipe.inputs)
                + list(recipe.draws_standing)
                + list(recipe.outputs)
                + list(recipe.loss)
            ):
                self.assertIn(good, self.catalogs.goods, f"{rid}: неизвестный товар {good}")
            for terrain in recipe.requires_terrain:
                self.assertIn(terrain, VALID_TERRAIN, f"{rid}: плохой террейн {terrain}")
            if not recipe.transform:
                sources = set(recipe.inputs) | set(recipe.draws_standing)
                for good in list(recipe.outputs) + list(recipe.loss):
                    self.assertIn(
                        good,
                        sources,
                        f"{rid}: товар '{good}' не объявлен на входе и рецепт "
                        f"не помечен transform",
                    )

    def test_spawn_rules(self) -> None:
        self.assertTrue(self.catalogs.spawn_rules, "Каталог появления пуст")
        for sid in sorted(self.catalogs.spawn_rules):
            rule = self.catalogs.spawn_rules[sid]
            self.assertIn(rule.target, VALID_TARGET, f"{sid}: плохой target")
            if rule.target == "good":
                self.assertIn("good", rule.params, f"{sid}: нет params.good")
                self.assertIn(
                    rule.params["good"], self.catalogs.goods, f"{sid}: товар не существует"
                )
                self.assertIn("terrain", rule.params, f"{sid}: нет params.terrain")

    def test_obligation_and_right_templates(self) -> None:
        # Пустой раздел каталога — не «всё хорошо», а отсутствие закона: цикл по
        # пустому словарю проходит зелёным и охраняет пустоту (ADR 0155).
        self.assertTrue(
            self.catalogs.obligation_templates,
            "Раздел obligations: каталога пуст — ни одной повинности",
        )
        self.assertTrue(
            self.catalogs.right_templates,
            "Раздел rights: каталога пуст — ни одного права",
        )
        for tid in sorted(self.catalogs.obligation_templates):
            template = self.catalogs.obligation_templates[tid]
            self.assertTrue(template.id and template.name and template.kind)
            if template.default_share is not None:
                self.assertGreaterEqual(template.default_share, 0.0)
                self.assertLessEqual(template.default_share, 1.0)
        for tid in sorted(self.catalogs.right_templates):
            template = self.catalogs.right_templates[tid]
            self.assertTrue(template.id and template.name and template.kind)
            if template.default_rent_share is not None:
                self.assertGreaterEqual(template.default_rent_share, 0.0)
                self.assertLessEqual(template.default_rent_share, 1.0)

    def test_ruin_rule_is_unknown_without_loot(self) -> None:
        ruins = [
            rule
            for rule in self.catalogs.spawn_rules.values()
            if rule.target == "ruin"
        ]
        self.assertTrue(ruins, "Нет правила появления руины")
        rule = ruins[0]
        self.assertEqual(rule.params.get("mode"), "unknown")
        self.assertIn(rule.params.get("loot"), (None, "none"))
        self.assertIn(rule.params.get("relic"), (None, "none"))


def _soil_param_keys() -> set[str]:
    """Ключи `grow_grain.params`, которые читает `soil.py` через `_number`/`_table`.

    Ключи извлекаются разбором AST вызовов в исходнике `soil.py` — **не** из
    `GROW_RULE_PARAMS`. Иначе проверка круговая: забытый ключ отсутствовал бы
    в обоих множествах сразу, и тест остался бы зелёным (ADR 0161: главная
    опасность задачи — своя же проверка против своего же списка).
    """
    tree = ast.parse(SOIL_PY.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in ("_number", "_table"):
            continue
        if len(node.args) < 2:
            continue
        key = node.args[1]
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.add(key.value)
    return keys


def _spawn_rule_fields() -> list[str]:
    """Поля `SpawnRule` по объявлению онтологии."""
    from dataclasses import fields

    from hillcourt.ontology import SpawnRule

    return [f.name for f in fields(SpawnRule)]


def _field_reads(field: str) -> list[str]:
    """Где поле читается: `.f`, `["f"]`, `.get("f")`, `getattr(...,"f")`.

    Файл-конструктор исключён: там ключ читается, чтобы **заполнить** поле, — это
    запись, а не потребитель. Единственный конструктор `SpawnRule` во всём дереве —
    `catalogs.py`, и исключение это не список полей, а факт о конструкторе, поэтому
    проверка не circular.
    """
    hits: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == SPAWN_RULE_CTOR:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Attribute) and node.attr == field
                or isinstance(node, ast.Subscript)
                and isinstance(node.slice, ast.Constant)
                and node.slice.value == field
                or isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == field
                or isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == field
            ):
                hits.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return hits


class TestSpawnRuleHasNoDeadFields(unittest.TestCase):
    """Симметричное правило ADR 0164 п. 1: и на отсутствие, и на присутствие.

    ADR 0157 закрыл случай «поле объявлено и грузится, а читает его ноль
    потребителей» для `Good.material_quality`. ADR 0164 п. 2 требует того же для
    ярлыка происхождения у `SpawnRule`. Закон держится на **общем** правиле, а не
    на записи «а вот это поле»: проверка оба раза ломается при воскрешении поля.

    Три формы проверки обязательны (ADR 0164 п. 1):

    * узкая — литерал в трёх местах (онтология, загрузчик, `spawn_rules.yml`),
      жёстко 0 совпадений;
    * широкая — чтения через AST по всему `sim/src` (см. `_field_reads`);
    * исполнение — тесты этого класса: ноль чтений роняет, фантом роняет.
    """

    @classmethod
    def setUpClass(cls) -> None:
        doc = yaml.safe_load(SPAWN_RULES.read_text(encoding="utf-8"))
        cls.records = doc["spawn_rules"]
        cls.fields = _spawn_rule_fields()
        cls.yaml_keys = {key for record in cls.records for key in record}

    def test_narrow_form_no_occurrence_in_ontoloader_or_yaml(self) -> None:
        """Форма 1: литерала нет ни в онтологии, ни в загрузчике, ни в YAML."""
        found = []
        for path in (
            SRC / "hillcourt" / "ontology.py",
            SRC / "hillcourt" / "catalogs.py",
            SPAWN_RULES,
        ):
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if "external" in line:
                    found.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
        self.assertEqual(
            found, [], "Ярлык происхождения вернулся в дерево:\n" + "\n".join(found)
        )

    def test_wide_form_every_field_is_read_by_somebody(self) -> None:
        """Форма 2: у каждого поля `SpawnRule` есть хотя бы одно чтение в `sim/src`.

        Ровно то, чем был плох ярлык происхождения: он был объявлен, грузился и
        не читался. Проверка идёт от кода, а не от своего же списка полей.
        """
        for field in self.fields:
            with self.subTest(field=field):
                reads = _field_reads(field)
                self.assertTrue(
                    reads,
                    f"SpawnRule.{field} объявлено и грузится, но не читается нигде "
                    f"в sim/src (ADR 0164 п. 2 — снять или найти потребителя)",
                )

    def test_no_phantom_field_in_spawn_rule(self) -> None:
        """Сторона «на присутствие»: фантом роняет тест.

        Фантом — поле, которое нечем наполнить: его нет ни в ключах загрузчика,
        ни в записях `spawn_rules.yml`. Поле `zzz_probe`, вписанное в онтологию
        «на будущее», ломает эту проверку.
        """
        loader_keys = _spawn_rule_loader_keys()
        for field in self.fields:
            with self.subTest(field=field):
                self.assertIn(
                    field, loader_keys,
                    f"SpawnRule.{field} не принимается загрузчиком: поле-фантом, "
                    "наполнить его нечем",
                )
                self.assertIn(
                    field, self.yaml_keys,
                    f"SpawnRule.{field} не встречается ни в одной записи "
                    "spawn_rules.yml: данные поля не существуют",
                )

    def test_no_orphan_key_in_spawn_rules_yaml(self) -> None:
        """Обратная сторона: ключ YAML без поля — правило, которое некуда деть."""
        loader_keys = _spawn_rule_loader_keys()
        orphans = sorted(self.yaml_keys - loader_keys)
        self.assertEqual(
            orphans, [],
            "spawn_rules.yml содержит ключи, которых нет в загрузчике: "
            f"{orphans} (загрузчик их отбросит или он перестанет грузиться)",
        )

    def test_spawn_rules_still_load_with_the_origin_label_gone(self) -> None:
        """Форма 3, исполнение: каталог грузится, поток несёт `rule_id`, а не ярлык.

        И-1 доказывается непустым `LedgerEntry.rule_id` (`test_matter_conservation`),
        и вот это здесь и подтверждаемо: у выданных правил есть `kind` и `target`,
        по которым фаза роста и берёт поток, а происхождение пишется в проводку.
        """
        rules = load_catalogs(ROOT).spawn_rules
        self.assertTrue(rules)
        calendric = [r for r in rules.values() if r.kind == "calendric" and r.target == "good"]
        self.assertTrue(calendric, "Ни одного календарного правила появления goods")
        for rule in calendric:
            with self.subTest(rule=rule.id):
                self.assertTrue(rule.id, "У правила нет id — проводка не получит rule_id")
                self.assertNotIn(
                    "external", vars(rule),
                    "В правиле снова появился ярлык происхождения",
                )


def _spawn_rule_loader_keys() -> set[str]:
    """Ключи, которые принимает `_parse_spawn_rules` — из её тела, не из списка."""
    from hillcourt import catalogs as catalogs_module

    source = (SRC / "hillcourt" / "catalogs.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_parse_spawn_rules":
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Assign)
                    and any(
                        isinstance(t, ast.Name) and t.id == "known" for t in inner.targets
                    )
                    and isinstance(inner.value, ast.Set)
                ):
                    return {
                        element.value
                        for element in inner.value.elts
                        if isinstance(element, ast.Constant)
                    }
    raise AssertionError("Не нашёл множество known в _parse_spawn_rules")


class TestSoilNumbersAreCatalogData(unittest.TestCase):
    """Прибавки пашни — числа `spawn_rules.yml`, а не числа Python (ADR 0157 п. 5).

    Раньше `economy/soil.py` держал двенадцать `DEFAULT_*`, дословно повторявших
    `grow_grain.params`. Fallback не срабатывал, пока ключ на месте, но стоило
    кому-то удалить ключ — и урожай тихо считался бы по зашитым в коде числам.
    Теперь отсутствие ключа — ошибка загрузки, и это проверяется здесь.
    """

    def _load_spawn_rules(self, drop: str | None = None):
        """Загрузить `spawn_rules.yml` во временной папке, выбросив один ключ."""
        data = yaml.safe_load(SPAWN_RULES.read_text(encoding="utf-8"))
        if drop is not None:
            for rule in data["spawn_rules"]:
                if rule["id"] == "grow_grain":
                    del rule["params"][drop]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spawn_rules.yml"
            path.write_text(
                yaml.safe_dump(data, allow_unicode=True), encoding="utf-8"
            )
            return load_catalogs(Path(directory), {"spawn_rules": path.name})

    def test_grow_grain_carries_every_number_the_soil_module_reads(self) -> None:
        params = load_catalogs(ROOT).spawn_rules["grow_grain"].params
        for key in _soil_param_keys():
            with self.subTest(param=key):
                self.assertIn(key, params, "Прибавка пашни не имеет числа в каталоге")

    def test_required_param_list_is_exactly_what_soil_reads(self) -> None:
        """Доказательство равенства множеств — ГЛАВНОЕ здесь.

        `GROW_RULE_PARAMS` обязан совпадать с ключами, которые `soil.py` читает
        через `_number`/`_table`, **ровно**: лишний ключ в списке — мёртвая
        проверка, недостающий — незакрытый путь подмены числа. Ключи берутся из
        исходника `soil.py` разбором AST, а не из самого `GROW_RULE_PARAMS`,
        иначе проверка была бы круговой и пропустила бы забытый ключ.
        """
        read = _soil_param_keys()
        required = set(GROW_RULE_PARAMS)
        self.assertEqual(
            read - required, set(),
            "soil.py читает ключ, которого нет в GROW_RULE_PARAMS: удаление ключа "
            "из grow_grain.params пройдёт незамеченным, и урожай молча посчитается "
            "не по каталогу (ADR 0161, долг 12↔11)",
        )
        self.assertEqual(
            required - read, set(),
            "GROW_RULE_PARAMS требует ключ, который soil.py не читает: проверка "
            "загрузки врёт и не защищает ни одного числа",
        )
        self.assertEqual(read, required)

    def test_missing_key_fails_the_load_instead_of_quietly_substituting(self) -> None:
        """Каждый ключ, который читает `soil.py`: нет ключа — падает загрузка.

        Ключи извлечены из вызовов `_number`/`_table` в `soil.py`, а не взяты из
        `GROW_RULE_PARAMS`, — иначе тест проверял бы сам себя.
        """
        keys = _soil_param_keys()
        self.assertTrue(keys, "Из soil.py не извлеклось ни одного ключа")
        for key in sorted(keys):
            with self.subTest(param=key):
                with self.assertRaises(ValueError) as caught:
                    self._load_spawn_rules(drop=key)
                self.assertIn(key, str(caught.exception))

    def test_soil_module_holds_no_fallback_numbers(self) -> None:
        """Ни одного `DEFAULT_*` в `soil.py`: дубль числа каталога не вернётся."""
        text = (SRC / "hillcourt" / "economy" / "soil.py").read_text(encoding="utf-8")
        self.assertNotIn(
            "DEFAULT_", text,
            "В soil.py вернулось запасное число экономики: отсутствие ключа "
            "снова станет тихой подменой (ADR 0157 п. 5)",
        )

    def test_soil_readers_have_no_second_argument_to_fall_back_on(self) -> None:
        """`_number`/`_table` берут только каталог: параметра-заменителя нет."""
        import inspect

        from hillcourt.economy import soil

        for reader in (soil._number, soil._table):
            with self.subTest(reader=reader.__name__):
                self.assertEqual(
                    list(inspect.signature(reader).parameters), ["params", "key"],
                    "У читателя каталога снова появился запасной аргумент",
                )
        with self.assertRaises(ValueError):
            soil._number({}, "draft_yield_cap")
        with self.assertRaises(ValueError):
            soil._table({}, "tool_yield")

    def test_readers_reject_a_catalog_number_that_is_not_a_number(self) -> None:
        """Битое значение — тоже ошибка, а не тихий ноль."""
        from hillcourt.economy import soil

        with self.assertRaises(ValueError):
            soil._number({"manure_uptake": "много"}, "manure_uptake")
        with self.assertRaises(ValueError):
            soil._table({"tool_yield": 1.05}, "tool_yield")
        with self.assertRaises(ValueError):
            soil._table({"tool_yield": {"wooden_plough": "плуг"}}, "tool_yield")
        with self.assertRaises(ValueError):
            soil._table({"tool_yield": {}}, "tool_yield")

    def test_grow_grain_rule_itself_cannot_vanish(self) -> None:
        """Нет правила — тоже ошибка: `_params` не возвращает пустой словарь."""
        from hillcourt.economy import soil
        from hillcourt.scenario import load_scenario

        world = load_scenario(ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml")
        del world.catalogs.spawn_rules["grow_grain"]
        with self.assertRaises(ValueError):
            soil.rotation_month(world)


class TestMaterialLadderPrices(unittest.TestCase):
    """Цены доски и планки ВЫВЕДЕНЫ по формуле шапки `goods.yml`, а не выдуманы.

    Формула шапки: `цена = (труд + сырьё + потери) × коэффициент дефицита`, где
    труд — `labor_days` выпускающего рецепта × ставка, а сырьё и потери — входы
    по ПОЛНОЙ себестоимости (труд+сырьё+потери входа), не по рыночной цене.
    Проверка держит числа каталога на этом правиле: подменить цену доски
    «на глазок» нельзя, не сломав тест.
    """

    @classmethod
    def setUpClass(cls) -> None:
        goods_doc = yaml.safe_load(GOODS_YML.read_text(encoding="utf-8"))
        cls.goods = {str(r["id"]): r for r in goods_doc["goods"]}
        cls.recipes = {
            str(r["id"]): r
            for r in yaml.safe_load(RECIPES_YML.read_text(encoding="utf-8"))["recipes"]
        }
        cls.wage = float(
            yaml.safe_load(MANOR_YML.read_text(encoding="utf-8"))["hire"][
                "wage_silver_per_day"
            ]
        )

    def cost(self, good: str) -> float:
        record = self.goods[good]
        return sum(
            float(record.get(field, 0.0))
            for field in (
                "price_labor_silver", "price_materials_silver", "price_losses_silver"
            )
        )

    def test_ladder_rungs_exist_in_both_catalogs(self) -> None:
        for good in LADDER:
            self.assertIn(good, self.goods, f"{good} не запись goods.yml")
        for recipe_id in ("make_board", "make_plank"):
            self.assertIn(recipe_id, self.recipes, f"{recipe_id} не запись recipes.yml")
        for good in ("board", "plank"):
            self.assertEqual(self.goods[good]["storage"], "barn")
            self.assertEqual(self.goods[good]["category"], "material")

    def test_each_rung_is_derived_from_the_rung_below(self) -> None:
        """`log → board → plank`: труд, сырьё и потери каждой ступени — снизу."""
        pairs = (("board", "make_board", "log"), ("plank", "make_plank", "board"))
        for good, recipe_id, source in pairs:
            with self.subTest(good=good):
                recipe = self.recipes[recipe_id]
                out = float(recipe["outputs"][good])
                self.assertGreater(out, 0.0)
                self.assertAlmostEqual(
                    float(self.goods[good]["price_labor_silver"]),
                    float(recipe["labor_days"]) * self.wage / out,
                    places=5,
                    msg="Труд в цене не равен трудодням рецепта × ставка",
                )
                self.assertAlmostEqual(
                    float(self.goods[good]["price_materials_silver"]),
                    sum(
                        float(qty) * self.cost(name)
                        for name, qty in sorted(recipe["inputs"].items())
                    ) / out,
                    delta=1e-4,
                    msg="Сырьё в цене посчитано не по себестоимости входа",
                )
                self.assertAlmostEqual(
                    float(self.goods[good]["price_losses_silver"]),
                    sum(
                        float(qty) * self.cost(name)
                        for name, qty in sorted(recipe["loss"].items())
                    ) / out,
                    delta=1e-4,
                    msg="Потери в цене посчитаны не по себестоимости входа",
                )
                self.assertAlmostEqual(
                    float(self.goods[good]["price_silver"]),
                    self.cost(good) * LADDER_DEFICIT,
                    places=5,
                    msg="Цена не равна (труд+сырьё+потери) × коэффициент дефицита",
                )

    def test_ladder_costs_more_than_the_log_below(self) -> None:
        """Распил дороже бревна, планка дороже доски: цена шла вверх по лестнице."""
        log, board, plank = (self.cost(good) for good in LADDER)
        self.assertLess(log, board)
        self.assertLess(board, plank)

    def test_chain_stops_at_plank(self) -> None:
        """Ниже планки цепочки нет: гвозди, рукояти, скобы — `concept_only`."""
        for good in ("nail", "handle", "peg", "bracket", "hinge"):
            with self.subTest(good=good):
                self.assertNotIn(good, self.goods, "Цепочка пошла ниже планки")
        consumers = {
            recipe_id
            for recipe_id, recipe in self.recipes.items()
            if "plank" in recipe.get("inputs", {})
        }
        self.assertEqual(consumers, set(), "У планки появился потребитель без закона")

    def test_ladder_is_reachable_by_a_household_action(self) -> None:
        """Ступень лестницы объявлена — и должна быть ДОСТИЖИМА двора (ADR 0157 п. 2).

        Проверка от объявления ничего не значит: рецепт, на который ни одно действие
        не ссылается, каталогом помечен, а двором невыполним. Закон требует, чтобы
        `make_board` и `make_plank` стояли в списке `recipes:` хотя бы одного
        действия (`cut_wood_if_allowed`, рядом с `split_logs`).
        """
        actions = yaml.safe_load(ACTIONS_YML.read_text(encoding="utf-8"))
        granted: set[str] = set()
        for record in actions.get("actions", []):
            granted |= set(record.get("recipes") or [])
        for recipe_id in ("make_board", "make_plank"):
            with self.subTest(recipe=recipe_id):
                self.assertIn(
                    recipe_id, granted,
                    "Ступень лестницы есть в recipes.yml, но ни одно действие двора "
                    "её не разрешает: объявление, которое двор выполнить не может",
                )
                self.assertIn(
                    recipe_id, self.recipes,
                    "Рецепт лестницы разрешён действием, но в каталоге его нет",
                )


class TestClearingGivesLogsAndStone(unittest.TestCase):
    """Расчистка леса (ADR 0177): камень учтён ДО вырубки, иначе вырубка — кран.

    Закон состоит из четырёх связанных частей, и проверяется каждая, а не «числа
    на месте»:

    1. **Камень — объект материи по И-7:** у него есть место хранения и правило
       появления, то есть он учтён в клетке **до** того, как рецепт его возьмёт.
       Рецепт, берущий материю, которой никогда не бывает, — это кран.
    2. **Потери читаются из учтённой материи клетки:** `draws_standing` не пуст,
       `loss` не пуст и не реден, баланс партии сходится.
    3. **Труд большой:** расчистка — не действие на месяц.
    4. **Право не призрак:** действие земли объявлено, выдано режимом и
       исполняемо рецептом, который разрешает действие двора.
    """

    RECIPE = "uproot_stumps"
    RIGHT = "clear_forest"
    GOOD = "stone"
    RULE = "grow_stone"
    FOREST_CAP_RULING = 3  # сколько партий расчистки должно помещаться в клетку

    @classmethod
    def setUpClass(cls) -> None:
        recipes_doc = yaml.safe_load(RECIPES_YML.read_text(encoding="utf-8"))
        cls.recipes = {str(r["id"]): r for r in recipes_doc["recipes"]}
        cls.goods = {
            str(r["id"]): r
            for r in yaml.safe_load(GOODS_YML.read_text(encoding="utf-8"))["goods"]
        }
        cls.rules = {
            str(r["id"]): r
            for r in yaml.safe_load(SPAWN_RULES.read_text(encoding="utf-8"))["spawn_rules"]
        }
        cls.catalogs = load_catalogs(ROOT)
        cls.recipe = cls.recipes[cls.RECIPE]

    # --- 1. камень учтён ДО вырубки -------------------------------------------------
    def test_stone_is_a_matter_object_with_storage_and_spawn_rule(self) -> None:
        """И-7: место хранения + правило появления. Без них камень — фантом."""
        self.assertIn(self.GOOD, self.goods, "Камень не товар")
        good = self.goods[self.GOOD]
        self.assertTrue(good["name"], "У камня нет русского name")
        self.assertEqual(good["name"], "Камень")
        self.assertIn(good["storage"], {"granary", "cellar", "barn", "pack", "open"})
        self.assertEqual(good["category"], "material")
        self.assertIn(self.RULE, self.rules, "У камня нет правила появления")
        rule = self.rules[self.RULE]
        self.assertEqual(rule["target"], "good")
        self.assertEqual(rule["kind"], "calendric", "Камень не появится: не calendric")
        self.assertEqual(rule["params"]["good"], self.GOOD)
        self.assertEqual(
            rule["params"]["terrain"], "forest",
            "Камень лежит в лесу, а не в пашне",
        )
        self.assertGreater(float(rule["params"]["amount"]), 0.0)
        self.assertGreater(float(rule["params"]["cap_per_tile"]), 0.0)

    def test_stone_appears_in_the_same_tiles_the_recipe_clears(self) -> None:
        """Правило камня и рецепт расчистки смотрят на ОДИН рельеф.

        Иначе камень «появится» на клетке, которую никто не расчищает, и рецепт
        будет просить материю, которой на его клетке нет.
        """
        rule_terrain = str(self.rules[self.RULE]["params"]["terrain"])
        self.assertEqual(
            list(self.recipe["requires_terrain"]), [rule_terrain],
            "Правило появления камня и рецепт расчистки работают на разных рельефах",
        )

    def test_recipe_actually_draws_the_accounted_stone(self) -> None:
        """Рецепт берёт из клетки то, что правило туда кладёт, — не из воздуха."""
        drawn = float(self.recipe["draws_standing"].get(self.GOOD, 0.0))
        self.assertGreater(drawn, 0.0, "Рецепт не берёт учтённый камень из клетки")
        cap = float(self.rules[self.RULE]["params"]["cap_per_tile"])
        self.assertGreater(
            cap, drawn * 0.5,
            "Потолок камня меньше половины партии: добывать можно, но нечем",
        )

    # --- 2. потери из учтённой материи, баланс ---------------------------------------
    def test_losses_are_mandatory_and_read_from_accounted_matter(self) -> None:
        """Потери обязательны: пни, корни, опилки, негодная древесина.

        Пустой `loss` означал бы, что из ямы достали 9.6 камня и ничего не
        оставили, — то есть операция создаёт материю (И-1).
        """
        loss = self.recipe.get("loss") or {}
        self.assertTrue(loss, "У расчистки нет потерь: она создаёт материю")
        for good, qty in loss.items():
            with self.subTest(loss=good):
                self.assertIn(good, self.goods, f"Потеря {good} — не товар")
                self.assertGreater(float(qty), 0.0)
        self.assertIn("log", loss, "Снятая древесина не объявлена потерей")
        self.assertTrue(self.recipe["draws_standing"], "Материя берётся из воздуха")

    def test_clearing_batch_is_balanced(self) -> None:
        left = sum(self.recipe.get("inputs", {}).values()) + sum(
            self.recipe["draws_standing"].values()
        )
        right = sum(self.recipe["outputs"].values()) + sum(self.recipe["loss"].values())
        self.assertAlmostEqual(left, right, places=6, msg="Партия расчистки не сбалансирована")

    def test_clearing_is_marked_transform(self) -> None:
        """`transform: true` обязателен и проверяется явно: каменя на входе нет."""
        self.assertIs(self.recipe.get("transform"), True)
        self.assertNotIn(
            self.GOOD, self.recipe.get("inputs", {}),
            "Камень объявлен входом — transform не нужен и не честен",
        )
        self.assertIs(
            self.catalogs.recipes[self.RECIPE].transform, True,
            "Загрузчик потерял transform: true",
        )

    def test_clearing_is_not_a_one_month_job(self) -> None:
        """Труд большой: расчистка не влезает в месяц одного взрослого.

        Норма взрослого — 20 трудодней в месяц (`seasons`/`labor`), поэтому
        «большой труд» здесь = больше месячной нормы, а не «чуть больше
        `cut_wood`».
        """
        labor = float(self.recipe["labor_days"])
        self.assertGreater(labor, 20.0, "Расчистка укладывается в один месяц")
        self.assertGreaterEqual(labor, 5.0 * float(self.recipes["cut_wood"]["labor_days"]))

    # --- 3. потолок лесной материи --------------------------------------------------
    def test_forest_matter_cap_holds_a_ruling_number_of_clearings(self) -> None:
        """Потолок лесной материи — кратное партий расчистки, а не круглое число.

        Закон: на лесной клетке должно помещаться ровно `FOREST_CAP_RULING`
        расчисток. Потолок берётся не «красивым числом», а умножением одной
        партии на это число — иначе «большое количество» нечем измерить.
        """
        for good, rule_id in (("log", "grow_log"), (self.GOOD, self.RULE)):
            with self.subTest(good=good):
                cap = float(self.rules[rule_id]["params"]["cap_per_tile"])
                drawn = float(
                    self.recipe["draws_standing"].get(good, 0.0)
                    or self.recipe["loss"].get(good, 0.0)
                )
                self.assertAlmostEqual(
                    cap, drawn * self.FOREST_CAP_RULING, places=6,
                    msg=f"{rule_id}: потолок {cap} не кратен партии расчистки {drawn}",
                )

    def test_forest_matter_cap_is_big_enough_to_matter(self) -> None:
        """«Большое количество» измеримо: клетка леса тянет больше прежнего.

        Прежний потолок бревна был 10.0 — меньше одной партии расчистки, то есть
        расчистку нельзя было повторить. Закон требует, чтобы клетка пережила
        несколько расчисток подряд.
        """
        cap = float(self.rules["grow_log"]["params"]["cap_per_tile"])
        self.assertGreaterEqual(cap, 24.0, "Клетка леса не вмещает двух расчисток")
        stone_cap = float(self.rules[self.RULE]["params"]["cap_per_tile"])
        self.assertGreaterEqual(
            stone_cap, 24.0, "Камень в клетке не вмещает двух расчисток"
        )

    def test_clearing_does_not_make_the_forest_an_infinite_source(self) -> None:
        """Лес — конечный актив: клетка восстанавливается годами (И-1).

        Мера честности — **время восстановления**: потолок делённый на годовой
        прирост. Расчистка одной клетки обязана быть такой, чтобы лес не успевал
        вернуться, иначе вырубка была бы краном: сколько бы ни вырубили, мир
        только рос бы. Порог — 2 года: дешевле лес не восстановит.
        """
        for rule_id in ("grow_log", self.RULE):
            with self.subTest(rule=rule_id):
                params = self.rules[rule_id]["params"]
                yearly = float(params["amount"]) * 12.0
                cap = float(params["cap_per_tile"])
                years = cap / yearly
                self.assertGreaterEqual(
                    years, 2.0,
                    f"{rule_id}: клетка восстанавливается за {years:.1f} года — "
                    "лес оборачивается быстрее расчистки, это кран материи",
                )

    # --- 4. право не призрак --------------------------------------------------------
    def test_right_is_declared_granted_and_backed_by_a_recipe(self) -> None:
        """Право объявлено, выдано и исполняемо — иначе это `build_hut` (ADR 0150)."""
        self.assertIn(self.RIGHT, LAND_ACTIONS, "Право не объявлено в LAND_ACTIONS")
        granting = [
            regime_id
            for regime_id, regime in self.catalogs.land_regimes.items()
            if self.RIGHT in regime.allowed_actions
        ]
        self.assertTrue(granting, "Ни один режим земли не выдаёт расчистку")
        self.assertIn(self.RECIPE, self.catalogs.recipes, "Рецепта расчистки нет")

    def test_right_and_recipe_carry_different_ids(self) -> None:
        """Право и рецепт — разные имена, как `take_game` и `take_game_*`.

        Право — разрешение, рецепт — работа. Одно имя на оба означало бы, что
        право и операция отождествлены, и добавляло бы ровно тот «призрак», что
        ADR 0150 велел снять: объявление без отдельной работы.
        """
        self.assertNotEqual(self.RIGHT, self.RECIPE)
        self.assertNotIn(self.RIGHT, self.catalogs.recipes, "Право стало рецептом")
        self.assertNotIn(self.RECIPE, LAND_ACTIONS, "Рецепт выдан как право земли")

    def test_right_points_at_the_clearing_recipe(self) -> None:
        """Связь права и работы объявлена в `LAND_ACTION_RECIPES` — не в догадке."""
        self.assertIn(self.RIGHT, LAND_ACTION_RECIPES)
        self.assertEqual(LAND_ACTION_RECIPES[self.RIGHT], (self.RECIPE,))
        for recipe_id in LAND_ACTION_RECIPES[self.RIGHT]:
            self.assertIn(recipe_id, self.catalogs.recipes, recipe_id)

    def test_only_the_demesne_may_clear(self) -> None:
        """Расчистку выдаёт ТОЛЬКО домен (ADR 0177 п. 5).

        Расширение деревни — дело лорда и общины (ADR 0173, ADR 0156). Частный
        держатель берёт бревно существующим `cut_wood`; второе право на ту же
        лесную работу — это ровно «два имени на одну операцию» (ADR 0161).
        """
        granting = {
            regime_id
            for regime_id, regime in self.catalogs.land_regimes.items()
            if self.RIGHT in regime.allowed_actions
        }
        self.assertEqual(granting, {"demesne"})

    def test_recipe_is_granted_by_a_household_action(self) -> None:
        """Рецепт объявлен И разрешён: иначе право есть, а исполнить нечем."""
        actions = yaml.safe_load(ACTIONS_YML.read_text(encoding="utf-8"))
        granted: set[str] = set()
        for record in actions.get("actions", []):
            granted |= set(record.get("recipes") or [])
        self.assertIn(
            self.RECIPE, granted,
            "Рецепт расчистки не разрешён ни одним действием двора — это призрак",
        )

    # --- 5. цена камня выведена, а не выдумана --------------------------------------
    def test_stone_price_is_derived_from_the_clearing_recipe(self) -> None:
        """Цена камня = (труд + сырьё + потери) × коэффициент класса.

        Сырьё и потери — по ПОЛНОЙ себестоимости входа (ADR 0161), не по дневной
        ставке. Камень единственный производится рецептом расчистки, поэтому
        проверка однозначна.
        """
        wage = float(
            yaml.safe_load(MANOR_YML.read_text(encoding="utf-8"))["hire"][
                "wage_silver_per_day"
            ]
        )
        self.assertEqual(wage, 0.02, "Ставка наёмного сдвинулась — пересчитать цены")
        out = float(self.recipe["outputs"][self.GOOD])
        self.assertGreater(out, 0.0)
        labor = float(self.recipe["labor_days"]) * wage / out
        self.assertAlmostEqual(
            float(self.goods[self.GOOD]["price_labor_silver"]), labor, places=5,
            msg="Труд в цене камня не равен трудодням расчистки × ставка",
        )
        # Потери камня считаются по его же себестоимости — решаем уравнение.
        good = self.goods[self.GOOD]
        other = sum(
            float(qty) * _cost(self.goods, lost)
            for lost, qty in sorted(self.recipe["loss"].items())
            if lost != self.GOOD
        )
        share = float(self.recipe["loss"][self.GOOD]) / out
        cost = (labor + other / out) / (1.0 - share)
        self.assertAlmostEqual(
            float(good["price_labor_silver"])
            + float(good["price_materials_silver"])
            + float(good["price_losses_silver"]),
            cost, places=5,
            msg="Части себестоимости камня не сходятся с рецептом",
        )
        self.assertAlmostEqual(
            float(good["price_silver"]), cost * STONE_DEFICIT, places=5,
            msg="Цена камня не равна себестоимости × коэффициент класса",
        )

    def test_stone_deficit_class_is_the_local_abundance_one(self) -> None:
        """Коэффициент 1.1 — класс материала местного изобилия (ADR 0161 п. 5).

        Обоснование числами: камень не требует переработки, тогда как соль
        (1.3) проходит ванну и выварку, а железо (1.3) — крицу и горн. Камень
        берётся прямо из клетки, как `log` и `peat` (1.1).
        """
        self.assertEqual(STONE_DEFICIT, 1.1)
        local = ("log", "firewood", "peat", "hay", "board", "plank", self.GOOD)
        for good in local:
            with self.subTest(good=good):
                self.assertIn(good, self.goods)
        scarce = ("salt", "iron")
        for good in scarce:
            with self.subTest(good=good):
                self.assertIn(good, self.goods)


def _cost(goods: dict, good: str) -> float:
    """Полная себестоимость товара: труд + сырьё + потери, без рыночной наценки."""
    record = goods[good]
    return sum(
        float(record.get(field, 0.0))
        for field in (
            "price_labor_silver", "price_materials_silver", "price_losses_silver"
        )
    )


class TestMaterialQualityIsGone(unittest.TestCase):
    """Поле `Good.material_quality` удалено: у него ноль чтений (ADR 0157 п. 4).

    Единственным его «потребителем» был тест, сверявший `material_quality == 1.05`
    с числом `1.05`, вшитым в код, — то есть сверявший числа кода с числами кода.
    Пока поле можно вернуть незаметно, удаление ничего не значит; поэтому закон
    проверяется здесь буквально: ни объявления, ни ключа парсера, ни YAML-поля.
    """

    def test_field_is_not_in_the_good_record(self) -> None:
        from dataclasses import fields

        from hillcourt.ontology import Good

        self.assertNotIn(
            "material_quality", {field.name for field in fields(Good)},
            "Поле с нулём чтений вернулось в онтологию",
        )

    def test_no_occurrence_left_in_src_or_catalogs(self) -> None:
        found = []
        sources = [p for p in sorted(SRC.rglob("*.py")) if "__pycache__" not in p.parts]
        catalogs = sorted((ROOT / "design" / "catalogs").glob("*.yml"))
        # Сканер, который ничего не нашёл бы из-за неверного пути, зелёный: пустой
        # список файлов — тоже отсутствие (ADR 0155, ADR 0162 п. 1).
        self.assertTrue(sources, "Исходники не найдены — сканер проверил пустоту")
        self.assertTrue(catalogs, "Каталоги не найдены — сканер проверил пустоту")
        for path in sources:
            if "material_quality" in path.read_text(encoding="utf-8"):
                found.append(str(path.relative_to(ROOT)))
        for path in catalogs:
            if "material_quality" in path.read_text(encoding="utf-8"):
                found.append(str(path.relative_to(ROOT)))
        self.assertEqual(found, [], "material_quality вернулся:\n" + "\n".join(found))


def _code_branches_on(action: str) -> bool:
    """Ветвится ли движок по имени права вместе с `allowed_actions`.

    Проба по исходникам `sim/src`: литерал действия, следом оператор проверки
    вхождения (`in` / `not in`). Именно вхождение, а не любое упоминание:
    `legal/regimes.py` для `plough` и `leave` делает `actions.discard(...)`, и
    такое упоминание НЕ делает право исполнимым — оно его только урезает.
    Право, на которое движок не ветвится, нечем исполнять.
    """
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "catalogs.py":
            continue
        if f'"{action}"' not in path.read_text(encoding="utf-8"):
            continue
        if any(
            match.group("action") == action
            for match in _MEMBERSHIP_PROBE.finditer(path.read_text(encoding="utf-8"))
        ):
            return True
    return False


def _recipe_backed(action: str) -> bool:
    """Есть ли у действия земли исполнитель: рецепт двора или `LAND_ACTION_RECIPES`."""
    actions = yaml.safe_load(ACTIONS_YML.read_text(encoding="utf-8"))
    for record in actions.get("actions", []):
        if action in (record.get("recipes") or []):
            return True
    return action in LAND_ACTION_RECIPES


class TestLandActionsAreNotGhosts(unittest.TestCase):
    """Объявленное и выданное право земли — право, на которое механизм отвечает.

    ADR 0150 п. 1 и ADR 0161 п. 2 требуют снимать объявления, под которыми нет
    ни рецепта, ни фазы. Сняты оба: постройка (ADR 0150 п. 1, ADR 0157 п. 4) и
    сбор хвороста (ADR 0161 п. 2) — у обоих не было ни одного рецепта, лес
    убирается существующим действием двора `cut_wood_if_allowed`, поэтому право
    было одно, а не два.

    Проверка двусторонняя, и вторая сторона — та, которой не было:

    1. **объявлено, но не выдано никем** — права не существует ни у кого;
    2. **выдано режимом, но исполнить нечем** — юрист видит разрешённое право,
       которого нет. Право считается исполнимым, если его id есть в списке
       `recipes:` хотя бы одного действия двора, либо в `LAND_ACTION_RECIPES`,
       либо движок ветвится по нему вместе с `allowed_actions` (`plough` —
       пахота из `engine/yield_law.py`, рецептом она не выражена).

    Обе стороны идут от данных, а не от своего же списка: множество прав берётся
    из `land_regimes.yml`, множество исполнителей — из рецептов и исходников.
    """

    @classmethod
    def setUpClass(cls) -> None:
        catalogs = load_catalogs(ROOT)
        cls.land_actions = catalogs.land_regimes
        cls.granted = {
            action
            for regime in catalogs.land_regimes.values()
            for action in regime.allowed_actions
        }
        cls.by_recipe = {a for a in sorted(cls.granted) if _recipe_backed(a)}
        cls.by_code = {a for a in sorted(cls.granted) if _code_branches_on(a)}
        cls.ghosts = sorted(cls.granted - cls.by_recipe - cls.by_code)

    def test_every_declared_action_is_granted_by_some_land_regime(self) -> None:
        """Сторона 1: объявлено, но не выдано никем — права нет ни у кого."""
        ungranted = sorted(set(LAND_ACTIONS) - self.granted)
        self.assertEqual(
            ungranted, [],
            "Действие земли объявлено, но ни один режим его не даёт — это призрак "
            "(ADR 0150 п. 1): снимите объявление",
        )

    def test_every_granted_action_is_declared(self) -> None:
        """Режим не может выдать несуществующее действие (ловит и загрузчик)."""
        self.assertEqual(sorted(self.granted - set(LAND_ACTIONS)), [])

    def test_granted_rights_are_enforceable(self) -> None:
        """Сторона 2: выдано, но исполнить нечем — тоже призрак (ADR 0161 п. 2).

        Список допущенных — **точный**, а не «содержит»: новый призрак, который
        никто не исполняет, ломает равенство и роняет тест. Допуск один и
        назван: право ухода. Ниже проверяется, что допуск не превратился в
        резиновую печать — что он и правда ничем не исполним.
        """
        self.assertEqual(
            self.ghosts, sorted(PENDING_UNENFORCEABLE),
            "Режим земли выдаёт право, которому нечем исполниться: призрак. "
            f"Исполнимые рецептом {self.by_recipe}, кодом {self.by_code}",
        )

    def test_pending_unenforceable_is_really_unenforceable(self) -> None:
        """Допуск не должен провенчиться: каждый его пункт правда не исполним."""
        for action in PENDING_UNENFORCEABLE:
            with self.subTest(action=action):
                self.assertIn(action, self.granted, "Допуск устарел: право не выдано")
                self.assertFalse(
                    _recipe_backed(action), "Допуск устарел: у права появился рецепт"
                )
                self.assertFalse(
                    _code_branches_on(action), "Допуск устарел: код начал ветвиться"
                )

    def test_land_actions_exist_only_as_land_actions(self) -> None:
        """Имя действия земли не должно быть рецептом двора — это второй признак
        того же призрака (право, которому отвечает не механизм рецептов)."""
        overlap = sorted(set(LAND_ACTIONS) & set(load_catalogs(ROOT).recipes))
        self.assertEqual(
            overlap, [], "Действие земли стало рецептом: механику есть что исполнять "
            "не по тому праву",
        )

    def test_mechanism_backed_actions_are_declared_and_real(self) -> None:
        """Каждый `LAND_ACTION_RECIPES` — объявленное действие с существующими
        рецептами: справочная таблица не должна врать о правах."""
        catalogs = load_catalogs(ROOT)
        for action, recipe_ids in sorted(LAND_ACTION_RECIPES.items()):

            with self.subTest(action=action):
                self.assertIn(action, LAND_ACTIONS)
                for recipe_id in recipe_ids:
                    self.assertIn(recipe_id, catalogs.recipes, recipe_id)

    def test_regimes_are_not_empty(self) -> None:
        """Стенд проверок выше имеет смысл, только если режимы земли есть."""
        self.assertTrue(self.land_actions)
        self.assertTrue(self.granted)


class TestWolvesDenRule(unittest.TestCase):
    """Допуск №4 (ADR 0042): топ-ап волков — контракт правила, фаза живая.

    Правило только дотягивает СУЩЕСТВУЮЩИХ волков (mode=topup, новых `Hazard`
    нет — иначе нужна весть ниоткуда), бросок — через `rng.hazard`, потолки
    держат (`intensity_cap`/`population_cap`, иначе бесконечные волки), рост
    требует `Report` (рождение вести — за Info; фаза метит рост в
    `world.stats`). Механика фазы — `sim.tests.test_wolves_den_phase`.
    """

    def test_dens_topup_existing_wolves_only(self) -> None:
        from pathlib import Path as _Path

        from hillcourt.scenario import load_scenario

        catalogs = load_scenario(
            _Path(__file__).resolve().parents[2]
            / "design" / "scenarios" / "v0_shire.yml"
        ).catalogs
        rule = catalogs.spawn_rules["wolves_den"]
        self.assertEqual(rule.target, "hazard")
        self.assertEqual(rule.kind, "probabilistic")
        self.assertEqual(rule.params.get("mode"), "topup")
        self.assertNotIn("spawn", str(rule.params.get("mode")))
        self.assertEqual(rule.params.get("kind"), "wolves")
        self.assertIn("wolves", catalogs.hazard_rules)
        self.assertEqual(rule.params.get("terrain"), "forest")
        self.assertGreater(float(rule.params.get("base_prob", 0.0)), 0.0)
        self.assertGreater(float(rule.params.get("intensity_gain", 0.0)), 0.0)
        self.assertGreater(float(rule.params.get("population_gain", 0.0)), 0.0)
        self.assertGreaterEqual(
            float(rule.params.get("intensity_cap", 0.0)), 0.6 + 0.2,
            "Потолок ниже живых волков сценария — правило мёртво",
        )
        self.assertGreater(
            float(rule.params.get("population_cap", 0.0)), 0.6,
            "Потолок ниже живой стаи — правило мёртво",
        )
        self.assertEqual(rule.params.get("rng_stream"), "hazard")
        self.assertTrue(rule.params.get("report_required"), "Рост без вести врёт И-3")

    def test_phase_topup_only_no_spawn_caps_hold(self) -> None:
        from pathlib import Path as _Path

        from hillcourt.engine.tick import run_month
        from hillcourt.scenario import load_scenario

        world = load_scenario(
            _Path(__file__).resolve().parents[2]
            / "design" / "scenarios" / "v0_shire.yml",
            seed=1729,
        )
        rule = world.catalogs.spawn_rules["wolves_den"]
        intensity_cap = float(rule.params.get("intensity_cap", 0.0))
        population_cap = float(rule.params.get("population_cap", 0.0))
        ids_before = set(world.hazards)
        for _ in range(12):
            run_month(world)
        self.assertEqual(set(world.hazards), ids_before, "Правило родило угрозу")
        for hid, hazard in world.hazards.items():
            if hazard.kind != "wolves":
                continue
            self.assertLessEqual(
                hazard.intensity, intensity_cap + 1e-9, f"{hid}: потолок пробит"
            )
            self.assertLessEqual(
                hazard.population, population_cap + 1e-9, f"{hid}: потолок пробит"
            )
        for key in world.stats:
            if key.startswith("wolves_den_topup_"):
                self.assertIn(
                    key[len("wolves_den_topup_") :],
                    ids_before,
                    f"Метка {key} — про несуществующую угрозу",
                )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


class TestBandCampRule(unittest.TestCase):
    """Шаг 1 pipeline ватаги (ADR 0045, образец — ADR 0042): контракт правила.

    Появление — условное (`terrain: marsh` + `min_households: 3`; гейт сейчас
    не стреляет нигде — дворов на топях 0, запинено). Новая сущность — только
    с потолками (`intensity_cap`/`population_cap`) и только с вестью
    (`report_required` — рождение за Info при фазе, иначе И-3). Бросок — через
    `rng.hazard`. Исполнителя для ватаги в тике нет: 12 месяцев шира — тишина.
    """

    SCENARIOS = (
        "v0_hill_and_salt.yml",
        "v0_two_settlements.yml",
        "v0_shire.yml",
    )

    def _load(self, name: str, seed: int | None = None):
        from pathlib import Path as _Path

        from hillcourt.scenario import load_scenario

        return load_scenario(
            _Path(__file__).resolve().parents[2] / "design" / "scenarios" / name,
            seed=seed,
        )

    def test_band_spawns_conditional_with_caps_and_report(self) -> None:
        world = self._load("v0_shire.yml")
        rule = world.catalogs.spawn_rules["band_camp"]
        self.assertEqual(rule.target, "hazard")
        self.assertEqual(rule.kind, "conditional")
        self.assertEqual(rule.params.get("mode"), "spawn")
        self.assertEqual(rule.params.get("kind"), "band")
        self.assertIn("band", world.catalogs.hazard_rules)
        self.assertEqual(rule.params.get("terrain"), "marsh")
        self.assertEqual(int(rule.params.get("min_households", 0)), 3)
        prob = float(rule.params.get("base_prob", 0.0))
        self.assertGreater(prob, 0.0)
        self.assertLess(prob, 1.0)
        self.assertGreater(float(rule.params.get("intensity_init", 0.0)), 0.0)
        self.assertGreater(float(rule.params.get("population_init", 0.0)), 0.0)
        self.assertGreaterEqual(
            float(rule.params.get("intensity_cap", 0.0)),
            float(rule.params.get("intensity_init", 0.0)),
            "Потолок ниже рождения — правило мёртво",
        )
        self.assertGreaterEqual(
            float(rule.params.get("population_cap", 0.0)),
            float(rule.params.get("population_init", 0.0)),
            "Потолок ниже рождения — правило мёртво",
        )
        self.assertEqual(rule.params.get("rng_stream"), "hazard")
        self.assertTrue(rule.params.get("report_required"), "Ватага без вести врёт И-3")

    def test_gate_fires_nowhere(self) -> None:
        for name in self.SCENARIOS:
            world = self._load(name)
            rule = world.catalogs.spawn_rules["band_camp"]
            need = int(rule.params.get("min_households", 0))
            top = 0
            for tile in world.tiles.values():
                if tile.terrain != rule.params.get("terrain"):
                    continue
                count = sum(
                    1
                    for household in world.households.values()
                    if household.left_at is None
                    and household.current_tile_id == tile.id
                )
                top = max(top, count)
            self.assertLess(
                top, need, f"{name}: гейт стреляет ({top} дворов на топи)",
            )

    def test_rule_is_silent_without_phase(self) -> None:
        from hillcourt.engine.tick import run_month

        world = self._load("v0_shire.yml", seed=1729)
        others_before = {
            hid: (h.population, h.intensity)
            for hid, h in world.hazards.items()
            if h.kind != "wolves"
        }
        for _ in range(12):
            run_month(world)
        bands = [h for h in world.hazards.values() if h.kind == "band"]
        self.assertEqual(bands, [], "Правило родило ватагу без фазы")
        others_after = {
            hid: (world.hazards[hid].population, world.hazards[hid].intensity)
            for hid in others_before
        }
        self.assertEqual(others_after, others_before, "Чужая угроза дрейфует без фазы")
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
