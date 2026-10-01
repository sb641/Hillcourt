"""Обвинитель закона хозяина о серебре (ADR 0219).

> «Само серебро не чеканится в баронстве — только королевский двор».

Проверяется не «серебро есть», а три вещи, каждая из которых ломает закон по
своему:

1. **Чеканить нельзя.** Внутри баронства нет ни рецепта серебра, ни
   календарного роста серебра. Если появится — закон мёртв, даже если торг
   при этом работает.
2. **Видимый источник.** Каждая проводка серебра извне обязана нести `rule_id`
   существующего правила, у которого объявлен `params.source` — имя плательщика.
   Молчаливый `external_in` без источника запрещён.
3. **Пара, а не одна проводка.** Серебра не может прийти больше, чем зерна
   ушло наружу, помноженного на каталожную цену. Это одновременно запрет вечного
   двигателя И-1 и проверка того, что цена берётся из каталога, а не из числа
   в коде: равенство считается по `price_of`, и подставленная константа его
   ломает.

Всё это проверяется на ДЛИННОМ прогоне, где серебра действительно набегает:
тест, который зеленеет на нулевой продаже, не проверяет ничего.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from hillcourt.engine.manor import manor_of_household, manor_stock
from hillcourt.engine.tick import run_month
from hillcourt.economy import exchange
from hillcourt.economy.needs import monthly_food_need
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_barony_100.yml"
START_STAND = ROOT / "design" / "scenarios" / "start_stand.yml"

SILVER = "silver"
RULE_ID = "royal_court_buys_grain"
# Горизонт замера. Серебро начинает приходить с 6-го месяца (амбар впервые
# переваливает через буфер еды), поэтому 12 месяцев достаточно, чтобы увидеть
# и приход, и несколько месяцев подряд с продажей. 24 месяца — цифры отчёта.
MONTHS = 12
# Короткий горизонт для детерминизма: серебро уже идёт, а один месяц на этой
# карте стоит ~4 секунды. Два прогона по 8 месяцев проверяют то же, что и один
# на 24, но вчетверо дешевле.
DETERMINISM_MONTHS = 8
DETERMINISM_SEED = 4242

_CACHED: dict[int, object] = {}


def _run(months: int, seed: int | None = None, scenario: Path = SCENARIO):
    """Прогон на `months` месяцев. Мир на (сценарий, months, seed) кэшируется.

    Один тяжёлый прогон на весь модуль: восемь месяцев этой карты стоят минуту,
    и десяток таких прогонов — десяток минут ради проверок, которые смотрят на
    один и тот же мир. Ключ кэша включает seed и сценарий, поэтому прогоны с
    разными seed и разными картами не смешиваются.
    """
    key = (str(scenario), months, seed)
    if key not in _CACHED:
        world = load_scenario(scenario, seed=seed)
        for _ in range(months):
            run_month(world)
        _CACHED[key] = world
    return _CACHED[key]


def _silver_entries(world) -> list:
    return [e for e in world.ledger.entries if e.good == SILVER]


def _run_scripted(scenario: Path, months: int, seed: int | None = None):
    """Прогон сценария С `script:` — так же, как это делает `runner.run`.

    `run_month` сама по себе записи `script:` не исполняет: приход двора, ради
    которого проверяется буфер обзаведения, создаётся именно приказом игрока
    из сценария. Без этого тест проверял бы месяц, в котором прихода ещё не
    было, и был бы зелёным всегда.
    """
    from hillcourt.runner import _apply_script_entry

    world = load_scenario(scenario, seed=seed)
    script = list(world.script)
    for month_index in range(1, months + 1):
        for entry in script:
            if int(entry.get("at_month", 0)) != month_index:
                continue
            _apply_script_entry(world, entry)
        run_month(world)
    return world


class TestSilverIsNotMintedInTheBarony(unittest.TestCase):
    """Закон: внутри баронства серебро не производится (ADR 0219 §1)."""

    def test_no_recipe_produces_silver(self) -> None:
        """Ни один рецепт `recipes.yml` не выпускает серебро.

        Рецепт серебра — это и есть «чеканка в баронстве»: из ничего, без
        внешней стороны. Проверяются и `outputs`, и `loss` (потери рецепта —
        тоже выпуск вещества), потому что обойти проверку через `loss` было бы
        тривиально.
        """
        world = load_scenario(SCENARIO)
        offenders = sorted(
            recipe_id
            for recipe_id, recipe in world.catalogs.recipes.items()
            if recipe.outputs.get(SILVER, 0.0) > 0.0
            or recipe.loss.get(SILVER, 0.0) > 0.0
        )
        self.assertEqual(
            offenders, [], f"Серебро чеканится рецептом: {offenders}"
        )

    def test_no_calendric_growth_rule_produces_silver(self) -> None:
        """Календарного роста серебра нет: фаза роста не исполняет `conditional`.

        `spawn_rules.yml::royal_court_buys_grain` объявлен с `kind: conditional`,
        и это не украшение: `engine/growth.py::run_detailed_growth` берёт только
        `kind == "calendric"` с `target == "good"`. Если бы правило прихода
        серебра оказалось `calendric`, фазе роста оно стало бы выдавать серебро
        на каждой клетке каждый месяц — то есть ровно то, что запрещает закон.
        """
        world = load_scenario(SCENARIO)
        offenders = sorted(
            rule_id
            for rule_id, rule in world.catalogs.spawn_rules.items()
            if rule.kind == "calendric"
            and rule.target == "good"
            and str(rule.params.get("good", "")) == SILVER
        )
        self.assertEqual(offenders, [], f"Серебро растёт в баронстве: {offenders}")

    def test_the_rule_for_silver_is_conditional_not_calendric(self) -> None:
        """Правило прихода серебра — `conditional`, а не `calendric` и не `probabilistic`.

        Смысл: серебро появляется не само по себе и не по броску, а по
        состоянию — когда у амбара есть излишек сверх буфера еды.
        """
        world = load_scenario(SCENARIO)
        rule = world.catalogs.spawn_rules.get(RULE_ID)
        self.assertIsNotNone(rule, f"Нет правила {RULE_ID} — торговать нечем")
        self.assertEqual(rule.kind, "conditional", rule.kind)
        self.assertEqual(rule.target, "good", rule.target)
        self.assertEqual(rule.params.get("good"), SILVER, rule.params)


class TestSilverHasVisibleSource(unittest.TestCase):
    """Закон: у каждого прихода серебра есть видимый источник (ADR 0219 §2)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = _run(MONTHS)

    def test_silver_actually_arrives(self) -> None:
        """Замер «до/после»: серебра в мире было 0.0, стало больше нуля.

        Без этой строки остальные проверки были бы согласны с пустым миром, где
        продажи не происходит вовсе, — то есть доказывали бы ноль.
        """
        total = sum(
            stock.amounts.get(SILVER, 0.0) for stock in self.world.stocks.values()
        )
        self.assertGreater(
            total, 1.0, "Серебра в мире нет: торг наружу не работает вовсе"
        )
        self.assertTrue(_silver_entries(self.world), "Нет ни одной проводки серебра")

    def test_every_silver_credit_has_a_rule_id(self) -> None:
        """Каждая проводка серебра несёт `rule_id` — без него И-1 не доказана."""
        credits = [e for e in _silver_entries(self.world) if e.kind == "external_in"]
        self.assertTrue(credits, "Серебро не пришло извне ни разу")
        for entry in credits:
            with self.subTest(date=str(entry.date), amount=entry.amount):
                self.assertTrue(
                    entry.rule_id,
                    f"Внешний приход серебра без rule_id ({entry.amount}) — "
                    "материя взята из ниоткуда",
                )
                self.assertIn(
                    entry.rule_id,
                    self.world.catalogs.spawn_rules,
                    f"rule_id '{entry.rule_id}' не объявлен в spawn_rules.yml",
                )

    def test_every_silver_rule_names_the_paying_party(self) -> None:
        """У правила прихода серебра объявлен `params.source` — кто платит.

        `external_in` сам по себе говорит только «откуда-то снаружи». Источник
        должен быть назван, иначе закон «только королевский двор» нечем
        проверить, а завтра под тем же `rule_id` придёт любой другой.
        """
        for entry in _silver_entries(self.world):
            if entry.kind != "external_in" or not entry.rule_id:
                continue
            rule = self.world.catalogs.spawn_rules[entry.rule_id]
            source = str(rule.params.get("source", "") or "")
            with self.subTest(rule=rule.id):
                self.assertTrue(
                    source,
                    f"Правило '{rule.id}' не называет источник (params.source)",
                )
                self.assertEqual(
                    source,
                    "royal_court",
                    f"Серебро пришло не от королевского двора, а от '{source}'",
                )

    def test_silver_credit_is_exactly_the_rule_reason(self) -> None:
        """Серебро приходит только с проводкой продажи, а не «просто так»."""
        reasons = {
            e.reason for e in _silver_entries(self.world) if e.kind == "external_in"
        }
        self.assertEqual(
            reasons, {exchange.SALE_OUT_REASON}, f"Неожиданные приходы: {reasons}"
        )


