"""G2: держатель не голодает при полном Manor.stock; relief — из своей книги.

Проверки (Economist + Legal, ADR 0013 дыры 1–3):
  1. полный амбар тэна кормит двор держателя, `phase_spoil` не съедает паёк
     (буфер на порчу), `mustered` — по своему двору; дельта материи 0;
  2. `apply_relief` берёт зерно из амбара СВОЕЙ книги: корень — из замка,
     тэн — из `manor:<id>`, двор без книги (соляной) подмоги не получает;
  3. 36 месяцев с пожалованием: голодных месяцев меньше половины окна, но
     полного нуля нет — амбар тэна беден (это отдельная дыра Economist).
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy import exchange
from hillcourt.engine.manor import grant_thegn, root_manor
from hillcourt.engine.tick import run_month
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
THEGN_PERSON = "hh_retinue_p1"
DEMESNE_TILE = "t_05_02"
FS_TILE = "t_03_01"
CASTLE = "settlement:hill_court"


class TestThegnFullBarn(unittest.TestCase):
    """Стол держателя — амбар тэна; рельеф идёт по книге двора."""

    def _grant_at_month_six(self):
        """Прогнать до месяца 6 и пожаловать тэна (прямой вызов)."""
        world = load_scenario(SCENARIO)
        for _ in range(5):
            run_month(world)
        self.assertEqual(world.clock.month, 6)
        grant_date = world.clock.date
        thegn = grant_thegn(world, THEGN_PERSON, [FS_TILE, DEMESNE_TILE], ["hh_02"])
        self.assertIsNotNone(thegn)
        holder = world.households[world.persons[THEGN_PERSON].household_id]
        self.assertEqual(holder.manor_id, thegn.id)
        self.assertIn(holder.id, thegn.household_ids)
        return world, thegn, holder, grant_date

    def test_holder_fed_from_own_barn_when_full(self) -> None:
        world, thegn, holder, _ = self._grant_at_month_six()
        holder_stock = world.get_stock(holder.stock_id)
        barn = world.get_stock(thegn.stock_id)
        castle = world.get_stock(CASTLE)

        # Собственный сток держателя пуст, амбар тэна полон. Материю не
        # создаём: зерно двора уезжает в замок, 100 в амбар — внешний приход.
        holder_grain = holder_stock.amounts.get("grain", 0.0)
        if holder_grain > 0:
            world.ledger.transfer(
                holder_stock, castle, "grain", holder_grain, "test_setup",
                world.clock.date,
            )
        existing = barn.amounts.get("grain", 0.0)
        world.ledger.external_in(
            barn, "grain", 100.0 - existing, "test_setup", None, world.clock.date
        )
        # Комплект на держателе — внешним приходом (не из воздуха в дельте:
        # external_in учтён в балансе), иначе mustered ложен без железа.
        world.ledger.external_in(
            holder_stock, "war_kit", 1.0, "test_setup", None, world.clock.date
        )
        holder.hunger_days = 0
        self.assertEqual(world.get_stock(holder.stock_id).amounts.get("grain", 0.0), 0.0)
        self.assertAlmostEqual(barn.amounts.get("grain", 0.0), 100.0, places=6)

        n0 = len(world.ledger.entries)
        delta_before = world.ledger.delta(world.total_matter())
        run_month(world)

        board = [
            entry
            for entry in world.ledger.entries[n0:]
            if entry.reason == "board" and entry.dst_id == holder.stock_id
        ]
        self.assertTrue(board, "Держатель не получил пайка из своего амбара")
        self.assertTrue(
            all(entry.src_id == thegn.stock_id for entry in board),
            "Паёк держателя пришёл не из амбара тэна",
        )
        self.assertFalse(
            any(entry.src_id == CASTLE for entry in board),
            "Паёк держателя тёк из замка холма",
        )
        # Буфер на порчу (1% зерна): пайка хватает после `phase_spoil`.
        self.assertGreater(
            board[-1].amount, 3.0, "Паёк выдан без буфера на порчу"
        )
        self.assertEqual(
            holder.hunger_days, 0, "Держатель голоден при полном амбаре тэна"
        )
        self.assertTrue(thegn.mustered, "mustered не по своему амбару/двору")
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), delta_before, places=6,
            msg="Месяц с полным амбаром создал или сжёг материю",
        )

    def test_relief_comes_from_own_book_only(self) -> None:
        world, thegn, holder, _ = self._grant_at_month_six()
        castle = world.get_stock(CASTLE)
        barn = world.get_stock(thegn.stock_id)
        salt = world.households["hh_salt_01"]
        root_hh = world.households["hh_01"]
        for household in world.households.values():
            household.main_action, household.minor_action = "idle_repair", "idle_repair"

        self.assertIsNone(salt.manor_id, "Соляной двор не должен числиться в книге")
        self.assertIsNone(root_manor(world).parent_manor_id)

        def _relief(hid: str) -> list:
            return [
                entry
                for entry in world.ledger.entries
                if entry.reason == "relief" and entry.dst_id == hid
            ]

        # a) Замок полон, амбар тэна пуст: замок держателю не источник.
        castle.amounts["grain"] = 999.0
        barn.amounts["grain"] = 0.0
        holder.main_action = "request_relief"
        n0 = len(world.ledger.entries)
        exchange.apply_relief(world, world.clock.date)
        relief = [
            entry
            for entry in world.ledger.entries[n0:]
            if entry.reason == "relief" and entry.dst_id == holder.stock_id
        ]
        self.assertEqual(relief, [], "Замок выдал relief двору тэна")
        self.assertEqual(_relief(holder.stock_id), [])

        # b) Амбар тэна ≥ min: relief идёт из своей книги.
        barn.amounts["grain"] = 10.0
        n0 = len(world.ledger.entries)
        exchange.apply_relief(world, world.clock.date)
        relief = [
            entry
            for entry in world.ledger.entries[n0:]
            if entry.reason == "relief" and entry.dst_id == holder.stock_id
        ]
        self.assertTrue(relief, "Двор тэна не получил relief из своего амбара")
        self.assertTrue(
            all(entry.src_id == thegn.stock_id for entry in relief),
            "Relief двору тэна пришёл не из manor:<id>",
        )

        # c) Соляной двор (manor_id None) — равный вне книги: relief нет.
        salt.main_action = "request_relief"
        n0 = len(world.ledger.entries)
        exchange.apply_relief(world, world.clock.date)
        self.assertEqual(_relief(salt.stock_id), [], "Соляной двор получил relief")

        # d) Корневой двор — из замка, как раньше.
        root_hh.main_action = "request_relief"
        n0 = len(world.ledger.entries)
        exchange.apply_relief(world, world.clock.date)
        relief = [
            entry
            for entry in world.ledger.entries[n0:]
            if entry.reason == "relief" and entry.dst_id == root_hh.stock_id
        ]
        self.assertTrue(relief, "Корневой двор не получил relief из замка")
        self.assertTrue(
            all(entry.src_id == CASTLE for entry in relief),
            "Relief корневого двора пришёл не из замка",
        )

    def test_thirty_six_months_hunger_and_muster(self) -> None:
        world, thegn, holder, grant_date = self._grant_at_month_six()
        # Два комплекта в амбар внешним приходом: выдача на держателя идёт
        # механикой фьефа, mustered читает свой двор и свой комплект.
        world.ledger.external_in(
            world.get_stock(thegn.stock_id), "war_kit", 2.0,
            "test_setup", None, world.clock.date,
        )
        hungry_months = 0
        mustered_false = 0
        for _ in range(31):
            run_month(world)
            if holder.hunger_days > 0:
                hungry_months += 1
            if not thegn.mustered:
                mustered_false += 1

        # Амбар тэна беден (~1.7 зерна/мес против нужды 3/мес), поэтому голод
        # не исчезает, но окно должно быть заметно лучше прежнего 18/30.
        self.assertLess(
            hungry_months, 18,
            f"Голодных месяцев держателя слишком много: {hungry_months}/31",
        )
        self.assertLess(
            mustered_false, 18,
            "mustered заморожен и не читает свой двор: "
            f"{mustered_false}/31 месяцев False",
        )
        self.assertTrue(
            mustered_false > 0,
            "mustered ни разу не падал у бедного амбара — проверка слепа",
        )

        board = [
            entry
            for entry in world.ledger.entries
            if entry.reason == "board"
            and entry.dst_id == holder.stock_id
            and entry.date > grant_date
        ]
        self.assertTrue(board, "После пожалования не было пайка из амбара")
        self.assertTrue(
            all(entry.src_id == thegn.stock_id for entry in board),
            "Пайки держателя после пожалования шли не из амбара тэна",
        )
        self.assertFalse(
            any(entry.src_id == CASTLE for entry in board),
            "Пайки держателя после пожалования текли из замка",
        )
        if holder.left_at is None:
            self.assertLess(
                holder.hunger_days, 3, "Двор держателя уходит только при голоде 3"
            )


if __name__ == "__main__":
    unittest.main()
