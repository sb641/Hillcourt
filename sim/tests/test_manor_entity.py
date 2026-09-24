"""Манор как книга земли: корень, вложенный тэн, амбар, действия, сезонный зубец.

Контракт тэна (ADR 0010):
  - `Manor.stock_id` обязателен; корень — `settlement:hill_court`, тэн — `manor:<id>`,
    заводится в `grant_thegn`; grant без стока запрещён;
  - двор всегда кормится из одной книги (`Household.manor_id`);
  - среди пожалованных клеток ≥1 `regime:demesne`, иначе grant отклоняется;
  - depth ≤ 1, второй тэн не создаётся;
  - `revoke_thegn` сливает землю, дворы и АМБАР в корень через Ledger;
  - `mustered` — по собственному стоку держателя, не по замку холма.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy import manor as manor_economy
from hillcourt.economy.manor import _is_root_household, duty_days_for
from hillcourt.engine.manor import (
    call_boon,
    ease_week_work,
    eased_days,
    grant_tenement,
    grant_thegn,
    manor_depth,
    manor_of_household,
    manor_of_tile,
    manor_stock,
    nested_manors,
    revoke_thegn,
    root_manor,
    set_tile_regime,
)
from hillcourt.engine.tick import phase_migrate, run_month
from hillcourt.ontology import Stock
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
FS02_TILE = "t_03_01"
DEMESNE_TILE = "t_05_02"  # поле домена холма (works_tiles)
SALT_TILE = "t_08_05"
THEGN_PERSON = "hh_retinue_p1"


class TestManorEntity(unittest.TestCase):
    """Книга земли: право и амбар двигаются между манорами, материя — нет."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO)

    def _grant(self, world=None, tiles=None, households=None):
        world = world or self.world
        return grant_thegn(
            world,
            THEGN_PERSON,
            tiles if tiles is not None else [FS02_TILE, DEMESNE_TILE],
            households if households is not None else ["hh_02"],
        )

    def _render_root_pool(self) -> float:
        self.world.clock.month = 4  # пахота: барщина виллана 12 дней
        manor_economy._replenish_labor(self.world)
        return manor_economy._render_pool(self.world)

    def test_villein_days_in_root_manor_without_grant(self) -> None:
        world = self.world
        root = root_manor(world)
        self.assertIsNotNone(root)
        self.assertIn("hh_02", root.household_ids)
        self.assertEqual(world.households["hh_02"].manor_id, root.id)
        world.households = {"hh_02": world.households["hh_02"]}
        pool = self._render_root_pool()
        self.assertGreater(pool, 0.0, "Дни виллана не видны в корневом маноре")
        self.assertAlmostEqual(root.demesne_labor_filled, pool, places=6)

    def test_grant_moves_book_stock_and_days(self) -> None:
        world = self.world
        root = root_manor(world)
        thegn = self._grant()
        self.assertIsNotNone(thegn)
        self.assertEqual(thegn.stock_id, f"manor:{thegn.id}")
        self.assertIn(thegn.stock_id, world.stocks)
        self.assertEqual(world.stocks[thegn.stock_id].owner_kind, "manor")
        self.assertIs(manor_stock(world, thegn), world.stocks[thegn.stock_id])
        self.assertEqual(world.households["hh_02"].manor_id, thegn.id)
        self.assertFalse(_is_root_household(world, "hh_02"))
        self.assertEqual(manor_of_household(world, "hh_02").id, thegn.id)
        self.assertEqual(manor_of_tile(world, DEMESNE_TILE).id, thegn.id)
        self.assertEqual(manor_depth(world, thegn), 1)
        self.assertTrue(
            any(world.tiles[tid].regime_id == "demesne" for tid in thegn.tile_ids)
        )
        self.assertNotIn("hh_02", root.household_ids)
        self.assertNotIn(FS02_TILE, root.tile_ids)
        self.assertEqual(thegn.parent_manor_id, root.id)
        self.assertEqual(thegn.upward_bundle, "thegn_service")

        world.households = {"hh_02": world.households["hh_02"]}
        pool = self._render_root_pool()
        self.assertEqual(pool, 0.0, "Дни ушли тэну, но остались в корне")
        self.assertGreater(
            thegn.demesne_labor_filled, 0.0, "Дни виллана не числятся у тэна"
        )

    def test_matter_not_duplicated(self) -> None:
        world = self.world
        household = world.households["hh_02"]
        stock = world.get_stock(household.stock_id)
        stock.amounts["grain"] = 7.0
        before_total = world.total_matter()
        stocks_before = set(world.stocks)

        self._grant()

        self.assertAlmostEqual(world.total_matter(), before_total, places=6)
        self.assertIs(world.get_stock(household.stock_id), stock)
        self.assertEqual(world.get_stock(household.stock_id).amounts["grain"], 7.0)
        self.assertTrue(stocks_before.issubset(set(world.stocks)))

    def test_grant_requires_demesne_tile(self) -> None:
        world = self.world
        root = root_manor(world)
        with self.assertRaises(ValueError):
            self._grant(tiles=[FS02_TILE])  # нет regime:demesne
        self.assertIn(FS02_TILE, root.tile_ids, "Земля ушла при отказе")
        self.assertIn("hh_02", root.household_ids)
        self.assertEqual(len(world.manors), 1)

    def test_grant_requires_stock_creation(self) -> None:
        world = self.world
        root = root_manor(world)
        busy = f"manor:manor_{THEGN_PERSON}"
        world.add_stock(Stock(id=busy, owner_kind="sink", owner_id="x", amounts={}))
        with self.assertRaises(ValueError):
            self._grant()
        self.assertEqual(len(world.manors), 1, "Манор создан без амбара")
        self.assertIn(FS02_TILE, root.tile_ids, "Земля потеряна")
        self.assertNotEqual(root_manor(world).grant_ids, ["grant_001"])

    def test_tied_still_does_not_leave(self) -> None:
        world = self.world
        self._grant()
        household = world.households["hh_02"]
        world.clock.month = 1
        household.hunger_days = 5
        phase_migrate(world)
        self.assertIsNone(household.left_at, "tied ушёл из манора тэна")

    def test_second_grant_rejected(self) -> None:
        world = self.world
        first = self._grant()
        self.assertIsNotNone(first)
        second = grant_thegn(world, "hh_retinue_p2", ["t_05_01"], ["hh_03"])
        self.assertIsNone(second, "Второе пожалование тэна не отклонено")
        self.assertEqual(len(root_manor(world).grant_ids), 1)
        self.assertTrue(
            any(a["action"] == "grant_thegn_rejected" for a in world.player_actions)
        )

    def test_thegn_holder_household_joins_book(self) -> None:
        """Двор самого держателя входит в манор тэна и не ест из замка.

        Лимит сценария считает только пожалованные тяглые дворы, поэтому
        держательский двор допустим сверх лимита.
        """
        world = self.world
        root = root_manor(world)
        holder_id = world.persons[THEGN_PERSON].household_id
        self.assertIn(holder_id, root.household_ids)

        thegn = self._grant()
        self.assertIsNotNone(thegn)
        self.assertEqual(world.households[holder_id].manor_id, thegn.id)
        self.assertIn(holder_id, thegn.household_ids)
        self.assertEqual(thegn.household_ids.count(holder_id), 1, "Двор добавлен дважды")
        self.assertNotIn(holder_id, root.household_ids, "Двор остался на книге корня")

        # Повторный grant отклонён как раньше: книга уже отдана.
        self.assertIsNone(grant_thegn(world, "hh_retinue_p2", ["t_05_01"], ["hh_03"]))
        self.assertEqual(len(root.grant_ids), 1)

        # Три пожалованных двора плюс держатель — четыре двора в книге,
        # но лимит сценария (3 тяглых) не нарушен.
        other = load_scenario(SCENARIO)
        limit_thegn = grant_thegn(
            other,
            THEGN_PERSON,
            [FS02_TILE, DEMESNE_TILE],
            ["hh_02", "hh_03", "hh_04"],
        )
        self.assertIsNotNone(limit_thegn)
        self.assertEqual(
            sorted(limit_thegn.household_ids),
            sorted(["hh_02", "hh_03", "hh_04", holder_id]),
        )
        self.assertEqual(other.households[holder_id].manor_id, limit_thegn.id)

        # Держательский двор, перечисленный явно, тоже не идёт в счёт лимита.
        explicit = load_scenario(SCENARIO)
        explicit_thegn = grant_thegn(
            explicit,
            THEGN_PERSON,
            [FS02_TILE, DEMESNE_TILE],
            [holder_id, "hh_02", "hh_03", "hh_04"],
        )
        self.assertIsNotNone(explicit_thegn)
        self.assertEqual(len(explicit_thegn.household_ids), 4)

        # Лимит тяглых дворов всё ещё держится: четвёртый пожалованный — отказ.
        with self.assertRaises(ValueError):
            grant_thegn(
                load_scenario(SCENARIO),
                THEGN_PERSON,
                [FS02_TILE, DEMESNE_TILE],
                ["hh_02", "hh_03", "hh_04", "hh_05"],
            )

    def test_grant_thegn_respects_scenario_limit(self) -> None:
        world = self.world
        with self.assertRaises(ValueError):
            self._grant(tiles=["t_02_01", "t_03_01", "t_05_01", "t_06_01"])
        self.assertIn(FS02_TILE, root_manor(world).tile_ids, "Земля ушла при отказе")

    def test_salt_cannot_become_fief_or_thegn(self) -> None:
        world = self.world
        with self.assertRaises(PermissionError):
            self._grant(tiles=[SALT_TILE, DEMESNE_TILE])
        with self.assertRaises((PermissionError, ValueError)):
            self._grant(households=["hh_salt_01"])
        with self.assertRaises(PermissionError):
            grant_thegn(
                world, "hh_salt_01_p1", [FS02_TILE, DEMESNE_TILE], ["hh_02"]
            )
        self.assertFalse(root_manor(world).grant_ids)

    def test_duplicate_grant_leaves_book_intact(self) -> None:
        world = self.world
        root = root_manor(world)
        with self.assertRaises(ValueError):
            self._grant(households=["hh_02", "hh_02"])
        self.assertIn(FS02_TILE, root.tile_ids, "Клетка потеряла книгу")
        self.assertIn("hh_02", root.household_ids, "Двор потерял книгу")
        self.assertEqual(len(world.manors), 1)

    def test_tenement_cannot_take_tile_from_thegn(self) -> None:
        world = self.world
        thegn = self._grant()
        with self.assertRaises(PermissionError):
            grant_tenement(world, "hh_01", [FS02_TILE])
        self.assertIn(FS02_TILE, thegn.tile_ids)
        self.assertNotIn(FS02_TILE, root_manor(world).tile_ids)

    def test_grant_tenement_sets_regime_and_right(self) -> None:
        world = self.world
        rights = grant_tenement(world, "hh_01", [FS02_TILE])
        self.assertTrue(rights)
        self.assertIn(rights[0].id, world.rights)
        self.assertEqual(world.tiles[FS02_TILE].regime_id, "free_holding")
        self.assertIn("grant_tenement", [a["action"] for a in world.player_actions])

    def test_revoke_returns_land_and_merges_stock(self) -> None:
        world = self.world
        root = root_manor(world)
        household = world.households["hh_01"]
        self.assertEqual(household.legal_status_id, "sokeman")
        thegn = grant_thegn(
            world, "hh_01_p1", [FS02_TILE, DEMESNE_TILE], ["hh_02"]
        )
        self.assertEqual(household.legal_status_id, "thegn")
        self.assertEqual(household.manor_id, thegn.id, "Двор держателя не ушёл к тэну")
        self.assertIn("hh_01", thegn.household_ids)
        world.get_stock(thegn.stock_id).add("grain", 5.0)
        total_before = world.total_matter()
        self.assertEqual(world.households["hh_02"].manor_id, thegn.id)

        self.assertTrue(revoke_thegn(world, thegn.id))

        self.assertNotIn(thegn.stock_id, world.stocks, "Мёртвый амбар остался")
        self.assertEqual(root.tile_ids.count(FS02_TILE), 1, "Земля вернулась дважды")
        self.assertEqual(root.household_ids.count("hh_02"), 1, "Двор вернулся дважды")
        self.assertEqual(world.households["hh_02"].manor_id, root.id)
        self.assertEqual(household.manor_id, root.id, "Двор держателя не вернулся")
        self.assertEqual(root.household_ids.count("hh_01"), 1, "Держатель вернулся дважды")
        self.assertAlmostEqual(world.total_matter(), total_before, places=6)
        self.assertEqual(
            world.get_stock(root.stock_id).amounts.get("grain", 0.0), 60.0 + 5.0
        )
        self.assertEqual(household.legal_status_id, "free_landless", "Бывший держатель не стал свободным без земли")
        self.assertEqual(len(world.manors), 1)

    def test_call_boon_has_effect(self) -> None:
        world = self.world
        world.clock.month = 8
        self.assertFalse(call_boon(world, 1), "Помога вне boon_allowed")
        self.assertTrue(call_boon(world, 8))
        self.assertGreater(
            manor_economy._render_boon(world, 0.0, force=True),
            0.0,
            "Помога по действию игрока ничего не дала",
        )
        quiet = load_scenario(SCENARIO)
        quiet.clock.month = 8
        self.assertEqual(
            manor_economy._render_boon(quiet, 0.0, force=False),
            0.0,
            "Помога берётся без недобора и без действия игрока",
        )

    def test_ease_is_capped_and_yearly(self) -> None:
        world = self.world
        world.clock.month = 8
        value = ease_week_work(world, 8, 999.0)
        self.assertEqual(value, 12.0, "Урезание не ограничено долгом месяца")
        self.assertEqual(eased_days(world, 8), 12.0)
        world.clock.year += 1
        self.assertEqual(eased_days(world, 8), 0.0, "Урезание пережило свой год")

    def test_ease_does_not_touch_thegn_tenant(self) -> None:
        world = self.world
        self._grant()
        world.clock.month = 8
        ease_week_work(world, 8, 12.0)
        self.assertEqual(duty_days_for(world, world.households["hh_02"]), 12.0)
        self.assertEqual(duty_days_for(world, world.households["hh_05"]), 0.0)

    def test_august_tooth_alive_on_root_demesne(self) -> None:
        world = self.world
        root = root_manor(world)
        field_tiles = [
            tid
            for tid in root.tile_ids
            if world.tiles[tid].regime_id == "demesne"
            and world.tiles[tid].terrain == "field"
        ]
        self.assertTrue(field_tiles, "У корня нет доменной пашни")
        expected_august = world.manor.demand_days_per_tile[8] * len(field_tiles)
        expected_february = world.manor.demand_days_per_tile[2] * len(field_tiles)
        demand_by_month: dict[int, float] = {}
        for _ in range(12):
            month = world.clock.month
            run_month(world)
            demand_by_month[month] = root.demesne_labor_demand_this_month
        self.assertEqual(demand_by_month[8], expected_august)
        self.assertEqual(demand_by_month[2], expected_february)
        self.assertGreater(
            demand_by_month[8],
            demand_by_month[2],
            "Календарный зубец августа не жив на корневом домене",
        )

    def test_set_tile_regime_is_logged_and_booked(self) -> None:
        world = self.world
        set_tile_regime(world, FS02_TILE, "reserved_wood")
        self.assertEqual(world.tiles[FS02_TILE].regime_id, "reserved_wood")
        self.assertEqual(
            root_manor(world).tile_regimes[FS02_TILE], "reserved_wood"
        )
        self.assertTrue(
            any(
                a["action"] == "set_tile_regime" and a["tile"] == FS02_TILE
                for a in world.player_actions
            )
        )

    def test_mustered_by_own_stock_not_castle(self) -> None:
        """Полный замок не кормит двор держателя: паёк идёт из амбара тэна."""
        from hillcourt.engine.manor import update_musters

        world = self.world
        thegn = self._grant()
        holder = world.households[world.persons[THEGN_PERSON].household_id]
        castle = world.get_stock(root_manor(world).stock_id)
        thegn_stock = world.get_stock(thegn.stock_id)
        holder_stock = world.get_stock(holder.stock_id)
        self.assertEqual(holder.manor_id, thegn.id)

        # Замок 999, амбар тэна 0, сток двора 0: замок не накрывает стол
        # держателя, двор честно голоден, mustered не поднимается «по замку».
        castle.amounts["grain"] = 999.0
        thegn_stock.amounts["grain"] = 0.0
        holder_stock.amounts["grain"] = 0.0
        holder.hunger_days = 0
        run_month(world)
        self.assertGreater(holder.hunger_days, 0, "Замок накормил двор тэна")
        update_musters(world)
        self.assertFalse(thegn.mustered, "mustered поднялся по замку холма")

        # Тот же полный замок, но амбар тэна не пуст: паёк приходит из него,
        # двор сыт своим стоком. Комплект на держателе — через его сток:
        # mustered требует минимума железа на нём, а не только сытости.
        thegn_stock.amounts["grain"] = 100.0
        holder_stock.amounts["war_kit"] = 1.0
        holder_stock.amounts["grain"] = 1.0
        holder.hunger_days = 0
        run_month(world)
        board = [
            entry
            for entry in world.ledger.entries
            if entry.reason == "board" and entry.dst_id == holder_stock.id
        ]
        self.assertTrue(board, "Амбар тэна не дал пайка держателю")
        self.assertTrue(
            all(entry.src_id == thegn_stock.id for entry in board),
            "Паёк держателя пришёл не из амбара тэна",
        )
        self.assertEqual(holder.hunger_days, 0, "Двор голоден при полном амбаре тэна")
        update_musters(world)
        self.assertTrue(thegn.mustered, "mustered не по своему амбару")
        self.assertTrue(
            any("muster" in entry for entry in world.manor_log),
            "mustered не попал в лог",
        )

    def test_mustered_updated_after_holder_death(self) -> None:
        world = self.world
        thegn = self._grant()
        holder = world.households[world.persons[THEGN_PERSON].household_id]
        world.get_stock(holder.stock_id).amounts["war_kit"] = 1.0
        run_month(world)
        self.assertTrue(thegn.mustered, "Живой, сытый и вооружённый тэн не mustered")
        world.persons[THEGN_PERSON].health = 0.0
        run_month(world)
        self.assertFalse(thegn.mustered, "Мёртвый тэн остался mustered")

    def test_acceptance_grant_thegn_month_six(self) -> None:
        """36 месяцев; на месяце 6 — ручное пожалование тэна.

        В жатву Y1-M08 корневая барщина меньше baseline (дни ушли тэну), у тэна
        есть амбар и домен, mustered виден в логе, дельта материи 0.
        """
        world = load_scenario(SCENARIO)
        baseline = load_scenario(SCENARIO)
        for _ in range(5):
            run_month(world)
        self.assertEqual(world.clock.month, 6)

        thegn = self._grant(world)
        self.assertIsNotNone(thegn)
        holder = world.households[world.persons[THEGN_PERSON].household_id]
        holder_stock = world.get_stock(holder.stock_id)
        world.ledger.external_in(
            holder_stock, "war_kit", 1.0, "test_setup", None, world.clock.date
        )
        for _ in range(31):
            run_month(world)
            run_month(baseline)

        def _first_harvest(log: list[dict], key: str) -> float:
            for entry in log:
                if entry.get("date") == "Y1-M08" and key in entry:
                    return float(entry[key])
            raise AssertionError(f"Нет записи манора '{key}' за Y1-M08")

        self.assertLess(
            _first_harvest(world.manor_log, "corvee_pool"),
            _first_harvest(baseline.manor_log, "corvee_pool"),
            "Барщина корня в жатву не уменьшилась после пожалования",
        )
        self.assertTrue(thegn.stock_id in world.stocks)
        self.assertTrue(
            any(world.tiles[tid].regime_id == "demesne" for tid in thegn.tile_ids)
        )
        self.assertGreater(
            thegn.demesne_labor_filled, 0.0, "Манор тэна не получил дней"
        )
        self.assertTrue(
            any(
                entry.get("muster", {}).get(thegn.id)
                for entry in world.manor_log
                if "muster" in entry
            ),
            "В логе нет mustered=true у живого и сытого тэна",
        )
        self.assertTrue(
            any(a["action"] == "grant_thegn" for a in world.player_actions)
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
