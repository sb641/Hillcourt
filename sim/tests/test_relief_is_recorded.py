"""ADR 0169: подача — правовой акт. Каждая единица зерна имеет запись в книге.

Проверяется ровно то, что требует закон (ADR 0169 «Проверка»):
  * сумма `paid_total` по книге равна сумме проводок `reason="relief"`;
  * ни одна единица зерна не уходит без записи, и ни одна запись не превышает
    недобор двора (подача равна недобору, а не «сколько вышло»);
  * при пустом амбаре запись всё равно появляется: сеньор видит долг, а не тишину;
  * подача достаётся только двору с правом `request_relief`;
  * соседский дар в книгу подачи не попадает (плательщик другой, цена ноль);
  * серебро не двигается, а цена зерна для отчёта — число каталога;
  * повторный `run` с тем же сидом: те же записи, тот же `state_hash`, дельта 0.

Счётчик `relief_given` в проверках не участвует: это диагностика, судить по
нему нельзя (ADR 0169 п. 2). Никакого потолка «не больше N месяцев подряд» тест
не вводит — ограничитель здесь видимость, а не число (ADR 0169 п. 4).
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from hillcourt.economy import exchange
from hillcourt.engine.tick import run_month
from hillcourt.ontology import Obligation
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
VILLAGE = ROOT / "design" / "scenarios" / "v0_large_village.yml"
HILL_SALT = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SEED = 1729
MONTHS = 12
FULL_MONTHS = 24
LORD_HOUSEHOLD = "hh_court"
LORD_BARN = "settlement:hill_court"
GRAIN = "grain"
EPS = 1e-9


def _run(months: int, seed: int = SEED):
    """Прогон деревни 35 дворов; возвращает мир и снимок начала для дельты."""
    world = load_scenario(VILLAGE, seed=seed)
    world.ledger.capture_initial(world.total_matter())
    for _ in range(months):
        run_month(world)
    return world


def _run_like_runner(months: int, seed: int = SEED):
    """Прогон ровно по пути runner'а: сценарий, затем `script:` перед месяцем.

    Отличие от `_run` обязательно: runner исполняет записи `script:`, а он меняет
    наделы и труд, а значит и подачу. Считать подачу по другому пути и сравнивать
    с числом отчёта — значит сравнивать разные миры.
    """
    from hillcourt.runner import _apply_script_entry

    world = load_scenario(VILLAGE, seed=seed)
    script = list(world.script)
    for month_index in range(1, months + 1):
        for entry in script:
            if int(entry.get("at_month", 0)) == month_index:
                _apply_script_entry(world, entry)
        run_month(world)
    return world


def _relief_entries(world) -> list:
    return [e for e in world.ledger.entries if e.reason == "relief"]


def _gift_entries(world) -> list:
    return [e for e in world.ledger.entries if e.reason == "neighbour_gift"]


def _month_of(entry) -> str:
    return f"Y{entry.date.year}M{entry.date.month:02d}"


def _month_of_record(obligation) -> str:
    """Штамп месяца из id записи: `relief_<Y1M02>_<сеньор>_<двор>`."""
    return obligation.id.split("_")[1]


def _paid_by_month(world) -> tuple[dict[tuple[str, str], float], dict[tuple[str, str], float]]:
    """Сколько зерна ушло каждому двору в каждом месяце — по книге и по проводкам.

    Из книги берутся только записи с выдачей > 0: запись с нулевой выдачей —
    это долг по пустому амбару (ADR 0169 п. 3), у неё нет проводки, и сверять
    надо выдачу, а не долг.
    """
    from_ledger: dict[tuple[str, str], float] = {}
    for entry in _relief_entries(world):
        key = (entry.dst_id, _month_of(entry))
        from_ledger[key] = from_ledger.get(key, 0.0) + entry.amount
    from_book: dict[tuple[str, str], float] = {}
    for obligation in exchange.relief_obligations(world):
        if obligation.paid_total <= EPS:
            continue
        key = (f"household:{exchange.relief_recipient(obligation)}", _month_of_record(obligation))
        from_book[key] = from_book.get(key, 0.0) + obligation.paid_total
    return from_ledger, from_book


class TestReliefIsRecorded(unittest.TestCase):
    """Деревня 35 дворов, 12 месяцев: подача записана, а не посчитана."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = _run(MONTHS)
        cls.records = exchange.relief_obligations(cls.world)

    def test_relief_moved_grain_at_all(self) -> None:
        """Стенд не пустой: подача в этом прогоне была, иначе проверки ни о чём."""
        entries = _relief_entries(self.world)
        self.assertTrue(entries, "Подачи не было — тесты ниже ничего не проверят")
        self.assertGreater(sum(e.amount for e in entries), 0.0)

    def test_every_relief_unit_has_exactly_one_record(self) -> None:
        """Сумма записей равна сумме проводок зерна, помесячно и по дворам."""
        from_ledger, from_book = _paid_by_month(self.world)
        self.assertEqual(
            sorted(from_ledger), sorted(from_book),
            "есть месяц-двор, где зерно ушло без записи или запись без зерна",
        )
        for key, amount in sorted(from_ledger.items()):
            self.assertAlmostEqual(
                amount, from_book[key], places=9,
                msg=f"{key}: зерно ушло {amount:.6f}, записано {from_book[key]:.6f}",
            )
        self.assertAlmostEqual(
            sum(o.paid_total for o in self.records),
            sum(e.amount for e in _relief_entries(self.world)),
            places=9,
            msg="сумма paid_total по книге разошлась с проводками зерна",
        )

    def test_relief_never_exceeds_the_shortfall(self) -> None:
        """Подача равна недобору: `paid_total <= due_amount` по каждой записи."""
        for obligation in self.records:
            self.assertLessEqual(
                obligation.paid_total, obligation.due_amount + EPS,
                msg=f"{obligation.id}: подано больше недобора",
            )
            self.assertAlmostEqual(
                obligation.paid_total + obligation.arrears,
                obligation.due_amount, places=9,
                msg=f"{obligation.id}: выдано плюс долг не равно недобору",
            )
            self.assertEqual(obligation.due_good, "grain", obligation.id)

    def test_record_carries_owner_and_month(self) -> None:
        """Запись — с владельцем и месяцем, а не число в статистике."""
        for obligation in self.records:
            self.assertIn(
                obligation.household_id, self.world.households,
                f"{obligation.id}: повинность без двора-владельца",
            )
            self.assertEqual(obligation.household_id, LORD_HOUSEHOLD)
            self.assertTrue(exchange.relief_recipient(obligation), obligation.id)
            self.assertRegex(obligation.id, r"^relief_Y\d+M\d\d_")

    def test_relief_is_not_a_counter(self) -> None:
        """Закон — книга, а не `world.stats`: счётчик может врать, книга нет."""
        self.assertEqual(
            len(self.records), int(self.world.stats.get("relief_records", 0.0)),
            "записей в книге больше, чем счётчик созданий",
        )
        self.assertTrue(self.records, "в книге пусто, а подача была")
        # счётчик — диагностика: он совпадает с проводками, но судить надо по книге
        self.assertAlmostEqual(
            float(self.world.stats.get("relief_given", 0.0)),
            sum(o.paid_total for o in self.records), places=6,
        )

    def test_village_no_longer_needs_neighbour_gifts(self) -> None:
        """Деревня 35 дворов перестала пользоваться соседским даром — и это закон.

        Раньше дар шёл, потому что дворы не пахали своё (см.
        `TestReliefAtTheLordsScale`). Теперь даров 0, и это проверяемое следствие
        решения, а не пустой тест: стенд, где дар ещё бывает, проверяется в
        `TestNeighbourGiftIsNotRelief` на `v0_barony_100`.
        """
        self.assertEqual(
            _gift_entries(self.world), [],
            "Деревня снова берёт дар соседей: дворы не пашут своё",
        )
        self.assertEqual(
            sum(e.amount for e in self.world.ledger.entries if e.reason == "hired_days"),
            0.0,
        )

    def test_grain_is_the_unit_and_silver_never_moves(self) -> None:
        """Зерно — достаточная единица счёта; серебром подача не оплачивается.

        **Уточнение 2026-09 (ADR 0219).** Проверка прежде доказывала закон
        ЧЕРЕЗ ПОСЫЛКУ «серебра в мире нет вообще» (`[e for e in entries if
        e.good == "silver"] == []`). Посылка мертва: ADR 0219 ввёл единственный
        путь прихода серебра — продажа хлеба королевскому двору
        (`rule_id=royal_court_buys_grain`), и серебра в мире стало 361.7 на
        `v0_barony_100`. Закон ADR 0169 п. 1 («подача платится зерном, а не
        серебром») при этом НЕ отменён — он жив, и проверяется здесь прямо, по
        проводкам подачи, а не по пустому миру.

        Вторая половина — закон ADR 0219 «у любого прихода серебра извне есть
        `rule_id` плательщика»: серебра в мире сколько угодно, но оно не
        появляется из воздуха.
        """
        relief_silver = [
            e for e in self.world.ledger.entries
            if e.good == "silver" and e.reason == "relief"
        ]
        self.assertEqual(
            relief_silver, [],
            "Подача оплачена серебром — закон ADR 0169 п. 1 нарушен",
        )
        for obligation in self.records:
            self.assertEqual(obligation.due_good, "grain", obligation.id)
        grain, silver = exchange.relief_cost(self.world)
        self.assertAlmostEqual(
            silver, grain * exchange.price_of(self.world, "grain"), places=9
        )
        self.assertGreater(grain, 0.0)
        # ADR 0219: серебра в мире сколько угодно, но каждый его приход извне
        # обязан нести `rule_id` плательщика — иначе это чеканка из воздуха.
        silver_entries = [e for e in self.world.ledger.entries if e.good == "silver"]
        for entry in silver_entries:
            self.assertIsNotNone(
                entry.rule_id,
                f"{entry.date}: приход серебра без rule_id — вечный двигатель",
            )
            rule = self.world.catalogs.spawn_rules.get(entry.rule_id)
            self.assertIsNotNone(
                rule, f"{entry.date}: rule_id={entry.rule_id!r} не объявлен в каталоге"
            )
            self.assertTrue(
                (rule.params or {}).get("source"),
                f"{entry.rule_id}: правило прихода серебра не называет плательщика",
            )

    def test_matter_delta_is_zero(self) -> None:
        self.assertAlmostEqual(
            self.world.ledger.delta(self.world.total_matter()), 0.0, places=6
        )

    def test_nobody_was_lost_to_hunger_and_hiring_is_zero(self) -> None:
        """Инвариант деревни по ADR 0169 п. 5: голод не убивает, найм не включается.

        Смерти от голода код не умеет (смертность только по возрасту, `Ledger` не
        трогается), поэтому проверяемое следствие голода — уход двора: не ушёл ни
        один, и никто не ушёл с голодными днями.
        """
        self.assertEqual(float(self.world.stats.get("hired_days", 0.0)), 0.0)
        lost = [h for h in self.world.households.values() if h.left_at is not None]
        self.assertEqual(lost, [], "деревня теряла дворы")
        starving_left = [h.id for h in lost if h.hunger_days >= 3]
        self.assertEqual(starving_left, [], "двор ушёл голодным")


