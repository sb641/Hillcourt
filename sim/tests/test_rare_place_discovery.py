"""Находка редкого места разведкой и её доставка игроку."""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy.needs import monthly_food_need
from hillcourt.engine.path import known_tiles
from hillcourt.engine.scouting import (
    observe_scout_packs,
    settle_reported_discoveries,
)
from hillcourt.engine.tick import phase_consume, phase_inform, run_month
from hillcourt.news.scouting import report_scout_observations
from hillcourt.ontology import Pack, Stock
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_barony_100.yml"
DISCOVERY_TILE = "t_15_65"


class TestRarePlaceDiscovery(unittest.TestCase):
    """Редкое место появляется только после доставленного факта разведки."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO, seed=1729)
        self.person_id = "hh_hill_geneat_001_p1"
        self.world.persons[self.person_id].location_tile_id = DISCOVERY_TILE

    def _scout(self) -> Pack:
        pack = Pack(
            id="scout_discovery_test",
            kind="party",
            origin_tile_id=DISCOVERY_TILE,
            destination_tile_id=DISCOVERY_TILE,
            route=[DISCOVERY_TILE],
            member_ids=[self.person_id],
            cargo=Stock(
                id="pack:scout_discovery_test",
                owner_kind="pack",
                owner_id="scout_discovery_test",
            ),
            departed_date=self.world.clock.date,
            eta_date=self.world.clock.date.advance(
                self.world.clock.months_per_year
            ),
            purpose="scout",
            profile_id="hunters",
        )
        self.world.packs[pack.id] = pack
        return pack

    def test_rare_site_is_not_a_settlement_during_a_tick_without_observation(self) -> None:
        before = (
            set(self.world.settlements),
            set(self.world.households),
            self.world.tiles[DISCOVERY_TILE].settlement_id,
        )
        run_month(self.world)
        self.assertEqual(
            before,
            (
                set(self.world.settlements),
                set(self.world.households),
                self.world.tiles[DISCOVERY_TILE].settlement_id,
            ),
        )

    def test_observation_without_delivery_is_not_a_settlement(self) -> None:
        self._scout()
        events = observe_scout_packs(self.world, self.world.clock.date)
        event = next(event for event in events if event["tile_id"] == DISCOVERY_TILE)
        self.assertTrue(event["fact"])
        self.assertEqual(event["discovery"], "hermitage")
        self.world.month_events = events
        reports = report_scout_observations(self.world)
        report = next(report for report in reports if report.subject_id == DISCOVERY_TILE)
        report.delivery_date = self.world.clock.date.advance(
            self.world.clock.months_per_year
        )
        settle_reported_discoveries(self.world)
        self.assertNotIn(DISCOVERY_TILE, known_tiles(self.world))
        self.assertIsNone(self.world.tiles[DISCOVERY_TILE].settlement_id)
        self.assertNotIn("discovered_settlement_15_65", self.world.settlements)

    def test_delivered_discovery_creates_one_household_with_calculated_stock(self) -> None:
        self._scout()
        events = observe_scout_packs(self.world, self.world.clock.date)
        self.world.month_events = events
        report_scout_observations(self.world)
        settle_reported_discoveries(self.world)

        settlement_id = "discovered_settlement_15_65"
        household_id = "discovered_household_15_65"
        settlement = self.world.settlements[settlement_id]
        household = self.world.households[household_id]
        self.assertEqual(settlement.household_ids, [household_id])
        self.assertEqual(self.world.tiles[DISCOVERY_TILE].settlement_id, settlement_id)
        self.assertEqual(settlement.kind, "farmstead")
        stock = self.world.get_stock(household.stock_id)
        self.assertAlmostEqual(
            stock.amounts["grain"], monthly_food_need(self.world, household)
        )
        self.assertAlmostEqual(stock.amounts["axe"], 2.0)
        self.assertEqual(set(stock.amounts), {"grain", "axe"})
        self.assertEqual(self.world.get_stock(settlement.stores_stock_id).amounts, {})
        self.assertAlmostEqual(
            self.world.ledger.delta(self.world.total_matter()), 0.0, places=9
        )

        phase_consume(self.world)
        self.assertAlmostEqual(stock.amounts.get("grain", 0.0), 0.0)
        self.assertEqual(household.hunger_days, 0)

    def test_phase_inform_keeps_discovery_after_report_delivery(self) -> None:
        self._scout()
        self.world.month_events = observe_scout_packs(
            self.world, self.world.clock.date
        )
        phase_inform(self.world)
        self.assertIn("discovered_settlement_15_65", self.world.settlements)
        self.assertIn("discovered_household_15_65", self.world.households)

    def test_discovery_replay_is_deterministic(self) -> None:
        worlds = [load_scenario(SCENARIO, seed=1729), load_scenario(SCENARIO, seed=1729)]
        for world in worlds:
            world.persons[self.person_id].location_tile_id = DISCOVERY_TILE
            pack = Pack(
                id="scout_discovery_replay",
                kind="party",
                origin_tile_id=DISCOVERY_TILE,
                destination_tile_id=DISCOVERY_TILE,
                route=[DISCOVERY_TILE],
                member_ids=[self.person_id],
                cargo=Stock(
                    id="pack:scout_discovery_replay",
                    owner_kind="pack",
                    owner_id="scout_discovery_replay",
                ),
                departed_date=world.clock.date,
                eta_date=world.clock.date.advance(world.clock.months_per_year),
                purpose="scout",
                profile_id="hunters",
            )
            world.packs[pack.id] = pack
            world.month_events = observe_scout_packs(world, world.clock.date)
            report_scout_observations(world)
            settle_reported_discoveries(world)
        self.assertEqual(worlds[0].state_hash(), worlds[1].state_hash())


if __name__ == "__main__":
    unittest.main()
