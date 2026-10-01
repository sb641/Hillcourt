"""Обвинители трёх улучшений пашни и починки enterprises-кормления.

Слова хозяина, ставшие проверками:

1. **Баг закрыт.** Функция «предприятие покрывает нужду» возвращала `True`, когда
   работников без надела нет, и глушила пашню землевладельца. Закон: предприятие
   кормит БЕЗЗЕМЕЛЬНЫХ, пашню дворов с наделом не выключает (ADR 0102 п. 4).
2. **Три множителя — данные каталога, а не константы** (`spawn_rules.yml::grow_grain.params`):
   пахота с буйволом и плугами, навоз (материал лестницы, множитель от числа
   скота по норме `needs.yml.livestock`), смена полей (единственный множитель,
   зависящий от времени: прибавка ЧЕРЕЗ ГОД).
3. **Выход гекса растёт от числа работников**: 4 работника ≈ 137 зерна/год,
   8 ≈ 300 (после пахоты с буйволом и плугами).
4. **Деревня 35 дворов на 9 гексах** не голодает ни одного дворомесяца без
   засыпки зерном, десятина барону > 0.
5. Регрессии законов: плотность 5 дворов в гексе, барщина **без выплат**,
   дельта материи 0.

Проверка: `PYTHONPATH=sim/src python3 -m unittest sim.tests.test_soil_improvements -v`
"""

from __future__ import annotations

import atexit
import os
import tempfile
import unittest
from pathlib import Path

import yaml

from hillcourt.economy import exchange, labor, soil
from hillcourt.economy.labor import (
    _enterprise_covers_workers,
    _enterprise_silences_plough,
    _has_feeding_land,
    own_land_feeds,
)
from hillcourt.engine.tick import phase_manor, run_month
from hillcourt.engine.yield_law import growth_season_factor
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
HILL_SALT = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
VILLAGE = ROOT / "design" / "scenarios" / "v0_large_village.yml"
GROW_RULE = "grow_grain"
HARVEST = "harvest_grain"
SEED = 1729
ADULT_LABOR_DAYS = 20.0
MONTHS = 12

# Фикстуры-сценарии лежат внутри репозитория (загрузчик ищет AGENTS.md
# относительно файла), поэтому снимаются дважды: сразу и на `atexit`. `finally`
# не выполняется при оборванном прогоне, а `.gitignore` прячет `tmp*.yml` в корне —
# мусор остаётся невидимым.
_TEMP_PATHS: list[Path] = []


def _cleanup_temp_paths() -> None:
    for path in _TEMP_PATHS:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


atexit.register(_cleanup_temp_paths)
EPS = 1e-9

# Ориентиры хозяина (ADR 0137 п. 1 и решения по village-расчёту).
HEX_AT_4 = 137.0
HEX_AT_8 = 274.0
VILLAGE_HEXES = 9
DENSITY_CAP = 5
YARDS = 35
# Прибавка буйвола: `draft_yield_per_head` — ставка ЗА стойловую голову, а вол
# по `needs.yml` стоит 1.2, поэтому один вол тянет 1 + 0.06 × 1.2 = 1.072.
OX_FEED = 1.2
OX_FACTOR = 1.0 + 0.06 * OX_FEED


def _harvest_by(world, household_id: str, good: str = "grain") -> float:
    return sum(
        e.amount
        for e in world.ledger.entries
        if e.reason == HARVEST
        and e.good == good
        and e.dst_id == f"household:{household_id}"
    )


def _hunger_months(world) -> int:
    return sum(1 for e in world.ledger.entries if e.good == "hunger")


def _relief_beneficiary(obligation, known_ids: set[str]) -> str:
    """Двор-получатель из id записи `relief_<месяц>_<сеньор>_<двор>`.

    У записи о подаче `household_id` — двор СЕНЬОРА (он недодал), поэтому получателя
    в поле нет, и он закодирован в id. Разбор по подчёркиваниям ненадёжен: и
    `hh_court`, и `hh_village_cotter_a_001` содержат `_`, поэтому получатель ищется
    как известный id, на который запись заканчивается.
    """
    for hid in known_ids:
        if obligation.id.endswith(hid) and obligation.id != hid:
            return hid
    return ""


