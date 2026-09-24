"""B1: рычаги игрока в прогоне — сценарий-фикстура `script:` и даты действий."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import yaml

from hillcourt.engine.tick import run_month
from hillcourt.ontology import SimDate
from hillcourt.runner import _apply_script_entry, run
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"

GRANT = {
    "at_month": 6,
    "action": "grant_thegn",
    "person": "hh_retinue_p1",
    "tiles": ["t_03_01", "t_05_02"],
    "households": ["hh_02"],
}


class TestScriptedActions(unittest.TestCase):
    """Сценарий-фикстура исполняет право и печатает действия с датами."""

    def setUp(self) -> None:
        with SCENARIO.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        data["script"] = [dict(GRANT)]
        fd, name = tempfile.mkstemp(
            prefix="test_script_", suffix=".yml", dir=SCENARIO.parent
        )
        os.close(fd)
        self.path = Path(name)
        with self.path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, allow_unicode=True)

    def tearDown(self) -> None:
        self.path.unlink(missing_ok=True)

    def _run_world(self, months: int = 18, seed: int = 1729):
        """Прогнать мир вручную, исполняя `script:` перед каждым месяцем."""
        world = load_scenario(self.path, seed=seed)
        for month_index in range(1, months + 1):
            for entry in world.script:
                if int(entry.get("at_month", 0)) == month_index:
                    _apply_script_entry(world, entry)
            run_month(world)
        return world

    def test_grant_thegn_is_recorded_with_date(self) -> None:
        result = run(self.path, 18, seed=1729)
        records = [
            action
            for action in result.player_actions
            if action.get("action") == "grant_thegn"
        ]
        self.assertTrue(records, "grant_thegn не записан в player_actions")
        self.assertEqual(records[0]["date"], "Y1-M06")

    def test_nested_manor_has_stock(self) -> None:
        world = self._run_world()
        nested = [
            manor
            for manor in world.manors.values()
            if manor.parent_manor_id is not None
        ]
        self.assertTrue(nested, "После grant нет вложенного манора")
        self.assertTrue(nested[0].stock_id, "У вложенного манора пустой stock_id")

    def test_no_gafol_from_granted_household_to_court(self) -> None:
        world = self._run_world()
        grant_date = SimDate(1, 6, 1)
        bad = [
            entry
            for entry in world.ledger.entries
            if entry.reason == "gafol"
            and entry.src_id == "household:hh_02"
            and entry.dst_id == "settlement:hill_court"
            and entry.date >= grant_date
        ]
        self.assertEqual(
            bad, [], "После пожалования гэфоль hh_02 всё ещё течёт в замок"
        )

    def test_matter_delta_is_zero(self) -> None:
        result = run(self.path, 18, seed=1729)
        self.assertAlmostEqual(result.matter_delta, 0.0, places=6)

    def test_same_seed_gives_same_hash(self) -> None:
        first = run(self.path, 18, seed=1729)
        second = run(self.path, 18, seed=1729)
        self.assertEqual(first.state_hash, second.state_hash)

    def test_unknown_action_raises_with_date_and_name(self) -> None:
        world = load_scenario(self.path, seed=1729)
        with self.assertRaises(ValueError) as ctx:
            _apply_script_entry(world, {"at_month": 1, "action": "no_such_lever"})
        message = str(ctx.exception)
        self.assertIn("no_such_lever", message)
        self.assertIn(str(world.clock.date), message)


if __name__ == "__main__":
    unittest.main()
