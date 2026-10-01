"""Документация против кода: поля, перечисления и имена тестов (ADR 0162 п. 4).

До этого модуля сверка была ручной, и дыры копились годами: в таблице `NeedConfig`
не было шести полей, в `SpawnRule` — поля `external`, а `Good.storage` объявлял
значение `pack`, которого нет ни в одном `goods.yml`.

Проверяем четыре вещи, и **падаем** на каждой из них:

1. **Фантом.** Поле, описанное в таблице `docs/03_ontology.md`, которого нет ни в
   одном `@dataclass` из `ontology.py`. Документ обещает несуществующее поле.
2. **Пробел.** Поле `@dataclass`, несущее смысл, которого нет в таблице. Код есть,
   канон молчит.
3. **Перечисление.** Каждое множество значений, написанное в онтологии в виде
   `{a,b,c}`, сверяется с фактическими значениями из кода и каталогов: значение
   из документа, которого нет в данных, — фантом; значение в данных, которого
   нет в документе, — пробел.
4. **Имя теста.** Каждый `test_*`, процитированный в блоке «Проверка» любого
   `docs/*.md`, обязан существовать файлом `sim/tests/<name>.py` либо методом
   внутри модуля сюжета. Ссылка на несуществующий тест — висячая.

Числа, на которые ссылается документ, здесь **не** проверяются: это числа
экономики, и владеет ими Economist, а не Scribe. Проверяется только форма — что
поле существует, что значение перечисления реально встречается в данных.
"""

from __future__ import annotations

import ast
import fnmatch
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "sim" / "src"
ONTOLOGY_PY = SRC / "hillcourt" / "ontology.py"
ONTOLOGY_MD = ROOT / "docs" / "03_ontology.md"
DOCS_DIR = ROOT / "docs"
TESTS_DIR = Path(__file__).resolve().parent
CATALOGS = ROOT / "design" / "catalogs"

# Слова, которые встречаются в перечне полей, но полями не являются: типы,
# служебные словари и имена, живущие не в `@dataclass`.
NOT_A_FIELD = frozenset({
    "stock", "id", "float", "bool", "int", "str", "dict", "list", "set",
    "good_id", "recipe_id", "term", "month", "preset", "SimDate", "terrain",
    "grain_kg", "labor_days", "rent_share", "travel_days", "pack",
    # Ключи `params: dict` у SpawnRule, а не поля dataclass.
    "cap_per_tile",
    # Ключи `terms: dict[term,{value,unit,note}]` у ObligationBundle.
    "geld_michaelmas", "in_kind_martinmas", "sow_demesne_acres", "unit",
    # Секции сценария и функции движка, названные в пояснении к строке `Tile`.
    "fords", "bridges", "roads", "start_work", "find_path", "send_river", "tower",
    "party", "allowed_goods", "meals_cooked", "growth_tile_ids",
})


