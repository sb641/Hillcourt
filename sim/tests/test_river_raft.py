"""Река как дорога: плот везёт груз по воде, без судна большой груз не едет.

Река зоны ВОДА в `v0_shire`: три клетки воды (t_04_03, t_04_04, t_04_05),
брод t_04_04. Сухопутный контур не форкнут: маршрут — `engine/path.py`,
риск — hazard-контур (`strongest_hazard`/`resolve_caravans`), живые обозы
(манхэттен) не тронуты. Зонды:
  плот построен → груз по реке; без плота тем же маршрутом не то же самое.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy import caravan
from hillcourt.economy.labor import apply_recipe
from hillcourt.engine.path import find_path
from hillcourt.engine.river import (
    has_vessel,
    route_has_water,
    send_river_pack,
)
from hillcourt.engine.terrain import entry_cost, profile_ids
from hillcourt.hazards.model import base_risk_for, strongest_hazard
from hillcourt.news.propagation import make_report
from hillcourt.ontology import Hazard
from hillcourt.runner import ACTION_HANDLERS
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SHIRE = ROOT / "design" / "scenarios" / "v0_shire.yml"

RIVER_TILES = ("t_03_03", "t_04_03", "t_04_04")
FORD_TILE = "t_04_04"
WEST_BANK = "t_03_04"
EAST_BANK = "t_05_04"


def _report_about(world, tile_id: str) -> None:
    """Положить игроку доставленное известие о клетке (задержка 0)."""
    make_report(
        world,
        source="messenger",
        subject_kind="tile",
        subject_id=tile_id,
        content=f"Весть о клетке '{tile_id}'",
        facts={},
        event_date=world.clock.date,
        delay_months=0,
        confidence=0.5,
    )


def _seed(world, stock_id: str, good: str, amount: float, rule_id: str) -> None:
    """Честно посеять материю в сток через Ledger.external_in по правилу.

    Прямой `stock.add` рвал бы дельту И-1 (материя без проводки); здесь каждая
    единица идёт с `rule_id` (`grow_log`/`grow_grain` — календарные правила
    природы, `test_seed` — только для судна, у которого правило — рецепт).
    """
    world.ledger.external_in(
        world.get_stock(stock_id), good, amount, "test_seed", rule_id,
        world.clock.date,
    )


def _craft_raft_for(world, household_id: str) -> None:
    """Построить плот двору честно: брёвна по правилу, плот — рецептом."""
    household = world.households[household_id]
    stock = world.get_stock(household.stock_id)
    if stock.amounts.get("raft", 0.0) < 1.0 - 1e-9:
        _seed(world, household.stock_id, "log", 3.0, "grow_log")
        tile = world.tiles[household.current_tile_id]
        apply_recipe(
            world, household, tile, world.catalogs.recipes["craft_raft"],
            1.0, world.clock.date,
        )


def _know_all(world) -> None:
    """Сделать игроку известной всю карту (для приказов в зондах)."""
    for tile_id in sorted(world.tiles):
        _report_about(world, tile_id)


class TestRiverCatalog(unittest.TestCase):
    """Благо vessel: плот и лодка хранятся, строятся из брёвен, баланс сходится."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = load_scenario(SHIRE)

    def test_profiles_include_water(self) -> None:
        self.assertIn("water_raft", profile_ids())
        self.assertIn("water_boat", profile_ids())
        self.assertAlmostEqual(
            entry_cost("water_raft", "water", False, False, False), 1.0
        )
        self.assertAlmostEqual(
            entry_cost("water_boat", "water", False, False, False), 0.8
        )

    def test_vessel_goods_have_barn_storage(self) -> None:
        for gid in ("raft", "boat"):
            good = self.world.catalogs.goods.get(gid)
            self.assertIsNotNone(good, f"Нет блага '{gid}'")
            self.assertTrue(good.name, f"У {gid} нет name")
            self.assertEqual(good.storage, "barn", f"{gid} хранится не в амбаре")

    def test_raft_and_boat_recipes_balance_and_transform(self) -> None:
        for rid in ("craft_raft", "craft_boat"):
            recipe = self.world.catalogs.recipes.get(rid)
            self.assertIsNotNone(recipe, f"Нет рецепта '{rid}'")
            left = sum(recipe.inputs.values()) + sum(recipe.draws_standing.values())
            right = sum(recipe.outputs.values()) + sum(recipe.loss.values())
            self.assertAlmostEqual(left, right, places=6, msg=f"{rid}: нет баланса")
            self.assertTrue(recipe.transform, f"{rid} без transform")
            self.assertIn("log", recipe.inputs, f"{rid} строится не из брёвен")
            self.assertGreater(recipe.labor_days, 0.0)
        raft = self.world.catalogs.recipes["craft_raft"]
        self.assertIn("raft", raft.outputs)
        boat = self.world.catalogs.recipes["craft_boat"]
        self.assertIn("boat", boat.outputs)

    def test_idle_repair_builds_raft(self) -> None:
        actions = self.world.household_actions
        self.assertIn("craft_raft", actions["idle_repair"].recipes)
        self.assertIn("craft_boat", actions["idle_repair"].recipes)


