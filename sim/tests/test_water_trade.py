"""Водный контур v1: ёмкость судна, фураж от часов, портовый сбор (ADR 0075).

Зонды:
  * ёмкости: `raft` 6.0 / `boat` 12.0 груза, 2 / 4 взрослых; перебор — отказ;
    без судна — только 1.0 / 1 взрослый (ADR 0027);
  * люди не входят в грузовую ёмкость, но кап людей обязателен (иначе «бесплатная»
    перевозка людей);
  * фураж от часов: сутки = hours/24, порция 4.5 (+2.75 тягло), на воде — только
    за ночёвку (>6 ч), короткий рейс без фуража;
  * портовый сбор 10 % натурой при разгрузке в порту (вода в `works_tiles`),
    соль пошлину не платит; дельта 0, детерминизм.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy.water import (
    CARGO_CAPACITY,
    NIGHT_FODDER_HOURS,
    PERSON_CAPACITY,
    check_water_load,
    port_toll,
    water_capacity,
    water_fodder,
)
from hillcourt.ontology import Pack, SimDate, Stock
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_shire.yml"
EPSILON = 1e-9


def _world(household_id: str = "hh_11"):
    return load_scenario(SCENARIO, seed=1729), household_id


def _give(world, household_id, good, amount):
    world.get_stock(f"household:{household_id}").amounts[good] = float(amount)


class TestWaterCapacity(unittest.TestCase):
    """Ёмкости по массе судна; перебор — отказ; люди — отдельный кап."""

    def test_capacities_are_law(self) -> None:
        self.assertEqual(CARGO_CAPACITY["raft"], 6.0)
        self.assertEqual(CARGO_CAPACITY["boat"], 12.0)
        self.assertEqual(PERSON_CAPACITY["raft"], 2)
        self.assertEqual(PERSON_CAPACITY["boat"], 4)

    def test_raft_and_boat_caps(self) -> None:
        world, hid = _world()
        stock = world.get_stock(f"household:{hid}")
        for good in ("raft", "boat"):
            stock.amounts.pop(good, None)
        self.assertEqual(water_capacity(world, hid, "water_raft"), (1.0, 1))
        _give(world, hid, "raft", 1.0)
        self.assertEqual(water_capacity(world, hid, "water_raft"), (6.0, 2))
        _give(world, hid, "boat", 1.0)
        self.assertEqual(water_capacity(world, hid, "water_boat"), (12.0, 4))

    def test_overload_refused(self) -> None:
        world, hid = _world()
        _give(world, hid, "raft", 1.0)
        check_water_load(world, hid, "water_raft", 6.0, 2)
        with self.assertRaises(ValueError):
            check_water_load(world, hid, "water_raft", 6.1, 1)
        with self.assertRaises(ValueError):
            check_water_load(world, hid, "water_raft", 1.0, 3)

    def test_people_not_free_and_not_in_cargo(self) -> None:
        world, hid = _world()
        _give(world, hid, "raft", 1.0)
        cargo_cap, persons_cap = water_capacity(world, hid, "water_raft")
        self.assertEqual(cargo_cap, 6.0)
        self.assertEqual(persons_cap, 2)
        with self.assertRaises(ValueError):
            check_water_load(world, hid, "water_raft", 0.0, 3)


class TestWaterFodderFromHours(unittest.TestCase):
    """Фураж считается от часов (ADR 0078), на воде — за ночёвку."""

    def test_short_water_trip_has_no_fodder(self) -> None:
        self.assertEqual(water_fodder(NIGHT_FODDER_HOURS, "none"), 0.0)
        self.assertEqual(water_fodder(3.0, "horse"), 0.0)

    def test_long_water_trip_charges_portion(self) -> None:
        # 48 ч = 2 суток: обоз 4.5×2 + тягло 2.75×2 = 14.5
        self.assertAlmostEqual(water_fodder(48.0, "horse"), 14.5, places=6)
        self.assertAlmostEqual(water_fodder(48.0, "none"), 9.0, places=6)

    def test_land_fodder_uses_day_fraction(self) -> None:
        from hillcourt.economy.caravan import fodder_for_pack

        world = load_scenario(SCENARIO, seed=1729)
        household = world.households["hh_11"]
        pack = Pack(
            id="probe", kind="caravan", origin_tile_id=household.current_tile_id,
            destination_tile_id="t_01_01", route=[household.current_tile_id, "t_01_01"],
            member_ids=[], cargo=Stock(id="pack:probe", owner_kind="pack",
                                       owner_id="probe", amounts={}),
            departed_date=world.clock.date, eta_date=world.clock.date,
            status="in_transit",
        )
        fodder = fodder_for_pack(world, pack)
        self.assertGreater(fodder, 0.0, "Порция фуража не посчитана")
        self.assertLess(fodder, 4.5, "Рейс короче суток — порция дня не полная")


class TestPortToll(unittest.TestCase):
    """10 % натурой в склад портового поселения; соль без пошлины; дельта 0."""

    def _pack(self, world, amounts):
        cargo = Stock(id="pack:toll", owner_kind="pack", owner_id="t", amounts=dict(amounts))
        world.add_stock(cargo)
        return Pack(
            id="toll", kind="caravan", origin_tile_id="t_01_01",
            destination_tile_id="t_01_01", route=["t_01_01"], member_ids=[],
            cargo=cargo, departed_date=world.clock.date, eta_date=world.clock.date,
            status="in_transit",
        )

    def test_edible_toll_ten_percent(self) -> None:
        world = load_scenario(SCENARIO, seed=1729)
        pack = self._pack(world, {"grain": 10.0, "hay": 5.0})
        stores = world.get_stock("settlement:hill_court")
        before = stores.amounts.get("grain", 0.0)
        world.ledger.capture_initial(world.total_matter())
        taken = port_toll(world, pack, stores, world.clock.date)
        self.assertAlmostEqual(taken.get("grain", 0.0), 1.0, places=6)
        self.assertAlmostEqual(stores.amounts.get("grain", 0.0), before + 1.0, places=6)
        self.assertAlmostEqual(pack.cargo.amounts.get("grain", 0.0), 9.0, places=6)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_salt_exempt(self) -> None:
        world = load_scenario(SCENARIO, seed=1729)
        pack = self._pack(world, {"salt": 8.0})
        stores = world.get_stock("settlement:hill_court")
        # База дельты — после подготовки фикстуры: груз заведён прямой записью
        # в сток, и без capture_initial дельта считала бы его «созданием».
        world.ledger.capture_initial(world.total_matter())
        taken = port_toll(world, pack, stores, world.clock.date)
        self.assertEqual(taken, {}, "Соль платит пошлину")
        self.assertAlmostEqual(pack.cargo.amounts.get("salt", 0.0), 8.0, places=6)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)
