"""Право доступа к общинным клеткам сбора. Капа партий на клетке НЕТ (ADR 0173 п. 2).

Общинная клетка сбора (режимы `waste`/`reserved_wood`) не отдаётся двору в
держание: дворы пользуются ею только по `Right.kind=common` или через
`Settlement.works_tiles` (`legal/regimes.py:has_access_to_communal_tile`,
`is_communal_collection_tile`). Соседство даёт встречу и путь, но не
экономический доступ.

Что отменено (ADR 0132 п. 1, отменён ADR 0173 п. 2 и ADR 0137): общий кап **4
партии на клетку** и делёж выхода поровну между дворами, имеющими доступ.
Символа `_communal_collection_shared` в коде нет (0 совпадений в `sim/src`), а
`labor._tile_batch_cap` вызывается ровно из одного места — `economy/livestock.py:615`,
то есть только для сена тяглом (кап СТОЙЛА, ADR 0050/0055). Это ДРУГОЕ число.

Живой закон режима 2 проверяется сравнениями, а не константой:
  * (a) второй двор с доступом УВЕЛИЧИВАЕТ общий выход клетки, а не делит его
    поровну и не упирается в кап;
  * (b) месяц двора ограничен его трудом: две общинные клетки не удваивают месяц;
  * (c) двор без доступа к чужой общинной клетке её не обрабатывает;
  * (d) регрессия соли: `v0_two_settlements` 60 мес seed 99 (script) — ненулевая
    выгрузка соли в замок, дельта ≈ 0;
  * (e) граница J3: полевой `tenement` — не общинная клетка сбора.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy.labor import _tile_batch_cap, work_month
from hillcourt.engine.tick import run_month
from hillcourt.legal.regimes import (
    has_access_to_communal_tile,
    is_communal_collection_tile,
)
from hillcourt.ontology import Right
from hillcourt.runner import _apply_script_entry
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
TWO_SETTLEMENTS = ROOT / "design" / "scenarios" / "v0_two_settlements.yml"

FORAGE = "forage_wild"
EPSILON = 1e-6

SALT_A = "hh_salt_01"
SALT_B = "hh_salt_02"
# Соляная деревня стоит на t_08_05; её леса — t_07_05 и t_08_04 (общинные,
# режим reserved_wood, держания по праву нет).
TARGET = "t_07_05"
TARGET_ALT = "t_08_04"
# Лес у холма, недоступный соляным дворам: ни земля поселения, ни сосед.
FAR = "t_00_01"
COMMUNAL_FIELD = "t_06_05"  # полевой works_tile соляной деревни (tenement)


def _grant_common_access(world, tile_id: str) -> None:
    """Выдать явное communal-право для тестового сценария."""
    right_id = f"right_test_common_{tile_id}"
    world.rights[right_id] = Right(
        id=right_id,
        holder_household_id="salt_village",
        tile_id=tile_id,
        kind="common",
        granted_date=world.clock.date,
        rent_share=0.0,
    )


def _seed_standing(world, tile_id: str, firewood: float = 1000.0, grain: float = 1000.0) -> None:
    """Засеять стоячую материю клетки через внешний приход (дельта остаётся 0)."""
    stock = world.get_stock(world.tiles[tile_id].standing_stock_id)
    world.ledger.external_in(
        stock, "firewood", firewood, "test_seed_standing", None, world.clock.date
    )
    world.ledger.external_in(
        stock, "grain", grain, "test_seed_standing", None, world.clock.date
    )


def _batches(world, tile_id: str) -> int:
    """Число партий сбора с клетки: одна партия `forage_wild` = одна проводка дров."""
    stock_id = world.tiles[tile_id].standing_stock_id
    return sum(
        1
        for entry in world.ledger.entries
        if entry.kind == "transfer"
        and entry.src_id == stock_id
        and entry.reason == FORAGE
        and entry.good == "firewood"
    )


def _household_batches(world, household_id: str) -> int:
    """Партии сбора, доставленные конкретному двору (выход идёт через sink)."""
    stock_id = world.households[household_id].stock_id
    return sum(
        1
        for entry in world.ledger.entries
        if entry.kind == "process"
        and entry.reason == FORAGE
        and entry.good == "firewood"
        and entry.dst_id == stock_id
    )


def _forage_world(household_ids: list[str]):
    """Мир, где только заданные дворы собирают по соседним угодьям, рук вдоволь."""
    world = load_scenario(SCENARIO)
    for household in world.households.values():
        household.labor_days = 0.0
    for household_id in household_ids:
        household = world.households[household_id]
        household.main_action = "forage_adjacent"
        household.minor_action = "idle_repair"
        household.labor_days = 400.0
    return world


class TestCommunalAccess(unittest.TestCase):
    """Общинная клетка сбора — доступ по праву, а не кап партий."""

    def test_second_household_increases_the_common_output(self) -> None:
        """(a) Два двора с правом с одной клетки дают больше, чем один.

        Отменённая норма («4 партии на клетку, делёж поровну») не возвращается:
        выход делится по числу ДВОРОВ С ДОСТУПОМ, а не поровну, и капом стойла
        (`_tile_batch_cap`) не режется — это число живёт только в
        `livestock.py:615`.
        """
        single_world = _forage_world([SALT_A])
        _grant_common_access(single_world, TARGET)
        _seed_standing(single_world, TARGET)
        work_month(single_world, single_world.clock.date)
        single = _batches(single_world, TARGET)

        shared_world = _forage_world([SALT_A, SALT_B])
        _grant_common_access(shared_world, TARGET)
        _seed_standing(shared_world, TARGET)
        work_month(shared_world, shared_world.clock.date)
        shared = _batches(shared_world, TARGET)

        self.assertGreaterEqual(single, 1, "Одиночный двор не собрал ничего")
        self.assertGreater(
            shared,
            single,
            f"Второй двор с правом не увеличил выход общинной клетки: {shared}"
            f" против {single} — выход делится поровну (отменено ADR 0173 п. 2)",
        )
        for household_id in (SALT_A, SALT_B):
            self.assertGreaterEqual(
                _household_batches(shared_world, household_id),
                1,
                f"{household_id}: право есть, а сбора нет",
            )
        self.assertEqual(
            shared,
            _household_batches(shared_world, SALT_A)
            + _household_batches(shared_world, SALT_B),
            "Выход общинной клетки не разошёлся по дворам",
        )
        self.assertGreater(
            shared,
            _tile_batch_cap(shared_world),
            "Общинный сбор упирается в кап стойла — это разные числа (ADR 0143 п. 2)",
        )
        for world in (single_world, shared_world):
            self.assertAlmostEqual(
                world.ledger.delta(world.total_matter()), 0.0, places=6
            )

    def test_one_household_month_is_labor_not_capacity(self) -> None:
        """(b) Две общинные клетки не удваивают месяц одного двора.

        Право доступа к двум клеткам не даёт второго месяца: труд двора уходит
        на первую клетку до конца бюджета, и вторая ждёт следующего месяца.
        Проверяется сравнением с тем же двором на одной клетке.
        """
        one_tile = _forage_world([SALT_A])
        _grant_common_access(one_tile, TARGET)
        _seed_standing(one_tile, TARGET)
        work_month(one_tile, one_tile.clock.date)
        single = _batches(one_tile, TARGET)

        world = _forage_world([SALT_A])
        _grant_common_access(world, TARGET)
        _grant_common_access(world, TARGET_ALT)
        _seed_standing(world, TARGET)
        _seed_standing(world, TARGET_ALT)
        work_month(world, world.clock.date)

        total = _batches(world, TARGET) + _batches(world, TARGET_ALT)
        self.assertGreaterEqual(single, 1, "Двор на одной клетке не собрал ничего")
        self.assertEqual(
            total,
            single,
            "Вторая общинная клетка удвоила месяц двора: труд не резиновый",
        )
        self.assertGreater(
            total,
            _tile_batch_cap(world),
            "Общинный сбор упирается в кап стойла — это разные числа (ADR 0143 п. 2)",
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_no_access_means_no_collection(self) -> None:
        """(c) Двор без доступа к чужой общинной клетке её не обрабатывает."""
        world = _forage_world([SALT_A])
        _seed_standing(world, FAR)
        salt = world.households[SALT_A]

        self.assertFalse(
            has_access_to_communal_tile(world, salt, world.tiles[FAR]),
            "Соляной двор получил доступ к лесу у холма",
        )
        work_month(world, world.clock.date)
        self.assertEqual(_batches(world, FAR), 0, "Чужая клетка всё же обработана")

    def test_salt_regression_two_settlements_seed_99(self) -> None:
        """(d) Соль A1: 60 мес seed 99 (script) — ненулевой поток, дельта ≈ 0."""
        world = load_scenario(TWO_SETTLEMENTS, seed=99)
        for month_index in range(1, 61):
            for entry in world.script:
                if int(entry.get("at_month", 0)) == month_index:
                    _apply_script_entry(world, entry)
            run_month(world)

        salt = world.get_stock("settlement:hill_court").amounts.get("salt", 0.0)
        self.assertGreater(salt, 4.0, "Замковая соль осталась 4.0 без transfer")
        unload_total = sum(
            entry.amount
            for entry in world.ledger.entries
            if entry.good == "salt" and entry.reason == "caravan_unload"
        )
        self.assertGreater(unload_total, 0.0, "Соляные обозы не привезли соль")
        self.assertAlmostEqual(
            salt,
            4.0 + unload_total,
            places=6,
            msg=f"соль {salt}, выгрузка {unload_total}",
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_tenement_field_is_not_communal_collection(self) -> None:
        """(e) Граница J3: полевой `tenement` — не общинная клетка сбора.

        Общинная клетка СБОРА — это `waste`/`reserved_wood`; пашня `tenement`
        сбора не даёт даже при `works_tiles`. Пашня идёт по первому режиму
        (ёмкость растёт от работников, ADR 0137) и проверяется в
        `test_communal_capacity`.
        """
        world = load_scenario(SCENARIO)
        field = world.tiles[COMMUNAL_FIELD]
        self.assertEqual(field.regime_id, "tenement")
        self.assertFalse(is_communal_collection_tile(field))
        self.assertTrue(is_communal_collection_tile(world.tiles[TARGET]))


if __name__ == "__main__":
    unittest.main()
