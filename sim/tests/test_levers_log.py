"""B1: рычаги игрока в логе на шире — запись и измеряемый сдвиг книги.

Сценарий `v0_shire` не меняется: скрипт сезона подменяется in-process
(`world.script`), затем месяцы идут через `_apply_script_entry` + `run_month`.
Проверяется, что шесть рычагов холма пишут `world.player_actions` с датой и
полями и каждый двигает учёт месяца (`world.manor_log`, `Ledger`, книга манора):

  * `ease_week_work` — пул барщины M3 ниже контроля (два числа);
  * `call_boon` — только месяц с `boon_allowed`, пул M8 выше (`boon_days > 0`);
  * `set_tile_regime` / `grant_tenement` — клетка выходит из домена, спрос M3 ниже;
  * `grant_tool` — перевод `iron_share` из амбара лорда во двор (reason `grant_tool`);
  * `grant_thegn` — новый `manor:<id>`, двор держателя в книге, `grant_ids`.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Callable

from hillcourt.engine.tick import run_month
from hillcourt.runner import ACTION_HANDLERS, _apply_script_entry
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SHIRE = ROOT / "design" / "scenarios" / "v0_shire.yml"
SEED = 1729
CASTLE = "settlement:hill_court"
DEMESNE_FIELD = "t_01_10"
LEVERS = (
    "set_tile_regime",
    "grant_tenement",
    "grant_tool",
    "grant_thegn",
    "call_boon",
    "ease_week_work",
)


def _run(
    script: list[dict],
    months: int,
    prepare: Callable | None = None,
) -> object:
    """Прогнать шир `months` месяцев, исполнив подменённый `script:` по месяцам."""
    world = load_scenario(SHIRE, seed=SEED)
    world.script = [dict(entry) for entry in script]
    if prepare is not None:
        prepare(world)
    for month_index in range(1, months + 1):
        for entry in world.script:
            if int(entry.get("at_month", 0)) == month_index:
                _apply_script_entry(world, entry)
        run_month(world)
    return world


def _month_entry(world, date: str) -> dict:
    """Запись манора за месяц (не `muster`-строку)."""
    for entry in world.manor_log:
        if entry.get("date") == date and "corvee_pool" in entry:
            return entry
    raise AssertionError(f"Нет учёта манора за '{date}'")


def _actions(world, name: str) -> list[dict]:
    return [a for a in world.player_actions if a.get("action") == name]


class TestEaseAndBoon(unittest.TestCase):
    """`ease_week_work` и `call_boon`: запись в лог и сдвиг пула месяца."""

    def test_ease_week_work_logged_and_lowers_march_pool(self) -> None:
        control = _run([], 4)
        eased = _run(
            [{"at_month": 3, "action": "ease_week_work", "month": 3, "delta": 12.0}],
            4,
        )

        records = _actions(eased, "ease_week_work")
        self.assertEqual(len(records), 1, "ease_week_work не записан")
        self.assertEqual(records[0]["date"], "Y1-M03")
        self.assertEqual(records[0]["month"], 3)
        self.assertAlmostEqual(records[0]["delta"], 12.0, places=6)

        control_entry = _month_entry(control, "Y1-M03")
        eased_entry = _month_entry(eased, "Y1-M03")
        self.assertLess(
            eased_entry["corvee_pool"],
            control_entry["corvee_pool"],
            f"Барщина M3 не урезана: {eased_entry['corvee_pool']} "
            f"против {control_entry['corvee_pool']}",
        )
        self.assertLess(
            eased_entry["worked"],
            control_entry["worked"],
            f"Работа M3 не упала: {eased_entry['worked']} "
            f"против {control_entry['worked']}",
        )

    def test_call_boon_outside_allowed_month_is_refused(self) -> None:
        from hillcourt.engine.manor import call_boon

        world = load_scenario(SHIRE, seed=SEED)
        self.assertFalse(world.calendar[3].boon_allowed, "M3 не должен разрешать boon")
        self.assertFalse(call_boon(world, 3), "Помоча вне boon_allowed не отклонена")
        self.assertEqual(_actions(world, "call_boon"), [], "Отказ попал в лог")

        self.assertTrue(world.calendar[8].boon_allowed, "M8 должен разрешать boon")
        self.assertTrue(call_boon(world, 8), "Помоча в разрешённый месяц не принята")
        self.assertEqual(len(_actions(world, "call_boon")), 1)

    def test_call_boon_logged_and_raises_august_pool(self) -> None:
        control = _run([], 9)
        boon = _run([{"at_month": 8, "action": "call_boon", "month": 8}], 9)

        records = _actions(boon, "call_boon")
        self.assertEqual(len(records), 1, "call_boon не записан")
        self.assertEqual(records[0]["date"], "Y1-M08")
        self.assertEqual(records[0]["month"], 8)
        self.assertGreater(
            boon.stats.get("boon_days", 0.0), 0.0, "Рычаг boon не дал дней (boon_days=0)"
        )

        control_entry = _month_entry(control, "Y1-M08")
        boon_entry = _month_entry(boon, "Y1-M08")
        self.assertGreater(
            boon_entry["pool"],
            control_entry["pool"],
            f"Пул M8 не вырос: {boon_entry['pool']} против {control_entry['pool']}",
        )


class TestBookLevers(unittest.TestCase):
    """Нарезка и пожалование: запись в лог и сдвиг книги/учёта."""

    def test_set_tile_regime_logged_and_cuts_demand(self) -> None:
        control = _run([], 4)
        changed = _run(
            [
                {
                    "at_month": 1,
                    "action": "set_tile_regime",
                    "tile": DEMESNE_FIELD,
                    "regime": "reserved_wood",
                }
            ],
            4,
        )

        self.assertEqual(changed.tiles[DEMESNE_FIELD].regime_id, "reserved_wood")
        self.assertEqual(
            changed.manors["manor_hill"].tile_regimes[DEMESNE_FIELD], "reserved_wood"
        )
        changed_entry = _month_entry(changed, "Y1-M03")
        control_entry = _month_entry(control, "Y1-M03")
        self.assertLess(
            changed_entry["demand"],
            control_entry["demand"],
            f"Спрос M3 не упал: {changed_entry['demand']} "
            f"против {control_entry['demand']}",
        )

        records = _actions(changed, "set_tile_regime")
        self.assertTrue(records, "set_tile_regime не записан")
        self.assertEqual(records[0]["date"], "Y1-M01")
        self.assertEqual(records[0]["tile"], DEMESNE_FIELD)
        self.assertEqual(records[0]["regime"], "reserved_wood")

    def test_grant_tenement_logged_and_cuts_demand(self) -> None:
        control = _run([], 4)
        granted = _run(
            [
                {
                    "at_month": 1,
                    "action": "grant_tenement",
                    "household": "hh_02",
                    "tiles": [DEMESNE_FIELD],
                }
            ],
            4,
        )

        right_id = f"right_tenement_hh_02_{DEMESNE_FIELD}"
        self.assertIn(right_id, granted.rights, "Право держания не создано")
        self.assertEqual(granted.tiles[DEMESNE_FIELD].regime_id, "villein_tenement")
        self.assertEqual(
            granted.manors["manor_hill"].tile_regimes[DEMESNE_FIELD],
            "villein_tenement",
        )
        granted_entry = _month_entry(granted, "Y1-M03")
        control_entry = _month_entry(control, "Y1-M03")
        self.assertLess(
            granted_entry["demand"],
            control_entry["demand"],
            f"Спрос M3 не упал: {granted_entry['demand']} "
            f"против {control_entry['demand']}",
        )

        records = _actions(granted, "grant_tenement")
        self.assertTrue(records, "grant_tenement не записан")
        self.assertEqual(records[0]["date"], "Y1-M01")
        self.assertEqual(records[0]["household"], "hh_02")
        self.assertEqual(records[0]["tiles"], [DEMESNE_FIELD])

    def test_grant_tool_logged_and_moves_ledger(self) -> None:
        def prepare(world) -> None:
            world.get_stock(CASTLE).amounts["iron_share"] = 2.0
            world.ledger.capture_initial(world.total_matter())

        world = _run(
            [
                {
                    "at_month": 1,
                    "action": "grant_tool",
                    "household": "hh_02",
                    "good": "iron_share",
                    "amount": 1.0,
                }
            ],
            1,
            prepare=prepare,
        )

        entries = [e for e in world.ledger.entries if e.reason == "grant_tool"]
        self.assertEqual(len(entries), 1, "grant_tool не сделал ровно один перевод")
        entry = entries[0]
        self.assertEqual(entry.src_id, CASTLE)
        self.assertEqual(entry.dst_id, "household:hh_02")
        self.assertEqual(entry.good, "iron_share")
        self.assertAlmostEqual(entry.amount, 1.0, places=6)

        records = _actions(world, "grant_tool")
        self.assertTrue(records, "grant_tool не записан")
        self.assertEqual(records[0]["date"], "Y1-M01")
        self.assertEqual(records[0]["household"], "hh_02")
        self.assertEqual(records[0]["good"], "iron_share")
        self.assertAlmostEqual(records[0]["amount"], 1.0, places=6)

        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_grant_thegn_logged_and_creates_nested_manor(self) -> None:
        world = _run(
            [
                {
                    "at_month": 1,
                    "action": "grant_thegn",
                    "person": "hh_retinue_p1",
                    "tiles": [DEMESNE_FIELD],
                    "households": ["hh_02"],
                }
            ],
            1,
        )

        manor_id = "manor_hh_retinue_p1"
        self.assertIn(manor_id, world.manors, "Вложенный манор не создан")
        self.assertIn(f"manor:{manor_id}", world.stocks, "Амбар тэна не заведён")
        self.assertEqual(
            world.households["hh_retinue"].manor_id,
            manor_id,
            "Двор держателя не в книге тэна",
        )
        self.assertEqual(
            world.manors["manor_hill"].grant_ids, ["grant_001"], "grant_ids не вырос"
        )

        records = _actions(world, "grant_thegn")
        self.assertTrue(records, "grant_thegn не записан")
        record = records[0]
        self.assertEqual(record["date"], "Y1-M01")
        self.assertEqual(record["person"], "hh_retinue_p1")
        self.assertEqual(record["manor"], manor_id)
        self.assertEqual(record["tiles"], [DEMESNE_FIELD])
        self.assertIn("hh_retinue", record["households"])
        self.assertEqual(record["grant"], "grant_001")


class TestDispatcher(unittest.TestCase):
    """Все шесть рычагов холма поддержаны диспетчером script-действий."""

    def test_all_six_levers_are_script_dispatchable(self) -> None:
        missing = [name for name in LEVERS if name not in ACTION_HANDLERS]
        self.assertEqual(missing, [], f"Рычаги без обработчика: {missing}")


if __name__ == "__main__":
    unittest.main()