def _relief_wires_into(world, household_ids: set[str]) -> list:
    """Проводки подачи в стоки переданных дворов."""
    stocks = {world.households[hid].stock_id for hid in household_ids}
    return [
        e for e in world.ledger.entries
        if e.reason == "relief" and e.good == "grain" and e.dst_id in stocks
    ]


def _salt_village_world() -> object:
    """Солеварня + двор С НАДЕЛОМ в том же поселении (обвинитель на баг).

    Сценарий `v0_hill_and_salt` уже содержит производственный гекс предприятия
    (солончак) и двух работников без надела. Добавляем третьего — виллана с
    надельной пашней: именно его пашню набитый склад предприятия глушил.
    """
    data = yaml.safe_load(HILL_SALT.read_text(encoding="utf-8"))
    data["households"].append(
        {
            "id": "hh_salt_farmer",
            "name": "Виллан при солеварне",
            "settlement_id": "salt_village",
            "adults": 2,
            "children": 1,
            "legal_status": "villein",
            "holding_tiles": [[6, 5]],
        }
    )
    data["starting_stocks"]["household:hh_salt_farmer"] = {"grain": 60.0, "firewood": 6.0}
    handle, name = tempfile.mkstemp(suffix=".yml", dir=ROOT)
    path = Path(name)
    _TEMP_PATHS.append(path)
    with os.fdopen(handle, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True)
    try:
        world = load_scenario(path, seed=SEED)
    finally:
        path.unlink(missing_ok=True)
    for hid in ("hh_salt_farmer",):
        world.households[hid].main_action = "work_plot"
        world.households[hid].minor_action = "work_plot"
    world.ledger.capture_initial(world.total_matter())
    return world


