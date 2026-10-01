"""Земля кормит (ADR 0124): надел, найм по праву, оплата деньгами и харчами.

Проверки-обвинители канона хозяина:

1. двор с кормящим наделом не голодает месяцами подряд;
2. двор, чей надел не кормит (соляная ванна), не «кормящийся землевладелец», а
   работник предприятия — получает харчи/соляную долю;
3. двор с наделом не нанимается, безземельный нанимается;
4. наёмный получает и харчи, и зарплату деньгами (ADR 0120) — не «или»;
5. в `relief_sources` нет приоритета «общая кладовая первой» (ADR 0111 отменён);
6. дельта материи 0 (И-1).
"""

from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from hillcourt.economy import exchange, manor
from hillcourt.economy.exchange import relief_sources
from hillcourt.economy.labor import own_land_feeds
from hillcourt.economy.needs import monthly_food_need
from hillcourt.engine.tick import run_month
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SALT_VILLAGE = "salt_village"
COURT = "hill_court"
SALTBOILER = "hh_salt_01"
SECOND_SALTBOILER = "hh_salt_02"
VILLEIN = "hh_02"
COTTER = "hh_03"
LANDLESS = "hh_06"
MONTHS = 12
EPS = 1e-9

_YEAR: dict[str, object] = {}


def year_world(**kwargs):
    """Один и тот же прогон на 12 месяцев для всех проверок (кеш на модуль)."""
    if not kwargs:
        if "world" not in _YEAR:
            _YEAR["world"] = _run_year()
        return _YEAR["world"]
    return _run_year(**kwargs)


def _run_year(**kwargs):
    world = load_scenario(SCENARIO, seed=1729)
    for stock_id, amounts in kwargs.items():
        stock = world.get_stock(stock_id)
        for good, amount in amounts.items():
            stock.amounts[good] = stock.amounts.get(good, 0.0) + amount
    world.ledger.capture_initial(world.total_matter())
    for _ in range(MONTHS):
        run_month(world)
    return world


def _paid(world, household_id: str, good: str) -> float:
    return sum(
        e.amount
        for e in world.ledger.entries
        if e.reason == "saltworks_pay"
        and e.good == good
        and e.dst_id == f"household:{household_id}"
    )


def _hired(world, household_id: str, good: str) -> float:
    return sum(
        e.amount
        for e in world.ledger.entries
        if e.reason == "hire"
        and e.good == good
        and e.dst_id == f"household:{household_id}"
    )


def _hunger_months(world, household_id: str) -> int:
    return sum(
        1
        for e in world.ledger.entries
        if e.good == "hunger" and e.src_id == f"household:{household_id}"
    )


class TestLandFeedsTheLaw(unittest.TestCase):
    """Канон: земля кормит, надел не нанимается, зарплата деньгами и харчами."""

    def test_holder_with_feeding_land_does_not_starve(self) -> None:
        world = year_world()
        for household_id in (VILLEIN, COTTER):
            with self.subTest(household=household_id):
                household = world.households[household_id]
                self.assertTrue(
                    own_land_feeds(world, household),
                    "Двор с надельной пашней перестал быть кормящимся",
                )
                self.assertEqual(household.hunger_days, 0)
                harvested = sum(
                    e.amount
                    for e in world.ledger.entries
                    if e.reason == "harvest_grain"
                    and e.good == "grain"
                    and e.dst_id == f"household:{household_id}"
                )
                self.assertGreater(harvested, 0.0, "Землевладелец не собрал хлеб")
                self.assertEqual(_hunger_months(world, household_id), 0)

    def test_non_feeding_holding_is_paid_not_fed_by_land(self) -> None:
        world = year_world()
        household = world.households[SALTBOILER]
        self.assertFalse(
            own_land_feeds(world, household),
            "Соляная ванна не кормит — двор не землевладелец-нетокормилец",
        )
        self.assertGreater(_paid(world, SALTBOILER, "grain"), 0.0, "Нет харчей")
        self.assertGreater(_paid(world, SALTBOILER, "salt"), 0.0, "Нет соляной доли")
        monthly = [
            round(
                sum(
                    e.amount
                    for e in world.ledger.entries
                    if e.reason == "saltworks_pay"
                    and e.good == "grain"
                    and e.dst_id == f"household:{SECOND_SALTBOILER}"
                    and e.date.month == month
                ),
                3,
            )
            for month in range(1, MONTHS + 1)
        ]
        self.assertGreaterEqual(
            len([m for m in monthly if m > 0.0]), MONTHS - 1, "Харчи не ежемесячные"
        )

    def test_board_follows_need_when_enterprise_is_filled(self) -> None:
        """Наполненный хозяином склад предприятия кормит работника по нужде (ADR 0102)."""
        world = year_world(**{"settlement:salt_village": {"grain": 200.0}})
        need = monthly_food_need(world, world.households[SECOND_SALTBOILER])
        monthly = [
            sum(
                e.amount
                for e in world.ledger.entries
                if e.reason == "saltworks_pay"
                and e.good == "grain"
                and e.dst_id == f"household:{SECOND_SALTBOILER}"
                and e.date.month == month
            )
            for month in range(1, MONTHS + 1)
        ]
        self.assertEqual(len([m for m in monthly if m > EPS]), MONTHS)
        self.assertAlmostEqual(monthly[-1], need, places=3, msg="Харчи не по нужде")
        for hid in (SALTBOILER, SECOND_SALTBOILER):
            self.assertEqual(world.households[hid].hunger_days, 0)
            self.assertEqual(_hunger_months(world, hid), 0)

    def test_landholder_not_hired_landless_hired(self) -> None:
        world = year_world()
        self.assertGreater(
            _hired(world, LANDLESS, "grain") + _hired(world, LANDLESS, "silver"),
            0.0,
            "Безземельный не нанят",
        )
        for household_id in (VILLEIN, COTTER, SALTBOILER):
            with self.subTest(household=household_id):
                self.assertEqual(
                    _hired(world, household_id, "grain")
                    + _hired(world, household_id, "silver"),
                    0.0,
                    "Двор с наделом получил выплату найма",
                )

    def test_hired_gets_board_and_silver(self) -> None:
        world = year_world(**{"settlement:hill_court": {"silver": 20.0}})
        board = _hired(world, LANDLESS, "grain")
        wage = _hired(world, LANDLESS, "silver")
        self.assertGreater(board, 0.0, "Наёмный не получил харчей")
        self.assertGreater(wage, 0.0, "Наёмный не получил зарплату деньгами")
        self.assertNotIn(
            "elif", inspect.getsource(manor._hire), "В оплате осталось «зерно или серебро»"
        )

    def test_relief_sources_without_granary_priority(self) -> None:
        world = year_world()
        settlement = world.settlements["fs_06"]
        sources = [stock.id for stock in relief_sources(world, world.households[LANDLESS])]
        self.assertNotIn(settlement.stores_stock_id, sources, "Общая кладовая снова первая")
        self.assertEqual(sources, ["settlement:hill_court"])
        self.assertNotIn("stores_stock_id", inspect.getsource(exchange.relief_sources))

    def test_matter_delta_is_zero(self) -> None:
        world = year_world()
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
