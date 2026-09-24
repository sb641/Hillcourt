"""Оси манора: пресеты из YAML, трудодни домена, паёк раба, уход по статусу."""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy.needs import member_counts, monthly_food_need
from hillcourt.engine.tick import phase_migrate
from hillcourt.legal.calendar import (
    BOON_USED_KEY,
    WEEKS_PER_MONTH,
    boon_cap,
    can_sow_demesne,
    hire_demand,
    monthly_labor_days,
    seasonal_labor_days,
    sow_demesne,
    take_boon,
)
from hillcourt.legal.manor import (
    demesne_labor_pool,
    land_feeds_household,
    land_requires_labor_days,
    render_labor,
)
from hillcourt.legal.regimes import can_leave, preset_of
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
LEGAL_SRC = ROOT / "sim" / "src" / "hillcourt" / "legal"

EXPECTED_TRIPLES = {
    "holder": ("free", "secure_holding", "holder_none"),
    "thegn": ("free", "secure_holding", "thegn_service"),
    "sokeman": ("free", "secure_holding", "sokeman_rent"),
    "geneat": ("free", "tenement", "geneat_service"),
    "villein": ("tied", "tenement", "villein_full"),
    "cotter": ("tied", "tenement", "cotter_monday"),
    "free_landless": ("free", "landless", "free_landless_none"),
    "slave": ("slave", "landless", "slave_ration"),
}


