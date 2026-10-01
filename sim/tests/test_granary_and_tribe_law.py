"""Обвинители двух законов: амбар барона продаётся (0139) и племя независимо (0140).

**ADR 0139 — амбар барона товар.** Канал теперь **один** (ADR 0153): месячный
шаг `manor._offer_surplus` → `offer_manor_surplus` выставляет остаток амбара на
прилавок, а `phase_exchange` → `sell_listed_grain` продаёт его дворам **по мере
надобности** — не больше собственного недобора и не больше месячной нормы
закупки (ADR 0144). Второго канала в коде нет, и он не изобретается: прилавок
показывает, что амбар предлагает **сейчас**, непроданное остаётся в амбаре и
предложится снова. Цена — только из каталога, параметра цены не существует.
Подача голодному и продажа — разные вещи; натуральный обмен законен при нуле
серебра; дельта материи 0.

**ADR 0140 — племя независимо.** Ни оброка, ни десятины, ни подачи, ни барщины
до солидарности; оброк при `allied` отменён (регрессия юриста); торговля с племенем
**по умолчанию** в обе стороны и закрыта только при военных отношениях.

Проверка: `PYTHONPATH=sim/src python3 -m unittest sim.tests.test_granary_and_tribe_law -v`
"""

from __future__ import annotations

import contextlib
import inspect
import unittest
from pathlib import Path

from hillcourt.economy import exchange, tribe as tribe_economy
from hillcourt.economy.exchange import (
    _offer_key,
    apply_relief,
    manor_grain_bought,
    offer_manor_surplus,
    price_of,
    purchase_allowance,
    purchase_allowance_left,
    relief_sources,
    sell_listed_grain,
)
from hillcourt.economy.tribe import (
    MUSTER_STANCE,
    TRIBUTE_STANCE,
    tribe_households,
    tribe_trade_open,
    tribe_tribute_month,
)
from hillcourt.economy.needs import food_shortfall
from hillcourt.engine.hexgrid import neighbor_ids
from hillcourt.engine.manor import manor_of_household, manor_stock, root_manor
from hillcourt.engine.tick import phase_exchange, phase_obligations, run_month
from hillcourt.ontology import Hazard, Manor
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
HILL_SALT = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
NATIVE = ROOT / "design" / "scenarios" / "v0_native_village.yml"
SEED = 1729
EPS = 1e-9
GRAIN = "grain"
SILVER = "silver"
LOG = "log"
YARD = "hh_02"
COURT = "hh_court"
TRIBE = "tribe_village"
TRIBE_YARDS = ("hh_tribe_01", "hh_tribe_02", "hh_tribe_03")
TRADE_REASONS = ("buy_pig", "buy_grain", "buy_log", "buy_beast")
# Пометка единственного канала продажи зерна из амбара (ADR 0153).
MARKET_REASON = "sell_grain_market"


def _delta(world) -> float:
    return world.ledger.delta(world.total_matter())


def _seal(world):
    """Запомнить материю ПОСЛЕ расстановки стоков: прямая правка `amounts` —
    не проводка, поэтому баланс берётся только после стартовых запасов."""
    world.ledger.capture_initial(world.total_matter())
    return world


@contextlib.contextmanager
def catalogue_price(world, good: str, value: float):
    """Временно поставить каталожную цену и вернуть её как было.

    Каталоги кэшируются на процесс, поэтому правка без возврата утёкла бы в
    следующие тесты.
    """
    rule = world.catalogs.goods[good]
    old = float(rule.price_silver)
    rule.price_silver = float(value)
    try:
        yield old
    finally:
        rule.price_silver = old


def _granary_world(grain: float = 100.0, silver: float = 0.0, yard_grain: float = 40.0):
    """Мир с набитым амбаром барона и одним двором в его книге."""
    world = load_scenario(HILL_SALT, seed=SEED)
    barn = manor_stock(world, root_manor(world))
    barn.amounts[GRAIN] = grain
    yard = world.get_stock(f"household:{YARD}")
    yard.amounts[SILVER] = silver
    yard.amounts[GRAIN] = yard_grain
    return _seal(world), barn, yard


def _yard_shortfall(world, household_id: str) -> float:
    """Недобор двора в единицах зерна: столько прилавок ему и должен продать."""
    good = world.catalogs.goods.get(GRAIN)
    nutrition = float(good.nutrition) if good is not None and good.nutrition > 0 else 1.0
    return food_shortfall(world, world.households[household_id]) / nutrition


def _moved(world, good: str, reason: str, dst_prefix: str = "") -> float:
    return sum(
        e.amount for e in world.ledger.entries
        if e.good == good
        and e.reason == reason
        and (e.dst_id or "").startswith(dst_prefix)
    )