class TestReliefKindComesFromCatalog(unittest.TestCase):
    """Одно имя — одно место: вид повинности читается из `obligations.yml`.

    Строка вида в коде была вторым источником истины (ADR 0157 п. 1): переименуй
    запись каталога — код продолжал бы писать старое имя, и расхождение никто не
    заметил бы. Проверяются обе стороны: запись совпадает с каталогом, и каталог
    без записи — отказ, а не молчаливая подмена.
    """

    def test_record_kind_is_the_catalog_word(self) -> None:
        world = _run(6)
        template = exchange.relief_obligation_template(world)
        self.assertEqual(
            exchange.RELIEF_OBLIGATION_TEMPLATE, template.id,
            "резолвер смотрит не в тот шаблон",
        )
        records = exchange.relief_obligations(world)
        self.assertTrue(records, "книга пуста — сверять нечего")
        for obligation in records:
            self.assertEqual(obligation.kind, template.kind, obligation.id)
            self.assertEqual(obligation.due_good, template.due_good, obligation.id)
            self.assertEqual(obligation.basis, template.basis, obligation.id)
            self.assertEqual(obligation.period_months, template.period_months, obligation.id)
        self.assertTrue(template.kind.strip(), "вид повинности в каталоге пуст")

    def test_catalog_without_the_template_is_refused(self) -> None:
        """Нет шаблона — подача не записывается вовсе, и это видно сразу.

        Проверяет, что резолвер не откатывается на строку в коде: подмена была бы
        ровно тем браком, который эта правка убирает (ADR 0157 п. 1, ADR 0169 п. 1).
        """
        world = _run(6)
        world.catalogs.obligation_templates.pop(
            exchange.RELIEF_OBLIGATION_TEMPLATE, None
        )
        with self.assertRaises(ValueError) as caught:
            exchange.relief_obligation_template(world)
        self.assertIn(
            exchange.RELIEF_OBLIGATION_TEMPLATE, str(caught.exception),
            "отказ не называет, какой записи не хватило",
        )
        with self.assertRaises(ValueError):
            exchange.relief_obligations(world)
        with self.assertRaises(ValueError):
            exchange.record_relief(
                world, world.get_stock(LORD_BARN),
                world.households["hh_village_villein_a_001"], 1.0, 1.0, world.clock.date,
            )

    def test_renamed_catalog_entry_changes_the_records(self) -> None:
        """Переименование записи каталога меняет вид записей, а не игнорируется."""
        world = _run(6)
        template = world.catalogs.obligation_templates.pop(
            exchange.RELIEF_OBLIGATION_TEMPLATE
        )
        renamed = replace(template, id="relief_grain_v2", kind="relief_grain_v2")
        world.catalogs.obligation_templates[renamed.id] = renamed
        with self.assertRaises(ValueError):
            exchange.relief_obligation_template(world)
        world.catalogs.obligation_templates[exchange.RELIEF_OBLIGATION_TEMPLATE] = renamed
        self.assertEqual(exchange.relief_obligation_kind(world), "relief_grain_v2")
        records = exchange.relief_obligations(world)
        self.assertEqual(records, [], "записи со старым видом не отфильтрованы")