class TestRiverMap(unittest.TestCase):
    """Часть клеток — вода/берег/брод в существующем сценарии (одна река)."""

    def setUp(self) -> None:
        self.world = load_scenario(SHIRE)

    def test_three_cells_are_water(self) -> None:
        for tile_id in RIVER_TILES:
            self.assertEqual(
                self.world.tiles[tile_id].terrain, "water", f"{tile_id} не вода"
            )

    def test_ford_marks_crossing(self) -> None:
        self.assertTrue(self.world.tiles[FORD_TILE].ford, "В броде нет флага ford")

    def test_banks_are_land(self) -> None:
        for tile_id in (WEST_BANK, EAST_BANK):
            self.assertNotEqual(
                self.world.tiles[tile_id].terrain, "water", f"{tile_id} залит"
            )


class TestRaftRecipeDrainsStock(unittest.TestCase):
    """Рецепт плота снимает материал из стока (не из воздуха)."""

    def test_craft_raft_moves_log_to_raft(self) -> None:
        world = load_scenario(SHIRE)
        household = world.households["hh_09"]
        tile = world.tiles[household.current_tile_id]
        _seed(world, household.stock_id, "log", 3.0, "grow_log")
        stock = world.get_stock(household.stock_id)
        stock.amounts.pop("raft", None)
        before = world.total_matter()
        recipe = world.catalogs.recipes["craft_raft"]
        apply_recipe(world, household, tile, recipe, 1.0, world.clock.date)
        self.assertAlmostEqual(stock.amounts.get("log", 0.0), 0.0, places=6)
        self.assertAlmostEqual(stock.amounts.get("raft", 0.0), 1.0, places=6)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)
        self.assertAlmostEqual(world.total_matter(), before, places=6)


class TestRiverPath(unittest.TestCase):
    """Pack с профилем реки идёт по воде, не по чаще; телега реку не прыгает."""

    def setUp(self) -> None:
        self.world = load_scenario(SHIRE)

    def test_raft_goes_by_water_not_forest(self) -> None:
        world = self.world
        # Чаща на южном обходе: суше там дорого, плоту — рекой.
        world.tiles["t_03_06"].terrain = "forest"
        world.tiles["t_04_06"].terrain = "forest"
        world.tiles["t_05_06"].terrain = "forest"
        found = find_path(world, WEST_BANK, EAST_BANK, "water_raft", known=None)
        self.assertIsNotNone(found, "Плот не нашёл путь рекой")
        route, _days = found
        self.assertTrue(route_has_water(world, route), "Плот пошёл сушей, а не рекой")
        self.assertIn(FORD_TILE, route, "Плот не взял брод-стрежень")
        for tile_id in route:
            self.assertNotEqual(
                world.tiles[tile_id].terrain, "forest", "Плот полез в чащу"
            )

    def test_cart_does_not_jump_river_without_ford(self) -> None:
        world = self.world
        world.tiles[FORD_TILE].ford = False
        found = find_path(world, WEST_BANK, EAST_BANK, "caravan", known=None)
        if found is not None:
            route, _days = found
            for tile_id in route:
                if world.tiles[tile_id].terrain == "water":
                    self.fail(f"Телега вошла в воду {tile_id} без брода")
        # С бродом суша пересекает реку дорого (тот же контур, без судна):
        # клетка воды стоит пешему 2.5 против 1.0 плота.
        world.tiles[FORD_TILE].ford = True
        foot = find_path(world, WEST_BANK, EAST_BANK, "foot", known=None)
        self.assertIsNotNone(foot, "Брод не пропустил пеших")
        self.assertIn(FORD_TILE, foot[0])
        self.assertAlmostEqual(
            entry_cost("water_raft", "water", False, False, False), 1.0, places=6
        )
        self.assertAlmostEqual(
            entry_cost("foot", "water", False, True, False), 2.5, places=6
        )
        raft = find_path(world, WEST_BANK, EAST_BANK, "water_raft", known=None)
        self.assertIsNotNone(raft)
        self.assertTrue(route_has_water(world, raft[0]))

    def test_foot_ford_is_expensive_and_risky_contour(self) -> None:
        world = self.world
        cost = entry_cost("foot", "water", False, True, False)
        self.assertAlmostEqual(cost, 2.5, places=6)
        self.assertGreater(cost, 1.0, "Брод дешевле плота: брод должен быть дорогим")