class TestBaronSellsThroughTheCounter(unittest.TestCase):
    """Канал один (ADR 0153): прилавок барона продаёт зерно нуждающимся дворам.

    Прежние тесты этого класса держали прямую продажу с ценой, которую назначает
    барон. Смысл — «амбар барона товар, цена из каталога, сделка ровно на своё
    зерно» — проверяется через прилавок: тот же амбар, та же каталожная цена, та
    же проводка зерна двору и серебра в амбар.
    """

    def test_barn_minus_exactly_sold_yard_plus_exactly_the_same(self) -> None:
        """Двор покупает своё недоборное зерно и платит каталожной ценой."""
        world, barn, yard = _granary_world(grain=100.0, silver=5.0, yard_grain=0.0)
        price = price_of(world, GRAIN)
        need = _yard_shortfall(world, YARD)
        offered = offer_manor_surplus(world, root_manor(world).id)
        self.assertAlmostEqual(offered, 100.0, places=6, msg="Прилавок не выставлен")
        self.assertAlmostEqual(
            barn.amounts[GRAIN], 100.0, places=6,
            msg="Выставленное зерно ушло из амбара до продажи",
        )

        sell_listed_grain(world, world.clock.date)

        bought = _moved(world, GRAIN, MARKET_REASON, "household:")
        self.assertGreater(bought, 0.0, "Двор не купил зерно с прилавка")
        self.assertAlmostEqual(
            bought, need, places=6,
            msg="Продано не по мере надобности: столько не недобор",
        )
        self.assertAlmostEqual(barn.amounts[GRAIN], 100.0 - bought, places=6)
        self.assertAlmostEqual(yard.amounts[GRAIN], bought, places=6, msg="Двор не получил")
        self.assertAlmostEqual(
            5.0 - yard.amounts[SILVER], bought * price, places=9,
            msg="Плата не по каталожной цене",
        )
        self.assertAlmostEqual(_delta(world), 0.0, places=6)

    def test_no_money_no_goods_no_trade_barn_untouched(self) -> None:
        """Ни серебра, ни товара — сделки нет, амбар не тронут (ADR 0139 п. 3)."""
        world, barn, yard = _granary_world(grain=100.0, silver=0.0, yard_grain=0.0)
        for good in sorted(yard.amounts):
            yard.amounts[good] = 0.0
        world = _seal(world)
        offer_manor_surplus(world, root_manor(world).id)
        sell_listed_grain(world, world.clock.date)
        self.assertEqual(
            _moved(world, GRAIN, MARKET_REASON, "household:"), 0.0,
            "Продажа без платежа состоялась",
        )
        self.assertAlmostEqual(barn.amounts[GRAIN], 100.0, places=6, msg="Амбар тронут")
        self.assertAlmostEqual(_delta(world), 0.0, places=6)

    def test_counter_never_sells_more_than_offered(self) -> None:
        """Прилавок не продаёт больше остатка и держит остаток на месте."""
        world, barn, yard = _granary_world(grain=2.0, silver=5.0, yard_grain=0.0)
        offered = offer_manor_surplus(world, root_manor(world).id)
        self.assertAlmostEqual(offered, 2.0, places=6)
        world = _seal(world)

        sell_listed_grain(world, world.clock.date)

        sold = _moved(world, GRAIN, MARKET_REASON, "household:")
        self.assertGreater(sold, 0.0, "Прилавок не купили вовсе")
        self.assertLessEqual(sold, offered + EPS, "Продано больше, чем выставлено")
        self.assertAlmostEqual(barn.amounts[GRAIN], 2.0 - sold, places=6)
        self.assertAlmostEqual(
            float(world.stats.get(_offer_key(root_manor(world).id), 0.0)),
            offered - sold, places=6,
            msg="Счётчик прилавка не уменьшился на проданное",
        )
        self.assertAlmostEqual(_delta(world), 0.0, places=6)

    def test_purchase_cap_stops_the_second_sale_in_the_same_month(self) -> None:
        """ADR 0144 п. 2-3: потолок месячный и общий на канал — прилавок его не обходит.

        Двор покупает свой недобор, снова оказывается голоден в том же месяце и
        приходит к прилавку второй раз: второй раз ему не дают ничего, потому что
        месячная норма закупки уже выбрана.
        """
        world, barn, yard = _granary_world(grain=100.0, silver=9.0, yard_grain=0.0)
        need = _yard_shortfall(world, YARD)
        cap = purchase_allowance(world, world.households[YARD])
        self.assertAlmostEqual(need, cap, places=6, msg="Стенд: недобор равен потолку")

        offer_manor_surplus(world, root_manor(world).id)
        sell_listed_grain(world, world.clock.date)
        first = _moved(world, GRAIN, MARKET_REASON, f"household:{YARD}")
        self.assertAlmostEqual(first, need, places=6)

        yard.amounts[GRAIN] = 0.0
        world = _seal(world)
        self.assertAlmostEqual(
            _yard_shortfall(world, YARD), need, places=6, msg="Стенд: двор снова голоден"
        )
        offer_manor_surplus(world, root_manor(world).id)
        sell_listed_grain(world, world.clock.date)

        second = _moved(world, GRAIN, MARKET_REASON, f"household:{YARD}") - first
        self.assertAlmostEqual(second, 0.0, places=6, msg="Месячный потолок закупки обойдён")
        self.assertAlmostEqual(
            manor_grain_bought(world, world.households[YARD]), cap, places=6,
            msg="Счётчик закупки больше месячной нормы",
        )
        self.assertAlmostEqual(barn.amounts[GRAIN], 100.0 - first, places=6)
        self.assertAlmostEqual(_delta(world), 0.0, places=6)

    def test_counter_does_not_sell_to_another_manors_book(self) -> None:
        """Прилавок барона не обслуживает чужую книгу.

        Двор записан в книгу другого манора: у него своя недобор и своё серебро, но
        зерно амбара этого барона ему не продают — книга одна и она решает всё.
        """
        world, barn, yard = _granary_world(grain=100.0, silver=9.0, yard_grain=0.0)
        root = root_manor(world)
        other = Manor(id="manor_other", holder_person_id=root.holder_person_id)
        other.household_ids = [YARD]
        world.manors[other.id] = other
        world.households[YARD].manor_id = other.id
        if YARD in root.household_ids:
            root.household_ids.remove(YARD)
        world = _seal(world)
        self.assertIs(manor_of_household(world, YARD), other, "Стенд: двор в чужой книге")

        offer_manor_surplus(world, root.id)
        sell_listed_grain(world, world.clock.date)

        sold = _moved(world, GRAIN, MARKET_REASON, f"household:{YARD}")
        self.assertGreater(_yard_shortfall(world, YARD), 0.0, "Стенд: двор голоден")
        self.assertEqual(sold, 0.0, "Прилавок продал двору чужого манора")
        self.assertAlmostEqual(barn.amounts[GRAIN], 100.0, places=6)
        self.assertAlmostEqual(_delta(world), 0.0, places=6)

    def test_offering_does_not_accumulate_months(self) -> None:
        """Прилавок показывает остаток СЕЙЧАС, а не копит (ADR 0148 п. 3).

        Непроданное зерно остаётся в амбаре и предлагается снова: выставка не
        переезжает из месяца в месяц, потому что она и есть остаток.
        """
        world, barn, _yard = _granary_world(grain=100.0, silver=0.0, yard_grain=0.0)
        for _ in range(3):
            offer_manor_surplus(world, root_manor(world).id)
            sell_listed_grain(world, world.clock.date)
        sold = _moved(world, GRAIN, MARKET_REASON, "household:")
        self.assertAlmostEqual(
            float(world.stats.get(_offer_key(root_manor(world).id), 0.0)),
            100.0 - sold, places=6,
            msg="Прилавок накопил выставку месяцами вместо остатка амбара",
        )
        self.assertAlmostEqual(
            barn.amounts[GRAIN], 100.0 - sold, places=6, msg="Амбар потерял зерно",
        )
        self.assertAlmostEqual(_delta(world), 0.0, places=6)