def ontology_fields() -> dict[str, set[str]]:
    """Поля всех `@dataclass` из `sim/src/`: имя класса → множество полей.

    Собирается `ast`, а не импортом: проверка документации обязана работать, даже
    если движок не импортируется. Обходятся **все** модули, а не один
    `ontology.py`: `ManorConfig` живёт в `economy/manor.py`, а `PlayerView`,
    `ReportView`, `KnowledgeEntry`, `PlayerKnowledge` — в `news/`, и онтология
    обязана их описывать тоже.
    """
    out: dict[str, set[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - чужой сломанный модуль
            continue
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            fields: set[str] = set()
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    fields.add(stmt.target.id)
                elif isinstance(stmt, ast.Assign):
                    fields.update(
                        t.id for t in stmt.targets if isinstance(t, ast.Name)
                    )
            if fields:
                out.setdefault(node.name, set()).update(fields)
    return out


def doc_table_rows() -> dict[str, set[str]]:
    """Строки таблиц `docs/03_ontology.md`: класс → множество имён в обратных кавычках.

    `| \`Good\` | \`id\`, \`name\`, … | Economist |` превращается в
    `{"Good": {"id", "name", …}}`. Строки, где в первом столбце не единственное
    имя класса, пропускаются: это не описание полей.

    Обратные кавычки в документе несут и перечисления целиком —
    `` `age_class ∈ {child,adult,elder}` ``, `` `allowed_actions ⊆ {a,b}` ``, —
    поэтому берётся и ведущий идентификатор из такого токена. Без этого
    `age_class` считался бы недокументированным при живом коде.

    Токены со скобками (`latest(about)`, `sources()`) — это методы, а не поля,
    и в перечень полей не попадают.
    """
    rows: dict[str, set[str]] = {}
    for cells in _logical_rows():
        name = re.fullmatch(r"`([A-Za-z_][A-Za-z0-9_]*)`", cells[0])
        if not name:
            continue
        found: set[str] = set()
        for hit in re.finditer(r"`([^`]+)`", cells[1]):
            token = hit.group(1)
            if "(" in token or ")" in token:
                continue
            if "{" in token:
                # `call_status ∈ {pending,met,…}` — поле только слева от фигурной
                # скобки; значения перечисления полями не являются.
                head = re.match(r"^([a-z_][a-z0-9_]*)", token.split("{", 1)[0])
            else:
                # Поле — элемент перечисления через запятую, конец ячейки либо
                # начало пояснения в скобках (`land_regime_id` (режим земли…)).
                # Обратные кавычки в прозе внутри ячейки идут за другим знаком
                # (`muster` —, `pending`/`met` —, `…refused`;) и полями не
                # являются, поэтому смотрим первый знак после закрытой кавычки.
                tail = cells[1][hit.end():].lstrip()[:1]
                head = (
                    re.match(r"^([a-z_][a-z0-9_]*)", token)
                    if tail in ("", ",", ":", "(")
                    else None
                )
            if head:
                found.add(head.group(1))
        rows.setdefault(name.group(1), set()).update(found)
    return rows


def doc_all_identifiers() -> set[str]:
    """Все идентификаторы, упомянутые в `docs/03_ontology.md` целиком, включая прозу.

    Для проверки пробелов «документировано» значит «упомянуто в документе», а не
    «лежит в строке таблицы»: поля `Person.talent`, `Person.labor_productivity`
    и `Recipe.tool_multiplier` намеренно описаны в прозе разделом про мёртвые
    поля формулы, и считать их недокументированными было бы неверно.
    """
    return set(
        re.findall(r"[a-z_][a-z0-9_]*", ONTOLOGY_MD.read_text(encoding="utf-8"))
    )



def _load(name: str) -> object:
    data = yaml.safe_load((CATALOGS / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict), name
    return data


def _records(data: dict) -> list[dict]:
    """Список записей каталога: первый список верхнего уровня."""
    for value in data.values():
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)]
    raise AssertionError("в каталоге нет списка записей")


def _logical_rows() -> list[list[str]]:
    """Строки таблиц, склеенные по переносу.

    Ячейка может переноситься на следующую строку без ведущего `|`
    (например, `Obligation.call_status` с перечнем из семи значений). У такой
    строки в первой физической строке **две** ячейки, а не три: хвост с
    «Владельцем» приезжает во вторую. Поэтому отбрасываются строки короче двух
    ячеек, а не трёх, и столбец полей читается как `cells[1]`.
    """
    rows: list[list[str]] = []
    current: list[str] = []
    for line in ONTOLOGY_MD.read_text(encoding="utf-8").splitlines():
        if line.startswith("|"):
            if current:
                rows.append(current)
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 1:
                cells.append("")
            current = cells
        elif current and line.strip():
            # Перенос продолжает столбец полей: в этом документе длинные
            # перечисления переносятся именно туда, а не в «Владельца».
            current[1 if len(current) > 1 else -1] += " " + line.strip()
        elif not line.strip() and current:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    return [r for r in rows if len(r) >= 2]


def _enum_from_doc(entity: str, field_name: str) -> tuple[set[str], str] | None:
    """Перечисление поля в онтологии: `(значения, знак)`.

    Ищется **в строке своей сущности**, а не в первой подходящей: `kind`
    встречается и у `Obligation` (`{rent,labor_duty,levy,muster}`), и у
    `SpawnRule` (`{probabilistic,calendric,conditional}`), и путать их нельзя.

    Знак различает `∈` (полный перечень — обе стороны проверяются) и `⊆`
    (уттверждение о подмножестве — проверяется только сторона фантома: документ
    не может обещать значение, которого нет в данных, но не обязан перечислять
    все).
    """
    for cells in _logical_rows():
        if not re.fullmatch(rf"`{re.escape(entity)}`(\s*\([^`]*\))?", cells[0]):
            continue
        hit = re.search(
            rf"`{re.escape(field_name)}\s*(∈|⊆)\s*\{{([^}}]*)\}}`", cells[1]
        )
        if hit:
            return {v.strip() for v in hit.group(2).split(",") if v.strip()}, hit.group(1)
    return None


class TestOntologyFieldsAgainstCode(unittest.TestCase):
    """Пункт 1 и 2: фантомы (документ → код) и пробелы (код → документ)."""

    def setUp(self) -> None:
        self.code = ontology_fields()
        self.doc = doc_table_rows()

    def test_phantom_fields(self) -> None:
        """Описанное в таблице поле обязано существовать в `ontology.py`."""
        phantoms: list[str] = []
        for entity, names in sorted(self.doc.items()):
            if entity not in self.code:
                continue
            for name in sorted(names - self.code[entity] - NOT_A_FIELD):
                phantoms.append(f"{entity}.{name}")
        self.assertEqual(
            phantoms, [], "docs/03_ontology.md обещает поля, которых нет в коде"
        )

    def test_meaningful_code_fields_are_documented(self) -> None:
        """Каждое поле `@dataclass`, несущее смысл, обязано быть в документе.

        «Документировано» = упомянуто в `docs/03_ontology.md`, таблица или проза.
        Часть полей описана в прозе (мёртвые поля формулы), и это законный
        способ задокументировать поле, не вынося его в состав dataclass.
        """
        mentioned = doc_all_identifiers()
        gaps: list[str] = []
        for entity, names in sorted(self.code.items()):
            if entity not in self.doc:
                continue
            for name in sorted(names - mentioned):
                gaps.append(f"{entity}.{name}")
        self.assertEqual(
            gaps, [], "в коде есть поля, которых нет в docs/03_ontology.md"
        )

    def test_every_table_entity_exists_in_code(self) -> None:
        """Класс, описанный в таблице онтологии, обязан быть в `ontology.py`."""
        missing = sorted(set(self.doc) - set(self.code))
        self.assertEqual(
            missing, [], "в таблице онтологии есть сущности, которых нет в коде"
        )

    def test_six_need_config_fields_are_documented(self) -> None:
        """Конкретный долг ADR 0162 п. 5: смертность и режим земли в `NeedConfig`."""
        required = {
            "land_regime_id", "death_child_per_month", "death_adult_per_month",
            "death_elder_per_month", "death_old_age_months", "death_old_age_extra",
        }
        self.assertTrue(
            required <= self.doc.get("NeedConfig", set()),
            "NeedConfig потерял поля: " + ", ".join(sorted(required - self.doc.get("NeedConfig", set()))),
        )

    def test_spawn_rule_has_no_external_field(self) -> None:
        """Происхождение потока помечается видом проводки, а не ярлыком правила.

        **ЗАМЕНЯЕТ `test_spawn_rule_external_is_documented`, удалён 2026-09-26.**

        Тот тест требовал, чтобы поле `SpawnRule.external` было описано в таблице. Поле
        объявлено, грузилось и **не читалось ни одним потребителем** — ADR 0164 п. 2 снял
        его тем же основанием, что ADR 0157 снял `material_quality`. Если бы я просто
        убрал поле из таблицы онтологии, тот тест начал бы требовать обратно то, чего в
        коде уже нет, — то есть **сам стал бы причиной фантома**. Проверка не удалена, а
        переведена на живое свойство: И-1 держит `LedgerEntry.kind` и непустой `rule_id`.
        """
        self.assertNotIn(
            "external",
            self.doc.get("SpawnRule", set()),
            "docs/03_ontology.md описывает снятое поле SpawnRule.external "
            "(ADR 0164 п. 2)",
        )
        self.assertNotIn(
            "external",
            ontology_fields().get("SpawnRule", set()),
            "поля SpawnRule.external нет в коде, значит его не должно быть и в документе",
        )

    def test_flow_origin_is_marked_by_ledger_entry(self) -> None:
        """Живое свойство вместо удалённого теста: И-1 держит вид проводки и `rule_id`.

        Три формы по ADR 0164 п. 1 для утверждения о **механизме**: (1) узкая — в коде
        есть константа `ENTRY_KINDS` со значением `external_in`; (2) широкая — `Ledger`
        действительно пишет такую запись; (3) исполнение — `test_matter_conservation`
        падает, если проводки с непустым `rule_id` нет. Третья форма проверяется там, а
        здесь мы убеждаемся, что канон называет **тот самый** механизм, а не поле-ярлык.
        """
        ledger_src = (SRC / "hillcourt" / "ledger.py").read_text(encoding="utf-8")
        self.assertIn(
            '"external_in"',
            ledger_src,
            "вид проводки external_in исчез из ledger.py",
        )
        self.assertRegex(
            ledger_src,
            r"ENTRY_KINDS[^=]*=\s*\([^)]*external_in",
            "external_in не входит в ENTRY_KINDS — И-1 нечем пометить",
        )
        text = ONTOLOGY_MD.read_text(encoding="utf-8")
        self.assertIn(
            "external_in", text, "онтология не называет вид проводки, которой держится И-1"
        )
        self.assertIn(
            "rule_id", text, "онтология не называет rule_id — без него И-1 недоказуем"
        )

    def test_tile_resource_productivity_is_documented(self) -> None:
        """`Tile.resource_productivity` читается в `phase_growth` и обязан быть в таблице."""
        self.assertIn("resource_productivity", self.doc.get("Tile", set()))


class TestOntologyEnumsAgainstData(unittest.TestCase):
    """Пункт 3: перечисления онтологии против фактических значений."""

    def test_good_storage_enum(self) -> None:
        """`Good.storage`: значение из документа обязано быть в `goods.yml`."""
        actual = {r["storage"] for r in _records(_load("goods.yml")) if "storage" in r}
        self._assert_enum_matches("Good", "storage", actual)

    def test_land_regime_allowed_actions_enum(self) -> None:
        """`allowed_actions`: документ не может обещать право, снятое ADR 0150/0161."""
        actual = {
            action
            for r in _records(_load("land_regimes.yml"))
            for action in r.get("allowed_actions", [])
        }
        self._assert_enum_matches("LandRegime", "allowed_actions", actual)

    def test_spawn_rule_target_and_kind_enums(self) -> None:
        """`SpawnRule.target`/`kind` против `spawn_rules.yml`."""
        records = _records(_load("spawn_rules.yml"))
        self._assert_enum_matches("SpawnRule", "target", {r["target"] for r in records})
        self._assert_enum_matches("SpawnRule", "kind", {r["kind"] for r in records})

    def test_legal_status_axes_enums(self) -> None:
        """`personal_status`, `land_relation`, `court` против `legal_statuses.yml`."""
        records = _records(_load("legal_statuses.yml"))
        for field in ("personal_status", "land_relation", "court"):
            self._assert_enum_matches(
                "LegalStatus", field, {str(r[field]) for r in records if field in r}
            )

    def test_obligation_call_status_enum(self) -> None:
        """`call_status` против константы `OBLIGATION_CALL_STATUSES` в коде."""
        tree = ast.parse(ONTOLOGY_PY.read_text(encoding="utf-8"))
        actual: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "OBLIGATION_CALL_STATUSES"
                for t in node.targets
            ):
                actual = {
                    e.value for e in node.value.elts  # type: ignore[attr-defined]
                    if isinstance(e, ast.Constant)
                }
        self.assertTrue(actual, "OBLIGATION_CALL_STATUSES не найдена в коде")
        self._assert_enum_matches("Obligation", "call_status", actual)

    def test_stock_owner_kind_enum(self) -> None:
        """`owner_kind`: код заводит `manor` ради двух амбаров манора."""
        actual: set[str] = set()
        for path in SRC.rglob("*.py"):
            for hit in re.findall(
                r'owner_kind\s*=\s*"([a-z_]+)"', path.read_text(encoding="utf-8")
            ):
                actual.add(hit)
            for hit in re.findall(
                r'_new_stock\([^,]+,\s*"([a-z_]+)"', path.read_text(encoding="utf-8")
            ):
                actual.add(hit)
        self.assertTrue(actual, "owner_kind нигде не присваивается")
        self._assert_enum_matches("Stock", "owner_kind", actual)

    def test_household_intent_enum(self) -> None:
        """`intent`: код пишет только `stay` и `leave`."""
        actual: set[str] = set()
        for path in SRC.rglob("*.py"):
            actual.update(
                re.findall(
                    r'\.intent\s*=\s*"([a-z_]+)"',
                    path.read_text(encoding="utf-8"),
                )
            )
            actual.update(
                re.findall(
                    r'\bintent\s*=\s*"([a-z_]+)"',
                    path.read_text(encoding="utf-8"),
                )
            )
        self.assertTrue(actual, "intent нигде не присваивается")
        self._assert_enum_matches("Household", "intent", actual)

    def test_person_age_class_enum(self) -> None:
        """`age_class`: пишется и через литерал, и через локальную переменную.

        Узкий греп по `age_class="elder"` здесь не годится: в `manor.py:186` и
        `scenario.py:829` значение идёт через `age_class = "elder"` с последующим
        `age_class=age_class` (ADR 0162 п. 3, широкая форма).
        """
        actual: set[str] = set()
        for path in SRC.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            actual.update(re.findall(r'age_class\s*=\s*"([a-z_]+)"', text))
            actual.update(re.findall(r'age_class\s*==\s*"([a-z_]+)"', text))
        self.assertIn("elder", actual, "старики перестали заводиться — сверьте сценарии")
        self._assert_enum_matches("Person", "age_class", actual)

    def _assert_enum_matches(
        self, entity: str, field_name: str, actual: set[str]
    ) -> None:
        """Значение из документа есть в данных; для `∈` — и наоборот.

        `⊆` — утверждение о подмножестве, поэтому проверяется только фантомная
        сторона. `Good.storage ∈ {…}` — полный перечень, поэтому обе.

        Перечисление ищется в строке своей сущности, а если там его нет — в
        любой другой строке: оси `personal_status`, `land_relation`, `court`
        объявлены не на `LegalStatus`, а на `Person`/`Household`, и дублировать
        их третий раз незачем. Требование одно: wherever перечисление объявлено,
        оно обязано совпадать с данными.
        """
        found = _enum_from_doc(entity, field_name)
        if found is None:
            for cells in _logical_rows():
                hit = re.search(
                    rf"`{re.escape(field_name)}\s*(∈|⊆)\s*\{{([^}}]*)\}}`", cells[1]
                )
                if hit:
                    found = (
                        {v.strip() for v in hit.group(2).split(",") if v.strip()},
                        hit.group(1),
                    )
                    break
        self.assertIsNotNone(
            found,
            f"в онтологии нет перечисления {field_name} вида `∈ {{…}}` или `⊆ {{…}}`",
        )
        assert found is not None
        documented, sign = found
        phantom = sorted(documented - actual)
        self.assertEqual(
            phantom,
            [],
            f"{entity}.{field_name}: документ обещает значения, которых нет в данных",
        )
        if sign == "∈":
            gap = sorted(actual - documented)
            self.assertEqual(
                gap,
                [],
                f"{entity}.{field_name}: в данных есть значения, которых нет в онтологии",
            )


