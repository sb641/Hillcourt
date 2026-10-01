"""Плотность дворов: внутри гекса 5, на urban-гексе 20 (ADR 0083, ADR 0132 п. 3).

Проверка капа в загрузчике обязана выполняться **всегда**, в том числе когда
`household_tiles` не объявлен: пустой список значит «все дворы поселения на
гексе `at`», а не «проверку пропустить». Шестой двор на сельском гексе и 21-й
на urban-гексе отвергаются загрузкой, а не молча проходят.

Про канонические карты. Раньше здесь стояли два теста «старые миры
перенаселены»: `load_scenario(v0_shire)` и `load_scenario(v0_two_settlements)`
обязаны были БРОСИТЬ `ValueError`. Это опиралось на частный факт о раскладке
дворов, а не на закон: как только карта раскладывает дворы по нескольким гексам
(так сегодня сделано через `household_tiles`), миры становятся законными, и тест
краснел наоборот. Теперь закон проверяется так:

  * **канон грузится и кап держит** — числа берутся из кода
    (`scenario._household_cap`), а не из ожидаемого исключения;
  * **перенаселённый гекс отвергается** — такой гекс строится здесь же, в
    фикстуре, потому что закон не зависит от того, как сегодня раскладываются
    дворы в каноне.
"""

from __future__ import annotations

import atexit
import os
import tempfile
import unittest
from pathlib import Path

import yaml

from hillcourt.scenario import _household_cap, load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "design" / "scenarios"
BASE = SCENARIOS / "v0_native_village.yml"

COURT_TILE = [5, 2]
RURAL_TILES = [[5, 2], [4, 2], [3, 3]]
CANON = ("v0_hill_and_salt.yml", "v0_two_settlements.yml", "v0_shire.yml", "v0_barony_100.yml")

# Фикстуры-сценарии лежат внутри репозитория (`load_scenario` ищет AGENTS.md
# относительно файла), значит `tempfile` без `dir=` уронил бы их в корень, а
# `finally` не срабатывает при оборванном прогоне. Поэтому снос — на `atexit`,
# как и сделано в `test_scenario_land_basis`.
_TEMP_PATHS: list[Path] = []


def _cleanup_temp_paths() -> None:
    for path in _TEMP_PATHS:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


atexit.register(_cleanup_temp_paths)


def _base() -> dict:
    with BASE.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _village(
    count: int,
    household_tiles: list[list[int]] | None = None,
    kind: str = "village",
) -> dict:
    """Одно поселение на гексе `COURT_TILE` с `count` дворами.

    `household_tiles=None` — секция не объявлена вовсе (случай старых миров).
    """
    data = _base()
    for key in ("tribes", "rights", "marks", "initial_hazards"):
        data.pop(key, None)
    settlement = {
        "id": "v",
        "name": "Двор на холме" if kind == "hill_court" else "Деревня",
        "kind": kind,
        "at": list(COURT_TILE),
        "works_tiles": [[4, 2], [5, 1], [3, 3]],
    }
    if household_tiles is not None:
        settlement["household_tiles"] = [list(point) for point in household_tiles]
    data["settlements"] = [settlement]
    data["player"] = {"court_settlement_id": "v", "rent_share": 0.1}
    data["households"] = [
        {
            "id": f"hh_{index:02d}",
            "name": f"Двор {index:02d}",
            "settlement_id": "v",
            "adults": 2,
            "children": 1,
            "legal_status": "free_landless",
            "initially_unemployed": True,
        }
        for index in range(1, count + 1)
    ]
    data["starting_stocks"] = {
        f"household:hh_{index:02d}": {"grain": 30.0} for index in range(1, count + 1)
    }
    return data


def _load(data: dict):
    """Загрузить сценарий из временного файла внутри репозитория (ищет AGENTS.md).

    Снос — на `atexit` (см. модульный docstring): `finally` не выполняется, если
    прогон оборван, а файл с именем `tmp*.yml` в корне прячется `.gitignore`.
    """
    handle, name = tempfile.mkstemp(suffix=".yml", dir=ROOT)
    path = Path(name)
    _TEMP_PATHS.append(path)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, allow_unicode=True)
        return load_scenario(path, seed=1729)
    finally:
        # Снос на КАЖДОМ выходе, а не только на успешном: раньше файл жил до
        # , и оборванный прогон (таймаут, SIGTERM) оставлял в корне
        # репозитория  — полные сценарии в 2-7 КБ.  ловит
        # исключение;  ниже остаётся второй стеной на SIGTERM.
        path.unlink(missing_ok=True)


def _per_tile(world) -> dict[str, int]:
    """Сколько дворов стоит на каждом гексе мира."""
    counts: dict[str, int] = {}
    for household in world.households.values():
        counts[household.current_tile_id] = counts.get(household.current_tile_id, 0) + 1
    return counts


class TestDensityCapAlwaysRuns(unittest.TestCase):
    """Кап не обходится пустым `household_tiles` — это и была поломка."""

    def test_six_households_without_household_tiles_rejected(self) -> None:
        with self.assertRaises(ValueError) as caught:
            _load(_village(6, household_tiles=None))
        message = str(caught.exception)
        self.assertIn("t_05_02", message)
        self.assertIn("6 дворов", message)
        self.assertIn("капе 5", message)

    def test_five_households_without_household_tiles_accepted(self) -> None:
        world = _load(_village(5, household_tiles=None))
        self.assertEqual(
            sorted(hh.current_tile_id for hh in world.households.values()),
            ["t_05_02"] * 5,
        )

    def test_six_households_on_one_declared_tile_rejected(self) -> None:
        with self.assertRaises(ValueError) as caught:
            _load(_village(6, household_tiles=[COURT_TILE]))
        self.assertIn("6 дворов", str(caught.exception))


