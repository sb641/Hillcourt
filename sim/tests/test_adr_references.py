"""Каждый `ADR NNNN` в коде либо есть в реестре, либо отозван — и это видно.

`sim/tests/test_docs_claims.py` (Scribe) сканирует только `docs/`, поэтому строка
`ADR 0114 п. 1` в комментарии кода проходила мимо любой проверки: документ удалён,
а код на него опирается. ADR 0181 §3 проводит границу: **отозванный номер как
история допустим, отозванный номер как основание живого решения — нарушение.**

Проверка различает два случая **по тексту вокруг ссылки**, потому что иначе их не
различить (ADR 0181 §3: «Автоматически отличить их по тексту нельзя, поэтому
проверка ловит грубый случай» — здесь она ловит не грубый, а оба, по окну из пяти
строк):

| случай | признак | где живёт |
|---|---|---|
| **история** | рядом (в окне ±2 строки) есть слово отзыва: отозван, снят, удалён, переиздан… | `ALLOWED_HISTORY` |
| **основание** | слова отзыва рядом нет — ссылка подана как живое обоснование | `KNOWN_DEBT` |

Два списка узкие и поштучные, оба печатаются при запуске, оба проверяются на
**неиспользуемые записи** — иначе любой из них протухает в тихую резиновую печать.
`KNOWN_DEBT` — это долг, а не прощение: он считается, печатается и у каждой записи
есть владелец правки. Новый номер вне реестра, не внесённый ни в один список,
роняет тест.

Проверка: `PYTHONPATH=sim/src python3 -m unittest sim.tests.test_adr_references -v`
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "docs" / "decisions"
SCANNED = (ROOT / "sim" / "src", ROOT / "sim" / "tests")
ADR_RE = re.compile(r"ADR\s+(\d{4})")

# Слова, которыми строка признаёт себя историей (ADR 0181 §3: прошедшее время и
# слово снятия рядом).
REVOKED_WORDS = (
    "отозван",
    "отменён",
    "отменен",
    "снят",
    "сняты",
    "снятие",
    "удалён",
    "удален",
    "удалены",
    "не существует",
    "несуществующ",
    "переиздан",
    "переиздана",
    "историческ",
    "отозван",
)

# История: отозванный номер рядом со словом отзыва. (путь, номер, почему строка
# именно историческая).
ALLOWED_HISTORY: tuple[tuple[str, str, str], ...] = (
    (
        "sim/tests/test_no_free_fuel_and_relief_first.py",
        "0105",
        "Номер 0105 не существует в реестре и отозван; модуль переименован по "
        "свойству (ADR 0181), и старый номер остался в нём только как упоминание "
        "истории в прошедшем времени.",
    ),
    (
        "sim/tests/test_relief_common_granary.py",
        "0111",
        "Снятие ADR 0111 — сам предмет модуля: он и проверяет, что общая кладовая "
        "поселения больше не источник подачи.",
    ),
    (
        "sim/tests/test_relief_common_granary.py",
        "0112",
        "Снятие ADR 0112 — тот же предмет модуля: общинных кладовых не заводим.",
    ),
    (
        "sim/tests/test_established_holder_not_starving.py",
        "0111",
        "Строка перечисляет, чего в `relief_sources` нет, со ссылкой на снятый "
        "ADR 0111, то есть фиксирует отмену, а не опирается на неё.",
    ),
    (
        "sim/tests/test_docs_claims.py",
        "0114",
        "Строка цитирует саму границу ADR 0181 §3 и признаёт отзыв прямо в строке: "
        "«отозван, закон переиздан как 0158». Это пример истории в модуле Scribe.",
    ),
    (
        "sim/src/hillcourt/economy/exchange.py",
        "0111",
        "Строка объявляет снятие сама: общая кладовая поселения больше не приоритет "
        "подачи, потому что ADR 0111 отменён.",
    ),
    (
        "sim/tests/test_adr_references.py",
        "0111",
        "Собственный текст обвинителя: называет отозванный номер, чтобы объяснить, "
        "что именно он ловит. Это упоминание истории, а не обоснование решения; "
        "новый отозванный номер, добавленный в этот файл, проверка всё равно поймает.",
    ),
    (
        "sim/tests/test_adr_references.py",
        "0112",
        "Собственный текст обвинителя: то же, что и для соседней записи 0111 — "
        "называю отозванный номер, чтобы объяснить правило.",
    ),
    (
        "sim/tests/test_adr_references.py",
        "0114",
        "Собственный текст обвинителя: то же, что и для соседних записей — называю "
        "отозванный номер, чтобы объяснить правило и перечислить долг.",
    ),
)

# Долг: отозванный номер подан как основание живого решения. Считается, не
# прощается: у каждой записи есть владелец правки. (путь, номер, владелец, долг).
KNOWN_DEBT: tuple[tuple[str, str, str, str], ...] = (
    (
        "sim/src/hillcourt/economy/needs.py",
        "0114",
        "Economist",
        "Обоснование «подача закрывает фактический недобор» висит на удалённом 0114 "
        "п. 1. Закон, который это говорит, — ADR 0158 п. 3.",
    ),
    (
        "sim/src/hillcourt/economy/exchange.py",
        "0114",
        "Economist",
        "То же в докстринге `apply_relief`: «недобор, а не фиксированная порция "
        "(номер 0114 п. 1)». Закон — ADR 0158 п. 3. Номер пишу без префикса `ADR`, "
        "чтобы цитата долга не выглядела обоснованием и в этом же файле.",
    ),
    (
        "sim/src/hillcourt/economy/decisions.py",
        "0114",
        "Economist",
        "Порядок действий при голоде обоснован удалённым 0114 п. 2. Переизданного "
        "номера с этим пунктом в ADR 0158 нет.",
    ),
    (
        "sim/src/hillcourt/economy/exchange.py",
        "0111",
        "Economist",
        "Детерминизм порядка выплат обоснован отозванным 0111 п. 3; живого "
        "основания в реестре на этот пункт нет.",
    ),
    (
        "sim/src/hillcourt/engine/tick.py",
        "0114",
        "хозяин",
        "Строка обосновывает ПОРЯДОК фаз (сначала подача) удалённым 0114. ADR 0158 "
        "п. 3 говорит о недоборе, а не о порядке, поэтому подменить номер я не мог: "
        "это было бы ложное обоснование. Жду решения хозяина.",
    ),
)


def _registry_numbers() -> set[str]:
    return {path.name.split("_", 1)[0] for path in REGISTRY.glob("*.md")}


def _python_files() -> list[Path]:
    out: list[Path] = []
    for root in SCANNED:
        out.extend(sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts))
    return out


def _revoked(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in REVOKED_WORDS)


def _mentions() -> list[tuple[str, str, int, str, str, bool]]:
    """Все `ADR NNNN` в коде.

    Возвращает `(путь, номер, строка, текст строки, текст окна, окно_с_отзывом)`.
    Окно — та же строка плюс по две соседние с каждой стороны: ADR 0181 §3 говорит
    «слово снятия **рядом**», а ссылка часто занимает две строки предложения.
    """
    out: list[tuple[str, str, int, str, str, bool]] = []
    for path in _python_files():
        relative = path.relative_to(ROOT).as_posix()
        lines = path.read_text(encoding="utf-8").split("\n")
        for line_no, text in enumerate(lines, 1):
            for number in ADR_RE.findall(text):
                window = "\n".join(lines[max(0, line_no - 3): line_no + 2])
                out.append((relative, number, line_no, text, window, _revoked(window)))
    return out


class TestAdrNumbersInCode(unittest.TestCase):
    """Обвинитель ссылок на номера, которых нет в реестре (ADR 0181 §3)."""

    def setUp(self) -> None:
        self.registry = _registry_numbers()
        self.history = {(path, number) for path, number, _ in ALLOWED_HISTORY}
        self.debt = {(path, number) for path, number, _, _ in KNOWN_DEBT}

    def test_lists_are_printed_on_every_run(self) -> None:
        """Что именно разрешено и что именно в долгу — видно при запуске."""
        print("\nИстория: отозванный номер рядом со словом отзыва (ADR 0181 §3):")
        for path, number, reason in ALLOWED_HISTORY:
            print(f"  {path}: ADR {number} — {reason.splitlines()[0]}")
        print(f"\nДолг: отозванный номер подан как основание — {len(KNOWN_DEBT)} записей:")
        for path, number, owner, debt in KNOWN_DEBT:
            print(f"  {path}: ADR {number} — владелец {owner}: {debt.splitlines()[0]}")

    def test_history_entry_says_it_is_revoked(self) -> None:
        """Строка истории без слов отзыва — это обоснование, и она в долгу."""
        for path, number, reason in ALLOWED_HISTORY:
            with self.subTest(entry=f"{path}:{number}"):
                self.assertTrue(
                    _revoked(reason),
                    f"{path}: ADR {number} в истории без пометки снятия: {reason!r}",
                )

    def test_no_entry_is_stale(self) -> None:
        """Неиспользуемая запись — протухший список: ссылку починили, запись удали."""
        used = {(path, number) for path, number, _, _, _, _ in _mentions()}
        for path, number, _ in ALLOWED_HISTORY:
            with self.subTest(list="history", entry=f"{path}:{number}"):
                self.assertIn(
                    (path, number), used,
                    f"{path}: ADR {number} в истории, а в файле его нет — запись протухла",
                )
        for path, number, _, _ in KNOWN_DEBT:
            with self.subTest(list="debt", entry=f"{path}:{number}"):
                self.assertIn(
                    (path, number), used,
                    f"{path}: ADR {number} в долгу, а в файле его нет — долг закрыт, "
                    "удали запись (ADR 0174: списки долга считаются)",
                )

    def test_revoked_number_is_history_or_listed_debt(self) -> None:
        """Номер вне реестра: либо история со словом отзыва, либо явный долг."""
        for path, number, line_no, text, _, is_history in _mentions():
            if number in self.registry:
                continue
            with self.subTest(mention=f"{path}:{line_no} ADR {number}"):
                self.assertIn(
                    (path, number), self.history | self.debt,
                    f"{path}:{line_no}: ADR {number} нет в реестре и не в списках — "
                    f"обоснование на пустоту: {text.strip()!r}",
                )
                if is_history:
                    self.assertIn(
                        (path, number), self.history,
                        f"{path}:{line_no}: ADR {number} со словом отзыва, но числится "
                        "долгом, а не историей",
                    )
                else:
                    self.assertIn(
                        (path, number), self.debt,
                        f"{path}:{line_no}: ADR {number} без слов отзыва подано как "
                        f"основание живого решения и не внесено в долг: {text.strip()!r}",
                    )

    def test_registry_numbers_are_unique(self) -> None:
        """Дубль номера в реестре — расхождение (ADR 0181 п. 1)."""
        seen: dict[str, list[str]] = {}
        for path in REGISTRY.glob("*.md"):
            seen.setdefault(path.name.split("_", 1)[0], []).append(path.name)
        duplicates = {number: names for number, names in seen.items() if len(names) > 1}
        self.assertEqual(duplicates, {}, f"Дубли номеров в реестре: {duplicates}")


if __name__ == "__main__":
    unittest.main()