class TestManorAxes(unittest.TestCase):
    """Пресеты — ярлыки из каталога; поведение читается из YAML, не из классов."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO)

    def test_presets_read_from_yaml_as_triples(self) -> None:
        statuses = self.world.catalogs.legal_statuses
        self.assertEqual(set(statuses), set(EXPECTED_TRIPLES))
        for pid, triple in EXPECTED_TRIPLES.items():
            preset = statuses[pid]
            self.assertEqual(
                (preset.personal_status, preset.land_relation, preset.obligation_bundle),
                triple,
                f"Пресет {pid}: неверная тройка",
            )
            self.assertIn(
                preset.obligation_bundle,
                self.world.catalogs.bundles,
                f"Пресет {pid}: бандл не существует",
            )

    def test_behavior_reads_catalog_not_class(self) -> None:
        hh = self.world.households["hh_02"]
        preset = preset_of(self.world, hh)
        self.assertFalse(can_leave(self.world, hh))
        preset.can_leave = True  # правка данных, не кода
        self.assertTrue(can_leave(self.world, hh), "can_leave не читает каталог")
        preset.can_leave = False

    def test_no_class_named_after_preset_in_code(self) -> None:
        for path in sorted(LEGAL_SRC.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for pid in EXPECTED_TRIPLES:
                self.assertNotIn(f"class {pid.capitalize()}", text)
            self.assertNotIn('== "Knight"', text)
            self.assertNotIn("class Knight", text)

    def test_villein_does_not_leave_legally(self) -> None:
        hh = self.world.households["hh_02"]
        self.assertEqual(hh.legal_status_id, "villein")
        hh.hunger_days = 5
        phase_migrate(self.world)
        self.assertIsNone(hh.left_at)

    def test_sokeman_leaves(self) -> None:
        hh = self.world.households["hh_01"]
        self.assertEqual(hh.legal_status_id, "sokeman")
        hh.hunger_days = 5
        phase_migrate(self.world)
        self.assertIsNotNone(hh.left_at)

    def test_labor_days_go_to_demesne_pool_not_from_air(self) -> None:
        hh = self.world.households["hh_03"]
        before = hh.labor_days
        self.assertGreater(before, 0.0)
        rendered = render_labor(self.world, hh, 30.0)
        self.assertEqual(rendered, 30.0)
        self.assertEqual(hh.labor_days, before - 30.0)
        self.assertEqual(demesne_labor_pool(self.world), 30.0)
        # Больше, чем есть, взять нельзя: труд не из воздуха.
        rendered_extra = render_labor(self.world, hh, 10_000.0)
        self.assertEqual(rendered_extra, before - 30.0)
        self.assertEqual(hh.labor_days, 0.0)
        self.assertEqual(demesne_labor_pool(self.world), before)

    def test_slave_eats_from_lord_stock_not_own_holding(self) -> None:
        court = self.world.households["hh_court"]
        slaves = [
            self.world.persons[pid]
            for pid in court.member_ids
            if self.world.persons[pid].personal_status == "slave"
        ]
        self.assertTrue(slaves, "У двора лорда нет рабов")
        for slave in slaves:
            self.assertEqual(slave.household_id, court.id)
            self.assertNotIn(f"household:{slave.id}", self.world.stocks)
        adults, _, _ = member_counts(self.world, court)
        # Рабы — рты при дворе лорда: их кормит сток лорда, а не свой надел.
        self.assertGreater(adults, 2)
        self.assertGreater(monthly_food_need(self.world, court), 2.0)

    def test_villein_winter_less_than_harvest(self) -> None:
        world = self.world
        self.assertEqual(world.calendar[1].season, "winter")
        self.assertEqual(world.calendar[8].season, "harvest")
        winter = monthly_labor_days(world, 1, "villein")
        harvest = monthly_labor_days(world, 8, "villein")
        self.assertGreater(harvest, winter, "Барщина виллана не сезонна")
        self.assertEqual(winter, 2 * WEEKS_PER_MONTH)
        self.assertEqual(harvest, 3 * WEEKS_PER_MONTH)

    def test_cotter_harvest_more_than_january(self) -> None:
        world = self.world
        january = monthly_labor_days(world, 1, "cotter")
        harvest = monthly_labor_days(world, 8, "cotter")
        self.assertGreater(harvest, january, "Коттер в жатве не отдаёт больше")

    def test_geneat_callout_only_no_week_days(self) -> None:
        world = self.world
        for month in range(1, 13):
            mod = world.calendar[month].labor_mod.get("geneat", {})
            self.assertNotIn("base_week_days", mod, f"У генеата недельные дни (мес {month})")
            self.assertIn("callout", mod)
        self.assertEqual(monthly_labor_days(world, 1, "geneat"), 0.0)
        self.assertGreater(monthly_labor_days(world, 6, "geneat"), 0.0)

    def test_slave_has_no_seasonal_zero(self) -> None:
        world = self.world
        for month in range(1, 13):
            self.assertGreater(
                monthly_labor_days(world, month, "slave"),
                0.0,
                f"У раба сезонный ноль в месяце {month}",
            )

    def test_bundles_reference_calendar_not_copies(self) -> None:
        for bundle_id in ("villein_full", "cotter_monday", "geneat_service", "sokeman_rent", "slave_ration"):
            bundle = self.world.catalogs.bundles[bundle_id]
            self.assertEqual(bundle.calendar_id, "calendar_v0")
            self.assertNotIn("week_labor_days", bundle.terms)
            self.assertNotIn("low_labor", bundle.terms)

    def test_boon_capped_and_counted(self) -> None:
        world = self.world
        hh = world.households["hh_02"]  # villein, boon_days_cap = 3
        cap = boon_cap(world, hh)
        self.assertGreater(cap, 0.0)
        self.assertEqual(take_boon(world, hh, 1), 0.0, "Помога в месяц без boon_allowed")
        total = 0.0
        for _ in range(5):
            total += take_boon(world, hh, 8)
        self.assertLessEqual(total, cap)
        self.assertAlmostEqual(world.stats.get(BOON_USED_KEY, 0.0), total)

    def test_gafol_acres_move_grain_to_demesne(self) -> None:
        world = self.world
        hh = world.households["hh_02"]  # villein, sow_demesne_acres = 1
        stock = world.get_stock(hh.stock_id)
        stock.amounts["grain"] = 5.0
        court = world.settlements[world.player.court_settlement_id]
        court_stock = world.get_stock(court.stores_stock_id)
        before_court = court_stock.amounts.get("grain", 0.0)
        total_before = world.total_matter()

        self.assertFalse(can_sow_demesne(world, 3), "Пахота гэфоль-акров не в осень")
        self.assertEqual(sow_demesne(world, hh, 3), 0.0)

        world.clock.month = 10
        self.assertTrue(can_sow_demesne(world, 10))
        moved = sow_demesne(world, hh, 10)
        self.assertGreater(moved, 0.0)
        self.assertLess(stock.amounts.get("grain", 0.0), 5.0)
        self.assertAlmostEqual(
            court_stock.amounts.get("grain", 0.0), before_court + moved, places=6
        )
        self.assertAlmostEqual(world.total_matter(), total_before, places=6)

    def test_tied_does_not_leave_in_winter(self) -> None:
        world = self.world
        hh = world.households["hh_02"]
        world.clock.month = 1
        self.assertLess(
            seasonal_labor_days(world, hh, 1),
            seasonal_labor_days(world, hh, 8),
            "Зимняя барщина виллана не меньше жатвенной",
        )
        hh.hunger_days = 5
        phase_migrate(world)
        self.assertIsNone(hh.left_at, "tied ушёл зимой из-за малой барщины")

    def test_hire_demand_by_season(self) -> None:
        world = self.world
        self.assertEqual(hire_demand(world, 1), "low")
        self.assertEqual(hire_demand(world, 8), "high")

    def test_manor_land_kinds(self) -> None:
        demesne = self.world.catalogs.land_regimes["demesne"]
        self.assertTrue(demesne.requires_labor_days)
        self.assertFalse(demesne.feeds_household)
        villein = self.world.catalogs.land_regimes["villein_tenement"]
        self.assertTrue(villein.feeds_household)
        self.assertFalse(villein.requires_labor_days)
        demesne_tile = self.world.tiles["t_01_01"]  # холм игрока
        self.assertTrue(land_requires_labor_days(self.world, demesne_tile))
        self.assertFalse(land_feeds_household(self.world, demesne_tile))


if __name__ == "__main__":
    unittest.main()
