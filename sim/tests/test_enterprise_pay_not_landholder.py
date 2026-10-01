"""Общинный доступ ≠ надел: работник предприятия получает выплату (ADR 0102, 0092).

`Settlement.works_tiles` и `Right.kind = common` — это ДОСТУП (`docs/07_legal.md:
64-68`), а не личный надел. Поэтому `_has_feeding_land` спрашивает про ВЛАДЕНИЕ
(`labor.own_holding_tiles`), и работник солеварни получает харчи и соляную долю
из склада предприятия, а землевладелец выплаты не получает.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy.labor import own_holding_tiles, own_tiles
from hillcourt.economy.saltworks import _has_feeding_land
from hillcourt.engine.tick import run_month
from hillcourt.ontology import Right
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SALT_VILLAGE = "salt_village"
SALTBOILER = "hh_salt_01"
LANDLESS = "hh_06"
VILLEIN = "hh_02"
COTTER = "hh_03"
MONTHS = 12


def _year():
    world = load_scenario(SCENARIO, seed=1729)
    world.ledger.capture_initial(world.total_matter())
    for _ in range(MONTHS):
        run_month(world)
    return world


def _paid(world, household_id: str, good: str) -> float:
    stock_id = f"household:{household_id}"
    return sum(
        e.amount
        for e in world.ledger.entries
        if e.reason == "saltworks_pay" and e.good == good and e.dst_id == stock_id
    )


class TestEnterprisePayNotLandholder(unittest.TestCase):
    """Доступ не делает двор землевладельцем; платит предприятие из своего склада."""

    def test_communal_access_is_not_a_landholding(self) -> None:
        world = load_scenario(SCENARIO, seed=1729)
        village = world.settlements[SALT_VILLAGE]
        household = world.households[SALTBOILER]
        communal = list(village.works_tiles)
        self.assertTrue(communal, "У соляной деревни нет общинных угодий")
        self.assertFalse(
            _has_feeding_land(world, household),
            "Общинное поле сделало работника землевладельцем",
        )
        own = {t.id for t in own_holding_tiles(world, household)}
        self.assertFalse(own & set(communal), "Общинные клетки попали в личный надел")
        self.assertTrue(
            {t.id for t in own_tiles(world, household)} & set(communal),
            "Доступ к общинным угодьям должен остаться",
        )
        world.rights["r_common_probe"] = Right(
            id="r_common_probe",
            holder_household_id=LANDLESS,
            tile_id=communal[0],
            kind="common",
            granted_date=world.clock.date,
            rent_share=0.0,
        )
        self.assertFalse(
            _has_feeding_land(world, world.households[LANDLESS]),
            "Право common сделало двор землевладельцем",
        )

    def test_salt_worker_is_paid_from_enterprise_store(self) -> None:
        world = _year()
        grain = sum(
            e.amount
            for e in world.ledger.entries
            if e.reason == "saltworks_pay" and e.good == "grain"
        )
        salt = sum(
            e.amount
            for e in world.ledger.entries
            if e.reason == "saltworks_pay" and e.good == "salt"
        )
        self.assertGreater(grain, 0.0, "Харчи предприятия не выданы")
        self.assertGreater(salt, 0.0, "Соляная доля не выдана")
        store = f"settlement:{SALT_VILLAGE}"
        drained = sum(
            e.amount
            for e in world.ledger.entries
            if e.reason == "saltworks_pay" and e.src_id == store
        )
        self.assertAlmostEqual(drained, grain + salt, places=6, msg="Выплата не из склада")
        self.assertGreater(_paid(world, SALTBOILER, "grain"), 0.0)

    def test_landholder_gets_no_enterprise_pay(self) -> None:
        world = _year()
        for household_id in (VILLEIN, COTTER):
            with self.subTest(household=household_id):
                self.assertTrue(
                    _has_feeding_land(world, world.households[household_id]),
                    "Двор с настоящим наделом перестал быть землевладельцем",
                )
                self.assertEqual(_paid(world, household_id, "grain"), 0.0)
                self.assertEqual(_paid(world, household_id, "salt"), 0.0)
                harvested = [
                    e
                    for e in world.ledger.entries
                    if e.reason == "harvest_grain"
                    and e.dst_id == f"household:{household_id}"
                ]
                self.assertTrue(harvested, "Землевладелец перестал собирать зерно")

    def test_matter_delta_is_zero(self) -> None:
        world = _year()
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_landless_without_hire_gets_no_enterprise_pay(self) -> None:
        """Рабочий без надела и без найма не получает подачи предприятия (законно)."""
        world = _year()
        self.assertEqual(_paid(world, LANDLESS, "grain"), 0.0)
        self.assertEqual(_paid(world, LANDLESS, "salt"), 0.0)
        salted = {
            e.dst_id
            for e in world.ledger.entries
            if e.reason == "saltworks_pay"
        }
        self.assertNotIn(f"household:{LANDLESS}", salted)


if __name__ == "__main__":
    unittest.main()
