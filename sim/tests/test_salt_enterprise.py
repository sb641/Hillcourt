"""Солеварное предприятие: выварка в склад, выплаты и детерминизм."""

from __future__ import annotations

import unittest

from hillcourt.engine.tick import run_month

try:
    from .salt_test_support import clone_salt_world, load_salt_template
except ImportError:
    from salt_test_support import clone_salt_world, load_salt_template


def _stand(template) -> object:
    world = clone_salt_world(template)
    settlement = world.settlements["salt_village"]
    world.get_stock(settlement.stores_stock_id).amounts["grain"] = 200.0
    for hid in settlement.household_ids:
        world.get_stock(f"household:{hid}").amounts["peat"] = 6.0
    world.ledger.capture_initial(world.total_matter())
    return world


class TestSaltEnterprise(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.template = load_salt_template()

    def test_boil_salt_deposits_in_enterprise_stock(self) -> None:
        world = _stand(self.template)
        settlement = world.settlements["salt_village"]
        enterprise = world.get_stock(settlement.stores_stock_id)
        initial_enterprise = dict(enterprise.amounts)
        run_month(world)
        run_month(world)
        entries = [
            e for e in world.ledger.entries
            if e.reason == "boil_salt" and e.good == "salt"
        ]
        self.assertTrue(entries)
        self.assertTrue(all(e.dst_id == enterprise.id for e in entries))
        self.assertGreater(enterprise.amounts.get("salt", 0.0), 0.0)
        paid = [e for e in world.ledger.entries if e.reason == "saltworks_pay"]
        self.assertTrue(paid)
        self.assertTrue(all(e.src_id == enterprise.id for e in paid))
        worker_stock_ids = {
            world.get_stock(f"household:{hid}").id
            for hid in settlement.household_ids
            if world.households[hid].left_at is None
        }
        paid_by_recipient = {
            stock_id: sum(e.amount for e in paid if e.dst_id == stock_id)
            for stock_id in worker_stock_ids
        }
        self.assertEqual(set(paid_by_recipient), worker_stock_ids)
        self.assertTrue(all(amount > 0.0 for amount in paid_by_recipient.values()))
        paid_by_good = {
            good: sum(e.amount for e in paid if e.good == good)
            for good in ("grain", "salt")
        }
        self.assertTrue(all(amount > 0.0 for amount in paid_by_good.values()))
        produced_salt = sum(e.amount for e in entries)
        available = {
            "grain": initial_enterprise.get("grain", 0.0),
            "salt": initial_enterprise.get("salt", 0.0) + produced_salt,
        }
        for good, amount in paid_by_good.items():
            self.assertLessEqual(amount, available[good] + 1e-9)

    def test_saltworks_pay_is_drawn_from_enterprise_stock(self) -> None:
        world = _stand(self.template)
        settlement = world.settlements["salt_village"]
        enterprise = world.get_stock(settlement.stores_stock_id)
        run_month(world)
        run_month(world)
        run_month(world)
        paid = [
            e for e in world.ledger.entries if e.reason == "saltworks_pay"
        ]
        self.assertTrue(paid)
        self.assertTrue(all(e.src_id == enterprise.id for e in paid))
        self.assertGreater(sum(e.amount for e in paid if e.good == "grain"), 0.0)
        self.assertGreater(sum(e.amount for e in paid if e.good == "salt"), 0.0)

    def test_salt_chain_preserves_matter_and_is_deterministic(self) -> None:
        first = _stand(self.template)
        second = _stand(self.template)
        for _ in range(3):
            run_month(first)
            run_month(second)
        self.assertAlmostEqual(first.ledger.delta(first.total_matter()), 0.0, places=6)
        self.assertEqual(first.state_hash(), second.state_hash())


if __name__ == "__main__":
    unittest.main()
