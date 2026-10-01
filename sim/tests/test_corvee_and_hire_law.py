"""Обвинители барщины и живого найма.

Барщина держится за землёй (ADR 0154): безземельный свободный `free_landless`
сезонной барщины не имеет — 0 трудодней в любом месяце, его нанимают. Сокольник,
виллан, коттер и невольник барщат по-прежнему (ADR 0154 п. 4). Выплаты за
`labor_duty` — 0 во всех сословиях (ADR 0131).
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy import decisions
from hillcourt.economy.decisions import allowed_action_ids
from hillcourt.economy.manor import manor_month
from hillcourt.engine.tick import phase_obligations
from hillcourt.legal.calendar import seasonal_labor_days
from hillcourt.legal.manor import hire_out_allowed
from hillcourt.legal.obligations import is_payable_duty
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
HILL = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
NATIVE = ROOT / "design" / "scenarios" / "v0_native_village.yml"


def _duty(world, household_id: str):
    return next(
        world.obligations[oid]
        for oid in world.households[household_id].obligation_ids
        if world.obligations[oid].kind == "labor_duty"
    )


class TestCorveeAndHireLaw(unittest.TestCase):
    def test_slave_has_corvee_and_works_nonzero_days(self) -> None:
        world = load_scenario(HILL, seed=1729)
        household = world.households["hh_court"]
        self.assertTrue(
            any(
                world.persons[pid].personal_status == "slave"
                for pid in household.member_ids
            )
        )
        obligation = _duty(world, household.id)
        manor_month(world, world.clock.date)
        phase_obligations(world)
        self.assertGreater(obligation.duty_days, 0.0)
        self.assertGreater(obligation.corvee_days, 0.0)
        self.assertGreater(world.stats.get("slave_labor_days", 0.0), 0.0)

    def test_landless_free_tenant_has_no_seasonal_corvee(self) -> None:
        """ADR 0154 п. 2: у безземельного свободного 0 трудодней в любом месяце."""
        world = load_scenario(HILL, seed=1729)
        household = world.households["hh_06"]
        self.assertEqual(household.legal_status_id, "free_landless")
        for month in range(1, 13):
            self.assertEqual(
                seasonal_labor_days(world, household, month), 0.0,
                f"Безземельный барщит в месяце {month}",
            )
        obligation = _duty(world, household.id)
        manor_month(world, world.clock.date)
        phase_obligations(world)
        self.assertEqual(obligation.basis, "duty")
        self.assertEqual(obligation.duty_days, 0.0)
        self.assertEqual(obligation.corvee_days, 0.0)
        self.assertAlmostEqual(obligation.paid_total, 0.0, places=6)

    def test_landholder_seasonal_corvee_is_unchanged(self) -> None:
        """ADR 0154 п. 4: сокольник, виллан, коттер и невольник барщат.

        Векторы трудодней помесячно — регрессия: правка не должна была сдвинуть
        ни один месяц ни у одного землевладельца.
        """
        world = load_scenario(HILL, seed=1729)
        expected = {
            "hh_01": [0.0, 0.0, 0.0, 0.0, 0.0, 4.0, 4.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "hh_02": [8.0, 8.0, 12.0, 12.0, 12.0, 8.0, 8.0, 12.0, 12.0, 8.0, 8.0, 8.0],
            "hh_03": [4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 12.0, 12.0, 4.0, 4.0, 4.0],
            "hh_court": [40.0] * 12,
        }
        for hid, vector in expected.items():
            household = world.households[hid]
            for index, days in enumerate(vector, start=1):
                self.assertAlmostEqual(
                    seasonal_labor_days(world, household, index), days, places=6,
                    msg=f"{hid} в месяце {index}",
                )
        villein = world.households["hh_02"]
        obligation = _duty(world, villein.id)
        manor_month(world, world.clock.date)
        phase_obligations(world)
        self.assertGreater(obligation.duty_days, 0.0)
        self.assertAlmostEqual(obligation.corvee_days, obligation.duty_days, places=6)
        self.assertGreater(world.stats.get("corvee_days", 0.0), 0.0)

    def test_slave_in_landless_household_still_owes_corvee(self) -> None:
        """ADR 0154 п. 4: невольник земли не имеет, но он не свободный и не наёмный."""
        world = load_scenario(HILL, seed=1729)
        household = world.households["hh_06"]
        self.assertEqual(seasonal_labor_days(world, household, 7), 0.0)
        world.persons[household.member_ids[0]].personal_status = "slave"
        self.assertAlmostEqual(
            seasonal_labor_days(world, household, 7), 20.0, places=6,
            msg="Невольника сняли с домена из-за отсутствия у него земли",
        )
        self.assertTrue(
            any(
                world.persons[pid].personal_status == "slave"
                for pid in household.member_ids
            )
        )

    def test_corvee_payment_is_zero_for_all_duties(self) -> None:
        world = load_scenario(HILL, seed=1729)
        manor_month(world, world.clock.date)
        phase_obligations(world)
        payments = [
            entry
            for entry in world.ledger.entries
            if entry.reason in {"corvee", "labor_duty", "duty_payment", "corvee_commuted"}
        ]
        duties = [
            obligation
            for obligation in world.obligations.values()
            if obligation.kind == "labor_duty"
        ]
        self.assertEqual(payments, [])
        self.assertTrue(duties)
        self.assertTrue(all(obligation.due_good is None for obligation in duties))
        self.assertTrue(all(not is_payable_duty(obligation) for obligation in duties))

    def test_own_duty_cannot_be_bought_out_even_with_full_grain(self) -> None:
        """ADR 0145 п. 1 / ADR 0131: свой двор от барщины не откупается никогда."""
        world = load_scenario(HILL, seed=1729)
        household = world.households["hh_02"]
        stock = world.get_stock(household.stock_id)
        stock.amounts["grain"] = 500.0
        grain_before = stock.amounts["grain"]
        obligation = _duty(world, household.id)
        manor_month(world, world.clock.date)
        world.ledger.capture_initial(world.total_matter())
        phase_obligations(world)
        self.assertFalse(is_payable_duty(obligation), "У своего двора появилась цена")
        self.assertGreater(obligation.duty_days, 0.0)
        self.assertGreater(
            obligation.corvee_days, 0.0, "Платёж отменил барщину своих дворов"
        )
        self.assertAlmostEqual(obligation.paid_total, 0.0, places=6)
        self.assertAlmostEqual(stock.amounts["grain"], grain_before, places=6)
        self.assertEqual(
            [e for e in world.ledger.entries if e.reason == "corvee_commuted"], []
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_status_without_duty_does_not_receive_one(self) -> None:
        world = load_scenario(HILL, seed=1729)
        self.assertFalse(
            any(
                world.obligations[oid].kind == "labor_duty"
                for oid in world.households["hh_retinue"].obligation_ids
            )
        )

    def test_hire_out_is_gated_by_legal_check_and_not_landholding(self) -> None:
        hired_world = load_scenario(HILL, seed=1729)
        free = hired_world.households["hh_06"]
        self.assertTrue(hire_out_allowed(hired_world, free))
        self.assertIn("hire_out", allowed_action_ids(hired_world, free))
        self.assertEqual(free.land_relation, "landless")
        self.assertFalse(
            any(right.holder_household_id == free.id for right in hired_world.rights.values())
        )
        outside_world = load_scenario(NATIVE, seed=1729)
        outside = outside_world.households["hh_tribe_01"]
        self.assertFalse(hire_out_allowed(outside_world, outside))
        self.assertNotIn("hire_out", allowed_action_ids(outside_world, outside))
        source = Path(decisions.__file__).read_text(encoding="utf-8")
        self.assertIn("hire_out_allowed(world, household)", source)


if __name__ == "__main__":
    unittest.main()
