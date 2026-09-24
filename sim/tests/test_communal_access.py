"""Право доступа к общинным клеткам и общий счёт партий сбора (J3, дыра 1).

Общинная клетка (режим `waste`/`reserved_wood`) не отдаётся двору в держание:
дворы пользуются ею только по `Right.kind=common` или через `Settlement.works_tiles`,
а ёмкость клетки (`_tile_batch_cap`, 4 партии) общая на всех, у кого есть право.
Соседство даёт встречу и путь, но не экономический доступ. «Каждый двор = полная
виргата» запрещён.

Пашня/жатва держимой клетки остаётся как G3: делится только клетка по
`Right`/усадьбе, двое на одной держимой дают 4 партии — полная проверка в
`test_plot_cap_per_tile.TestPlotCapPerTile.test_two_households_share_tile_capacity`,
здесь не дублируется. Общинные поля (`tenement`) по-новому не шарится, поэтому
соль A1 не задет. Материю правило не создаёт, yield не трогает.

Проверки:
  * (a) два двора собирают с ОДНОЙ общинной клетки: суммарно ≤ 4 партий (не 8),
    выход строго меньше 2× одиночного;
  * (b) один двор на двух общинных клетках собирает с каждой в пределах капа;
  * (c) двор без доступа к чужой общинной клетке её не обрабатывает;
  * (d) регрессия соли: `v0_two_settlements` 60 мес seed 99 (script) — замковая
    соль 6.1 (не 4.0-без-transfer), дельта ≈ 0;
  * (e) граница J3: полевой `tenement` общинной клеткой сбора не считается.
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
TILE_CAP = 4
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
    """Ёмкость общинной клетки сбора — общая на всех, у кого есть доступ."""

    def test_two_households_share_one_communal_tile(self) -> None:
        """(a) Два двора на одной общинной клетке: 4 партии, не 8."""
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

        self.assertEqual(_tile_batch_cap(shared_world), TILE_CAP)
        self.assertGreaterEqual(single, 1, "Одиночный двор не собрал ничего")
        self.assertLessEqual(shared, TILE_CAP, "С клетки снято больше её ёмкости")
        self.assertLess(
            shared, 2 * single, "Второй двор удвоил выход общинной клетки"
        )
        for world in (single_world, shared_world):
            self.assertAlmostEqual(
                world.ledger.delta(world.total_matter()), 0.0, places=6
            )

    def test_one_household_two_communal_tiles_within_cap(self) -> None:
        """(b) Один двор на двух общинных клетках: с каждой — в пределах капа."""
        world = _forage_world([SALT_A])
        _grant_common_access(world, TARGET)
        _grant_common_access(world, TARGET_ALT)
        _seed_standing(world, TARGET)
        _seed_standing(world, TARGET_ALT)

        work_month(world, world.clock.date)

        first = _batches(world, TARGET)
        second = _batches(world, TARGET_ALT)
        self.assertGreaterEqual(first, 1, "Первая клетка не обработана")
        self.assertGreaterEqual(second, 1, "Вторая клетка не обработана")
        self.assertLessEqual(first, TILE_CAP, "Первая клетка перепахана сверх ёмкости")
        self.assertLessEqual(second, TILE_CAP, "Вторая клетка перепахана сверх ёмкости")
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
        """(d) Соль A1: 60 мес seed 99 (script) — 7.33, не 4.0, дельта ≈ 0.

        После ADR 0077 геометрический доступ соседних дворов к соляным лесам убран:
        сценарий не содержит `Right.common` на этих клетках, поэтому честный результат
        возвращается к измеренному потоку соли 7.33. Число не подгоняется под старый
        пин; причина — доступ по праву, а не геометрии.
        """
        world = load_scenario(TWO_SETTLEMENTS, seed=99)
        for month_index in range(1, 61):
            for entry in world.script:
                if int(entry.get("at_month", 0)) == month_index:
                    _apply_script_entry(world, entry)
            run_month(world)

        salt = world.get_stock("settlement:hill_court").amounts.get("salt", 0.0)
        self.assertGreater(salt, 0.0, "Замковая соль исчезла")
        self.assertGreater(salt, 4.0, "Замковая соль осталась 4.0 без transfer")
        self.assertAlmostEqual(salt, 7.33, delta=0.5, msg=f"соль {salt}")
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_tenement_field_is_not_communal_collection(self) -> None:
        """(e) Граница J3: полевой `tenement` — не общинная клетка сбора.

        Держимую клетку делят как в G3 (`test_plot_cap_per_tile`); общинное поле
        (works_tile соляной деревни, режим tenement) по-новому не шарится.
        """
        world = load_scenario(SCENARIO)
        field = world.tiles[COMMUNAL_FIELD]
        self.assertEqual(field.regime_id, "tenement")
        self.assertFalse(is_communal_collection_tile(field))
        self.assertTrue(is_communal_collection_tile(world.tiles[TARGET]))


if __name__ == "__main__":
    unittest.main()