class TestSilverIsPaidForByMatterLeaving(unittest.TestCase):
    """Закон И-1: серебро — не подарок, а плата за ушедшее зерно (ADR 0219 §3)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = _run(MONTHS)

    def test_silver_equals_grain_left_times_catalog_price(self) -> None:
        """Серебра входит ровно столько, сколько стоило ушедшее наружу зерно.

        Равенство, а не «меньше или равно»: оно держит сразу два закона.
        * **И-1.** Серебра не может быть больше оплаченного — вечный двигатель
          невозможен, даже если код сломан так, что `external_out` потерян.
        * **Цена в каталоге.** Правая часть считается через `price_of`
          (`goods.yml::grain.price_silver`). Подставленная в код константа
          нарушит равенство сразу, как только каталог с ценой отличается.
        """
        world = self.world
        rule = world.catalogs.spawn_rules[RULE_ID]
        bought_good = str(rule.params["bought_good"])
        price = exchange.price_of(world, bought_good)
        self.assertGreater(price, 0.0, "У зерна нет цены в каталоге")

        silver_in = sum(
            e.amount
            for e in _silver_entries(world)
            if e.kind == "external_in" and e.good == SILVER
        )
        bought_out = sum(
            e.amount
            for e in world.ledger.entries
            if e.kind == "external_out" and e.good == bought_good
        )
        self.assertGreater(
            bought_out, 0.0, "Ни одного зерна не ушло наружу: серебру не за что платить"
        )
        self.assertAlmostEqual(
            silver_in,
            bought_out * price,
            places=6,
            msg=(
                f"серебра вошло {silver_in:.6f}, а зерна ушло на "
                f"{bought_out:.6f} * {price:.6f} = {bought_out * price:.6f}"
            ),
        )

    def test_matter_delta_stays_zero_every_month(self) -> None:
        """И-1 помесячно: приход извне и уход наружу уравновешены."""
        world = load_scenario(SCENARIO)
        for month in range(MONTHS):
            run_month(world)
            with self.subTest(month=month + 1):
                self.assertAlmostEqual(
                    world.ledger.delta(world.total_matter()), 0.0, places=6
                )

    def test_silver_follows_the_catalog_price(self) -> None:
        """Цена серебра — число каталога: смена цены меняет приход, а не код.

        Проба на мутацию: если бы цена зерна была зашита в коде, смена
        `price_silver` в каталоге не сдвинула бы сумму — и равенство из
        `test_silver_equals_grain_left_times_catalog_price` осталось бы зелёным
        только потому, что код и сравнение читали одно и то же зашитое число.

        Мир достраивается ДО смены цены, а тик идёт после: иначе цена менялась
        бы на неиспользуемом каталоге, и проверка была бы круговой.
        """
        world = _run(DETERMINISM_MONTHS)
        good = world.catalogs.goods["grain"]
        rule = world.catalogs.spawn_rules[RULE_ID]
        bought_good = str(rule.params["bought_good"])
        old_price = float(good.price_silver)
        self.assertGreater(old_price, 0.0)
        cut = len(world.ledger.entries)
        good.price_silver = 5.0
        try:
            for _ in range(2):
                run_month(world)
            fresh = world.ledger.entries[cut:]
            silver_in = sum(
                e.amount for e in fresh
                if e.kind == "external_in" and e.good == SILVER
            )
            bought_out = sum(
                e.amount for e in fresh
                if e.kind == "external_out" and e.good == bought_good
            )
            self.assertGreater(silver_in, 0.0, "При новой цене продажа молчит")
            self.assertAlmostEqual(
                silver_in, bought_out * exchange.price_of(world, bought_good), places=6
            )
            self.assertGreater(
                silver_in, bought_out * old_price,
                "Цена в коде зашита: смена каталога не сдвинула приход",
            )
        finally:
            good.price_silver = old_price


class TestSilverWithoutRuleIsLoud(unittest.TestCase):
    """Правила нет — это ошибка, а не тихий ноль (ADR 0219 §4)."""

    def test_missing_rule_raises_instead_of_selling_nothing(self) -> None:
        """Без правила закупки продажа наружу падает, а не возвращает 0.0.

        Обратная сторона закона: если бы код молча возвращал ноль при отсутствии
        правила, то удаление строки из `spawn_rules.yml` выглядело бы как «торг
        закрылся», а не как «закон сломан». Именно такой молчащий ноль хозяин и
        запретил в задании.
        """
        world = load_scenario(SCENARIO)
        del world.catalogs.spawn_rules[RULE_ID]
        with self.assertRaises(ValueError) as caught:
            exchange.sell_grain_to_royal_court(world, world.clock.date)
        self.assertIn(RULE_ID, str(caught.exception))


class TestSilverDoesNotEatTheVillage(unittest.TestCase):
    """Барон не продаёт хлеб, на котором держится его деревня (ADR 0148 п. 2)."""

    def test_sale_leaves_the_declared_food_buffer(self) -> None:
        """Постусловие продажи: в амбаре остаётся `keep_months_food` месяцев нужды.

        Проверяется в том месяце, где продажа состоялась, и **сразу после** неё:
        буфер — это обязательство самой продажи, а не амбара в конце месяца.
        Внутри `phase_exchange` до неё работает ещё и внутренний прилавок
        (`sell_listed_grain`, ADR 0148), и он тоже ест амбар — приписывать его
        расход закону о продаже наружу было бы подменой вины.
        """
        world = load_scenario(SCENARIO)
        rule = world.catalogs.spawn_rules[RULE_ID]
        keep_months = float(rule.params["keep_months_food"])
        self.assertGreater(keep_months, 0.0, "Буфер еды не объявлен")
        manor_id = sorted(world.manors)[0]
        manor = world.manors[manor_id]
        barn = manor_stock(world, manor)
        self.assertIsNotNone(barn)
        # Доводим амбар до состояния, где продажа обязана состояться. Зерно
        # ПЕРЕНОСИТСЯ из стоков дворов, а не создаётся: тест об И-1 не должен
        # сам стать источником материи. Нужда при этом не меняется — она
        # считается по составу двора, а не по его запасу.
        for hid in sorted(world.households):
            stock = world.get_stock(world.households[hid].stock_id)
            spare = stock.amounts.get("grain", 0.0)
            if spare > 1e-9:
                world.ledger.transfer(
                    stock, barn, "grain", spare, "test_fill", world.clock.date
                )
        need = sum(
            monthly_food_need(world, world.households[hid])
            for hid in sorted(world.households)
            if world.households[hid].left_at is None
            and manor_of_household(world, hid) is manor
        )
        self.assertGreater(
            barn.amounts.get("grain", 0.0), keep_months * need,
            "Амбар не доведён до состояния, где продажа обязана состояться",
        )
        before_grain = barn.amounts["grain"]
        cut = len(world.ledger.entries)
        bought = exchange.sell_grain_to_royal_court(world, world.clock.date)
        self.assertGreater(bought, 0.0, "Продажа не состоялась: тест проверял пустоту")
        self.assertGreaterEqual(
            barn.amounts.get("grain", 0.0) + 1e-6,
            keep_months * need - 1e-6,
            "Продажа съела хлеб, которым кормится деревня",
        )
        # И сверх того: наружу ушёл ровно излишек, а не больше и не меньше.
        sold = sum(
            e.amount
            for e in world.ledger.entries[cut:]
            if e.kind == "external_out" and e.good == "grain"
        )
        self.assertAlmostEqual(
            sold, before_grain - keep_months * need, places=6,
            msg="Наружу ушло не ровно излишек сверх буфера",
        )

    def test_shipment_reserve_protects_arriving_tenants(self) -> None:
        """Амбар не съедает обзаведение двора, которому ещё предстоит прибыть.

        Амбар барона — не только продовольствие: из него
        `engine/manor.py::arrive_household` везёт новому tenant'у
        `starting_stocks`. Продажа, съевшая этот груз, не принесла бы серебра —
        она сломала бы приход: бухгалтерия откажет по `arrival_cargo`, и
        следующий приказ скрипта упадёт с `KeyError`.

        Проверка на живом сценарии `start_stand`, где такая волна есть: 7-го
        месяца двор `hh_wave_01` должен получить 180.0 зерна.
        """
        # Обещание читается из сценария СТАТИЧЕСКИ, до прогона: после прихода
        # двор уже в `world.households`, и список «не прибывших» был бы пуст —
        # проверка на пустоте зеленеет всегда.
        promised = 0.0
        for entry in yaml.safe_load(
            START_STAND.read_text(encoding="utf-8")
        ).get("script", []) or []:
            if str(entry.get("action", "")) != "grant_tenement":
                continue
            stocks = entry.get("starting_stocks")
            if isinstance(stocks, dict):
                promised += float(stocks.get("grain", 0.0) or 0.0)
        self.assertGreaterEqual(
            promised, 180.0, "Сценарий не обещает столько зерна новому двору"
        )
        world = _run_scripted(START_STAND, 8)
        barn_id = "settlement:" + world.player.court_settlement_id
        self.assertIn(
            "hh_wave_01", world.households,
            "Двор не прибыл: продажа наружу съела его обзаведение",
        )
        # Проверяется ПРОВОДКА обзаведения, а не остаток стока: двор, придя,
        # сразу ест, и к 8-му месяцу в стоке 176.5 из 180. Закон о буфере — про
        # то, что груз был выдан, а не про то, что он лежит нетронутым.
        cargo = sum(
            e.amount
            for e in world.ledger.entries
            if e.kind == "transfer"
            and e.good == "grain"
            and e.reason == "arrival_cargo"
            and e.dst_id == "household:hh_wave_01"
        )
        self.assertGreaterEqual(
            cargo, 180.0 - 1e-6,
            f"Двору волны выдано {cargo:.2f} зерна вместо 180: продажа наружу "
            "съела обзаведение",
        )
        self.assertGreater(
            world.get_stock(barn_id).amounts.get("grain", 0.0) + 1e-6,
            -1e-6,
            "Амбар ушёл в минус",
        )

    def test_buffer_holds_in_every_month_it_trades(self) -> None:
        """В живом тике буфер держится каждый месяц, когда продажа состоялась.

        Продажа обёрнута, чтобы замерить остаток амбара **сразу после** неё, а
        не в конце месяца: дальше по тику амбар ест ещё и внутренний прилавок.
        """
        world = load_scenario(SCENARIO)
        rule = world.catalogs.spawn_rules[RULE_ID]
        keep_months = float(rule.params["keep_months_food"])
        original = exchange.sell_grain_to_royal_court
        seen: list[tuple[int, float, float, float]] = []

        def spy(w, date):
            got = original(w, date)
            if got > 0.0:
                for manor_id in sorted(w.manors):
                    barn = manor_stock(w, w.manors[manor_id])
                    need = sum(
                        monthly_food_need(w, w.households[hid])
                        for hid in sorted(w.households)
                        if w.households[hid].left_at is None
                        and manor_of_household(w, hid) is w.manors[manor_id]
                    )
                    if barn is not None:
                        seen.append(
                            (w.clock.year * 12 + w.clock.month,
                             barn.amounts.get("grain", 0.0), need, got)
                        )
            return got

        exchange.sell_grain_to_royal_court = spy
        try:
            for _ in range(MONTHS):
                run_month(world)
        finally:
            exchange.sell_grain_to_royal_court = original
        self.assertTrue(seen, "Продажа наружу не состоялась ни разу за 24 месяца")
        for stamp, left, need, got in seen:
            with self.subTest(month=stamp):
                self.assertGreaterEqual(
                    left + 1e-6, keep_months * need - 1e-6,
                    f"месяц {stamp}: после продажи на {got:.2f} серебра "
                    f"в амбаре {left:.2f} при нужде {need:.2f}",
                )


class TestDeterminismWithSilver(unittest.TestCase):
    """И-6: один seed дважды — один `state_hash` (серебро в хеш входит)."""

    def test_same_seed_same_state_hash(self) -> None:
        first = _run(DETERMINISM_MONTHS, seed=DETERMINISM_SEED)
        second = _run(DETERMINISM_MONTHS, seed=DETERMINISM_SEED)
        self.assertGreater(
            sum(s.amounts.get(SILVER, 0.0) for s in first.stocks.values()),
            0.0,
            "Серебра нет: детерминизм проверялся бы на пустом канале",
        )
        self.assertEqual(first.state_hash(), second.state_hash())


if __name__ == "__main__":
    unittest.main()
