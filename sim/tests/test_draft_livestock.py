"""Скот и тягло: каталог, пахота, воз, приплод, корм, служба тэна (ADR 0025).

Зонды держат наблюдаемые факты приёмки:
  - в стоке различаются вол/осёл/конь и пол; старого общего `horse` нет;
  - пахота с волом даёт больше зерна с того же труда, чем без (выход труда,
    не зерно с неба); конь хуже вола, осёл не пашет;
  - воз с ослом/конём везёт иначе, чем пеший (ёмкость/дни), телега нужна
    по-прежнему;
  - приплод редок (шанс ≤ 0.1), кобыла не исчезает, молодняк появляется,
    материя сходится;
  - скот ест сено двора/амбара, без корма гибнет;
  - служба тэна не сломана: пожалование работает без коней, рабочий конь
    нормой не считается (только `war_kit`), боевое применение — чужой зоне.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy import caravan, livestock
from hillcourt.economy.labor import work_month
from hillcourt.engine.manor import fief_kits, grant_thegn
from hillcourt.engine.tick import phase_consume, run_month
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
HILL_SALT = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
TWO = ROOT / "design" / "scenarios" / "v0_two_settlements.yml"
SHIRE = ROOT / "design" / "scenarios" / "v0_shire.yml"
GRAIN_RULE = "caravan_grain_to_ash"

MALES = ("ox_m", "donkey_m", "horse_m")
FEMALES = ("ox_f", "donkey_f", "horse_f")
YOUNG = ("ox_calf", "donkey_foal", "horse_foal")


def _give(world, stock_id: str, good: str, amount: float) -> None:
    """Положить благо в сток прямой записью (подготовка зонда, не тик).

    В отличие от `external_in`, не трогает журнал: `capture_initial` после
    подготовки видит полный итог, и дельта честно показывает баланс тика.
    """
    world.get_stock(stock_id).amounts[good] = float(amount)


class TestHerdSumCache(unittest.TestCase):
    """Кэш суммы стойловых голов поселения: быстро И равно перебору (ADR 0096 п. 1).

    `soil.settlement_livestock_units` зовётся на каждую клетку-кандидат каждого
    рецепта каждого двора: замер `v0_barony_100` — 26 769 вызовов на месяц. Без
    кэша это 1 362 392 вызова `livestock_units`; с кэшем — 27 992. Кэш поэтому
    ОБЯЗАН быть равен перебору, и инвалидация обязана ловить смену стада внутри
    месяца — числом, а не флагом.

    Места, где стадо меняется (все в `economy/`):
      * `labor.apply_recipe` — `mature_*` переводит телёнка в корову/вола
        (`ox_calf` 0.4 → `ox_m` 1.2, то есть **+0.8** к сумме);
      * `livestock.livestock.py:329` — смерть от голода (`starved`), минус рацион;
      * `livestock.livestock.py:441` — падёж (`died`), минус рацион.
    Приход и уход двоя ловятся структурной частью ключа (длина
    `household_ids` и число ушедших), потому что они происходят вне `economy/`.
    """

    def _herd_world(self, calf: float = 1.0):
        world = load_scenario(HILL_SALT, seed=1729)
        household = world.households["hh_02"]
        world.households = {household.id: household}
        stock = world.get_stock(household.stock_id)
        stock.amounts.clear()
        stock.amounts["ox_calf"] = calf
        stock.amounts["hay"] = 10.0
        return world, household, stock

    def test_maturing_a_calf_changes_the_sum_by_number(self) -> None:
        """Тёлка → вол внутри месяца: сумма обязана вырасти РОВНО на 0.8.

        Это и есть главная проверка кэша. Без неё кэш тихо врал бы: год к году
        одно и то же поголовье давало бы один и тот же множитель навоза, а
        выросший вол не давал бы подстилки. Мутация «инвалидация выключена»
        возвращает старое значение, и тест падает на `0.8 != 0.0`.
        """
        from hillcourt.economy import soil
        from hillcourt.economy.labor import apply_recipe

        world, household, stock = self._herd_world()
        settlement_id = household.settlement_id
        before = soil.settlement_livestock_units(world, settlement_id)
        self.assertAlmostEqual(before, 0.4, places=9, msg="стенд: не телёнка")
        recipe = world.catalogs.recipes["mature_ox_calf_m"]
        apply_recipe(world, household, world.tiles[household.current_tile_id], recipe, 1.0,
                     world.clock.date)
        after = soil.settlement_livestock_units(world, settlement_id)
        self.assertAlmostEqual(
            after - before, 0.8, places=9,
            msg=(
                "стадо изменилось в этом же месяце, а сумма поселения — нет: "
                f"было {before:.6f}, стало {after:.6f}"
            ),
        )
        self.assertAlmostEqual(after, 1.2, places=9)
        self.assertEqual(stock.amounts.get("ox_calf", 0.0), 0.0)

    def test_death_inside_a_month_changes_the_sum_by_number(self) -> None:
        """Смерть от голода в этом же месяце: минус рацион, а не ноль."""
        from hillcourt.economy import livestock, soil

        world, household, stock = self._herd_world(calf=0.0)
        stock.amounts["ox_m"] = 1.0
        settlement_id = household.settlement_id
        before = soil.settlement_livestock_units(world, settlement_id)
        self.assertAlmostEqual(before, 1.2, places=9, msg="стенд: не вол")
        livestock._die_stock(
            world, stock, world.clock.date, die_adult=1.0, die_young=1.0,
        )
        after = soil.settlement_livestock_units(world, settlement_id)
        self.assertAlmostEqual(
            after, 0.0, places=9,
            msg=f"палёж вола не увиден кэшем: было {before:.6f}, стало {after:.6f}",
        )

    def test_cache_equals_the_scan_on_every_scenario(self) -> None:
        """Кэш == перебор на всех семи сценариях, месяц за месяцем.

        Расхождение хотя бы в одной единице означало бы, что кэш неверен, и
        месяц тихо поехал бы. Здесь сверка идёт с ЭТАЛОНА
        (`settlement_livestock_units_uncached`, без кэша) по каждому двору после
        каждого месяца — на тех же мирах, где сценарий грузится.
        """
        from pathlib import Path

        from hillcourt.economy import soil
        from hillcourt.engine.tick import run_month

        for path in sorted((ROOT / "design" / "scenarios").glob("*.yml")):
            if path.name.startswith("tmp"):
                continue
            world = load_scenario(path, seed=1729)
            months = 2 if "barony" in path.name else 6
            for month in range(1, months + 1):
                for hid in sorted(world.households):
                    settlement_id = world.households[hid].settlement_id
                    with self.subTest(scenario=path.name, month=month, household=hid):
                        self.assertAlmostEqual(
                            soil.settlement_livestock_units(world, settlement_id),
                            soil.settlement_livestock_units_uncached(world, settlement_id),
                            places=9,
                            msg=f"{path.name} M{month} {hid}: кэш разошёлся с перебором",
                        )
                run_month(world)


class TestDraftCatalog(unittest.TestCase):
    """Каталог: виды, пол, корм, рецепты приплода и взросления."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = load_scenario(HILL_SALT)

    def test_species_and_sex_in_stock(self) -> None:
        for good in MALES + FEMALES + YOUNG:
            self.assertIn(good, self.world.catalogs.goods, f"Нет товара {good}")
            rule = self.world.catalogs.goods[good]
            self.assertEqual(rule.category, "livestock")
            self.assertEqual(rule.storage, "barn")
            self.assertFalse(rule.edible)
            self.assertTrue(rule.name, f"У {good} нет русского имени")

    def test_no_generic_horse(self) -> None:
        self.assertNotIn(
            "horse", self.world.catalogs.goods,
            "Общий `horse` остался рядом с половыми: пол не различается",
        )

    def test_feed_covers_draft(self) -> None:
        for good in MALES + FEMALES + YOUNG:
            self.assertIn(good, self.world.needs.feed_per_month, f"{good} не ест")
            self.assertGreater(self.world.needs.feed_per_month[good], 0.0)
        self.assertEqual(self.world.needs.feed_good, "hay")

    def test_breed_and_mature_recipes_balance(self) -> None:
        recipes = self.world.catalogs.recipes
        breeding = sorted(
            rid
            for rid in recipes
            if rid.startswith("breed_") or rid.startswith("mature_")
        )
        # Обход по префиксу без проверки непустоты — тест, который ничего не
        # проверяет: убери из каталога все `breed_*`/`mature_*`, и он останется
        # зелёным (ADR 0155). Поэтому список сначала сверяется с **данными модуля**:
        # каждое животное из MALES/FEMALES должно иметь рецепт размножения, каждое
        # из YOUNG — рецепт взросления. Проверка выведена из вида скота, а не из
        # выдуманного числа.
        for good in MALES + FEMALES:
            with self.subTest(species=good, need="breed"):
                self.assertTrue(
                    [rid for rid in breeding if good.split("_")[0] in rid],
                    f"{good}: в каталоге нет рецепта размножения",
                )
        for good in YOUNG:
            with self.subTest(species=good, need="mature"):
                self.assertTrue(
                    [rid for rid in breeding if good.split("_")[0] in rid],
                    f"{good}: в каталоге нет рецепта взросления",
                )
        for rid in breeding:
            with self.subTest(recipe=rid):
                recipe = recipes[rid]
                left = sum(recipe.inputs.values()) + sum(recipe.draws_standing.values())
                right = sum(recipe.outputs.values()) + sum(recipe.loss.values())
                self.assertAlmostEqual(left, right, places=6, msg=f"{rid}: нет баланса")
                self.assertTrue(recipe.transform, f"{rid}: нет transform")
                self.assertGreater(recipe.labor_days, 0.0)
                for good in list(recipe.outputs) + list(recipe.loss):
                    self.assertIn(good, self.world.catalogs.goods)


