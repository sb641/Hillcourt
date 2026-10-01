"""Весть ответа племени: И-3 и различение accepted/unable/refused."""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy.tribe import tribe_answer
from hillcourt.news.tribe import make_tribe_answer_report
from hillcourt.news.views import build_player_view
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_native_village.yml"


def _world():
    world = load_scenario(SCENARIO, seed=1729)
    tribe = world.tribes["tribe_village"]
    tribe.stance = "allied"
    tribe.muster_kits = 8.0
    world.get_stock(world.settlements[tribe.settlement_id].stores_stock_id).amounts["war_kit"] = 4.0
    return world, tribe


class TestTribeAnswerReport(unittest.TestCase):
    """Ответ племени доступен игроку только через доставленную весть."""

    def _answer(self, status: str, world, tribe):
        if status == "refused":
            return tribe_answer(world, tribe.id, 1, 2, world.clock.date, policy="refuse")
        if status == "unable":
            settlement = world.settlements[tribe.settlement_id]
            world.get_stock(settlement.stores_stock_id).amounts["war_kit"] = 0.0
        return tribe_answer(world, tribe.id, 1, 2, world.clock.date)

    def test_answer_is_delayed_with_honest_observer(self) -> None:
        world, tribe = _world()
        answer = self._answer("accepted", world, tribe)
        report = make_tribe_answer_report(world, answer)
        self.assertEqual(report.observer_id, tribe.id)
        self.assertEqual(report.source, "messenger")
        self.assertEqual(report.delivery_date.to_day_index(), answer.call_date.to_day_index() + 15)
        self.assertEqual(report.facts["answer"], "accepted")

    def test_unable_and_refused_are_distinct_reports(self) -> None:
        unable_world, unable_tribe = _world()
        unable = self._answer("unable", unable_world, unable_tribe)
        unable_report = make_tribe_answer_report(unable_world, unable)
        refused_world, refused_tribe = _world()
        refused = self._answer("refused", refused_world, refused_tribe)
        refused_report = make_tribe_answer_report(refused_world, refused)
        self.assertNotEqual(unable_report.facts["answer"], refused_report.facts["answer"])
        self.assertNotEqual(unable_report.content, refused_report.content)
        self.assertNotEqual(unable_report.facts["reason"], refused_report.facts["reason"])

    def test_undelivered_answer_is_not_player_knowledge(self) -> None:
        world, tribe = _world()
        report = make_tribe_answer_report(world, self._answer("accepted", world, tribe))
        self.assertNotIn(report.id, {entry.id for entry in build_player_view(world, world.clock.date).entries})
        delivered = {entry.id for entry in build_player_view(world, report.delivery_date).entries}
        self.assertIn(report.id, delivered)

    def test_same_seed_is_deterministic_and_delta_zero(self) -> None:
        first_world, first_tribe = _world()
        first_before = first_world.total_matter()
        first_answer = self._answer("accepted", first_world, first_tribe)
        first = make_tribe_answer_report(first_world, first_answer)
        second_world, second_tribe = _world()
        second_before = second_world.total_matter()
        second_answer = self._answer("accepted", second_world, second_tribe)
        second = make_tribe_answer_report(second_world, second_answer)
        self.assertEqual(first.content, second.content)
        self.assertEqual(first.facts, second.facts)
        self.assertEqual(first_world.total_matter(), first_before)
        self.assertEqual(second_world.total_matter(), second_before)


if __name__ == "__main__":
    unittest.main()