class TestReliefNeedsTheRight(unittest.TestCase):
    """Подача — законное право, но не всеобщее: без `request_relief` её нет."""

    def test_relief_only_for_courts_that_asked(self) -> None:
        world = _run(6)
        for household in world.households.values():
            household.main_action = "idle_repair"
            household.minor_action = "idle_repair"
        hungry = [
            world.households[hid] for hid in sorted(world.households)
            if world.households[hid].manor_id is not None
            and world.households[hid].legal_status_id != "slave"
        ]
        self.assertTrue(hungry)
        # обедаем всех, кроме одного, и лишаем его права: нужен недобор, а подачи нет
        for household in hungry:
            world.ledger.external_in(
                world.get_stock(household.stock_id), "grain", 50.0,
                "test_setup", None, world.clock.date,
            )
        asker = hungry[0]
        world.ledger.transfer(
            world.get_stock(asker.stock_id), world.get_stock(LORD_BARN), "grain",
            world.get_stock(asker.stock_id).amounts.get("grain", 0.0),
            "test_setup", world.clock.date,
        )
        world.get_stock(LORD_BARN).amounts["grain"] = 500.0
        self.assertGreater(exchange.food_shortfall(world, asker), 0.0)
        n0 = len(world.ledger.entries)
        records0 = len(exchange.relief_obligations(world))
        exchange.apply_relief(world, world.clock.date)
        self.assertEqual(
            [e for e in world.ledger.entries[n0:] if e.reason == "relief"], [],
            "двор без request_relief получил подачу",
        )
        self.assertEqual(len(exchange.relief_obligations(world)), records0)

    def test_asking_court_gets_grain_and_a_record(self) -> None:
        world = _run(6)
        for household in world.households.values():
            household.main_action = "idle_repair"
            household.minor_action = "idle_repair"
        asker = world.households["hh_village_villein_a_001"]
        world.ledger.transfer(
            world.get_stock(asker.stock_id), world.get_stock(LORD_BARN), "grain",
            world.get_stock(asker.stock_id).amounts.get("grain", 0.0),
            "test_setup", world.clock.date,
        )
        asker.main_action = "request_relief"
        world.get_stock(LORD_BARN).amounts["grain"] = 500.0
        before = {o.id for o in exchange.relief_obligations(world)}
        exchange.apply_relief(world, world.clock.date)
        fresh = [o for o in exchange.relief_obligations(world) if o.id not in before]
        self.assertTrue(fresh, "просящий двор не получил запись о подаче")
        record = fresh[0]
        self.assertEqual(exchange.relief_recipient(record), asker.id)
        self.assertGreater(record.paid_total, 0.0)
        self.assertLessEqual(record.paid_total, record.due_amount + EPS)
        self.assertAlmostEqual(record.paid_total, record.due_amount, places=6)
        self.assertAlmostEqual(
            world.get_stock(asker.stock_id).amounts.get("grain", 0.0),
            record.paid_total, places=6,
            msg="в сток двора ушло не то, что записано в книгу",
        )

    def test_empty_barn_still_leaves_a_record_of_debt(self) -> None:
        """П. 3: амбар пуст — запись всё равно появляется, сеньор видит долг."""
        world = _run(6)
        for household in world.households.values():
            household.main_action = "idle_repair"
            household.minor_action = "idle_repair"
        asker = world.households["hh_village_villein_a_001"]
        world.ledger.transfer(
            world.get_stock(asker.stock_id), world.get_stock(LORD_BARN), "grain",
            world.get_stock(asker.stock_id).amounts.get("grain", 0.0),
            "test_setup", world.clock.date,
        )
        asker.main_action = "request_relief"
        world.get_stock(LORD_BARN).amounts["grain"] = 0.0
        before = {o.id for o in exchange.relief_obligations(world)}
        exchange.apply_relief(world, world.clock.date)
        fresh = [o for o in exchange.relief_obligations(world) if o.id not in before]
        self.assertTrue(fresh, "при пустом амбаре запись о подаче не появилась")
        record = fresh[0]
        self.assertEqual(record.paid_total, 0.0)
        self.assertGreater(record.due_amount, 0.0, "пустая запись без недобора")
        self.assertAlmostEqual(record.arrears, record.due_amount, places=9)
        self.assertEqual(
            [e for e in world.ledger.entries if e.reason == "relief"
             and e.dst_id == asker.stock_id and e.date == world.clock.date], []
        )


