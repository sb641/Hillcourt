"""Производительность тика: асимптотика, а не «быстро», и сторож на оптимизации.

Задача модуля — поймать регрессию ФОРМЫ, а не числа секунд. Секунды на общей
машине не воспроизводимы (load average выше числа ядер), а вот «сколько раз
функция позвана за месяц» и «растёт ли стоимость с размером мира» — воспроизводимы
полностью. Поэтому здесь нет ни одной проверки на время: есть проверки на
ЧАСТОТУ и на АСИМПТОТИКУ.

ЧТО ОХРАНЯЕТСЯ (и почему это не «тюнинг под сценарий»)

1. `TestLazyHeads` — `soil.cell_yield_factor` и `tile_yield_factor` обязаны НЕ
   читать число голов у двора, когда на клетке нет навоза: закон навоза
   безусловен (`нет навоза → множитель 1.0`), и число голов не имеет ни одного
   наблюдаемого последствия. Оптимизация ленивая, а не «мир без скота»:
   `test_heads_are_still_read_when_manure_exists` требует, чтобы на
   удобренной клетке число голов ЧИТАЛОСЬ. Если оптимизацию переделают в
   «не считать всегда», этот тест краснеет — вместе с законом.

2. `TestHerdSumNotScannedPerTile` — сумма стойловых голов поселения
   (`settlement_livestock_units`) обходит всех членов поселения. Её нельзя
   звать на каждую клетку-кандидат: это O(клетки × члены) на месяц. Тест
   требует, чтобы на клетке БЕЗ навоза сумма не считалась ВООБЩЕ, и падает,
   если вернуть жадный подсчёт.

3. `TestTradeCapIsCountedOnce` — `small_livestock_cap` зависит от надела и
   `world.rights`, но не от товара и не от сделок. В `_trade_livestock` он обязан
   считаться не чаще раза на двор кластера, а не по разу на пару
   (товар, двор). При 13 товарах это 13× меньше работы. Мутируемая проверка:
   если убрать ленивую раздачу капа, тест краснеет.

4. `TestGrowthIsNotQuadratic` — асимптотика. Стоимость месяца обязана расти
   примерно ЛИНЕЙНО по числу дворов. Проверка сравнивает наклон на 100 и на 300
   дворах: если рост стал сверхлинейным (например, появился квадрат по
   `world.rights` или по членам поселения), тест краснеет. Это ровно та поломка,
   которую не видно на 150 дворах, — и ради неё модуль написан.

5. `TestCachesDieWithTheirWorld` — закон AGENTS.md §6: запись ушла в
   `weakref.ref`, значит в дереве обязан быть обход, который её чистит. Мир без
   обхода = висящий на переиспользованном `id()` кэш = тик создаёт материю из
   ничего. Здесь это проверяется и МУТИРУЕТСЯ: тест с убранной очисткой обязан
   краснеть (см. `test_mutation_without_cleanup_is_caught`).

Ни одного числа симуляции здесь не проверяется: все правки обязаны менять только
скорость ответа на вопрос, а не сам ответ. Сквозной обвинитель неизменности
живёт в `test_matter_conservation`, `test_start_stand`, `test_barony_100`, а
побитовое совпадение `state_hash` — в ADR 0224.
"""

from __future__ import annotations

import gc
import time
import unittest
from pathlib import Path

from hillcourt.economy import exchange, labor, livestock, soil
from hillcourt.engine.tick import run_month
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_barony_100.yml"
SEED = 4242


def _warm(world, months: int = 2) -> None:
    for _ in range(months):
        run_month(world)


def _month_seconds(world, months: int = 2) -> float:
    """Секунд на месяц. ЧИСТО ИЗМЕРЕНИЕ, без cProfile: нужен сам тик."""
    t0 = time.process_time()
    for _ in range(months):
        run_month(world)
    return (time.process_time() - t0) / months


class _Counter:
    """Счётчик вызовов функции, подставляемый вместо неё в модуль."""

    def __init__(self, module, name: str) -> None:
        self.module = module
        self.name = name
        self.original = getattr(module, name)
        self.count = 0

    def __enter__(self) -> "_Counter":
        def wrapper(*args, **kwargs):
            self.count += 1
            return self.original(*args, **kwargs)

        setattr(self.module, self.name, wrapper)
        return self

    def __exit__(self, *exc) -> None:
        setattr(self.module, self.name, self.original)

    def per_month(self, months: int) -> float:
        return self.count / months


