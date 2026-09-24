"""D1: третий посёлок — деревня у ясеня как обычные дворы книги manor_hill.

Не новая карта и не новый движок: те же клетки холма, две добавленные строки
снизу, ash_village симулируется всеми фазами наравне с fs_*. Сценарий обязан
грузиться существующим `load_scenario` без правок кода.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.tick import run_month
from hillcourt.runner import _apply_script_entry
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_two_settlements.yml"

AV_HOUSEHOLDS = [f"av_{n:02d}" for n in range(1, 13)]
HOME_TILE = "t_02_07"
FIELD_WORKS = ["t_03_07", "t_04_07", "t_05_08", "t_06_08"]


class TestTwoSettlementsWorld(unittest.TestCase):
    """Мир из трёх посёлков: холм, соль и деревня у ясеня в книге manor_hill."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO)

    def _run_months(self, months: int):
        """Прогнать мир вручную, исполняя `script:` перед каждым месяцем."""
        world = load_scenario(SCENARIO, seed=1729)
        for month_index in range(1, months + 1):
            for entry in world.script:
                if int(entry.get("at_month", 0)) == month_index:
                    _apply_script_entry(world, entry)
            run_month(world)
        return world

    def test_three_settlements_present(self) -> None:
        for sid in ("hill_court", "salt_village", "ash_village"):
            self.assertIn(sid, self.world.settlements, f"Нет поселения {sid}")
        self.assertEqual(self.world.settlements["ash_village"].kind, "village")

    def test_ash_households_alive_on_manor_book(self) -> None:
        for hid in AV_HOUSEHOLDS:
            self.assertIn(hid, self.world.households, f"Нет двора {hid}")
            household = self.world.households[hid]
            self.assertIsNone(household.left_at, f"{hid} ушёл на старте")
            self.assertEqual(household.settlement_id, "ash_village")
            self.assertEqual(
                household.manor_id,
                "manor_hill",
                f"{hid} не в книге корневого манора",
            )

    def test_home_tile_regime_feeds_household(self) -> None:
        tile = self.world.tiles[HOME_TILE]
        self.assertEqual(tile.settlement_id, "ash_village")
        regime = self.world.catalogs.land_regimes[tile.regime_id]
        self.assertTrue(
            regime.feeds_household,
            f"Режим '{tile.regime_id}' усадьбы {HOME_TILE} не кормит двор",
        )

    def test_works_tiles_are_four_fields(self) -> None:
        settlement = self.world.settlements["ash_village"]
        self.assertEqual(set(settlement.works_tiles), set(FIELD_WORKS))
        for tile_id in settlement.works_tiles:
            self.assertEqual(
                self.world.tiles[tile_id].terrain, "field", f"{tile_id} не поле"
            )

    def test_root_manor_holds_ash_tiles(self) -> None:
        root = self.world.manors["manor_hill"]
        for tile_id in [HOME_TILE] + FIELD_WORKS:
            self.assertIn(tile_id, root.tile_ids, f"{tile_id} нет в корневом маноре")
        for hid in AV_HOUSEHOLDS:
            self.assertIn(hid, root.household_ids)

    def test_script_has_single_grant_thegn(self) -> None:
        self.assertTrue(self.world.script, "script: пуст")
        grants = [
            entry
            for entry in self.world.script
            if entry.get("action") == "grant_thegn"
        ]
        self.assertEqual(len(grants), 1, "ожидалось ровно одно пожалование тэна")

    def test_twelve_months_conserve_and_ash_stays(self) -> None:
        """12 месяцев: материя цела, деревня у ясеня в основном держится.

        F1-стресс (10 дворов на общей усадьбе) честно выбивает один двор к
        концу года; порог допускает уход отдельных голодных, но не распад.
        """
        world = self._run_months(12)
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )
        living_ash = [
            hid
            for hid in AV_HOUSEHOLDS
            if world.households[hid].left_at is None
        ]
        self.assertGreaterEqual(
            len(living_ash), 10, f"в ash_village осталось дворов: {len(living_ash)}"
        )

    def test_thirty_six_months_runs_with_living_households(self) -> None:
        """36 месяцев: мир не падает, материя цела, уход голодных — не крах.

        Порог не «подгоняет» экономику: голод и уход дворов в v0 — честный
        результат без подключённого обмена. F1-стресс (12 дворов ash_village)
        снижает выживаемость относительно 8 дворов, поэтому фиксируется
        измеренный уровень, а не прежний.
        """
        world = self._run_months(36)
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )
        departed = [
            hid for hid, hh in world.households.items() if hh.left_at is not None
        ]
        living = len(world.households) - len(departed)
        self.assertGreaterEqual(living, 18, f"живых дворов всего {living}")
        self.assertLessEqual(
            len(departed), 8, f"ушло дворов {len(departed)}: {sorted(departed)}"
        )


if __name__ == "__main__":
    unittest.main()
