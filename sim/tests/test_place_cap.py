"""Механический кап места: сельский гекс 5, urban-гекс 20.

**Уточнение 2026-09 (ADR 0207).** Баронство пересобрано: 600 ЖИТЕЛЕЙ и 150 дворов
вместо 600 дворов, и файл сценария сознательно сажает **12** городских дворов на
квартал («Занято 12 городских дворов на квартал», шапка `v0_barony_100.yml`).
Прежняя проверка утверждала не закон, а ЗАПОЛНЕННОСТЬ ДАННЫХ: `household_count ==
20` на городском гексе. Это умершее утверждение — числа сдвинулись законно, а
кап (20/5, ADR 0083/0132) остался прежним.

Закон, который проверка теперь утверждает, — тот, что в её имени: **кап выбирается
по месту, а не по числу домов в данных**. То есть:
  * кап 20 на urban-гексе и 5 на сельском берётся из МАРКЕРА клетки, а не из
    того, сколько дворов в неё положили;
  * занятость данных не превышает кап — это проверка на САМОМ сценарии, и она
    ловит подмену константы;
  * посадка сверх капа — `PermissionError`, а не «поместилось».
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.tile_view import (
    URBAN_MAX_HOUSEHOLDS,
    can_settle,
    household_count,
    settle_household,
    tile_max_households,
)
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_barony_100.yml"
URBAN_TILE = "t_45_50"
# Сельский гекс под посадку. Прежний `t_11_17` — `regime_id: waste`, то есть
# `_has_dwelling_place` (`tile_view.py:428-434`) запрещает на нём поселение
# независимо от капа; проверка капа не должна стоять на клетке, непригодной для
# посадки. Этот гекс — `tenement`, на нём посадка законна и кап равен 5.
RURAL_TILE = "t_29_60"


class TestPlaceCap(unittest.TestCase):
    """Кап выбирается по месту, а не по числу домов в данных."""

    def test_rural_and_urban_caps_are_mechanical(self) -> None:
        world = load_scenario(SCENARIO, seed=1729)
        # Кап — свойство МЕСТА. Он не зависит от того, сколько дворов в данных.
        self.assertEqual(tile_max_households(world, URBAN_TILE), URBAN_MAX_HOUSEHOLDS)
        self.assertEqual(URBAN_MAX_HOUSEHOLDS, 20)
        urban = household_count(world, URBAN_TILE)
        self.assertGreater(urban, 0, "Городской квартал пуст — проверять нечего")
        self.assertLessEqual(
            urban, URBAN_MAX_HOUSEHOLDS,
            "Занятость данных превысила кап: проверка наполнения потеряла смысл",
        )
        self.assertEqual(tile_max_households(world, RURAL_TILE), 5)
        self.assertTrue(can_settle(world, RURAL_TILE))

    def test_urban_accepts_twenty_and_rejects_twenty_first(self) -> None:
        """Ровно 20 дворов помещаются, двадцать первое — отказ.

        Стенд собирается САМИМ тестом: кап читается из закона, недостающие дворы
        досаживаются, и только потом проверяется отказ. Прежняя версия брала 20
        дворов из данных сценария, то есть проверяла не кап, а файл сценария —
        и краснела всякий раз, когда ADR 0207 пересобирал баронство.
        """
        world = load_scenario(SCENARIO, seed=1729)
        cap = tile_max_households(world, URBAN_TILE)
        self.assertEqual(cap, 20)

        # Досаживаем клетку до капа ДВОРЫ: наполнение — дело теста, а не данных.
        donors = [
            household
            for household in world.households.values()
            if household.left_at is None
            and household.current_tile_id not in {URBAN_TILE, RURAL_TILE}
        ]
        shortfall = cap - household_count(world, URBAN_TILE)
        self.assertGreater(len(donors), shortfall, "Не хватает дворов, чтобы набить кап")
        for household in donors[:shortfall]:
            world.households[household.id].current_tile_id = URBAN_TILE
        self.assertEqual(household_count(world, URBAN_TILE), cap)
        self.assertFalse(can_settle(world, URBAN_TILE), "Полная клетка вправе принять ещё")

        # Двадцать первое — отказ загрузчика, а не «поместилось».
        extra = donors[shortfall]
        world.households[extra.id].current_tile_id = RURAL_TILE
        with self.assertRaises(PermissionError):
            settle_household(world, extra.id, URBAN_TILE)


if __name__ == "__main__":
    unittest.main()
