"""Факты вести об охоте: дрейф вместо точного числа, чистота от внутренних id.

Обвинитель к `news/hunting.py`: канал охоты шумной (`adjacent_daily`, дрейф
`HUNT_CHANNEL.noise`), поэтому точное `amount` в фактах быть не должно; внутренние
id контейнеров и режим клетки не попадают в факты даже когда вызывающий подсовывает
их в аргументы; задержка месяц и «мелкая дича — двору, крупная — барону» не сломаны.
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
SEED = 8675309
SAMPLES = 16
STEP = 0.05
DRIFT = HUNT_CHANNEL.noise
TRUE_AMOUNT = 0.15
TILE_ID = "t_00_00"
PLANTED_STOCK_ID = "stock_planted_hunt_chest"
PLANTED_HOUSEHOLD_ID = "hh_planted_hunt"
PLANTED_REGIME_ID = "reserved_wood_planted"
BANNED_KEYS = (
    "amount",
    "destination_stock_id",
    "source_stock_id",
    "stock_id",
    "regime",
    "regime_id",
    "land_regime_id",
    "household_id",
)


class TestHuntReportFacts(unittest.TestCase):
    """Точного числа нет, внутренних id нет, срок и получатель на месте."""

    def _hunt_world(self, household_id: str, good_id: str, *, grant: bool):
        world = load_scenario(SCENARIO, seed=SEED)
        household = world.households[household_id]
        world.households = {household.id: household}
        tile = world.tiles[TILE_ID]
        if grant:
            world.rights["right_test_hunt_facts"] = Right(
                id="right_test_hunt_facts",
                holder_household_id=household.id,
                tile_id=tile.id,
                kind="common",
                granted_date=world.clock.date,
                rent_share=0.0,
            )
        recipe = world.catalogs.recipes[f"take_game_{good_id}"]
        world.ledger.capture_initial(world.total_matter())
        world.ledger.external_in(
            world.get_stock(tile.standing_stock_id),
            good_id,
            recipe.draws_standing[good_id] * 4.0,
            "test_hunt_report_facts_seed",
            f"game_{good_id}",
            world.clock.date,
        )
        household.current_tile_id = tile.id
        household.labor_days = recipe.labor_days
        household.main_action = "take_game"
        household.minor_action = "idle_repair"
        return world, household, tile, recipe

    def _hunt_report(self, world):
        return next(r for r in world.reports if r.facts.get("event") == "hunt")

    def test_facts_have_drifted_amount_not_the_true_one(self) -> None:
        world, household, tile, _ = self._hunt_world("hh_01", "rabbit", grant=True)
        seen: list[float] = []
        for _ in range(SAMPLES):
            report = report_hunt(
                world,
                household.id,
                tile.id,
                "rabbit",
                TRUE_AMOUNT,
                household.stock_id,
                world.clock.date,
            )
            self.assertNotIn("amount", report.facts)
            approx = float(report.facts["amount_approx"])
            self.assertLessEqual(approx, TRUE_AMOUNT * (1.0 + DRIFT) + STEP + 1e-9)
            self.assertGreaterEqual(approx, TRUE_AMOUNT * (1.0 - DRIFT) - STEP - 1e-9)
            self.assertEqual(report.distorted, approx != TRUE_AMOUNT)
            seen.append(approx)
        differs = sum(1 for value in seen if value != TRUE_AMOUNT)
        self.assertGreaterEqual(
            differs,
            SAMPLES - 2,
            f"шумный канал выдал точное число: {seen}",
        )
        self.assertAlmostEqual(DRIFT, 0.3, places=9)
        self.assertNotEqual(round(TRUE_AMOUNT * (1.0 + DRIFT), 1), TRUE_AMOUNT)

    def test_facts_carry_no_internal_id_or_regime_even_when_planted(self) -> None:
        world, household, tile, _ = self._hunt_world("hh_01", "rabbit", grant=True)
        report = report_hunt(
            world,
            PLANTED_HOUSEHOLD_ID,
            tile.id,
            "rabbit",
            TRUE_AMOUNT,
            PLANTED_STOCK_ID,
            world.clock.date,
            recipe_id="take_game_rabbit",
        )
        facts = report.facts
        blob = json.dumps(facts, ensure_ascii=False, default=str) + report.content
        self.assertEqual(facts["to"], "unknown")
        keys = {str(key) for key in facts}
        for banned in BANNED_KEYS:
            self.assertNotIn(banned, keys)
        for planted in (PLANTED_STOCK_ID, PLANTED_HOUSEHOLD_ID, PLANTED_REGIME_ID):
            self.assertNotIn(planted, blob)
        self.assertNotIn(tile.regime_id, blob)
        self.assertEqual(facts["tile"], TILE_ID)
        self.assertEqual(facts["good"], "rabbit")

        for destination, holder in (
            (household.stock_id, "household"),
            (world.manors[world.player_manor_id].stock_id, "baron"),
        ):
            with self.subTest(holder=holder):
                real = report_hunt(
                    world,
                    household.id,
                    tile.id,
                    "rabbit",
                    TRUE_AMOUNT,
                    destination,
                    world.clock.date,
                )
                self.assertEqual(real.facts["to"], holder)
                truth_free = json.dumps(real.facts, ensure_ascii=False, default=str)
                truth_free += real.content
                self.assertNotIn(destination, truth_free)
                for banned in BANNED_KEYS:
                    self.assertNotIn(banned, {str(k) for k in real.facts})

    def test_delay_month_and_holder_by_game_size_survive(self) -> None:
        for good_id, household_id, holder in (
            ("rabbit", "hh_01", "household"),
            ("deer", "hh_court", "baron"),
        ):
            with self.subTest(good=good_id):
                world, household, _, recipe = self._hunt_world(
                    household_id,
                    good_id,
                    grant=holder == "household",
                )
                phase_labor(world)
                report = self._hunt_report(world)
                self.assertEqual(report.source, "adjacent_daily")
                self.assertEqual(report.facts["to"], holder)
                self.assertGreater(report.delivery_date, report.event_date)
                months = world.clock.months_per_year
                gap = (report.delivery_date.year * months + report.delivery_date.month) - (
                    report.event_date.year * months + report.event_date.month
                )
                self.assertEqual(gap, 1)
                self.assertAlmostEqual(report.confidence, 0.7, places=9)
                self.assertAlmostEqual(report.noise, DRIFT, places=9)
                true_amount = recipe.outputs[good_id]
                approx = float(report.facts["amount_approx"])
                self.assertLessEqual(approx, true_amount * (1.0 + DRIFT) + STEP + 1e-9)
                self.assertGreaterEqual(approx, true_amount * (1.0 - DRIFT) - STEP - 1e-9)
                for banned in BANNED_KEYS:
                    self.assertNotIn(banned, {str(key) for key in report.facts})


if __name__ == "__main__":
    unittest.main()
