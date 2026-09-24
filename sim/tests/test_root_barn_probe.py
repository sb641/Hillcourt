"""Зонд G2 на КОРНЕ: корневой держатель и полный амбар корня.

Гипотеза (ADR 0016 дыры 2-3, ADR 0019 ограничение 2): двор игрока `hh_court`
(книга `manor_hill` = `settlement:hill_court`) может голодать при полном амбаре
корня или есть не из своей книги. Проверки:

  (a) полный амбар корня + собственный сток `hh_court` = 0 → весь `board` в
      `household:hh_court` идёт из `settlement:hill_court` (`root Manor.stock_id`),
      `hunger_days == 0`, двора не уводит (zombie нет);
  (b) изоляция книги: корень пуст, чужой (тэн) амбар полон → корневой двор НЕ
      получает board/relief из чужой книги и честно голоден;
  (c) порча-до-еды: board = нужно × буфер (порча 1 %), после `spoil` еды хватает,
      `hunger == 0`;
  (d) дельта мира ≈ 0 (с учётом `external_in`); replay: два прогона одного сида —
      одинаковый `state_hash`.

Фикстура — `v0_hill_and_salt` (seed 1729). Материю добавляем только через
`ledger.external_in`, чтобы дельта осталась честной.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy import exchange
from hillcourt.economy import manor as economy_manor
from hillcourt.economy.manor import _spoil_buffer
from hillcourt.economy.needs import monthly_food_need
from hillcourt.engine.manor import grant_thegn, root_manor
from hillcourt.engine.tick import phase_consume, run_month
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
CASTLE = "settlement:hill_court"
WASTE = "sink:waste"
ROOT_MANOR = "manor_hill"
HOLDER = "hh_court"
THEGN_PERSON = "hh_retinue_p1"
EPSILON = 1e-9


class TestRootBarnProbe(unittest.TestCase):
    """Стол корневого держателя — замок; чужой амбар его не кормит."""

    def _drain_grain(self, world, stock, reason: str = "test_setup") -> None:
        """Убрать зерно стока переводом (материя едет, не исчезает)."""
        grain = stock.amounts.get("grain", 0.0)
        if grain > EPSILON:
            world.ledger.transfer(
                stock, world.get_stock(WASTE), "grain", grain, reason, world.clock.date
            )
        self.assertAlmostEqual(stock.amounts.get("grain", 0.0), 0.0, places=6)

    def _fill_barn(self, world, stock, target: float) -> None:
        """Наполнить амбар зерном извне (честный external_in)."""
        grain = stock.amounts.get("grain", 0.0)
        if grain < target:
            world.ledger.external_in(
                stock, "grain", target - grain, "test_setup", None, world.clock.date
            )
        self.assertAlmostEqual(stock.amounts.get("grain", 0.0), target, places=6)

    def _quiet_actions(self, world) -> None:
        for household in world.households.values():
            household.main_action, household.minor_action = "idle_repair", "idle_repair"

    def test_full_root_barn_feeds_root_holder_from_own_book(self) -> None:
        """(a) Полный амбар корня кормит hh_court из своей книги, hunger 0."""
        world = load_scenario(SCENARIO)
        root = root_manor(world)
        self.assertEqual(root.id, ROOT_MANOR)
        self.assertEqual(root.stock_id, CASTLE, "Амбар корня — не замок")
        holder = world.households[HOLDER]
        self.assertEqual(holder.manor_id, root.id)

        # Собственный сток держателя — 0; в замке зерно с запасом (external_in).
        self._drain_grain(world, world.get_stock(holder.stock_id))
        self._fill_barn(world, world.get_stock(CASTLE), 500.0)
        holder.hunger_days = 0

        packs_before = len(world.packs)
        n0 = len(world.ledger.entries)
        run_month(world)

        board = [
            entry
            for entry in world.ledger.entries[n0:]
            if entry.reason == "board" and entry.dst_id == holder.stock_id
        ]
        self.assertTrue(board, "Корневой держатель не получил пайка")
        self.assertTrue(
            all(entry.src_id == CASTLE for entry in board),
            "Паёк корневого держателя пришёл не из settlement:hill_court",
        )
        self.assertGreater(board[-1].amount, 4.0, "Паёк выдан без покрытия двора")
        self.assertEqual(holder.hunger_days, 0, "Голод при полном амбаре корня")
        self.assertIsNone(holder.left_at, "Двор держателя ушёл при голоде 0")
        self.assertEqual(len(world.packs), packs_before, "Голод-0 поднял воз ухода")

    def test_foreign_full_barn_does_not_feed_root_holder(self) -> None:
        """(b) Чужой (тэн) амбар полон, корень пуст — двор честно голоден."""
        world = load_scenario(SCENARIO)
        thegn = grant_thegn(world, THEGN_PERSON, ["t_03_01", "t_05_02"], ["hh_02"])
        self.assertIsNotNone(thegn)
        holder = world.households[HOLDER]
        thegn_holder = world.households["hh_retinue"]
        self.assertEqual(holder.manor_id, ROOT_MANOR)
        self.assertEqual(thegn_holder.manor_id, thegn.id)

        # Корень (своя книга) и собственные стоки держателей — пусты,
        # чужой амбар тэна — полон.
        self._drain_grain(world, world.get_stock(CASTLE))
        self._drain_grain(world, world.get_stock(holder.stock_id))
        self._drain_grain(world, world.get_stock(thegn_holder.stock_id))
        self._fill_barn(world, world.get_stock(thegn.stock_id), 100.0)
        self._quiet_actions(world)

        # Паёк: корневой двор не берёт из чужого амбара (своя книга пуста).
        n0 = len(world.ledger.entries)
        economy_manor._board(world, world.clock.date)
        board = [
            entry
            for entry in world.ledger.entries[n0:]
            if entry.reason == "board" and entry.dst_id == holder.stock_id
        ]
        self.assertEqual(board, [], "Корневой двор взял паёк из чужой книги")
        # Контроль: чужой амбар не пуст — держатель тэна из него накормлен.
        thegn_board = [
            entry
            for entry in world.ledger.entries[n0:]
            if entry.reason == "board" and entry.dst_id == thegn_holder.stock_id
        ]
        self.assertTrue(thegn_board, "Полный амбар тэна никого не накормил")
        self.assertTrue(
            all(entry.src_id == thegn.stock_id for entry in thegn_board),
            "Паёк держателя тэна пришёл не из manor:<id>",
        )

        # Подмога: корневой двор просит, но своя книга пуста — relief нет.
        holder.main_action = "request_relief"
        n0 = len(world.ledger.entries)
        exchange.apply_relief(world, world.clock.date)
        relief = [
            entry
            for entry in world.ledger.entries[n0:]
            if entry.reason == "relief" and entry.dst_id == holder.stock_id
        ]
        self.assertEqual(relief, [], "Корневой двор получил relief из чужой книги")
        self.assertFalse(
            any(
                entry.src_id == thegn.stock_id and entry.dst_id == holder.stock_id
                for entry in world.ledger.entries[n0:]
            ),
            "Из чужого амбара тэна ушло что-то корневому двору",
        )

        # Голод честен: своя книга пуста, чужой амбар двор не кормит.
        phase_consume(world)
        self.assertGreater(holder.hunger_days, 0, "Двор сыт при пустой своей книге")
        self.assertLess(thegn_holder.hunger_days, 1, "Контрольный двор тэна голоден")
        self.assertIsNone(holder.left_at, "Голод 1 не уводит двор")

    def test_spoil_before_eat_board_covers_household(self) -> None:
        """(c) Паёк = нужно × буфер; после порчи еды хватает, hunger 0."""
        world = load_scenario(SCENARIO)
        holder = world.households[HOLDER]
        # Дрену собственный сток и наполним корень.
        self._drain_grain(world, world.get_stock(holder.stock_id))
        self._fill_barn(world, world.get_stock(CASTLE), 500.0)
        holder.hunger_days = 0

        buffer = _spoil_buffer(world)
        self.assertGreater(buffer, 1.0, "Буфер против порчи не поднят")
        expected = monthly_food_need(world, holder) * buffer

        n0 = len(world.ledger.entries)
        run_month(world)
        board = [
            entry
            for entry in world.ledger.entries[n0:]
            if entry.reason == "board" and entry.dst_id == holder.stock_id
        ]
        self.assertTrue(board)
        self.assertAlmostEqual(
            board[-1].amount, expected, places=6,
            msg="Паёк не покрывает нужду двора с буфером на порчу",
        )
        self.assertEqual(holder.hunger_days, 0, "Порча съела паёк")

    def test_matter_delta_zero_and_replay(self) -> None:
        """(d) Дельта мира ≈ 0 с учётом external_in; два прогона — один hash."""
        hashes: list[str] = []
        deltas: list[float] = []
        for _ in range(2):
            world = load_scenario(SCENARIO)
            holder = world.households[HOLDER]
            self._drain_grain(world, world.get_stock(holder.stock_id))
            self._fill_barn(world, world.get_stock(CASTLE), 500.0)
            holder.hunger_days = 0
            run_month(world)
            delta = world.ledger.delta(world.total_matter())
            self.assertAlmostEqual(delta, 0.0, places=6, msg="Мир создал/сжёг материю")
            deltas.append(delta)
            hashes.append(world.state_hash())
        self.assertEqual(hashes[0], hashes[1], "Replay дал другой state_hash")
        self.assertAlmostEqual(deltas[0], deltas[1], places=12)


if __name__ == "__main__":
    unittest.main()
