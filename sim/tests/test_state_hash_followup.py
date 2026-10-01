"""Поля повинности и Pack обязаны различать состояния в `state_hash`."""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.ontology import Pack, SimDate, Stock
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_two_settlements.yml"


class TestStateHashFollowup(unittest.TestCase):
    """Хеш включает статус службы и связь Pack с повинностью."""

    def _world_with_changed_fields(self) -> tuple:
        world = load_scenario(SCENARIO, seed=42)
        obligation = next(iter(world.obligations.values()))
        obligation.call_status = "returned"
        world.packs["hash_pack"] = Pack(
            id="hash_pack",
            kind="party",
            origin_tile_id="t_01_01",
            destination_tile_id="t_02_01",
            route=["t_01_01", "t_02_01"],
            member_ids=[],
            cargo=Stock(id="pack:hash_pack", owner_kind="pack", owner_id="hash_pack"),
            departed_date=SimDate(1, 1),
            eta_date=SimDate(1, 1),
            obligation_id="obligation_a",
        )
        return world, obligation

    def test_call_status_and_pack_obligation_change_hash(self) -> None:
        first, obligation = self._world_with_changed_fields()
        before = first.state_hash()
        obligation.call_status = "overdue"
        after_status = first.state_hash()
        first.packs["hash_pack"].obligation_id = "obligation_b"
        after_pack = first.state_hash()
        self.assertNotEqual(before, after_status)
        self.assertNotEqual(after_status, after_pack)

    def test_same_fields_replay_same_hash(self) -> None:
        first, _ = self._world_with_changed_fields()
        second, _ = self._world_with_changed_fields()
        self.assertEqual(first.state_hash(), second.state_hash())


if __name__ == "__main__":
    unittest.main()