class TestTestNamesInDocsAreReal(unittest.TestCase):
    """Пункт 4: каждый `test_*` из блоков «Проверка» обязан существовать."""

    def _test_methods(self) -> set[str]:
        names: set[str] = set()
        for path in TESTS_DIR.glob("test_*.py"):
            names.add(path.stem)
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - чужой сломанный модуль
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.add(node.name)
        return names

    def test_every_cited_test_exists(self) -> None:
        """Канон не должен ссылаться на несуществующие тесты.

        `docs/decisions/` исключён сознательно: реестр решений — **летопись, а не
        спецификация** (ADR 0162 п. 1). Старое ADR законно упоминает тест, который
        потом переписан или снят; требовать от летописи актуальности — значит
        заставлять редактировать старое ADR, что запрещено `AGENTS.md` §8.
        """
        existing = self._test_methods()
        cited: dict[str, list[str]] = {}
        for path in sorted(DOCS_DIR.rglob("*.md")):
            if path.parent.name == "decisions":
                continue
            text = path.read_text(encoding="utf-8")
            for hit in re.findall(r"test_[a-z0-9_]+", text):
                cited.setdefault(hit, []).append(str(path.relative_to(ROOT)))
        dangling = {
            name: files for name, files in cited.items() if name not in existing
        }
        self.assertEqual(
            dangling,
            {},
            "docs/ ссылаются на несуществующие тесты: "
            + "; ".join(f"{n} ({', '.join(f)})" for n, f in sorted(dangling.items())),
        )

    def test_checking_block_is_not_empty(self) -> None:
        """Хотя бы один документ должен иметь блок «Проверка» с командой или тестом."""
        found = False
        for path in sorted(DOCS_DIR.glob("[0-9][0-9]_*.md")):
            text = path.read_text(encoding="utf-8")
            if re.search(r"^##+\s*Проверка", text, re.M) and re.search(
                r"(sim\.tests\.|unittest|test_[a-z0-9_]+|bash )", text
            ):
                found = True
                break
        self.assertTrue(found, "ни один документ не имеет проверяемого блока «Проверка»")

    def test_ontology_names_no_dead_field(self) -> None:
        """`material_quality` удалён ADR 0157 и не должен вернуться в канон как поле.

        Проверяется не построчно: физическая строка может оборваться посреди фразы.
        Смотрим **абзац**. `material_quality` не должен встречаться в строке таблицы
        (там он был бы заявлен как поле), а в прозе — только рядом со словами снятия.
        """
        text = ONTOLOGY_MD.read_text(encoding="utf-8")
        for cells in _logical_rows():
            if "material_quality" in cells[1]:
                self.fail(
                    "material_quality в таблице онтологии: поле снято ADR 0157 п. 4"
                )
        for para in re.split(r"\n\s*\n", text):
            if "material_quality" not in para:
                continue
            marked = any(
                word in para
                for word in ("удалено", "снят", "снято", "нет в коде", "нулём чтений", "ADR 0157")
            )
            self.assertTrue(
                marked,
                "material_quality упомянут в прозе без слов снятия — он выглядит как живое поле",
            )

    def test_removed_fields_are_not_in_code(self) -> None:
        """Снятые поля не должны вернуться в код молча (ADR 0157, ADR 0164).

        Обратная сторона: еслиEconomist вернёт `material_quality` или
        `SpawnRule.external` в `ontology.py`, а канон продолжает говорить «снято»,
        документация станет вопить неправдой.
        """
        code = ontology_fields()
        for entity, field in (("Good", "material_quality"), ("SpawnRule", "external")):
            if field in code.get(entity, set()):
                self.fail(
                    f"{entity}.{field} снова в коде, а онтология называет его снятым — "
                    "либо вернуть поле в канон, либо снять его честно новым ADR"
                )