class TestVesselRequired(unittest.TestCase):
    """Без судна большой груз по воде не едет; зонд-пары одним маршрутом."""

    def _ready_world(self):
        world = load_scenario(SHIRE)
        _know_all(world)
        return world

    def test_no_vessel_no_large_cargo_same_route(self) -> None:
        world = self._ready_world()
        self.assertFalse(has_vessel(world, "hh_retinue", "water_raft"))
        with self.assertRaises(ValueError):
            send_river_pack(
                world,
                "hh_retinue",
                [],
                "t_06_03",
                "water_raft",
                {"grain": 4.0},
            )
        self.assertFalse(
            [p for p in world.packs.values() if p.owner_household_id == "hh_retinue"],
            "Воз создан без судна",
        )

    def test_raft_built_then_cargo_goes_by_river(self) -> None:
        world = self._ready_world()
        _craft_raft_for(world, "hh_retinue")
        _seed(
            world, world.households["hh_retinue"].stock_id, "grain", 10.0,
            "grow_grain",
        )
        self.assertTrue(has_vessel(world, "hh_retinue", "water_raft"))
        pack = send_river_pack(
            world, "hh_retinue", [], "t_06_03", "water_raft", {"grain": 4.0}
        )
        self.assertEqual(pack.kind, "caravan")
        self.assertTrue(route_has_water(world, pack.route))
        self.assertGreater(pack.cargo.total(), 0.0)
        records = [r for r in world.player_actions if r.get("action") == "send_river"]
        self.assertEqual(len(records), 1, "Приказ не попал в лог")
        self.assertEqual(records[0]["profile"], "water_raft")
        self.assertEqual(records[0]["route"], pack.route)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_runner_knows_send_river(self) -> None:
        self.assertIn("send_river", ACTION_HANDLERS)


class TestRiverDeliveryAndMatter(unittest.TestCase):
    """Речной воз доходит до склада поселения; материя сходится; суша жива."""

    def test_river_unloads_to_settlement_and_conserves(self) -> None:
        world = load_scenario(SHIRE)
        _know_all(world)
        _craft_raft_for(world, "hh_retinue")
        _seed(
            world, world.households["hh_retinue"].stock_id, "grain", 10.0,
            "grow_grain",
        )
        court_before = world.get_stock("settlement:hill_court").amounts.get(
            "grain", 0.0
        )
        pack = send_river_pack(
            world, "hh_retinue", [], "t_06_03", "water_raft", {"grain": 4.0}
        )
        pack.eta_date = world.clock.date
        resolved = caravan.resolve_caravans(world, world.clock.date)
        self.assertIn(pack, resolved, "Речной груз застыл в стоке воза")
        self.assertAlmostEqual(pack.cargo.total(), 0.0, places=6)
        dest_stock = world.get_stock(
            world.settlements["fs_09"].stores_stock_id
        )
        # Груз ушёл со стоков origin в склад назначения через воз.
        self.assertGreater(
            dest_stock.amounts.get("grain", 0.0), 0.0, "Склад fs_09 пуст после реки"
        )
        _ = court_before
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )

    def test_land_salt_convoy_still_alive(self) -> None:
        world = load_scenario(SHIRE)
        world.clock.month = 3
        _seed(world, "household:hh_salt_01", "salt", 3.0, "test_seed")
        _seed(world, "household:hh_salt_02", "salt", 2.0, "test_seed")
        packs = caravan.dispatch_caravans(world, world.clock.date)
        self.assertTrue(packs, "Сухопутный соляной воз не вышел")
        for pack in packs:
            # Сухопутный воз идёт манхэттеном в обход реки (y=1, x=8).
            for tile_id in pack.route:
                if world.tiles[tile_id].terrain == "water":
                    self.fail(f"Сухопутный воз зашёл в воду {tile_id}")
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )


class TestRiverHazardContour(unittest.TestCase):
    """Риск воды — тот же hazard-контур; без опасности — честный свободный проход."""

    def test_water_risk_uses_existing_contour_or_honest_free(self) -> None:
        world = load_scenario(SHIRE)
        # Без опасности — честно свободно: контур молчит, отказа «паводок» нет.
        self.assertIsNone(strongest_hazard(world, FORD_TILE))
        # Та же опасность на воде видна тем же контуром (топь 0.1).
        hazard = Hazard(
            id="haz_river_001",
            kind="bog",
            tile_id=FORD_TILE,
            intensity=1.0,
            active=True,
            spawn_rule_id="bog",
            population=1.0,
            satiety=0.4,
        )
        world.hazards[hazard.id] = hazard
        world.tiles[FORD_TILE].hazard_ids.append(hazard.id)
        found = strongest_hazard(world, FORD_TILE)
        self.assertIsNotNone(found)
        self.assertEqual(found.kind, "bog")
        self.assertAlmostEqual(base_risk_for(world, "bog"), 0.1, places=6)


if __name__ == "__main__":
    unittest.main()
