"""Кэш земель двора: инвалидация по каждому входу и сторож на сам кэш.

Задача модуля — не «быстро», а «кэш не может молча отдать прошлый месяц».
Кэш земель в `economy/labor.py` (`own_tiles`, `_feeding_tiles`) отвечает на
вопрос, который за месяц пересобирается тысячи раз ради одного и того же
ответа, поэтому он неполным ключом опасен ровно тем, что тик поедет молча.

Поэтому здесь нет ни одной проверки на секунды: стоят ПРОВЕРКИ ОТВЕТА.

1. `test_invalidation_*` — по одному тесту на КАЖДЫЙ вход, который читают
   кэшируемые функции. Для каждого: прогреть кэш, изменить вход, и получить
   ответ, равный честному обходу (`_own_tiles_scan` / `_feeding_tiles_scan`) и
   отличный от ответа до мутации. Список входов — не список хозяина, а полный
   перечень того, что функции читают; он разобран в блоке «КЭШ ЗЕМЕЛЬ ДВОРА»
   в `labor.py` и повторён здесь поимённо, чтобы расхождение ключа и теста
   было видно сразу.

2. `test_stale_cache_is_detected_by_these_checks` — МУТАЦИОННЫЙ страж: ключ
   кэша намеренно ломается (на все вопросы отвечает одна константа), и после
   этого те же самые проверки обязаны упасть. Пока тесты зелёные и на сломанном
   ключе, они ничего не охраняют.

3. `test_land_cards_die_with_their_world` и `test_land_cards_do_not_grow` —
   закон AGENTS.md §6: запись ушла в `weakref.ref`, значит в дереве обязан быть
   обход, который её чистит, и на длинном прогоне кэш обязан оставаться
   ограниченным мирами, а не расти по числу месяцев.

Ни одного числа симуляции здесь не проверяется: обе правки обязаны менять
только скорость ответа на вопрос, а не сам ответ. Сквозной обвинитель
неизменности живёт в `test_start_stand`, `test_matter_conservation` и
`test_barony_100`.
"""

from __future__ import annotations

import gc
import unittest
from dataclasses import replace
from pathlib import Path

from hillcourt.economy import labor
from hillcourt.economy.labor import (
    _feeding_tiles,
    _feeding_tiles_scan,
    _own_tiles_scan,
    own_tiles,
)
from hillcourt.ontology import Right, SimDate
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_barony_100.yml"
SEED = 4242


def _ids(tiles) -> list[str]:
    return [tile.id for tile in tiles]


def _drop_land_cards(world) -> None:
    """Сбросить кэши земель МИРА, не трогая остальные миры (чистый холодный старт)."""
    entry = labor._LAND_CARDS.get(id(world))
    if entry is not None:
        entry[0].clear()
        entry[1].clear()