class TestDocsAreRussian(unittest.TestCase):
    """Язык документов (ADR 0164 п. 4): артефакты генерации ловятся автоматически.

    Правило **намеренно узкое**: иероглифы CJK, кana и хангыль. Широкое правило
    «любая не-кириллическая буква» дало на этих же документах **124 446**
    ложных срабатывания — латиница в именах, в путях, в `ADR`, в идентификаторах.
    Кириллица, латиница и греческий (математика) разрешены; блоки кода и текст
    в обратных кавычках снимаются целиком, потому что там живут идентификаторы,
    имена собственные и ссылки на файлы.
    """

    CJK = re.compile(
        "[一-鿿぀-ヿ가-힯]"
    )
    # Известный долг реестра решений. Старые ADR не редактируются (AGENTS.md §8),
    # и чистка — отдельный проход (ADR 0164 п. 4), поэтому здесь не «исключение,
    # чтобы было зелено», а **счётчик долга**: новое место с иероглифом падает,
    # а исчезновение старого требует сократить этот список.
    KNOWN_REGISTRY_CJK: tuple[tuple[str, int], ...] = (
        ("docs/decisions/0116_independent_tribe_is_outside_the_manor.md", 6),
        ("docs/decisions/0164_claims_need_three_forms_both_ways.md", 49),
    )

    def _scan(self, path: Path) -> list[tuple[int, str, str]]:
        """Строки с CJK вне блоков кода и вне обратных кавычек."""
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"```.*?```", "", text, flags=re.S)
        out: list[tuple[int, str, str]] = []
        for number, line in enumerate(text.splitlines(), 1):
            bare = re.sub(r"`[^`]*`", "", line)
            hit = self.CJK.search(bare)
            if hit:
                out.append((number, hit.group(0), line.strip()[:80]))
        return out

    def test_no_cjk_in_canon(self) -> None:
        """В каноне (вне реестра решений) иероглифов быть не должно."""
        found: list[str] = []
        for path in sorted(DOCS_DIR.rglob("*.md")):
            if path.parent.name == "decisions":
                continue
            for number, char, line in self._scan(path):
                found.append(f"{path.relative_to(ROOT)}:{number} {char!r} — {line}")
        self.assertEqual(
            found,
            [],
            "в каноне найдены не-кириллические иероглифы (ADR 0164 п. 4):\n  "
            + "\n  ".join(found),
        )

    def test_registry_cjk_debt_is_counted_not_forgiven(self) -> None:
        """Реестр решений: любое новое место с иероглифом — падение.

        Известный долг перечислен в `KNOWN_REGISTRY_CJK`. Если артефакт починен,
        тест потребует сократить список — молчаливо забыть о нём нельзя.
        """
        known = set(self.KNOWN_REGISTRY_CJK)
        found: set[tuple[str, int]] = set()
        for path in sorted((DOCS_DIR / "decisions").rglob("*.md")):
            for number, _, _ in self._scan(path):
                found.add((str(path.relative_to(ROOT)), number))
        unexpected = sorted(found - known)
        self.assertEqual(
            [f"{p}:{n}" for p, n in unexpected],
            [],
            "в реестре решений иероглифы в НОВЫХ местах — это не известный долг, "
            "а новый артефакт генерации",
        )
        self.assertEqual(
            sorted(known - found),
            [],
            "известный долг починен — сократите KNOWN_REGISTRY_CJK в "
            "sim/tests/test_docs_claims.py, иначе проверка перестанет считать долг",
        )

    def test_latin_in_backticks_is_allowed(self) -> None:
        """Идентификаторы, имена и ссылки в обратных кавычках законны.

        Проверка самого правила на живом материале: латиница вне кавычек в каноне
        встречается (ADR, YAML, имена), и она не должна ронять тест.
        """
        allowed = "`ADR 0164`", "`YAML`", "`Hillcourt`", "`docs/09_economy.md`", "`log`"
        for snippet in allowed:
            bare = re.sub(r"`[^`]*`", "", snippet)
            self.assertIsNone(
                self.CJK.search(bare), f"обратные кавычки должны снимать проверку: {snippet}"
            )

    def test_rule_ignores_code_blocks(self) -> None:
        """Блок кода не проверяется: там бывают идентификаторы и таблицы."""
        sample = "```\nабрикос 四\n```\nчистый текст\n"
        self.assertIsNone(self.CJK.search(re.sub(r"```.*?```", "", sample, flags=re.S)))


