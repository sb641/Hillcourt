"""Обвинители закона выхода гекса домена: число из каталога, а не полоса.

**Полоса выхода отменена** (ADR 0142 п. «Что отменяется», реестр ADR 0143):
«выход сверху не ограничен, больше зерна — лучше». Поэтому этот модуль **не требует**
никакой полосы и не закрепляет историческое число: он требует **правила**.

1. **Выход — число каталога, и движок его читает.** Норма
   `spawn_rules.yml::grow_grain.params.demesne_base_yield` (сейчас 1.27) — единственный
   источник. Ожидаемый год считается здесь же из каталогов, а не подгоняется:

       спрос гекса `manor.yml::demesne.demand_days_per_tile` = 1458 трудодней/год
       партия `recipes.yml::harvest_grain` = 20 трудодней → 72.9 партии
       Σ(спрос_m × demesne_yield_m) = 1024.3                 (`seasons.yml`)
       выход = 5.0 зерна/партию × 1024.3 / 20 = 256.075 при норме 1.0
       норма 1.27 → 325.215 — и ровно это движок отдаёт в амбар

   Закон проверяется **линейностью**: удвоил норму в каталоге — удвоился год.
   Сдвинь норму в коде, замурованную константой, — тест упадёт.

2. **Выход сверху не ограничен** (ADR 0142 п. 1): больше работников — больше зерна.
3. **Потолка урожая нет** (ADR 0137 п. 2, ADR 0142 п. 3): клетка копит весь прирост,
   и уборка быстрее роста снимает больше, чем клетка нарастила за месяц.
4. **Пахота с буйволом и плугами** — повышающий коэффициент, и он считается от
   числа скота по стойловой норме `needs.yml::livestock.feed_per_month`.
5. Ничего не создаётся из воздуха: дельта материи 0.

Отменённой полосы нет в этом файле, и `assertNotIn` ниже — обвинитель её возврата,
а не её покрытие. В `design/catalogs/manor.yml:29` комментарий Economist'а полосу
ещё цитирует: это его файл, его зона, см. раздел «Находки вне моей зоны».

Трудонагрузка (`demesne.demand_days_per_tile`) — закон и не тронута.

Проверка: `PYTHONPATH=sim/src python3 -m unittest sim.tests.test_demesne_hex_300_350 -v`
"""

from __future__ import annotations

import contextlib
import unittest
from pathlib import Path
from typing import Callable

from hillcourt.economy import soil
from hillcourt.economy.manor import _manor_demesne_fields, _manor_worker, _work_demesne
from hillcourt.engine.manor import manor_stock
from hillcourt.engine.tick import phase_growth, run_month
from hillcourt.engine.yield_law import growth_season_factor
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
VILLAGE = ROOT / "design" / "scenarios" / "v0_large_village.yml"
HILL_SALT = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
MANOR_YML = ROOT / "design" / "catalogs" / "manor.yml"
GROW_RULE = "grow_grain"
HARVEST = "harvest_grain"
SEED = 1729
ADULT_LABOR_DAYS = 20.0
MONTHS = 12
EPS = 1e-9

# Отменённые полосы выхода гекса (ADR 0142, реестр ADR 0143). В законе их нет, и
# этот модуль обязан падать, если кто-то снова начнёт ими кого-то обвинять.
CANCELLED_BANDS = tuple(f"{low}-{high}" for low, high in ((300, 350), (250, 300)))
# Таблица спроса, которая обязана остаться нетронутой (ADR 0138).
DEMAND_AS_GIVEN = {
    1: 30, 2: 30, 3: 114, 4: 114, 5: 114, 6: 171,
    7: 171, 8: 266, 9: 266, 10: 76, 11: 76, 12: 30,
}


