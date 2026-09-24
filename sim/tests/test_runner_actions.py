"""B1: runner принимает фикстуру действий игрока и печатает player_actions с датами.

Внешняя фикстура (`actions=` / `--actions`) исполняется существующими
обработчиками `ACTION_HANDLERS` перед тиком указанного месяца; каждая запись
видна в `player_actions` с датой. Новая механика не вводится.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import yaml

from hillcourt.runner import load_actions_fixture, main, run

ROOT = Path(__file__).resolve().parents[2]
SHIRE = ROOT / "design" / "scenarios" / "v0_shire.yml"
SEED = 1729

FIXTURE = [
    {
        "at_month": 3,
        "action": "grant_tenement",
        "household": "hh_02",
        "tiles": ["t_01_10"],
    }
]


class TestRunnerActionsFixture(unittest.TestCase):
    """Фикстура `grant_tenement` на месяц 3 — в player_actions с датой Y1-M03."""

    def test_grant_tenement_visible_with_date(self) -> None:
        result = run(SHIRE, 4, seed=SEED, actions=[dict(FIXTURE[0])])
        records = [
            action
            for action in result.player_actions
            if action.get("action") == "grant_tenement"
        ]
        self.assertTrue(records, "grant_tenement из фикстуры не записан")
        self.assertEqual(records[0]["date"], "Y1-M03")
        self.assertEqual(records[0]["household"], "hh_02")
        self.assertEqual(records[0]["tiles"], ["t_01_10"])

    def test_fixture_keeps_matter_delta_zero(self) -> None:
        result = run(SHIRE, 4, seed=SEED, actions=[dict(FIXTURE[0])])
        self.assertAlmostEqual(result.matter_delta, 0.0, places=6)

    def test_fixture_combines_with_scenario_script(self) -> None:
        """Сценарный `script:` (пожалование M6) и фикстура уживаются."""
        result = run(SHIRE, 7, seed=SEED, actions=[dict(FIXTURE[0])])
        dated = {
            (action.get("action"), action.get("date"))
            for action in result.player_actions
        }
        self.assertIn(("grant_tenement", "Y1-M03"), dated)
        self.assertIn(("grant_thegn", "Y1-M06"), dated)


class TestRunnerActionsCli(unittest.TestCase):
    """`--actions` печатает действие с датой в stdout."""

    def test_cli_prints_fixture_action_with_date(self) -> None:
        fd, name = tempfile.mkstemp(prefix="test_actions_", suffix=".yml")
        os.close(fd)
        path = Path(name)
        try:
            with path.open("w", encoding="utf-8") as fh:
                yaml.safe_dump([dict(FIXTURE[0])], fh, allow_unicode=True)
            loaded = load_actions_fixture(path)
            self.assertEqual(len(loaded), 1)
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(
                    [
                        "--scenario", str(SHIRE),
                        "--months", "4",
                        "--seed", str(SEED),
                        "--actions", str(path),
                    ]
                )
            self.assertEqual(code, 0)
            self.assertIn("[Y1-M03] grant_tenement", buf.getvalue())
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
