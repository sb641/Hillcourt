"""Зонды смыкания дыр: выплавка крицы, заготовка сена тяглом, RNG без скота.

Держат три наблюдаемых факта смены:
  - `smelt_iron_bloom` есть в каталоге, масса сходится (iron + firewood →
    крица + потери); крица производится действием `idle_repair` (очередь E2,
    гейт сытости, ADR 0037), а не появлением — `external_in` по ней нет;
  - двор с тяглом, которому не хватает сена, косит доступное пастбище
    (материя переводится, дельта 0); tied-конь в тягло не идёт и сена не просит;
  - без скота фаза стада не тратит RNG и не делает ни одной проводки.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy import livestock
from hillcourt.economy.labor import _tile_batch_cap, apply_recipe, work_month
from hillcourt.engine.tick import run_month
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
HILL_SALT = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"


def _give(world, stock_id: str, good: str, amount: float) -> None:
    world.get_stock(stock_id).amounts[good] = float(amount)


class TestSmeltBloom(unittest.TestCase):
    """Рецепт выплавки: баланс, transform, инертность крицы."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = load_scenario(HILL_SALT)

    def test_recipe_balances_and_marks_bloom(self) -> None:
        recipe = self.world.catalogs.recipes["smelt_iron_bloom"]
        left = sum(recipe.inputs.values()) + sum(recipe.draws_standing.values())
        right = sum(recipe.outputs.values()) + sum(recipe.loss.values())
        self.assertAlmostEqual(left, right, places=6, msg="Выплавка не сходится")
        self.assertTrue(recipe.transform, "Выплавка не помечена transform")
        self.assertGreater(recipe.labor_days, 0.0)
        self.assertEqual(recipe.outputs, {"iron_bloom": 1.0})
        self.assertIn("iron", recipe.inputs)
        self.assertIn("iron_bloom", self.world.catalogs.goods)

    def test_smelt_moves_matter_no_creation(self) -> None:
        world = load_scenario(HILL_SALT)
        household = world.households["hh_salt_01"]
        stock = world.get_stock(household.stock_id)
        stock.amounts.clear()
        _give(world, household.stock_id, "iron", 1.2)
        _give(world, household.stock_id, "firewood", 0.6)
        tile = world.tiles[household.current_tile_id]
        recipe = world.catalogs.recipes["smelt_iron_bloom"]
        world.ledger.capture_initial(world.total_matter())
        apply_recipe(world, household, tile, recipe, 1.0, world.clock.date)
        self.assertAlmostEqual(stock.amounts.get("iron_bloom", 0.0), 1.0, places=6)
        self.assertAlmostEqual(stock.amounts.get("iron", 0.0), 0.0, places=6)
        self.assertAlmostEqual(stock.amounts.get("firewood", 0.0), 0.0, places=6)
        self.assertAlmostEqual(
            world.get_stock("sink:waste").amounts.get("iron", 0.0), 0.2, places=6
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_bloom_is_inert_and_feeds_kit_only(self) -> None:
        recipe = self.world.catalogs.recipes["smith_kit"]
        self.assertEqual(recipe.inputs, {"iron_bloom": 1.0})
        self.assertEqual(recipe.outputs, {"war_kit": 1.0})


class TestSmeltWiring(unittest.TestCase):
    """Проводка выплавки к действию (долг №1, ADR 0037): крица — действием.

    Очередь `idle_repair` (ADR 0035 E2): `repair_axe → smelt_iron_bloom →
    smith_iron_share → smith_kit`. Гейт — как у дорогого: только сытый двор
    (`food_months ≥ 2.0`) при входах на партию, труд — из остатка месяца
    (отдельного бюджета нет). Появления крицы нет: ни одно правило не
    производит `iron_bloom` (`external_in` по ней запрещён отсутствием правила).
    """

    def _sated_smith(self, seed: int = 1729):
        world = load_scenario(HILL_SALT, seed=seed)
        household = world.households["hh_01"]
        stock = world.get_stock(household.stock_id)
        stock.amounts.clear()
        stock.amounts.update(
            {"grain": 50.0, "iron": 2.4, "firewood": 2.0, "axe": 1.0}
        )
        household.main_action = "work_plot"
        household.minor_action = "idle_repair"
        household.labor_days = 40.0
        return world, household, stock

    def test_queue_order_pins_e2(self) -> None:
        world = load_scenario(HILL_SALT)
        recipes = world.household_actions["idle_repair"].recipes
        order = ["repair_axe", "smelt_iron_bloom", "smith_iron_share", "smith_kit"]
        idx = [recipes.index(name) for name in order]
        self.assertEqual(
            idx, sorted(idx),
            "Очередь E2 нарушена: repair_axe → smelt → share → kit",
        )

    def test_sated_household_smelts_by_action(self) -> None:
        world, household, stock = self._sated_smith()
        world.ledger.capture_initial(world.total_matter())
        work_month(world, world.clock.date)
        self.assertGreater(
            stock.amounts.get("iron_bloom", 0.0), 0.0,
            "Сытый двор с входами не выплавил крицу действием",
        )
        wires = [e for e in world.ledger.entries if e.reason == "smelt_iron_bloom"]
        self.assertTrue(wires, "Нет проводки выплавки действием")
        leaked = [
            e for e in world.ledger.entries
            if e.kind == "external_in" and e.good == "iron_bloom"
        ]
        self.assertEqual(leaked, [], "Крица пришла появлением, а не действием")
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_hungry_household_smelts_nothing(self) -> None:
        world, household, stock = self._sated_smith()
        stock.amounts["grain"] = 0.0
        world.ledger.capture_initial(world.total_matter())
        work_month(world, world.clock.date)
        wires = [e for e in world.ledger.entries if e.reason == "smelt_iron_bloom"]
        self.assertEqual(wires, [], "Голодный двор выплавил крицу")
        self.assertAlmostEqual(
            stock.amounts.get("iron", 0.0), 2.4, places=6,
            msg="Голодный двор потратил железо",
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)



class TestDraftHay(unittest.TestCase):
    """Сено: двор с тяглом косит доступное пастбище, tied-конь не в счёт."""

    def test_tied_household_with_horse_gathers_nothing(self) -> None:
        world = load_scenario(HILL_SALT, seed=1729)
        household = world.households["hh_05"]  # tied, пастбище t_00_02 рядом
        stock = world.get_stock(household.stock_id)
        stock.amounts.clear()
        _give(world, household.stock_id, "horse_m", 1.0)
        world.ledger.capture_initial(world.total_matter())
        livestock.gather_draft_hay(world, world.clock.date)
        self.assertAlmostEqual(stock.amounts.get("hay", 0.0), 0.0, places=6,
                               msg="tied-двор накосил сено коню, которого не держит")
        self.assertEqual(
            [e for e in world.ledger.entries if e.reason == "gather_hay"], []
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    @staticmethod
    def _grant_pasture_access(world, household, tile_id: str) -> None:
        """Открыть двору пастбище по закону: клетка в `works_tiles` поселения.

        ADR 0068 (Legal): communal-доступ дают только `works_tiles` поселения и
        `Right.kind=common`; одного соседства больше не хватает. Фикстура теста
        оформляет право честно, данными сценария не трогая.
        """
        settlement = world.settlements.get(household.settlement_id or "")
        if settlement is not None and tile_id not in settlement.works_tiles:
            settlement.works_tiles.append(tile_id)

    def test_free_household_with_ox_gathers_hay(self) -> None:
        world = load_scenario(HILL_SALT, seed=1729)
        household = world.households["hh_09"]  # free geneat, пастбище t_05_03
        stock = world.get_stock(household.stock_id)
        stock.amounts.clear()
        _give(world, household.stock_id, "ox_m", 1.0)
        self._grant_pasture_access(world, household, "t_05_03")
        pastures = livestock.hay_pastures(world, household)
        self.assertTrue(pastures, "У свободного двора нет доступного пастбища")
        for tile in pastures:
            world.get_stock(tile.standing_stock_id).amounts["hay"] = 20.0
        labor_before = household.labor_days
        world.ledger.capture_initial(world.total_matter())
        livestock.gather_draft_hay(world, world.clock.date)
        self.assertGreater(stock.amounts.get("hay", 0.0), 0.0, "Тягло осталось без сена")
        gathered = [e for e in world.ledger.entries if e.reason == "gather_hay"]
        self.assertTrue(gathered, "Нет проводки заготовки сена")
        self.assertAlmostEqual(
            household.labor_days, labor_before - 8.0, places=6,
            msg="Заготовка сена не стоила труда",
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


class TestHayTileCap(unittest.TestCase):
    """Ёмкость пастбища общая на косцов: счёт партий на клетку, как в `work_month`."""

    @staticmethod
    def _grant_pasture_access(world, household, tile_id: str) -> None:
        """Открыть двору пастбище по закону (ADR 0068): клетка в `works_tiles`."""
        settlement = world.settlements.get(household.settlement_id or "")
        if settlement is not None and tile_id not in settlement.works_tiles:
            settlement.works_tiles.append(tile_id)

    def test_shared_pasture_cap_not_bypassed(self) -> None:
        world = load_scenario(HILL_SALT, seed=1729)
        target = world.tiles["t_04_01"]  # общее пастбище hh_02, hh_03, hh_08
        world.get_stock(target.standing_stock_id).amounts["hay"] = 100.0
        for hid in ("hh_02", "hh_03", "hh_08"):
            household = world.households[hid]
            self._grant_pasture_access(world, household, target.id)
            stock = world.get_stock(household.stock_id)
            for good in livestock.DRAFT_GOODS:
                stock.amounts.pop(good, None)
            stock.amounts["hay"] = 0.0
            _give(world, household.stock_id, "ox_m", 4.0)
            household.labor_days = 40.0
        cap = _tile_batch_cap(world)
        world.ledger.capture_initial(world.total_matter())
        livestock.gather_draft_hay(world, world.clock.date)
        batches = sum(
            1
            for e in world.ledger.entries
            if e.reason == "gather_hay"
            and e.src_id == f"tile:{target.id}"
            and e.dst_id == "sink:processing"
        )
        self.assertGreater(batches, 0, "Никто не косил общее пастбище")
        self.assertLessEqual(
            batches, cap,
            f"Косцы обошли ёмкость клетки: {batches} партий > кап {cap}",
        )
        self.assertEqual(
            batches, cap, "Кап не выбран полностью: счёт партий на клетку не общий"
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


class TestLivestockNotExternal(unittest.TestCase):
    """Скот не появляется из `external_in` — только старт/приплод/перевод."""

    def test_no_external_in_for_livestock_goods(self) -> None:
        world = load_scenario(HILL_SALT, seed=1729)
        household = world.households["hh_09"]
        stock = world.get_stock(household.stock_id)
        for good in livestock.DRAFT_GOODS:
            stock.amounts.pop(good, None)
        _give(world, household.stock_id, "ox_m", 1.0)
        _give(world, household.stock_id, "ox_f", 1.0)
        _give(world, household.stock_id, "hay", 10.0)
        world.ledger.capture_initial(world.total_matter())
        for _ in range(6):
            run_month(world)
        leaked = [
            e
            for e in world.ledger.entries
            if e.kind == "external_in" and e.good in livestock.DRAFT_GOODS
        ]
        self.assertEqual(leaked, [], f"Скот пришёл из external_in: {leaked}")
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


class TestNoLivestockIsSilent(unittest.TestCase):
    """Без скота фаза стада: ни RNG, ни проводки."""

    def test_no_rng_and_no_ledger_without_herd(self) -> None:
        world = load_scenario(HILL_SALT, seed=1729)
        state_before = world.rng.economy.getstate()
        entries_before = len(world.ledger.entries)
        livestock.livestock_month(world, world.clock.date)
        self.assertEqual(
            world.rng.economy.getstate(), state_before,
            "Без скота израсходован RNG экономики",
        )
        self.assertEqual(
            len(world.ledger.entries), entries_before,
            "Без скота фаза стада сделала проводку",
        )


if __name__ == "__main__":
    unittest.main()