@contextlib.contextmanager
def catalog_norm(value: float):
    """Временно поставить норму выхода домена в каталоге и вернуть её как было.

    Каталоги кэшируются на процесс, поэтому правка без возврата утёкла бы в
    следующие тесты. Файл каталога не трогаем — правится только разобранный мир.
    """
    world = load_scenario(VILLAGE, seed=SEED)
    params = world.catalogs.spawn_rules[GROW_RULE].params
    was = float(params["demesne_base_yield"])
    params["demesne_base_yield"] = value
    try:
        yield world
    finally:
        params["demesne_base_yield"] = was


def _catalog_year(world) -> float:
    """Год гекса домена, предсказанный каталогами: спрос × партия × норма.

    Это **ожидание из данных**, а не записанное число. Смысл: движок обязан
    совпасть с каталогом; если экономист поднимет норму, тест останется зелёным
    сам, а если движок перестанет читать каталог — тест упадёт.
    """
    demand = world.manor.demand_days_per_tile
    recipe = world.catalogs.recipes[HARVEST]
    weighted = sum(
        demand[month] * float(world.seasons[month].demesne_yield)
        for month in range(1, MONTHS + 1)
    )
    norm = float(world.catalogs.spawn_rules[GROW_RULE].params["demesne_base_yield"])
    return float(recipe.outputs["grain"]) * weighted / float(recipe.labor_days) * norm


def _unit_norm_year(world) -> float:
    """То же самое при норме 1.0 — откуда взялась прежняя запись «256»."""
    return _catalog_year(world) / float(
        world.catalogs.spawn_rules[GROW_RULE].params["demesne_base_yield"]
    )


def _root_manor(world):
    return sorted(world.manors.values(), key=lambda m: m.id)[0]


def _worker(world):
    return _manor_worker(world, _root_manor(world))


def _demesne_year(
    workers: float | None,
    world=None,
    prepare: Callable[[object], None] | None = None,
) -> float:
    """Год одного гекса домена: рост клетки, потом жатва — через движок.

    `workers` — сколько работников делят гекс; `None` = «полный спрос», то есть
    пул манор получает ровно столько трудодней, сколько гекс просит, плюс труд
    смены полей (`rotation_days_per_tile` на каждую клетку домена): смена
    покупается из того же пула, и отнимать её у жатвы было бы подгонкой.
    `prepare` получает мир до тика — чтобы подложить двору плуг и буйвола.

    Рост идёт первым (`phase_growth` в тике идёт до `phase_manor`), поэтому
    месячный прирост доступен уборке того же месяца. Пул делится между клетками
    домена по порядку, как в `manor._work_demesne`, поэтому весь спрос уходит на
    первую клетку — её и меряем. Возвращает зерно, снятое в амбар манор.
    """
    if world is None:
        world = load_scenario(VILLAGE, seed=SEED)
    if prepare is not None:
        prepare(world)
    config = world.manor
    manor = _root_manor(world)
    fields = _manor_demesne_fields(world, manor)
    stock = manor_stock(world, manor)
    assert stock is not None
    before = stock.amounts.get("grain", 0.0)
    demand = config.demand_days_per_tile
    rotation_month = soil.rotation_month(world)
    rotation_days = soil.rotation_days(world)
    for month in range(1, MONTHS + 1):
        world.clock.month = month
        phase_growth(world)
        pool = (
            demand[month]
            if workers is None
            else min(demand[month], workers * ADULT_LABOR_DAYS)
        )
        if month == rotation_month:
            pool += rotation_days * len(fields)
        manor.demesne_labor_filled = pool
        _work_demesne(world, world.clock.date)
    return stock.amounts.get("grain", 0.0) - before


def _increments(world, norm: float = 1.0) -> list[float]:
    """Месячные приросты стоячего зерна клетки пашни за год."""
    amount = float(world.catalogs.spawn_rules[GROW_RULE].params["amount"])
    return [
        amount * growth_season_factor(world, GROW_RULE, month, 1.0) * norm
        for month in range(1, MONTHS + 1)
    ]


def _demesne_tile(world):
    return _manor_demesne_fields(world, _root_manor(world))[0]