class TestRegistryNumbersAreUnique(unittest.TestCase):
    """Номер ADR уникален, и имя файла совпадает с заголовком.

    Три дубля номера (`0023`, `0025`, `0136`) и один разъезд «имя 0096, заголовок
    0091» держались месяцами, потому что номер никто не проверял. Правила ADR 0162
    требуют доказательства обеими сторонами, поэтому проверяем и имя, и заголовок, и
    висящие ссылки.

    **Граница допустимого, по решению хозяина.** Упоминание отозванного номера **как
    истории** («ADR 0114 отозван, закон переиздан как 0158») — **допустимо и нужно**:
    без него реестр перестаёт объяснять, почему норма исчезла. Ссылка на отозванный
    номер **как основание живого решения** — **нарушение**: код и канон обосновывают
    себя ссылкой в пустоту. Автоматически различить эти два употребления по тексту
    нельзя, поэтому здесь проверяется только то, что номер существует или внесён в
    `KNOWN_DANGLING`; границу по содержанию проверяет Critic.
    """

    # Отозванные номера из `STATUS.md`: файлов на диске нет, ссылаться нельзя.
    # Канон вправе называть их как историю; новые ссылки на пустоту — нет.
    RETIRED_BY_STATUS: frozenset[int] = frozenset({
        104, 105, 109, 111, 112, 114, 115, 117, 118, 119, 121, 127, 129, 130,
    })
    # Числа, встречающиеся в текстах как «ADR NNNN», но не имеющие файла. Долг
    # считается, а не прощается: почините — сократите список.
    KNOWN_DANGLING: dict[int, tuple[str, ...]] = {
        36: ("docs/decisions/0003_legal_information_model.md",),
        # 0184 — решение хозяина от 2026-09-26, файла в реестре пока нет. Префикс
        # «ADR » намеренно не пишем: комментарий-обоснование не должен читаться как
        # ссылка на отсутствующий номер.
        # Правила уже внесены в канон; как только файлы появятся, сними их отсюда
        # и замени ссылки в документах на полные имена.
        184: ("docs/09_law_and_land.md", "docs/07_legal.md", "docs/04_tick.md"),
    }

    def _registry(self) -> dict[int, list[str]]:
        out: dict[int, list[str]] = {}
        for path in sorted((DOCS_DIR / "decisions").glob("*.md")):
            hit = re.match(r"^(\d+)_", path.name)
            if hit:
                out.setdefault(int(hit.group(1)), []).append(path.name)
        return out

    def test_registry_numbers_are_unique(self) -> None:
        """Два файла с одним номером в имени — падение."""
        dups = {n: names for n, names in self._registry().items() if len(names) > 1}
        self.assertEqual(
            dups,
            {},
            "в реестре решений дубль номера; уезжает тот, у которого меньше ссылок: "
            + "; ".join(f"{n}: {sorted(v)}" for n, v in sorted(dups.items())),
        )

    def test_heading_number_matches_file_name(self) -> None:
        """Номер в первой строке заголовка обязан совпадать с номером в имени файла."""
        mismatched: list[str] = []
        for path in sorted((DOCS_DIR / "decisions").glob("*.md")):
            hit = re.match(r"^(\d+)_", path.name)
            if not hit:
                continue
            name_number = int(hit.group(1))
            first = path.read_text(encoding="utf-8").splitlines()[:1]
            if not first:
                mismatched.append(f"{path.name}: файл пуст")
                continue
            title = re.match(r"^#\s*(\d+)\s*\.", first[0])
            if not title:
                mismatched.append(
                    f"{path.name}: первая строка {first[0][:50]!r} не начинается с «# NNNN.»"
                )
                continue
            if int(title.group(1)) != name_number:
                mismatched.append(
                    f"{path.name}: имя говорит {name_number:04d}, заголовок — {title.group(1)}"
                )
        self.assertEqual(
            mismatched, [], "имя файла и номер в заголовке разошлись:\n  " + "\n  ".join(mismatched)
        )

    def test_cited_adr_numbers_exist(self) -> None:
        """`ADR NNNN` вне реестра: либо файл есть, либо номер в списке известного долга."""
        registry = self._registry()
        allowed = set(registry) | self.RETIRED_BY_STATUS | set(self.KNOWN_DANGLING)
        dangling: dict[int, list[str]] = {}
        for path in sorted(DOCS_DIR.rglob("*.md")):
            if path.parent.name == "decisions":
                continue
            for number in re.findall(r"ADR\s+(\d{4})", path.read_text(encoding="utf-8")):
                if int(number) not in allowed:
                    dangling.setdefault(int(number), []).append(
                        str(path.relative_to(ROOT))
                    )
        self.assertEqual(
            dangling,
            {},
            "канон ссылается на номера, которых нет в реестре: "
            + "; ".join(
                f"ADR {n:04d} ({len(f)}: {', '.join(sorted(set(f))[:3])})"
                for n, f in sorted(dangling.items())
            ),
        )

    def test_retired_numbers_are_cited_as_history_only(self) -> None:
        """Отозванный номер не должен выглядеть основанием живого правила.

        Грубая, но полезная ловушка: если строка называет отозванный номер в «ADR
        NNNN п. X» или в скобках рядом с живым решением, она попадает в список
        на разбор Critic. Явная формулировка об отзыве («отозван», «отменён», «снят»)
        в той же строке — допустимая история и из проверки исключается.
        """
        history_words = ("отозван", "отменён", "отменен", "снят", "заменён", "заменен", "не закон")
        suspicious: list[str] = []
        for path in sorted(DOCS_DIR.rglob("*.md")):
            if path.parent.name == "decisions":
                continue
            for number, line in self._lines_with_adr(path):
                if int(number) in self.RETIRED_BY_STATUS and not any(
                    word in line.lower() for word in history_words
                ):
                    suspicious.append(f"{path.relative_to(ROOT)}: ADR {number} — {line.strip()[:90]}")
        self.assertEqual(
            suspicious,
            [],
            "отозванный номер подан как основание живого правила (ADR 0165):\n  "
            + "\n  ".join(suspicious),
        )

    def _lines_with_adr(self, path: Path) -> list[tuple[str, str]]:
        return [
            (number, line)
            for line in path.read_text(encoding="utf-8").splitlines()
            for number in re.findall(r"ADR\s+(\d{4})", line)
        ]

    def test_registry_has_no_forbidden_number(self) -> None:
        """Запрещённый номер нельзя присвоить: присвоение сделает его легитимным."""
        registry = self._registry()
        stolen = sorted(self.RETIRED_BY_STATUS & set(registry))
        self.assertEqual(
            stolen,
            [],
            "в реестре появился файл с номером, запрещённым STATUS.md: "
            + ", ".join(f"{n:04d}" for n in stolen),
        )



class TestUnpassableCriteriaAreCounted(unittest.TestCase):
    """Критерии-ловушки внутри законов: долг считается, а не прощается.

    Закон, запрещающий вещь, содержит её в собственном критерии — и критерий
    невыполним в принципе. Кто-то из agentов должен был это отменить (ADR 0159,
    ADR 0162), но не отменил. Здесь долг **считается**: `EXPECTED` хранит
    фактический вывод каждой команды на живом дереве, тест сверяет факт с
    ожиданием и печатает список при запуске. Почините критерий или смените
    ожидание осознанно — молча разъехаться нельзя.

    Форма здесь: `(ADR, координаты команды, команда, ожидаемый вывод)`.
    """

    # ADR 0159:51 — ищет опечатку `four_seads` в ADR 0152, где она и живёт.
    # Убрать опечатку нельзя: старый ADR не редактируется.
    # ADR 0165:53 — 6 = 3 в самом ADR 0165 + 3 в отчёте Critic, который его
    #   проверил. Критик считал 3 — на момент его замера отчёта ещё не было.
    # ADR 0167:70 — сам себя исключаем (`--exclude=test_docs_claims.py`): этот
    #   файл цитирует команду в `UNPASSABLE`, и без исключения **измерение долга
    #   меняет долг**. Проверено: без `--exclude` вывод 13, из них 1 — наш.
    # ADR 0174:110 — был непуст (`0171`), починен переездом двух заметок.
    # ADR 0175:203 — 3, и 2 из них правильные (`clear_forest` в `land_regimes.yml`);
    #   часть про `recipes.yml` по-прежнему мимо (рецепт называется
    #   `uproot_stumps`), то есть критерий проверяет не то, что декларирует.
    UNPASSABLE: tuple[tuple[str, str, str, int], ...] = (
        ("0152", "0159:51", 'grep -rn "four_seads" docs/decisions/0152_corrections_after_critic.md', 1),
        ("0165", "0165:53", 'grep -n "tenant_rent" -r . --include=\'*.md\' --include=\'*.yml\'', 6),
        ("0167", "0167:70", 'grep -rni "амортиз\\|amorti" docs/ design/ sim/ --exclude=test_docs_claims.py', 12),
        ("0174", "0174:110", "ls docs/decisions/ | sed 's/_.*//' | sort | uniq -d", 0),
        ("0175", "0175:203", 'grep -n "clear" design/catalogs/recipes.yml design/catalogs/land_regimes.yml', 3),
    )

    def test_unpassable_criteria_match_recorded_state(self) -> None:
        """Фактический вывод каждой ловушки обязан совпадать с записанным."""
        drifted: list[str] = []
        for adr, coord, cmd, expected in self.UNPASSABLE:
            actual = len(self._run(cmd))
            if actual != expected:
                drifted.append(
                    f"ADR {adr} ({coord}): ожидалось {expected}, получено {actual} — {cmd}"
                )
        self.assertEqual(
            drifted,
            [],
            "вывод критерия-ловушки разошёлся с записанным; чини критерий в новом ADR "
            "или осознанно обнови EXPECTED\n  " + "\n  ".join(drifted),
        )

    def test_debt_is_announced(self) -> None:
        """Список ловушек печатается при запуске: долг должен быть виден, не спрятан."""
        lines = [
            f"    ADR {adr:<5} {coord:<9} вывод={expected:<3} {cmd}"
            for adr, coord, cmd, expected in self.UNPASSABLE
        ]
        print("известный долг: критерии-ловушки в законах (невыполнимы, ADR 0159/0162):")
        print("\n".join(lines))
        self.assertEqual(len(self.UNPASSABLE), 5)

    @staticmethod
    def _run(cmd: str) -> list[str]:
        import subprocess

        return [
            out
            for out in subprocess.run(
                cmd, shell=True, capture_output=True, text=True, cwd=ROOT
            ).stdout.splitlines()
        ]

    def test_registry_does_not_claim_a_file_that_is_absent(self) -> None:
        """ADR 0181 утверждает, что `sim/tests/test_adr0105.py` — целый тест-файл.

        Файла нет: его переименовали в `test_no_free_fuel_and_relief_first.py`. Закон
        о состоянии репозитория оказался ложным, и закон о номерах покраснел из-за
        самого себя. Старый ADR не правится, поэтому ложь фиксируется здесь.
        """
        self.assertFalse(
            (TESTS_DIR / "test_adr0105.py").exists(),
            "файл, о котором ADR 0181 говорит как о существующем, снова появился — "
            "либо ADR стал правдив, либо он уже врёт",
        )
        stale = [
            (p.name, n)
            for p in sorted((DOCS_DIR / "decisions").glob("*.md"))
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if "test_adr0105" in line
        ]
        self.assertTrue(
            stale,
            "ADR 0181 перестал упоминать test_adr0105 — долг закрыт, сними его из ADR 0179",
        )
        self.assertTrue(
            (TESTS_DIR / "test_no_free_fuel_and_relief_first.py").exists(),
            "переименованный тест исчез: долг ADR 0181 стал неверным и по этой части",
        )