class TestLazyHeads(unittest.TestCase):
    """Навоз — условие применения; без него число голов не читается вовсе."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO, seed=SEED)
        _warm(self.world)
        self.household = next(
            h
            for h in sorted(self.world.households.values(), key=lambda h: h.id)
            if h.settlement_id == "hill_court"
        )
        self.tile = labor.own_tiles(self.world, self.household)[0]

    def _clear_manure(self) -> None:
        standing = self.world.get_stock(self.tile.standing_stock_id)
        standing.amounts.pop("manure", None)

    def test_heads_are_still_read_when_manure_exists(self) -> None:
        """Страховка от «оптимизации» в духе «не считать головы никогда».

        На удобренной клетке число голов ОБЯЗАН читаться: смена `worker` на
        другого двора обязана менять множитель. Если оптимизацию переделали в
        «головы не считаются никогда», оба числа сравняются и тест краснеет.

        Голова берётся у ДВУХ разных дворов, потому что в баронстве скота нет
        (`test_no_draft_animals_in_the_barony`), и без выставленного скота оба
        множителя были бы 1.0 — тест прошёл бы вхолостую.
        """
        other = next(
            h
            for h in sorted(self.world.households.values(), key=lambda h: h.id)
            if h.settlement_id is not None and h.id != self.household.id
        )
        _give_ox(self.world, self.household, 4.0)
        _give_ox(self.world, other, 12.0)
        tile = self.tile
        self.world.get_stock(tile.standing_stock_id).amounts["manure"] = 10.0
        few = soil.cell_yield_factor(self.world, self.household, tile)
        many = soil.cell_yield_factor(self.world, other, tile)
        self.assertGreater(
            many,
            few,
            "навоз на клетке не отличил дворы по поголовью — число голов не читается, "
            "ленивость превратилась в «не считать никогда»",
        )

    def test_no_draft_animals_in_the_barony(self) -> None:
        """Предпосылка замера: в баронстве нет тягла, поэтому головы нулевые.

        Именно поэтому «навоз есть, а голов нет» — случай, который ленивость
        ОБЯЗАНА обрабатывать правильно (считать головы, раз навоз есть). Если
        скот появится, переснимется кривая роста.
        """
        heads = max(
            soil.worker_livestock_units(self.world, h)
            for h in self.world.households.values()
            if h.left_at is None
        )
        self.assertEqual(heads, 0.0, "в баронстве появился скот — пересними замеры")

    def test_manure_factor_scales_with_the_herd(self) -> None:
        """Число голов влияет на величину прибавки — значит, оно читается."""
        tile = self.tile
        self.world.get_stock(tile.standing_stock_id).amounts["manure"] = 10.0
        none_herd = soil.tile_manure_factor(self.world, tile, 0.0)
        big_herd = soil.tile_manure_factor(self.world, tile, 30.0)
        self.assertEqual(none_herd, 1.0)
        self.assertGreater(big_herd, none_herd)

    def test_cell_yield_factor_does_not_sum_the_herd_on_bare_tile(self) -> None:
        """На клетке БЕЗ навоза сумма голов поселения не считается ни разу.

        `settlement_livestock_units` обходит всех членов поселения — это
        O(клетки × члены) на месяц. Возврат жадного подсчёта роняет тест.
        """
        self._clear_manure()
        with _Counter(soil, "settlement_livestock_units") as counter:
            soil.cell_yield_factor(self.world, self.household, self.tile)
        self.assertEqual(
            counter.count,
            0,
            "на клетке без навоза число голов посчитано — ленивость потеряна",
        )

    def test_tile_yield_factor_does_not_sum_the_herd_on_bare_tile(self) -> None:
        """То же для домена (`tile_yield_factor`): та же прибавка, тот же закон."""
        tile = next(
            t
            for t in sorted(self.world.tiles.values(), key=lambda t: t.id)
            if t.regime_id == "demesne"
        )
        self.world.get_stock(tile.standing_stock_id).amounts.pop("manure", None)
        with _Counter(soil, "settlement_livestock_units") as counter:
            soil.tile_yield_factor(self.world, tile, self.household)
        self.assertEqual(
            counter.count, 0, "домен без навоза посчитал сумму голов поселения"
        )

    def test_no_manure_anywhere_in_the_barony(self) -> None:
        """Зафиксировать ПРЕДПОСЫЛКУ замера, чтобы её нельзя было потерять молча.

        Если в баронстве появится навоз, прежние замеры перестанут быть
        применимы к этой карте — и об этом должен узнать тот, кто смотрит на
        числа, а не тот, кто случайно сломал оптимизацию.
        """
        with_manure = [
            tile.id
            for tile in self.world.tiles.values()
            if self.world.get_stock(tile.standing_stock_id).amounts.get("manure", 0.0)
            > 1e-9
        ]
        self.assertEqual(
            with_manure, [], "в баронстве появился навоз — пересними кривую роста"
        )


class TestTradeCapIsCountedOnce(unittest.TestCase):
    """Кап мелкого скота не зависит от товара: 13 товаров — один расчёт."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO, seed=SEED)
        _warm(self.world)

    def test_cap_not_recomputed_per_good(self) -> None:
        """В `_trade_livestock` кап считается не чаще раза на ДВОР, не на пару.

        Один расчёт капа на двор вместо одного на (товар, двор) — это 13× меньше
        работы при 13 товарах `LIVESTOCK_PRICE_GRAIN`. Возврат жадного подсчёта
        (без раздачи `caps`) роняет тест.

        Порог структурный, а не «число секунд»: капов должно быть заметно меньше,
        чем пар (товар, двор) — those считаются как `goods × пары, дошедшие до
        капа`. Считается ровно то, что функция делает, поэтому тест переживает
        и смену числа товаров, и смену размера мира.
        """
        months = 2
        with _Counter(livestock, "small_livestock_cap") as cap_counter:
            with _Counter(livestock, "has_animal_access") as access_counter:
                for _ in range(months):
                    run_month(self.world)
        cap_calls = cap_counter.per_month(months)
        access_calls = access_counter.per_month(months)
        self.assertGreater(access_calls, cap_calls)
        # Наивная реализация звала бы кап на КАЖДУЮ проверку доступа, а их на
        # товар больше одного на двор. Требуем: капов заметно меньше проверок.
        self.assertLess(
            cap_calls,
            access_calls * 0.5,
            f"кап посчитан {cap_calls:.0f} раз/мес при {access_calls:.0f} проверках "
            "доступа — раздача капа на двор убрана",
        )

    def test_cap_does_not_depend_on_the_good(self) -> None:
        """Свойство, на котором держится раздача капа: ответ не зависит от товара.

        Если бы кап зависел от покупаемого товара, раздача одного значения на
        весь кластер была бы подменой закона. Тест фиксирует инвариант.
        """
        household = next(
            h
            for h in sorted(self.world.households.values(), key=lambda h: h.id)
            if h.settlement_id == "hill_court"
        )
        first = livestock.small_livestock_cap(self.world, household)
        for _ in range(3):
            self.assertEqual(
                livestock.small_livestock_cap(self.world, household),
                first,
                "кап зависит от предыдущих вызовов — он не чистая функция мира",
            )

    def test_access_by_cap_matches_the_general_answer(self) -> None:
        """`_access_by_cap` (кап готов) == `has_animal_access` (кап считается)."""
        for household in list(sorted(self.world.households.values(), key=lambda h: h.id))[:12]:
            if household.left_at is not None:
                continue
            stock = self.world.get_stock(household.stock_id)
            cap = livestock.small_livestock_cap(self.world, household)
            self.assertEqual(
                livestock.has_animal_access(self.world, household, stock, cap),
                livestock._access_by_cap(stock, cap),
            )
            self.assertEqual(
                livestock.has_animal_access(self.world, household, stock),
                livestock._access_by_cap(stock, cap),
                "обходной путь разошёлся с быстрым — числа поедут",
            )