class TestReliefIsNotPurchase(unittest.TestCase):
    """Подача голодному и продажа — разные вещи (ADR 0139 п. 5, ADR 0144)."""

    def test_relief_goes_at_zero_silver(self) -> None:
        """Серебра нет вообще — подача всё равно идёт, и это не покупка."""
        world, barn, yard = _granary_world(grain=100.0, yard_grain=0.0)
        for stock in world.stocks.values():
            stock.amounts[SILVER] = 0.0
        world.households[YARD].main_action = "request_relief"
        world.households[YARD].minor_action = "request_relief"
        world = _seal(world)
        apply_relief(world, world.clock.date)
        given = _moved(world, GRAIN, "relief", "household:")
        self.assertGreater(given, 0.0, "Подача при нуле серебра не пошла")
        self.assertAlmostEqual(yard.amounts[GRAIN], given, places=6)
        self.assertEqual(
            [e for e in world.ledger.entries if e.reason == MARKET_REASON],
            [],
            "Подача выглядит как продажа: прилавок тронут при нуле серебра",
        )
        self.assertAlmostEqual(_delta(world), 0.0, places=6)

    def test_silver_buys_instead_of_relief(self) -> None:
        """Двор с серебром покупает сам: подачи нет, сделка есть.

        Подача идёт только тем, кто попросил (`main_action = request_relief`).
        Здесь двор не просит, а покупает — и единственная проводка зерна в его сток
        это продажа с прилавка.
        """
        world, barn, yard = _granary_world(grain=100.0, silver=9.0, yard_grain=0.0)
        world.households[YARD].main_action = "work_plot"
        world = _seal(world)
        offer_manor_surplus(world, root_manor(world).id)
        sell_listed_grain(world, world.clock.date)
        bought = _moved(world, GRAIN, MARKET_REASON, "household:")
        self.assertGreater(bought, 0.0, "Двор с серебром не купил зерно у барона")
        self.assertEqual(
            _moved(world, GRAIN, "relief", "household:"), 0.0,
            "Двор, который не просил, получил подачу",
        )
        self.assertEqual(
            {e.reason for e in world.ledger.entries if e.good == GRAIN},
            {MARKET_REASON},
            "Зерно пришло не только с прилавка",
        )
        self.assertAlmostEqual(
            9.0 - yard.amounts[SILVER], bought * price_of(world, GRAIN), places=9,
        )
        self.assertAlmostEqual(_delta(world), 0.0, places=6)

    def test_relief_does_not_spend_the_purchase_allowance(self) -> None:
        """ADR 0144 п. 2: подача не тратит месячный лимит закупки."""
        world, barn, yard = _granary_world(grain=100.0, silver=9.0, yard_grain=0.0)
        world.households[YARD].main_action = "request_relief"
        world = _seal(world)
        apply_relief(world, world.clock.date)
        self.assertGreater(
            _moved(world, GRAIN, "relief", "household:"), 0.0, "Подача не пошла"
        )
        self.assertAlmostEqual(
            manor_grain_bought(world, world.households[YARD]), 0.0, places=9,
            msg="Подача потратила лимит закупки (ADR 0144 п. 2)",
        )
        self.assertAlmostEqual(
            purchase_allowance_left(world, world.households[YARD]),
            purchase_allowance(world, world.households[YARD]),
            places=9,
            msg="Лимит закупки уменьшился после подачи",
        )
        self.assertAlmostEqual(_delta(world), 0.0, places=6)

    def test_relief_and_sale_are_two_separate_moves(self) -> None:
        """Два стенда: попросивший получает подачу, покупающий — сделку.

        Проверяется не «обе двери работают», а раздельность: в стенде подачи нет
        проводки продажи, в стенде продажи нет проводки подачи, и обе двигают
        материю честно (дельта 0).
        """
        asking, asking_barn, _ = _granary_world(grain=100.0, silver=9.0, yard_grain=0.0)
        asking.households[YARD].main_action = "request_relief"
        asking = _seal(asking)
        apply_relief(asking, asking.clock.date)
        asked = _moved(asking, GRAIN, "relief", "household:")
        self.assertGreater(asked, 0.0, "Подача не пошла")
        self.assertEqual(_moved(asking, GRAIN, MARKET_REASON, "household:"), 0.0)
        self.assertAlmostEqual(asking_barn.amounts[GRAIN], 100.0 - asked, places=6)
        self.assertAlmostEqual(_delta(asking), 0.0, places=6)

        buying, buying_barn, _ = _granary_world(grain=100.0, silver=9.0, yard_grain=0.0)
        buying.households[YARD].main_action = "work_plot"
        buying = _seal(buying)
        apply_relief(buying, buying.clock.date)
        offer_manor_surplus(buying, root_manor(buying).id)
        sell_listed_grain(buying, buying.clock.date)
        sold = _moved(buying, GRAIN, MARKET_REASON, "household:")
        self.assertGreater(sold, 0.0, "Продажа с прилавка не пошла")
        self.assertEqual(_moved(buying, GRAIN, "relief", "household:"), 0.0)
        self.assertAlmostEqual(buying_barn.amounts[GRAIN], 100.0 - sold, places=6)
        self.assertAlmostEqual(_delta(buying), 0.0, places=6)

    def test_natural_exchange_works_without_silver(self) -> None:
        """Серебра в обращении нет — натуральный обмен законен (ADR 0139 п. 3).

        Плата покрывает всю сумму сделки: брёвна на сумму `проданное × цена
        зерна`, остаток брёвен у двора, зерно пришло в его сток.
        """
        world, barn, yard = _granary_world(grain=100.0, silver=0.0, yard_grain=0.0)
        for good in sorted(yard.amounts):
            yard.amounts[good] = 0.0
        log_before = 8.0
        yard.amounts[LOG] = log_before
        world = _seal(world)

        offer_manor_surplus(world, root_manor(world).id)
        sell_listed_grain(world, world.clock.date)

        sold = _moved(world, GRAIN, MARKET_REASON, "household:")
        self.assertGreater(sold, 0.0, msg="Натуральный обмен не сработал")
        self.assertEqual(yard.amounts[SILVER], 0.0, "Серебра не было, а двор его отдал")
        given = {
            e.good: e.amount for e in world.ledger.entries
            if e.reason == MARKET_REASON and e.dst_id == barn.id
        }
        self.assertIn(LOG, given, "Двор не отдал натуральный товар")
        value = given[LOG] * price_of(world, LOG)
        self.assertAlmostEqual(
            value, sold * price_of(world, GRAIN), places=6, msg="Отдано не по цене",
        )
        self.assertAlmostEqual(yard.amounts[LOG], log_before - given[LOG], places=6)
        self.assertAlmostEqual(yard.amounts[GRAIN], sold, places=6)
        self.assertAlmostEqual(_delta(world), 0.0, places=6)