class TestPloughDraft(unittest.TestCase):
    """Пахота: вол ≠ без тягла; конь хуже вола; осёл не пашет."""

    def _harvest_gain(
        self, draft: dict[str, float], household_id: str = "hh_02"
    ) -> tuple[float, float]:
        world = load_scenario(HILL_SALT, seed=1729)
        household = world.households[household_id]
        world.households = {household_id: household}
        world.clock.month = 9
        stock = world.get_stock(household.stock_id)
        stock.amounts["grain"] = 0.0
        for good in MALES + FEMALES + YOUNG:
            stock.amounts.pop(good, None)
        for good, amount in draft.items():
            _give(world, household.stock_id, good, amount)
        _give(world, household.stock_id, "hay", 10.0)
        household.labor_days = 40.0
        household.main_action = "work_plot"
        household.minor_action = "idle_repair"
        for tile in world.tiles.values():
            tile_stock = world.get_stock(tile.standing_stock_id)
            if tile.terrain == "field":
                tile_stock.amounts["grain"] = 200.0
        world.ledger.capture_initial(world.total_matter())
        work_month(world, world.clock.date)
        return (
            stock.amounts.get("grain", 0.0),
            world.ledger.delta(world.total_matter()),
        )

    def test_ox_beats_no_draft_horse_middle_donkey_none(self) -> None:
        none, delta_none = self._harvest_gain({})
        ox, delta_ox = self._harvest_gain({"ox_m": 1.0, "ox_f": 1.0})
        # Конь — только у свободного (tied-конь в тягло не идёт, ADR 0025).
        horse, delta_horse = self._harvest_gain({"horse_m": 1.0}, household_id="hh_01")
        donkey, delta_donkey = self._harvest_gain({"donkey_m": 1.0})
        self.assertGreater(none, 0.0, "Базовая жатва не дала зерна")
        self.assertGreater(ox, none, "Вол не прибавил выхода с того же труда")
        self.assertLess(horse, ox, "Конь пашет как вол или лучше")
        self.assertGreater(horse, none, "Конь вовсе не пашет (правило: хуже вола)")
        self.assertAlmostEqual(donkey, none, places=6, msg="Осёл пашет, а не должен")
        for delta in (delta_none, delta_ox, delta_horse, delta_donkey):
            self.assertAlmostEqual(delta, 0.0, places=6)

    def test_tied_household_horse_is_no_draft(self) -> None:
        """tied-двор коня не использует: пахота как без тягла, из решений конь убран."""
        world = load_scenario(HILL_SALT, seed=1729)
        tied = world.households["hh_02"]
        free = world.households["hh_01"]
        self.assertFalse(livestock.can_hold_horse(world, tied))
        self.assertTrue(livestock.can_hold_horse(world, free))
        _give(world, tied.stock_id, "horse_m", 1.0)
        self.assertEqual(
            livestock.draft_plough_factor(world, tied),
            1.0,
            "tied-двор пашет конём",
        )
        self.assertNotIn(
            "horse_m", livestock.draft_goods_for(world, tied),
            "tied-конь попал в тягло решений",
        )
        self.assertIn("horse_m", livestock.draft_goods_for(world, free))
        self.assertIn(
            "ox_m", livestock.draft_goods_for(world, tied),
            "Вол запрещён tied-двору, а он разрешён всем",
        )

    def test_tied_horse_does_not_pull_cart(self) -> None:
        """Конь tied-двора не тянет воз поселения (`origin_draft`)."""
        world = load_scenario(HILL_SALT)
        _give(world, "household:hh_02", "horse_m", 2.0)  # tied, fs_02
        self.assertEqual(caravan.origin_draft(world, "fs_02"), "none")
        _give(world, "household:hh_02", "donkey_m", 1.0)
        self.assertEqual(caravan.origin_draft(world, "fs_02"), "donkey")
        _give(world, "household:hh_01", "horse_m", 1.0)  # free, fs_01
        self.assertEqual(caravan.origin_draft(world, "fs_01"), "horse")



