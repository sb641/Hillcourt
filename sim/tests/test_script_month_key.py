"""Ключ месяца в записи `script:`: `at_month` — один, а не два.

Дефект, который этот тест запирает. Обработчики `call_boon` и `ease_week_work`
брали месяц из ключа `month`, тогда как запись сценария и планировщик прогона
(`runner.run`: `int(entry.get("at_month", 0)) == month_index`) пишут и читают
`at_month`. Написать действие так, как написаны все шестнадцать остальных и все
`design/scenarios/*.yml`, означало `KeyError: 'month'`: кнопка помощи и кнопка
смягчения барщины физически не нажимались из сценария. Тесты, писавшие ОБА ключа,
расхождение скрывали.

Проверяется не «есть ли `at_month` в словаре», а ровно то свойство, из-за
которого два ключа несовместимы: месяц, который пишет `ease_week_work`, обязан
быть месяцем тика, в котором запись исполняется. `ease_week_work` кладёт урезку в
`stats["eased_days_<год>_<month>"]`, а `economy/manor.py::duty_days_for` читает
`eased_days(world, world.clock.month)`. Расхождение ключей не падает — оно тихо
урезает месяц, который никто не читает, то есть кнопка «работает» и ничего не
делает. Поэтому `month` ≠ `at_month` обязан быть отказом, а не выбором любого.

Проверка падает без механики: с `entry["month"]` в обработчике первый тест
падает с `KeyError`, второй — с молчаливым `ValueError`/успехом на расхождении.
"""

from __future__ import annotations

import unittest

from hillcourt.runner import ACTION_HANDLERS, _apply_script_entry
from hillcourt.scenario import load_scenario

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
START_STAND = ROOT / "design" / "scenarios" / "start_stand.yml"
SEED = 7
#: M7 — `boon_allowed` (`legal/calendar_v0.yml`), иначе крик помощи законно
#: отклоняется и проверка ничего не различает.
CALL_MONTH = 7
MONTH_LEVERS = ("call_boon", "ease_week_work")


def _lever_entry(action: str, month: int, **extra) -> dict:
    """Запись сценария ровно в том виде, как её пишет `design/scenarios/*.yml`."""
    return {"at_month": month, "action": action, **extra}


class TestScriptMonthKeyIsOne(unittest.TestCase):
    """`at_month` — единственный ключ месяца; расхождение с `month` — отказ."""

    def test_scenario_style_entry_does_not_raise_key_error(self) -> None:
        """Запись с одним ключом `at_month` исполняется, а не падает `KeyError`.

        Ровно тот путь, которым кнопку жмёт игрок: `_apply_script_entry` — та же
        функция, что зовёт `runner.run` для записей `script:`.
        """
        for action, extra in (("call_boon", {}), ("ease_week_work", {"delta": 12.0})):
            with self.subTest(action=action):
                world = load_scenario(START_STAND, seed=SEED)
                _apply_script_entry(world, _lever_entry(action, CALL_MONTH, **extra))
                records = [a for a in world.player_actions if a.get("action") == action]
                self.assertEqual(
                    len(records), 1,
                    f"{action}: действие не попало в лог (запись с at_month "
                    f"единственным ключом месяца)",
                )

    def test_both_levers_are_in_the_handler_table(self) -> None:
        """Обе кнопки — в `ACTION_HANDLERS`, то есть «нажимаемы» вообще."""
        for action in MONTH_LEVERS:
            with self.subTest(action=action):
                self.assertIn(action, ACTION_HANDLERS)

    def test_month_differs_from_at_month_is_refused_loudly(self) -> None:
        """Два разных месяца — отказ с именем действия, а не молчаливый no-op.

        Через публичный путь, а не прямым вызовом помощника: иначе проверка
        падала бы на отсутствии имени, а не на поведении записи.
        """
        world = load_scenario(START_STAND, seed=SEED)
        entry = {
            "at_month": CALL_MONTH, "month": 3,
            "action": "ease_week_work", "delta": 12.0,
        }
        with self.assertRaises(ValueError) as ctx:
            _apply_script_entry(world, entry)
        message = str(ctx.exception)
        self.assertIn("ease_week_work", message, "Отказ не назвал действие")
        self.assertIn("at_month", message, "Отказ не назвал верный ключ")
        self.assertNotIsInstance(ctx.exception, KeyError)

    def test_matching_legacy_month_key_is_still_accepted(self) -> None:
        """Фикстуры, писавшие оба ключа, не ломаются — при РАВНЫХ месяцах."""
        for action, extra in (("call_boon", {}), ("ease_week_work", {"delta": 12.0})):
            with self.subTest(action=action):
                world = load_scenario(START_STAND, seed=SEED)
                _apply_script_entry(
                    world,
                    {
                        "at_month": CALL_MONTH, "month": CALL_MONTH,
                        "action": action, **extra,
                    },
                )
                self.assertEqual(
                    len([a for a in world.player_actions if a.get("action") == action]), 1
                )

    def test_missing_month_names_the_key_instead_of_key_error(self) -> None:
        """Нет месяца — `ValueError`, который называет ключ, а не `KeyError: 'month'`."""
        world = load_scenario(START_STAND, seed=SEED)
        with self.assertRaises(ValueError) as ctx:
            _apply_script_entry(world, {"action": "call_boon"})
        message = str(ctx.exception)
        self.assertIn("at_month", message, "Отказ не подсказал ключ сценария")
        self.assertNotIsInstance(ctx.exception, KeyError)

    def test_eased_days_lands_on_the_month_that_is_actually_read(self) -> None:
        """Смысл одного ключа: урезка попадает в месяц, который читает экономика.

        `ease_week_work` пишет `eased_days_<год>_<month>`; `duty_days_for` читает
        `eased_days(world, world.clock.month)`. Пока ключи одни, запись в `script:`
        на M3 обязана урезать M3 — тот месяц, который тик и читает.
        """
        from hillcourt.economy.manor import duty_days_for
        from hillcourt.engine.tick import run_month
        from hillcourt.engine.manor import eased_days

        world = load_scenario(START_STAND, seed=SEED)
        for entry in list(world.script):
            if int(entry.get("at_month", 0)) == 1:
                _apply_script_entry(world, entry)
        run_month(world)
        for month_index in (2, 3):
            for entry in list(world.script):
                if int(entry.get("at_month", 0)) == month_index:
                    _apply_script_entry(world, entry)
            run_month(world)
        before = duty_days_for(world, world.households["hh_player"])
        self.assertEqual(
            eased_days(world, world.clock.month), 0.0,
            "Предусловие: до приказа урезки в этом месяце нет",
        )
        _apply_script_entry(
            world, _lever_entry("ease_week_work", world.clock.month, delta=12.0)
        )
        self.assertGreater(
            eased_days(world, world.clock.month), 0.0,
            "Приказ, записанный как в сценарии, не урезал месяц, который читает тик",
        )
        self.assertLess(
            duty_days_for(world, world.households["hh_player"]), before,
            "Урезанный месяц не изменил барщину двора",
        )


if __name__ == "__main__":
    unittest.main()