class TestPriceComesFromCatalog(unittest.TestCase):
    """Цена — каталог (ADR 0101, ADR 0153 п. 2), не число в коде."""

    def test_catalogue_price_is_the_only_source(self) -> None:
        world, barn, yard = _granary_world(grain=100.0, silver=9.0, yard_grain=0.0)
        catalog = float(world.catalogs.goods[GRAIN].price_silver)
        self.assertGreater(catalog, 0.0, "У зерна в каталоге нет цены")
        self.assertAlmostEqual(price_of(world, GRAIN), catalog, places=9)
        offer_manor_surplus(world, root_manor(world).id)
        sell_listed_grain(world, world.clock.date)
        sold = _moved(world, GRAIN, MARKET_REASON, "household:")
        self.assertGreater(sold, 0.0, "Двор не купил — цену не из чего было платить")
        self.assertAlmostEqual(9.0 - yard.amounts[SILVER], sold * catalog, places=9)
        self.assertAlmostEqual(_delta(world), 0.0, places=6)

    def test_changing_the_catalog_price_changes_the_sale(self) -> None:
        world, barn, yard = _granary_world(grain=100.0, silver=9.0, yard_grain=0.0)
        with catalogue_price(world, GRAIN, 0.5):
            offer_manor_surplus(world, root_manor(world).id)
            sell_listed_grain(world, world.clock.date)
            sold = _moved(world, GRAIN, MARKET_REASON, "household:")
            self.assertGreater(sold, 0.0)
            self.assertAlmostEqual(9.0 - yard.amounts[SILVER], sold * 0.5, places=9)
        self.assertAlmostEqual(price_of(world, GRAIN), float(
            world.catalogs.goods[GRAIN].price_silver), places=9)

    def test_sale_has_no_price_parameter(self) -> None:
        """ADR 0153 п. 2: параметра цены в продаже не существует вовсе."""
        source = Path(exchange.__file__).read_text(encoding="utf-8")
        self.assertNotIn(
            "price_per_unit", source,
            "Параметр цены вернулся в продажу — цену можно задать извне",
        )
        for entry in (offer_manor_surplus, sell_listed_grain):
            for parameter in inspect.signature(entry).parameters:
                self.assertNotIn(
                    "price", parameter,
                    f"{entry.__name__}: появился параметр цены (ADR 0153 п. 2)",
                )

    def test_catalogue_covers_every_good_the_exchange_prices(self) -> None:
        """Константы в модуле — капы, сборы носильщика и цены скота, у которых в
        каталоге цены нет. Как только у товара появляется цена, дубль в коде
        обязан быть удалён — этот тест на это и смотрит."""
        world = load_scenario(HILL_SALT, seed=SEED)
        for good in (GRAIN, LOG):
            self.assertGreater(price_of(world, good), 0.0, f"{good}: нет цены в каталоге")
        for good in ("ox_m", "ox_f", "pig", "war_kit", "wooden_plough"):
            self.assertEqual(
                price_of(world, good), 0.0,
                f"{good}: в каталоге появилась цена — константа в коде стала дублем",
            )
        source = Path(exchange.__file__).read_text(encoding="utf-8")
        self.assertIn("price_of(world, SELL_GOOD)", source, "Цена зерна не из каталога")
        self.assertNotIn(
            f'"grain": {price_of(world, GRAIN)}', source, "Цена зерна продублирована в коде"
        )


