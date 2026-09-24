"""Проверка манора: трудодень, барщина, домен, гэфоль, паёк, найм.

Проверки стола (задача Economist):
  1. трудодень не создаётся из статуса — равен числу взрослых;
  2. барщина снимает руки с надела (доля месяца), а не просто счётчик;
  3. гэфоль переводит материю в замок, не телепортирует;
  4. раб ест паёк лорда и работает на домене;
  5. домен сам не пашется: без труда пула урожая нет;
  6. найм free_landless — за еду/пенс, только при недоборе;
  7. сезонный зубец: домен собирает урожай в жатву, материя сходится.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy import manor
from hillcourt.economy.needs import monthly_food_need
from hillcourt.engine.manor import grant_thegn, nested_manors, root_manor
from hillcourt.engine.tick import run_month
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
EPSILON = 1e-9


class TestManorEconomy(unittest.TestCase):
    """Домен и надел не смешивают стоки времени; материя едет, не берётся."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO)

    def test_labor_day_not_created_from_status(self) -> None:
        world = self.world
        household = world.households["hh_02"]
        world.households = {"hh_02": household}
        household.member_ids = []  # нет взрослых — нет рук
        manor._replenish_labor(world)
        self.assertEqual(household.labor_days, 0.0, "Трудодень взят из статуса")
        self.assertEqual(manor._render_pool(world), 0.0)

    def test_corvee_removes_hands_from_plot(self) -> None:
        world = self.world
        household = world.households["hh_02"]
        world.households = {"hh_02": household}
        world.clock.month = 4  # пахота: барщина виллана 3 дня/нед = 12 дней
        manor._replenish_labor(world)
        before = household.labor_days
        pool = manor._render_pool(world)
        self.assertGreater(pool, 0.0, "Барщина не взяла рук")
        self.assertAlmostEqual(household.labor_days, before - pool, places=6)
        self.assertLess(household.labor_days, before, "Руки не сняты с надела")

    def test_gafol_moves_matter_to_castle(self) -> None:
        world = self.world
        household = world.households["hh_02"]
        world.households = {"hh_02": household}
        world.clock.month = manor.MARTINMAS_MONTH
        stock = world.get_stock(household.stock_id)
        stock.amounts["grain"] = 20.0
        castle = world.get_stock("settlement:hill_court")
        before_castle = castle.amounts.get("grain", 0.0)
        before_total = world.total_matter()

        manor._gafol(world, world.clock.date)

        self.assertLess(stock.amounts["grain"], 20.0, "Гэфоль не списан")
        self.assertGreater(castle.amounts.get("grain", 0.0), before_castle)
        self.assertAlmostEqual(
            world.total_matter(), before_total, places=6, msg="Гэфоль создал материю"
        )

    def test_slave_eats_board_and_works_demesne(self) -> None:
        world = self.world
        # hh_court — двор лорда с двумя рабами (personal_status slave)
        court = world.households["hh_court"]
        others = {
            hid: hh
            for hid, hh in world.households.items()
            if hid != "hh_court"
        }
        world.households = {"hh_court": court, **others}
        castle = world.get_stock("settlement:hill_court")
        castle.amounts["grain"] = 100.0
        court_stock = world.get_stock(court.stock_id)
        before_board = court_stock.amounts.get("grain", 0.0)
        before_slave = world.stats.get("slave_labor_days", 0.0)

        manor.manor_month(world, world.clock.date)

        self.assertGreater(
            court_stock.amounts.get("grain", 0.0),
            before_board,
            "Раб не получил пайка из замка",
        )
        self.assertGreater(
            world.stats.get("slave_labor_days", 0.0),
            before_slave,
            "Раб ест, но не работает на домене",
        )

    def test_demesne_does_not_plough_itself(self) -> None:
        world = self.world
        castle = world.get_stock("settlement:hill_court")
        castle.amounts["grain"] = 0.0
        before = castle.amounts.get("grain", 0.0)
        for book in world.manors.values():
            book.demesne_labor_filled = 0.0
            book.demesne_labor_demand_this_month = 999.0
        worked = manor._work_demesne(world, world.clock.date)
        self.assertEqual(sum(worked.values()), 0.0, "Домен вспахался без трудодней")
        self.assertAlmostEqual(castle.amounts.get("grain", 0.0), before, places=6)

    def test_hire_pays_and_adds_labor(self) -> None:
        world = self.world
        # Оставляем только безземельного и лорда, чтобы домен голодал по рукам.
        landless = world.households["hh_06"]
        court = world.households["hh_court"]
        world.households = {"hh_06": landless, "hh_court": court}
        for hid in ("hh_06", "hh_court"):
            world.households[hid].member_ids = [
                pid
                for pid in world.households[hid].member_ids
                if (p := world.persons.get(pid)) is not None
                and p.personal_status != "slave"
            ]
        castle = world.get_stock("settlement:hill_court")
        castle.amounts["grain"] = 100.0
        world.clock.month = 8  # жатва: hire_demand high
        before_hired = world.stats.get("hired_days", 0.0)
        before_stock = world.get_stock(landless.stock_id).amounts.get("grain", 0.0)
        before_castle = castle.amounts.get("grain", 0.0)

        manor.manor_month(world, world.clock.date)

        self.assertGreater(
            world.stats.get("hired_days", 0.0), before_hired, "Найма не было"
        )
        self.assertGreater(
            world.get_stock(landless.stock_id).amounts.get("grain", 0.0),
            before_stock,
            "Наём не заплатил",
        )
        self.assertLess(castle.amounts.get("grain", 0.0), before_castle)

    def test_sow_moves_grain_to_demesne_field(self) -> None:
        world = self.world
        household = world.households["hh_02"]
        world.households = {"hh_02": household}
        world.clock.month = manor.SOW_MONTHS[0]
        stock = world.get_stock(household.stock_id)
        stock.amounts["grain"] = 20.0
        field = manor.demesne_field_tiles(world)[0]
        standing = world.get_stock(field.standing_stock_id)
        before_standing = standing.amounts.get("grain", 0.0)
        before_total = world.total_matter()

        manor._sow_demesne(world, world.clock.date)

        self.assertLess(stock.amounts["grain"], 20.0, "Посев не списал зерно")
        self.assertGreater(
            standing.amounts.get("grain", 0.0),
            before_standing,
            "Зерно не доехало до поля домена",
        )
        self.assertAlmostEqual(
            world.total_matter(), before_total, places=6, msg="Посев создал материю"
        )

    def test_seasonal_tooth_and_matter(self) -> None:
        world = self.world
        villein = world.households["hh_02"]
        demesne_by_month: dict[int, float] = {}
        # Запас еды на душу (месяцев): абсолютное зерно врёт при растущем
        # населении (роды добавляют и рты, и руки) — давит именно голод.
        mouth_by_month: dict[int, float] = {}
        for _ in range(36):
            month = world.clock.month
            grain_before = world.stats.get("demesne_grain", 0.0)
            run_month(world)
            demesne_by_month[month] = demesne_by_month.get(month, 0.0) + (
                world.stats.get("demesne_grain", 0.0) - grain_before
            )
            need = monthly_food_need(world, villein)
            store = world.get_stock(villein.stock_id).amounts.get("grain", 0.0)
            mouth_by_month[month] = mouth_by_month.get(month, 0.0) + (
                store / need if need > 0 else 0.0
            )
        harvest = demesne_by_month.get(8, 0.0) + demesne_by_month.get(9, 0.0)
        hay = demesne_by_month.get(6, 0.0) + demesne_by_month.get(7, 0.0)
        self.assertGreater(
            harvest, hay, "Урожай домена не собран в жатву — календарь не подключён"
        )
        self.assertLess(
            mouth_by_month[8],
            mouth_by_month[2],
            "В августе на душу худее февраля — барщина не снимает руки",
        )
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()),
            0.0,
            places=6,
            msg="Материя не сошлась (природа учтена в external_in)",
        )

    def _grant(self):
        """Пожаловать тэну доменную клетку и виллана hh_02."""
        world = load_scenario(SCENARIO)
        thegn = grant_thegn(world, "hh_retinue_p1", ["t_05_02"], ["hh_02"])
        self.assertIsNotNone(thegn)
        return world, thegn

    def test_granted_days_leave_root_and_enter_thegn(self) -> None:
        world, thegn = self._grant()
        root = root_manor(world)
        world.households = {"hh_02": world.households["hh_02"]}
        world.clock.month = 4
        manor._replenish_labor(world)
        root_pool = manor._render_pool(world)
        self.assertEqual(root_pool, 0.0, "Дни пожалованного двора остались в корне")
        self.assertAlmostEqual(root.demesne_labor_filled, 0.0, places=6)
        self.assertGreater(
            thegn.demesne_labor_filled, 0.0, "Дни не дошли до книги тэна"
        )

    def test_thegn_demesne_harvest_goes_to_thegn_stock(self) -> None:
        world, thegn = self._grant()
        root = root_manor(world)
        world.clock.month = 9
        tile = world.tiles["t_05_02"]
        world.get_stock(tile.standing_stock_id).amounts["grain"] = 40.0
        root.demesne_labor_filled = 0.0
        root.demesne_labor_demand_this_month = 0.0
        thegn.demesne_labor_filled = 60.0
        thegn.demesne_labor_demand_this_month = 60.0
        castle = world.get_stock("settlement:hill_court")
        castle_before = castle.amounts.get("grain", 0.0)
        thegn_stock = world.get_stock(thegn.stock_id)
        thegn_before = thegn_stock.amounts.get("grain", 0.0)

        manor._work_demesne(world, world.clock.date)

        self.assertGreater(
            thegn_stock.amounts.get("grain", 0.0),
            thegn_before,
            "Урожай домена тэна не попал в амбар тэна",
        )
        self.assertAlmostEqual(
            castle.amounts.get("grain", 0.0), castle_before, places=6,
            msg="Урожай тэна утёк в замок холма",
        )

    def test_granted_gafol_does_not_reach_root(self) -> None:
        world, thegn = self._grant()
        world.clock.month = manor.MARTINMAS_MONTH
        household = world.households["hh_02"]
        world.households = {"hh_02": household}
        world.get_stock(household.stock_id).amounts["grain"] = 10.0
        castle = world.get_stock("settlement:hill_court")
        castle_before = castle.amounts.get("grain", 0.0)
        thegn_stock = world.get_stock(thegn.stock_id)
        thegn_before = thegn_stock.amounts.get("grain", 0.0)

        manor._gafol(world, world.clock.date)

        self.assertAlmostEqual(
            castle.amounts.get("grain", 0.0), castle_before, places=6,
            msg="Гэфоль пожалованного двора дошёл до корня",
        )
        self.assertGreater(
            thegn_stock.amounts.get("grain", 0.0),
            thegn_before,
            "Гэфоль не дошёл до амбара тэна",
        )

    def test_thegn_holder_eats_from_own_barn(self) -> None:
        """Двор держателя числится у тэна и ест из его амбара, а не из замка.

        Контрпример Critic (0012, команда 4): замок пуст, амбар тэна полон —
        держатель сыт и mustered, потому что паёк идёт из амбара тэна.
        """
        world = load_scenario(SCENARIO)
        for _ in range(5):
            run_month(world)
        grant_date = world.clock.date
        thegn = grant_thegn(world, "hh_retinue_p1", ["t_05_02"], ["hh_02"])
        holder = world.households[world.persons["hh_retinue_p1"].household_id]
        self.assertEqual(holder.manor_id, thegn.id)
        self.assertIn(holder.id, thegn.household_ids)
        self.assertNotIn(holder.id, root_manor(world).household_ids)

        for _ in range(12):
            run_month(world)

        board = [
            entry
            for entry in world.ledger.entries
            if entry.reason == "board"
            and entry.dst_id == holder.stock_id
            and entry.date >= grant_date
        ]
        self.assertTrue(board, "Держательский двор не получал пайка из своего манора")
        self.assertTrue(
            all(entry.src_id == thegn.stock_id for entry in board),
            "Паёк держателя шёл не из амбара тэна",
        )
        self.assertFalse(
            any(entry.src_id == "settlement:hill_court" for entry in board),
            "Паёк держателя после пожалования тёк из замка холма",
        )

        # Замок пуст, амбар тэна полон: паёк из своего амбара держит двор сытым.
        world.get_stock("settlement:hill_court").amounts["grain"] = 0.0
        world.get_stock(thegn.stock_id).amounts["grain"] = 100.0
        world.get_stock(holder.stock_id).amounts["war_kit"] = 1.0
        holder.hunger_days = 0
        run_month(world)
        board_after = [
            entry
            for entry in world.ledger.entries
            if entry.reason == "board" and entry.dst_id == holder.stock_id
        ]
        self.assertGreater(
            len(board_after), len(board), "Амбар тэна не накормил держателя"
        )
        self.assertEqual(board_after[-1].src_id, thegn.stock_id)
        self.assertEqual(holder.hunger_days, 0, "Держатель голоден при полном амбаре")
        self.assertTrue(thegn.mustered, "mustered не по своему амбару, а по замку")

    def test_thegn_fed_or_hungry_by_month_18(self) -> None:
        world = load_scenario(SCENARIO)
        for _ in range(5):
            run_month(world)
        grant_date = world.clock.date
        thegn = grant_thegn(world, "hh_retinue_p1", ["t_05_02"], ["hh_02"])
        holder = world.households[world.persons["hh_retinue_p1"].household_id]
        for _ in range(13):
            run_month(world)

        self.assertEqual(holder.manor_id, thegn.id)
        board = [
            entry
            for entry in world.ledger.entries
            if entry.reason == "board"
            and entry.dst_id == holder.stock_id
            and entry.date >= grant_date
        ]
        self.assertFalse(
            any(entry.src_id == "settlement:hill_court" for entry in board),
            "Паёк держателя тёк из замка холма",
        )
        if holder.hunger_days == 0:
            self.assertTrue(
                board and all(entry.src_id == thegn.stock_id for entry in board),
                "Двор сыт, но пайка из амбара тэна не было",
            )
        # «Амбар тэна пуст» больше не прикрывается замком: если двор сыт,
        # источник пайка доказан выше. Остаток амбара на конец месяца не
        # гарантирован — паёк мог выбрать его (это не ложь, а расход).

    def test_root_corvee_pool_worse_after_grant(self) -> None:
        def corvee_at_harvest(grant: bool) -> float:
            world = load_scenario(SCENARIO)
            for _ in range(5):
                run_month(world)
            if grant:
                grant_thegn(world, "hh_retinue_p1", ["t_05_02"], ["hh_02"])
            for _ in range(31):
                run_month(world)
            entry = next(e for e in world.manor_log if e["date"] == "Y1-M08")
            return float(entry["corvee_pool"])

        self.assertLess(
            corvee_at_harvest(True),
            corvee_at_harvest(False),
            "После пожалования корневая барщина в жатву не уменьшилась",
        )