class TestDemesneYieldComesFromTheCatalog(unittest.TestCase):
    """Выход гекса домена — число каталога. Полосы как нормы больше нет.

    Обвиняет не «полосу хозяина», а **правило**: сколько ни меняй норму в каталоге,
    ровно столько зерна движок и кладёт в амбар. Отменённая полоса (ADR 0142,
    реестр ADR 0143) в этом модуле не встречается и возвращаться не должна.
    """

    def test_the_frozen_demand_table_is_untouched(self) -> None:
        """Откуда взялось 256: та же формула при норме 1.0. Труд не тронут."""
        world = load_scenario(VILLAGE, seed=SEED)
        demand = world.manor.demand_days_per_tile
        weighted = sum(
            demand[month] * float(world.seasons[month].demesne_yield)
            for month in range(1, MONTHS + 1)
        )
        self.assertEqual(demand, DEMAND_AS_GIVEN, "Трудонагрузка гекса изменилась")
        self.assertEqual(sum(demand.values()), 1458.0)
        self.assertAlmostEqual(weighted, 1024.3, places=3)
        self.assertAlmostEqual(_unit_norm_year(world), 256.075, places=3)

    def test_engine_year_equals_the_catalog_formula(self) -> None:
        """Замер движком совпадает с предсказанием каталогов, а не с записью в тесте."""
        world = load_scenario(VILLAGE, seed=SEED)
        expected = _catalog_year(world)
        grain = _demesne_year(None)
        self.assertGreater(grain, 0.0, "Гекс при полном спросе не дал ничего")
        self.assertAlmostEqual(
            grain, expected, places=6,
            msg=f"движок {grain:.6f} != каталоги {expected:.6f}",
        )

    def test_engine_reads_the_norm_from_the_catalog(self) -> None:
        """Закон линейности: удвоил норму в каталоге — удвоился год.

        Если движок когда-нибудь замурует норму константой в коде, этот тест упадёт
        (год останется прежним), а не «поплывёт вместе с каталогом».
        """
        with catalog_norm(2.54) as world:
            doubled = _demesne_year(None, world=world)
        base = _demesne_year(None)
        self.assertAlmostEqual(doubled, 2.0 * base, places=6, msg="Норма каталога не влияет")

    def test_norm_lives_in_the_catalog(self) -> None:
        """Норма выхода — положительное число каталога, и в нём она одна."""
        world = load_scenario(VILLAGE, seed=SEED)
        params = world.catalogs.spawn_rules[GROW_RULE].params
        self.assertIn("demesne_base_yield", params, "Норма выхода домена не в каталоге")
        norm = float(params["demesne_base_yield"])
        self.assertGreater(norm, 0.0, "Норма выхода не положительна")
        self.assertNotIn("cap_per_tile", params, "Потолок урожая вернулся в каталог")

    def test_missing_norm_raises_instead_of_hiding_a_stub(self) -> None:
        """Нет ключа — `ValueError`, а не тихая заглушка (ADR 0163 п. 5).

        Заглушка `1.0` против каталожных `1.27` — это минус 21 % урожая домена,
        спрятанный за `.get`. Проверка требует, чтобы мир об этом узнавал.
        """
        from hillcourt.engine.yield_law import demesne_field_factor

        world = load_scenario(VILLAGE, seed=SEED)
        params = world.catalogs.spawn_rules[GROW_RULE].params
        was = params.pop("demesne_base_yield")
        try:
            with self.assertRaises(ValueError):
                demesne_field_factor(world, _demesne_tile(world).id, params)
        finally:
            params["demesne_base_yield"] = was
        self.assertAlmostEqual(
            demesne_field_factor(world, _demesne_tile(world).id, params), 1.27, places=9,
            msg="С ключом каталога множитель обязан читаться как есть",
        )

    def test_norm_is_read_without_a_numeric_fallback(self) -> None:
        """Ловля `.get(key, число)` по форме ADR 0162 п. 3, а не только по факту.

        Мутация может оставить `ValueError` и вернуть заглушку в другую строку —
        поэтому проверка смотрит исходник функции, а не только её поведение.
        """
        import inspect

        from hillcourt.engine import yield_law

        source = inspect.getsource(yield_law.demesne_field_factor)
        self.assertIn("demesne_base_yield", source)
        for line in source.splitlines():
            if ".get(" in line and "demesne_base_yield" in line:
                self.assertNotRegex(
                    line.strip(), r'\.get\([^)]*,\s*[0-9]',
                    "Читаем норму с числовой заглушкой — молчаливый другой мир",
                )
        self.assertNotIn(
            'params.get("demesne_base_yield"', source,
            "Норма выхода читается через .get — заглушка вернулась (ADR 0163 п. 5)",
        )

    def test_cancelled_band_is_not_the_law_anymore(self) -> None:
        """Этот обвинитель не требует отменённой полосы и не содержит её.

        ADR 0142: выход сверху не ограничен. Значит модуль, который обвиняет
        движок в неверном выходе, не имеет права требовать полосу как норму.
        Проверяемый факт — сам файл: если кто-то снова начнёт обвинять по полосе,
        тест это увидит.
        """
        text = Path(__file__).read_text(encoding="utf-8")
        for band in CANCELLED_BANDS:
            self.assertNotIn(
                band, text, f"Отменённая полоса {band} вернулась в обвинителя"
            )

    def test_catalog_comment_names_where_the_law_lives(self) -> None:
        """Каталог говорит, где норма живёт и что потолка нет, а не провозглашает полосу."""
        text = MANOR_YML.read_text(encoding="utf-8")
        self.assertIn("demesne_base_yield", text, "Не сказано, где живёт норма выхода")
        self.assertIn("потолк", text.lower(), "Не сказано, что потолка урожая нет")
        self.assertIn("13.3 работника", text, "Не сказано, при каком числе работников")

    def test_full_demand_names_its_workers(self) -> None:
        """Полный спрос — 1458 трудодней/год: 13.3 работника в пиковые месяцы."""
        demand = load_scenario(VILLAGE, seed=SEED).manor.demand_days_per_tile
        self.assertEqual(max(demand.values()), 266.0, "Пиковый спрос изменился")
        self.assertAlmostEqual(266.0 / ADULT_LABOR_DAYS, 13.3, delta=0.05)
        # 1458 трудодней/год при 20 днях на взрослого = 6.075 работника круглый год.
        self.assertAlmostEqual(
            sum(demand.values()) / (ADULT_LABOR_DAYS * MONTHS), 6.075, places=3
        )


