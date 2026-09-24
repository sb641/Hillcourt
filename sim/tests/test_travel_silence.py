"""Посылка людей: послать можно по статусу; погиб отряд — приходит молчание."""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.hazards.travel import resolve_packs
from hillcourt.info.knowledge import build_player_knowledge
from hillcourt.legal.actions import send_party
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
WOLVES_TILE = "t_00_01"


class _ZeroRng:
    """Заглушка потока: любой бросок выпадает в 0 — отряд гибнет целиком."""

    def random(self) -> float:
        return 0.0

    def choice(self, seq):
        return seq[0]


class TestTravelSilence(unittest.TestCase):
    """Разбор похода и молчание вместо пересказа волка."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO)

    def test_send_requires_can_be_sent(self) -> None:
        world = self.world
        adults = list(world.households["hh_01"].member_ids)
        with self.assertRaises(PermissionError):
            send_party(world, "hh_01", adults, WOLVES_TILE)

    def test_send_requires_adjacent(self) -> None:
        world = self.world
        adults = list(world.households["hh_retinue"].member_ids)
        with self.assertRaises(ValueError):
            send_party(world, "hh_retinue", adults, "t_08_05")

    def test_lost_party_yields_silence_not_wolf_replay(self) -> None:
        world = self.world
        household = world.households["hh_retinue"]
        adults = list(household.member_ids)[:3]
        pack = send_party(world, "hh_retinue", adults, WOLVES_TILE, kind="party")

        wolves = next(
            h for h in world.hazards.values() if h.tile_id == WOLVES_TILE
        )
        wolves.population = 50.0
        wolves.satiety = 0.0
        world.rng.hazard = _ZeroRng()

        resolve_packs(world, pack.eta_date)

        self.assertEqual(pack.status, "lost")
        self.assertFalse(pack.member_ids)
        silences = [
            r
            for r in world.reports
            if r.source == "silence" and r.subject_id == WOLVES_TILE
        ]
        self.assertTrue(silences, "Потерянный отряд не породил молчания")
        replayed = [
            r
            for r in world.reports
            if r.subject_id == WOLVES_TILE and "hazard" in r.facts
        ]
        self.assertFalse(replayed, "Игрок получил пересказ волка от погибшего отряда")

        knowledge = build_player_knowledge(world, pack.eta_date)
        self.assertTrue(
            any(entry.about == WOLVES_TILE for entry in knowledge.silences()),
            "Молчание не попало в знание игрока",
        )


if __name__ == "__main__":
    unittest.main()