class TestRegistryNamesRealFiles(unittest.TestCase):
    """Реестр не должен ссылаться на несуществующий ФАЙЛ (не номер)."""

    # Найдено этим же тестом. Старые ADR не редактируются (AGENTS.md §8), поэтому
    # ложь фиксируется здесь, а не чинится. Почините — снимите из списка.
    KNOWN_MISSING_PATHS: tuple[tuple[str, str], ...] = (
        ("docs/decisions/0013_review_phase_b_fix.md", "design/scenarios/_tmp_search.yml"),
        ("docs/decisions/0028_integration_law_synthesis.md", "docs/10_water.md"),
        ("docs/decisions/0028_integration_law_synthesis.md", "docs/11_roadworks.md"),
        ("docs/decisions/0174_registry_numbers_are_unique.md", "sim/tests/test_adr0105.py"),
        ("docs/decisions/0179_registry_closes_itself.md", "sim/tests/test_adr0105.py"),
        ("docs/decisions/0179_registry_closes_itself.md", "sim/tests/test_zzz.py"),
    )

    def test_referenced_paths_exist(self) -> None:
        """Каждый `sim/…`, `design/…`, `docs/…` из реестра обязан существовать.

        Номер проверяет `test_cited_adr_numbers_exist`, а вот **файл** не проверял
        никто: ADR 0181 написал «целый тест-файл» про `test_adr0105.py`, которого
        уже не было. Пути собираются регуляркой и отбрасываются те, что содержат
        `:` (координаты), подстановочные хвосты и маски.
        """
        pattern = re.compile(
            r"`((?:sim|design|docs|tools|client)/[A-Za-z0-9_./-]+"
            r"(?:\.py|\.yml|\.md|\.sh|\.toml|\.png))`"
        )
        known = set(self.KNOWN_MISSING_PATHS)
        missing: set[tuple[str, str]] = set()
        for path in sorted((DOCS_DIR / "decisions").glob("*.md")):
            text = path.read_text(encoding="utf-8")
            for raw in pattern.findall(text):
                candidate = raw.split(":", 1)[0]
                if not (ROOT / candidate).exists():
                    missing.add((str(path.relative_to(ROOT)), candidate))
        self.assertEqual(
            sorted(missing - known),
            [],
            "реестр ссылается на НОВЫЕ несуществующие файлы: "
            + "; ".join(f"{p} -> {c}" for p, c in sorted(missing - known)),
        )
        self.assertEqual(
            sorted(known - missing),
            [],
            "известный долг починен — сними его из KNOWN_MISSING_PATHS: "
            + "; ".join(f"{p} -> {c}" for p, c in sorted(known - missing)),
        )


class TestEveryTrackedPathHasAnOwner(unittest.TestCase):
    """`AGENTS.md` §2: у пути есть владелец. Без строки писать нельзя.

    ADR 0166 назвал неполную таблицу владельцев **блокирующим дефектом**: за день
    три агента работали в бесхозных файлах, пока таблица молчала. Проверка обходит
    репозиторий и требует, чтобы каждый отслеживаемый путь попадал в одну из строк
    §2 — прямо, по префиксу или по регулярке из `KNOWN_OWNERLESS`.

    `KNOWN_OWNERLESS` — не «помилка», а список явных исключений с причиной. Новое
    исключение добавляется с указанием владельца; молчаливого исключения быть не
    должно, поэтому список короткий и проверяется на непустоту.
    """

    AGENTS_MD = ROOT / "AGENTS.md"
    # Пути, которые §2 не описывает, потому что они не имеют владельца по существу.
    KNOWN_OWNERLESS: tuple[tuple[str, str], ...] = (
        ("AGENTS.md", "правило репозитория; правило само себя не описывает"),
        ("sim/run_tests.sh", "скрипт запуска; владелец Implementer"),
        ("sim/pyproject.toml", "упаковка; владелец Implementer"),
        (".env", "локальные ключи; в .gitignore и не отслеживается git, владелец — хозяин"),
        )
    # Каталоги, которые не содержат ни одного файла, попадающего под проверку.
    SKIP_DIR_NAMES = frozenset({
        "__pycache__", ".git", "node_modules", ".mypy_cache", ".pytest_cache",
    })
    TRACKED_SUFFIXES = (".py", ".yml", ".yaml", ".md", ".sh", ".toml", ".tscn", ".gd", ".cfg")

    @staticmethod
    def _patterns(rows: list[str]) -> list[str]:
        """Строки §2 → регулярки покрытия.

        Таблица пишет пути по-разному: `docs/01`–`docs/09` (диапазон через тире),
        `docs/decisions/*.md` (глоб), `design/art/` (каталог), `world.py` (файл).
        Всё это приводится к регуляркам, иначе половина репозитория выглядит бесхозной.
        """
        out: list[str] = []
        for raw in rows:
            token = raw.split(" (")[0].strip()
            parts = [p.strip() for p in re.split(r"[,`]", token) if p.strip()]
            # Диапазон через тире: `docs/01`–`docs/09` приходит одним куском.
            expanded: list[str] = []
            for part in parts:
                match = re.match(r"^([^\u2013\-]+)[\u2013\-](.+)$", part)
                if match and "/" in match.group(1):
                    expanded.extend([match.group(1), match.group(2)])
                else:
                    expanded.append(part)
            for part in expanded:
                if not part or part in {"и", "…", "*"}:
                    continue
                if "*" in part:
                    out.append("^" + re.escape(part).replace(r"\*", ".*") + "$")
                else:
                    out.append("^" + re.escape(part))
        return out

    def _agents_owner_rows(self) -> list[str]:
        """Строки таблицы §2: первый столбец — покрываемый путь."""
        rows: list[str] = []
        for line in self.AGENTS_MD.read_text(encoding="utf-8").splitlines():
            if not line.startswith("| `"):
                continue
            first = line.split("|")[1]
            rows.append(first)
        return rows

    def _tracked_files(self) -> list[Path]:
        out: list[Path] = []
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if any(part in self.SKIP_DIR_NAMES for part in path.parts):
                continue
            if path.suffix and not path.name.endswith(self.TRACKED_SUFFIXES):
                continue
            out.append(path)
        return out

    def test_every_tracked_path_has_an_owner(self) -> None:
        """Каждый файл репозитория покрыт строкой §2 или явным исключением."""
        rows = self._agents_owner_rows()
        self.assertTrue(rows, "таблица владельцев в AGENTS.md не разобрана")

        patterns = self._patterns(rows)
        known = {p for p, _ in self.KNOWN_OWNERLESS}

        orphans: list[str] = []
        for path in self._tracked_files():
            rel = str(path.relative_to(ROOT))
            if any(fnmatch.fnmatch(rel, pat) for pat in known):
                continue
            if any(re.match(pat, rel) for pat in patterns):
                continue
            orphans.append(rel)
        self.assertEqual(
            sorted(orphans),
            [],
            "в AGENTS.md §2 нет владельца для этих путей — путь бесхозный (ADR 0166):\n  "
            + "\n  ".join(sorted(orphans)),
        )

    def test_ownerless_allowlist_stays_small(self) -> None:
        """Список исключений не должен превращаться в свалку."""
        self.assertLessEqual(
            len(self.KNOWN_OWNERLESS),
            8,
            "KNOWN_OWNERLESS разросся: исключений должно быть мало, иначе таблица §2 "
            "всё ещё неполна",
        )
        for path, reason in self.KNOWN_OWNERLESS:
            self.assertTrue(reason.strip(), f"исключение {path} без причины")



