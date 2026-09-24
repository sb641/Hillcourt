"""Племенная деревня v1: загрузчик вида и общих прав (ADR 0059/0060).

Сценарий `design/scenarios/v0_native_village.yml` — мир племени: вид поселения
`native_village` и права (`kind=common`) берутся из YAML, без ручных атрибутов
в коде; племенные дворы `free_landless` и вне книги корневого манора
(`manor_id=None`); `common` — общий доступ, а НЕ индивидуальный надел
(механика Economist в `economy/labor.py`, здесь пин). И-3: племя не расширяет
знание игрока — формулировка вести существующего канала (код Info).
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy.labor import _held_tile_ids, own_tiles
from hillcourt.engine.path import known_tiles
from hillcourt.engine.tick import run_month
from hillcourt.news.tribe import tribe_content
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_native_village.yml"
TRIBE = "tribal_village"
TRIBE_TILE = "t_05_02"
COMMON_RIGHT = "right_common_pasture"
GRAZING_RIGHT = "right_grazing_hill"
COMMON_TILE = "t_05_03"


class TestNativeVillageScenario(unittest.TestCase):
    """Сценарий племени грузится и ведёт себя по закону."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO, seed=1729)

    def test_settlement_kind_loaded_from_yaml(self) -> None:
        settlement = self.world.settlements[TRIBE]
        self.assertEqual(settlement.kind, "native_village")
        self.assertEqual(settlement.coord, (5, 2))
        self.assertEqual(self.world.tiles[TRIBE_TILE].settlement_id, TRIBE)
        self.assertTrue(settlement.works_tiles)
        terrains = {
            tile_id: self.world.tiles[tile_id].terrain
            for tile_id in settlement.works_tiles
        }
        self.assertIn("pasture", terrains.values(), "Пастбища не в works_tiles")
        for tile_id, terrain in terrains.items():
            self.assertIn(terrain, ("pasture", "heath"), f"{tile_id}: не угодье")

    def test_tribe_households_free_landless_and_out_of_manor(self) -> None:
        settlement = self.world.settlements[TRIBE]
        self.assertEqual(len(settlement.household_ids), 3)
        root = self.world.manors[self.world.player_manor_id]
        for hid in settlement.household_ids:
            household = self.world.households[hid]
            self.assertEqual(household.legal_status_id, "free_landless")
            self.assertEqual(household.personal_status, "free")
            self.assertEqual(household.land_relation, "landless")
            self.assertIsNone(household.manor_id, f"{hid}: племя в книге лорда")
            self.assertNotIn(hid, root.household_ids)
            self.assertEqual(household.current_tile_id, TRIBE_TILE)

    def test_rights_loaded_from_yaml(self) -> None:
        common = self.world.rights[COMMON_RIGHT]
        self.assertEqual(common.kind, "common")
        self.assertEqual(common.holder_household_id, TRIBE)
        self.assertEqual(common.tile_id, COMMON_TILE)
        self.assertEqual(common.granted_date, self.world.clock.date)
        grazing = self.world.rights[GRAZING_RIGHT]
        self.assertEqual(grazing.kind, "grazing")
        self.assertEqual(grazing.holder_household_id, "hh_court")
        self.assertEqual(grazing.tile_id, COMMON_TILE)

    def test_common_right_gives_no_holding(self) -> None:
        tribe = self.world.households["hh_tribe_01"]
        held = {tile.id for tile in own_tiles(self.world, tribe)}
        self.assertNotIn(
            COMMON_TILE, held, "Right.common стал индивидуальным наделом"
        )
        self.assertNotIn(COMMON_TILE, _held_tile_ids(self.world, tribe))
        # Соседняя с селом (grazing) — обычное индивидуальное право: в надел идёт.
        lord = self.world.households["hh_court"]
        lord_held = {tile.id for tile in own_tiles(self.world, lord)}
        self.assertIn(COMMON_TILE, lord_held, "Индивидуальное право не сработало")

    def test_tribe_wording_by_kind_only(self) -> None:
        tribe = tribe_content(
            self.world,
            TRIBE_TILE,
            "eye_from_hill",
            "Видно с холма: деревня",
            {"grain_approx": 3.0},
        )
        self.assertIn("племенная", tribe.lower())
        plain = tribe_content(
            self.world, "t_00_03", "eye_from_hill", "Видно с холма: деревня", {}
        )
        self.assertEqual(
            plain, "Видно с холма: деревня", "Чужая клетка получила племенный текст"
        )

    def test_knowledge_not_grown_by_tribe(self) -> None:
        self.assertEqual(
            known_tiles(self.world),
            {"t_01_01"},
            "Племя само по себе растит знание игрока (И-3)",
        )

    def test_runs_twelve_months_deterministic_delta_zero(self) -> None:
        for _ in range(12):
            run_month(self.world)
        alive = [
            hid
            for hid in ("hh_tribe_01", "hh_tribe_02", "hh_tribe_03")
            if self.world.households[hid].left_at is None
        ]
        self.assertEqual(len(alive), 3, "Племя вымерло за 12 месяцев")
        self.assertAlmostEqual(
            self.world.ledger.delta(self.world.total_matter()), 0.0, places=6
        )
        again = load_scenario(SCENARIO, seed=1729)
        for _ in range(12):
            run_month(again)
        self.assertEqual(
            self.world.state_hash(), again.state_hash(), "Тот же seed — разный мир"
        )


class TestLoaderGuards(unittest.TestCase):
    """Гейты загрузчика: прямоугольная карта, носитель и клетка права."""

    def _data(self) -> dict:
        import yaml

        with SCENARIO.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    def _write(self, data: dict) -> Path:
        import tempfile

        import yaml

        with tempfile.NamedTemporaryFile(
            "w", suffix=".yml", delete=False, encoding="utf-8"
        ) as fh:
            yaml.safe_dump(data, fh, allow_unicode=True)
            return Path(fh.name)

    def test_ragged_map_rejected(self) -> None:
        data = self._data()
        data["map"]["rows"][1] = data["map"]["rows"][1][:-1]
        with self.assertRaises(ValueError):
            load_scenario(self._write(data), seed=1729)

    def test_right_without_holder_rejected(self) -> None:
        data = self._data()
        data["rights"] = [{"id": "right_bad", "kind": "common", "at": [5, 3]}]
        with self.assertRaises(ValueError):
            load_scenario(self._write(data), seed=1729)

    def test_right_on_missing_tile_rejected(self) -> None:
        data = self._data()
        data["rights"] = [
            {
                "id": "right_bad",
                "kind": "common",
                "holder_settlement_id": TRIBE,
                "at": [9, 9],
            }
        ]
        with self.assertRaises(ValueError):
            load_scenario(self._write(data), seed=1729)


if __name__ == "__main__":
    unittest.main()