class TestSeptemberPeak(unittest.TestCase):
    """Допуск №2 (ADR 0040): сентябрьский пик своих рук.

    Сентябрь — месяц, когда двор снимает свой хлеб (0.85 стоячей, руки упираются
    в выросшее, а не в лень). Пик надела 1.60 при домене 1.30: тот же хлеб
    меньшей кровью — партии реже, руки свободны на сено/дрова/починку.
    Отклонённые варианты запинены: август 0.30 и зима 0.15 не тронуты (рост там
    рожает рты быстрее, чем кормит, — замер в ADR 0040).
    """

    def _sept_plot(self, world) -> float:
        for season in world.seasons.values():
            if season.month == 9:
                return float(season.plot_yield)
        raise AssertionError("Нет сентября в каталоге")

    def test_peak_pins_september_asymmetry(self) -> None:
        world = load_scenario(SCENARIO)
        demesne = next(
            float(s.demesne_yield) for s in world.seasons.values() if s.month == 9
        )
        self.assertAlmostEqual(self._sept_plot(world), 1.60, places=6)
        self.assertAlmostEqual(demesne, 1.30, places=6)
        august = next(
            float(s.plot_yield) for s in world.seasons.values() if s.month == 8
        )
        winter = next(
            float(s.plot_yield) for s in world.seasons.values() if s.month == 1
        )
        self.assertAlmostEqual(august, 0.30, places=6, msg="Август тронут без ADR")
        self.assertAlmostEqual(winter, 1.5e-1, places=6, msg="Зима тронута без ADR")

    def test_september_batch_feeds_more_hands_free(self) -> None:
        from hillcourt.economy.labor import apply_recipe

        world = load_scenario(SCENARIO)
        household = world.households["hh_02"]
        world.households = {"hh_02": household}
        stock = world.get_stock(household.stock_id)
        stock.amounts.clear()
        household.labor_days = 40.0
        tile = next(
            tile
            for tile in sorted(world.tiles.values(), key=lambda t: t.id)
            if tile.terrain == "field"
            and (world.catalogs.land_regimes.get(tile.regime_id) is not None)
            and world.catalogs.land_regimes[tile.regime_id].feeds_household
        )
        world.get_stock(tile.standing_stock_id).amounts["grain"] = 100.0
        recipe = world.catalogs.recipes["harvest_grain"]
        world.ledger.capture_initial(world.total_matter())
        apply_recipe(
            world, household, tile, recipe, 1.0, world.clock.date,
            yield_factor=self._sept_plot(world),
        )
        self.assertAlmostEqual(stock.amounts.get("grain", 0.0), 8.0, places=6,
                               msg="Сентябрьская партия дала не 5 × 1.6")
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