class TestOutputGrowsWithWorkers(unittest.TestCase):
    """Выход гекса — функция числа работников, а не регламент."""

    def test_four_workers_are_strictly_less_than_eight(self) -> None:
        four = _demesne_year(4)
        eight = _demesne_year(8)
        self.assertGreater(four, 0.0)
        self.assertLess(four, eight, "Выход гекса не растёт от числа работников")
        self.assertAlmostEqual(four, 164.7, places=0, msg=f"{four:.1f} != ~165")
        self.assertAlmostEqual(eight, 257.8, places=0, msg=f"{eight:.1f} != ~258")

    def test_eight_workers_are_still_below_full_demand(self) -> None:
        """8 и 8.7 работника дают меньше полного спроса: не хватает рук, не урожая."""
        full = _demesne_year(None)
        eight = _demesne_year(8)
        eight_seven = _demesne_year(8.7)
        self.assertLess(eight, full)
        self.assertLess(eight_seven, full)
        self.assertLess(eight, eight_seven)
        self.assertAlmostEqual(eight_seven, 269.7, places=0, msg=f"{eight_seven:.1f} != ~270")


class TestStandingCapDoesNotThrottle(unittest.TestCase):
    """Потолка урожая нет: клетка копит весь прирост (ADR 0137 п. 2)."""

    def test_no_cap_and_the_tile_keeps_every_increment(self) -> None:
        """Потолка нет в каталоге, и год без уборки = сумма месячных приростов."""
        world = load_scenario(VILLAGE, seed=SEED)
        params = world.catalogs.spawn_rules[GROW_RULE].params
        self.assertNotIn("cap_per_tile", params, "Потолок урожая вернулся в каталог")
        norm = float(params["demesne_base_yield"])
        increments = _increments(world)
        self.assertAlmostEqual(sum(increments), 685.0, places=3)
        for month in range(1, MONTHS + 1):
            world.clock.month = month
            phase_growth(world)
        tile = _demesne_tile(world)
        standing = world.get_stock(tile.standing_stock_id).amounts.get("grain", 0.0)
        self.assertAlmostEqual(standing, 685.0 * norm, places=6)
        self.assertAlmostEqual(standing, 869.95, places=1)

    def test_harvest_faster_than_growth_is_not_throttled(self) -> None:
        """Уборка быстрее роста: выход режет труд, а не потолок посева.

        Пул вчетверо больше спроса — уборка забирает всё, что успело вырасти: это
        больше годового прироста пиковой жатвы (август+сентябрь) и больше любого
        месяца. Срезать это мог бы только потолок standing-зерна, которого в
        каталоге нет, — и вот его отсутствие здесь и проверяется, числами.
        """
        world = load_scenario(VILLAGE, seed=SEED)
        norm = float(world.catalogs.spawn_rules[GROW_RULE].params["demesne_base_yield"])
        grain = _demesne_year(None, world=world)
        self.assertAlmostEqual(grain, _catalog_year(world), places=6)
        increments = _increments(world, norm)
        self.assertGreater(grain, sum(increments[7:9]), "Выход не больше прироста пиковой жатвы")
        self.assertGreater(grain, max(increments), "Выход не больше пикового месяца")

    def test_harvest_never_drains_the_crop(self) -> None:
        """Урожай снимает меньше, чем клетка нарастила: посев не кончается."""
        world = load_scenario(VILLAGE, seed=SEED)
        params = world.catalogs.spawn_rules[GROW_RULE].params
        recipe = world.catalogs.recipes[HARVEST]
        norm = float(params["demesne_base_yield"])
        batches = sum(DEMAND_AS_GIVEN.values()) / float(recipe.labor_days)
        drawn = batches * float(recipe.draws_standing["grain"]) * norm
        grown = sum(_increments(world, norm))
        self.assertAlmostEqual(grown, 869.95, places=1)
        self.assertAlmostEqual(drawn, 574.0, places=0)
        self.assertLess(drawn, grown, "Уборка выбирает больше, чем клетка вырастила")


