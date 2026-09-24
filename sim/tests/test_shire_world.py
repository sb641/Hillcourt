"""D1, часть 2 «Шир»: четыре посёлка, 34 двора и ДВА пожалования тэна.

Основа — `v0_two_settlements`; нового движка нет: снизу две строки карты,
четыре хутора и четыре двора у ясеня. Проверяется, что второй тен — хозяйство
(свой амбар, свой домен, свой двор, двор держателя в книге), а не флаг, и что
его пожалованный двор платит гэфоль в `manor:<id>`, а не в замок.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.manor import manor_depth
from hillcourt.engine.tick import run_month
from hillcourt.runner import _apply_script_entry
from hillcourt.runner import run as run_scenario
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_shire.yml"

ROOT_MANOR = "manor_hill"
GRANT1 = {"manor": "manor_hh_retinue_p1", "holder_person": "hh_retinue_p1", "holder_hh": "hh_retinue"}
GRANT2 = {"manor": "manor_hh_03_p1", "holder_person": "hh_03_p1", "holder_hh": "hh_03"}
GRANT2_TENANT = "hh_05"
# Второе пожалование: поле корня t_06_02 и тяглый двор hh_08 добавлены из корня,
# чтобы второй тен кормился сам. Числа — предмет приёмки J4.
GRANT2_TILES = ["t_01_10", "t_04_10", "t_06_02"]
GRANT2_TENANTS = ["hh_05", "hh_08"]
# Baseline ADR 0016: держатель второго тэна голодал 39 месяцев из 60 (сид 1729).
GRANT2_BASELINE_HUNGER_MONTHS = 39


class TestShireWorld(unittest.TestCase):
    """Мир шира: два тэна, каждый со своим амбаром и доменом."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO)

    def _run_months(self, months: int):
        """Прогнать мир вручную, исполняя `script:` перед каждым месяцем."""
        world = load_scenario(SCENARIO, seed=1729)
        for month_index in range(1, months + 1):
            for entry in world.script:
                if int(entry.get("at_month", 0)) == month_index:
                    _apply_script_entry(world, entry)
            run_month(world)
        return world

    def _run_months_with_hunger(self, months: int, household_id: str, since_month: int):
        """Прогнать и посчитать месяцы голода двора с месяца `since_month`.

        Голодным считается месяц, где `hunger_days > 0` после тика. Для тэна
        счёт идёт с месяца пожалования: до него двор кормился из корня.
        """
        world = load_scenario(SCENARIO, seed=1729)
        hungry_months = 0
        for month_index in range(1, months + 1):
            for entry in world.script:
                if int(entry.get("at_month", 0)) == month_index:
                    _apply_script_entry(world, entry)
            run_month(world)
            if (
                month_index >= since_month
                and world.households[household_id].hunger_days > 0
            ):
                hungry_months += 1
        return world, hungry_months

    def _corvee_pool(self, world, date: str) -> float:
        """Пул барщины корня в месяце `date` из manor_log."""
        for entry in world.manor_log:
            if entry.get("date") == date and "corvee_pool" in entry:
                return float(entry["corvee_pool"])
        raise AssertionError(f"Нет corvee_pool за {date}")

    def test_world_loads_with_32_to_36_households(self) -> None:
        self.assertGreaterEqual(len(self.world.households), 32)
        self.assertLessEqual(len(self.world.households), 36)
        for sid in ("hill_court", "salt_village", "ash_village"):
            self.assertIn(sid, self.world.settlements, f"Нет поселения {sid}")
        self.assertGreaterEqual(len(self.world.settlements), 3)

    def test_script_has_two_grants(self) -> None:
        grants = [
            entry
            for entry in self.world.script
            if entry.get("action") == "grant_thegn"
        ]
        self.assertEqual(len(grants), 2, "ожидалось два пожалования тэна")
        self.assertEqual(
            sorted(int(g["at_month"]) for g in grants), [6, 18]
        )

    def test_both_thegns_are_households_after_24_months(self) -> None:
        world = self._run_months(24)
        for grant in (GRANT1, GRANT2):
            manor = world.manors.get(grant["manor"])
            self.assertIsNotNone(manor, f"нет манора {grant['manor']}")
            self.assertEqual(manor.parent_manor_id, ROOT_MANOR)
            self.assertEqual(manor_depth(world, manor), 1)
            self.assertTrue(manor.stock_id, "у тэна пустой stock_id")
            self.assertIn(manor.stock_id, world.stocks)
            self.assertEqual(world.stocks[manor.stock_id].owner_kind, "manor")
            demesne = [
                tid
                for tid in manor.tile_ids
                if world.tiles[tid].regime_id == "demesne"
            ]
            self.assertTrue(demesne, f"у {grant['manor']} нет доменной клетки")
            self.assertIn(grant["holder_hh"], manor.household_ids)
            self.assertEqual(
                world.households[grant["holder_hh"]].manor_id, grant["manor"]
            )
            self.assertTrue(manor.grant_ids, "пожалование не записано в книгу")

    def test_second_thegn_gets_root_demesne_and_extra_tenant(self) -> None:
        """Второй тен держит 3 клетки и 2 тяглых двора; доменов ≥ 2.

        Пожалование M18 забирает из корня последнее доменное поле `t_06_02`
        и двор `hh_08` — тэну есть что пахать и с кого брать гэфоль.
        """
        world = self._run_months(24)
        manor = world.manors[GRANT2["manor"]]
        self.assertEqual(
            sorted(manor.tile_ids), sorted(GRANT2_TILES), "не тот набор клеток тэна"
        )
        for hid in GRANT2_TENANTS + [GRANT2["holder_hh"]]:
            self.assertIn(hid, manor.household_ids)
        demesne = [
            tid
            for tid in manor.tile_ids
            if world.tiles[tid].regime_id == "demesne"
        ]
        self.assertGreaterEqual(
            len(demesne), 2, f"у {GRANT2['manor']} меньше двух доменов: {demesne}"
        )
        self.assertIn("t_06_02", demesne, "поле корня t_06_02 не стало доменом тэна")

    def test_second_thegn_holder_hunger_months_below_baseline(self) -> None:
        """Держатель второго тэна голодает реже, чем 39 мес из 60 (сид 1729)."""
        _, hungry = self._run_months_with_hunger(60, GRANT2["holder_hh"], since_month=18)
        self.assertLess(
            hungry,
            GRANT2_BASELINE_HUNGER_MONTHS,
            f"голодных месяцев держателя второго тэна {hungry}, "
            f"baseline {GRANT2_BASELINE_HUNGER_MONTHS}",
        )

    def test_second_thegn_farms_its_own_barn(self) -> None:
        """Второй тен — хозяйство: свой амбар принимает гэфоль и кормит держателя.

        Мгновенный «пустой амбар на снимке M24» ничего не доказывает (держатель
        только что поел), поэтому проверяется оборот книги, а не остаток.
        """
        world = self._run_months(24)
        manor = world.manors[GRANT2["manor"]]
        stock = world.stocks[manor.stock_id]
        self.assertEqual(stock.owner_kind, "manor")
        gafol_in = [
            entry
            for entry in world.ledger.entries
            if entry.reason == "gafol" and entry.dst_id == manor.stock_id
        ]
        board_out = [
            entry
            for entry in world.ledger.entries
            if entry.reason == "board" and entry.src_id == manor.stock_id
        ]
        self.assertTrue(gafol_in, f"в амбар {manor.stock_id} не пришёл гэфоль")
        self.assertTrue(
            board_out, f"из амбара {manor.stock_id} держатель не получал паёк"
        )

    def test_granted_household_gafol_goes_to_thegn_barn(self) -> None:
        """Гэфоль пожалованного двора идёт в `manor:<id>`, не в замок."""
        world = self._run_months(24)
        manor = world.manors[GRANT2["manor"]]
        gafol = [
            entry
            for entry in world.ledger.entries
            if entry.reason == "gafol"
            and entry.src_id == f"household:{GRANT2_TENANT}"
            and entry.dst_id == manor.stock_id
        ]
        self.assertTrue(
            gafol,
            f"нет гэфоля {GRANT2_TENANT} → {manor.stock_id}",
        )
        self.assertTrue(all(entry.amount > 0 for entry in gafol))

    def test_root_corvee_pool_falls_after_second_grant(self) -> None:
        """В жатву после M18 рук в корневом пуле меньше, чем в жатву до M18."""
        world = self._run_months(24)
        before = self._corvee_pool(world, "Y1-M09")
        after = self._corvee_pool(world, "Y2-M09")
        self.assertLess(
            after,
            before,
            f"пул корня не упал: до M18 {before}, после M18 {after}",
        )

    def test_twenty_four_months_conserve_matter(self) -> None:
        world = self._run_months(24)
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )

    def test_sixty_months_runs_and_conserves(self) -> None:
        world = self._run_months(60)
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )

    def test_replay_same_seed_same_hash(self) -> None:
        first = run_scenario(SCENARIO, 24, seed=1729)
        second = run_scenario(SCENARIO, 24, seed=1729)
        self.assertEqual(first.state_hash, second.state_hash)

    def test_sixty_months_replay_same_hash(self) -> None:
        """Полное окно 60 мес детерминировано: два прогона — один хеш."""
        first = run_scenario(SCENARIO, 60, seed=1729)
        second = run_scenario(SCENARIO, 60, seed=1729)
        self.assertEqual(first.state_hash, second.state_hash)


if __name__ == "__main__":
    unittest.main()