class TestPackDraft(unittest.TestCase):
    """Воз: осёл/конь везут иначе пешего; телега нужна по-прежнему."""

    def test_capacity_and_days_order(self) -> None:
        world = load_scenario(TWO)
        rule = world.catalogs.spawn_rules[GRAIN_RULE]
        base = float(rule.params["cargo_amount"])
        none_cart = caravan.caravan_capacity(rule, True, "none")
        donkey_cart = caravan.caravan_capacity(rule, True, "donkey")
        horse_cart = caravan.caravan_capacity(rule, True, "horse")
        self.assertAlmostEqual(none_cart, base * 1.5, places=6)
        self.assertAlmostEqual(donkey_cart, base * 1.5 * 1.2, places=6)
        self.assertAlmostEqual(horse_cart, base * 1.5 * 1.3, places=6)
        # Без телеги воз жив, но хуже — и с тяглом тоже хуже тележного.
        horse_bare = caravan.caravan_capacity(rule, False, "horse")
        self.assertLess(horse_bare, none_cart, "Тягло заменило телегу")
        self.assertGreater(
            horse_bare, caravan.caravan_capacity(rule, False, "none")
        )
        self.assertAlmostEqual(caravan.caravan_days(7, True, "none"), 7.0, places=6)
        self.assertAlmostEqual(caravan.caravan_days(7, True, "donkey"), 7.0, places=6)
        self.assertLess(
            caravan.caravan_days(7, True, "horse"),
            caravan.caravan_days(7, True, "none"),
            "Конь не ускорил воз",
        )

    def test_origin_draft_horse_over_donkey_ox_ignored(self) -> None:
        world = load_scenario(TWO)
        for hh in world.households.values():
            if hh.settlement_id == "hill_court":
                stock = world.get_stock(hh.stock_id)
                for good in MALES + FEMALES + YOUNG:
                    stock.amounts.pop(good, None)
        world.get_stock("settlement:hill_court").amounts.pop("donkey_m", None)
        self.assertEqual(caravan.origin_draft(world, "hill_court"), "none")
        _give(world, "household:hh_court", "ox_m", 2.0)
        _give(world, "household:hh_court", "horse_foal", 3.0)
        self.assertEqual(
            caravan.origin_draft(world, "hill_court"), "none",
            "Вол и молодняк тянут воз",
        )
        _give(world, "household:hh_court", "donkey_m", 1.0)
        self.assertEqual(caravan.origin_draft(world, "hill_court"), "donkey")
        _give(world, "household:hh_court", "horse_f", 1.0)
        self.assertEqual(caravan.origin_draft(world, "hill_court"), "horse")


