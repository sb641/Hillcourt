"""A2: seed собирает ОДИН мир — и черты людей, и RNG из одного числа."""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.runner import run
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SCENARIO_SEED = 1729


def _traits(world) -> tuple:
    """Кортеж (curiosity, fear) всех Person в порядке id."""
    return tuple(
        (pid, world.persons[pid].curiosity, world.persons[pid].fear)
        for pid in sorted(world.persons)
    )


class TestScenarioSeed(unittest.TestCase):
    """Один seed — один мир; CLI-переопределение не оставляет гибрида."""

    def test_load_with_seed_42_is_reproducible(self) -> None:
        first = load_scenario(SCENARIO, seed=42)
        second = load_scenario(SCENARIO, seed=42)
        self.assertEqual(first.state_hash(), second.state_hash())
        self.assertEqual(_traits(first), _traits(second))

    def test_default_run_matches_explicit_scenario_seed(self) -> None:
        default = run(SCENARIO, 6)
        explicit = run(SCENARIO, 6, seed=SCENARIO_SEED)
        self.assertEqual(default.state_hash, explicit.state_hash)

    def test_run_with_seed_42_is_reproducible(self) -> None:
        first = run(SCENARIO, 6, seed=42)
        second = run(SCENARIO, 6, seed=42)
        self.assertEqual(first.state_hash, second.state_hash)

    def test_run_seed_42_differs_from_scenario_seed(self) -> None:
        other = run(SCENARIO, 6, seed=42)
        base = run(SCENARIO, 6, seed=SCENARIO_SEED)
        self.assertNotEqual(other.state_hash, base.state_hash)

    def test_person_traits_depend_on_seed(self) -> None:
        default = load_scenario(SCENARIO)
        other = load_scenario(SCENARIO, seed=42)
        self.assertNotEqual(
            _traits(default),
            _traits(other),
            "Переопределение seed не изменило черты Person",
        )


if __name__ == "__main__":
    unittest.main()