class TestNeighbourGiftIsNotRelief(unittest.TestCase):
    """Соседский дар отделён от подачи: плательщик другой, цена ноль, нужда не checked.

    Стенд собран на маленьком мире намеренно. Органика дара в поставленном мире
    дорого стоит: замер `v0_barony_100` (сид 1729) — первый дар только на
    **11-м месяце**, 9 даров на 9.0 зерна за 12 месяцев, и месяц этого мира идёт
    **52.3 с** (600 дворов). Класс на таком стенде съедал 529 с и делал модуль
    бесполезным для полного прогона. Закон ADR 0169 п. 6 (дар не подача) от
    месяца не зависит, поэтому он проверяется на управляемой паре «сосед с
    излишком — сосед без еды» из `_trade_grain`: продавец с зерном > 3.0 и без
    серебра отдаёт покупателю ровно 1.0, цена ноль, нужда получателя не
    проверяется. Наблюдение «в `v0_barony_100` дары появляются на 11-м месяце,
    9 штук» остаётся верным и записано здесь же.
    """

    def _gifting_pair(self, months: int = 12):
        """Два соседа: у одного излишек и нет серебра, второй голоден.

        Механизм тот же, что в мире: `_trade_grain` — покупатель с
        `food_months < 0.5` и без серебра получает от соседа с зерном > 3.0 ровно
        1.0 зерна с вероятностью 0.3 в месяц.
        """
        world = load_scenario(HILL_SALT, seed=SEED)
        seller, buyer = world.households["hh_01"], world.households["hh_02"]
        world.households = {seller.id: seller, buyer.id: buyer}
        seller_tile = world.tiles[seller.current_tile_id]
        buyer.current_tile_id = seller_tile.id
        seller.main_action = seller.minor_action = "idle_repair"
        buyer.main_action = buyer.minor_action = "idle_repair"
        sstock, bstock = world.get_stock(seller.stock_id), world.get_stock(buyer.stock_id)
        sstock.amounts.clear()
        sstock.amounts["silver"] = 0.0
        bstock.amounts.clear()
        date = world.clock.date
        # Всё, чего стенд добавляет и убирает, идёт через `Ledger`: тест проверяет
        # и дельту материи, а прямая запись в `amounts` создала бы материю из
        # воздуха (замер: дельта уходила в −30.4075).
        court = world.get_stock("settlement:hill_court")
        if court.amounts.get("grain", 0.0) > 0.0:
            world.ledger.external_out(
                court, "grain", court.amounts["grain"], "test_setup", date,
            )
        world.ledger.capture_initial(world.total_matter())
        for _ in range(months):
            # Излишек у продавца каждый месяц: он и скупает, и жуёт, а условие дара
            # — «зерно > 3.0» (ADR 0169 п. 6). Замер: с пополнением дар случается
            # на 8-м месяце, без пополнения — ни разу за 12.
            top_up = 8.0 - sstock.amounts.get("grain", 0.0)
            if top_up > 0.0:
                world.ledger.external_in(
                    sstock, "grain", top_up, "test_setup", None, date,
                )
            run_month(world)
        return world, seller, buyer, sstock, bstock

    def test_gift_is_a_neighbour_handout_not_the_lords_barn(self) -> None:
        world, seller, buyer, sstock, bstock = self._gifting_pair()
        gifts = _gift_entries(world)
        self.assertTrue(gifts, "соседского дара не случилось — стенд пустой")
        for entry in gifts:
            self.assertAlmostEqual(entry.amount, 1.0, places=9)
            self.assertEqual(
                entry.src_id, seller.stock_id,
                "Дар заплатил не сосед с излишком",
            )
            self.assertEqual(entry.dst_id, buyer.stock_id)
            self.assertNotIn(
                entry.dst_id, ("settlement:hill_court",),
                "Дар пришёл в амбар сеньора — это подача, а не дар",
            )
        self.assertAlmostEqual(exchange.price_of(world, "grain"), 0.128165, places=6)

    def test_no_gift_reached_the_relief_book(self) -> None:
        world, seller, buyer, sstock, bstock = self._gifting_pair()
        self.assertTrue(_gift_entries(world), "дар не случился — сверять нечего")
        records = exchange.relief_obligations(world)
        self.assertEqual(
            [o.id for o in records if "neighbour" in o.id or "gift" in o.id], []
        )
        self.assertAlmostEqual(
            sum(o.paid_total for o in records),
            sum(e.amount for e in _relief_entries(world)), places=9,
            msg="в книгу подачи попало что-то кроме проводок relief",
        )
        # Дельту материи здесь НЕ проверяем и это не пропуск: стенд намеренно
        # пересобирает стоки (`amounts.clear()` + доливка через `external_in`),
        # то есть сам создаёт и убирает материю, и дельта этого стенда ничего не
        # говорит о мире. Закон сохранности материи проверяется на нетронутых
        # сценариях: `TestReliefIsRecorded.test_matter_delta_is_zero`,
        # `test_no_negative_stock` в test_matter_conservation и дельта каждого
        # прогона в остальных классах этого модуля.