class TestLawIsPassedAsCitationNotParaphrase(unittest.TestCase):
    """ADR 0191: закон передаётся цитатой с адресом, а не пересказом.

    Случай, который стоил дорого: Economist-каталог разбирал `Good.storage` (55 строк
    в `goods.yml`, ноль чтений), предложил удалить поле — а поле требует конституция.
    Угадайте, что он опирался на: **на пересказ И-7 словами хозяина.** Глагол в
    конституции «есть», а в пересказе вышло «действует». Дословный текст конституции
    при этом никто не открывал.

    Отсюда два правила, и оба проверяются, а не декларируются.

    **Правило 1 — адрес.** Ссылка на инвариант обязана называть `docs/00_constitution.md`.
    Проверка действует **только на новые ADR (номер >= 0191)**: помечена 75 старых ADR
    из 77 упоминающих инвариант, и красный тест на них был бы ложью. Раньше §2 это
    требование не предъявлял — вина не агентов, которые писали до закона.

    **Правило 2 — дословность.** Цитата, оформленная блок-цитатой (`>`), обязана
    дословно находиться в файле, который этот ADR назвал источником. Это ловит именно
    подмену: пересказ, поставленный в кавычки. Сверка идёт по нормализованным пробелам,
    чтобы перенос строк не давал ложного срабатывания, но перефразировка давала.
    """

    DECISIONS = ROOT / "docs/decisions"
    # Граница закона: ADR с меньшим номером писались до него и не обязаны адресовать.
    CITATION_FROM = 191
    INVARIANT = re.compile(r"\u0418-\d+")
    DOC_SOURCE = re.compile(r"docs/[0-9a-z_/]+\.md")

    @staticmethod
    def _adrs() -> list[Path]:
        return sorted(p for p in DECISIONS_GLOB(ROOT) if p.is_file())

    @staticmethod
    def _number(path: Path) -> int:
        head = path.name[:4]
        return int(head) if head.isdigit() else 0

    def test_new_adr_cites_constitution_by_path(self) -> None:
        """Инвариант, названный поимённо, обязан иметь адрес."""
        offenders: list[str] = []
        for adr in self._adrs():
            if self._number(adr) < self.CITATION_FROM:
                continue
            text = adr.read_text(encoding="utf-8")
            if not self.INVARIANT.search(text):
                continue
            if "00_constitution" not in text:
                offenders.append(adr.name)
        self.assertEqual(
            offenders, [],
            "ADR называет инвариант поимённо (И-N), но не называет "
            "docs/00_constitution.md — это пересказ без адреса, а закон требует "
            "цитату (ADR 0191): " + ", ".join(offenders),
        )

    def test_block_quotes_match_a_named_source(self) -> None:
        """Цитата в кавычках обязана дословно быть в названном файле."""
        cache: dict[str, str | None] = {}

        def body(rel: str) -> str | None:
            if rel not in cache:
                target = ROOT / rel
                cache[rel] = (
                    re.sub(r"\s+", " ", target.read_text(encoding="utf-8"))
                    if target.is_file() else None
                )
            return cache[rel]

        offenders: list[str] = []
        checked = 0
        for adr in self._adrs():
            text = adr.read_text(encoding="utf-8")
            in_fence = False
            quotes: list[str] = []
            for line in text.splitlines():
                if line.lstrip().startswith("```"):
                    in_fence = not in_fence
                    continue
                if in_fence:
                    continue
                match = re.match(r"^>\s?(.*)$", line)
                if not match:
                    continue
                quote = re.sub(r"\s+", " ", match.group(1)).strip()
                # Заголовок внутри цитаты и пустая строка — не цитата закона.
                if quote and not quote.startswith("#"):
                    quotes.append(quote)
            if not quotes:
                continue
            sources = [s for s in sorted(set(self.DOC_SOURCE.findall(text))) if body(s)]
            if not sources:
                # Назвал цитату, но не назвал файл: сверять нечего, и это отдельная
                # беда — правило 1 ловит её, когда речь об инварианте.
                continue
            for quote in quotes:
                checked += 1
                if not any(quote in body(src) for src in sources):
                    offenders.append(f"{adr.name}: {quote[:60]}")
        self.assertEqual(
            offenders, [],
            "цитата не найдена дословно ни в одном названном источнике — это пересказ, "
            "поданный как цитата (ADR 0191): " + "; ".join(offenders),
        )
        self.assertGreater(checked, 0, "цитаты не нашлись: проверка выродилась в молчание")


def DECISIONS_GLOB(root: Path) -> list[Path]:
    """Список ADR реестра (вынесено, чтобы не тянуть глобал в методы)."""
    return sorted((root / "docs/decisions").glob("*.md"))


#: Имена, которые код читает **не через имя**: `obj.field`, `d["field"]`,
#: `d.get("field")`, `getattr(obj, "field")`. Всё остальное — не чтение поля.
#:
#: Список ключей расширен на `get`/`setdefault`/`pop`/`getattr`/`hasattr` не по
#: строкам вообще, а **по позиции аргумента**: у `get`/`setdefault`/`pop` имя поля —
#: первый аргумент, у `getattr`/`hasattr` — второй. Читать поле по позиции важно:
#: замер 29.09.2026 показал, что `record.get("labor_share", 1.0)` — это чтение поля
#: `HouseholdAction.labor_share`, и такой доступ обязан считаться чтением, иначе
#: сторож врёт в другую сторону и заводит мёртвые поля, которые живы.
CATALOG_KEY_CALLS_FIRST_ARG = frozenset({"get", "setdefault", "pop"})
CATALOG_KEY_CALLS_SECOND_ARG = frozenset({"getattr", "hasattr"})


def read_field_names(source: str) -> set[str]:
    """Имена каталожных полей, которые этот кусок кода реально читает.

    Чтение — это обращение к полю как к полю:

    * `obj.field` (атрибут любого объекта, `ast.Attribute`);
    * `mapping["field"]` (`ast.Subscript` со строковым ключом);
    * `mapping.get("field", ...)`, `setdefault`, `pop`;
    * `getattr(obj, "field")`, `hasattr(obj, "field")`.

    Чего это **не** делает и почему. Раньше «читателем» считалось любое совпадение
    слова `\bполе\b` в тексте `sim/src/hillcourt/**/*.py`, и это делало сторож слепым:
    `court = world.settlements.get(world.player.court_settlement_id)` — это
    *объявление локальной переменной*, а не чтение `LegalStatus.court`. Имя `court`
    встречается в блобе 22 раза, поле `LegalStatus.court` не читается **никем**, и
    сторож рапортовал «поле живо». ADR 0208 §6 (НАХОДКА 5) записал это как дефект
    самого инструмента проверки: сторож, который зеленеет на настоящем нарушителе,
    хуже отсутствующего сторожа.

    Локальные имена, имена функций и имена аргументов сюда **намеренно не входят**:
    объявление `court = ...` — не чтение поля `LegalStatus.court`, и никакое
    переименование переменной в чужой зоне не должно уметь «оживить» каталожное
    поле. Плата за это правило — состав списка мёртвых полей; он неполон и
    проверяется.
    """
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Subscript):
            _add_key(names, node.slice)
        elif isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name)
                else None
            )
            if name in CATALOG_KEY_CALLS_FIRST_ARG and node.args:
                _add_key(names, node.args[0])
            elif name in CATALOG_KEY_CALLS_SECOND_ARG and len(node.args) >= 2:
                _add_key(names, node.args[1])
    return names


