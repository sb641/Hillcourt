"""Обмен между поселениями: конкретный груз, фураж, телега и риск клетки.

D2/D3: обоз с зерном идёт с холма в деревню у ясеня, соль — из соляной
деревни в замок. Путь считается по клеткам, фураж естся из груза (или из
везённого сена), опасная клетка маршрута может рассеять долю груза в сток
клетки назначения. Ёмкость воза — от телеги у origin (`cart_bonus`): с ней
×1.5, без неё ×0.6 и дни пути ×1.5 вверх. Глобальных цен нет: обмен — это
груз, а не цена.
"""

from __future__ import annotations

import math
import unittest
from pathlib import Path

from hillcourt.economy import caravan
from hillcourt.engine.hexgrid import neighbor_ids
from hillcourt.engine.path import find_path
from hillcourt.engine.terrain import HOURS_PER_DAY, entry_cost
from hillcourt.economy.water import PORTION_PER_DAY
from hillcourt.engine.tick import run_month
from hillcourt.ontology import SpawnRule
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_two_settlements.yml"
MONTHS = 36
SEEDS = (1729, 42, 7, 99)
ASH_STORES = "settlement:ash_village"
COURT_STORES = "settlement:hill_court"
ASH_TILE = "t_02_07"
BOG_TILE = "t_02_04"
EATEN = "sink:eaten"


class _AlwaysHazard:
    """Заглушка потока опасности: бросок 0.0 — риск срабатывает всегда."""

    def random(self) -> float:
        return 0.0


class _NeverHazard:
    """Заглушка потока опасности: бросок 1.0 — риска нет."""

    def random(self) -> float:
        return 1.0


def _run_monthly(world, months: int) -> None:
    for _ in range(months):
        run_month(world)


def _transfers(world, *, good=None, reason=None, dst=None, src=None) -> list:
    """Проводки `transfer` по фильтрам (товар/причина/получатель/отправитель)."""
    found = []
    for entry in world.ledger.entries:
        if entry.kind != "transfer":
            continue
        if good is not None and entry.good != good:
            continue
        if reason is not None and entry.reason != reason:
            continue
        if dst is not None and entry.dst_id != dst:
            continue
        if src is not None and entry.src_id != src:
            continue
        found.append(entry)
    return found


def _transfer_sum(world, **kwargs) -> float:
    return sum(entry.amount for entry in _transfers(world, **kwargs))


def _grain_packs(packs) -> list:
    return [pack for pack in packs if pack.cargo.amounts.get("grain", 0.0) > 0.0]


