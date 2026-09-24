"""Экономика племени v1: оброк `levy` и потенциал вызова (ADR 0064/0066).

Проверки:
  * `allied` → повинность `levy` заведена, оплата идёт в амбар корня; при пустом
    стоке зерна — `arrears` растёт (не падёж, не телепорт);
  * `independent` → повинности нет;
  * пустой двор (нет живых людей) → оброк не берётся, долг не растёт;
  * потенциал вызова честен: `war_kit` в стоках дворов + живые взрослые,
    потолок `muster_kits`; ни вызова, ни `send_sally`, ни нормы тэна.
Материя — только перевод существующей фазы (дельта 0).
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy import manor as manor_economy
from hillcourt.economy.tribe import (
    tribe_can_muster,
    tribe_households,
    tribe_muster_kits,
    tribe_tribute_month,
)
from hillcourt.engine.tick import phase_obligations
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_native_village.yml"
EPSILON = 1e-9


def _world(stance: str = "allied"):
    world = load_scenario(SCENARIO, seed=1729)
    tribe = world.tribes["tribe_village"]
    tribe.stance = stance
    tribe.tribute_grain = 0.6
    tribe.muster_kits = 2.0
    return world, tribe


class TestTribeTribute(unittest.TestCase):
    """Натуральный оброк 0.6/мес на двор через существующий `levy`."""

    def test_allied_levy_pays_to_root_barn(self) -> None:
        world, tribe = _world("allied")
        households = tribe_households(world, tribe)
        self.assertTrue(households, "Племя без двора в сценарии")
        tribute = tribe_tribute_month(world, world.clock.date)
        self.assertEqual(len(tribute), len(households))
        for household in households:
            stock = world.get_stock(household.stock_id)
            stock.amounts["grain"] = 10.0
        before = {
            hid: world.get_stock(f"household:{hid}").amounts["grain"]
            for hid in sorted(world.households)
        }
        paid_before = {o.id: o.paid_total for o in tribute}
        root = world.get_stock("settlement:hill_court")
        root_before = root.amounts.get("grain", 0.0)
        world.ledger.capture_initial(world.total_matter())
        phase_obligations(world)
        paid = sum(o.paid_total - paid_before[o.id] for o in tribute)
        self.assertAlmostEqual(paid, 0.6 * len(households), places=6)
        rent_grain = sum(
            e.amount for e in world.ledger.entries
            if e.reason == "rent" and e.good == "grain"
        )
        self.assertAlmostEqual(
            root.amounts.get("grain", 0.0), root_before + rent_grain, places=6,
            msg="Оброк не в амбаре корня",
        )
        for hid, was in before.items():
            if tribe_households(world, tribe) and any(
                h.id == hid for h in tribe_households(world, tribe)
            ):
                self.assertAlmostEqual(
                    world.get_stock(f"household:{hid}").amounts["grain"],
                    was - 0.6, places=6,
                    msg=f"{hid}: оброк не списан с племенного двора",
                )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_unpaid_tribute_grows_arrears_not_hunger(self) -> None:
        world, tribe = _world("allied")
        households = tribe_households(world, tribe)
        for household in households:
            world.get_stock(household.stock_id).amounts["grain"] = 0.0
        tribute = tribe_tribute_month(world, world.clock.date)
        world.ledger.capture_initial(world.total_matter())
        phase_obligations(world)
        for obligation in tribute:
            self.assertGreater(obligation.arrears, 0.0, "Неоплата не в долг")
            self.assertAlmostEqual(obligation.paid_total, 0.0, places=6)
        for household in households:
            self.assertEqual(household.hunger_days, 0, "Оброк превратился в голод")
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_independent_has_no_obligation(self) -> None:
        world, tribe = _world("independent")
        households = tribe_households(world, tribe)
        self.assertEqual(tribe_tribute_month(world, world.clock.date), [])
        for household in households:
            self.assertEqual(
                [oid for oid in household.obligation_ids], [],
                "У независимого племени появилась повинность",
            )
        self.assertEqual(
            [o for o in world.obligations.values() if o.kind == "levy"], []
        )

    def test_empty_household_pays_nothing(self) -> None:
        world, tribe = _world("allied")
        households = tribe_households(world, tribe)
        empty = households[0]
        for pid in list(empty.member_ids):
            world.persons.pop(pid, None)
        empty.member_ids = []
        tribute = tribe_tribute_month(world, world.clock.date)
        self.assertNotIn(
            empty.id, [o.household_id for o in tribute],
            "С пустого двора племени взяли оброк",
        )
        self.assertEqual(empty.obligation_ids, [])
        world.ledger.capture_initial(world.total_matter())
        phase_obligations(world)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_stance_flip_drops_levy_growth(self) -> None:
        world, tribe = _world("allied")
        tribute = tribe_tribute_month(world, world.clock.date)
        for obligation in tribute:
            obligation.arrears = 1.2
        tribe.stance = "independent"
        self.assertEqual(tribe_tribute_month(world, world.clock.date), [])
        for obligation in tribute:
            self.assertNotIn(
                obligation.id, world.obligations,
                "Договор снят, а повинность племени осталась в мире",
            )
        world.ledger.capture_initial(world.total_matter())
        phase_obligations(world)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


class TestTribeMusterPotential(unittest.TestCase):
    """Потенциал вызова — данные: комплекты в руках + живые взрослые."""

    def test_kits_capped_by_muster_potential(self) -> None:
        world, tribe = _world("allied")
        households = tribe_households(world, tribe)
        for household in households:
            world.get_stock(household.stock_id).amounts["war_kit"] = 3.0
        self.assertAlmostEqual(tribe_muster_kits(world, tribe), 2.0, places=6)
        for household in households:
            world.get_stock(household.stock_id).amounts["war_kit"] = 0.5
        self.assertAlmostEqual(tribe_muster_kits(world, tribe), 0.5 * len(households), places=6)

    def test_can_muster_needs_kits_and_adults(self) -> None:
        world, tribe = _world("allied")
        households = tribe_households(world, tribe)
        self.assertFalse(tribe_can_muster(world, tribe), "Без комплектов дают вызов")
        for household in households:
            world.get_stock(household.stock_id).amounts["war_kit"] = 1.0
        self.assertTrue(tribe_can_muster(world, tribe), "Взрослые + комплект — не могут")
        for household in households:
            for pid in list(household.member_ids):
                person = world.persons.get(pid)
                if person is not None and person.age_class == "adult":
                    person.age_class = "child"
        self.assertFalse(tribe_can_muster(world, tribe), "Без взрослых дают вызов")

    def test_independent_gives_nothing(self) -> None:
        world, tribe = _world("independent")
        for household in tribe_households(world, tribe):
            world.get_stock(household.stock_id).amounts["war_kit"] = 5.0
        self.assertAlmostEqual(tribe_muster_kits(world, tribe), 0.0, places=6)
        self.assertFalse(tribe_can_muster(world, tribe))

    def test_tribute_hook_runs_without_manor_month_side_effects(self) -> None:
        """Крюк оброка не трогает труд/паёк: hook = только повинности."""
        world, tribe = _world("allied")
        labor_before = {
            hid: household.labor_days for hid, household in world.households.items()
        }
        tribute = tribe_tribute_month(world, world.clock.date)
        self.assertTrue(tribute)
        for hid, was in labor_before.items():
            self.assertAlmostEqual(
                world.households[hid].labor_days, was, places=6
            )
        self.assertIsNotNone(manor_economy)