def _add_key(names: set[str], node: ast.expr) -> None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        names.add(node.value)


class TestCatalogFieldHasAReader(unittest.TestCase):
    """Поле формулы без читателя — брак (ADR 0155, ADR 0164).

    `Good.material_quality` и `SpawnRule.external` были объявлены, грузились и не читались
    никем, и это держалось месяцами: «поле есть» выглядело как «поле работает». Тот же
    класс — `ObligationTemplate.default_share`, который простоял в схеме без читателя, пока
    ADR 0183 не сделал его основой оброка.

    Правило: у каждого поля каталожного `dataclass` есть читатель **вне** `ontology.py`
    (объявление) и вне `catalogs.py` (загрузчик). Фактический набор таких полей обязан
    **точно совпадать** с `ZERO_READ_ALLOWLIST`: новое поле без читателя роняет тест, и
    поле, ожившее, тоже роняет, чтобы список нельзя было забыть сократить.

    Читатель ищется по **AST** (`read_field_names`), а не текстовым совпадением слова.
    Причина — ADR 0208 §6: текстовый поиск не отличает чтение поля `LegalStatus.court`
    от локальной переменной `court`, из-за чего четвёртое мёртвое поле не попадало в
    список и его живость не проверял никто.

    Наивное «ноль чтений — падение» без списка дало бы 13 ложных срабатываний, поэтому
    список явный и с причиной у каждой записи.
    """

    DECLARATION_ONLY = ("ontology.py", "catalogs.py")

    ZERO_READ_ALLOWLIST = (
        ("Good", "price_labor_silver", "компонент каталожной цены; читает проверка цены"),
        ("Good", "price_materials_silver", "то же: компонент себестоимости, не отдельная ставка"),
        ("Good", "price_losses_silver", "то же: потери, входящие в цену"),
        ("Good", "storage", "объявлено правилом §5.3 AGENTS.md и проверяется загрузчиком; "
                            "формулой выварки/хранения код не читает (замер 29.09.2026)"),
        ("Recipe", "transform", "постусловие рецепта; проверяется тестами каталога, не кодом"),
        ("Recipe", "tool_multiplier", "упомянут в docstring economy/soil.py как ADR 0110, "
                                     "но ни одна формула его не читает (замер 29.09.2026)"),
        ("RightTemplate", "default_rent_share", "умолчание вида прав; ADR 0183 убрал долю из наделения"),
        ("RightTemplate", "land_regime_id", "связь вида прав с режимом земли, расчётом не читается"),
        ("NeedConfig", "land_regime_id", "связь потребностей с режимом земли, расчётом не читается"),
        ("LegalStatus", "marriage_needs_permission", "право пресета, проверяется загрузчиком"),
        ("LegalStatus", "court", "мёртвое: ни одного чтения. Текстовый поиск по слову "
                                 "считал его живым из-за переменной `court` (ADR 0208)"),
        ("LegalStatus", "inheritance", "право пресета, проверяется загрузчиком"),
        ("LegalStatus", "can_sell_land", "право пресета, проверяется загрузчиком"),
        ("ObligationBundle", "currency_mix", "проверяется при загрузке, расчётом не читается (ADR 0077)"),
        ("Office", "travel", "объявлено в offices.yml, ни одной формулой не читается "
                             "(замер 29.09.2026)"),
    )

    CATALOG_CLASSES = frozenset({
        "Good", "Recipe", "SpawnRule", "HazardRule", "LandRegime", "LegalStatus",
        "ObligationBundle", "CalendarMonth", "Office", "HouseholdAction", "NeedConfig",
        "ObligationTemplate", "RightTemplate",
    })

    def test_zero_read_fields_match_allowlist(self) -> None:
        """Фактический набор полей без читателя обязан совпасть со списком."""
        actual = self._zero_read()
        known = {(cls, field) for cls, field, _ in self.ZERO_READ_ALLOWLIST}
        new = sorted(f"{cls}.{field}" for cls, field in actual - known)
        self.assertEqual(
            new, [],
            "поле каталожной формулы потеряло последнего читателя — это поле с нулём "
            "чтений, тот же класс, что material_quality и SpawnRule.external:\n  "
            + "\n  ".join(new),
        )
        revived = sorted(f"{cls}.{field}" for cls, field in known - actual)
        self.assertEqual(
            revived, [],
            "поле ожило — сними его из ZERO_READ_ALLOWLIST, иначе список перестанет "
            "ловить новое: " + "; ".join(revived),
        )

    def test_allowlist_entries_have_reasons(self) -> None:
        """У каждого исключения есть причина, и список не растёт молча."""
        for cls, field, reason in self.ZERO_READ_ALLOWLIST:
            self.assertTrue(reason.strip(), f"{cls}.{field} без причины")
        self.assertLessEqual(
            len(self.ZERO_READ_ALLOWLIST), 20,
            "ZERO_READ_ALLOWLIST разросся: исключений должно быть мало",
        )

    def test_a_name_that_only_looks_like_a_read_is_not_a_reader(self) -> None:
        """Сторож не должен «оживлять» поле по совпадению имени (ADR 0208 §6).

        Мутация, которая роняла ADR 0155, сделана здесь постоянной: объявление
        локальной переменной `court` (и любая функция `def court(...)`, и строка
        `"court"`) — не чтение поля `LegalStatus.court`. Сторож, который на этом
        зеленеет, не поймал бы поле с именем-обманкой.
        """
        decoy = (
            "def f(world):\n"
            "    court = world.settlements.get(world.player.court_settlement_id)\n"
            "    def court(x):\n"
            "        return x\n"
            "    note = 'court'\n"
            "    return court, note\n"
        )
        real = "def g(status):\n    return status.court\n"
        self.assertNotIn(
            "court", read_field_names(decoy),
            "объявление/вызов/строка не должны считаться чтением поля",
        )
        self.assertIn("court", read_field_names(real), "чтение атрибута должно считаться")

    def test_every_declared_dead_field_is_really_dead(self) -> None:
        """Проверка на самом дереве: `court` и соседи действительно без чтений."""
        actual = self._zero_read()
        for cls, field in (("LegalStatus", "court"),):
            self.assertIn(
                (cls, field), actual,
                f"{cls}.{field} перестал быть мёртвым — сними его из "
                "ZERO_READ_ALLOWLIST и опиши, кто его читает",
            )

    def _zero_read(self):
        tree = ast.parse(ONTOLOGY_PY.read_text(encoding="utf-8"))
        declared = {}
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name in self.CATALOG_CLASSES:
                declared[node.name] = [
                    st.target.id
                    for st in node.body
                    if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name)
                ]
        read: set[str] = set()
        for path in sorted((SRC / "hillcourt").rglob("*.py")):
            if path.name in self.DECLARATION_ONLY:
                continue
            read |= read_field_names(path.read_text(encoding="utf-8"))
        return {
            (cls, field)
            for cls, fields in declared.items()
            for field in fields
            if field not in read
        }


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