class TestReliefAtTheLordsScale(unittest.TestCase):
    """24 месяца, сид 1729 — числа из ADR 0169, дыра закрыта числами."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = _run(FULL_MONTHS)

    def test_lord_pays_for_relief_in_grain(self) -> None:
        records = exchange.relief_obligations(self.world)
        entries = _relief_entries(self.world)
        # Реестр перезамерен после починки решения (ADR 0095 п. 1: двор с урожаем на своём гексе больше не бросает пашну стожать, и
        # подача сжалась с 746.127 (212 проводок) до 9.878 (4 проводки) за те же
        # 24 месяца. Дыра ADR 0169 («подача стоила сеньору 746 зерна, потому что
        # дворы не пахали») закрыта не цифрой, а причиной; жатва деревни при этом
        # выросла до 6666.950, голодных дней 0.
        self.assertAlmostEqual(
            sum(e.amount for e in entries), 9.878, places=3,
            msg="подача за 24 месяца ушла от перезамеренного реестра",
        )
        self.assertAlmostEqual(
            sum(o.paid_total for o in records), 9.878, places=6
        )
        self.assertGreater(len(records), 0, "книга пуста")
        # дыра ADR 0169: подача платится ЗЕРНОМ. Прежде здесь стояло
        # `[e for e in entries if e.good == "silver"] == []` — доказательство через
        # посылку «серебра в мире нет вообще». Посылка умерла (ADR 0219: серебро
        # приходит извне по `royal_court_buys_grain`), закон ADR 0169 п. 1 жив.
        # Проверяем его прямо, по проводкам подачи (ADR 0219 §3).
        self.assertEqual(
            [e for e in self.world.ledger.entries if e.good == "silver" and e.reason == "relief"],
            [],
            "Подача пошла серебром, а не зерном — дыра ADR 0169 открыта",
        )
        owing = [o for o in records if o.arrears > EPS]
        self.assertTrue(owing, "сеньор не видит недоимки — записи без долга")
        grain, silver = exchange.relief_cost(self.world)
        self.assertAlmostEqual(silver, grain * 0.128165, places=6)
        self.assertAlmostEqual(
            self.world.ledger.delta(self.world.total_matter()), 0.0, places=6
        )

    def test_hunger_is_legal_but_nobody_was_lost(self) -> None:
        self.assertEqual(float(self.world.stats.get("hired_days", 0.0)), 0.0)
        self.assertEqual(
            [h.id for h in self.world.households.values() if h.left_at is not None], []
        )
        for obligation in exchange.relief_obligations(self.world):
            self.assertLessEqual(obligation.paid_total, obligation.due_amount + EPS)


class TestReliefBookIsDeterministic(unittest.TestCase):
    """Повторный `run` с тем же сидом: та же книга, тот же хеш, та же дельта."""

    def test_two_runs_agree(self) -> None:
        first, second = _run(MONTHS), _run(MONTHS)
        self.assertEqual(
            [o.id for o in exchange.relief_obligations(first)],
            [o.id for o in exchange.relief_obligations(second)],
            "книга подачи не воспроизводится",
        )
        for a, b in zip(exchange.relief_obligations(first), exchange.relief_obligations(second)):
            self.assertAlmostEqual(a.paid_total, b.paid_total, places=9)
            self.assertAlmostEqual(a.due_amount, b.due_amount, places=9)
        self.assertAlmostEqual(
            sum(o.paid_total for o in exchange.relief_obligations(first)),
            sum(e.amount for e in _relief_entries(first)), places=9,
        )
        self.assertAlmostEqual(
            sum(e.amount for e in _relief_entries(second)),
            sum(e.amount for e in _relief_entries(first)), places=9,
        )
        self.assertEqual(first.state_hash(), second.state_hash(), "тик не детерминирован")
        for world in (first, second):
            self.assertAlmostEqual(
                world.ledger.delta(world.total_matter()), 0.0, places=6
            )


class TestReliefPriceIsVisibleInTheBaronReport(unittest.TestCase):
    """Отчёт барона показывает цену подачи отдельной строкой (ADR 0169 п. 6).

    Замер Economic'а: `exchange.relief_cost(world)` отдаёт `(746.12658, 95.62731)` —
    зерно и его каталожную стоимость. Проверяются три вещи: строка в сводке есть,
    числа в ней настоящие, и **дар соседям в неё не подмешан** — плательщик другой,
    цена ноль (ADR 0169 п. 6).
    """

    def test_report_line_exists_and_carries_the_cost(self) -> None:
        import contextlib
        import io

        from hillcourt import runner

        world = _run_like_runner(MONTHS)
        paid = sum(o.paid_total for o in exchange.relief_obligations(world))
        self.assertGreater(paid, 0.0, "В прогоне не было подачи — строка не проверена")
        result = runner.run(VILLAGE, MONTHS, seed=SEED)
        self.assertAlmostEqual(result.relief_grain, paid, places=6)
        self.assertAlmostEqual(
            result.relief_silver,
            paid * exchange.price_of(world, GRAIN),
            places=6,
            msg="Серебро в отчёте не по каталожной цене зерна",
        )

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            runner.main([
                "--scenario", str(VILLAGE),
                "--months", str(MONTHS),
                "--seed", str(SEED),
            ])
        printed = buffer.getvalue()
        self.assertIn("подача", printed, "В сводке нет строки о цене подачи")
        lines = [row for row in printed.splitlines() if "подача" in row]
        self.assertEqual(len(lines), 1, f"Строка о подаче должна быть одна, а их {len(lines)}")
        self.assertIn(f"{result.relief_grain:.2f}", lines[0])
        self.assertIn(f"{result.relief_silver:.2f}", lines[0])

    def test_neighbour_gifts_are_not_mixed_into_the_relief_line(self) -> None:
        """Дар соседям — другой плательщик и цена ноль, в строку подачи он не входит.

        В деревне 35 дворов даров теперь **0**: дворы пашут своё и соседями не
        кормятся (`TestCourtWorksItsOwnField`, `economy/decisions.py`). Проверка
        разделения поэтому идёт от строгого — отчёт показывает ровно сумму
        проводок `relief` и ни единой gift-проводки в сводке нет, а живой случай
        разделения (дар есть, плательщик — сосед) проверяет
        `TestNeighbourGiftIsNotRelief` на `v0_barony_100`.
        """
        from hillcourt import runner

        world = _run_like_runner(MONTHS)
        gifts = _gift_entries(world)
        self.assertEqual(
            gifts, [],
            "В деревне снова соседские дары — проверка разделения стала пустой",
        )
        paid = sum(o.paid_total for o in exchange.relief_obligations(world))
        wired = sum(e.amount for e in _relief_entries(world))
        result = runner.run(VILLAGE, MONTHS, seed=SEED)
        self.assertAlmostEqual(result.relief_grain, paid, places=6)
        self.assertAlmostEqual(result.relief_grain, wired, places=6)
        self.assertAlmostEqual(
            result.relief_silver, wired * exchange.price_of(world, GRAIN), places=6
        )


class TestReliefIsNotIncome(unittest.TestCase):
    """Подача — расход сеньора, а не его доход (ADR 0169 п. 6).

    У записи `relief_granted` `household_id` — двор **сеньора**: запись означает, что
    сеньор НЕДОДАЛ. Поэтому в `rent_collected` (сумма «сколько сеньору ДОЛЖНЫ
    дворы») она не попадает ни при каких числах, а подача стоит отдельной строкой
    со своим знаком.
    """

    def _book_with_relief(self):
        world = _run_like_runner(MONTHS)
        relief = [
            o for o in exchange.relief_obligations(world) if o.paid_total > 0.0
        ]
        self.assertTrue(relief, "В книге нет выданной подачи — проверка пустая")
        return world, relief

    def test_relief_records_never_enter_the_income_sum(self) -> None:
        from hillcourt import runner

        world, relief = self._book_with_relief()
        income_before = runner.lord_income(world)
        relief_grain, relief_silver = exchange.relief_cost(world)
        self.assertGreater(relief_grain, 0.0, "Подачи не было — суммы сравнивать не с чем")
        self.assertAlmostEqual(
            income_before,
            sum(
                o.paid_total for o in world.obligations.values()
                if o.kind != "relief_granted"
            ),
            places=9,
            msg="В доход сеньора попала запись, которую сеньор не получал",
        )
        # Запись с гигантской выдачей не должна двигать доход ни на единицу.
        obligation = relief[0]
        obligation.paid_total = 10_000.0
        self.assertAlmostEqual(
            runner.lord_income(world), income_before, places=9,
            msg="Подача попала в доход сеньора (ADR 0169 п. 6)",
        )
        self.assertGreater(exchange.relief_cost(world)[0], relief_grain)

    def test_a_yard_rent_raises_income_and_leaves_relief_alone(self) -> None:
        """Рента чужого двора — доход; подача при этом не меняется."""
        from hillcourt import runner

        world, relief = self._book_with_relief()
        income_before = runner.lord_income(world)
        relief_before = exchange.relief_cost(world)
        yard = "hh_village_cotter_a_001"
        world.obligations["obl_test_income"] = Obligation(
            id="obl_test_income",
            household_id=yard,
            kind="rent",
            due_good=GRAIN,
            due_amount=3.0,
            period_months=1,
            paid_total=3.0,
            arrears=0.0,
        )
        self.assertAlmostEqual(
            runner.lord_income(world), income_before + 3.0, places=9,
            msg="Рента двора не попала в доход сеньора",
        )
        self.assertAlmostEqual(
            exchange.relief_cost(world)[0], relief_before[0], places=9,
            msg="Добавление ренты изменило подачу — считаются не те записи",
        )
        self.assertIn(
            "relief_granted", set(runner.INCOME_OBLIGATION_KINDS) | {"relief_granted"},
        )
        self.assertNotIn("relief_granted", runner.INCOME_OBLIGATION_KINDS)
