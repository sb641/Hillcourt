"""Проверка слоёв: sim не знает графики/LLM, дневной контур не трогает Person."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "hillcourt"
BANNED_MODULES = {"godot", "pygame", "PIL", "openai", "anthropic", "transformers"}
BANNED_CLASSES = {"Knight", "Explorer"}


class TestLayering(unittest.TestCase):
    """И-5 и И-10: запрещённые импорты/классы и отсутствие дневного обхода людей."""

    def test_no_forbidden_imports_or_classes(self) -> None:
        for path in sorted(SRC.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".")[0]
                        self.assertNotIn(
                            root, BANNED_MODULES, f"{path}: запрещённый импорт {alias.name}"
                        )
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        root = node.module.split(".")[0]
                        self.assertNotIn(
                            root, BANNED_MODULES, f"{path}: запрещённый импорт {node.module}"
                        )
            for name in BANNED_CLASSES:
                self.assertNotIn(f"class {name}", text, f"{path}: запрещённый класс {name}")

    def test_v0_daily_contour_does_not_walk_persons(self) -> None:
        tick_path = SRC / "engine" / "tick.py"
        tree = ast.parse(tick_path.read_text(encoding="utf-8"), filename=str(tick_path))
        checked = {"phase_growth", "run_month"}
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in checked:
                for inner in ast.walk(node):
                    if (
                        isinstance(inner, ast.Attribute)
                        and inner.attr == "persons"
                        and isinstance(inner.value, ast.Name)
                        and inner.value.id == "world"
                    ):
                        found.add(node.name)
        self.assertEqual(found, set(), f"world.persons упомянут в {sorted(found)}")


if __name__ == "__main__":
    unittest.main()
