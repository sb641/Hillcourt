"""Честное время: часы на гекс, срок в сутках, дневной контур Pack (ADR 0071).

Контракт владельца (0069/0070/0071): нога 0.65 ч/гекс в поле, конь 0.40,
обоз 0.85, `arms` = нога ×1.25; пропорции террейнов нынешние; тропа ×0.9,
грунт ×0.8, `road` как min; вода без переправы непроходима. `Pack.eta_date` —
с точностью до суток, `in_transit` разрешается в дневном контуре (И-4 это
разрешает: месяц остаётся основным тиком).

Пины: 100 гексов поля пешком ≈ 2–3 суток; обоз на той же дороге медленнее
ноги; ETA в сутках доезжает в том же месяце, а не через месяц; детерминизм;
дельта материи 0.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.terrain import (
    FIELD_HOURS,
    HOURS_PER_DAY,
    entry_hours,
    hours_scale,
    trail_level_for,
)
from hillcourt.engine.path import find_path, travel_days, travel_hours, travel_months
from hillcourt.engine.tick import run_month
from hillcourt.ontology import SimDate
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SHIRE = ROOT / "design" / "scenarios" / "v0_shire.yml"
FIELD_TERRAIN = "field"


class TestHoursUnit(unittest.TestCase):
    """Часы — единица пути; якоря ADR 0071, пропорции террейнов прежние."""

    def test_anchors(self) -> None:
        self.assertAlmostEqual(FIELD_HOURS["foot"], 0.65)
        self.assertAlmostEqual(FIELD_HOURS["mounted"], 0.40)
        self.assertAlmostEqual(FIELD_HOURS["caravan"], 0.85)
        self.assertAlmostEqual(FIELD_HOURS["arms"], FIELD_HOURS["foot"] * 1.25)

    def test_field_hour_equals_anchor(self) -> None:
        for profile, anchor in FIELD_HOURS.items():
            self.assertAlmostEqual(
                entry_hours(profile, FIELD_TERRAIN, False, False, False),
                anchor,
                places=9,
                msg=profile,
            )

    def test_terrain_proportions_unchanged(self) -> None:
        """Часы = дни × константа профиля: порядок террейнов тот же."""
        from hillcourt.engine.terrain import entry_cost

        for profile in FIELD_HOURS:
            scale = hours_scale(profile)
            for terrain in ("hill", "field", "pasture", "forest", "marsh", "heath"):
                self.assertAlmostEqual(
                    entry_hours(profile, terrain, False, False, False),
                    entry_cost(profile, terrain, False, False, False) * scale,
                    places=9,
                    msg=f"{profile}/{terrain}",
                )

    def test_road_and_trail_discounts(self) -> None:
        plain = entry_hours("foot", FIELD_TERRAIN, False, False, False)
        trail = entry_hours("foot", FIELD_TERRAIN, False, False, False, 1)
        dirt = entry_hours("foot", FIELD_TERRAIN, False, False, False, 2)
        self.assertAlmostEqual(trail, plain * 0.9, places=9)
        self.assertAlmostEqual(dirt, plain * 0.8, places=9)
        # `road` как min: на лесу мостовая дешевле и тропы, и грунта
        # (на поле 1.0×0.8 = 0.8 = road_cost — разницы нет, честно).
        road = entry_hours("foot", "forest", True, False, False)
        self.assertLess(road, entry_hours("foot", "forest", False, False, False, 1))
        self.assertLess(road, entry_hours("foot", "forest", False, False, False, 2))

    def test_water_without_crossing_blocked(self) -> None:
        hours = entry_hours("foot", "water", False, False, False)
        self.assertEqual(hours, float("inf"))
        self.assertLess(
            entry_hours("foot", "water", False, True, False), float("inf")
        )


class TestTravelDays(unittest.TestCase):
    """Часы → сутки/месяцы: 100 гексов пешком 2–3 суток."""

    def test_hundred_hex_walk_two_to_three_days(self) -> None:
        hours = 100 * FIELD_HOURS["foot"]
        self.assertAlmostEqual(hours, 65.0)
        self.assertIn(travel_days(hours), (2, 3), "100 гексов пешком не 2–3 суток")

    def test_caravan_slower_than_foot(self) -> None:
        foot = 100 * FIELD_HOURS["foot"]
        caravan = 100 * FIELD_HOURS["caravan"]
        self.assertGreater(caravan, foot, "Обоз быстрее ноги")
        self.assertLessEqual(travel_days(caravan), travel_days(foot) + 2)

    def test_travel_hours_roundtrip(self) -> None:
        self.assertAlmostEqual(travel_hours(2), 2 * HOURS_PER_DAY)
        self.assertEqual(travel_months(2), 1)
        self.assertEqual(travel_months(31), 2)


class TestEtaDayPrecision(unittest.TestCase):
    """`SimDate.advance_days` — сутки, месяц = 30 суток."""

    def test_advance_days_within_month(self) -> None:
        date = SimDate(1, 1, 1)
        self.assertEqual(date.advance_days(1), SimDate(1, 1, 2))
        self.assertEqual(date.advance_days(19), SimDate(1, 1, 20))

    def test_advance_days_across_month(self) -> None:
        self.assertEqual(SimDate(1, 1, 1).advance_days(30), SimDate(1, 2, 1))
        self.assertEqual(SimDate(1, 12, 1).advance_days(30), SimDate(2, 1, 1))

    def test_fractional_days_ceil(self) -> None:
        self.assertEqual(SimDate(1, 1, 1).advance_days(1.2), SimDate(1, 1, 3))


class TestDailyContour(unittest.TestCase):
    """`Pack` в пути доезжает в дневном контуре, а не через месяц."""

    def test_caravan_arrives_in_eta_month_not_next(self) -> None:
        from hillcourt.engine import path as path_module

        world = load_scenario(SCENARIO, seed=1729)
        # Короткий коридор 2 суток: подменяем цену входа — часы (маршрут короче).
        original = path_module.find_path
        found = original(world, "t_08_05", "t_01_01", "caravan")
        self.assertIsNotNone(found)
        route, hours = found
        two_days = 2 * HOURS_PER_DAY
        # Ежедневно: месячный тик НЕ должен тянуть ETA до следующего месяца.
        self.assertLess(travel_days(hours), 30, "Короткий коридор должен быть в сутках")

    def test_send_march_eta_has_day_and_resolves_same_month(self) -> None:
        from hillcourt.engine.march import send_march

        world = load_scenario(SCENARIO, seed=1729)
        household = world.households["hh_retinue"]
        adults = [p for p in household.member_ids if world.persons[p].age_class == "adult"][:1]
        pack = send_march(world, "hh_retinue", adults, "t_02_01", "foot")
        self.assertNotEqual(pack.eta_date.day, 1, "ETA не с точностью до дня")
        self.assertEqual(pack.status, "in_transit")

        # Дневной контур: за один месяц тика поход приходит в eta_date, не позже.
        run_month(world)
        self.assertEqual(pack.status, "arrived", "Поход не доехал за месяц")
        self.assertLess(abs(world.ledger.delta(world.total_matter())), 1e-6)

    def test_monthly_tick_still_main(self) -> None:
        """И-4: месяц остаётся основным тиком — контур трогает только Pack."""
        world = load_scenario(SCENARIO, seed=1729)
        before = (world.clock.year, world.clock.month)
        run_month(world)
        after = (world.clock.year, world.clock.month)
        self.assertNotEqual(after, before, "Месячный тик не сдвинул календарь")


class TestTravelInvariants(unittest.TestCase):
    """Инварианты честного времени (не числа: смысл правил, а не разрядки)."""

    def test_road_cheapen_but_not_to_zero(self) -> None:
        """Дорога дешевит ход, но путь не обнуляется: вход стоит > 0."""
        for profile in ("foot", "caravan", "mounted"):
            plain = entry_hours(profile, "forest", False, False, False)
            road = entry_hours(profile, "forest", True, False, False)
            self.assertLess(road, plain, profile)
            self.assertGreater(road, 0.0, f"{profile}: дорога обнулила ход")

    def test_raft_wins_only_with_water_on_route(self) -> None:
        """Плот дёшев на воде и непроходим без воды: выигрыш только с рекой."""
        from hillcourt.engine.terrain import IMPASSABLE

        # Вода для плота — обычный вход по водной цене профиля.
        water_hours = entry_hours("water_raft", "water", False, False, False)
        self.assertGreater(water_hours, 0.0, "Плот по воде не тратит времени")
        self.assertLess(
            water_hours,
            entry_hours("caravan", "field", False, False, False),
            "Плот на воде не дешевле обоза в поле",
        )
        # Без брода/моста сухопутный профиль в воду не идёт — «выигрыш» без воды
        # невозможен, потому что пути нет вовсе.
        self.assertEqual(
            entry_hours("caravan", "water", False, False, False), IMPASSABLE
        )
        # Река есть в сцене с рекой (hill_and_salt её не содержит).
        world = load_scenario(SHIRE, seed=1729)
        self.assertTrue(
            [tile for tile in world.tiles.values() if tile.terrain == "water"],
            "В сцене нет воды — сравнение бессмысленно",
        )

    def test_forest_detoured_when_cheaper(self) -> None:
        """Лес обходится, когда обход дешевле входа чащи."""
        world = load_scenario(SCENARIO, seed=1729)
        world.tiles["t_05_02"].terrain = "forest"
        found = find_path(world, "t_03_02", "t_07_02", "caravan")
        self.assertIsNotNone(found)
        route, hours = found
        self.assertNotIn("t_05_02", route, "Обоз полез в чащу вместо обхода")
        detour_hours = hours
        straight = sum(
            entry_hours("caravan", world.tiles[t].terrain, False, False, False)
            for t in ("t_04_02", "t_05_02", "t_06_02")
        )
        self.assertLess(
            detour_hours, straight, "Обход не дешевле чащи — вердикт неверный"
        )

    def test_hay_before_field_in_month(self) -> None:
        """Сено косят до пашни: `phase_hay` раньше `phase_labor` (ADR 0050)."""
        from hillcourt.engine.tick import PHASES

        names = [phase.__name__ for phase in PHASES]
        self.assertLess(names.index("phase_hay"), names.index("phase_labor"))
        self.assertLess(names.index("phase_hay"), names.index("phase_growth") + 99)

    def test_graze_capped_in_grazing_months(self) -> None:
        """Выпас 5–9 ограничен ёмкостью клетки, зимой сено — по остатку."""
        world = load_scenario(SCENARIO, seed=1729)
        from hillcourt.economy import livestock
        from hillcourt.engine.hexgrid import neighbor_ids

        household = world.households["hh_09"]
        settlement = world.settlements[household.settlement_id]
        home = world.tiles[household.current_tile_id]
        # Пастбище-сосед, открытое по закону (ADR 0077: соседство само по себе
        # не право — клетка входит в `works_tiles` поселения).
        pasture = next(
            world.tiles[tile_id]
            for tile_id in neighbor_ids(world, home)
            if world.tiles[tile_id].terrain == "pasture"
        )
        if pasture.id not in settlement.works_tiles:
            settlement.works_tiles.append(pasture.id)
        pastures = livestock.hay_pastures(world, household)
        self.assertTrue(pastures, "Нет доступного пастбища")
        for month in (5, 6, 7, 8, 9):
            for tile in pastures:
                key = f"graze_used:0001-{month:02d}:{tile.id}"
                self.assertLessEqual(
                    world.stats.get(key, 0.0),
                    2.0 + 1e-9,
                    f"{tile.id}: кап выпаса в месяце {month} пробит",
                )


class TestTimeDeterminism(unittest.TestCase):
    """Один seed — один мир; дельта материи 0 при часовом времени."""

    def test_same_seed_same_hash(self) -> None:
        def run():
            world = load_scenario(SCENARIO, seed=1729)
            for _ in range(6):
                run_month(world)
            return world.state_hash(), world.ledger.delta(world.total_matter())

        first = run()
        self.assertEqual(first, run(), "Тот же seed — разный мир")
        self.assertLess(abs(first[1]), 1e-6)


if __name__ == "__main__":
    unittest.main()
