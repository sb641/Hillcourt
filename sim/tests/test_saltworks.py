"""Солеварня как предприятие (ADR 0092): топливо — условие, оплата — transfer.

Зонды:
  * без торфа выварки нет (топливо — условие производства, не «соль из ничего»);
  * с торфом — соль идёт по рецепту из стоячей рапы;
  * работник без пашни получает харч + соляную долю из склада предприятия;
  * пустой склад — двор голодает честно, и это НЕ «не мог» (нет отдельной отметки
    неспособности: только недоплата хозяина);
  * дельта 0, детерминизм, двое с пашней платы не получают.
"""

from __future__ import annotations

import unittest

from hillcourt.economy.needs import monthly_food_need
from hillcourt.economy import labor
from hillcourt.economy.saltworks import saltworks_month
from hillcourt.economy.saltworks import _has_feeding_land
from hillcourt.engine.tick import run_month

try:
    from .salt_test_support import clone_salt_world, load_salt_template
except ImportError:
    from salt_test_support import clone_salt_world, load_salt_template

MONTHS = 12


def _stand(
    template,
    peat_per_worker: float = 6.0,
    board_grain: float = 200.0,
    fuel_cells: bool = True,
):
    world = clone_salt_world(template)
    settlement = world.settlements["salt_village"]
    stores = world.get_stock(settlement.stores_stock_id)
    stores.amounts["grain"] = board_grain
    for hid in settlement.household_ids:
        world.get_stock(f"household:{hid}").amounts["peat"] = peat_per_worker
    if not fuel_cells:
        settlement.works_tiles = [
            tid
            for tid in settlement.works_tiles
            if world.tiles[tid].terrain not in ("marsh", "forest")
        ]
        world.catalogs.spawn_rules.pop("grow_peat", None)
    world.ledger.capture_initial(world.total_matter())
    return world, settlement