class TestOxPloughMultiplier(unittest.TestCase):
    """Пахота с буйволом и плугами: повышающий коэффициент от числа скота."""

    def setUp(self) -> None:
        self.world = load_scenario(HILL_SALT, seed=SEED)
        self.params = self.world.catalogs.spawn_rules[GROW_RULE].params
        self.hh = self.world.households["hh_02"]
        self.tile = self.world.tiles[self.hh.current_tile_id]
        self.stock = self.world.get_stock(self.hh.stock_id)

    def test_multiplier_is_catalog_data(self) -> None:
        self.assertIn("draft_yield_per_head", self.params, "Множитель от буйвола не в каталоге")
        self.assertIn("draft_yield_cap", self.params, "Потолок множителя не в каталоге")
        self.assertGreater(float(self.params["draft_yield_per_head"]["ox"]), 0.0)
        self.assertGreater(float(self.params["draft_yield_cap"]), 1.0)
        self.assertEqual(soil.draft_cap(self.world), float(self.params["draft_yield_cap"]))

    def test_heads_come_from_the_stabled_norm(self) -> None:
        """Голова — стойловая единица `needs.yml`: ox_m 1.2, ox_f 1.0."""
        feed = self.world.needs.feed_per_month
        self.assertAlmostEqual(float(feed["ox_m"]), 1.2, places=6)
        self.assertAlmostEqual(float(feed["ox_f"]), 1.0, places=6)
        self.stock.amounts["ox_m"] = 3.0
        self.stock.amounts["ox_f"] = 2.0
        self.assertAlmostEqual(soil.draft_heads(self.world, self.stock, "ox"), 5.6, places=6)
        self.stock.amounts["ox_calf"] = 5.0
        self.assertAlmostEqual(
            soil.draft_heads(self.world, self.stock, "ox"), 5.6, places=6,
            msg="Телята в плуг пошли, а плуг их не тянет",
        )

    def test_ox_with_plough_is_strictly_above_plough_alone(self) -> None:
        """Слова хозяина: буйвол с плугом строго выше, чем плуг без скота."""
        bare = soil.cell_yield_factor(self.world, self.hh, self.tile)
        self.assertEqual(bare, 1.0, "Голый двор не должен получать прибавку")
        self.stock.amounts["wooden_plough"] = 1.0
        ploughed = soil.cell_yield_factor(self.world, self.hh, self.tile)
        self.assertGreater(ploughed, bare, "Плуг не поднял выход гекса")
        self.assertEqual(soil.draft_yield_factor(self.world, self.hh), 1.0)
        self.stock.amounts["ox_m"] = 1.0
        with_ox = soil.cell_yield_factor(self.world, self.hh, self.tile)
        self.assertGreater(with_ox, ploughed, "Буйвол с плугом не поднял выход гекса")
        self.assertAlmostEqual(
            with_ox / ploughed,
            1.0 + float(self.params["draft_yield_per_head"]["ox"]) * 1.2,
            places=6,
            msg="Прибавка посчитана не от 1.2 стойловой головы вола",
        )

    def test_multiplier_counts_the_livestock(self) -> None:
        """Величина прибавки — от числа голов, и потолок её срезает."""
        rate = float(self.params["draft_yield_per_head"]["ox"])
        cap = float(self.params["draft_yield_cap"])
        self.stock.amounts["wooden_plough"] = 1.0
        seen = []
        for heads in (1, 2, 4, 8, 40):
            self.stock.amounts["ox_m"] = float(heads)
            factor = soil.draft_yield_factor(self.world, self.hh)
            seen.append(factor)
            self.assertAlmostEqual(factor, min(cap, 1.0 + rate * heads * 1.2), places=6)
        self.assertEqual(seen, sorted(seen), "Прибавка не растёт от числа скота")
        self.assertGreater(seen[-1], seen[0])
        self.assertLessEqual(seen[-1], cap + EPS)
        self.assertAlmostEqual(seen[2], 1.288, places=3, msg="4 вола != 1.288")
        self.stock.amounts["ox_m"] = 0.0
        self.assertEqual(soil.draft_yield_factor(self.world, self.hh), 1.0)

    def test_ox_lifts_the_demesne_full_demand(self) -> None:
        """Плуг и буйвол у работника домена поднимают год гекса."""
        def plough_and_ox(world) -> None:
            stock = world.get_stock(_worker(world).stock_id)
            stock.amounts["wooden_plough"] = 1.0
            stock.amounts["ox_m"] = 1.0

        bare = _demesne_year(None)
        with_ox = _demesne_year(None, prepare=plough_and_ox)
        self.assertGreater(with_ox, bare, "Буйвол с плугом не дал гексу больше зерна")


class TestMatterIsConserved(unittest.TestCase):
    """Ничего не создано из воздуха: год деревни — дельта материи 0."""

    def test_matter_delta_is_zero(self) -> None:
        world = load_scenario(VILLAGE, seed=SEED)
        world.ledger.capture_initial(world.total_matter())
        for _ in range(MONTHS):
            run_month(world)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)
        self.assertGreater(world.stats.get("demesne_grain", 0.0), 0.0, "Домен не собрал")


if __name__ == "__main__":
    unittest.main()
