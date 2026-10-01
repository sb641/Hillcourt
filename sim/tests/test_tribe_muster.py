"""Ответ племени на вызов (ADR 0089 п.2, зона Economist).

Зонды:
  * может выставить (люди+комплекты+корм) → `accepted`, ровно требуемое число голов;
  * не хватает людей/комплектов/корма → `unable` (не отказ), причина видна;
  * политическое решение (`policy="refuse"`) → `refused` — отдельный факт;
  * `unable` ≠ `refused` юридически (разные статусы и причины);
  * детерминизм: два одинаковых вызова — одинаковый ответ; дельта 0;
  * дедлайн = дата вызова + срок службы (2/3), `independent` — `unable`.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy.tribe import (
    KITS_PER_PERSON,
    TribeAnswer,
    tribe_answer,
    tribe_muster_capacity,
)
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_native_village.yml"
EPSILON = 1e-9


def _world(stance: str = "allied", kits: float = 4.0):
    world = load_scenario(SCENARIO, seed=1729)
    tribe = world.tribes["tribe_village"]
    tribe.stance = stance
    tribe.tribute_grain = 0.6
    tribe.muster_kits = 8.0
    if kits > 0.0:
        settlement = world.settlements[tribe.settlement_id]
        world.get_stock(settlement.stores_stock_id).amounts["war_kit"] = kits
    return world, tribe


class TestTribeAnswer(unittest.TestCase):
    """`accepted` / `unable` / `refused` — по измеренным числам, без бросков."""

    def test_can_muster_is_accepted(self) -> None:
        world, tribe = _world("allied", kits=4.0)
        capacity = tribe_muster_capacity(world, tribe, 3)
        self.assertGreaterEqual(capacity["capacity"], 3, "Племя не может выставить 3")
        answer = tribe_answer(world, tribe.id, 3, 3, world.clock.date)
        self.assertIsInstance(answer, TribeAnswer)
        self.assertEqual(answer.status, "accepted")
        self.assertEqual(answer.reason, "ok")
        self.assertEqual(answer.persons_available, 3)

    def test_missing_kits_is_unable_not_refusal(self) -> None:
        world, tribe = _world("allied", kits=0.0)
        answer = tribe_answer(world, tribe.id, 2, 2, world.clock.date)
        self.assertEqual(answer.status, "unable")
        self.assertEqual(answer.reason, "no_kits")
        self.assertNotEqual(answer.status, "refused")

    def test_missing_food_is_unable(self) -> None:
        world, tribe = _world("allied", kits=4.0)
        for h in world.households.values():
            world.get_stock(h.stock_id).amounts["grain"] = 0.0
        for st in world.settlements.values():
            world.get_stock(st.stores_stock_id).amounts["grain"] = 0.0
        answer = tribe_answer(world, tribe.id, 1, 3, world.clock.date)
        self.assertEqual(answer.status, "unable")
        self.assertEqual(answer.reason, "no_food")
        self.assertLess(answer.food_available, answer.food_needed)

    def test_policy_refusal_is_separate_fact(self) -> None:
        world, tribe = _world("allied", kits=4.0)
        answer = tribe_answer(
            world, tribe.id, 1, 2, world.clock.date, policy="refuse"
        )
        self.assertEqual(answer.status, "refused")
        self.assertEqual(answer.reason, "tribe_decision")
        self.assertTrue(answer.is_refusal)
        # Тот же вызов без политического решения — accepted: отказ не «повесил» племя.
        self.assertEqual(
            tribe_answer(world, tribe.id, 1, 2, world.clock.date).status, "accepted"
        )

    def test_independent_is_unable(self) -> None:
        world, tribe = _world("independent", kits=4.0)
        answer = tribe_answer(world, tribe.id, 1, 2, world.clock.date)
        self.assertEqual(answer.status, "unable")
        self.assertEqual(answer.reason, "independent")

    def test_due_date_is_call_plus_period(self) -> None:
        world, tribe = _world("allied", kits=4.0)
        call = world.clock.date
        for period in (2, 3):
            answer = tribe_answer(world, tribe.id, 1, period, call)
            self.assertEqual(answer.period_months, period)
            self.assertEqual(
                (answer.due_date.year, answer.due_date.month),
                ((call.year * 12 + call.month - 1 + period) // 12,
                 (call.year * 12 + call.month - 1 + period) % 12 + 1),
            )

    def test_deterministic_and_delta_zero(self) -> None:
        world, tribe = _world("allied", kits=4.0)
        world.ledger.capture_initial(world.total_matter())
        first = tribe_answer(world, tribe.id, 2, 3, world.clock.date)
        second = tribe_answer(world, tribe.id, 2, 3, world.clock.date)
        self.assertEqual(first, second, "Ответ племени разошёлся на том же входе")
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_kits_per_person_measured_ratio(self) -> None:
        self.assertAlmostEqual(KITS_PER_PERSON, 0.5, places=6)
        world, tribe = _world("allied", kits=1.0)
        capacity = tribe_muster_capacity(world, tribe, 2)
        self.assertEqual(capacity["by_kits"], 2, "Один комплект не кормит двух голов")


class TestPolicyFailClosed(unittest.TestCase):
    """Политика племени — закрытый список: неизвестное значение не согласие."""

    def test_unknown_policy_raises_not_accept(self) -> None:
        world, tribe = _world("allied", kits=4.0)
        for bad in ("maybe", "REFUSE", "", "yes", None):
            with self.assertRaises(ValueError, msg=f"policy={bad!r} прошёл как согласие"):
                tribe_answer(world, tribe.id, 1, 2, world.clock.date, policy=bad)

    def test_known_policies_work(self) -> None:
        world, tribe = _world("allied", kits=4.0)
        self.assertEqual(
            tribe_answer(world, tribe.id, 1, 2, world.clock.date, policy="accept").status,
            "accepted",
        )
        self.assertEqual(
            tribe_answer(world, tribe.id, 1, 2, world.clock.date, policy="refuse").status,
            "refused",
        )