class TestTribeIsIndependent(unittest.TestCase):
    """Племя независимо: повинностей 0, платежей 0, подачи 0 (ADR 0140)."""

    def _tribe(self, stance: str = "independent", tribute: float = 0.6):
        world = load_scenario(NATIVE, seed=SEED)
        tribe = world.tribes[TRIBE]
        tribe.stance = stance
        tribe.tribute_grain = tribute
        return _seal(world), tribe

    def test_independent_has_no_obligations(self) -> None:
        world, tribe = self._tribe("independent")
        households = tribe_households(world, tribe)
        self.assertTrue(households, "Племя без двора в сценарии")
        self.assertEqual(tribe_tribute_month(world, world.clock.date), [])
        self.assertEqual([o for o in world.obligations.values() if o.kind == "levy"], [])
        for household in households:
            self.assertEqual(household.obligation_ids, [], "У независимого племени повинность")
        self.assertAlmostEqual(_delta(world), 0.0, places=6)

    def test_independent_pays_nothing(self) -> None:
        """Ни оброка, ни подачи, ни взноса: племя ничего не отдаёт."""
        world, tribe = self._tribe("independent")
        for hid in TRIBE_YARDS:
            world.get_stock(f"household:{hid}").amounts[GRAIN] = 50.0
        world = _seal(world)
        phase_obligations(world)
        phase_exchange(world)
        out = sum(
            e.amount for e in world.ledger.entries
            if e.good == GRAIN
            and (e.src_id or "").startswith("household:hh_tribe")
            and e.dst_id != e.src_id
        )
        out += sum(
            e.amount for e in world.ledger.entries
            if e.good == GRAIN
            and (e.src_id or "").startswith("settlement:tribal_village")
        )
        self.assertAlmostEqual(out, 0.0, places=6, msg="Племя что-то отдало")
        self.assertAlmostEqual(_delta(world), 0.0, places=6)

    def test_independent_gets_no_relief_from_our_granary(self) -> None:
        """Юрист: подача не должна идти в общинный склад племени."""
        world, tribe = self._tribe("independent")
        root = manor_stock(world, root_manor(world))
        root.amounts[GRAIN] = 500.0
        for hid in TRIBE_YARDS:
            household = world.households[hid]
            household.main_action = "request_relief"
            household.minor_action = "request_relief"
            world.get_stock(household.stock_id).amounts[GRAIN] = 0.0
        world = _seal(world)
        for hid in TRIBE_YARDS:
            self.assertEqual(
                relief_sources(world, world.households[hid]), [],
                "У племенного двора появился источник нашей подачи",
            )
        stores = world.settlements["tribal_village"].stores_stock_id
        stores_before = world.get_stock(stores).amounts.get(GRAIN, 0.0)
        apply_relief(world, world.clock.date)
        self.assertAlmostEqual(
            world.get_stock(stores).amounts.get(GRAIN, 0.0), stores_before, places=6,
            msg="Подача ушла в общинный склад племени",
        )
        self.assertEqual(
            [e for e in world.ledger.entries
             if e.reason == "relief" and (e.dst_id or "").startswith("household:hh_tribe")],
            [],
            "Племенный двор получил нашу подачу",
        )
        self.assertAlmostEqual(_delta(world), 0.0, places=6)

    def test_allied_without_solidarity_has_no_obligations(self) -> None:
        """Регрессия юриста: `allied` — союз, а не подчинение, повинностей 0."""
        world, tribe = self._tribe("allied")
        self.assertNotIn("allied", TRIBUTE_STANCE, "Оброк при allied не отменён")
        self.assertEqual(tribe_tribute_month(world, world.clock.date), [])
        self.assertEqual([o for o in world.obligations.values() if o.kind == "levy"], [])
        for hid in TRIBE_YARDS:
            self.assertEqual(world.households[hid].obligation_ids, [], hid)
        world2, _ = self._tribe("allied")
        for hid in TRIBE_YARDS:
            world2.get_stock(f"household:{hid}").amounts[GRAIN] = 20.0
        world2 = _seal(world2)
        phase_obligations(world2)
        self.assertEqual(
            [o for o in world2.obligations.values() if o.kind == "levy"], [],
            "Союзник снова попал в оброк",
        )
        self.assertAlmostEqual(_delta(world2), 0.0, places=6)

    def test_solidarity_makes_the_due_equal_a_yards(self) -> None:
        """После солидарности повинность такая же, как у двора оборонца."""
        world, tribe = self._tribe("vassal")
        self.assertIn(tribe.stance, TRIBUTE_STANCE)
        households = tribe_households(world, tribe)
        tribute = tribe_tribute_month(world, world.clock.date)
        # Равенство длин не защищает от «племя без дворов»: тогда оба цикла ниже
        # пусты, а тест зелёный (ADR 0155).
        self.assertTrue(households, "У племени нет дворов — цикл обвинителя пуст")
        self.assertEqual(len(tribute), len(households))
        for obligation in tribute:
            self.assertEqual(obligation.kind, "levy")
            self.assertEqual(obligation.due_good, GRAIN)
            self.assertAlmostEqual(obligation.due_amount, 0.6, places=6)
        for household in households:
            world.get_stock(household.stock_id).amounts[GRAIN] = 10.0
        world = _seal(world)
        before = {o.id: o.paid_total for o in tribute}
        phase_obligations(world)
        paid = sum(o.paid_total - before[o.id] for o in tribute)
        self.assertAlmostEqual(paid, 0.6 * len(households), places=6)
        self.assertAlmostEqual(_delta(world), 0.0, places=6)

    def test_muster_stays_a_service_not_a_due(self) -> None:
        """Сужение оброка не отняло вызов: союзника звать можно (ADR 0066)."""
        self.assertIn("allied", MUSTER_STANCE)
        world, tribe = self._tribe("allied")
        for household in tribe_households(world, tribe):
            world.get_stock(household.stock_id).amounts["war_kit"] = 2.0
        self.assertEqual(tribe_tribute_month(world, world.clock.date), [])
        self.assertEqual(tribe_economy.tribe_muster_kits(world, tribe), 0.0)