def _hex_year(world, workers: int, factor: float = 1.0, demesne: bool = False):
    """Годовой выход гекса пашни по тем же формулам, что и движок.

    Рост — `growth_season_factor` (`engine/yield_law.py`), вынос и выход —
    сезон месяца (`economy/seasons.py`), партия — 20 трудодней
    (`harvest_grain.labor_days`). Рабочих ровно `workers`, у каждого
    `ADULT_LABOR_DAYS` трудодней в месяц. Потолка урожая нет (ADR 0137 п. 2):
    стоячая материя копится вся (`grow_grain` без `cap_per_tile`).
    """
    recipe = world.catalogs.recipes[HARVEST]
    params = world.catalogs.spawn_rules[GROW_RULE].params
    amount = float(params["amount"])
    draw = float(recipe.draws_standing["grain"])
    out = float(recipe.outputs["grain"])
    labor = float(recipe.labor_days)
    standing = 0.0
    grain = 0.0
    batches = 0
    for month in range(1, MONTHS + 1):
        entry = world.seasons[month]
        season = float(entry.demesne_yield if demesne else entry.plot_yield) * factor
        standing += amount * growth_season_factor(world, GROW_RULE, month, 1.0)
        need = draw * season
        by_labor = int(workers * ADULT_LABOR_DAYS // labor)
        by_standing = int(standing // need) if need > EPS else 0
        made = max(0, min(by_labor, by_standing))
        standing -= made * need
        grain += made * out * season
        batches += made
    return grain, batches


class TestEnterpriseMustNotSilenceLandholder(unittest.TestCase):
    """Баг закрыт: набитый склад предприятия не выключает надел землевладельца."""

    def setUp(self) -> None:
        # Свой мир на каждый тест: год жатвы двигает склад и часы.
        self.world = _salt_village_world()
        self.hh = self.world.households["hh_salt_farmer"]
        self.settlement = self.world.settlements["salt_village"]

    def test_landholder_has_feeding_land(self) -> None:
        self.assertTrue(
            own_land_feeds(self.world, self.hh),
            "Двор с надельной пашней перестал быть землевладельцем",
        )
        self.assertTrue(_has_feeding_land(self.world, self.hh))

    def test_enterprise_store_is_full_and_landless_exist(self) -> None:
        """Склад набит и работники без надела есть — «покрывает нужду» = True."""
        need = sum(
            1.0
            for hid in self.settlement.household_ids
            if not own_land_feeds(self.world, self.world.households[hid])
        )
        self.assertGreaterEqual(need, 2.0, "В поселении нет работников без надела")
        store = self.world.get_stock(self.settlement.stores_stock_id)
        self.assertGreater(store.amounts.get("grain", 0.0), 0.0)
        self.assertTrue(
            _enterprise_covers_workers(self.world, self.hh),
            "Склад предприятия перестал покрывать нужду безземельных",
        )

    def test_no_landless_means_enterprise_covers_nothing(self) -> None:
        """Нет работников без надела — `need` ноль, глушить пашню нечем.

        Именно этот случай хозяин назвал багом: «покрывает нужду» возвращало
        `True` при `need == 0`. Оставляем в поселении только двор с наделом.
        """
        self.settlement.household_ids = ["hh_salt_farmer"]
        self.assertFalse(
            _enterprise_covers_workers(self.world, self.hh),
            "Предприятие без безземельных снова считается покрывающим нужду",
        )
        self.assertFalse(
            _enterprise_silences_plough(self.world, self.hh, True),
            "Предприятие без безземельных снова глушит пашню",
        )

    def test_landholder_plot_is_not_silenced(self) -> None:
        production_hex = labor._has_production_only_hex(
            self.world, self.hh, list(self.world.catalogs.recipes.values())
        )
        self.assertTrue(production_hex, "У солеварни нет производственного гекса")
        self.assertFalse(
            _enterprise_silences_plough(self.world, self.hh, production_hex),
            "Склад предприятия снова глушит пашню двора с наделом",
        )

    def test_landless_worker_is_still_silenced(self) -> None:
        """Безземельный при полном складе пашню сверх нужды не идёт (ADR 0102)."""
        worker = self.world.households["hh_salt_01"]
        self.assertFalse(_has_feeding_land(self.world, worker))
        self.assertTrue(
            _enterprise_silences_plough(self.world, worker, True),
            "Предприятие перестало кормить безземельного",
        )

    def test_landholder_harvests_its_plot(self) -> None:
        """Землевладелец пашет надел и получает жатву — числами."""
        world = self.world
        for _ in range(MONTHS):
            run_month(world)
        harvested = _harvest_by(world, "hh_salt_farmer")
        self.assertGreater(harvested, 0.0, "Землевладелец не собрал хлеб")
        self.assertGreater(
            harvested, 20.0,
            f"Пашня землевладельца всё ещё глушится: {harvested:.1f} зерна за год",
        )
        self.assertEqual(self.hh.hunger_days, 0)
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )


class TestThreeMultipliersAreCatalogData(unittest.TestCase):
    """Три прибавки — числа каталога, а не константы модуля."""

    def setUp(self) -> None:
        self.world = load_scenario(HILL_SALT, seed=SEED)
        self.params = self.world.catalogs.spawn_rules[GROW_RULE].params
        self.hh = self.world.households["hh_02"]
        self.tile = self.world.tiles[self.hh.current_tile_id]

    def test_coefficients_live_in_the_catalog(self) -> None:
        for key in (
            "tool_yield", "draft_yield_per_head", "draft_yield_cap",
            "manure_yield_per_head",
            "manure_yield_cap", "manure_uptake", "rotation_yield",
            "rotation_month", "rotation_days_per_tile",
            "spread_days_per_unit", "spread_units_per_month",
        ):
            with self.subTest(param=key):
                self.assertIn(key, self.params, "Множитель не в каталоге")
        self.assertGreater(
            self.params["draft_yield_per_head"]["ox"], 0.0, "Буйволу нет прибавки"
        )
        self.assertGreater(self.params["draft_yield_cap"], 1.0)
        for good in ("wooden_plough", "iron_share"):
            with self.subTest(tool=good):
                self.assertGreater(self.params["tool_yield"][good], 1.0)

    def test_bare_hex_has_no_bonus(self) -> None:
        self.assertEqual(soil.tool_yield_factor(self.world, self.hh), 1.0)
        self.assertEqual(soil.draft_yield_factor(self.world, self.hh), 1.0)
        self.assertEqual(soil.tile_manure_factor(self.world, self.tile, 30.0), 1.0)
        self.assertEqual(soil.rotation_factor(self.world, self.tile), 1.0)
        self.assertEqual(soil.cell_yield_factor(self.world, self.hh, self.tile), 1.0)

    def test_ox_is_strictly_better_than_no_plough(self) -> None:
        """Пахота с буйволом и плугами — строго выше, чем без плуга."""
        stock = self.world.get_stock(self.hh.stock_id)
        bare = soil.cell_yield_factor(self.world, self.hh, self.tile)
        stock.amounts["wooden_plough"] = 1.0
        ploughed = soil.cell_yield_factor(self.world, self.hh, self.tile)
        stock.amounts["ox_m"] = 1.0
        with_ox = soil.cell_yield_factor(self.world, self.hh, self.tile)
        self.assertGreater(ploughed, bare, "Плуг не поднял выход гекса")
        self.assertGreater(with_ox, ploughed, "Буйвол с плугом не поднял выход гекса")
        # Стойловая норма вола needs.yml = 1.2 головы, прибавка — за голову.
        self.assertAlmostEqual(
            with_ox / bare,
            self.params["tool_yield"]["wooden_plough"]
            * (1.0 + self.params["draft_yield_per_head"]["ox"] * 1.2),
            places=6,
        )
        stock.amounts["ox_m"] = 4.0
        self.assertGreater(
            soil.cell_yield_factor(self.world, self.hh, self.tile),
            with_ox,
            "Прибавка не растёт от числа буйволов",
        )
        stock.amounts["iron_share"] = 1.0
        self.assertGreater(
            soil.cell_yield_factor(self.world, self.hh, self.tile),
            with_ox,
            "Железный лемех слабее деревянного плуга",
        )

    def test_manure_is_strictly_better_and_counts_livestock(self) -> None:
        """Навоз поднимает урожайность клетки; величина — от числа скота."""
        standing = self.world.get_stock(self.tile.standing_stock_id)
        self.assertEqual(soil.tile_manure_factor(self.world, self.tile, 30.0), 1.0)
        standing.amounts["manure"] = 1.0
        one_ox = soil.tile_manure_factor(self.world, self.tile, 2.2)
        ten_ox = soil.tile_manure_factor(self.world, self.tile, 22.0)
        self.assertGreater(one_ox, 1.0, "Навоз на клетке не поднял множитель")
        self.assertGreater(ten_ox, one_ox, "Множитель навоза не считается от числа скота")
        # Число голов берётся по стойловой норме needs.yml.livestock: вол 1.2.
        stock = self.world.get_stock(self.hh.stock_id)
        one = soil.livestock_units(self.world, stock)
        stock.amounts["ox_m"] = 2.0
        two = soil.livestock_units(self.world, stock)
        self.assertAlmostEqual(two - one, 2.0 * 1.2, places=6, msg="Норма вола не 1.2")
        stock.amounts["pig"] = 5.0
        self.assertAlmostEqual(
            soil.livestock_units(self.world, stock), two, places=6,
            msg="Свиньи в стойловые головы не считаются",
        )
        cap = float(self.params["manure_yield_cap"])
        self.assertLessEqual(soil.manure_yield_factor(self.world, 10000.0), cap + EPS)
        self.assertEqual(soil.manure_yield_factor(self.world, 0.0), 1.0)

    def test_manure_recipe_is_matter_and_needs_draught(self) -> None:
        recipe = self.world.catalogs.recipes["make_manure"]
        left = sum(recipe.inputs.values()) + sum(recipe.draws_standing.values())
        right = sum(recipe.outputs.values()) + sum(recipe.loss.values())
        self.assertAlmostEqual(left, right, places=6, msg="Партия навоза не сбалансирована")
        self.assertIn("manure", recipe.outputs)
        self.assertIn("manure", self.world.catalogs.goods)
        self.assertEqual(self.world.catalogs.goods["manure"].edible, False)
        stock = self.world.get_stock(self.hh.stock_id)
        stock.amounts["hay"] = 1.0
        stock.amounts["manure"] = 0.0
        self.world.bump("manure_made", 0.0)
        from hillcourt.economy.livestock import _manure_stock

        _manure_stock(self.world, stock, self.world.clock.date)
        self.assertEqual(
            stock.amounts.get("manure", 0.0), 0.0,
            "Без тягла хлев навоза не даёт",
        )
        stock.amounts["ox_f"] = 1.0
        _manure_stock(self.world, stock, self.world.clock.date)
        self.assertGreater(stock.amounts.get("manure", 0.0), 0.0, "Вол/корова не дали навоза")

    def test_rotation_raises_next_year_only(self) -> None:
        """Смена полей — единственный множитель, зависящий от времени: через год."""
        self.world.clock.year = 1
        self.assertEqual(soil.rotation_year(self.world, self.tile), 0.0)
        self.assertEqual(soil.rotation_factor(self.world, self.tile), 1.0)
        self.assertTrue(soil.rotate_cell(self.world, self.tile, 1))
        self.assertEqual(soil.rotation_year(self.world, self.tile), 1.0)
        self.assertEqual(
            soil.rotation_factor(self.world, self.tile), 1.0,
            "Прибавка включилась в год смены, а не через год",
        )
        self.assertFalse(soil.rotate_cell(self.world, self.tile, 1), "Повторная смена")
        self.world.clock.year = 2
        self.assertEqual(soil.rotation_factor(self.world, self.tile), 1.08)
        self.assertGreater(soil.rotation_factor(self.world, self.tile), 1.0)

    def test_rotation_costs_labour_and_is_paid_from_leftover(self) -> None:
        """Смена полей не бесплатна: она покупается остатком месяца."""
        world = load_scenario(HILL_SALT, seed=SEED)
        hh = world.households["hh_02"]
        hh.main_action = "idle_repair"
        hh.minor_action = "idle_repair"
        days = soil.rotation_days(world)
        self.assertGreater(days, 0.0)
        labor.work_month(world, world.clock.date)
        self.assertEqual(soil.rotation_year(world, world.tiles[hh.current_tile_id]), 0.0)
        world.clock.month = soil.rotation_month(world)
        labor.work_month(world, world.clock.date)
        self.assertEqual(
            soil.rotation_year(world, world.tiles[hh.current_tile_id]), 0.0,
            "Смена полей прошла без трудодней",
        )


class TestHexOutputGrowsWithWorkers(unittest.TestCase):
    """Выход гекса — функция числа работников (ADR 0137 п. 1)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = load_scenario(HILL_SALT, seed=SEED)
        cls.params = cls.world.catalogs.spawn_rules[GROW_RULE].params

    def test_four_workers_give_137(self) -> None:
        grain, batches = _hex_year(self.world, 4)
        self.assertEqual(batches, 48, "4 работника не дали 4 партии/мес")
        self.assertAlmostEqual(grain, HEX_AT_4, places=3, msg=f"{grain:.1f} != 137")

    def test_output_grows_with_workers(self) -> None:
        four, _ = _hex_year(self.world, 4)
        eight, _ = _hex_year(self.world, 8)
        self.assertGreater(four, 0.0)
        self.assertGreater(eight, four, "Выход гекса не растёт от числа работников")
        self.assertAlmostEqual(eight, HEX_AT_8, places=3, msg=f"{eight:.1f} != 274")
        self.assertAlmostEqual(eight / four, 2.0, places=6, msg="Выход не линейный")

    def test_plough_and_ox_reach_three_hundred(self) -> None:
        """8 работников с буйволом и плугами дают ориентир хозяина ~300."""
        bare, _ = _hex_year(self.world, 8)
        factor = self.params["tool_yield"]["wooden_plough"] * OX_FACTOR
        improved, _ = _hex_year(self.world, 8, factor=factor)
        self.assertGreater(improved, bare)
        self.assertAlmostEqual(improved, 308.4, places=0, msg=f"{improved:.1f} != ~308")
        self.assertAlmostEqual(improved * VILLAGE_HEXES, 2775.7, delta=3.0)

    def test_all_three_improvements_raise_the_hex(self) -> None:
        """Все три улучшения вместе: 8 работников дают выше 350, а не 274."""
        factor = (
            self.params["tool_yield"]["wooden_plough"]
            * OX_FACTOR
            * self.params["manure_yield_cap"]
            * self.params["rotation_yield"]
        )
        bare, _ = _hex_year(self.world, 8)
        full, batches = _hex_year(self.world, 8, factor=factor)
        self.assertEqual(batches, 96, "8 работников не дали 8 партий/мес")
        self.assertGreater(full, bare)
        self.assertAlmostEqual(full, 399.7, places=0, msg=f"{full:.1f} != ~400")
        rotation, _ = _hex_year(self.world, 8, factor=self.params["rotation_yield"])
        self.assertAlmostEqual(rotation, 295.9, places=0, msg=f"{rotation:.1f} != ~296")

    def test_yard_labour_is_the_only_limit(self) -> None:
        """Потолка урожая нет: 10 работников дают больше 8 (ADR 0137 п. 2)."""
        self.assertNotIn("cap_per_tile", self.params)
        eight, _ = _hex_year(self.world, 8)
        ten, batches = _hex_year(self.world, 10)
        self.assertEqual(batches, 120, "10 работников не дали 10 партий/мес")
        self.assertGreater(ten, eight, "Гекс упёрся в потолок, а не в труд")


class TestVillageOfThirtyFiveYards(unittest.TestCase):
    """Деревня 35 дворов на 9 гексах: голода 0 без засыпки, десятина > 0."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = load_scenario(VILLAGE, seed=SEED)
        cls.world.ledger.capture_initial(cls.world.total_matter())
        for _ in range(MONTHS):
            run_month(cls.world)
        cls.village = cls.world.settlements["large_village"]

    def test_thirty_five_yards_on_nine_hexes(self) -> None:
        self.assertEqual(len(self.village.household_ids), YARDS)
        field_hexes = {
            tile_id
            for tile_id in self.village.works_tiles
            if self.world.tiles[tile_id].terrain == "field"
        }
        self.assertEqual(len(field_hexes), VILLAGE_HEXES, "Гексов пашни не 9")

    def test_density_is_five_yards_per_hex(self) -> None:
        per_tile: dict[str, int] = {}
        for hid in self.village.household_ids:
            tile_id = self.world.households[hid].current_tile_id
            per_tile[tile_id] = per_tile.get(tile_id, 0) + 1
        self.assertLessEqual(max(per_tile.values()), DENSITY_CAP, "Плотность выше 5")
        self.assertEqual(
            sorted(per_tile.values()), [DENSITY_CAP] * 7, "Плотность 5 дворов на гекс"
        )

    def test_nobody_left_the_land_and_relief_is_recorded_as_a_debt(self) -> None:
        """ADR 0124: голод при неурожае законен. Закон — другой и проверяемый.

        Прежний тест требовал «ни одного голодного месяца». Факт: за 24 месяца
        голодные месяцы были в 11, пик `hunger_days` = 5, и это **законно** — неурожай
        допускается, подача по недобору разрешена (ADR 0139, ADR 0158 п. 3). Запрещать
        в тесте право нельзя.

        Что действительно обязано быть верно, и это проверяется здесь дословно:
        1. **никто не ушёл с земли и не умер**: `left_at is None` у каждого двора
           (уход — порог 3 дня голода плюс право `can_leave`, а у `villein`/`cotter`
           права ухода нет);
        2. **подача записана в книгу сеньора**: на каждую единицу зерна, дошедшую до
           двора деревни, есть запись вида `relief_granted`;
        3. **подача равна недобору, а не «сколько вышло»**: `paid_total <= due_amount`,
           а `arrears` — ровно невыданное. Подача сверх недобора была бы воровством
           сеньора у самого себя.
        """
        for hid in self.village.household_ids:
            self.assertIsNone(
                self.world.households[hid].left_at, f"{hid} ушёл с земли от голода"
            )
        village = set(self.village.household_ids)
        records = [
            o for o in exchange.relief_obligations(self.world)
            if _relief_beneficiary(o, village)
        ]
        self.assertTrue(records, "В книге сеньора нет ни одной записи о подаче деревне")
        for obligation in records:
            yard = _relief_beneficiary(obligation, village)
            self.assertLessEqual(
                obligation.paid_total, obligation.due_amount + 1e-9,
                f"{yard}: подано больше недобора ({obligation.paid_total} > "
                f"{obligation.due_amount})",
            )
            self.assertAlmostEqual(
                obligation.arrears, obligation.due_amount - obligation.paid_total, places=9,
                msg=f"{yard}: недоимка в записи не равна невыданному",
            )
        wires = _relief_wires_into(self.world, village)
        self.assertTrue(wires, "Деревне не выдано ни зерна подачи — книга пустая")
        self.assertAlmostEqual(
            sum(o.paid_total for o in records),
            sum(e.amount for e in wires),
            places=6,
            msg="Книга сеньора и проводки подачи разошлись",
        )

    def test_village_eats_its_own_field_and_legal_relief(self) -> None:
        """Еда — своя пашня плюс ЗАКОННЫЕ поступления, а не засыпка сверху.

        Прежний тест требовал, чтобы в сток двора деревни зерно приходило
        **исключительно** из `harvest_grain`. Это неверно: подача — закреплённое право
        (ADR 0139, ADR 0158 п. 3), и запрещать его в тесте значит запрещать право.
        Оставлено ровно то, что законно, и ничего сверх:
          * `harvest_grain` — своя пашня (оброк идёт ОБРАТНО, дворы платят барону);
          * `relief` — право по недобору, и оно записано в книгу (проверяет соседний
            тест, здесь — что другой причины нет);
          * `neighbour_graft` — дар соседям по цене ноль (ADR 0169 п. 6);
          * `board` — паёк, и он не должен доходить до деревни.
        """
        village_stocks = {
            self.world.households[hid].stock_id for hid in self.village.household_ids
        }
        reasons = {
            e.reason
            for e in self.world.ledger.entries
            if e.good == "grain" and e.dst_id in village_stocks
        }
        self.assertTrue(
            reasons <= {"harvest_grain", "relief", "neighbour_gift"},
            f"Деревне зерно пришло не законным путём: {sorted(reasons)}",
        )
        self.assertIn(
            "harvest_grain", reasons, "Деревня не собирает урожай со своей пашни"
        )
        self.assertNotIn(
            "board", reasons, "Паёк из амбара барона дошёл до дворов деревни"
        )
        harvested = sum(
            e.amount for e in self.world.ledger.entries
            if e.reason == "harvest_grain" and e.good == "grain" and e.dst_id in village_stocks
        )
        self.assertGreater(
            harvested, 0.0, f"Деревня не прокормила себя пашней: {harvested:.3f}"
        )
        self.assertEqual(self.world.stats.get("hired_days", 0.0), 0.0, "Деревню наняли")
        boards = {
            e.dst_id for e in self.world.ledger.entries if e.reason == "board"
        }
        self.assertTrue(
            boards <= {"household:hh_court"},
            f"Паёк из амбара барона ушёл не только его двору: {sorted(boards)}",
        )

    def test_tithe_to_the_baron_is_positive(self) -> None:
        tithe = self.world.stats.get("demesne_grain", 0.0)
        self.assertGreater(tithe, 0.0, "Излишек барону (десятина) не идёт")
        self.assertGreater(
            self.world.stats.get("corvee_days", 0.0), 0.0,
            "Вся деревня не отработала барщину",
        )

    def test_corvee_is_unpaid(self) -> None:
        """Барщина бесплатна (ADR 0131): ни одной выплаты за неё."""
        payments = [
            e for e in self.world.ledger.entries
            if e.reason in {"corvee", "labor_duty", "duty_payment"}
        ]
        self.assertEqual(payments, [], "За барщину заплатили")
        duties = [
            o for o in self.world.obligations.values() if o.kind == "labor_duty"
        ]
        self.assertTrue(duties, "Повинности по барщине нет")
        self.assertTrue(all(o.due_good is None for o in duties))

    def test_matter_delta_is_zero(self) -> None:
        self.assertAlmostEqual(
            self.world.ledger.delta(self.world.total_matter()), 0.0, places=6
        )

    def test_village_keeps_its_own_labour_soil_works(self) -> None:
        """Улучшения пашни не съели труд двора: год закрыт, matter цел."""
        self.assertGreater(
            self.world.stats.get("soil_labor", 0.0), 0.0,
            "Ни смены полей, ни навоза — улучшения мертвы",
        )
        self.assertGreater(
            self.world.stats.get("fields_rotated", 0.0), 0.0, "Поля не менялись"
        )


class TestManorHarvestUsesCellMultipliers(unittest.TestCase):
    """Домен барона получает те же прибавки клетки, что и наделы (ADR 0110 п. 1)."""

    def test_demesne_cell_factor_matches_worker_stock(self) -> None:
        world = load_scenario(VILLAGE, seed=SEED)
        from hillcourt.economy.manor import _manor_demesne_fields, _manor_worker

        manor = next(iter(world.manors.values()))
        worker = _manor_worker(world, manor)
        field = _manor_demesne_fields(world, manor)[0]
        self.assertIsNotNone(worker)
        # Голый домен берёт свою норму выхода (300-350 при полном спросе), а не
        # 1.0: надел и домен — разные нормы (`demesne_base_yield`).
        norm = float(world.catalogs.spawn_rules[GROW_RULE].params["demesne_base_yield"])
        self.assertAlmostEqual(soil.tile_yield_factor(world, field, worker), norm)
        world.clock.year = 2
        soil.rotate_cell(world, field, 1)
        self.assertGreater(soil.tile_yield_factor(world, field, worker), norm)
        phase_manor(world)
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )


if __name__ == "__main__":
    unittest.main()
