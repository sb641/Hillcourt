"""Проверка агентной экономики двора: голод, рента, скот, труд, страх.

Пять обязательных сценариев v0 (задача Economist):
  1. нет еды и нет труда → голод, а не магический хлеб;
  2. рента переводит зерно из стока двора в сток замка;
  3. свинья не появляется без купли или приплода;
  4. travel_adjacent снимает руки с поля на этот месяц;
  5. при высокой опасности и низком авантюризме двор не идёт в чащу.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from hillcourt.economy import decisions, labor
from hillcourt.economy.labor import work_month
from hillcourt.economy.needs import food_shortfall
from hillcourt.economy.seasons import plot_yield
from hillcourt.engine.growth import run_detailed_growth
from hillcourt.engine.tick import (
    phase_consume,
    phase_decide,
    phase_growth,
    phase_obligations,
    run_month,
)
from hillcourt.ontology import Hazard
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SEED = 1729
EPSILON = 1e-9

# По клетке каждого рельефа для проверки потолка роста: индекс роста
# (`engine/growth.py:index_growth_tiles`) обходится по местам поселений, а лес в
# `v0_hill_and_salt` в него не входит, поэтому для `forest` берём клетку леса и
# сужаем индекс до неё (поле `World.growth_tile_ids` — данные, не механика).
# Правила роста, поимённо названные ADR 0143 §3: дрова, бревно, торф, рассол,
# сено, шерсть, железо и четыре зверя. Список ИМЁН, а не количество: новое
# правило с потолком (`grow_stone`, ADR 0182) не ломает тест, а потеря потолка у
# любого из этих — ломает.
ADR_0143_CAPPED_RULES = (
    "grow_firewood",
    "grow_log",
    "grow_peat",
    "grow_brine",
    "grow_hay",
    "grow_wool",
    "grow_iron",
    "game_squirrel",
    "game_rabbit",
    "game_deer",
    "game_boar",
)

TILE_BY_TERRAIN = {
    "field": "t_02_01",
    "forest": "t_00_01",
    "marsh": "t_07_06",
    "pasture": "t_00_02",
    "salt_flat": "t_08_05",
}


class TestHouseholdEconomy(unittest.TestCase):
    """Двор — автономный агент: сам решает, работает, ест и платит."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO)

    def _isolate(self, hid: str):
        """Оставить в мире один двор, чтобы обмен с соседями не смазывал тест."""
        household = self.world.households[hid]
        self.world.households = {hid: household}
        return household

    def test_no_food_no_work_gives_hunger_not_bread(self) -> None:
        household = self._isolate("hh_08")
        stock = self.world.get_stock(household.stock_id)
        for good in ("grain", "flour", "meat"):
            stock.amounts[good] = 0.0
        household.labor_days = 0.0
        household.main_action = "work_plot"
        household.minor_action = "idle_repair"
        grain_before = stock.amounts.get("grain", 0.0)

        work_month(self.world, self.world.clock.date)
        phase_consume(self.world)

        self.assertGreaterEqual(household.hunger_days, 1, "Голод не наступил")
        self.assertLessEqual(
            stock.amounts.get("grain", 0.0),
            grain_before + EPSILON,
            "Зерно появилось без работы и еды — магический хлеб",
        )

    def test_rent_moves_grain_from_household_to_court(self) -> None:
        household = self._isolate("hh_01")
        stock = self.world.get_stock(household.stock_id)
        stock.amounts["grain"] = 10.0
        court = self.world.settlements[self.world.player.court_settlement_id]
        court_stock = self.world.get_stock(court.stores_stock_id)
        before = court_stock.amounts.get("grain", 0.0)
        self.assertTrue(household.obligation_ids, "У двора нет повинности")

        phase_obligations(self.world)

        due = sum(
            self.world.obligations[oid].due_amount
            for oid in household.obligation_ids
            if self.world.obligations[oid].due_good == "grain"
            and self.world.obligations[oid].due_amount > 0
        )
        self.assertAlmostEqual(stock.amounts["grain"], 10.0 - due, places=6)
        self.assertAlmostEqual(
            court_stock.amounts.get("grain", 0.0), before + due, places=6
        )

    def test_pig_needs_purchase_or_birth(self) -> None:
        household = self._isolate("hh_03")
        stock = self.world.get_stock(household.stock_id)
        self.assertEqual(stock.amounts.get("pig", 0.0), 0.0)
        self.assertEqual(stock.amounts.get("silver", 0.0), 0.0)

        for _ in range(24):
            run_month(self.world)

        self.assertEqual(
            stock.amounts.get("pig", 0.0),
            0.0,
            "Свинья появилась без купли и без приплода",
        )

    def test_farrow_requires_breeding_pair(self) -> None:
        household = self._isolate("hh_06")
        stock = self.world.get_stock(household.stock_id)
        self.assertGreaterEqual(stock.amounts.get("pig", 0.0), 2.0)
        stock.amounts["hay"] = 20.0
        household.labor_days = 40.0
        household.main_action = "tend_animals"
        household.minor_action = "tend_animals"

        work_month(self.world, self.world.clock.date)

        self.assertGreater(
            stock.amounts.get("pig", 0.0), 2.0, "Приплод не случился при паре и сене"
        )

    def test_travel_removes_hands_from_field(self) -> None:
        household = self._isolate("hh_01")
        tile = self.world.tiles[household.current_tile_id]
        tile_stock = self.world.get_stock(tile.standing_stock_id)
        stock = self.world.get_stock(household.stock_id)
        household.labor_days = 60.0

        tile_stock.amounts["grain"] = 50.0
        household.main_action = "work_plot"
        household.minor_action = "idle_repair"
        work_month(self.world, self.world.clock.date)
        harvested = stock.amounts.get("grain", 0.0)
        self.assertGreater(harvested, 0.0, "На своей земле двор не собрал зерно")

        stock.amounts["grain"] = 0.0
        tile_stock.amounts["grain"] = 50.0
        household.main_action = "travel_adjacent"
        work_month(self.world, self.world.clock.date)

        self.assertTrue(household.traveling, "Двор не отмечен как ушедший")
        self.assertEqual(
            stock.amounts.get("grain", 0.0),
            0.0,
            "Двор ушёл, но всё равно работал на поле",
        )

    def test_fear_keeps_household_out_of_dangerous_wood(self) -> None:
        household = self.world.households["hh_court"]
        forest = self.world.tiles["t_00_01"]
        self.assertTrue(forest.hazard_ids, "Лес у холма должен быть опасен")
        self.world.clock.month = 6
        self.world.get_stock(household.stock_id).amounts["grain"] = 0.0
        household.hunger_days = 1
        # Второй соседний лес тоже опасен, чтобы «безопасного» сбора не осталось.
        safe_forest = self.world.tiles["t_01_00"]
        safe_forest.hazard_ids.append("haz_test")
        self.world.hazards["haz_test"] = Hazard(
            id="haz_test", kind="wolves", tile_id=safe_forest.id, intensity=0.6
        )

        for pid in household.member_ids:
            self.world.persons[pid].curiosity = 0.0
            self.world.persons[pid].fear = 1.0
        household.rumor_fear = 1.0
        household.adventurism = decisions.compute_adventurism(self.world, household)
        self.assertFalse(
            decisions.should_venture(self.world, household, forest),
            "Робкий двор пошёл в опасный лес",
        )
        main, _ = decisions.choose_actions(self.world, household)
        self.assertNotEqual(main, "forage_adjacent")

        for pid in household.member_ids:
            self.world.persons[pid].curiosity = 1.0
            self.world.persons[pid].fear = 0.0
        household.rumor_fear = 0.0
        household.adventurism = decisions.compute_adventurism(self.world, household)
        self.assertTrue(
            decisions.should_venture(self.world, household, forest),
            "Любопытный двор напрасно боится леса",
        )

    def test_grain_growth_has_no_cap_per_tile(self) -> None:
        """Зерно — единственное правило роста БЕЗ потолка, и это закон.

        ADR 0137 п. 2 и ADR 0142 п. 3: урожайного потолка у зерна нет, клетка
        копит столько standing-зерна, сколько успеет снять уборкой. Прежняя
        проверка требовала `cap_per_tile` = 40 — это отменённая норма, и вернуть
        её нельзя (ADR 0143 §3).

        Закон проверяется ДВУМЯ частями, и обе падающие по-своему:

        1. **В каталоге** у `grow_grain` нет `cap_per_tile`.
        2. **Движок не подрезает**: клетка растёт и на втором году. Потолок — это
           «после N перестало», поэтому единственный честный признак отсутствия
           потолка — **плоская линия, которой нет**: за 24 месяца standing зерна
           растёт каждый месяц, и на 24-м месяце больше чем вдвое против 12-го.

        **Чем заменён прежний прокси и почему он был ложью.** Раньше третьей
        частью стояло `seen[-1] > max(caps)` — «годовое зерно перевалило потолок
        САМОГО БОЛЬШОГО из остальных товаров». Это сравнение **разных рельефов**:
        `max(caps)` = 1200.0 — это потолок торфа (`grow_peat`, `terrain: marsh`), а
        зерно растёт на `field`. Прокси проверял не закон зерна, а величину чужого
        каталожного числа, и потому ломался сам собой, когда экономист поднял
        потолок торфа. Замерено: 685.0 против 1200.0, и **это было так ДО правки
        индекса роста** (проверено подменой индекса, дерево не трогали), то есть
        тест был красным на чужом долгу. Прокси-величина убрана; закон остался и
        проверяется напрямую.
        """
        params = self.world.catalogs.spawn_rules["grow_grain"].params
        self.assertNotIn(
            "cap_per_tile", params, "Потолок урожая вернулся в каталог (ADR 0137 п. 2)"
        )
        tile = self.world.tiles["t_02_01"]
        self.assertEqual(tile.terrain, "field")
        tile_stock = self.world.get_stock(tile.standing_stock_id)
        tile_stock.amounts["grain"] = 0.0

        seen = []
        for month in range(1, 25):
            self.world.clock.month = (month - 1) % 12 + 1
            phase_growth(self.world)
            seen.append(tile_stock.amounts.get("grain", 0.0))
        for month, (before, after) in enumerate(zip(seen, seen[1:]), start=1):
            self.assertGreater(
                after,
                before,
                f"Месяц {month}: зерно перестало расти — потолок урожая вернулся",
            )
        # `>=`, а не `>`: прирост без потолка детерминирован, поэтому второй год
        # даёт РОВНО вдвое больше первого (685.0 → 1370.0 на `t_02_01`). Это
        # равенство и есть доказательство, что потолка нет: любой потолок ниже
        # 1370.0 дал бы меньше удвоенного, и проверка упала бы на своём законе.
        self.assertGreaterEqual(
            seen[23], 2.0 * seen[11],
            f"Второй год не дал удвоенного первого ({seen[11]:.1f} → {seen[23]:.1f}): "
            f"рост упёрся в потолок",
        )

    def test_every_other_growth_rule_is_capped(self) -> None:
        """«Природа не бездонный кран» — закон для всех ОСТАЛЬНЫХ правил роста.

        ADR 0143 §3: `cap_per_tile` есть у **каждого** правила роста, кроме
        `grow_grain`, и остаётся законом. Проверяется и каталог, и движок:
        заполненная до потолка клетка за месяц не растёт, пустая — растёт.

        ЧИСЛА ПРАВИЛ В ТЕСТЕ НЕТ, и это не ослабление. Закон — «без потолка
        ровно одно правило, и это зерно»; количество правил меняется вместе с
        каталогом (`grow_stone` ADR 0182 сделал двенадцатым), и literals вида
        «11» устаревают тем же путём, каким умер рецепт без кандидатов. Поимённо
        проверяются только те правила, которые ADR 0143 §3 назвал: если у любого
        из них потолок пропадёт — тест упадёт на своём имени.
        """
        world = self.world
        growth_rules = {
            rid: rule
            for rid, rule in world.catalogs.spawn_rules.items()
            if rule.kind == "calendric" and rule.target == "good"
        }
        self.assertIn("grow_grain", growth_rules, "Зерно перестало быть правилом роста")
        capped = {
            rid
            for rid, rule in growth_rules.items()
            if rid != "grow_grain" and "cap_per_tile" in rule.params
        }
        uncapped = set(growth_rules) - capped
        self.assertEqual(
            capped,
            set(growth_rules) - {"grow_grain"},
            "Правило роста без потолка, кроме зерна (ADR 0143 §3)",
        )
        self.assertEqual(
            uncapped, {"grow_grain"},
            "Без потолка осталось не только зерно — природа снова бездонный кран",
        )
        for rule_id in ADR_0143_CAPPED_RULES:
            self.assertIn(
                rule_id, capped,
                f"{rule_id}: правило ADR 0143 §3 потеряло потолок",
            )
        for rule_id in sorted(capped):
            with self.subTest(rule=rule_id):
                rule = growth_rules[rule_id]
                good = str(rule.params["good"])
                cap = float(rule.params["cap_per_tile"])
                self.assertGreater(cap, 0.0, f"{rule_id}: потолок не положителен")
                tile = world.tiles[TILE_BY_TERRAIN[str(rule.params["terrain"])]]
                # Сузить индекс до ОДНОЙ клетки и звать `run_detailed_growth`
                # напрямую, а не `phase_growth`. Причина — не удобство: фаза
                # роста теперь ПЕРЕСЧИТЫВАЕТ индекс из данных мира
                # (`engine/growth.py::index_growth_tiles`), и сужение поля
                # внутри фазы перестало бы существовать. «Посмотреть на одну
                # клетку» — это измерение, а не состояние мира, поэтому оно и
                # делается измерением. Тот же приём — суженный мир из
                # `test_barony_100.TestLazyGrowth.test_growth_phase_does_not_scan_coarse_tiles`.
                # Обвязка фазы при этом не теряется: её проверяет
                # `test_barony_100.TestLazyGrowth.test_index_covers_worked_land`.
                world.growth_tile_ids = (tile.id,)
                stock = world.get_stock(tile.standing_stock_id)

                stock.amounts[good] = cap
                run_detailed_growth(world, 1.0)
                self.assertLessEqual(
                    stock.amounts.get(good, 0.0),
                    cap + EPSILON,
                    f"{rule_id}: природа перелила потолок {cap} — бездонный кран",
                )

                stock.amounts[good] = 0.0
                run_detailed_growth(world, 1.0)
                self.assertGreater(
                    stock.amounts.get(good, 0.0),
                    0.0,
                    f"{rule_id}: пустая клетка не получила ничего — правило мертво",
                )

    def test_pig_does_not_multiply_without_feed(self) -> None:
        household = self._isolate("hh_06")
        stock = self.world.get_stock(household.stock_id)
        stock.amounts["pig"] = 2.0
        stock.amounts["hay"] = 0.0
        household.main_action = "tend_animals"
        household.minor_action = "tend_animals"

        work_month(self.world, self.world.clock.date)

        self.assertEqual(
            stock.amounts.get("pig", 0.0), 2.0, "Приплод без сена — свинья из ничего"
        )


