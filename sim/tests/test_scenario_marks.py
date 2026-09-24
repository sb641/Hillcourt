"""Секция `marks:` в загрузчике: маркеры вида из YAML (ADR 0065, view-only).

Маркеры — данные сценария: булевы (`LAND_MARKS` → `True`) и строковый
`dwelling` (уровень дома из `DWELLING_FORMS`). Тик их не пишет и не читает
(И-5: вид без бонусов), онтология не меняется — стоят через `getattr`.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.tick import run_month
from hillcourt.engine.tile_view import DWELLING_FORMS, dwelling_form, has_mark, tile_form
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_native_village.yml"
DWELLING_TILE = "t_00_03"
TAVERN_TILE = "t_03_01"


def _data() -> dict:
    import yaml

    with SCENARIO.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _write(data: dict) -> Path:
    import tempfile

    import yaml

    with tempfile.NamedTemporaryFile(
        "w", suffix=".yml", delete=False, encoding="utf-8"
    ) as fh:
        yaml.safe_dump(data, fh, allow_unicode=True)
        return Path(fh.name)


class TestMarksSection(unittest.TestCase):
    """Форма появляется из YAML; значения и клетки проверяются."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO, seed=1729)

    def test_forms_appear_from_yaml(self) -> None:
        self.assertEqual(tile_form(self.world, DWELLING_TILE), "tent_earth_homestead")
        self.assertEqual(
            dwelling_form(self.world, DWELLING_TILE), DWELLING_FORMS["tent_earth"]
        )
        self.assertEqual(tile_form(self.world, TAVERN_TILE), "tavern_site")
        self.assertTrue(has_mark(self.world, TAVERN_TILE, "tavern"))

    def test_marker_is_data_not_world_field(self) -> None:
        tile = self.world.tiles[DWELLING_TILE]
        self.assertEqual(getattr(tile, "dwelling", None), "tent_earth")
        self.assertFalse(
            hasattr(type(tile), "dwelling"),
            "Маркер вида стал полем онтологии",
        )

    def test_tick_does_not_touch_marks(self) -> None:
        before = {
            tile_id: (getattr(tile, "dwelling", None), getattr(tile, "tavern", False))
            for tile_id, tile in self.world.tiles.items()
        }
        for _ in range(6):
            run_month(self.world)
        after = {
            tile_id: (getattr(tile, "dwelling", None), getattr(tile, "tavern", False))
            for tile_id, tile in self.world.tiles.items()
        }
        self.assertEqual(after, before, "Тик тронул маркеры вида")
        self.assertAlmostEqual(
            self.world.ledger.delta(self.world.total_matter()), 0.0, places=6
        )

    def test_mark_on_missing_tile_rejected(self) -> None:
        data = _data()
        data["marks"] = [{"at": [9, 9], "mark": "tavern"}]
        with self.assertRaises(ValueError):
            load_scenario(_write(data), seed=1729)

    def test_unknown_mark_rejected(self) -> None:
        data = _data()
        data["marks"] = [{"at": [0, 3], "mark": "wat_mill"}]
        with self.assertRaises(ValueError):
            load_scenario(_write(data), seed=1729)

    def test_unknown_dwelling_value_rejected(self) -> None:
        data = _data()
        data["marks"] = [{"at": [0, 3], "mark": "dwelling", "value": "barracks"}]
        with self.assertRaises(ValueError):
            load_scenario(_write(data), seed=1729)

    def test_boolean_mark_rejects_value(self) -> None:
        data = _data()
        data["marks"] = [{"at": [0, 3], "mark": "tavern", "value": "beer"}]
        with self.assertRaises(ValueError):
            load_scenario(_write(data), seed=1729)

    def test_canon_scenarios_load_without_marks(self) -> None:
        for name in ("v0_hill_and_salt.yml", "v0_two_settlements.yml", "v0_shire.yml"):
            data = _data()
            data.pop("marks", None)
            world = load_scenario(ROOT / "design" / "scenarios" / name, seed=1729)
            self.assertFalse(
                [
                    tile_id
                    for tile_id, tile in world.tiles.items()
                    if getattr(tile, "dwelling", None) is not None
                    or getattr(tile, "tavern", False)
                ],
                f"{name}: маркер без секции marks",
            )


if __name__ == "__main__":
    unittest.main()
