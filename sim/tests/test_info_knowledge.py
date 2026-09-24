"""Знание игрока: только Report, срок годности, молчание, приёмка голода."""

from __future__ import annotations

import unittest
from dataclasses import fields
from pathlib import Path

from hillcourt.engine.tick import run_month
from hillcourt.info.knowledge import (
    STALE_AFTER_MONTHS,
    KnowledgeEntry,
    absolute_month,
    build_player_knowledge,
)
from hillcourt.info.rumor import UNKNOWN_HAZARD_RISK, perceived_risk
from hillcourt.news.propagation import delivered_reports, make_report
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SALT_TILE = "t_08_05"


class TestInfoKnowledge(unittest.TestCase):
    """Игрок читает известие с датой, а не запас дальней деревни."""

    def test_knowledge_entry_has_no_truth_fields(self) -> None:
        names = {field.name for field in fields(KnowledgeEntry)}
        for forbidden in ("tile", "household", "stock", "amounts", "world"):
            self.assertNotIn(forbidden, names, f"KnowledgeEntry светит истиной: {forbidden}")

    def test_knowledge_equals_delivered_reports(self) -> None:
        world = load_scenario(SCENARIO)
        for _ in range(12):
            run_month(world)
        as_of = world.clock.date
        knowledge = build_player_knowledge(world, as_of)
        delivered = delivered_reports(world, as_of)
        self.assertEqual(len(knowledge.entries), len(delivered))

    def test_latest_is_last_arrived(self) -> None:
        world = load_scenario(SCENARIO)
        for _ in range(12):
            run_month(world)
        knowledge = build_player_knowledge(world)
        entry = knowledge.latest(SALT_TILE)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.about, SALT_TILE)
        same = [e for e in knowledge.entries if e.about == SALT_TILE]
        self.assertEqual(entry.arrived_month, max(e.arrived_month for e in same))
        self.assertIsInstance(knowledge.silences(), list)

    def test_staleness_after_term(self) -> None:
        world = load_scenario(SCENARIO)
        run_month(world)
        date = world.clock.date
        entry = build_player_knowledge(world, date).entries[0]
        late = date
        for _ in range(STALE_AFTER_MONTHS + 1):
            late = late.advance()
        stale_entry = build_player_knowledge(world, late).latest(entry.about)
        self.assertTrue(stale_entry.stale, "Старое известие не помечено устаревшим")

    def test_player_does_not_read_distant_stock(self) -> None:
        """Приёмка: деревня уже голодает, а знание игрока — старое «норм»."""
        world = load_scenario(SCENARIO)
        for _ in range(6):
            run_month(world)
        date = world.clock.date
        before = build_player_knowledge(world, date).latest(SALT_TILE)
        self.assertIsNotNone(before, "Нет известия о дальней деревне")
        old_salt = before.facts.get("salt_approx", 0.0)

        salt = world.settlements["salt_village"]
        world.get_stock(salt.stores_stock_id).amounts["salt"] = 0.0
        still = build_player_knowledge(world, date).latest(SALT_TILE)
        self.assertEqual(
            still.facts.get("salt_approx", 0.0),
            old_salt,
            "Знание игрока само прочитало истину деревни",
        )

        make_report(
            world,
            "messenger",
            "tile",
            SALT_TILE,
            "Гонец: у соли соли нет.",
            {"salt_approx": 0.0},
            date,
            0,
            0.5,
        )
        arrived = build_player_knowledge(world, date).latest(SALT_TILE)
        self.assertEqual(arrived.facts.get("salt_approx"), 0.0)
        self.assertEqual(arrived.arrived_month, absolute_month(date))
        self.assertFalse(arrived.stale)

    def test_adventurer_uses_rumor_not_truth(self) -> None:
        world = load_scenario(SCENARIO)
        wolves_tile = next(
            h.tile_id for h in world.hazards.values() if h.kind == "wolves"
        )
        blind = build_player_knowledge(world, world.clock.date)
        unknown = perceived_risk(
            blind, wolves_tile, 1, world.catalogs.hazard_rules
        )
        self.assertAlmostEqual(unknown, UNKNOWN_HAZARD_RISK)

        make_report(
            world,
            "adjacent_daily",
            "tile",
            wolves_tile,
            "В лесу волки.",
            {"hazard": {"kind": "wolves", "population_approx": 2.0}},
            world.clock.date,
            0,
            0.5,
        )
        informed = build_player_knowledge(world, world.clock.date)
        heard = perceived_risk(informed, wolves_tile, 1, world.catalogs.hazard_rules)
        self.assertGreater(heard, unknown, "Слух не поднял оценку риска")

        wolf = next(h for h in world.hazards.values() if h.kind == "wolves")
        wolf.population = 999.0
        self.assertEqual(
            perceived_risk(informed, wolves_tile, 1, world.catalogs.hazard_rules),
            heard,
            "Оценка риска прочитала истинную опасность",
        )


if __name__ == "__main__":
    unittest.main()