class TestNeighborPricing(unittest.TestCase):
    """Соседский торг (ADR 0057): кап зерна, цена носителя — с богатой стороны.

    Чужая сделка (разные клетки): зерно ≤ 2.0, покупатель платит чистую цену,
    продавец даёт сверх — укус носильщика 0.25 в отход (голодного сбором не
    облагать). Без серебра на чистую цену — платной сделки нет (дар — отдельный
    путь неимущих). Только `transfer`/потери, дельта 0. Детерминированно.

    Цена зерна — `goods.yml::grain.price_silver` (ADR 0101, ADR 0144 п. 1), и она
    берётся ИЗ КАТАЛОГА, а не из числа в тесте: кодовой цены 0.5 больше нет, и
    возвращать её нельзя. Потолок закупки `monthly_food_need` (ADR 0144 п. 2) к
    соседскому торгу не относится — он держит голод на закупке у барона
    (`economy/exchange.py:_trade_grain`).
    """

    SHIRE = ROOT / "design" / "scenarios" / "v0_shire.yml"

    def _pair(self):
        from hillcourt.economy import exchange

        world = load_scenario(self.SHIRE, seed=1729)
        buyer = world.households["hh_11"]  # t_00_09, голодный покупатель
        seller = world.households["hh_12"]  # t_01_09, сытый продавец
        self.assertNotEqual(buyer.current_tile_id, seller.current_tile_id)
        bstock = world.get_stock(buyer.stock_id)
        sstock = world.get_stock(seller.stock_id)
        bstock.amounts.clear()
        sstock.amounts.clear()
        return world, buyer, seller, bstock, sstock, exchange

    def _grain_price(self, world, exchange) -> float:
        """Каталожная цена зерна — единственный источник (ADR 0101, 0144 п. 1)."""
        price = exchange.price_of(world, exchange.SELL_GOOD)
        self.assertGreater(price, 0.0, "У зерна в каталоге нет цены")
        return price

    def test_cross_grain_fee_and_cap(self) -> None:
        world, buyer, seller, bstock, sstock, exchange = self._pair()
        price = self._grain_price(world, exchange)
        bstock.amounts.update({"silver": 10.0})
        sstock.amounts.update({"grain": 20.0})
        world.ledger.capture_initial(world.total_matter())
        exchange._trade_grain(world, [buyer, seller], world.clock.date, cross=True)
        grain_wires = [
            e for e in world.ledger.entries
            if e.reason == "buy_grain" and e.good == "grain"
        ]
        silver_wires = [
            e for e in world.ledger.entries
            if e.reason == "buy_grain" and e.good == "silver"
        ]
        self.assertEqual(len(grain_wires), 1, "Чужая сделка не состоялась")
        self.assertAlmostEqual(grain_wires[0].amount, 2.0, places=6,
                               msg="Зерно через клетку — не кап 2.0")
        self.assertEqual(len(silver_wires), 1)
        self.assertAlmostEqual(
            silver_wires[0].amount,
            2.0 * price,
            places=6,
            msg="Покупатель заплатил не каталожную цену зерна (ADR 0144 п. 1)",
        )
        bite_wires = [
            e for e in world.ledger.entries
            if e.reason == "porter_loss" and e.good == "grain"
        ]
        self.assertEqual(len(bite_wires), 1, "Укус носильщика не списан")
        self.assertAlmostEqual(bite_wires[0].amount, 0.25, places=6)
        self.assertAlmostEqual(bstock.amounts.get("grain", 0.0), 2.0, places=6)
        self.assertAlmostEqual(sstock.amounts.get("grain", 0.0), 20.0 - 2.25, places=6)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_cross_partial_fill_clean_price(self) -> None:
        world, buyer, seller, bstock, sstock, exchange = self._pair()
        price = self._grain_price(world, exchange)
        # Серебра ровно на 0.9 зерна каталожной цены: закупка неполная, кап не
        # достигнут, и покупатель платит ровно за взятое, без округления вверх.
        want = 0.9
        bstock.amounts.update({"silver": want * price})
        sstock.amounts.update({"grain": 20.0})
        world.ledger.capture_initial(world.total_matter())
        exchange._trade_grain(world, [buyer, seller], world.clock.date, cross=True)
        grain_got = sum(
            e.amount for e in world.ledger.entries
            if e.reason == "buy_grain" and e.good == "grain"
            and e.dst_id == buyer.stock_id
        )
        silver_paid = sum(
            e.amount for e in world.ledger.entries
            if e.reason == "buy_grain" and e.good == "silver"
        )
        self.assertLess(want, exchange.NEIGHBOR_GRAIN_MAX, "Фикстура не частичная")
        self.assertAlmostEqual(grain_got, want, places=6)
        self.assertAlmostEqual(
            silver_paid, want * price, places=6, msg="Цена не чистая: сбор с голодного"
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


class TestCourtWorksItsOwnField(unittest.TestCase):
    """Подача не отменяет пашню: двор с урожаем на своём гексе его собирает.

    Основание — ADR 0095 п. 1: трудодни двора сначала идут на собственное
    хозяйство, и голодный двор ест со своего гекса, если там что-то выросло.
    (Раньше здесь стояли номера 0113 и 0114 — оба отозваны, и обоснование живого
    решения не может висеть на отозванном номере.) Выбор «подача **или** пашня» был
    ложной развилкой и замыкался сам на себя: двор не пахал → не собрал → недобор
    → просит подачу → пашня не сорвана → недобор. Замер `start_stand` (24 мес,
    сид 1729): пять `INITIAL_FAMILIES` собрали `harvest_grain` 0.000 при стоящем
    зерне 8.0 на своём наделе, а двор, пришедший с едой в стоке, собрал 6.4516.

    Проверка не про реестр, а про решение: у голодного двора с урожаем на своём
    кормящем гексе минорное действие — пашня, а не дойка, и зерно с гекса попадает
    в его сток за один месяц.
    """

    def _hungry_court_with_crop(self):
        world = load_scenario(SCENARIO, seed=SEED)
        household = world.households["hh_09"]
        world.households = {household.id: household}
        stock = world.get_stock(household.stock_id)
        stock.amounts.clear()
        fields = [
            tile
            for tile in labor.own_tiles(world, household)
            if tile.terrain == "field"
        ]
        self.assertTrue(fields, "У двора нет пашни — стенд пустой")
        standing = world.get_stock(fields[0].standing_stock_id)
        standing.amounts["grain"] = 8.0
        world.ledger.capture_initial(world.total_matter())
        return world, household, stock, fields[0]

    def test_hungry_court_with_standing_crop_harvests_its_field(self) -> None:
        world, household, stock, field = self._hungry_court_with_crop()
        self.assertGreater(
            food_shortfall(world, household), 0.0, "Двор не голоден — стенд пустой"
        )
        phase_decide(world)
        self.assertEqual(
            (household.main_action, household.minor_action)[:1] + (household.minor_action,),
            ("request_relief", "work_plot"),
            msg=(
                "Голодный двор с урожаем на своём гексе не пашет: "
                f"main={household.main_action} minor={household.minor_action}"
            ),
        )
        run_month(world)
        harvested = sum(
            entry.amount
            for entry in world.ledger.entries
            if entry.reason == "harvest_grain" and entry.good == "grain"
            and entry.dst_id == household.stock_id
        )
        self.assertGreater(
            harvested, 0.0, "Двор просил подачу, а своё поле оставил стожать"
        )
        # Остаток на клетке судить нельзя: `grow_gain` на домене кладёт зерно быстрее,
        # чем одна партия снимает. Судим по СНЯТИЮ со стока клетки.
        drawn = sum(
            entry.amount
            for entry in world.ledger.entries
            if entry.reason == "harvest_grain" and entry.good == "grain"
            and entry.src_id == field.standing_stock_id
            and entry.dst_id == "sink:processing"
        )
        self.assertGreater(drawn, 0.0, "С гекса двора зерно не снято")
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )

    def test_court_without_a_holding_is_not_affected_by_the_rule(self) -> None:
        """Двор без СВОЕГО гекса развилкой не затронут — и это видно по действию.

        Закон этого класса (ADR 0095 п. 1) — про ВЕТКУ, а не про подачу: у двора
        **с урожаем на своём кормящем гексе** минорное действие становится
        `work_plot` (пашня вместо дойки). Проверка-контроль обязана доказать, что
        у двора БЕЗ кормящих клеток эта ветка не включается.

        **Правка 2026-09 (Implementer): умершее утверждение.** Прежняя проверка
        утверждала `relief == 0.0` для двора без гекса. Это НЕ закон этого класса,
        и оно прямо противоречит соседней `test_court_without_crop_keeps_asking_and_dairy`,
        где пустое поле — законно `request_relief`. Подача (ADR 0158 п. 3; прежний
        номер 0114 отозван — писать его как обоснование нельзя) идёт
        ДВОРОВУ С НЕДОБОРОМ независимо от того, есть ли у него пашня: безземельный
        двор без гекса голодает законно и подачу получает законно. Студенческий
        стенд `hh_06` — `free_landless`, кормящих клеток 0, и подача 1.135 за 12
        месяцев это правильное поведение, а не поломка.

        Что проверка теперь утверждает (и что действительно ломается при
        возврате бага):
          * `_feeding_tiles` пуст — стенд годен (иначе проверка пустая);
          * `_own_hex_has_crop` ложен — ветка пашни выключена;
          * действие НЕ `work_plot` и НЕ дойка — двор не пашет и не доит;
          * подача при этом идёт — голодный двор без гекса её ЗАКОННО получает
            (это отдельный закон, и его отрицание было ошибкой проверки);
          * наём положителен — труд идёт в дело, а не пропадает.
        """
        world = load_scenario(SCENARIO, seed=SEED)
        bare = [
            hid
            for hid in sorted(world.households)
            if not labor._feeding_tiles(world, world.households[hid])
        ]
        self.assertTrue(bare, "В стенде нет двора без гекса — проверка пустая")
        for hid in bare:
            household = world.households[hid]
            self.assertFalse(
                decisions._own_hex_has_crop(world, household),
                f"{hid}: у двора без кормящих клеток нашёлся урожай — стенд сломан",
            )
            world.households = {hid: household}
        world.ledger.capture_initial(world.total_matter())
        for _ in range(12):
            run_month(world)
        for hid in bare:
            household = world.households[hid]
            with self.subTest(household=hid):
                # Ветка пашни/дойки выключена: действие не про собственное
                # хозяйство. Это и есть проверяемый закон ADR 0095 п. 1.
                self.assertNotEqual(
                    household.main_action, "work_plot",
                    "Двор без гекса пашет — развилка включилась без урожая",
                )
                self.assertNotEqual(
                    household.minor_action, "work_plot",
                    "Двор без гекса пашет — развилка включилась без урожая",
                )
                self.assertEqual(
                    sum(
                        e.amount for e in world.ledger.entries
                        if e.reason in ("milk_cow", "milk_goat", "milk_sheep")
                        and e.dst_id == household.stock_id
                    ),
                    0.0, msg="Двор без гекса доил — у него нет скота",
                )
                # Подача законна (ADR 0158 п. 3): недобор есть, значит подача есть.
                # Раньше здесь стояло `== 0.0`, что противоречило соседней проверке.
                self.assertGreater(
                    float(world.stats.get("hired_days", 0.0)), 0.0,
                    "Двор без гекса не нанимался: труд идёт в никуда",
                )
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )

    def test_court_without_crop_keeps_asking_and_dairy(self) -> None:
        """Правило не превращается в заглушку: пустое поле — подача и дойка."""
        world = load_scenario(SCENARIO, seed=SEED)
        household = world.households["hh_09"]
        world.households = {household.id: household}
        stock = world.get_stock(household.stock_id)
        stock.amounts.clear()
        stock.amounts["goat"] = 1.0
        for tile in labor.own_tiles(world, household):
            if tile.terrain == "field":
                world.get_stock(tile.standing_stock_id).amounts.pop("grain", None)
        self.assertGreater(food_shortfall(world, household), 0.0)
        phase_decide(world)
        self.assertEqual(household.main_action, "request_relief")
        self.assertEqual(
            household.minor_action, "milk_animal",
            "Правило сработало на пустом поле: двор не пашет и не доит",
        )


