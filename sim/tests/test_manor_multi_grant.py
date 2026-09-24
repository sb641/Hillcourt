"""Второй тэн: лимит пожалований, две независимые книги, отзыв одной.

Контракт (фаза I, часть 1):
  - число пожалований ограничено `world.stats["thegn_limit_grants"]`
    (`player.thegn_grant_limit.grants`, по умолчанию 1);
  - сценарий без ключа `grants` по-прежнему допускает ровно одно пожалование,
    второй grant отклоняется с `reason=grants_limit`;
  - каждый тэн — своя книга: `stock_id=manor:<id>`, свои дворы, свой двор
    держателя, независимый пул трудодней и амбар;
  - `revoke_thegn` второго тэна возвращает землю/дворы/амбар в корень,
    не задевая первого; материя не двоится (delta ≈ 0).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import yaml

from hillcourt.engine.manor import (
    grant_thegn,
    manor_depth,
    manor_of_tile,
    manor_stock,
    nested_manors,
    revoke_thegn,
    root_manor,
)
from hillcourt.engine.tick import run_month
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"

FIRST_PERSON = "hh_retinue_p1"
FIRST_TILES = ["t_03_01", "t_05_02"]
FIRST_HOUSEHOLD = "hh_02"
SECOND_PERSON = "hh_01_p1"
SECOND_TILE = "t_07_02"  # полевая доменная клетка из расширенных works_tiles
SECOND_HOUSEHOLD = "hh_03"
THIRD_PERSON = "hh_05_p1"
THIRD_TILE = "t_01_03"
THIRD_HOUSEHOLD = "hh_05"


class TestSecondThegn(unittest.TestCase):
    """Два тэна в лимите сценария живут в разных книгах и не мешают друг другу."""

    @classmethod
    def setUpClass(cls) -> None:
        with SCENARIO.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        # Копия v0_hill_and_salt: два пожалования и ещё одна полевая доменная
        # клетка холма (t_07_02), иначе второй тэн останется без пашни.
        data["player"]["thegn_grant_limit"] = {
            "tiles": 3,
            "households": 3,
            "grants": 2,
        }
        for entry in data["settlements"]:
            if entry["id"] == "hill_court":
                entry["works_tiles"] = [[5, 2], [6, 2], [7, 2]]
        fd, name = tempfile.mkstemp(
            prefix="test_manor_multi_grant_", suffix=".yml", dir=SCENARIO.parent
        )
        os.close(fd)
        cls.path = Path(name)
        with cls.path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, allow_unicode=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.path.unlink(missing_ok=True)

    def _grant_first(self, world):
        """Пожалование №1 на M6: держатель hh_retinue_p1, двор hh_02."""
        return grant_thegn(world, FIRST_PERSON, list(FIRST_TILES), [FIRST_HOUSEHOLD])

    def _world_two_grants(self):
        """Мир с двумя тэнами: grant №1 на M6, grant №2 на M18."""
        world = load_scenario(self.path)
        for _ in range(5):
            run_month(world)
        self.assertEqual(world.clock.month, 6)
        first = self._grant_first(world)
        self.assertIsNotNone(first)
        for _ in range(12):
            run_month(world)
        self.assertEqual(world.clock.month, 6)
        self.assertEqual(world.clock.year, 2)
        second = grant_thegn(
            world, SECOND_PERSON, [SECOND_TILE], [SECOND_HOUSEHOLD]
        )
        self.assertIsNotNone(second)
        return world, first, second

    def _rejected_reasons(self, world) -> list[str]:
        return [
            action.get("reason")
            for action in world.player_actions
            if action.get("action") == "grant_thegn_rejected"
        ]

    def test_second_grant_rejected_without_grants_key(self) -> None:
        """(a) Сценарий без `grants`: лимит 1, второй grant — grants_limit."""
        world = load_scenario(SCENARIO)
        self.assertEqual(int(world.stats.get("thegn_limit_grants", 0)), 1)
        first = self._grant_first(world)
        self.assertIsNotNone(first)
        second = grant_thegn(world, "hh_retinue_p2", ["t_05_01"], ["hh_03"])
        self.assertIsNone(second, "Второе пожалование не отклонено")
        self.assertEqual(self._rejected_reasons(world), ["grants_limit"])
        self.assertEqual(len(nested_manors(world)), 1, "Создан лишний тэн")
        self.assertEqual(len(root_manor(world).grant_ids), 1)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_second_thegn_own_book_and_third_rejected(self) -> None:
        """(b) Две книги, два амбара, два пула; третий grant — grants_limit."""
        world, first, second = self._world_two_grants()
        root = root_manor(world)

        self.assertEqual(len(nested_manors(world)), 2)
        self.assertEqual(first.parent_manor_id, root.id)
        self.assertEqual(second.parent_manor_id, root.id)
        self.assertEqual(manor_depth(world, first), 1)
        self.assertEqual(manor_depth(world, second), 1)

        self.assertEqual(first.stock_id, f"manor:{first.id}")
        self.assertEqual(second.stock_id, f"manor:{second.id}")
        self.assertNotEqual(first.stock_id, second.stock_id)
        for manor in (first, second):
            self.assertIs(manor_stock(world, manor), world.stocks[manor.stock_id])
            self.assertTrue(
                any(
                    world.tiles[tid].regime_id == "demesne"
                    for tid in manor.tile_ids
                ),
                f"У тэна '{manor.id}' нет доменной клетки",
            )

        first_holder = world.persons[FIRST_PERSON].household_id
        second_holder = world.persons[SECOND_PERSON].household_id
        self.assertEqual(first.household_ids.count(first_holder), 1)
        self.assertEqual(second.household_ids.count(second_holder), 1)
        self.assertEqual(world.households[first_holder].manor_id, first.id)
        self.assertEqual(world.households[second_holder].manor_id, second.id)
        # Книги не пересекаются, из корня дворы ушли.
        self.assertNotIn("hh_03", first.household_ids)
        self.assertNotIn("hh_02", second.household_ids)
        self.assertNotIn("hh_02", root.household_ids)
        self.assertNotIn("hh_03", root.household_ids)

        # Независимые пулы трудодней: в пахоту каждый тэн кормит свой домен.
        world.clock.month = 4
        run_month(world)
        self.assertGreater(first.demesne_labor_filled, 0.0)
        self.assertGreater(second.demesne_labor_filled, 0.0)

        third = grant_thegn(world, THIRD_PERSON, [THIRD_TILE], [THIRD_HOUSEHOLD])
        self.assertIsNone(third, "Третье пожалование не отклонено лимитом")
        self.assertEqual(self._rejected_reasons(world), ["grants_limit"])
        self.assertEqual(len(root.grant_ids), 2)

        for _ in range(12):
            run_month(world)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_revoke_second_returns_to_root_not_first(self) -> None:
        """(c) Отзыв второго тэна сливает его сток в корень, первый не тронут."""
        world, first, second = self._world_two_grants()
        root = root_manor(world)
        first_stock = world.get_stock(first.stock_id)
        root_stock = world.get_stock(root.stock_id)
        second_stock = world.get_stock(second.stock_id)

        first_amounts = dict(first_stock.amounts)
        first_tiles = sorted(first.tile_ids)
        first_households = sorted(first.household_ids)

        # Зерно второму тэну — переводом, а не из воздуха.
        world.ledger.transfer(
            root_stock, second_stock, "grain", 5.0, "test_seed", world.clock.date
        )
        root_grain_before = root_stock.amounts.get("grain", 0.0)
        second_grain = second_stock.amounts.get("grain", 0.0)
        self.assertGreater(second_grain, 0.0)

        self.assertTrue(revoke_thegn(world, second.id))

        self.assertNotIn(second.stock_id, world.stocks, "Мёртвый амбар остался")
        self.assertAlmostEqual(
            root_stock.amounts.get("grain", 0.0),
            root_grain_before + second_grain,
            places=6,
            msg="Амбар второго тэна не слился в корень",
        )
        merge = [
            entry
            for entry in world.ledger.entries
            if entry.reason == "revoke_thegn" and entry.src_id == second.stock_id
        ]
        self.assertTrue(merge, "Слияние амбара не через Ledger.transfer")
        self.assertTrue(all(entry.dst_id == root_stock.id for entry in merge))
        self.assertEqual(world.households["hh_03"].manor_id, root.id)
        self.assertEqual(
            world.households[world.persons[SECOND_PERSON].household_id].manor_id,
            root.id,
        )
        self.assertIn("hh_03", root.household_ids)
        self.assertIn(SECOND_TILE, root.tile_ids)
        self.assertEqual(manor_of_tile(world, SECOND_TILE).id, root.id)
        self.assertEqual(world.households["hh_01"].legal_status_id, "free_landless")

        # Первый тэн не тронут: своя книга, земля, дворы и амбар.
        self.assertIn(first.id, world.manors)
        self.assertIn(first.id, [manor.id for manor in nested_manors(world)])
        self.assertEqual(sorted(first.tile_ids), first_tiles)
        self.assertEqual(sorted(first.household_ids), first_households)
        self.assertEqual(first_stock.amounts, first_amounts)

        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
