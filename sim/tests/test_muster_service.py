"""Служба 60–90 суток: срок, обратный Pack, статусы и подавление тэна."""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.manor import grant_thegn, revoke_thegn
from hillcourt.legal.service import (
    call_manor_muster,
    call_tribe_muster,
    complete_muster_return,
    create_muster_obligation,
    create_return_pack,
    mark_muster_overdue,
    record_muster_lost,
    refuse_muster,
    service_deadline,
    start_muster_service,
)
from hillcourt.ontology import SimDate
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
TWO = ROOT / "design" / "scenarios" / "v0_two_settlements.yml"
NATIVE = ROOT / "design" / "scenarios" / "v0_native_village.yml"


class TestMusterService(unittest.TestCase):
    """Проверки ADR 0066 и ADR 0082 в существующих сущностях."""

    def setUp(self) -> None:
        self.world = load_scenario(NATIVE, seed=1729)
        self.world.tribes["tribe_village"].stance = "allied"

    def test_period_is_two_or_three_months_and_deadline_starts_at_departure(self) -> None:
        """Срок хранится в повинности, дедлайн — от outbound Pack."""
        obligation = create_muster_obligation(self.world, "hh_tribe_01", 2, 2)
        self.assertEqual(obligation.period_months, 2)
        self.assertEqual(obligation.call_status, "pending")
        self.assertEqual(obligation.due_amount, 2.0)
        pack = start_muster_service(self.world, obligation, "t_06_01")
        self.assertIsNotNone(pack)
        self.assertEqual(service_deadline(self.world, obligation), SimDate(1, 3))
        three_months = create_muster_obligation(
            self.world, "hh_tribe_02", 1, 3
        )
        three_pack = start_muster_service(self.world, three_months, "t_06_01")
        self.assertIsNotNone(three_pack)
        self.assertEqual(three_months.period_months, 3)
        self.assertEqual(service_deadline(self.world, three_months), SimDate(1, 4))

    def test_return_in_time_sets_returned(self) -> None:
        """Outbound и return связаны с одной повинностью."""
        calls = call_tribe_muster(self.world, "tribe_village", "t_06_01", 2)
        obligation, outbound = calls[0]
        self.assertIsNotNone(outbound)
        self.assertEqual(outbound.obligation_id, obligation.id)
        outbound.status = "arrived"
        returned = create_return_pack(self.world, obligation)
        self.assertIsNotNone(returned)
        self.assertEqual(returned.obligation_id, obligation.id)
        returned.status = "arrived"
        complete_muster_return(self.world, obligation)
        self.assertEqual(obligation.call_status, "returned")

    def test_overdue_is_not_refused(self) -> None:
        """Просроченный возврат — overdue, не активный отказ."""
        calls = call_tribe_muster(self.world, "tribe_village", "t_06_01", 2)
        obligation, outbound = calls[0]
        self.assertIsNotNone(outbound)
        self.assertTrue(mark_muster_overdue(self.world, obligation, SimDate(1, 4)))
        self.assertEqual(obligation.call_status, "overdue")
        self.assertNotEqual(obligation.call_status, "refused")

    def test_lost_pack_is_unable_not_refused(self) -> None:
        """Гибель Pack не становится отказом племени."""
        calls = call_tribe_muster(self.world, "tribe_village", "t_06_01", 2)
        obligation, outbound = calls[0]
        self.assertIsNotNone(outbound)
        outbound.status = "lost"
        record_muster_lost(self.world, obligation, outbound.id)
        self.assertEqual(obligation.call_status, "unable")
        self.assertNotEqual(obligation.call_status, "refused")

    def test_silence_and_no_action_do_not_refuse(self) -> None:
        """Тишина и отсутствие решения не меняют pending на refused."""
        obligation = create_muster_obligation(self.world, "hh_tribe_01", 1, 2)
        self.assertEqual(obligation.call_status, "pending")
        self.assertNotIn(obligation.id, self.world.packs)

    def test_active_refusal_is_explicit(self) -> None:
        """Только действие решения племени ставит refused."""
        obligation = create_muster_obligation(self.world, "hh_tribe_01", 1, 2)
        refuse_muster(self.world, obligation)
        self.assertEqual(obligation.call_status, "refused")

    def test_no_people_means_unable_without_pack(self) -> None:
        """Нет фактически вышедших людей — нет Pack, статус unable."""
        obligation = create_muster_obligation(self.world, "hh_tribe_01", 1, 2)
        self.assertIsNone(start_muster_service(self.world, obligation, "t_06_01", []))
        self.assertEqual(obligation.call_status, "unable")
        self.assertNotIn(f"pack_{obligation.id}_out", self.world.packs)

    def test_manor_muster_and_revoke_pattern(self) -> None:
        """Тэн получает muster, а подавление использует revoke_thegn."""
        world = load_scenario(TWO, seed=1729)
        manor = grant_thegn(
            world,
            "hh_retinue_p1",
            ["t_03_01", "t_05_02"],
            ["hh_02", "hh_retinue"],
        )
        self.assertIsNotNone(manor)
        obligation, pack = call_manor_muster(world, manor.id, "t_06_01", 3)
        self.assertIsNotNone(pack)
        self.assertEqual(obligation.period_months, 3)
        self.assertEqual(pack.obligation_id, obligation.id)
        self.assertEqual(world.ledger.delta(world.total_matter()), 0.0)
        self.assertTrue(revoke_thegn(world, manor.id))
        holder = world.households["hh_retinue"]
        self.assertEqual(holder.legal_status_id, "free_landless")
        self.assertEqual(world.ledger.delta(world.total_matter()), 0.0)


if __name__ == "__main__":
    unittest.main()