class TestBreeding(unittest.TestCase):
    """Приплод редок; кобыла не исчезает; материя сходится."""

    def test_breeding_is_rare(self) -> None:
        self.assertGreater(livestock.BREED_PROB, 0.0)
        self.assertLessEqual(livestock.BREED_PROB, 0.1, "Приплод не редок")

    def test_forced_breeding_keeps_mare_gives_foal(self) -> None:
        world = load_scenario(HILL_SALT, seed=1729)
        household = world.households["hh_06"]
        world.households = {"hh_06": household}
        stock = world.get_stock(household.stock_id)
        stock.amounts["pig"] = 0.0
        _give(world, household.stock_id, "horse_m", 1.0)
        _give(world, household.stock_id, "horse_f", 1.0)
        _give(world, household.stock_id, "hay", 10.0)
        world.ledger.capture_initial(world.total_matter())
        livestock.livestock_month(
            world, world.clock.date, breed_prob=1.0, grow_prob=0.0,
            die_adult=0.0, die_young=0.0,
        )
        self.assertAlmostEqual(stock.amounts.get("horse_f", 0.0), 1.0, places=6,
                               msg="Кобыла исчезла в приплоде")
        self.assertAlmostEqual(stock.amounts.get("horse_m", 0.0), 1.0, places=6)
        self.assertAlmostEqual(stock.amounts.get("horse_foal", 0.0), 1.0, places=6,
                               msg="Молодняк не появился")
        # 10.0 − 4.0 (приплод `breed_horse`: 1.0 вход + 3.0 потери) − 1.0 (подстилка
        # `make_manure`, хлев с двумя кобылами) = 5.0. Подстилка законна при тягле
        # (`soil.livestock_units`, закон без тягла — `test_soil_improvements`).
        self.assertAlmostEqual(stock.amounts.get("hay", 0.0), 5.0, places=6)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_no_pair_no_offspring_no_rng_spend(self) -> None:
        world = load_scenario(HILL_SALT, seed=1729)
        household = world.households["hh_06"]
        world.households = {"hh_06": household}
        stock = world.get_stock(household.stock_id)
        stock.amounts["pig"] = 0.0
        _give(world, household.stock_id, "horse_f", 1.0)
        _give(world, household.stock_id, "hay", 10.0)
        world.ledger.capture_initial(world.total_matter())
        livestock.livestock_month(
            world, world.clock.date, breed_prob=1.0, grow_prob=0.0,
            die_adult=0.0, die_young=0.0,
        )
        self.assertAlmostEqual(stock.amounts.get("horse_foal", 0.0), 0.0, places=6,
                               msg="Приплод без пары")
        # 10.0 − 1.0 (подстилка `make_manure`: кобыла — тягло, хлев даёт навоз) = 9.0.
        # Приплода нет — значит и нечего кормить; подстилка от пары не зависит.
        self.assertAlmostEqual(stock.amounts.get("hay", 0.0), 9.0, places=6)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_maturation_grows_foal_to_adult(self) -> None:
        world = load_scenario(HILL_SALT, seed=1729)
        household = world.households["hh_06"]
        world.households = {"hh_06": household}
        stock = world.get_stock(household.stock_id)
        stock.amounts["pig"] = 0.0
        _give(world, household.stock_id, "horse_foal", 2.0)
        _give(world, household.stock_id, "hay", 10.0)
        world.ledger.capture_initial(world.total_matter())
        livestock.livestock_month(
            world, world.clock.date, breed_prob=0.0, grow_prob=1.0,
            die_adult=0.0, die_young=0.0,
        )
        self.assertAlmostEqual(stock.amounts.get("horse_foal", 0.0), 0.0, places=6)
        adults = stock.amounts.get("horse_m", 0.0) + stock.amounts.get("horse_f", 0.0)
        self.assertAlmostEqual(adults, 2.0, places=6, msg="Взрослые не выросли")
        # 10.0 − 4.0 (взросление двух жеребят: по 2.0 сена на голову) − 1.0
        # (подстилка `make_manure`) = 5.0.
        self.assertAlmostEqual(stock.amounts.get("hay", 0.0), 5.0, places=6)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


