"""Обоз соли: материя едет из дальней деревни в замок, а не только в Report."""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy import caravan
from hillcourt.engine.tick import run_month
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
MONTHS = 36
SALT_VILLAGE = "salt_village"
COURT_STORES = "settlement:hill_court"


class TestCaravanModule(unittest.TestCase):
    """Стоки-источники, отправка по каденции и доставка соли за 36 месяцев."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO)

    def _run_months(self, months: int = MONTHS) -> None:
        """Месяц за месяцем: отправка, тик, разгрузка дошедших обозов."""
        world = self.world
        for _ in range(months):
            caravan.dispatch_caravans(world, world.clock.date)
            run_month(world)
            caravan.resolve_caravans(world, world.clock.date)

    def test_source_stocks_are_live_households_then_stores(self) -> None:
        world = self.world
        ids = [stock.id for stock in caravan.caravan_source_stocks(world, SALT_VILLAGE)]
        self.assertEqual(
            ids,
            ["household:hh_salt_01", "household:hh_salt_02", "settlement:salt_village"],
        )
        self.assertAlmostEqual(caravan.salt_in_village(world), 0.0)

        world.get_stock("household:hh_salt_01").add("salt", 2.5)
        world.get_stock("household:hh_salt_02").add("salt", 1.25)
        world.get_stock("settlement:salt_village").add("salt", 0.25)
        self.assertAlmostEqual(caravan.salt_in_village(world), 4.0)

        world.households["hh_salt_01"].left_at = world.clock.date
        ids = [stock.id for stock in caravan.caravan_source_stocks(world, SALT_VILLAGE)]
        self.assertEqual(ids, ["household:hh_salt_02", "settlement:salt_village"])
        self.assertAlmostEqual(caravan.salt_in_village(world), 1.5)

    def test_dispatch_follows_rule_cadence_and_empty_village(self) -> None:
        world = self.world
        self.assertEqual(caravan.dispatch_caravans(world, world.clock.date), [])

        world.clock.month = 3
        self.assertEqual(caravan.dispatch_caravans(world, world.clock.date), [])
        self.assertEqual(world.stats.get("caravan_empty"), 1.0)
        self.assertEqual(world.packs, {})

        world.catalogs.spawn_rules.pop("caravan_visit")
        world.clock.month = 6
        self.assertEqual(caravan.dispatch_caravans(world, world.clock.date), [])
        self.assertEqual(world.packs, {})

    def test_arrived_pack_with_cargo_is_still_unloaded(self) -> None:
        """Дневной контур пометил воз arrived, но груз обязан доехать до замка."""
        world = self.world
        for _ in range(2):
            run_month(world)
        packs = caravan.dispatch_caravans(world, world.clock.date)
        self.assertTrue(packs, "Обоз не загрузил соль со дворов солеваров")
        pack = packs[0]
        loaded = pack.cargo.total()
        self.assertGreater(loaded, 0.0)

        pack.status = "arrived"
        pack.eta_date = world.clock.date
        self.assertGreater(pack.cargo.total(), 0.0)

        resolved = caravan.resolve_caravans(world, world.clock.date)

        self.assertIn(pack, resolved, "Груз обоза застыл в стоке воза")
        self.assertAlmostEqual(pack.cargo.total(), 0.0, places=6)
        castle_salt = world.get_stock(COURT_STORES).amounts.get("salt", 0.0)
        self.assertAlmostEqual(castle_salt, 4.0 + loaded, places=6)
        self.assertLess(abs(world.ledger.delta(world.total_matter())), 1e-6)

    def test_caravan_delivers_salt_over_36_months(self) -> None:
        world = self.world
        initial_salt = sum(st.amounts.get("salt", 0.0) for st in world.stocks.values())
        self._run_months()

        entries = world.ledger.entries
        loads = [
            e
            for e in entries
            if e.kind == "transfer" and e.good == "salt" and e.reason == "caravan_load"
        ]
        self.assertTrue(loads, "Ни одного погруза соли в обоз")
        self.assertTrue(
            any(e.src_id.startswith("household:hh_salt") for e in loads),
            "Соль не грузилась со стоков дворов солеваров",
        )

        unloads = [
            e
            for e in entries
            if e.kind == "transfer" and e.good == "salt" and e.reason == "caravan_unload"
        ]
        self.assertTrue(unloads, "Ни одной разгрузки соли в замке")
        self.assertTrue(
            all(e.dst_id == COURT_STORES and e.amount > 0.0 for e in unloads),
            "Разгрузка идёт не в сток замка",
        )
        self.assertTrue(
            all(e.src_id.startswith("pack:") for e in unloads),
            "Соль в замок пришла не из стока воза",
        )

        first_load = next(
            i for i, e in enumerate(entries) if e.reason == "caravan_load"
        )
        first_unload = next(
            i for i, e in enumerate(entries) if e.reason == "caravan_unload"
        )
        self.assertLess(first_load, first_unload, "Разгрузка раньше погруза")

        delivered = sum(e.amount for e in unloads)
        castle_salt = world.get_stock(COURT_STORES).amounts.get("salt", 0.0)
        self.assertGreater(castle_salt, 4.0, "Замковая соль не выросла выше 4.0")
        self.assertAlmostEqual(castle_salt, 4.0 + delivered, places=6)

        packs = [p for p in world.packs.values() if p.kind == "caravan"]
        self.assertTrue(packs, "Ни одного обоза")
        self.assertTrue(any(len(p.route) > 1 for p in packs), "Маршрут из одной клетки")
        self.assertTrue(
            any(p.eta_date > p.departed_date for p in packs),
            "Срок доставки не сдвинулся",
        )
        for pack in packs:
            self.assertEqual(pack.route[0], pack.origin_tile_id)
            self.assertEqual(pack.route[-1], pack.destination_tile_id)
            self.assertEqual(len(pack.route), len(set(pack.route)), "Маршрут петляет")

        produced = sum(
            e.amount
            for e in entries
            if e.kind == "process" and e.good == "salt"
        )
        total_salt = sum(st.amounts.get("salt", 0.0) for st in world.stocks.values())
        self.assertAlmostEqual(total_salt, initial_salt + produced, places=6)

        expected_village = 0.0
        for hid in sorted(world.households):
            household = world.households[hid]
            if household.settlement_id == SALT_VILLAGE and household.left_at is None:
                expected_village += world.get_stock(household.stock_id).amounts.get(
                    "salt", 0.0
                )
        expected_village += world.get_stock(
            world.settlements[SALT_VILLAGE].stores_stock_id
        ).amounts.get("salt", 0.0)
        self.assertAlmostEqual(
            caravan.salt_in_village(world), expected_village, places=6
        )

        self.assertLess(abs(world.ledger.delta(world.total_matter())), 1e-6)


class TestHonestDaysFodder(unittest.TestCase):
    """Фураж по честным СУТКАМ от часов маршрута (ADR 0075/0078), не по входам.

    Пин переведён на измеренные часы `find_path`: сутки = `hours/24`, порция
    обоза 4.5 + 2.75 за тягло (`water.PORTION_PER_DAY`). Инварианты: дорога на
    маршруте дешевит фураж, фураж пропорционален суткам, речной короткий рейс
    (≤6 ч) без ночёвки — фуража не берёт, длинный — считает порцию.
    """

    SHIRE = ROOT / "design" / "scenarios" / "v0_shire.yml"

    def _pack(self, world, route, origin="t_08_05", dest="t_01_01"):
        from hillcourt.ontology import Pack, Stock

        return Pack(
            id="caravan_caravan_visit_0001_01", kind="caravan",
            origin_tile_id=origin, destination_tile_id=dest, route=list(route),
            member_ids=[],
            cargo=Stock(id="pack:probe", owner_kind="pack", owner_id="p", amounts={}),
            departed_date=world.clock.date, eta_date=world.clock.date,
            status="in_transit",
        )

    def test_unpaved_pays_full_days(self) -> None:
        from hillcourt.engine.path import find_path
        from hillcourt.engine.terrain import HOURS_PER_DAY
        from hillcourt.economy.water import PORTION_PER_DAY

        world = load_scenario(self.SHIRE, seed=1729)
        route, hours = find_path(world, "t_08_05", "t_01_01", "caravan")
        days = hours / float(HOURS_PER_DAY)
        pack = self._pack(world, route)
        self.assertAlmostEqual(caravan._route_days(world, pack), days, places=6)
        portion = caravan.DEFAULT_FODDER_PER_DAY
        draft = caravan._pack_draft(world, pack)
        if draft in ("donkey", "horse"):
            portion += PORTION_PER_DAY["draft"]
        self.assertAlmostEqual(
            caravan.fodder_for_pack(world, pack), portion * days, places=6
        )
        self.assertGreater(
            caravan.fodder_for_pack(world, pack), 0.0,
            "Немощёный коридор не платит фураж вовсе",
        )

    def test_paved_route_eats_less(self) -> None:
        """Мощёный маршрут ест меньше фуража (сутки от измеренных часов)."""
        from hillcourt.engine.path import find_path

        world = load_scenario(self.SHIRE, seed=1729)
        route, hours = find_path(world, "t_08_05", "t_01_01", "caravan")
        pack = self._pack(world, route)
        before = caravan.fodder_for_pack(world, pack)
        for tile_id in route[1:]:
            world.tiles[tile_id].road = True
        after = caravan.fodder_for_pack(world, pack)
        self.assertLess(
            after, before, "Мощёный маршрут не сэкономил фураж"
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_river_pack_stays_finite(self) -> None:
        """Короткий речной рейс (≤6 ч) — без ночёвки, фуража 0 (ADR 0075 п.6).

        Инвариант прежний: фураж конечен (вода не даёт «бесконечности»);
        длинный речной переход (>6 ч) порцию берёт — проверяется рядом.
        """
        world = load_scenario(self.SHIRE, seed=1729)
        self.assertEqual(world.tiles["t_04_04"].terrain, "water")
        pack = self._pack(
            world, ["t_03_04", "t_04_04", "t_05_04"],
            origin="t_03_04", dest="t_05_04",
        )
        fodder = caravan.fodder_for_pack(world, pack)
        self.assertTrue(fodder < float("inf"), "Фураж речного воза бесконечен")
        self.assertEqual(
            caravan._route_days(world, pack) * 24.0 <= 6.0, True,
            "Пин «короткий речной рейс» перестал быть коротким",
        )
        self.assertAlmostEqual(fodder, 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
