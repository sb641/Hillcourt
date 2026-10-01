"""Весть об охоте: приближённое число, чистые факты, срок и роль.

Тесты-обвинители к `news/hunting.py`: шумный канал `adjacent_daily` не имеет
права выдать точное `amount`, внутренние id контейнеров не попадают в факты
даже при подсунутой id, а задержка месяц и «мелкая — двору, крупная — барону»
не сломаны.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from hillcourt.engine.tick import phase_labor
from hillcourt.news.hunting import HUNT_CHANNEL, report_hunt
from hillcourt.ontology import Right
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SAMPLES = 12
ROUND_STEP = 0.05
PLANTED_STOCK_ID = "stock_planted_secret_chest"
PLANTED_HOUSEHOLD_ID = "hh_planted_secret"


class TestHuntReportNews(unittest.TestCase):
    """Канал врёт, факты чисты, срок и назначение по роли сохранены."""

    def _world(self, household_id: str, seed: int):
        world = load_scenario(SCENARIO, seed=seed)
        household = world.households[household_id]
        world.households = {household.id: household}
        return world, household

    def _hunt_world(self, household_id: str, good_id: str, seed: int, *, grant: bool):
        world, household = self._world(household_id, seed)
        tile = world.tiles["t_00_00"]
        if grant:
            world.rights["right_test_news"] = Right(
                id="right_test_news",
                holder_household_id=household.id,
                tile_id=tile.id,
                kind="common",
                granted_date=world.clock.date,
                rent_share=0.0,
            )
        household.current_tile_id = tile.id
        recipe = world.catalogs.recipes[f"take_game_{good_id}"]
        world.ledger.capture_initial(world.total_matter())
        world.ledger.external_in(
            world.get_stock(tile.standing_stock_id),
            good_id,
            recipe.draws_standing[good_id] * 4.0,
            "test_hunt_news_seed",
            f"game_{good_id}",
            world.clock.date,
        )
        household.labor_days = recipe.labor_days
        household.main_action = "take_game"
        household.minor_action = "idle_repair"
        return world, household, tile, recipe

    def _hunt_report(self, world):
        return next(r for r in world.reports if r.facts.get("event") == "hunt")

    def test_noisy_channel_reports_approximate_amount(self) -> None:
        world, _, tile, _ = self._hunt_world("hh_01", "rabbit", 1729, grant=True)
        true_amount = 0.15
        drift = HUNT_CHANNEL.noise
        reported: list[float] = []
        for index in range(SAMPLES):
            report = report_hunt(
                world,
                "hh_01",
                tile.id,
                "rabbit",
                true_amount,
                PLANTED_STOCK_ID,
                world.clock.date,
            )
            approx = float(report.facts["amount_approx"])
            self.assertNotIn("amount", report.facts)
            self.assertLessEqual(approx, true_amount * (1.0 + drift) + ROUND_STEP + 1e-9)
            self.assertGreaterEqual(approx, true_amount * (1.0 - drift) - ROUND_STEP - 1e-9)
            if approx != true_amount:
                self.assertTrue(report.distorted)
            reported.append(approx)
        differs = sum(1 for value in reported if value != true_amount)
        self.assertGreaterEqual(differs, SAMPLES - 3, f"канал выдал точное число: {reported}")
        self.assertAlmostEqual(HUNT_CHANNEL.noise, 0.3, places=9)

    def test_planted_internal_id_never_reaches_facts(self) -> None:
        world, household, tile, _ = self._hunt_world("hh_01", "rabbit", 1729, grant=True)
        report = report_hunt(
            world,
            PLANTED_HOUSEHOLD_ID,
            tile.id,
            "rabbit",
            0.15,
            PLANTED_STOCK_ID,
            world.clock.date,
            recipe_id="take_game_rabbit",
        )
        facts = report.facts
        self.assertEqual(facts["to"], "unknown")
        blob = json.dumps(facts, ensure_ascii=False, default=str) + report.content
        self.assertNotIn(PLANTED_STOCK_ID, blob)
        self.assertNotIn(PLANTED_HOUSEHOLD_ID, blob)
        for banned in ("destination_stock_id", "household", "regime", "stock_id"):
            self.assertNotIn(banned, {str(key) for key in facts})
            self.assertNotIn(banned, blob)
        self.assertEqual(facts["tile"], tile.id)
        self.assertEqual(facts["good"], "rabbit")
        real = report_hunt(
            world,
            household.id,
            tile.id,
            "rabbit",
            0.15,
            household.stock_id,
            world.clock.date,
        )
        self.assertEqual(real.facts["to"], "household")
        self.assertNotIn(household.stock_id, json.dumps(real.facts, default=str))
        manor_stock_id = world.manors[world.player_manor_id].stock_id
        to_baron = report_hunt(
            world,
            household.id,
            tile.id,
            "rabbit",
            0.15,
            manor_stock_id,
            world.clock.date,
        )
        self.assertEqual(to_baron.facts["to"], "baron")
        self.assertNotIn(manor_stock_id, json.dumps(to_baron.facts, default=str))
        self.assertNotIn(manor_stock_id, to_baron.content)

    def test_small_game_goes_to_court_large_game_to_baron(self) -> None:
        for good_id, holder in (("rabbit", "household"), ("deer", "baron")):
            with self.subTest(good=good_id):
                household_id = "hh_01" if holder == "household" else "hh_court"
                world, household, _, recipe = self._hunt_world(
                    household_id, good_id, 1729, grant=holder == "household"
                )
                stocks = [household.stock_id]
                manor = world.manors.get(world.player_manor_id)
                if manor is not None and manor.stock_id not in stocks:
                    stocks.append(manor.stock_id)
                before = sum(
                    world.get_stock(stock_id).amounts.get(good_id, 0.0)
                    for stock_id in stocks
                )
                phase_labor(world)
                true_amount = sum(
                    world.get_stock(stock_id).amounts.get(good_id, 0.0)
                    for stock_id in stocks
                ) - before
                self.assertAlmostEqual(true_amount, recipe.outputs[good_id], places=6)
                report = self._hunt_report(world)
                self.assertEqual(report.facts["to"], holder)
                self.assertEqual(report.source, "adjacent_daily")
                self.assertGreater(report.delivery_date, report.event_date)
                self.assertAlmostEqual(
                    (report.delivery_date.year * world.clock.months_per_year
                     + report.delivery_date.month)
                    - (report.event_date.year * world.clock.months_per_year
                       + report.event_date.month),
                    1,
                    places=6,
                )
                approx = float(report.facts["amount_approx"])
                self.assertLessEqual(approx, true_amount * 1.3 + ROUND_STEP + 1e-9)
                self.assertGreaterEqual(approx, true_amount * 0.7 - ROUND_STEP - 1e-9)

    def test_news_text_is_deterministic_for_one_seed(self) -> None:
        first, _, _, _ = self._hunt_world("hh_01", "rabbit", 4242, grant=True)
        second, _, _, _ = self._hunt_world("hh_01", "rabbit", 4242, grant=True)
        phase_labor(first)
        phase_labor(second)
        left = self._hunt_report(first)
        right = self._hunt_report(second)
        self.assertEqual(left.content, right.content)
        self.assertEqual(left.facts, right.facts)
        self.assertEqual(left.distorted, right.distorted)
        self.assertEqual(first.state_hash(), second.state_hash())


if __name__ == "__main__":
    unittest.main()