class TestCaravanTrade(unittest.TestCase):
    """Зерно к ясеню, соль в замок, фураж и риск пути — всё по клеткам."""

    def test_grain_and_salt_exchange_over_60_months(self) -> None:
        world = load_scenario(SCENARIO)
        _run_monthly(world, 60)

        grain_to_ash = _transfers(
            world, good="grain", reason="caravan_unload", dst=ASH_STORES
        )
        self.assertTrue(grain_to_ash, "Зерно ни разу не доехало до деревни у ясеня")
        self.assertGreater(sum(e.amount for e in grain_to_ash), 0.0)
        self.assertTrue(
            all(e.src_id.startswith("pack:") for e in grain_to_ash),
            "Зерно в деревню пришло не из стока воза",
        )

        salt_to_court = _transfers(
            world, good="salt", reason="caravan_unload", dst=COURT_STORES
        )
        self.assertTrue(salt_to_court, "Соль перестала доезжать до замка (A1 сломан)")

        fodder = _transfers(world, reason="fodder", dst=EATEN)
        self.assertTrue(fodder, "Фураж не съеден: проводки fodder нет")
        self.assertTrue(all(e.src_id.startswith("pack:") for e in fodder))

        routes = [
            len(pack.route)
            for pack in world.packs.values()
            if pack.kind == "caravan"
        ]
        self.assertTrue(routes, "Ни одного обоза за прогон")
        self.assertTrue(
            all(length > 1 for length in routes), "Маршрут обоза из одной клетки"
        )

        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_missing_settlement_rule_is_skipped(self) -> None:
        world = load_scenario(SCENARIO)
        world.catalogs.spawn_rules["caravan_nowhere"] = SpawnRule(
            id="caravan_nowhere",
            name="Обоз в никуда",
            target="pack",
            kind="calendric",
            params={
                "kind": "caravan",
                "every_months": 1,
                "origin": "hill_court",
                "destination": "nowhere",
                "cargo": "grain",
                "cargo_amount": 1.0,
            },
        )
        dispatched = caravan.dispatch_caravans(world, world.clock.date)
        self.assertEqual(
            dispatched, [], "Правило с несуществующим поселением создало воз"
        )
        self.assertFalse(
            [pid for pid in world.packs if "caravan_nowhere" in pid],
            "Воз в никуда зарегистрирован в мире",
        )
        self.assertFalse(
            [sid for sid in world.stocks if "caravan_caravan_nowhere" in sid],
            "Сток воза в никуда создан",
        )

    def test_hazard_tile_scatters_share_and_reports_silence(self) -> None:
        world = load_scenario(SCENARIO)
        rule = world.catalogs.spawn_rules["caravan_grain_to_ash"]
        capacity = caravan.caravan_capacity(rule, has_cart=True)
        self.assertAlmostEqual(
            capacity, 9.0, places=6, msg="С телегой ёмкость зернового воза не 9.0"
        )
        world.clock.month = 4
        date = world.clock.date
        available = sum(
            stock.amounts.get("grain", 0.0)
            for stock in caravan.caravan_source_stocks(world, "hill_court")
        )
        dispatched = caravan.dispatch_caravans(world, date)
        grain_packs = [
            pack for pack in dispatched if pack.cargo.amounts.get("grain", 0.0) > 0.0
        ]
        self.assertEqual(len(grain_packs), 1, "Обоз с зерном не отправился")
        pack = grain_packs[0]
        # Живой обоз идёт дешёвым путём `find_path` и обходит топь (2,4), где
        # сидела стартовая опасность. Чтобы зонд проверял рассеяние, а не
        # конкретную манхэттен-клетку, переносим опасность на клетку маршрута.
        self.assertNotIn(
            BOG_TILE, pack.route, "Маршрут холм→ясень всё ещё идёт через топь"
        )
        hazard = world.hazards["haz_002"]
        world.tiles[hazard.tile_id].hazard_ids.remove(hazard.id)
        hazard.tile_id = pack.route[1]
        world.tiles[hazard.tile_id].hazard_ids.append(hazard.id)
        loaded = pack.cargo.amounts["grain"]
        self.assertAlmostEqual(
            loaded,
            min(capacity, available),
            places=6,
            msg="Загрузка не равна min(ёмкость с телегой, доступное зерно)",
        )
        fodder = caravan.fodder_for_pack(world, pack)
        self.assertGreater(fodder, 0.0)

        pack.eta_date = date
        world.rng.hazard = _AlwaysHazard()
        resolved = caravan.resolve_caravans(world, date)

        self.assertIn(pack, resolved)
        self.assertEqual(pack.status, "arrived", "Частичная потеря не губит воз")
        expected_scatter = (loaded - fodder) * 0.25
        standing = world.get_stock(f"tile:{ASH_TILE}").amounts.get("grain", 0.0)
        self.assertAlmostEqual(standing, expected_scatter, places=6)
        silences = [
            report
            for report in world.reports
            if report.source == "silence" and report.subject_id == ASH_TILE
        ]
        self.assertTrue(silences, "Провал риска не породил silence-Report о клетке")
        self.assertAlmostEqual(pack.cargo.total(), 0.0, places=6)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_no_cart_capacity_and_days(self) -> None:
        """Без телеги у origin ёмкость 3.6, дни пути ×1.5 и загрузка меньше."""
        world = load_scenario(SCENARIO)
        rule = world.catalogs.spawn_rules["caravan_grain_to_ash"]
        capacity_cart = caravan.caravan_capacity(rule, has_cart=True)
        capacity_no_cart = caravan.caravan_capacity(rule, has_cart=False)
        self.assertAlmostEqual(capacity_cart, 9.0, places=6)
        self.assertAlmostEqual(
            capacity_no_cart,
            3.6,
            places=6,
            msg="Без телеги ёмкость зернового воза не 3.6",
        )

        for stock in caravan.caravan_source_stocks(world, "hill_court"):
            stock.amounts.pop("cart", None)
        world.ledger.capture_initial(world.total_matter())
        self.assertFalse(caravan.origin_has_cart(world, "hill_court"))

        world.clock.month = 4
        date = world.clock.date
        available = sum(
            stock.amounts.get("grain", 0.0)
            for stock in caravan.caravan_source_stocks(world, "hill_court")
        )
        packs = [
            pack
            for pack in caravan.dispatch_caravans(world, date)
            if pack.cargo.amounts.get("grain", 0.0) > 0.0
        ]
        self.assertEqual(len(packs), 1, "Обоз без телеги не отправился")
        pack = packs[0]
        loaded = pack.cargo.amounts["grain"]
        self.assertAlmostEqual(
            loaded,
            min(capacity_no_cart, available),
            places=6,
            msg="Загрузка не равна min(ёмкость без телеги, доступное зерно)",
        )
        self.assertLess(
            loaded,
            min(capacity_cart, available),
            "Без телеги воз взял не меньше, чем с телегой",
        )

        route_len = len(pack.route)
        self.assertAlmostEqual(
            caravan.caravan_days(route_len, has_cart=True),
            float(route_len),
            places=6,
        )
        self.assertAlmostEqual(
            caravan.caravan_days(route_len, has_cart=False),
            float(math.ceil(route_len * 1.5)),
            places=6,
        )
        self.assertGreater(
            caravan.caravan_days(route_len, has_cart=False),
            caravan.caravan_days(route_len, has_cart=True),
            "Без телеги дни пути не выросли в 1.5 раза",
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_non_edible_cargo_carries_and_eats_hay(self) -> None:
        world = load_scenario(SCENARIO)
        world.get_stock("household:hh_court").amounts["hay"] = 5.0
        world.ledger.capture_initial(world.total_matter())
        world.catalogs.spawn_rules["caravan_salt_hay"] = SpawnRule(
            id="caravan_salt_hay",
            name="Соль с холма",
            target="pack",
            kind="calendric",
            params={
                "kind": "caravan",
                "every_months": 1,
                "origin": "hill_court",
                "destination": "ash_village",
                "cargo": "salt",
                "cargo_amount": 2.0,
            },
        )
        date = world.clock.date
        dispatched = caravan.dispatch_caravans(world, date)
        packs = [
            pack
            for pack in dispatched
            if pack.id.startswith("caravan_caravan_salt_hay")
        ]
        self.assertEqual(len(packs), 1, "Несъедобный груз не отправился")
        pack = packs[0]
        self.assertAlmostEqual(
            pack.cargo.amounts.get("hay", 0.0),
            caravan.fodder_for_pack(world, pack),
            places=6,
            msg="Воз не взял сено на фураж",
        )

        pack.eta_date = date
        world.rng.hazard = _NeverHazard()
        caravan.resolve_caravans(world, date)

        self.assertAlmostEqual(pack.cargo.amounts.get("hay", 0.0), 0.0, places=6)
        self.assertAlmostEqual(
            _transfer_sum(world, reason="fodder", dst=EATEN),
            caravan.fodder_for_pack(world, pack),
            places=6,
        )
        self.assertAlmostEqual(
            world.get_stock(ASH_STORES).amounts.get("salt", 0.0), 2.0, places=6
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_intervillage_transfer_and_hunger_over_four_seeds(self) -> None:
        """36 месяцев × 4 seed: обмен идёт, но голод не убит обменом-богом."""
        numbers: dict[int, tuple[float, float, float]] = {}
        for seed in SEEDS:
            world = load_scenario(SCENARIO, seed=seed)
            _run_monthly(world, MONTHS)
            grain = _transfer_sum(
                world, good="grain", reason="caravan_unload", dst=ASH_STORES
            )
            salt = _transfer_sum(
                world, good="salt", reason="caravan_unload", dst=COURT_STORES
            )
            hunger = float(world.stats.get("hunger_months", 0.0))
            self.assertGreater(grain, 0.0, f"seed {seed}: зерно не дошло до ясеня")
            self.assertGreater(salt, 0.0, f"seed {seed}: соль не дошла до замка")
            self.assertGreater(
                hunger, 0.0, f"seed {seed}: обмен-бог убил голод целиком"
            )
            self.assertAlmostEqual(
                world.ledger.delta(world.total_matter()),
                0.0,
                places=6,
                msg=f"seed {seed}: материя не сохранилась",
            )
            numbers[seed] = (grain, salt, hunger)
        self.assertEqual(sorted(numbers), sorted(SEEDS))

    def test_road_on_route_shortens_days_and_keeps_delta(self) -> None:
        """Дорога на маршруте обоза дешевит вход: дни `find_path` падают, дельта 0."""
        world = load_scenario(SCENARIO)
        origin, dest = "t_01_01", "t_02_07"
        before = find_path(world, origin, dest, "caravan")
        self.assertIsNotNone(before)
        route, hours_before = before
        # Самый дорогой участок фактического маршрута — туда и кладём дорогу.
        costly = max(
            route[1:],
            key=lambda tile_id: entry_cost(
                "caravan",
                world.tiles[tile_id].terrain,
                world.tiles[tile_id].road,
                world.tiles[tile_id].ford,
                world.tiles[tile_id].bridge,
            ),
        )
        world.tiles[costly].road = True

        after = find_path(world, origin, dest, "caravan")
        self.assertIsNotNone(after)
        route_after, hours_after = after
        self.assertLess(hours_after, hours_before, "Дорога на маршруте не ускорила воз")
        # Инвариант: дорога дешевит ход, но не до нуля (часы > 0).
        self.assertGreater(hours_after, 0.0, "Дорога обнулила путь")

        world.ledger.capture_initial(world.total_matter())
        world.clock.month = 4
        packs = _grain_packs(caravan.dispatch_caravans(world, world.clock.date))
        self.assertEqual(len(packs), 1, "Обоз с зерном не отправился")
        pack = packs[0]
        self.assertEqual(list(pack.route), list(route_after), "Воз идёт не маршрутом find_path")
        self.assertEqual(pack.route[0], origin)
        # Фураж по СУТКАМ маршрута, сутки — из часов (ADR 0075/0078):
        # `hours/24`, порция правила + тягло, тем же счётом, что и тик.
        route_days = hours_after / float(HOURS_PER_DAY)
        portion = float(
            world.catalogs.spawn_rules["caravan_grain_to_ash"].params["fodder_per_day"]
        )
        draft = caravan._pack_draft(world, pack)
        if draft in ("donkey", "horse"):
            portion += PORTION_PER_DAY["draft"]
        self.assertAlmostEqual(
            caravan.fodder_for_pack(world, pack),
            portion * route_days,
            places=6,
            msg="Фураж не пропорционален суткам пути от часов (ADR 0075/0078)",
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_no_path_blocks_dispatch_and_keeps_delta(self) -> None:
        """Река без брода вокруг цели: `find_path` — None, воз не создаётся.

        Кольцо воды — полное, по шести соседям гекс-сетки (ADR 0071: соседство
        из `engine/hexgrid.py`); иначе два лишних направления обходят блокаду.
        """
        world = load_scenario(SCENARIO)
        target = world.tiles[ASH_TILE]
        for tile_id in neighbor_ids(world, target):
            world.tiles[tile_id].terrain = "water"
            world.tiles[tile_id].ford = False
            world.tiles[tile_id].bridge = False
        self.assertIsNone(
            find_path(world, "t_01_01", ASH_TILE, "caravan"),
            "Вода без переправы не отрезала цель",
        )

        world.ledger.capture_initial(world.total_matter())
        world.clock.month = 4
        dispatched = caravan.dispatch_caravans(world, world.clock.date)
        self.assertEqual(
            _grain_packs(dispatched), [], "Воз отправился без пути"
        )
        self.assertFalse(
            [pid for pid in world.packs if "caravan_grain_to_ash" in pid],
            "Воз без пути зарегистрирован в мире",
        )
        self.assertFalse(
            any(e.kind == "external_in" for e in world.ledger.entries),
            "Отправка без пути создала материю",
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