class TestSaltworksFuelAndPay(unittest.TestCase):
    """Топливо → соль; жалованье из склада, без новых валют и без долга-долга."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.template = load_salt_template()

    def test_no_fuel_no_salt_no_pay(self) -> None:
        world, settlement = _stand(
            self.template,
            peat_per_worker=0.0,
            board_grain=200.0,
            fuel_cells=False,
        )
        for _ in range(6):
            run_month(world)
        self.assertEqual(
            [e for e in world.ledger.entries if e.reason == "boil_salt"], [],
            "Соль выварилась без топлива",
        )
        # ADR 0186 перевернул эту строку: раньше она требовала полного молчания
        # при отсутствии выварки, и это было ровно то, из-за чего нанятый работник
        # голодал при непустом складе (ADR 0102 п. 1 и 5: предприятие кормит работника
        # по нужде). Теперь закон такой: без выварки — нет СОЛИ и нет соляной доли
        # (ADR 0092 п. 2), но харч нанятому работнику идёт, пока есть зерно в складе.
        payments = [e for e in world.ledger.entries if e.reason == "saltworks_pay"]
        self.assertTrue(payments, "Нанятый работник не получил харч при простое")
        self.assertTrue(
            all(e.good == "grain" for e in payments),
            "Без выварки не должно быть соляной доли (ADR 0092 п. 2)",
        )
        self.assertEqual(
            world.get_stock(settlement.stores_stock_id).amounts.get("salt", 0.0)
            + sum(
                w.amounts.get("salt", 0.0)
                for h in settlement.household_ids
                if (w := world.get_stock(f"household:{h}")) is not None
            ),
            0.0,
            "Без топлива соли быть не должно: ни в складе предприятия, ни у дворов",
        )

    def test_worker_cuts_own_fuel_on_enterprise_marsh(self) -> None:
        world, _ = _stand(self.template, peat_per_worker=0.0, board_grain=200.0)
        for _ in range(MONTHS):
            run_month(world)
        self.assertTrue(
            [e for e in world.ledger.entries if e.reason == "cut_peat"],
            "Работник солеварни не добывает топливо на топливных клетках предприятия",
        )
        self.assertTrue(
            [e for e in world.ledger.entries if e.reason == "boil_salt"],
            "Соль не вываривается: топливо предприятия никто не берёт",
        )

    def test_salt_wage_does_not_empty_enterprise_store(self) -> None:
        world, settlement = _stand(
            self.template, peat_per_worker=6.0, board_grain=200.0
        )
        for _ in range(MONTHS):
            run_month(world)
        self.assertGreater(
            world.get_stock(settlement.stores_stock_id).amounts.get("salt", 0.0), 0.0,
            "Соляная доля съела весь склад предприятия (обоз нечего грузить)",
        )

    def test_with_fuel_salt_produced_and_workers_paid(self) -> None:
        world, settlement = _stand(
            self.template, peat_per_worker=6.0, board_grain=200.0
        )
        for _ in range(MONTHS):
            run_month(world)
        self.assertTrue(
            [e for e in world.ledger.entries if e.reason == "boil_salt"],
            "С топливом выварки нет",
        )
        paid = [e for e in world.ledger.entries if e.reason == "saltworks_pay"]
        self.assertTrue(paid, "Работники солеварни не получают жалованье")
        grain_paid = sum(e.amount for e in paid if e.good == "grain")
        salt_paid = sum(e.amount for e in paid if e.good == "salt")
        self.assertGreater(grain_paid, 0.0, "Харч не выдан")
        self.assertGreaterEqual(salt_paid, 0.0)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_empty_barn_honest_hunger_not_inability(self) -> None:
        world, settlement = _stand(
            self.template, peat_per_worker=6.0, board_grain=0.0
        )
        run_month(world)
        run_month(world)
        run_month(world)
        self.assertEqual(world.stats.get("saltworks_grain_paid", 0.0), 0.0)
        self.assertGreater(
            world.stats.get("saltworks_short", 0.0), 0.0, "Недоплата не видна",
        )
        self.assertEqual(
            world.stats.get("saltworks_short", 0.0) > 0.0, True,
            "Недоплата не отмечена (отметка «не мог» — чужая вина)",
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_need_rule_moves_only_below_one_batch(self) -> None:
        from hillcourt.ontology import Tile

        """ADR 0187, проверяемое «нужно»: 3.15 — партия, 5.625 — месяц."""
        from hillcourt.economy.labor import (
            PEAT_BATCH_STANDING,
            PEAT_MONTHLY_NEED,
            _peat_works_tiles,
        )

        self.assertEqual(PEAT_BATCH_STANDING, 3.15)
        self.assertEqual(PEAT_MONTHLY_NEED, 5.625)
        # месяц = 2 партии по 3.15 = 6.3 >= 5.625, округление не подводит
        self.assertGreaterEqual(2 * PEAT_BATCH_STANDING, PEAT_MONTHLY_NEED)

        from pathlib import Path as _Path

        from hillcourt.scenario import load_scenario

        world = load_scenario(
            _Path(__file__).resolve().parents[2] / "design" / "scenarios"
            / "v0_hill_and_salt.yml",
            seed=1729,
        )
        bogs = [t for t in world.tiles.values() if t.terrain == "marsh"]
        self.assertGreaterEqual(len(bogs), 2, "нужно два болота для проверки переезда")

        def _set(tile: Tile, amount: float) -> None:
            stock = world.get_stock(tile.standing_stock_id)
            stock.amounts["peat"] = amount

        # тайл промысла ещё держит ударную партию -> уезжать незачем
        _set(bogs[0], PEAT_BATCH_STANDING)
        _set(bogs[1], 0.0)
        stays = _peat_works_tiles(world, bogs)
        self.assertEqual([t.id for t in stays], [bogs[0].id])

        # нехватка: своё болото ниже одной партии, соседнее богаче -> переезд
        _set(bogs[0], PEAT_BATCH_STANDING - 0.01)
        _set(bogs[1], 40.0)
        moved = _peat_works_tiles(world, bogs)
        self.assertEqual(
            [t.id for t in moved], [bogs[1].id], "промысел обязан уехать на богатое"
        )

        # уезжать некуда: нигде нет целой партии -> остаётся на наибольшем
        _set(bogs[0], 3.0)
        _set(bogs[1], 2.0)
        idle = _peat_works_tiles(world, bogs)
        self.assertEqual([t.id for t in idle], [bogs[0].id])

    def test_peat_works_follows_the_peat_and_stands_on_one_bog(self) -> None:
        """ADR 0187: промысел стоит на ОДНОМ болоте и идёт туда, где торфа есть.

        Три формы проверки. Итог — не «все болота сразу», а один тайл: если
        отбор отменён, промысел начнёт свободно перескакивать между болотами,
        и закон «переезжает» перестанет читаться.
        """
        from pathlib import Path as _Path

        from hillcourt.scenario import load_scenario

        # Настоящий мир, а не клон-фикстура: в клоне одно болото, и отбор
        # «где торфа больше» неотличим от «все болота сразу» — проверка была бы
        # пустой (проверено мутацией).
        world = load_scenario(
            _Path(__file__).resolve().parents[2] / "design" / "scenarios"
            / "v0_hill_and_salt.yml",
            seed=1729,
        )
        settlement = next(
            s for s in world.settlements.values() if s.kind == "salt_village"
        )
        recipe = world.catalogs.recipes["cut_peat"]
        worker = next(
            world.households[hid] for hid in sorted(settlement.household_ids)
            if (hh := world.households.get(hid)) is not None
            and hh.left_at is None and not _has_feeding_land(world, hh)
        )
        bogs = [
            tid for tid in settlement.works_tiles
            if world.tiles[tid].terrain == "marsh"
        ]
        self.assertTrue(bogs, "У солеварни нет болота — стенд пустой")
        if len(bogs) < 2:
            # Одно болото — отбор неотличим от «все болота сразу», и проверка
            # переезда была бы пустой. Добавляем второе болото как клетку промысла.
            spare = next(
                (
                    t.id for t in sorted(world.tiles.values(), key=lambda x: x.id)
                    if t.terrain == "marsh" and t.id not in bogs
                ),
                None,
            )
            self.assertIsNotNone(spare, "в мире нет второго болота")  # v0_hill_and_salt: 7
            settlement.works_tiles.append(spare)
            bogs.append(spare)
        for tid in bogs:
            world.get_stock(world.tiles[tid].standing_stock_id).amounts["peat"] = 0.0
        rich, poor = bogs[0], bogs[-1]
        world.get_stock(world.tiles[rich].standing_stock_id).amounts["peat"] = 30.0
        world.get_stock(world.tiles[poor].standing_stock_id).amounts["peat"] = 1.0

        chosen = labor._tiles_for_recipe(world, worker, recipe)
        self.assertEqual(
            [t.id for t in chosen], [rich],
            "Промысел взял не то болото: стоять надо там, где торфа больше",
        )
        self.assertEqual(
            len(chosen), 1, "Промысел работает сразу на нескольких болотах"
        )

        # Переезд: опустошили богатое болото — промысел должен уйти на бедное,
        # а не стоять на выработанном.
        world.get_stock(world.tiles[rich].standing_stock_id).amounts["peat"] = 0.0
        world.get_stock(world.tiles[poor].standing_stock_id).amounts["peat"] = 12.0
        self.assertEqual(
            [t.id for t in labor._tiles_for_recipe(world, worker, recipe)], [poor],
            "Промысел не переехал на болото, где торф есть",
        )

    def test_idle_worker_is_paid_but_gets_no_salt(self) -> None:
        """ADR 0186: нанят и простаивает — платит харчом, соли не получает.

        Закон хозяина: «платить, если нанят и простаивает» (ADR 0186), и он же
        выбрал ADR 0102 п. 1 и 5 против ADR 0092 п. 2. Старый код резал по
        `produced > 0` оба платежа, хотя докстринг ниже себя же писал «харч —
        каждый месяц»; замер падения `test_board_follows_need_when_enterprise_is_filled`
        (5 != 12) — это 6 работников × 2.0 зерна при лаге ровно в месяц.
        """
        world, settlement = _stand(
            self.template, peat_per_worker=0.0, board_grain=60.0
        )
        # Работник солеварни — из книги поселения и без своей кормящей земли.
        worker = next(
            world.households[hid] for hid in sorted(settlement.household_ids)
            if (hh := world.households.get(hid)) is not None
            and hh.left_at is None and not _has_feeding_land(world, hh)
        )
        boiled_before = sum(
            e.amount for e in world.ledger.entries
            if e.reason == "boil_salt" and e.good == "salt"
        )
        self.assertEqual(boiled_before, 0.0, "Стенд: выварка идёт, простоя нет")

        saltworks_month(world, world.clock.date)

        paid = [
            e for e in world.ledger.entries
            if e.reason == "saltworks_pay" and e.dst_id == worker.stock_id
        ]
        self.assertTrue(paid, "Простаивающий нанятый работник не получил харч")
        self.assertTrue(
            all(e.good == "grain" for e in paid),
            "Простой не даёт права на соляную долю (ADR 0092 п. 2)",
        )
        self.assertAlmostEqual(
            sum(e.amount for e in paid), monthly_food_need(world, worker), places=6
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_idle_payment_is_bounded_by_the_enterprise_barn(self) -> None:
        """Харч простоя не бездонен: он limited складом, а не потолком по месяцам.

        Пустой склад — честный голод работника и счётчик `short_households`;
        никакого «потолка N месяцев» не заводим (ADR 0169 п. 4, ADR 0186).
        """
        world, settlement = _stand(
            self.template, peat_per_worker=0.0, board_grain=0.0
        )
        worker = next(
            world.households[hid] for hid in sorted(settlement.household_ids)
            if (hh := world.households.get(hid)) is not None
            and hh.left_at is None and not _has_feeding_land(world, hh)
        )
        stores_id = world.settlements["salt_village"].stores_stock_id
        before = world.get_stock(worker.stock_id).amounts.get("grain", 0.0)
        totals = saltworks_month(world, world.clock.date)
        self.assertAlmostEqual(
            world.get_stock(worker.stock_id).amounts.get("grain", 0.0), before, places=6,
            msg="Платили из воздуха при пустом складе",
        )
        self.assertEqual(totals["grain_paid"], 0.0)
        self.assertGreaterEqual(
            totals["short_households"], 1.0,
            "Пустой склад не отмечен как недоплата хозяина",
        )

    def test_unemployed_court_is_not_paid_by_the_enterprise(self) -> None:
        """Нанят — платит; не нанят (своя кормящая земля) — не платит (ADR 0102).

        Двор-«фермер» берётся ИЗ КНИГИ солеварни: мутация «найм отменён» (считаем
        нанятым каждого) ловится только на таком дворе — двор вне книги предприятия
        не получает выплаты при любой настройке найма.
        """
        world, settlement = _stand(
            self.template, peat_per_worker=0.0, board_grain=60.0
        )
        farmers = [
            world.households[hid] for hid in sorted(settlement.household_ids)
            if (hh := world.households.get(hid)) is not None
            and hh.left_at is None and _has_feeding_land(world, hh)
        ]
        if not farmers:
            # В фикстуре нет двора с пашней внутри книги — берём любого с землёй,
            # но тогда проверка не ловит снятие найма, и это сказано прямо.
            self.skipTest("в книге солеварни нет двора с кормящей землёй")
        farmer = farmers[0]
        saltworks_month(world, world.clock.date)
        self.assertEqual(
            [e for e in world.ledger.entries if e.reason == "saltworks_pay"
             and e.dst_id == farmer.stock_id],
            [],
            "Двор, не нанятый на солеварню, получил харч предприятия",
        )

    def test_farmer_with_own_land_is_not_paid(self) -> None:
        world, _ = _stand(
            self.template, peat_per_worker=6.0, board_grain=200.0
        )
        farmer = next(
            hh for hh in world.households.values()
            if hh.left_at is None and _has_feeding_land(world, hh)
        )
        stores_id = world.settlements["salt_village"].stores_stock_id
        grain_before = world.get_stock(farmer.stock_id).amounts.get("grain", 0.0)
        salt_before = world.get_stock(farmer.stock_id).amounts.get("salt", 0.0)
        saltworks_month(world, world.clock.date)
        self.assertAlmostEqual(
            world.get_stock(farmer.stock_id).amounts.get("grain", 0.0), grain_before,
            places=6, msg="Двор с пашней получил харч от солеварни",
        )
        self.assertAlmostEqual(
            world.get_stock(farmer.stock_id).amounts.get("salt", 0.0), salt_before,
            places=6, msg="Двор с пашней получил соляную долю предприятия",
        )
        self.assertEqual(
            [
                e for e in world.ledger.entries
                if e.reason == "saltworks_pay" and e.dst_id == farmer.stock_id
            ],
            [],
            "Двор с кормящей пашней получил выплату солеварни",
        )
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6,
            msg="Нарушен баланс материи при выплате солеварни",
        )
        # Склад предприятия теперь тратится на НАНЯТЫХ работников даже без выварки
        # (ADR 0186) — это и было падение 5 != 12, которое хозяин разобрал. Проверка
        # остаётся за двором с пашней: он в солеварню не нанят и харча не получает
        # (проверено выше), и его зерно в счёт выплаты не идёт.
        spent = 200.0 - world.get_stock(stores_id).amounts.get("grain", 0.0)
        self.assertAlmostEqual(
            spent, sum(
                e.amount for e in world.ledger.entries
                if e.reason == "saltworks_pay" and e.good == "grain"
            ), places=6,
            msg="Склад потрачен не на харч нанятым и вообще не на харч",
        )
