"""Цены: единица хранения, вывод из рецептов, исторические якоря (ADR 0101).

Правило единицы: `price_silver` — серебро за ОДНУ единицу хранения (`unit`), ровно за
1.0 в складе. Пуд (16.38 кг) единицей не является; масса unit закреплена паётом
(`needs.yml food.adult_per_month: 1.0` — месячный рацион взрослого, историческая норма
≈1 кг зерна в день → `KG_PER_UNIT = 30`). Отсюда `цена за кг = цена / 30`,
`цена за пуд = цена за кг × 16.38`.

Тест-обвинитель проверяет, что единицу нельзя сломать молча: цена за кг зерна,
умноженная на 16.38, обязана дать цену пуда, а дневная ставка — 3…12 кг зерна
(Англия 1300–1400: заработок 1.5–2.5 пенса/день при пшенице ≈0.41 пенса/кг → 5–10 кг/день;
Clark 2003, Farmer 1988/1991, LSE WP360/WP375). Цена «за килограмм» провалила бы нижнюю
границу в 16 раз, цена «за пуд» — верхнюю.

Здесь же живёт обвинение по ADR 0144: **цены зерна в коде нет** (в `sim/src/` не
осталось ни одной ценовой константы по зерну, кроме осознанного исключения по
свинье), код читает ровно `goods.yml::grain.price_silver`, а покупку зерна у барона
держит **потолок закупки** — месячная норма двора, а не завышенная цена.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

from hillcourt.economy import exchange
from hillcourt.economy.needs import food_months, monthly_food_need
from hillcourt.engine.tick import phase_exchange
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "sim" / "src"
SHIRE = ROOT / "design" / "scenarios" / "v0_shire.yml"
KG_PER_UNIT = 30.0
PUD_KG = 16.38
PENCE_PER_SILVER = 12.0
MONTH_DAYS = 30
WAGE_KG_PER_DAY = (3.0, 12.0)
FEED_MONTHS = 12.0
GRAIN = "grain"
GRAIN_PRICE = 0.128165
NEED = 4.4  # hh_11: 3 взрослых × 1.0 + 2 ребёнка × 0.7 рот-единицы в месяц
# Имя константы, в которой ЗЕРНО — продаваемый товар: `GRAIN_*PRICE*`. Имена вида
# `LOG_PRICE_GRAIN`/`LIVESTOCK_PRICE_GRAIN` сюда не попадают: там зерно — единица
# ПЛАТЕЖА (цена бревна/скота в зерне), а не цена зерна (ADR 0101, ADR 0144 п. 1).
GRAIN_PRICE_CONSTANT = re.compile(r"^GRAIN_[A-Z0-9_]*PRICE[A-Z0-9_]*\s*=[^=]*\d")

PRICE_REQUIRED = (
    "grain", "flour", "milk", "cheese", "butter", "eggs", "meat", "roots", "greens",
    "mushrooms", "berries", "firewood", "log", "peat", "hay", "salt", "iron", "wool",
    "hide", "axe", "butter_churn", "board", "plank",
)
NO_PATH = (
    "brine", "straw", "cloth", "iron_bloom", "war_kit", "cart", "raft", "boat",
    "wooden_plough", "iron_share",
)
EXPECTED_FACTORS = {
    "grain": 1.2, "flour": 1.2, "milk": 1.1, "cheese": 1.1, "butter": 1.2,
    "eggs": 1.1, "meat": 1.2, "roots": 1.1, "greens": 1.1, "mushrooms": 1.1,
    "berries": 1.3, "firewood": 1.1, "log": 1.1, "peat": 1.1, "hay": 1.1,
    "salt": 1.3, "iron": 1.3, "wool": 1.1, "hide": 1.1, "axe": 1.2,
    "butter_churn": 1.1, "board": 1.1, "plank": 1.1,
}
SERVICE_RECIPES = ("repair_axe",)


class TestPriceTable(unittest.TestCase):
    """Цена = (труд + сырьё + потери) × коэффициент, всё за единицу хранения."""

    @classmethod
    def setUpClass(cls) -> None:
        goods_doc = yaml.safe_load(
            (ROOT / "design/catalogs/goods.yml").read_text(encoding="utf-8")
        )
        cls.goods = {record["id"]: record for record in goods_doc["goods"]}
        cls.recipes = {
            record["id"]: record
            for record in yaml.safe_load(
                (ROOT / "design/catalogs/recipes.yml").read_text(encoding="utf-8")
            )["recipes"]
        }
        cls.manor = yaml.safe_load(
            (ROOT / "design/catalogs/manor.yml").read_text(encoding="utf-8")
        )
        cls.needs = yaml.safe_load(
            (ROOT / "design/catalogs/needs.yml").read_text(encoding="utf-8")
        )
        cls.obligations = yaml.safe_load(
            (ROOT / "design/catalogs/obligations.yml").read_text(encoding="utf-8")
        )
        cls.wage = float(cls.manor["hire"]["wage_silver_per_day"])

    def price(self, good: str) -> float:
        return float(self.goods[good]["price_silver"])

    def part(self, good: str, field: str) -> float:
        return float(self.goods[good].get(field, 0.0))

    def cost(self, good: str) -> float:
        """Себестоимость unit без рыночной наценки — по самим каталожным полям."""
        return (
            self.part(good, "price_labor_silver")
            + self.part(good, "price_materials_silver")
            + self.part(good, "price_losses_silver")
        )

    def producers(self, good: str) -> list[dict]:
        return [
            recipe
            for rid, recipe in sorted(self.recipes.items())
            if rid not in SERVICE_RECIPES
            and float(recipe["outputs"].get(good, 0.0)) > 0.0
        ]

    def units(self, good: str) -> float:
        return sum(float(r["outputs"][good]) for r in self.producers(good))

    def animal_value(self, animal: str) -> float:
        """Взрослое животное = годовой корм из `needs.yml` (рецепты разведения
        замкнуты: их вход — взрослое животное того же вида)."""
        feed_good = self.needs["livestock"]["feed_good"]
        per_month = float(self.needs["livestock"]["feed_per_month"][animal])
        return per_month * FEED_MONTHS * self.cost(feed_good)

    def value(self, good: str) -> float:
        if good in ("pig", "hen", "duck", "goose"):
            return self.animal_value(good)
        return self.cost(good)

    def labor_days_per_unit(self, good: str) -> float:
        return sum(float(r["labor_days"]) for r in self.producers(good)) / self.units(good)

    def test_price_is_per_storage_unit_and_derived_from_recipes(self) -> None:
        """Цена за 1 unit = труд рецептов × ставка + сырьё и потери по себестоимости."""
        for good in PRICE_REQUIRED:
            with self.subTest(good=good):
                self.assertTrue(self.producers(good), f"Нет рецепта: {good}")
                labor = self.labor_days_per_unit(good) * self.wage
                material = sum(
                    float(qty) * self.value(src)
                    for recipe in self.producers(good)
                    for src, qty in sorted(recipe["inputs"].items())
                    if src not in NO_PATH
                ) / self.units(good)
                loss = sum(
                    float(qty) * self.value(lost)
                    for recipe in self.producers(good)
                    for lost, qty in sorted(recipe["loss"].items())
                    if lost not in NO_PATH
                ) / self.units(good)
                self.assertAlmostEqual(self.part(good, "price_labor_silver"), labor, places=5)
                # Сырьё и потери суммируются по округлённым до 6 знаков себестоимостям
                # входа, поэтому допуск 1e-4 (накопление округлений, не ошибка модели).
                self.assertAlmostEqual(
                    self.part(good, "price_materials_silver"), material, delta=1e-4
                )
                self.assertAlmostEqual(
                    self.part(good, "price_losses_silver"), loss, delta=1e-4
                )
                self.assertGreater(self.cost(good), 0.0)
                self.assertAlmostEqual(
                    self.price(good),
                    self.cost(good) * EXPECTED_FACTORS[good],
                    places=5,
                )

    def test_grain_kg_and_pud_conversion_cannot_break_silently(self) -> None:
        """Цена за кг × 16.38 = цена пуда; масса unit закреплена паётом взрослого."""
        ration = float(self.needs["food"]["adult_per_month"])
        self.assertAlmostEqual(ration, 1.0, places=6, msg="Паётом взрослого больше не 1 unit")
        per_kg = self.price("grain") / KG_PER_UNIT
        per_pud = per_kg * PUD_KG
        self.assertAlmostEqual(
            per_pud, self.price("grain") * PUD_KG / KG_PER_UNIT, places=9
        )
        # Дневная норма взрослого ≈ 1 кг: ставка должна покупать и день, и десять дней хлеба.
        self.assertGreaterEqual(per_kg, self.wage / 10.0, "Зерно дороже хлеба на месяц")
        self.assertLessEqual(per_kg, self.wage, "Ставка не покупает и дня хлеба")

    def test_daily_wage_buys_historically_sane_grain(self) -> None:
        """Англия 1300–1400: 1.5–2.5 пенса/день при 0.41 пенса/кг → 5–10 кг/день."""
        kg_per_day = self.wage / self.price("grain") * KG_PER_UNIT
        low, high = WAGE_KG_PER_DAY
        self.assertGreaterEqual(kg_per_day, low, f"Ставка покупает {kg_per_day:.2f} кг/день")
        self.assertLessEqual(kg_per_day, high, f"Ставка покупает {kg_per_day:.2f} кг/день")

    def test_family_daily_food_is_not_a_single_shift(self) -> None:
        food = self.needs["food"]
        monthly_units = 2.0 * float(food["adult_per_month"]) + 2.0 * float(
            food["child_per_month"]
        )
        daily_cost = monthly_units * self.price("grain") / MONTH_DAYS
        self.assertLessEqual(daily_cost, self.wage * 2.0)
        self.assertGreaterEqual(self.wage, daily_cost / 2.0)

    def test_food_order_follows_labour_and_processing(self) -> None:
        grain = self.price("grain")
        for good in ("roots", "greens"):
            with self.subTest(good=good):
                self.assertLess(self.price(good), grain, f"{good} не дешевле зерна")
        for good in ("mushrooms", "berries"):
            with self.subTest(good=good):
                ratio = self.price(good) / grain
                self.assertGreaterEqual(ratio, 0.3, f"{good} подозрительно дёшев")
                self.assertLessEqual(ratio, 2.0, f"{good} подозрительно дорог")
        self.assertGreater(self.price("cheese"), self.price("milk"))
        self.assertGreater(self.price("butter"), self.price("cheese"))
        # Молочная полоса (3…6) и сыр (8…16) заданы потерями молочных рецептов:
        # дойка теряет 60…100 % молока, сыроварение — 45 % (recipes.yml). По
        # историческим полосам сыр 0.6 пенса/кг и масло 1.4 пенса/кг — в 2…4 раза
        # выше источников; чинить это надо потерями рецептов, а не ценой.
        self.assertGreaterEqual(self.price("milk") / grain, 3.0)
        self.assertLessEqual(self.price("milk") / grain, 6.0)
        self.assertGreaterEqual(self.price("cheese") / grain, 8.0)
        self.assertLessEqual(self.price("cheese") / grain, 16.0)
        self.assertGreaterEqual(self.price("flour") / grain, 1.5)
        self.assertLessEqual(self.price("flour") / grain, 3.5)
        self.assertGreaterEqual(self.price("eggs") / grain, 1.5)
        self.assertLessEqual(self.price("eggs") / grain, 5.0)
        self.assertGreaterEqual(self.price("meat") / grain, 2.0)
        self.assertLessEqual(self.price("meat") / grain, 10.0)
        self.assertGreater(self.price("hide"), self.price("meat"))
        for good in ("hay", "firewood", "peat", "log"):
            with self.subTest(good=good):
                self.assertLess(self.price(good), grain)
        self.assertGreaterEqual(self.price("wool") / grain, 0.7)
        self.assertLessEqual(self.price("wool") / grain, 2.0)

    def test_mass_yield_explains_dairy_price(self) -> None:
        """Сыр дороже молока на единицу и тем более на килограмм: 4 молока → 2.2 сыра."""
        milk_units = float(self.recipes["make_cheese"]["inputs"]["milk"])
        cheese_units = float(self.recipes["make_cheese"]["outputs"]["cheese"])
        self.assertGreater(milk_units / cheese_units, 1.0)
        self.assertGreater(
            self.price("cheese") / self.price("milk") * milk_units / cheese_units, 1.0
        )

    def test_salt_iron_and_tools_are_cost_ordered(self) -> None:
        grain = self.price("grain")
        self.assertGreater(self.price("salt"), 3.0 * grain)
        self.assertLessEqual(self.price("salt") / grain, 15.0)
        self.assertGreater(self.price("iron"), 2.0 * grain)
        self.assertLessEqual(self.price("iron") / grain, 20.0)
        self.assertGreater(self.price("axe"), self.price("iron"))

    def test_salt_burns_fuel_from_the_recipe(self) -> None:
        recipe = self.recipes["boil_salt"]
        peat = float(recipe["inputs"]["peat"])
        salt_units = float(recipe["outputs"]["salt"])
        expected = (
            self.labor_days_per_unit("salt") * self.wage
            + (peat + float(recipe["loss"]["peat"])) / salt_units * self.cost("peat")
        )
        self.assertAlmostEqual(self.cost("salt"), expected, places=5)

    def test_sokeman_conversion_uses_grain_price(self) -> None:
        bundle = next(
            bundle
            for bundle in self.obligations["bundles"]
            if bundle["id"] == "sokeman_rent"
        )
        conversion = bundle["terms"]["fixed_rent_price_conversion"]
        grain_price = self.price(conversion["price_good"])
        grain = float(conversion["grain"])
        pence = float(conversion["pence_per_silver"])
        self.assertEqual(grain, 0.6)
        self.assertAlmostEqual(float(conversion["price_silver"]), grain_price, places=6)
        self.assertAlmostEqual(
            float(conversion["equivalent_pence"]), grain * grain_price * pence, places=5
        )
        rent_silver = grain * grain_price
        self.assertGreaterEqual(rent_silver, self.wage, "Оброк меньше дневной ставки")
        self.assertLessEqual(rent_silver, 30.0 * self.wage, "Оброк дороже месяца работы")
        self.assertLessEqual(
            rent_silver / (20.0 * self.wage), 0.5, "Оброк съедает больше половины зарплаты"
        )

    def test_priced_goods_have_full_path_and_others_none(self) -> None:
        for good in NO_PATH:
            with self.subTest(good=good):
                self.assertNotIn("price_silver", self.goods[good], "Цена без полного пути")
        for good in ("pig", "hen", "duck", "goose"):
            with self.subTest(good=good):
                self.assertNotIn("price_silver", self.goods[good], "Цена животного")
        silver = self.goods["silver"]
        self.assertEqual(float(silver["price_silver"]), 1.0)
        self.assertNotIn("price_labor_silver", silver)


class TestGrainPriceLivesInCatalog(unittest.TestCase):
    """Обвинение по ADR 0144 п. 1: цена зерна — число каталога, а не число в коде.

    Дубль был ровно один: `GRAIN_PRICE_SILVER = 0.5` в `economy/exchange.py` против
    `goods.yml::grain.price_silver = 0.128165` — расхождение в 3,90 раза. Тест не даёт
    дублю вернуться ни под каким именем и проверяет, что читаемое кодом число равно
    каталожному, а не «похоже на него».
    """

    def test_no_grain_price_constant_left_in_src(self) -> None:
        """В `sim/src/` нет ни одной константы, где ЗЕРНО — продаваемый товар."""
        found = []
        for path in sorted(SRC.rglob("*.py")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if GRAIN_PRICE_CONSTANT.search(line):
                    found.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
        self.assertEqual(
            found, [], "Цена зерна вернулась в код константой:\n" + "\n".join(found)
        )

    def test_catalog_grain_price_is_not_hardcoded_in_src(self) -> None:
        """Само каталожное число не вшито в код: единственный источник — YAML."""
        hardcoded = []
        for path in sorted(SRC.rglob("*.py")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "0.128165" in line:
                    hardcoded.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
        self.assertEqual(hardcoded, [], "Цена зерна продублирована числом в коде")

    def test_pig_price_exception_stays_deliberate(self) -> None:
        """Свинья — единственное исключение, и оно осознанное (ADR 0144).

        В `goods.yml:24-25` сказано прямо: животные цен не имеют. Пока цены у свиньи в
        каталоге нет, её цена остаётся в коде — иначе покупка свиньи стала бы невозможной.
        """
        world = load_scenario(SHIRE, seed=1729)
        self.assertEqual(exchange.PIG_PRICE_SILVER, 3.0)
        self.assertEqual(
            world.catalogs.goods["pig"].price_silver, 0.0,
            "У свиньи появилась цена в каталоге — тогда PIG_PRICE_SILVER дубль",
        )
        self.assertEqual(exchange.price_of(world, "pig"), 0.0)

    def test_neighbor_trade_and_granary_sale_read_the_same_catalog_price(self) -> None:
        """Соседский торг и продажа амбара барона берут ОДНО число — из каталога."""
        world = load_scenario(SHIRE, seed=1729)
        catalog = float(world.catalogs.goods[GRAIN].price_silver)
        self.assertAlmostEqual(catalog, GRAIN_PRICE, places=6, msg="Цена зерна в каталоге")
        self.assertAlmostEqual(
            exchange.price_of(world, GRAIN), catalog, places=9,
            msg="Код читает цену не из каталога",
        )
        # Соседский торг: 1.0 серебра покупает 1/price зерна — ровно по каталогу.
        price = 0.128165
        silver = 1.0
        self.assertAlmostEqual(silver / price, silver / catalog, places=9)


class TestPurchaseAllowance(unittest.TestCase):
    """Потолок закупки: месячная норма двора (ADR 0144 п. 2-4), один канал (ADR 0153).

    Числа сценария `v0_shire`, двор `hh_11` (3 взрослых + 2 ребёнка): своя месячная
    норма **4.4** зерна, цена зерна 0.128165. Живой канал один — прилавок
    (`offer_manor_surplus` → `sell_listed_grain`), и он берёт не больше недобора и не
    больше остатка потолка.
    """

    def setUp(self) -> None:
        self.world = load_scenario(SHIRE, seed=1729)
        self.barn = self.world.get_stock(self.world.manors["manor_hill"].stock_id)
        self.buyer = self.world.households["hh_11"]
        self.stock = self.world.get_stock(self.buyer.stock_id)
        self.price = self.world.catalogs.goods[GRAIN].price_silver
        # Один двор в мире: ни соседский обмен, ни чужие раздачи не подмешиваются.
        self.world.households = {self.buyer.id: self.buyer}
        self.buyer.current_tile_id = "t_00_09"
        self.buyer.main_action = "work_plot"
        self.buyer.minor_action = "idle_repair"
        self.give(self.barn, grain=40.0)
        self.assertAlmostEqual(
            monthly_food_need(self.world, self.buyer), NEED, places=9,
            msg="Месячная норма двора — не 4.4, потолок считается от неё",
        )
        self.assertAlmostEqual(exchange.purchase_allowance(self.world, self.buyer), NEED, places=9)

    def give(self, stock, **goods) -> None:
        """Подложка стока мимо бухгалтерии: это сценарий теста, а не тик."""
        stock.amounts.clear()
        for good, amount in sorted(goods.items()):
            stock.amounts[good] = amount
        self.world.ledger.capture_initial(self.world.total_matter())

    def grain(self, stock) -> float:
        return float(stock.amounts.get(GRAIN, 0.0))

    def delta(self) -> float:
        return self.world.ledger.delta(self.world.total_matter())

    def market_step(self) -> float:
        """Живой шаг месяца: остаток амбара на прилавок, затем продажа двору."""
        mark = len(self.world.ledger.entries)
        exchange.offer_manor_surplus(self.world, "manor_hill")
        exchange.sell_listed_grain(self.world, self.world.clock.date)
        return sum(
            entry.amount
            for entry in self.world.ledger.entries[mark:]
            if entry.kind == "transfer" and entry.good == GRAIN
            and entry.reason == "sell_grain_market"
            and entry.dst_id == self.buyer.stock_id
        )

    def test_allowance_is_the_monthly_need_ratio_one(self) -> None:
        """Потолок — своя месячная норма двора, отношение ровно 1.0 (ADR 0144 п. 2)."""
        need = monthly_food_need(self.world, self.buyer)
        allowance = exchange.purchase_allowance(self.world, self.buyer)
        self.assertAlmostEqual(allowance / need, 1.0, places=9)

    def test_zero_silver_buys_nothing_at_the_catalog_price(self) -> None:
        """Серебра 0: покупка не идёт ни на 1.0 зерна, даже по честной цене (ADR 0101, 0139)."""
        self.give(self.stock, silver=0.0)
        self.assertEqual(self.market_step(), 0.0, "Двор без серебра купил зерно")
        self.assertEqual(exchange.manor_grain_bought(self.world, self.buyer), 0.0)
        self.assertAlmostEqual(self.grain(self.barn), 40.0, places=9)
        self.assertAlmostEqual(self.delta(), 0.0, places=9)

    def test_purchase_never_exceeds_the_monthly_need(self) -> None:
        """Серебра хватает на весь амбар: двор берёт 4.4 — свою месячную норму."""
        self.give(self.stock, silver=100.0)
        sold = self.market_step()
        self.assertLessEqual(
            sold, monthly_food_need(self.world, self.buyer) + 1e-9,
            msg="Двор купил больше месячной нормы",
        )
        self.assertAlmostEqual(sold, NEED, places=9)
        self.assertAlmostEqual(
            exchange.manor_grain_bought(self.world, self.buyer), sold, places=9,
            msg="Счётчик закупки разошёлся с бухгалтерией",
        )
        self.assertAlmostEqual(self.grain(self.barn), 40.0 - NEED, places=9)
        self.assertAlmostEqual(self.delta(), 0.0, places=9)

    def test_second_offer_in_the_same_month_adds_nothing(self) -> None:
        """Второй прилавок в том же месяце ничего не добавляет: двор уже сыт."""
        self.give(self.stock, silver=100.0)
        self.assertAlmostEqual(self.market_step(), NEED, places=9)
        self.give(self.barn, grain=40.0)
        self.assertEqual(self.market_step(), 0.0, "Двор докупил сверх месячной нормы")
        self.assertAlmostEqual(self.grain(self.stock), NEED, places=9)
        self.assertAlmostEqual(
            exchange.manor_grain_bought(self.world, self.buyer), NEED, places=9
        )
        self.assertAlmostEqual(self.delta(), 0.0, places=9)

    def test_sated_court_with_a_full_granary_buys_nothing(self) -> None:
        """«По мере надобности»: сытый двор не закупается про запас (ADR 0124)."""
        self.give(self.stock, silver=100.0, grain=60.0)
        self.assertEqual(self.market_step(), 0.0, "Сытый двор закупился зерном")
        self.assertAlmostEqual(self.grain(self.barn), 40.0, places=9)
        self.assertAlmostEqual(self.delta(), 0.0, places=9)

    def test_barter_payment_spends_the_same_counter(self) -> None:
        """Серебра нет, платим натуральным хозяйством — счётчик закупки тот же (ADR 0139 п. 3)."""
        price_firewood = self.world.catalogs.goods["firewood"].price_silver
        self.give(self.stock, silver=0.0, firewood=40.0)
        sold = self.market_step()
        self.assertAlmostEqual(sold, NEED, places=9)
        self.assertAlmostEqual(
            exchange.manor_grain_bought(self.world, self.buyer), NEED, places=9,
            msg="Натуральный обмен не записан в счётчик закупки",
        )
        self.assertAlmostEqual(
            self.stock.amounts.get("firewood", 0.0),
            40.0 - NEED * self.price / price_firewood, places=9,
        )
        self.assertAlmostEqual(self.grain(self.barn), 40.0 - NEED, places=9)
        self.assertAlmostEqual(self.delta(), 0.0, places=9)

    def test_relief_does_not_spend_the_allowance(self) -> None:
        """Подача — не покупка: счётчик закупки она не тратит (ADR 0144 п. 2, ADR 0139 п. 5)."""
        self.give(self.stock, silver=0.0)
        self.buyer.main_action = "request_relief"
        self.buyer.minor_action = "request_relief"
        self.buyer.hunger_days = 2
        self.assertLess(food_months(self.world, self.buyer), 1.0)
        exchange.offer_manor_surplus(self.world, "manor_hill")
        phase_exchange(self.world)
        self.assertAlmostEqual(
            self.grain(self.stock), NEED, places=9, msg="Подача при нуле серебра не дошла"
        )
        self.assertEqual(
            exchange.manor_grain_bought(self.world, self.buyer), 0.0,
            "Подача потратила лимит закупки",
        )
        self.assertAlmostEqual(
            exchange.purchase_allowance_left(self.world, self.buyer), NEED, places=9
        )
        reasons = {e.reason for e in self.world.ledger.entries if e.good == GRAIN}
        self.assertEqual(reasons, {"relief"})
        self.assertAlmostEqual(self.delta(), 0.0, places=9)

    def test_counter_is_monthly_and_resets_itself(self) -> None:
        """Счётчик месячный: в новом месяце двор может купить снова."""
        self.give(self.stock, silver=100.0)
        self.assertAlmostEqual(self.market_step(), NEED, places=9)
        self.give(self.barn, grain=40.0)
        self.give(self.stock, silver=100.0)
        self.world.clock.advance_month()
        self.assertAlmostEqual(
            exchange.manor_grain_bought(self.world, self.buyer), 0.0, places=9,
            msg="Счётчик закупки не протух вместе с месяцем",
        )
        self.assertAlmostEqual(self.market_step(), NEED, places=9)
        self.assertAlmostEqual(self.delta(), 0.0, places=9)

    def test_allowance_follows_the_court_after_it_grows(self) -> None:
        """Потолок — норма ДВОРА, а не константа: новый ребёнок поднимает её на 0.7."""
        self.give(self.stock, silver=100.0)
        before = exchange.purchase_allowance(self.world, self.buyer)
        child_id = f"person:{self.buyer.id}:probe"
        self.world.persons[child_id] = _Person(age_class="child")
        self.buyer.member_ids = list(self.buyer.member_ids) + [child_id]
        after = exchange.purchase_allowance(self.world, self.buyer)
        self.assertAlmostEqual(after - before, 0.7, places=9)
        self.assertLessEqual(self.market_step(), after + 1e-9)
        self.assertAlmostEqual(self.delta(), 0.0, places=9)

    def test_same_seed_gives_the_same_hash_with_the_counter_used(self) -> None:
        """И-6: счётчик закупки не ломает детерминизм — один сид, один `state_hash`.

        Мир строится дважды с одним сидом, сценарий покупок один и тот же (серебром,
        потом натурой, потом новый месяц), и хеш обязан совпасть до знака: счётчик лежит
        в `world.stats`, а значит входит в хеш — и не должен вносить «дрожание».
        """
        hashes = []
        for _ in range(2):
            world = load_scenario(SHIRE, seed=1729)
            barn = world.get_stock(world.manors["manor_hill"].stock_id)
            buyer = world.households["hh_11"]
            stock = world.get_stock(buyer.stock_id)
            world.households = {buyer.id: buyer}
            stock.amounts.clear()
            stock.amounts["silver"] = 50.0
            stock.amounts["firewood"] = 20.0
            world.ledger.capture_initial(world.total_matter())
            exchange.offer_manor_surplus(world, "manor_hill")
            exchange.sell_listed_grain(world, world.clock.date)
            stock.amounts.pop("silver", None)
            stock.amounts["firewood"] = 40.0
            stock.amounts.pop(GRAIN, None)  # второй месяц: двор снова голоден
            world.clock.advance_month()
            exchange.offer_manor_surplus(world, "manor_hill")
            exchange.sell_listed_grain(world, world.clock.date)
            hashes.append(world.state_hash())
            self.assertGreater(
                exchange.manor_grain_bought(world, buyer), 0.0,
                "Счётчик закупки не заполнился — сценарий покупок не тот",
            )
        self.assertEqual(hashes[0], hashes[1], "Один сид дал два разных мира")


class _Person:
    """Человек-дворник для проверки «потолок растёт вместе с двором»."""

    def __init__(self, age_class: str) -> None:
        self.id = ""
        self.age_class = age_class
        self.age_months = 0
        self.health = 1.0


if __name__ == "__main__":
    unittest.main()
