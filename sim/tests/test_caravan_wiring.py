"""A1: обоз вшит в тик — соль доезжает из деревни в замок сама.

`phase_caravan` между `phase_migrate` и `phase_travel` отправляет и разгружает
обозы. Тест гоняет сценарий 36 месяцев без ручных вызовов caravan и проверяет,
что в Ledger есть разгрузка соли в замок, замковая соль выросла выше 4.0,
ни один обоз не потерян и материя не создана/не уничтожена.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.tick import run_month
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
MONTHS = 36
COURT_STORES = "settlement:hill_court"


class TestCaravanWiring(unittest.TestCase):
    """Обоз едет и разгружается самим тиком, на двух seed'ах."""

    def _run(self, seed: int | None = None):
        world = load_scenario(SCENARIO, seed=seed)
        for _ in range(MONTHS):
            run_month(world)
        return world

    def _assert_delivered(self, world) -> None:
        unloads = [
            entry
            for entry in world.ledger.entries
            if entry.kind == "transfer"
            and entry.good == "salt"
            and entry.reason == "caravan_unload"
        ]
        self.assertTrue(unloads, "За 36 месяцев обоз ни разу не разгрузился")
        self.assertTrue(
            all(
                entry.dst_id == COURT_STORES and entry.amount > 0.0
                for entry in unloads
            ),
            "Разгрузка идёт не в сток замка или нулевым куском",
        )
        castle_salt = world.get_stock(COURT_STORES).amounts.get("salt", 0.0)
        self.assertGreater(castle_salt, 4.0, "Замковая соль не выросла выше 4.0")
        caravans = [p for p in world.packs.values() if p.kind == "caravan"]
        self.assertTrue(caravans, "Ни одного обоза за прогон")
        self.assertFalse(
            any(p.status == "lost" for p in caravans), "Обоз потерян в пути"
        )
        self.assertLess(
            abs(world.ledger.delta(world.total_matter())),
            1e-6,
            "Материя не сохранилась при проводке обоза",
        )

    def test_caravan_delivers_over_36_months(self) -> None:
        self._assert_delivered(self._run())

    def test_caravan_delivers_with_seed_42(self) -> None:
        self._assert_delivered(self._run(seed=42))


if __name__ == "__main__":
    unittest.main()