class TestGrowthIsNotQuadratic(unittest.TestCase):
    """Асимптотика по числу дворов: наклон на 100 и на 300 должен совпадать.

    150 и 200 дворов — ФИКСТУРЫ ЗАМЕРА, а не константы дизайна: игрок сам
    настраивает население. Поэтому проверка не «уложиться в 1.83 с», а «не
    сломать форму»: сверхлинейный рост не виден на одной фикстуре и виден
    сразу на двух.
    """

    def _households(self, count: int):
        """Собрать мир на `count` дворов изменением СЧЁТЧИКА группы сценария.

        Правится число, а не список: карта, угодья и рельеф остаются базовыми,
        поэтому сравниваются миры одинаковой формы, различающиеся населением.
        Кап гекса — 20 дворов (`_CITY_HOUSEHOLD_CAP`), поэтому сверх 160 дворов
        замку нужны ДОПОЛНИТЕЛЬНЫЕ кварталы: иначе загрузчик честно откажет, и
        фикстура врала бы о пределе, которого в игре нет.
        """
        text = SCENARIO.read_text()
        base = 'name: "Квартальный двор", count: 86,'
        self.assertIn(base, text)
        text = text.replace(
            base, f'name: "Квартальный двор", count: {86 + (count - 150)},'
        )
        need = -(-count // 20) - 8
        if need > 0:
            extra = [[44, 50], [48, 50], [43, 51], [49, 51], [43, 50], [49, 50],
                     [44, 52], [48, 52], [43, 52], [49, 52]]
            self.assertLessEqual(need, len(extra), "не хватило кварталов в пуле")
            for field in ("quarter_tiles", "household_tiles"):
                marker = f"    {field}: "
                start = text.index(marker) + len(marker)
                end = text.index("\n", start)
                current = eval(text[start:end])  # noqa: S307 — свой же YAML
                self.assertEqual(
                    len(current), 8, f"у замка не 8 кварталов — правь фикстуру, а не мир"
                )
                body = (
                    "["
                    + ", ".join(str(list(t)) for t in current + extra[:need])
                    + "]"
                )
                text = text[:start] + body + text[end:]
        return _world_from_text(self, text, f"bench_hh{count}.yml")

    def test_slope_is_flat_enough_to_be_linear(self) -> None:
        small = self._households(100)
        big = self._households(300)
        try:
            _warm(small, 1)
            _warm(big, 1)
            t_small = min(_month_seconds(small) for _ in range(2))
            t_big = min(_month_seconds(big) for _ in range(2))
            # Линейный рост: утра 200, втрое больше дворов — втрое больше времени.
            # Допуск щедрый (машина общая), но он отсекает именно квадрат:
            # при квадрате тройка дала бы девятикратный рост.
            linear = t_small * 3.0
            self.assertLess(
                t_big,
                linear * 1.6,
                f"100 дворов {t_small:.3f} с/мес, 300 дворов {t_big:.3f} с/мес: "
                f"рост сверхлинейный (линейный ждал бы < {linear * 1.6:.3f})",
            )
        finally:
            del small, big
            gc.collect()

    def test_own_tiles_key_cost_is_bounded_by_the_households_own_rights(self) -> None:
        """Ключ надела не должен расти вместе с РЕЕСТРОМ прав всего баронства.

        `_own_tiles_key` читает права ДВОРА. Если он обходит весь `world.rights`,
        то игрок, выдающий наделы приказом `grant_tenure`, платит за каждый
        приказ всем дворам сразу — и на 150 дворах это не видно. Тест требует,
        чтобы чужое право (другого двора) НЕ меняло ключ по существу: не
        «скорость», а форма зависимости.
        """
        world = load_scenario(SCENARIO, seed=SEED)
        _warm(world, 1)
        household = next(
            h
            for h in sorted(world.households.values(), key=lambda h: h.id)
            if h.settlement_id == "hill_court"
        )
        before = labor.own_tiles(world, household)
        from hillcourt.ontology import Right

        world.rights["right_bench_stranger"] = Right(
            id="right_bench_stranger",
            holder_household_id="hh_bench_stranger",
            tile_id="t_90_90",
            kind="tenure",
            granted_date=world.clock.date,
            rent_share=0.1,
        )
        after = labor.own_tiles(world, household)
        self.assertEqual(
            [tile.id for tile in before],
            [tile.id for tile in after],
            "чужое право изменило надел — так не бывает по закону",
        )


def _world_from_text(case: unittest.TestCase, text: str, name: str):
    """Собрать мир из изменённого текста сценария (чистая фикстура замера)."""
    import tempfile

    scenario_dir = SCENARIO.parent
    path = Path(tempfile.mkdtemp(prefix="perf_bench_")) / name
    # `load_scenario` ищет корень репозитория по `AGENTS.md` вверх, поэтому
    # фикстура обязана лежать ВНУТРИ дерева, и убирается за собой.
    path = scenario_dir / f"tmp_perf_{name}"
    path.write_text(text)
    case.addCleanup(lambda: path.unlink(missing_ok=True))
    return load_scenario(path, seed=SEED)


class TestCachesDieWithTheirWorld(unittest.TestCase):
    """Закон AGENTS.md §6: запись ушла в `weakref.ref` — обход обязан её чистить.

    Это самая опасная правка во всём проекте: если обхода нет, новый мир
    получит кэш прежнего по переиспользованному `id()`, и тик создаст материю
    из ничего. Здесь это проверяется и МУТИРУЕТСЯ — см. ADR 0224.
    """

    def test_land_cards_die_with_their_world(self) -> None:
        world = load_scenario(SCENARIO, seed=SEED)
        household = next(iter(world.households.values()))
        labor.own_tiles(world, household)
        labor._feeding_tiles(world, household)
        key = id(world)
        self.assertIn(key, labor._LAND_CARDS, "карточки земель не завелись")
        self.assertIsNotNone(labor._LAND_CARDS[key][2]())
        self.assertTrue(labor._LAND_CARDS[key][0], "карточка надела пуста — кэш мёртвый")

        del world
        gc.collect()
        self.assertNotIn(
            key,
            labor._LAND_CARDS,
            "карточки земель пережили свой мир: нет обхода, который их чистит",
        )

    def test_herd_state_dies_with_its_world(self) -> None:
        world = load_scenario(SCENARIO, seed=SEED)
        household = next(
            h for h in world.households.values() if h.settlement_id is not None
        )
        soil.settlement_livestock_units(world, household.settlement_id)
        key = id(world)
        self.assertIn(key, soil._HERD_STATE, "состояние стада не завелось")

        del world
        gc.collect()
        self.assertNotIn(
            key,
            soil._HERD_STATE,
            "состояние стада пережило свой мир: кэш висит на переиспользованном id()",
        )

    def test_herd_state_rejects_a_foreign_world(self) -> None:
        """Вторая страховка: на чтении сверяется `ref() is world`.

        Подсаживаем состояние ЧУЖОГО (но живого) мира под наш ключ — так увидел
        бы мир, попавший на переиспользованный `id()`. Состояние чужого мира
        создаётся заранее, чтобы подсаживать было что.
        """
        world = load_scenario(SCENARIO, seed=SEED)
        other = load_scenario(SCENARIO, seed=SEED)
        household = next(
            h for h in world.households.values() if h.settlement_id is not None
        )
        other_household = next(
            h for h in other.households.values() if h.settlement_id is not None
        )
        soil.settlement_livestock_units(world, household.settlement_id)
        soil.settlement_livestock_units(other, other_household.settlement_id)
        key = id(world)
        self.assertIn(id(other), soil._HERD_STATE, "состояние второго мира не завелось")
        soil._HERD_STATE[key] = soil._HERD_STATE[id(other)]
        try:
            # Сверка обязана отвергнуть чужую запись и завести свою.
            soil.settlement_livestock_units(world, household.settlement_id)
            self.assertIs(
                soil._HERD_STATE[key][1](),
                world,
                "чужое состояние стада принято за своё — сверки ref() is world нет",
            )
        finally:
            soil._HERD_STATE.pop(key, None)


def _give_ox(world, household, amount: float) -> None:
    """Положить волов в сток двора прямой записью — подготовка зонда, не тик.

    Так же, как `test_draft_livestock._give`: это НЕ тик и не создание материи,
    а подготовка входа перед измерением. Проводкой здесь нельзя: бухгалтерия
    справедливо отказывает брать `ox_m`, которого в амбаре нет (в баронстве скота
    нет вообще, см. `test_no_draft_animals_in_the_barony`), и тест падал бы на
    законе И-1 вместо своего предмета.
    """
    world.get_stock(household.stock_id).amounts["ox_m"] = float(amount)


if __name__ == "__main__":
    unittest.main()