class TestFeed(unittest.TestCase):
    """Скот ест сено двора; без корма гибнет; материя сходится."""

    def test_hungry_ox_dies_fed_ox_lives(self) -> None:
        world = load_scenario(HILL_SALT, seed=1729)
        household = world.households["hh_02"]
        world.households = {"hh_02": household}
        stock = world.get_stock(household.stock_id)
        stock.amounts["grain"] = 50.0
        _give(world, household.stock_id, "ox_m", 1.0)
        stock.amounts["hay"] = 0.0
        world.ledger.capture_initial(world.total_matter())
        phase_consume(world)
        self.assertAlmostEqual(stock.amounts.get("ox_m", 0.0), 0.0, places=6,
                               msg="Вол пережил голод без сена")
        self.assertAlmostEqual(
            world.get_stock("sink:waste").amounts.get("ox_m", 0.0), 1.0, places=6
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

        stock.amounts["hay"] = 5.0
        _give(world, household.stock_id, "ox_m", 1.0)
        world.ledger.capture_initial(world.total_matter())
        phase_consume(world)
        self.assertAlmostEqual(stock.amounts.get("ox_m", 0.0), 1.0, places=6,
                               msg="Сытый вол пал")
        # 5.0 − 1.2 (корм волу, `feed`) − 1.0 (подстилка `make_manure`, хлев с волом)
        # = 2.8. Подстилка законна при тягле: `soil.livestock_units` считает вола.
        self.assertAlmostEqual(stock.amounts.get("hay", 0.0), 5.0 - 1.2 - 1.0, places=6)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_manor_barn_feeds_too(self) -> None:
        world = load_scenario(HILL_SALT, seed=1729)
        manor = grant_thegn(world, "hh_retinue_p1", ["t_03_01", "t_05_02"], ["hh_02"])
        self.assertIsNotNone(manor)
        barn = world.get_stock(manor.stock_id)
        _give(world, manor.stock_id, "donkey_f", 1.0)
        barn.amounts["hay"] = 0.0
        world.ledger.capture_initial(world.total_matter())
        livestock.livestock_month(
            world, world.clock.date, breed_prob=0.0, grow_prob=0.0,
            die_adult=0.0, die_young=0.0,
        )
        self.assertAlmostEqual(barn.amounts.get("donkey_f", 0.0), 0.0, places=6,
                               msg="Амбарный скот не ест")
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


class TestHorseHoldingAndService(unittest.TestCase):
    """Конь — у свободных; служба тэна — только комплекты, не табун."""

    def test_only_free_hold_horse(self) -> None:
        world = load_scenario(HILL_SALT)
        for hid in ("hh_01", "hh_retinue", "hh_court", "hh_04"):
            self.assertTrue(
                livestock.can_hold_horse(world, world.households[hid]),
                f"Свободный {hid} не держит коня",
            )
        for hid in ("hh_02", "hh_03", "hh_05"):
            self.assertFalse(
                livestock.can_hold_horse(world, world.households[hid]),
                f"Tied-двор {hid} держит коня",
            )

    def test_no_tied_starts_with_horse(self) -> None:
        for path in (HILL_SALT, TWO, SHIRE):
            world = load_scenario(path)
            for hid, household in world.households.items():
                if household.personal_status != "free":
                    stock = world.get_stock(household.stock_id)
                    for good in ("horse_m", "horse_f"):
                        self.assertAlmostEqual(
                            stock.amounts.get(good, 0.0), 0.0, places=6,
                            msg=f"{path.name}: tied-двор {hid} начинает с {good}",
                        )

    def test_thegn_grant_works_without_horses(self) -> None:
        world = load_scenario(HILL_SALT, seed=1729)
        manor = grant_thegn(world, "hh_retinue_p1", ["t_03_01", "t_05_02"], ["hh_02"])
        self.assertIsNotNone(manor, "Нет коня → нет тэна: служба сломана")

    def test_working_horse_is_not_service_kit(self) -> None:
        world = load_scenario(HILL_SALT, seed=1729)
        manor = grant_thegn(world, "hh_retinue_p1", ["t_03_01", "t_05_02"], ["hh_02"])
        holder = world.households["hh_retinue"]
        _give(world, manor.stock_id, "grain", 100.0)
        _give(world, holder.stock_id, "grain", 10.0)
        _give(world, holder.stock_id, "hay", 30.0)
        _give(world, holder.stock_id, "horse_m", 3.0)
        world.ledger.capture_initial(world.total_matter())
        for _ in range(3):
            run_month(world)
        self.assertAlmostEqual(fief_kits(world, manor), 0.0, places=6,
                               msg="Табун засчитан комплектами")
        self.assertGreater(manor.service_gap, 0.0)
        self.assertFalse(manor.mustered, "mustered без комплекта на коне")
        self.assertGreaterEqual(
            world.get_stock(holder.stock_id).amounts.get("horse_m", 0.0),
            2.0, msg="Рабочий конь съеден службой",
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


class TestHerdStreamIsolation(unittest.TestCase):
    """Стойлу — свой жребий: приплод/взросление/падёж не едят `rng.economy`.

    Долг ADR 0030/0032: жребий стойла реролил трио/соль через общий поток.
    Хеш-жребий (`seed:herd:сток:месяц:цель:номер`) детерминирован и потока
    не трогает: демография и обмен не сдвигаются от скота.
    """

    def _herd_world(self):
        world = load_scenario(HILL_SALT, seed=1729)
        household = world.households["hh_06"]
        world.households = {"hh_06": household}
        stock = world.get_stock(household.stock_id)
        stock.amounts["pig"] = 0.0
        _give(world, household.stock_id, "horse_m", 1.0)
        _give(world, household.stock_id, "horse_f", 1.0)
        _give(world, household.stock_id, "horse_foal", 2.0)
        _give(world, household.stock_id, "hay", 50.0)
        return world, stock

    def test_herd_rolls_leave_shared_stream_untouched(self) -> None:
        world, stock = self._herd_world()
        world.ledger.capture_initial(world.total_matter())
        before = world.rng.economy.getstate()
        livestock.livestock_month(
            world, world.clock.date, breed_prob=1.0, grow_prob=1.0,
            die_adult=1.0, die_young=1.0,
        )
        self.assertEqual(
            world.rng.economy.getstate(), before,
            "Стойло съело общий поток: демография реролится от скота",
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_herd_outcomes_are_deterministic(self) -> None:
        first, _ = self._herd_world()
        second, _ = self._herd_world()
        for world in (first, second):
            livestock.livestock_month(
                world, world.clock.date, breed_prob=1.0, grow_prob=1.0,
                die_adult=1.0, die_young=1.0,
            )
        self.assertEqual(
            dict(first.get_stock(first.households["hh_06"].stock_id).amounts),
            dict(second.get_stock(second.households["hh_06"].stock_id).amounts),
            "Жребий стойла разошёлся на том же seed",
        )


class TestSeasonalGraze(unittest.TestCase):
    """Сезонный выпас (ADR 0053, шаг 1): сено — низкий сезон, летом подножный.

    Зимняя норма из каталога; в `graze_months` сено только коэффициентом
    (`graze_hay_fraction`, рабочий скот тоже с коэффициентом, не нулём).
    Корм амбаров и гейты излишка — сезонные; корм дворов — следующим шагом
    (движок, `tick._feed_livestock`), замеры полной модели — стендом в ADR.
    """

    def test_winter_full_summer_fraction(self) -> None:
        from hillcourt.economy.needs import hay_fraction, hay_need_rate

        world = load_scenario(HILL_SALT, seed=1729)
        self.assertEqual(sorted(world.needs.graze_months), [5, 6, 7, 8, 9])
        self.assertAlmostEqual(float(world.needs.graze_hay_fraction), 0.3, places=6)
        self.assertAlmostEqual(hay_fraction(world, 1), 1.0, places=6)
        self.assertAlmostEqual(hay_fraction(world, 7), 0.3, places=6)
        self.assertAlmostEqual(hay_need_rate(world, "ox_m", 1), 1.2, places=6)
        self.assertAlmostEqual(hay_need_rate(world, "ox_m", 7), 0.36, places=6)

    def test_barn_feeds_seasonal(self) -> None:
        from hillcourt.ontology import SimDate

        world = load_scenario(HILL_SALT, seed=1729)
        manor = grant_thegn(world, "hh_retinue_p1", ["t_03_01", "t_05_02"], ["hh_02"])
        barn = world.get_stock(manor.stock_id)
        _give(world, manor.stock_id, "ox_m", 1.0)
        _give(world, manor.stock_id, "hay", 10.0)
        world.ledger.capture_initial(world.total_matter())
        livestock.livestock_month(
            world, SimDate(1, 1, 1), breed_prob=0.0, grow_prob=0.0,
            die_adult=0.0, die_young=0.0,
        )
        self.assertAlmostEqual(
            barn.amounts.get("hay", 0.0), 10.0 - 1.2 - 1.0, places=6,
            msg="Амбар зимой скормил не полную норму",
        )
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )

    @staticmethod
    def _grant_pasture_access(world, household, tile_id: str) -> None:
        """Открыть двору пастбище по закону: клетка в `works_tiles` поселения.

        ADR 0068 (Legal): communal-доступ дают только `works_tiles` поселения и
        `Right.kind=common`; одного соседства больше не хватает. Фикстура теста
        оформляет право честно, данными сценария не трогая.
        """
        settlement = world.settlements.get(household.settlement_id or "")
        if settlement is not None and tile_id not in settlement.works_tiles:
            settlement.works_tiles.append(tile_id)

    def test_summer_graze_capped_then_hay(self) -> None:
        from hillcourt.ontology import SimDate

        world = load_scenario(HILL_SALT, seed=1729)
        household = world.households["hh_09"]
        stock = world.get_stock(household.stock_id)
        stock.amounts.clear()
        _give(world, household.stock_id, "ox_m", 1.0)
        _give(world, household.stock_id, "hay", 10.0)
        self._grant_pasture_access(world, household, "t_05_03")
        pastures = livestock.hay_pastures(world, household)
        self.assertTrue(pastures, "Нет пастбища для выпаса")
        for tile in pastures:
            world.get_stock(tile.standing_stock_id).amounts["hay"] = 20.0
        world.ledger.capture_initial(world.total_matter())
        livestock._feed_stock(world, stock, SimDate(1, 7, 1))
        grazed = sum(
            e.amount for e in world.ledger.entries if e.reason == "graze"
        )
        self.assertAlmostEqual(grazed, 0.84, places=6, msg="Выпас дал не 70 % нормы")
        self.assertAlmostEqual(
            stock.amounts.get("hay", 0.0), 10.0 - 0.36, places=6,
            msg="Сено летом съедено не коэффициентом",
        )
        for tile in pastures:
            used = world.stats.get("graze_used:0001-07:" + tile.id, 0.0)
            self.assertLessEqual(used, 2.0 + 1e-9, f"{tile.id}: кап ёмкости пробит")
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )

    def test_graze_gap_falls_back_to_hay(self) -> None:
        from hillcourt.ontology import SimDate

        world = load_scenario(HILL_SALT, seed=1729)
        manor = grant_thegn(world, "hh_retinue_p1", ["t_03_01", "t_05_02"], ["hh_02"])
        barn = world.get_stock(manor.stock_id)
        _give(world, manor.stock_id, "ox_m", 1.0)
        _give(world, manor.stock_id, "hay", 10.0)
        world.ledger.capture_initial(world.total_matter())
        livestock.livestock_month(
            world, SimDate(1, 7, 1), breed_prob=0.0, grow_prob=0.0,
            die_adult=0.0, die_young=0.0,
        )
        grazed = [e for e in world.ledger.entries if e.reason == "graze"]
        self.assertEqual(grazed, [], "Выпас из ничего при пустых пастбищах")
        self.assertAlmostEqual(
            barn.amounts.get("hay", 0.0), 10.0 - 1.2 - 1.0, places=6,
            msg="Недобор выпаса не покрыт сеном",
        )
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )


if __name__ == "__main__":
    unittest.main()
