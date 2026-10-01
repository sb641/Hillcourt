"""Приказ лорда `grant_grain`: зерно двора из амбара лорда, а не из ниоткуда.

Замер, который открыл приказ: пять дворов на `start_stand` стояли в
`request_relief` месяцами подряд — `food_months = 0.0`, `_own_hex_has_crop` ложно,
`_own_book_grain` смотрит на амбар КНИГИ, а подача шла из амбара сеньора. Ручная
выдача 60 зерна прямо в состояние мира возвращала двор к `work_plot`, то есть
заморозка стояла ровно на зерне в руках. Приказа, который это зерно даёт, в
`ACTION_HANDLERS` не было. Проверки:

  * приказ зарегистрирован в каталоге (`actions_household.yml`) и в
    `ACTION_HANDLERS` — иначе его нечем вызвать;
  * зерно идёт в амбар ТОГО двора (`Household.stock_id`), а общий амбар поселения
    пуст: это ручная выдача, а не складовая кладовая;
  * источник — амбар корневого манора, и материя сходится (И-1);
  * при нехватке — `ValueError` и НИ ОДНОГО перевода (частичной выдачи нет);
  * приказ исполняется как `script:`-действие runner с датой в `player_actions`;
  * главное: замороженный двор (`food_months == 0`, урожая на гексе нет) после
    приказа возвращается к `work_plot` — приказ снимает заморозку, а не только
    двигает число на счету.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy.decisions import _feeding_tiles, _own_hex_has_crop, choose_actions
from hillcourt.economy.needs import food_months, food_shortfall
from hillcourt.engine.manor import grant_grain, root_manor
from hillcourt.runner import ACTION_HANDLERS, _apply_script_entry
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
STAND = ROOT / "design" / "scenarios" / "start_stand.yml"
COURT = "settlement:hill_court"
COMMON_BARN = "settlement:farm"
TENANT = "hh_family_01"
OTHER_TENANT = "hh_family_02"
GRANT = 60.0
EPSILON = 1e-9
# Незимний месяц: зимой `choose_actions` уводит в заготовку топлива раньше, чем
# смотрит на сытость, и приказ не был бы виден.
SUMMER_MONTH = 6


def _world():
    return load_scenario(STAND, seed=1729)


def _grant_entries(world) -> list:
    return [e for e in world.ledger.entries if e.reason == "grant_grain"]


def _frozen(world, household_id: str = TENANT):
    """Довести двор до заморозки: зерна нет, на кормящих гексах ничего не стоит."""
    world = world or _world()
    household = world.households[household_id]
    world.get_stock(household.stock_id).amounts["grain"] = 0.0
    for tile in _feeding_tiles(world, household):
        world.get_stock(tile.standing_stock_id).amounts.clear()
    household.hunger_days = 30
    world.clock.month = SUMMER_MONTH
    return world, household


class TestGrantGrainIsRegistered(unittest.TestCase):
    """Приказ объявлен в данных и в диспетчере — иначе его нечем исполнить."""

    def test_catalog_declares_the_order(self) -> None:
        world = _world()
        action = world.household_actions.get("grant_grain")
        self.assertIsNotNone(
            action, "Приказ grant_grain не объявлен в actions_household.yml"
        )
        self.assertEqual(action.purpose, "lord")
        self.assertEqual(action.labor_share, 0.0)
        self.assertEqual(action.recipes, [])

    def test_dispatcher_knows_the_order(self) -> None:
        self.assertIn(
            "grant_grain", ACTION_HANDLERS, "Приказ не зарегистрирован в ACTION_HANDLERS"
        )


class TestGrantGrainMovesMatter(unittest.TestCase):
    """Зерно — материя: перевод из амбара лорда в амбар ЭТОГО двора."""

    def test_grain_lands_in_that_household_barn(self) -> None:
        world = _world()
        court = world.get_stock(COURT)
        target = world.get_stock(world.households[TENANT].stock_id)
        other = world.get_stock(world.households[OTHER_TENANT].stock_id)
        common = world.get_stock(COMMON_BARN)
        court_before = court.amounts.get("grain", 0.0)
        target_before = target.amounts.get("grain", 0.0)
        other_before = other.amounts.get("grain", 0.0)
        common_before = common.amounts.get("grain", 0.0)
        world.ledger.capture_initial(world.total_matter())

        grant_grain(world, TENANT, GRANT)

        entries = _grant_entries(world)
        self.assertEqual(len(entries), 1, "Приказ не сделал ровно один перевод")
        entry = entries[0]
        self.assertEqual(entry.src_id, COURT)
        self.assertEqual(entry.dst_id, f"household:{TENANT}")
        self.assertEqual(entry.good, "grain")
        self.assertAlmostEqual(entry.amount, GRANT, places=6)
        self.assertAlmostEqual(
            court.amounts.get("grain", 0.0), court_before - GRANT, places=6
        )
        self.assertAlmostEqual(
            target.amounts.get("grain", 0.0), target_before + GRANT, places=6
        )
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6,
            msg="Приказ создал материю",
        )
        record = world.player_actions[-1]
        self.assertEqual(record["action"], "grant_grain")
        self.assertEqual(record["household"], TENANT)
        self.assertEqual(record["good"], "grain")
        self.assertAlmostEqual(record["amount"], GRANT, places=6)
        self.assertEqual(record["date"], str(world.clock.date))
        self.assertEqual(world.stats.get("relief_given"), None, "Приказ не подача")
        self.assertAlmostEqual(other.amounts.get("grain", 0.0), other_before, places=6)
        self.assertAlmostEqual(
            common.amounts.get("grain", 0.0), common_before, places=6,
            msg="Зерно попало в общий амбар поселения, а не в амбар двора",
        )

    def test_short_grant_raises_without_partial_transfer(self) -> None:
        world = _world()
        court = world.get_stock(COURT)
        court_before = court.amounts.get("grain", 0.0)
        world.ledger.capture_initial(world.total_matter())

        with self.assertRaises(ValueError):
            grant_grain(world, TENANT, court_before + 1.0)

        self.assertEqual(_grant_entries(world), [], "Появился лишний перевод")
        self.assertAlmostEqual(
            court.amounts.get("grain", 0.0), court_before, places=6
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_bad_request_touches_nothing(self) -> None:
        world = _world()
        with self.assertRaises(ValueError):
            grant_grain(world, TENANT, 0.0)
        with self.assertRaises(ValueError):
            grant_grain(world, "hh_nowhere", 1.0)
        self.assertEqual(_grant_entries(world), [])


class TestScriptGrantGrain(unittest.TestCase):
    """`script:`-приказ проходит через диспетчер runner.

    Ключевое: тест идёт через `_apply_script_entry`, то есть через
    `ACTION_HANDLERS`. Снятый обработчик роняет его на `ValueError` с датой,
    а снятая механика (перевода нет) — на пустом `ledger`.
    """

    def test_script_entry_moves_grain_through_the_dispatcher(self) -> None:
        world = _frozen(_world())[0]
        target = world.get_stock(world.households[TENANT].stock_id)
        target_before = target.amounts.get("grain", 0.0)
        world.ledger.capture_initial(world.total_matter())

        records = _apply_script_entry(
            world,
            {"at_month": 1, "action": "grant_grain", "household": TENANT, "amount": GRANT},
        )

        self.assertEqual(len(records), 1, "Приказ не дал записи в player_actions")
        self.assertEqual(records[0]["action"], "grant_grain")
        self.assertEqual(records[0]["household"], TENANT)
        self.assertAlmostEqual(records[0]["amount"], GRANT, places=6)
        self.assertEqual(records[0]["date"], str(world.clock.date))
        self.assertEqual(records[0]["month"], SUMMER_MONTH)
        entries = _grant_entries(world)
        self.assertEqual(len(entries), 1, "Приказ не сделал перевода")
        self.assertEqual(entries[0].dst_id, f"household:{TENANT}")
        self.assertAlmostEqual(
            target.amounts.get("grain", 0.0), target_before + GRANT, places=6
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_unknown_action_still_raises(self) -> None:
        """Диспетчер честно ругается на незнакомый приказ, а не молча ест запись."""
        world = _world()
        with self.assertRaises(ValueError):
            _apply_script_entry(
                world,
                {"at_month": 1, "action": "grant_grain_nope", "household": TENANT, "amount": 1.0},
            )
        self.assertEqual(_grant_entries(world), [])


class TestGrantGrainUnfreezesTheHousehold(unittest.TestCase):
    """Приказ снимает заморозку: двор возвращается к `work_plot`."""

    def test_frozen_household_asks_relief_then_works_the_plot(self) -> None:
        world, household = _frozen(_world())
        root = root_manor(world)
        self.assertIsNotNone(root, "Нет корневого манора")
        court = world.get_stock(root.stock_id)
        self.assertGreater(court.amounts.get("grain", 0.0), GRANT, "У лорда нет зерна")
        self.assertFalse(_own_hex_has_crop(world, household), "На гексе есть урожай")
        self.assertAlmostEqual(food_months(world, household), 0.0, places=6)
        self.assertEqual(choose_actions(world, household)[0], "request_relief")

        grant_grain(world, TENANT, GRANT)

        self.assertGreater(
            food_months(world, household), 1.0,
            msg="Приказ не довёл рот двора до нормы: месяцев еды не прибавилось",
        )
        self.assertLessEqual(
            food_shortfall(world, household), EPSILON,
            msg="Приказ не покрыл месячную нужду двора",
        )
        self.assertEqual(
            choose_actions(world, household)[0], "work_plot",
            "Двор с зерном в руках всё ещё просит подачу и не пашет",
        )