class TestDensityCapPerHex(unittest.TestCase):
    """Пять дворов проходят, шестой — нет; кап считается по гексу."""

    def test_five_pass_and_six_rejected(self) -> None:
        self.assertEqual(len(_load(_village(5, household_tiles=[COURT_TILE])).households), 5)
        with self.assertRaises(ValueError):
            _load(_village(6, household_tiles=[COURT_TILE]))

    def test_cap_runs_with_nonempty_household_tiles(self) -> None:
        """Три гекса по 5 дворов — десятый, одиннадцатый... пятнадцатый проходят,
        а шестнадцатый кладёт шестого на `t_05_02` и загрузка падает."""
        world = _load(_village(15, household_tiles=RURAL_TILES))
        placed = {tile_id: 0 for tile_id in ("t_05_02", "t_04_02", "t_03_03")}
        for household in world.households.values():
            placed[household.current_tile_id] += 1
        self.assertEqual(placed, {"t_05_02": 5, "t_04_02": 5, "t_03_03": 5})
        with self.assertRaises(ValueError) as caught:
            _load(_village(16, household_tiles=RURAL_TILES))
        self.assertIn("6 дворов", str(caught.exception))

    def test_ten_across_two_hexes_do_not_sum_into_one_cap(self) -> None:
        world = _load(_village(10, household_tiles=[[5, 2], [4, 2]]))
        placed: dict[str, int] = {}
        for household in world.households.values():
            placed[household.current_tile_id] = placed.get(household.current_tile_id, 0) + 1
        self.assertEqual(placed, {"t_05_02": 5, "t_04_02": 5})


class TestUrbanCap(unittest.TestCase):
    """`hill_court` — urban-гекс: 20 дворов проходят, 21-й нет."""

    def test_twenty_urban_households_accepted(self) -> None:
        world = _load(_village(20, household_tiles=[COURT_TILE], kind="hill_court"))
        urban = [
            hh for hh in world.households.values() if hh.current_tile_id == "t_05_02"
        ]
        self.assertEqual(len(urban), 20)

    def test_twenty_first_urban_household_rejected(self) -> None:
        with self.assertRaises(ValueError) as caught:
            _load(_village(21, household_tiles=[COURT_TILE], kind="hill_court"))
        self.assertIn("21 дворов", str(caught.exception))
        self.assertIn("капе 20", str(caught.exception))


class TestOldWorldsAreLawful(unittest.TestCase):
    """Канонические карты: кап держится, и держит его не «ожидаемое исключение»."""

    def test_canon_maps_load_and_respect_the_cap(self) -> None:
        """Каждый канон грузится, и ни один гекс не переполнен.

        Кап берётся из кода (`_household_cap` по виду поселения), поэтому тест
        остаётся законным при любой раскладке дворов, которую выберет картограф.
        """
        for name in CANON:
            with self.subTest(scenario=name):
                world = load_scenario(SCENARIOS / name, seed=1729)
                caps = {
                    settlement.id: _household_cap(settlement.kind)
                    for settlement in world.settlements.values()
                }
                settlement_of_tile: dict[str, set[str]] = {}
                for settlement in world.settlements.values():
                    for tile_id in (f"t_{settlement.coord[0]:02d}_{settlement.coord[1]:02d}",
                                    *settlement.works_tiles):
                        settlement_of_tile.setdefault(tile_id, set()).add(settlement.id)
                for tile_id, count in _per_tile(world).items():
                    owners = settlement_of_tile.get(tile_id)
                    if not owners:
                        continue
                    cap = max(caps[owner] for owner in owners)
                    self.assertLessEqual(
                        count, cap, f"{name}: гекс {tile_id} переполнен ({count} при {cap})"
                    )

    def test_overpacked_hex_is_rejected_whatever_the_canon_looks_like(self) -> None:
        """Перенаселённый гекс отвергается — гекс построен здесь, а не взят из канона.

        Тот же закон, что и раньше, только стенд не зависит от того, как сегодня
        разложены дворы в `v0_shire`.
        """
        with self.assertRaises(ValueError) as caught:
            _load(_village(6, household_tiles=[COURT_TILE]))
        message = str(caught.exception)
        self.assertIn("t_05_02", message)
        self.assertIn("6 дворов", message)
        self.assertIn("капе 5", message)

    def test_six_over_four_hexes_passes_while_six_on_one_does_not(self) -> None:
        """Шесть дворов на четырёх гексах — законно; шестой на том же гексе — нет.

        Это ровно тот случай, из-за которого прежние тесты «старые миры
        перенаселены» стали ложными: раскладка по гексам меняет допустимость, а
        закон — нет.
        """
        spread = _village(6, household_tiles=[[5, 2], [4, 2], [3, 3], [2, 3]])
        world = _load(spread)
        self.assertEqual(max(_per_tile(world).values()), 2)
        with self.assertRaises(ValueError):
            _load(_village(6, household_tiles=[COURT_TILE]))


if __name__ == "__main__":
    unittest.main()
