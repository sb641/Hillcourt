"""H3: общий помощник гибели воза — груз ложится в клетку, не исчезает.

Полная гибель воза (`resolve_pack_loss`) переводит ВЕСЬ груз в стоячий сток
клетки назначения (reason `scattered`), помечает воз `lost` и рождает
`silence`-Report по клетке. Помощник общий для обоза (`caravan`) и ушедшего
двора (`household_move`): ветка полной гибели `resolve_migrations` зовёт его
же. Материя сохраняется: сток воза остаётся зарегистрированным пустым.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.tick import run_month
from hillcourt.hazards.travel import resolve_migrations, resolve_pack_loss
from hillcourt.ontology import Pack, Stock
from hillcourt.runner import run
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SEED = 1729
WOLVES_TILE = "t_00_01"


class _ZeroRng:
    """Заглушка потока опасности: любой бросок выпадает в 0 — гибнут все."""

    def random(self) -> float:
        return 0.0

    def choice(self, seq):
        return seq[0]


def _matter_delta(world) -> float:
    return abs(world.ledger.delta(world.total_matter()))


class TestPackLoss(unittest.TestCase):
    """Гибель воза сохраняет материю и порождает молчание."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO, seed=SEED)

    def _make_pack(self, kind, good, amount, destination) -> Pack:
        """Собрать воз с грузом и зарегистрировать его сток в мире."""
        world = self.world
        pack_id = f"test_{kind}"
        cargo = Stock(
            id=f"pack:{pack_id}",
            owner_kind="pack",
            owner_id=pack_id,
            amounts={good: amount},
        )
        world.stocks[cargo.id] = cargo
        origin = next(iter(sorted(world.tiles)))
        pack = Pack(
            id=pack_id,
            kind=kind,
            origin_tile_id=origin,
            destination_tile_id=destination,
            route=[origin, destination],
            member_ids=[],
            cargo=cargo,
            departed_date=world.clock.date,
            eta_date=world.clock.date,
            status="in_transit",
        )
        world.packs[pack_id] = pack
        return pack

    def test_caravan_and_household_cargo_goes_to_standing_stock(self) -> None:
        world = self.world
        caravan = self._make_pack("caravan", "salt", 4.0, "t_03_01")
        migration = self._make_pack("household_move", "grain", 3.0, "t_04_01")
        caravans_dest = "t_03_01"
        migrations_dest = "t_04_01"
        caravan_stock = world.get_stock(
            world.tiles[caravans_dest].standing_stock_id
        )
        migration_stock = world.get_stock(
            world.tiles[migrations_dest].standing_stock_id
        )
        caravan_before = caravan_stock.total()
        migration_before = migration_stock.total()
        # Стартовая материя включает выставленный груз: гибель — только перевод.
        world.ledger.capture_initial(world.total_matter())

        report = resolve_pack_loss(world, caravan, world.clock.date)
        resolve_pack_loss(world, migration, world.clock.date)

        self.assertEqual(caravan.status, "lost")
        self.assertEqual(migration.status, "lost")
        self.assertIn(caravan.cargo.id, world.stocks, "Сток воза удалён")
        self.assertIn(migration.cargo.id, world.stocks, "Сток воза удалён")
        self.assertAlmostEqual(caravan.cargo.total(), 0.0, places=9)
        self.assertAlmostEqual(migration.cargo.total(), 0.0, places=9)
        self.assertAlmostEqual(
            caravan_stock.total(), caravan_before + 4.0, places=6
        )
        self.assertAlmostEqual(
            migration_stock.total(), migration_before + 3.0, places=6
        )

        self.assertEqual(report.source, "silence")
        self.assertEqual(report.subject_kind, "tile")
        self.assertEqual(report.subject_id, caravans_dest)
        self.assertEqual(report.confidence, 0.3)
        self.assertFalse(report.distorted)
        self.assertEqual(report.delivery_date, world.clock.date)
        silences = [
            r
            for r in world.reports
            if r.source == "silence" and r.subject_id == caravans_dest
        ]
        self.assertTrue(silences, "Гибель воза не породила молчания")
        self.assertLess(_matter_delta(world), 1e-6)

    def _first_departure(self):
        """Прокрутить до первого ухода; вернуть (двор, воз)."""
        world = self.world
        for _ in range(24):
            run_month(world)
            for hid in sorted(world.households):
                household = world.households[hid]
                if household.left_at is None:
                    continue
                pack = next(
                    (
                        p
                        for p in world.packs.values()
                        if p.kind == "household_move"
                        and p.owner_household_id == hid
                    ),
                    None,
                )
                return household, pack
        return None, None

    def test_first_household_move_loses_cargo_to_destination(self) -> None:
        world = self.world
        household, pack = self._first_departure()
        self.assertIsNotNone(pack, "За 24 месяца ни один двор не ушёл")
        destination = pack.destination_tile_id
        wolves = world.hazards["haz_001"]
        self.assertEqual(wolves.tile_id, WOLVES_TILE)
        world.tiles[WOLVES_TILE].hazard_ids.remove(wolves.id)
        wolves.tile_id = destination
        world.tiles[destination].hazard_ids.append(wolves.id)

        cargo_before = pack.cargo.total()
        self.assertGreater(cargo_before, 0.0, "Воз ушёл пустым")
        dest_stock = world.get_stock(world.tiles[destination].standing_stock_id)
        dest_before = dest_stock.total()

        world.rng.hazard = _ZeroRng()
        resolve_migrations(world, pack.eta_date)

        self.assertEqual(pack.status, "lost")
        self.assertAlmostEqual(pack.cargo.total(), 0.0, places=9)
        self.assertAlmostEqual(
            dest_stock.total(), dest_before + cargo_before, places=6
        )
        silences = [
            r
            for r in world.reports
            if r.source == "silence" and r.subject_id == destination
        ]
        self.assertTrue(silences, "Полная гибель ухода не породила молчания")
        self.assertLess(_matter_delta(world), 1e-6)

    def test_same_seed_same_state_hash(self) -> None:
        first = run(SCENARIO, 18, seed=SEED)
        second = run(SCENARIO, 18, seed=SEED)
        self.assertEqual(
            first.state_hash,
            second.state_hash,
            "Повторный прогон того же сида дал другой state_hash",
        )


if __name__ == "__main__":
    unittest.main()