class TestClearingForest(unittest.TestCase):
    """ADR 0175: расчистка леса — выполнимая работа, а не объявление.

    Рецепт `uproot_stumps` (`place: tile`, `requires_terrain: [forest]`) не имеет
    кандидата, если `_tiles_for_recipe` отдаёт `place: tile` только свой кормящий
    надел: у двора, у которого пашня, леса нет. Соседний лес обязан быть
    кандидатом, иначе рецепт мёртв ни одним тиком.

    Признак «рецепт дикой земли» выводится из каталога (`_works_wild_land`), а не
    из списка id: будущий лесной рецепт оживает сам. Мутация, которая это
    проверяет, — сузить ветку обратно до трёх прежних рецептов.
    """

    FOREST_TILE = "t_00_01"   # лес рядом с двором hh_05
    COURT = "hh_05"

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO)
        self.world.ledger.capture_initial(self.world.total_matter())
        self.household = self.world.households[self.COURT]
        self.world.households = {self.COURT: self.household}
        self.recipe = self.world.catalogs.recipes["uproot_stumps"]
        self.tile = self.world.tiles[self.FOREST_TILE]
        self.forest_stock = self.world.get_stock(self.tile.standing_stock_id)

    def _own_the_forest_as_demesne(self) -> None:
        """Отдать лесную клетку домену — иначе право `clear_forest` её не касается.

        Поле `Tile.regime_id` — данные, не механика (здесь же тест правила роста
        сужает `World.growth_tile_ids`). В сценарии `t_00_01` — `reserved_wood`
        (`take_game`, `leave`), а `clear_forest` по ADR 0182 п. 5 выдаёт только
        `demesne`; ни в одном v0-сценарии лесной клетки домена нет вообще, см.
        `test_clearing_needs_the_demesne_right_not_just_a_forest`.
        """
        self.tile.regime_id = "demesne"

    def _seed_standing(self, log: float = 36.0, stone: float = 36.0) -> None:
        """Засеять лесную клетку учтённой стоячей материей (через `Ledger`)."""
        for good, rule_id, amount in (("log", "grow_log", log), ("stone", "grow_stone", stone)):
            self.world.ledger.external_in(
                self.forest_stock, good, amount,
                reason=rule_id, rule_id=rule_id, date=self.world.clock.date,
            )

    def _only_clearing_in_queue(self) -> None:
        """Оставить в очереди двора только расчистку.

        Данные каталога, не механика: `cut_wood_if_allowed` отдаёт ещё и
        `cut_firewood`, а тот по закону ADR 0161 идёт раньше материала и съедает
        месячный труд целиком (15 трудодней партия против 60 у расчистки). Право
        выдачи рецепта проверяется отдельным тестом, здесь важна механика клеток.
        """
        action = self.world.household_actions["cut_wood_if_allowed"]
        self.world.household_actions["cut_wood_if_allowed"] = replace(
            action, recipes=["uproot_stumps"]
        )
        self.household.main_action = "cut_wood_if_allowed"
        self.household.minor_action = "pay_rent"

    def test_clearing_is_granted_by_a_household_action(self) -> None:
        """Шаг «право → рецепт» не может выпасть молча (ADR 0182 п. 5)."""
        recipes = self.world.household_actions["cut_wood_if_allowed"].recipes
        self.assertIn("uproot_stumps", recipes, "Расчистку не выдаёт ни одно действие двора")
        self.assertEqual(self.recipe.place, "tile")
        self.assertEqual(self.recipe.requires_terrain, ["forest"])
        self.assertTrue(self.recipe.transform, "камень на входе нет — нужен transform")

    def test_adjacent_forest_is_a_candidate_and_own_field_is_not(self) -> None:
        self._own_the_forest_as_demesne()
        candidates = labor._tiles_for_recipe(self.world, self.household, self.recipe)
        ids = [tile.id for tile in candidates]
        self.assertIn(
            self.FOREST_TILE, ids,
            "Соседний лес не кандидат: расчистка останется мёртвой",
        )
        own = self.household.current_tile_id
        self.assertNotIn(
            own, ids, "Пашня попала в кандидаты: requires_terrain не отфильтровал",
        )

    def test_clearing_runs_through_work_month_and_keeps_matter(self) -> None:
        """Замер: партии, камень в сток, бревно в потери, дельта материи 0."""
        self._own_the_forest_as_demesne()
        self._seed_standing()
        self._only_clearing_in_queue()
        self.household.labor_days = 180.0
        labor_budget = self.household.labor_days
        before = self.world.ledger.delta(self.world.total_matter())
        log_before = self.forest_stock.amounts.get("log", 0.0)
        stone_before = self.forest_stock.amounts.get("stone", 0.0)
        n0 = len(self.world.ledger.entries)

        work_month(self.world, self.world.clock.date)

        entries = [
            e for e in self.world.ledger.entries[n0:] if e.reason == "uproot_stumps"
        ]
        self.assertTrue(entries, "Расчистка не выполнилась ни одной партией")
        stone_out = sum(
            e.amount for e in entries
            if e.good == "stone" and e.dst_id == self.household.stock_id
        )
        log_loss = sum(
            e.amount for e in entries if e.good == "log" and e.dst_id == "sink:waste"
        )
        stone_loss = sum(
            e.amount for e in entries
            if e.good == "stone" and e.dst_id == "sink:waste"
        )
        self.assertGreater(stone_out, 0.0, "Камень не попал в сток двора")
        self.assertAlmostEqual(
            stone_out, self.world.get_stock(self.household.stock_id).amounts.get("stone", 0.0),
            places=6, msg="В сток двора пришло не только камнем расчистки",
        )
        # Баланс партии взят из каталога, season-множитель общий, поэтому отношения
        # точные: 9.6 камня против 12.0 бревна и 2.4 камня в потери (ADR 0182 п. 4).
        self.assertAlmostEqual(log_loss / stone_out, 12.0 / 9.6, places=9)
        self.assertAlmostEqual(stone_loss / stone_out, 2.4 / 9.6, places=9)
        # Бревно двор берёт отдельно (`cut_wood`): расчистка бревна не выдаёт.
        self.assertAlmostEqual(
            sum(
                e.amount for e in entries
                if e.good == "log" and e.dst_id == self.household.stock_id
            ),
            0.0, places=9, msg="Расчистка выдала бревно — это работа `cut_wood`",
        )
        # Клетка леса отдала стоящее: 12.0 бревна и 12.0 камня на партию.
        factor = labor._recipe_yield_factor(
            self.world, self.household, self.recipe,
            plot_yield(self.world, self.world.clock.month), self.tile,
        )
        batches = stone_out / (self.recipe.outputs["stone"] * factor)
        self.assertGreaterEqual(batches, 1.0 - EPSILON, "Партий меньше одной")
        self.assertLessEqual(
            batches * self.recipe.labor_days, labor_budget + EPSILON,
            msg="Партий больше, чем месячный труд двора",
        )
        for good, before_amount in (("log", log_before), ("stone", stone_before)):
            drawn = sum(
                e.amount for e in entries
                if e.good == good and e.dst_id == "sink:processing"
            )
            self.assertAlmostEqual(
                self.forest_stock.amounts.get(good, 0.0), before_amount - drawn, places=6,
                msg=f"{good} клетки не убыл на взятое",
            )
        self.assertAlmostEqual(
            self.world.ledger.delta(self.world.total_matter()), before, places=6,
            msg="Расчистка создала или сожгла материю",
        )

    def test_clearing_needs_the_demesne_right_not_just_a_forest(self) -> None:
        """Призрак права запрещён (ADR 0150, ADR 0161, ADR 0182 п. 5).

        Лес `reserved_wood` — это `take_game` и `leave`; расчистка там не его
        работа, даже если двор умеет `cut_wood_if_allowed`. Иначе право, которое
        выдаёт домен, исполнял бы частный двор.
        """
        self.assertEqual(
            self.world.catalogs.land_regimes["reserved_wood"].allowed_actions,
            ["take_game", "leave"],
            "Фикстура поехала: у reserved_wood появилось лишнее право",
        )
        self.assertEqual(
            [rid for rid, regime in self.world.catalogs.land_regimes.items()
             if "clear_forest" in regime.allowed_actions],
            ["demesne"],
            "Расчистку выдаёт не только домен",
        )
        self.assertEqual(
            [tile.id for tile in labor._tiles_for_recipe(
                self.world, self.household, self.recipe)],
            [],
            "Лес reserved_wood стал кандидатом на выкорчёвку",
        )
        self._own_the_forest_as_demesne()
        self.assertEqual(
            [tile.id for tile in labor._tiles_for_recipe(
                self.world, self.household, self.recipe)],
            [self.FOREST_TILE],
            "Лес домена не стал кандидатом на выкорчёвку",
        )

    def test_court_without_adjacent_forest_has_no_clearing_candidate(self) -> None:
        """Границы досягаемости: соседнего леса нет — расчистка не идёт нигде."""
        world = load_scenario(SCENARIO)  # setUp оставил в мире только hh_05
        court = world.households["hh_02"]
        candidates = labor._tiles_for_recipe(world, court, self.recipe)
        self.assertEqual(
            [tile.id for tile in candidates], [],
            "У двора без соседнего леса появился кандидат на расчистку",
        )
        self.assertNotIn(
            "forest",
            [tile.terrain for tile in labor._adjacent_tiles(world, court)],
            "Фикстура поехала: у hh_02 появился соседний лес",
        )

    def test_food_recipes_on_wild_land_stay_on_their_own_holding(self) -> None:
        """Соседство — для рубки и добычи, а не для сбора: грибы и ягоды как были.

        Иначе двор собирал бы чужой лес (ADR 0077), а `forage_wild`, которому
        соседство разрешено законом, разбирается отдельной веткой выше.
        """
        for rid in ("gather_mushrooms", "pick_berries", "take_game_deer"):
            recipe = self.world.catalogs.recipes.get(rid)
            self.assertIsNotNone(recipe, rid)
            self.assertFalse(
                labor._works_wild_land(self.world, recipe),
                f"{rid}: сбор еды не должен доставать соседнюю клетку",
            )
        for rid in ("cut_firewood", "cut_wood", "mine_iron", "uproot_stumps"):
            recipe = self.world.catalogs.recipes[rid]
            self.assertTrue(
                labor._works_wild_land(self.world, recipe),
                f"{rid}: производство на дикой земле должно доставать соседнюю клетку",
            )


if __name__ == "__main__":
    unittest.main()
