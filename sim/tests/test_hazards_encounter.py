"""Опасность как популяция+сытость: одиночка гибнет чаще отряда."""

from __future__ import annotations

import random
import unittest
from pathlib import Path

from hillcourt.hazards.encounter import roll_losses
from hillcourt.hazards.model import per_person_loss_risk, settle, strongest_hazard
from hillcourt.ontology import Hazard
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"


def _hazard(population: float = 2.0, satiety: float = 0.2) -> Hazard:
    return Hazard(
        id="haz_test",
        kind="wolves",
        tile_id="t_00_00",
        intensity=1.0,
        population=population,
        satiety=satiety,
    )


class TestHazardEncounter(unittest.TestCase):
    """Шанс потерять человека падает с размером группы."""

    def test_loner_risk_much_higher_than_party(self) -> None:
        hazard = _hazard()
        loner = per_person_loss_risk(hazard, 1, base_risk=0.5)
        party = per_person_loss_risk(hazard, 3, base_risk=0.5)
        self.assertGreater(loner, party * 2.0)

    def test_satiety_lowers_risk(self) -> None:
        hungry = _hazard(satiety=0.0)
        full = _hazard(satiety=1.0)
        self.assertGreater(
            per_person_loss_risk(hungry, 1, 0.5),
            per_person_loss_risk(full, 1, 0.5),
        )

    def test_loner_dies_more_often_in_simulation(self) -> None:
        lone = sum(
            len(roll_losses(random.Random(seed), _hazard(), ["p1"], 0.5))
            for seed in range(200)
        )
        party = sum(
            len(roll_losses(random.Random(seed), _hazard(), ["p1", "p2", "p3"], 0.5))
            for seed in range(200)
        )
        self.assertGreater(lone, party)

    def test_strongest_hazard_of_wolves_tile(self) -> None:
        world = load_scenario(SCENARIO)
        wolves = next(h for h in world.hazards.values() if h.kind == "wolves")
        weaker = Hazard(
            id="haz_weak",
            kind="bog",
            tile_id=wolves.tile_id,
            intensity=0.1,
            population=0.1,
            satiety=1.0,
        )
        world.hazards[weaker.id] = weaker
        strongest = strongest_hazard(world, wolves.tile_id)
        self.assertIsNotNone(strongest)
        self.assertEqual(strongest.kind, "wolves", "Выбрана не сильнейшая угроза")

    def test_settle_changes_satiety(self) -> None:
        hazard = _hazard(satiety=0.4)
        settle(hazard, ate=True)
        after_meal = hazard.satiety
        self.assertGreater(after_meal, 0.4)
        settle(hazard, ate=False)
        self.assertLess(hazard.satiety, after_meal, "Сытость не падает без добычи")
        self.assertGreaterEqual(hazard.satiety, 0.0)


if __name__ == "__main__":
    unittest.main()
