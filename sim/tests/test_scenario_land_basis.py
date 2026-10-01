"""Проверка загрузочных оснований земли и дохода двора."""

from __future__ import annotations

import atexit
import os
import tempfile
import unittest
from pathlib import Path

import yaml

from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_native_village.yml"

# Фикстуры-сценарии лежат в `design/scenarios/`, а не в корне репозитория:
# `scenario._find_repo_root` ищет `AGENTS.md`, поднимаясь от файла вверх, поэтому
# файл внутри репозитория обязателен (вне репо `load_scenario` падает), а каталог
# сценариев — ближайший к корню и не засоряет корень. Имя с префиксом
# `test_land_basis_`, а не `tmp`, чтобы не попадать под `/tmp*.yml` в .gitignore.
_TEMP_PATHS: list[Path] = []


def _cleanup_temp_paths() -> None:
    for path in _TEMP_PATHS:
        path.unlink(missing_ok=True)
    _TEMP_PATHS.clear()


atexit.register(_cleanup_temp_paths)


def _data() -> dict:
    with SCENARIO.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    for household in data["households"]:
        if household["id"] == "hh_court":
            household["settlement_id"] = "fs_01"
            household["legal_status"] = "geneat"
        elif household["id"] == "hh_02":
            household["legal_status"] = "geneat"
    return data


def _write(data: dict) -> Path:
    # Файл обязан пережить возврат из `_write`: вызывающий читает его уже потом
    # (`load_scenario(_write(data), ...)`), поэтому автоудаление NamedTemporaryFile
    # здесь неприменимо. Удаление потому вынесено в `_cleanup_temp_paths` через
    # `atexit` — файл снимается при любом исходе тестов, включая падение, и утечка
    # невозможна даже если тест упал до конца. Путь приходит из `SCENARIO.parent`
    # (внутри репозитория) — см. комментарий к `_TEMP_PATHS` выше.
    fd, name = tempfile.mkstemp(
        prefix="test_land_basis_", suffix=".yml", dir=SCENARIO.parent
    )
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True)
    path = Path(name)
    _TEMP_PATHS.append(path)
    return path


class TestScenarioLandBasis(unittest.TestCase):
    def test_zero_households_loads_without_rights_or_obligations(self) -> None:
        data = _data()
        data["settlements"] = [
            settlement for settlement in data["settlements"]
            if settlement["id"] == "hill_court"
        ]
        data["households"] = []
        data["household_groups"] = []
        data["tribes"] = []
        data["rights"] = []
        data["starting_stocks"] = {}
        data["player"] = {
            "court_settlement_id": "hill_court",
            "household_id": "hh_missing",
        }
        world = load_scenario(_write(data), seed=1729)
        self.assertIsNone(world.player.household_id)
        self.assertNotIn(world.manors["manor_hill"].holder_person_id, world.persons)
        self.assertEqual(world.households, {})
        self.assertEqual(world.rights, {})
        self.assertEqual(world.obligations, {})
        self.assertEqual(world.manors["manor_hill"].household_ids, [])
        self.assertEqual(world.get_stock("settlement:hill_court").total(), 0.0)

        data = _data()
        hill_court = next(
            settlement for settlement in data["settlements"]
            if settlement["id"] == "hill_court"
        )
        hill_court["works_tiles"] = [[2, 1], [3, 0]]
        household = next(h for h in data["households"] if h["id"] == "hh_01")
        household["settlement_id"] = "hill_court"
        household["legal_status"] = "geneat"
        with self.assertRaisesRegex(ValueError, "hh_01.*feeds_household=true"):
            load_scenario(_write(data), seed=1729)

    def test_missing_player_household_reference_is_rejected_when_households_exist(
        self,
    ) -> None:
        data = _data()
        data["player"]["household_id"] = "hh_missing"
        with self.assertRaisesRegex(ValueError, "Игрок.*hh_missing"):
            load_scenario(_write(data), seed=1729)

    def test_landless_without_basis_is_rejected(self) -> None:
        data = _data()
        household = next(h for h in data["households"] if h["id"] == "hh_02")
        household["legal_status"] = "free_landless"
        household.pop("initially_unemployed", None)
        with self.assertRaisesRegex(ValueError, "hh_02.*landless"):
            load_scenario(_write(data), seed=1729)

    def test_common_right_and_works_tiles_allows_landless(self) -> None:
        world = load_scenario(_write(_data()), seed=1729)
        self.assertEqual(
            world.households["hh_tribe_01"].legal_status_id,
            "free_landless",
        )

    def test_nonempty_salt_village_employer_allows_landless(self) -> None:
        data = _data()
        household = next(h for h in data["households"] if h["id"] == "hh_tribe_01")
        data["settlements"].append(
            {
                "id": "salt_village",
                "name": "Солеварня",
                "kind": "salt_village",
                "at": [6, 4],
                "works_tiles": [[6, 3]],
            }
        )
        household["settlement_id"] = "salt_village"
        household["legal_status"] = "free_landless"
        data["starting_stocks"]["settlement:salt_village"] = {"grain": 10.0}
        world = load_scenario(_write(data), seed=1729)
        self.assertEqual(
            world.households["hh_tribe_01"].legal_status_id,
            "free_landless",
        )

    def test_initially_unemployed_allows_landless(self) -> None:
        data = _data()
        household = next(h for h in data["households"] if h["id"] == "hh_02")
        household["legal_status"] = "free_landless"
        household["initially_unemployed"] = True
        world = load_scenario(_write(data), seed=1729)
        self.assertEqual(world.households["hh_02"].legal_status_id, "free_landless")


if __name__ == "__main__":
    unittest.main()