class TestTribeTradesByDefault(unittest.TestCase):
    """Торговля с племенем открыта по умолчанию, в обе стороны (ADR 0140 п. 3)."""

    def _border_world(self, at_war: bool = False):
        """Племенной двор рядом с двором лорда: оба в одном кластере соседей."""
        world = load_scenario(NATIVE, seed=SEED)
        tribe = world.tribes[TRIBE]
        tribe.stance = "independent"
        court_tile = world.households[COURT].current_tile_id
        tribe_yard = world.households["hh_tribe_01"]
        tribe_yard.current_tile_id = sorted(neighbor_ids(world, world.tiles[court_tile]))[0]
        tribe_stock = world.get_stock(tribe_yard.stock_id)
        tribe_stock.amounts[SILVER] = 9.0
        tribe_stock.amounts["pig"] = 0.0
        tribe_stock.amounts[GRAIN] = 300.0
        court_stock = world.get_stock(f"household:{COURT}")
        court_stock.amounts["pig"] = 2.0
        court_stock.amounts[GRAIN] = 0.0
        court_stock.amounts[SILVER] = 5.0
        if at_war:
            world.hazards["hazard_war"] = Hazard(
                id="hazard_war",
                kind="war",
                tile_id=tribe_economy.tribe_tribe_tile(world, tribe),
                intensity=1.0,
                active=True,
            )
        return _seal(world), tribe, tribe_yard

    def _counter_border_world(self, at_war: bool = False):
        """Стенд прилавка на племенной двор: недобор, серебро и книга барона.

        Общий стенд `_border_world` не трогаем — он держит племенное зерно для
        проверки торговли в обе стороны. Здесь двор наоборот голоден, а книга
        барона (солидарность ADR 0156) кладёт его в видимость прилавка: без книги
        прилавок племени не видит вовсе, и проверять было бы нечего.
        """
        world, tribe, tribe_yard = self._border_world(at_war=at_war)
        book = root_manor(world)
        tribe_yard.manor_id = book.id
        if tribe_yard.id not in book.household_ids:
            book.household_ids.append(tribe_yard.id)
        stock = world.get_stock(tribe_yard.stock_id)
        stock.amounts[GRAIN] = 0.0
        stock.amounts[SILVER] = 9.0
        manor_stock(world, book).amounts[GRAIN] = 100.0
        return _seal(world), tribe, tribe_yard

    def test_counter_serves_only_the_manors_book(self) -> None:
        """Прилавок обслуживает дворы книги манора, а не всех рядом стоящих.

        Торговля с племенем открыта, но племенной двор в книгу барона не вписан —
        солидарности нет, значит и клиентом прилавка он не является. Как только
        двор в книге (ADR 0156), прилавок ему продаёт (см. тест выше).
        """
        world, tribe, tribe_yard = self._border_world()
        stock = world.get_stock(tribe_yard.stock_id)
        stock.amounts[GRAIN] = 0.0
        stock.amounts[SILVER] = 9.0
        manor_stock(world, root_manor(world)).amounts[GRAIN] = 100.0
        world = _seal(world)
        self.assertTrue(tribe_trade_open(world, tribe), "Стенд: торговля с племенем открыта")
        self.assertIsNone(manor_of_household(world, tribe_yard.id), "Стенд: двор вне книги")

        offer_manor_surplus(world, root_manor(world).id)
        sell_listed_grain(world, world.clock.date)

        self.assertGreater(
            _yard_shortfall(world, tribe_yard.id), 0.0, "Стенд: племенной двор голоден"
        )
        self.assertEqual(
            _moved(world, GRAIN, MARKET_REASON, f"household:{tribe_yard.id}"), 0.0,
            "Прилавок продал двору вне книги манора",
        )
        self.assertAlmostEqual(
            manor_stock(world, root_manor(world)).amounts[GRAIN],
            100.0 - _moved(world, GRAIN, MARKET_REASON, "household:"), places=6,
            msg="Амбар сошёлся не с проданным",
        )
        self.assertAlmostEqual(_delta(world), 0.0, places=6)

    def test_trade_is_open_by_default(self) -> None:
        world, tribe, _ = self._border_world()
        self.assertTrue(tribe_trade_open(world, tribe), "Торговля закрыта без войны")

    def test_independence_does_not_close_the_trade(self) -> None:
        world, tribe, _ = self._border_world()
        self.assertEqual(tribe.stance, "independent")
        self.assertEqual(
            [h for h in world.hazards.values() if h.kind == "war"], [],
            "В мире появилась угроза без повода",
        )
        self.assertTrue(tribe_trade_open(world, tribe))

    def test_tribe_buys_and_village_buys(self) -> None:
        """Обе стороны: племя покупает свинью, у племени покупают зерно."""
        world, _tribe, _yard = self._border_world()
        phase_exchange(world)
        bought_by_tribe = sum(
            e.amount for e in world.ledger.entries
            if e.reason in TRADE_REASONS
            and (e.dst_id or "").startswith("household:hh_tribe")
        )
        bought_from_tribe = sum(
            e.amount for e in world.ledger.entries
            if e.reason in TRADE_REASONS
            and (e.dst_id or "").startswith("household:hh_court")
        )
        self.assertGreater(bought_by_tribe, 0.0, "Племя ничего не купило")
        self.assertGreater(bought_from_tribe, 0.0, "У племени не купили ничего")
        self.assertAlmostEqual(_delta(world), 0.0, places=6)

    def test_military_relations_close_the_trade(self) -> None:
        """Военные отношения — единственная причина закрытия торговли."""
        world, tribe, _ = self._border_world()
        phase_exchange(world)
        open_moves = self._moves(world)
        self.assertGreater(open_moves, 0.0, "Торговля и так не идёт")
        war_world, war_tribe, _ = self._border_world(at_war=True)
        self.assertFalse(tribe_trade_open(war_world, war_tribe), "Война не закрыла торговлю")
        phase_exchange(war_world)
        self.assertEqual(
            self._moves(war_world), 0.0,
            "При военных отношениях с племенем торговля не закрыта",
        )
        self.assertAlmostEqual(_delta(war_world), 0.0, places=6)

    def test_barons_granary_does_not_sell_to_a_tribe_at_war(self) -> None:
        """Война закрывает прилавок: племенной двор в книге барона, а зерно не продаётся."""
        world, _tribe, tribe_yard = self._counter_border_world(at_war=True)
        barn = manor_stock(world, root_manor(world))
        offer_manor_surplus(world, root_manor(world).id)
        sell_listed_grain(world, world.clock.date)
        sold = _moved(world, GRAIN, MARKET_REASON, "household:")
        self.assertGreater(sold, 0.0, "Прилавок вообще не продал — стенд пустой")
        self.assertEqual(
            _moved(world, GRAIN, MARKET_REASON, f"household:{tribe_yard.id}"), 0.0,
            "Племя при войне купило зерно у барона",
        )
        self.assertAlmostEqual(barn.amounts[GRAIN], 100.0 - sold, places=6)
        self.assertAlmostEqual(_delta(world), 0.0, places=6)

    def test_granary_still_sells_to_a_tribe_without_war(self) -> None:
        """Торговля открыта — значит и прилавок племени не закрыт (обе стороны).

        Стенд кладёт племенной двор в книгу барона: без книги прилавок его просто
        не видит, и проверять было бы нечего. Книга — это солидарность ADR 0156.
        """
        world, _tribe, tribe_yard = self._counter_border_world()
        barn = manor_stock(world, root_manor(world))
        need = _yard_shortfall(world, tribe_yard.id)
        offer_manor_surplus(world, root_manor(world).id)
        sell_listed_grain(world, world.clock.date)
        sold = _moved(world, GRAIN, MARKET_REASON, f"household:{tribe_yard.id}")
        stock = world.get_stock(tribe_yard.stock_id)
        self.assertGreater(sold, 0.0, msg="Племю не продали зерно")
        self.assertAlmostEqual(sold, need, places=6, msg="Племю продали не по мере надобности")
        self.assertAlmostEqual(
            9.0 - stock.amounts[SILVER], sold * price_of(world, GRAIN), places=9,
        )
        self.assertAlmostEqual(
            barn.amounts[GRAIN], 100.0 - _moved(world, GRAIN, MARKET_REASON, "household:"),
            places=6, msg="Амбар сошёлся не с проданным",
        )
        self.assertAlmostEqual(_delta(world), 0.0, places=6)

    def _moves(self, world) -> float:
        """Сделки, где хоть одной стороной выступает племенной двор."""
        tribe_stocks = {
            f"household:{hid}" for hid in TRIBE_YARDS
        }
        return sum(
            e.amount for e in world.ledger.entries
            if e.reason in TRADE_REASONS
            and (e.src_id in tribe_stocks or e.dst_id in tribe_stocks)
        )


class TestExchangeStaysMatterNeutral(unittest.TestCase):
    """Дельта материи 0 во всех каналах (И-1)."""

    def test_year_of_the_native_village_keeps_matter(self) -> None:
        world = _seal(load_scenario(NATIVE, seed=SEED))
        for _ in range(12):
            run_month(world)
        self.assertAlmostEqual(_delta(world), 0.0, places=6)

    def test_year_of_the_hill_and_salt_keeps_matter(self) -> None:
        world = _seal(load_scenario(HILL_SALT, seed=SEED))
        for _ in range(12):
            run_month(world)
        self.assertAlmostEqual(_delta(world), 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