class _LandFixture(unittest.TestCase):
    """Мир и двор, на котором крутятся все проверки инвалидации."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO, seed=SEED)
        self.settlement_id = "hill_court"
        self.household = next(
            h for h in self.world.households.values()
            if h.settlement_id == self.settlement_id
        )
        self.settlement = self.world.settlements[self.settlement_id]
        _drop_land_cards(self.world)

    def pick_holder(self) -> None:
        """Перейти на двор, у которого есть не-общинное право на клетку ВНЕ угодья.

        «Вне `works_tiles`» — обязательное условие подготовки: клетка, которая
        лежит ещё и в угодьях поселения, осталась бы в наделе и после отзыва
        права, и проверка инвалидации ничего бы не проверяла.
        """
        for household in sorted(self.world.households.values(), key=lambda h: h.id):
            settlement = self.world.settlements.get(household.settlement_id or "")
            if settlement is None:
                continue
            works = set(settlement.works_tiles)
            if any(
                right.holder_household_id == household.id
                and right.kind != "common"
                and right.tile_id not in works
                for right in self.world.rights.values()
            ):
                self.household = household
                self.settlement = settlement
                _drop_land_cards(self.world)
                return
        self.skipTest("сценарий не дал не-общинного права вне угодья поселения")

    def free_tile_id(self) -> str:
        """Клетка, которой у двора РЕШИТЕЛЬНО нет: не в наделе, не под правом.

        Считает надел ЧЕСТНЫМ обходом (`_own_tiles_scan`), а не кэшем, и
        намеренно НЕ трогает карточки: иначе проверка инвалидации сбросила бы
        прогретый кэш и пропустила бы сломанный ключ (так и случилось в первый
        прогон этого модуля — тест был зелёный на ключе-константе).
        """
        held = set(_ids(_own_tiles_scan(self.world, self.household)))
        held.update(self.settlement.works_tiles)
        held.add(labor._tile_id(*self.settlement.coord))
        for right in self.world.rights.values():
            if right.holder_household_id == self.household.id:
                held.add(right.tile_id)
        for tile_id in sorted(self.world.tiles):
            if tile_id not in held:
                return tile_id
        self.skipTest("в мире нет свободной клетки")

    def prime(self) -> tuple[list[str], list[str]]:
        """Прогреть кэш и вернуть ответы ДО мутации (список id надела и кормящих)."""
        return (
            _ids(own_tiles(self.world, self.household)),
            _ids(_feeding_tiles(self.world, self.household)),
        )

    def assert_matches_reference(self) -> None:
        """Ответы кэша равны честному обходу — это база каждой проверки ниже."""
        want_own = _own_tiles_scan(self.world, self.household)
        want_feed = _feeding_tiles_scan(self.world, self.household, want_own)
        got_own = own_tiles(self.world, self.household)
        got_feed = _feeding_tiles(self.world, self.household)
        self.assertEqual(
            _ids(want_own), _ids(got_own),
            "надел разошёлся с честным обходом `_own_tiles_scan`",
        )
        self.assertEqual(
            _ids(want_feed), _ids(got_feed),
            "кормящий надел разошёлся с честным обходом `_feeding_tiles_scan`",
        )

    def assert_fresh(self, before: tuple[list[str], list[str]], changed=("own",)) -> None:
        """Ответ равен честному обходу И указанный ответ сменился после мутации."""
        self.assert_matches_reference()
        got = self.prime()
        if "own" in changed:
            self.assertNotEqual(
                before[0], got[0],
                "мутация не изменила надел — проверка инвалидации ничего не проверяет",
            )
        if "feed" in changed:
            self.assertNotEqual(
                before[1], got[1],
                "мутация не изменила кормящий надел — проверка ничего не проверяет",
            )



class TestOwnTilesInvalidationPerInput(_LandFixture):
    """По одному тесту на каждый вход, который читает `own_tiles`."""

    def test_invalidation_household_changed_settlement(self) -> None:
        """Вход 1-3: `household.settlement_id` и `settlement.coord`/`works_tiles`.

        Двор переезжает в другое поселение — надел обязан стать наделом нового
        поселения (его усадьба и угодья), а не прежним.
        """
        before = self.prime()
        other = self.world.settlements["village_forest"]
        self.household.settlement_id = other.id
        self.assert_fresh(before)

    def test_invalidation_settlement_coord_moved(self) -> None:
        """Вход 3: `settlement.coord` — адрес усадьбы, первая клетка надела.

        Поселение осталось тем же, угодья те же, но усадьба переехала: первая
        клетка надела обязана смениться.
        """
        before = self.prime()
        old_home = labor._tile_id(*self.settlement.coord)
        self.settlement.coord = (7, 7)
        self.assert_fresh(before)
        self.assertIn(labor._tile_id(7, 7), _ids(own_tiles(self.world, self.household)))
        self.assertNotIn(labor._tile_id(7, 7), before[0])
        self.assertNotEqual(before[0][0], labor._tile_id(7, 7))
        self.assertTrue(before[0][0] == old_home)

    def test_invalidation_works_tiles_changed(self) -> None:
        """Вход 4: `settlement.works_tiles` — общинные угодья, и порядок тоже.

        Клетка добавлена В СЕРЕДИНУ списка: этого достаточно, потому что ответ
        зависит и от состава, и от порядка.
        """
        before = self.prime()
        extra = next(
            t.id for t in self.world.tiles.values()
            if t.id not in set(self.settlement.works_tiles)
            and t.id != labor._tile_id(*self.settlement.coord)
        )
        self.settlement.works_tiles.insert(1, extra)
        self.assert_fresh(before)
        self.assertIn(extra, _ids(own_tiles(self.world, self.household)))

    def test_invalidation_household_current_tile_changed(self) -> None:
        """Вход 5: `household.current_tile_id` — весь надел двора без поселения."""
        before = self.prime()
        self.household.settlement_id = None
        other = next(
            t.id for t in self.world.tiles.values()
            if t.id not in set(self.settlement.works_tiles)
        )
        self.household.current_tile_id = other
        self.assert_fresh(before)
        self.assertEqual(
            [other], _ids(own_tiles(self.world, self.household)),
            "двор без поселения и без прав обязан иметь надел ровно в одной клетке",
        )

    def test_invalidation_household_gained_right(self) -> None:
        """Вход 7-8: двор получил `Right` (не общинное) — клетка вошла в надел."""
        before = self.prime()
        free = self.free_tile_id()
        self.world.rights[f"right_test_{free}"] = Right(
            id=f"right_test_{free}",
            holder_household_id=self.household.id,
            tile_id=free,
            kind="tenure",
            granted_date=SimDate(1, 1, 1),
            rent_share=0.0,
        )
        self.assert_fresh(before)
        self.assertIn(free, _ids(own_tiles(self.world, self.household)))

    def test_invalidation_household_lost_right(self) -> None:
        """Вход 7-8: двор потерял `Right` — клетка вышла из надела."""
        self.pick_holder()
        right = next(
            r for r in self.world.rights.values()
            if r.holder_household_id == self.household.id and r.kind != "common"
        )
        before = self.prime()
        self.assertIn(right.tile_id, before[0], "подготовка: клетка была в наделе")
        del self.world.rights[right.id]
        self.assert_fresh(before)
        self.assertNotIn(right.tile_id, _ids(own_tiles(self.world, self.household)))

    def test_invalidation_common_right_is_not_holding(self) -> None:
        """Вход 7: `Right.kind = common` — доступ, а не надел (ADR 0060).

        Право сменило вид, ответ обязан остаться прежним: это и есть закон
        «община принадлежит всем, а не первому двору», и кэш не имеет права
        его испортить, приняв общинное право за держание.
        """
        before = self.prime()
        free = self.free_tile_id()
        self.world.rights[f"right_common_{free}"] = Right(
            id=f"right_common_{free}",
            holder_household_id=self.household.id,
            tile_id=free,
            kind="common",
            granted_date=SimDate(1, 1, 1),
            rent_share=0.0,
        )
        self.assert_matches_reference()
        self.assertEqual(before[0], _ids(own_tiles(self.world, self.household)))

    def test_invalidation_right_moved_to_another_household(self) -> None:
        """Вход 7: `holder_household_id` — право ушло другому двору.

        Число прав в мире не изменилось (ровно тот случай, где счётчик
        `len(world.rights)` промолчал бы, а надел — нет).
        """
        self.pick_holder()
        right = next(
            r for r in self.world.rights.values()
            if r.holder_household_id == self.household.id and r.kind != "common"
        )
        other = next(
            h for h in self.world.households.values()
            if h.settlement_id == self.settlement_id and h.id != self.household.id
        )
        before = self.prime()
        self.world.rights[right.id] = replace(right, holder_household_id=other.id)
        self.assert_fresh(before)
        self.assertNotIn(right.tile_id, _ids(own_tiles(self.world, self.household)))


    def test_invalidation_tile_object_replaced_in_world(self) -> None:
        """Вход 6: `world.tiles[tid]` ЗАМЕНЁН другим объектом с тем же id.

        Это тот случай, ради которого сверка идёт по `is`, а не по `tile.id`:
        id тот же, а клетка — другая, и `own_tiles` обязан вернуть новую.
        """
        before = self.prime()
        tile_id = self.settlement.works_tiles[0]
        old = self.world.tiles[tile_id]
        self.world.tiles[tile_id] = replace(old, terrain="bog")
        self.assert_matches_reference()
        got = {t.id: t for t in own_tiles(self.world, self.household)}
        self.assertIs(got[tile_id], self.world.tiles[tile_id])
        self.assertIsNot(got[tile_id], old)
        self.assertEqual(got[tile_id].terrain, "bog")
        self.assertEqual(before[0], _ids(own_tiles(self.world, self.household)))

    def test_invalidation_tile_removed_from_world(self) -> None:
        """Вход 6: клетка убрана из `world.tiles` — она обязана выпасть из надела."""
        before = self.prime()
        tile_id = self.settlement.works_tiles[0]
        saved = self.world.tiles.pop(tile_id)
        try:
            self.assert_fresh(before)
            self.assertNotIn(tile_id, _ids(own_tiles(self.world, self.household)))
        finally:
            self.world.tiles[tile_id] = saved

    def test_invalidation_missing_tile_appeared_in_world(self) -> None:
        """Вход 6: право уже было, а клетки в `world.tiles` ещё не было.

        Надел обязан РОСТИ. Это проверка на «пропущенную клетку», которую не
        поймала бы сверка только по клеткам, уже лежащим в ответе.
        """
        free = self.free_tile_id()
        template = self.world.tiles[free]
        self.world.rights[f"right_test_{free}"] = Right(
            id=f"right_test_{free}",
            holder_household_id=self.household.id,
            tile_id=free,
            kind="tenure",
            granted_date=SimDate(1, 1, 1),
            rent_share=0.0,
        )
        del self.world.tiles[free]
        try:
            before = self.prime()
            self.assertNotIn(
                free, before[0],
                "подготовка: без клетки в мире права в наделе не видно",
            )
            self.world.tiles[free] = replace(template, id=free, terrain="bog")
            self.assert_fresh(before)
            self.assertIn(free, _ids(own_tiles(self.world, self.household)))
        finally:
            self.world.tiles[free] = template
            self.world.rights.pop(f"right_test_{free}", None)

    def test_invalidation_household_left_world(self) -> None:
        """Двор ушёл, и его `id` занял другой двор.

        `left_at` в ответе не участвует, поэтому сам ответ уйти не может, —
        но карточка не имеет права достаться новому двору с тем же `id`.
        """
        before = self.prime()
        successor = replace(
            self.household,
            id=self.household.id,
            settlement_id="village_forest",
        )
        del self.world.households[self.household.id]
        self.world.households[successor.id] = successor
        self.household = successor
        self.assert_fresh(before)
        self.assertEqual(
            _ids(own_tiles(self.world, successor)),
            _ids(_own_tiles_scan(self.world, successor)),
        )

    def test_invalidation_left_household_keeps_its_own_answer(self) -> None:
        """Вход, которого нет в списке: `Household.left_at` ответ не меняет.

        Это записано отдельной проверкой, а не молчаливом умолчании: если
        когда-нибудь `own_tiles` начнёт читать `left_at` (двор ушёл — земли
        нет), тест обязан стать красным и сказать, где именно.
        """
        before = self.prime()
        self.household.left_at = SimDate(1, 2, 1)
        self.assertEqual(before[0], _ids(own_tiles(self.world, self.household)))
        self.assert_matches_reference()


class TestFeedingTilesInvalidation(_LandFixture):
    """`_feeding_tiles` поверх надела: режим клетки, рельеф клетки, каталог."""

    def test_invalidation_tile_regime_changed(self) -> None:
        """Вход 9: `tile.regime_id` — режим решает, кормит клетка или нет."""
        before = self.prime()
        self.assertGreater(len(before[1]), 0, "подготовка: кормящий надел пуст")
        tile_id = before[1][0]
        old = self.world.tiles[tile_id]
        self.world.tiles[tile_id] = replace(old, regime_id="salt_flat")
        self.assert_fresh(before, changed=("feed",))
        self.assertNotIn(tile_id, _ids(_feeding_tiles(self.world, self.household)))

    def test_invalidation_tile_terrain_changed(self) -> None:
        """Вход 9: `tile.terrain` — солончак не кормит (ADR 0108, 0124)."""
        before = self.prime()
        self.assertGreater(len(before[1]), 0, "подготовка: кормящий надел пуст")
        tile_id = before[1][0]
        old = self.world.tiles[tile_id]
        self.world.tiles[tile_id] = replace(old, terrain="salt_flat")
        self.assert_fresh(before, changed=("feed",))
        self.assertNotIn(tile_id, _ids(_feeding_tiles(self.world, self.household)))

    def test_invalidation_catalog_object_replaced(self) -> None:
        """Вход 11: `world.catalogs` подменён ДРУГИМ объектом с другим содержимым.

        Содержимое нового набора — с флагом `feeds_household`, снятым у первого
        режима надела, поэтому ответ обязан сжаться. Проверка ловит обмен
        карточек между мирами, а не сам факт «каталогов стало два».
        """
        import copy

        before = self.prime()
        self.assertGreater(len(before[1]), 0, "подготовка: кормящий надел пуст")
        regime_id = self.world.tiles[before[1][0]].regime_id
        catalogs = copy.deepcopy(self.world.catalogs)
        catalogs.land_regimes[regime_id].feeds_household = False
        self.world.catalogs = catalogs
        self.assert_fresh(before, changed=("feed",))
        self.assertNotIn(before[1][0], _ids(_feeding_tiles(self.world, self.household)))

    def test_invalidation_land_regime_flag_in_catalog(self) -> None:
        """Вход 10: каталог тот же объект, а режим на месте перестал кормить.

        Этот тест ПЕРВЫМ поймал дыру: сверки `id(world.catalogs)` на правку
        режима на месте не хватало, и кэш отдавал прошлый месяц. Теперь флаг
        режима входит в ключ, поэтому проверка обязана быть зелёной — и остаётся
        границей: если `feeds_household` снова выпадет из ключа, она покраснеет.
        """
        before = self.prime()
        self.assertGreater(len(before[1]), 0, "подготовка: кормящий надел пуст")
        regime_id = self.world.tiles[before[1][0]].regime_id
        regime = self.world.catalogs.land_regimes[regime_id]
        old_flag = regime.feeds_household
        regime.feeds_household = not old_flag
        try:
            self.assert_fresh(before, changed=("feed",))
        finally:
            regime.feeds_household = old_flag
        self.assert_matches_reference()



class TestStaleCacheIsDetected(_LandFixture):
    """МУТАЦИОННЫЙ страж: сломанный ключ обязан ронять эти же проверки.

    Пока тесты инвалидации зелёные и на ключе, который отвечает одной
    константой, они не охраняют ничего. Здесь ключ ломается намеренно, и
    каждая проверка обязана после этого упасть.
    """

    def test_stale_cache_is_detected_by_these_checks(self) -> None:
        """Ключ-константа: и смена поселения, и выдача права должны отдать прошлый месяц."""
        self.addCleanup(setattr, labor, "_own_tiles_key", labor._own_tiles_key)
        self.addCleanup(setattr, labor, "_feeding_tiles_key", labor._feeding_tiles_key)

        labor._own_tiles_key = lambda world, household: ("sabotaged",)
        labor._feeding_tiles_key = lambda world, own: ("sabotaged",)
        self.prime()
        self.household.settlement_id = self.world.settlements["village_forest"].id
        self.assertNotEqual(
            _ids(own_tiles(self.world, self.household)),
            _ids(_own_tiles_scan(self.world, self.household)),
            "сломанный ключ `own_tiles` не был замечен: проверки инвалидации "
            "не охраняют кэш",
        )

        self.household.settlement_id = self.settlement_id
        _drop_land_cards(self.world)
        labor._own_tiles_key = lambda world, household: ("sabotaged",)
        labor._feeding_tiles_key = lambda world, own: ("sabotaged",)
        self.prime()
        self.household.settlement_id = "village_forest"
        self.assertNotEqual(
            _ids(_feeding_tiles(self.world, self.household)),
            _ids(
                _feeding_tiles_scan(
                    self.world, self.household, _own_tiles_scan(self.world, self.household)
                )
            ),
            "сломанный ключ `_feeding_tiles` не был замечен",
        )



class TestLandCacheIsBoundedAndDies(_LandFixture):
    """Закон AGENTS.md §6: запись в кэше обязана чиститься, а не расти."""

    def test_land_cards_die_with_their_world(self) -> None:
        """Карточки уходят вместе с миром: колбэк `weakref.ref` выбрасывает ключ.

        Без этого новый мир получил бы надел прежнего — кэш, висящий на
        переиспользованном `id()`, и тик создал бы материю из ничего.
        """
        world = load_scenario(SCENARIO, seed=SEED)
        household = next(iter(world.households.values()))
        own_tiles(world, household)
        _feeding_tiles(world, household)
        key = id(world)
        self.assertIn(key, labor._LAND_CARDS, "карточки не завелись")
        self.assertIsNotNone(labor._LAND_CARDS[key][2]())
        self.assertTrue(labor._LAND_CARDS[key][0], "карточка надела пуста — кэш мёртвый")

        del world
        gc.collect()
        self.assertNotIn(
            key, labor._LAND_CARDS,
            "карточки пережили свой мир: нужен обход, который их чистит",
        )

    def test_land_cards_reject_entry_of_another_world(self) -> None:
        """Вторая страховка: на чтении сверяется `ref() is world`.

        Подсаживаем карточки ЧУЖОГО (но живого) мира под тем же ключом — это
        ровно то, что увидел бы мир, попавший на переиспользованный `id`.
        """
        world = load_scenario(SCENARIO, seed=SEED)
        other = load_scenario(SCENARIO, seed=SEED)
        household = next(iter(world.households.values()))
        key = id(world)
        own_tiles(world, household)
        labor._LAND_CARDS[key] = ({}, {}, weakref_ref(other))
        self.assertIsNot(labor._LAND_CARDS[key][2](), world)
        _drop_land_cards(world)
        self.assertEqual(
            _ids(own_tiles(world, household)),
            _ids(_own_tiles_scan(world, household)),
            "кэш поверил карточкам чужого мира: сверка `ref() is world` убрана",
        )
        del other
        gc.collect()

    def test_land_cards_do_not_grow_over_a_long_run(self) -> None:
        """Кэш ограничен мирами, а не растёт по числу месяцев и дворов.

        Прогон 24 месяца: если бы карточка копилась на каждый тик, её размер
        рос бы линейно. Змеряется ЧИСЛО КЛЮЧЕЙ (миров) и ЧИСЛО КАРТОЧЕК на
        двор, а не время.
        """
        from hillcourt.engine.tick import run_month
        from hillcourt.runner import _apply_script_entry

        gc.collect()
        keys_before = set(labor._LAND_CARDS)
        world = load_scenario(SCENARIO, seed=SEED)
        script = list(world.script)
        households = len(world.households)
        sizes = []
        for month in range(1, 25):
            for entry in script:
                if int(entry.get("at_month", 0)) == month:
                    _apply_script_entry(world, entry)
            run_month(world)
            entry = labor._LAND_CARDS.get(id(world))
            if entry is not None:
                sizes.append((len(entry[0]), len(entry[1])))
        self.assertTrue(sizes, "кэш земель не завелся за 24 месяца")
        gc.collect()
        new_keys = set(labor._LAND_CARDS) - keys_before
        self.assertEqual(
            len(new_keys), 1,
            f"прогон 24 месяцев завёл {len(new_keys)} миров в кэше: карточки не чистятся",
        )
        self.assertLessEqual(
            max(sizes)[0], households,
            f"карточек надела {max(sizes)[0]} при {households} дворах: кэш растёт",
        )
        self.assertLessEqual(
            max(sizes)[1], households,
            f"карточек кормящего надела {max(sizes)[1]} при {households} дворах",
        )
        self.assertEqual(
            sizes[-1], sizes[len(sizes) // 2],
            "размер кэша рос от месяца к месяцу — это утечка, а не кэш",
        )
        self.assertGreater(max(sizes)[0], 0, "карточек нет вовсе — кэш не работает")
        del world
        gc.collect()


    def test_other_worlds_keep_their_own_cards(self) -> None:
        """Два мира не делят карточки: сброс одного не трогает другой."""
        first = load_scenario(SCENARIO, seed=SEED)
        second = load_scenario(SCENARIO, seed=SEED)
        h1 = next(iter(first.households.values()))
        h2 = next(iter(second.households.values()))
        own_tiles(first, h1)
        own_tiles(second, h2)
        _drop_land_cards(first)
        self.assertTrue(labor._LAND_CARDS[id(second)][0], "сброс чужого мира унёс его карточки")
        self.assertFalse(labor._LAND_CARDS[id(first)][0])
        del first, second, h1, h2
        gc.collect()


def weakref_ref(obj):
    import weakref
    return weakref.ref(obj)


if __name__ == "__main__":
    unittest.main()
