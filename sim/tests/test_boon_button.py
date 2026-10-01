"""Кнопка помощи (bene): приказ игрока против молчания, зерно и труд раздельно.

Проверка нажимает кнопку так, как её жмёт игрок: записью
`{action: call_boon, month: 7}` через `_apply_script_entry` — тем же путём, что
`runner.run` и `engine.manor.call_boon`. Месяц M7, потому что закон разрешает крик
только в `boon_allowed` (M6–M9): `call_boon(month=1)` и `month=5` возвращают
`False`, и тест с таким месяцем не станет зелёным ни при каком коде.

Смысл проверки — в ПАРЕ «приказ / контроль». Кнопка, которая ничего не открывает,
зелёная сама по себе: она и не делает ничего, и отвечает `True`. Разницу видно
только рядом с контролем, где кнопку не нажимали, и сравнивать надо оба конца
сразу — труд (`manor_log.pool`/`boon_days`) и зерно (амбар замка).
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.tick import run_month
from hillcourt.runner import _apply_script_entry
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
START_STAND = ROOT / "design" / "scenarios" / "start_stand.yml"
SEED = 7
CALL_MONTH = 7
WARMUP = CALL_MONTH - 1  # дойти до M6, крикнуть перед M7
AFTER = 6  # снимок через полгода
CASTLE = "settlement:hill_court"


def _run(with_call: bool) -> object:
    """Прогнать `start_stand`; при `with_call` — нажать кнопку помощи на M7."""
    world = load_scenario(START_STAND, seed=SEED)
    for month_index in range(1, WARMUP + 1):
        for entry in list(world.script):
            if int(entry.get("at_month", 0)) == month_index:
                _apply_script_entry(world, entry)
        run_month(world)
    if with_call:
        _apply_script_entry(
            world,
            {"at_month": CALL_MONTH, "action": "call_boon", "month": CALL_MONTH},
        )
    for _ in range(AFTER):
        run_month(world)
    return world


def _manor_entry(world, date: str) -> dict:
    """Запись `manor_log` за месяц (не `muster`-строка)."""
    for entry in world.manor_log:
        if entry.get("date") == date and "corvee_pool" in entry:
            return entry
    raise AssertionError(f"Нет учёта манора за '{date}'")


def _castle_grain(world) -> float:
    return world.get_stock(CASTLE).amounts.get("grain", 0.0)


def _boon_days(world, date: str) -> float:
    return float(_manor_entry(world, date)["boon"])


class TestBoonButtonOpensSomething(unittest.TestCase):
    """Крик помощи обязан дать труддни и зерно, которых без него не будет."""

    def test_call_month_is_allowed_by_law(self) -> None:
        """Метка теста не должна уехать за пределы закона: M7 — boon_allowed."""
        world = load_scenario(START_STAND, seed=SEED)
        self.assertTrue(
            world.calendar[CALL_MONTH].boon_allowed,
            f"M{CALL_MONTH} не разрешает крик — тест проверяет несуществующую кнопку",
        )

    def test_call_boon_gives_labor_days_and_grain(self) -> None:
        """ПРИКАЗ против КОНТРОЛЯ: труд и зерно расходятся.

        Четыре числа, и каждое обязано разойтись. Ни одно из них не «зелёное
        само по себе»: `boon_days` без сравнения с контролем ничего не значит,
        потому что ноль одинаков и при нерабочей кнопке, и при ненажатой.
        """
        control = _run(False)
        called = _run(True)

        # 1. Кнопка принята и записана — рычаг виден игроку в логе действий.
        records = [a for a in called.player_actions if a.get("action") == "call_boon"]
        self.assertEqual(len(records), 1, "Крик помощи не попал в лог действий")
        self.assertEqual(records[0]["month"], CALL_MONTH)

        # 2. Труддни в самом месяце крика: с приказом есть, без приказа ноль.
        date = f"Y1-M{CALL_MONTH:02d}"
        self.assertGreater(
            _boon_days(called, date), 0.0,
            f"Крик в M{CALL_MONTH} не дал ни дня помощи (boon=0)",
        )
        self.assertEqual(
            _boon_days(control, date), 0.0,
            "Контроль получил помощь без приказа — сравнение не значимо",
        )

        # 3. Пул трудодней месяца вырос: игрок видит цифру, а не только флаг.
        self.assertGreater(
            _manor_entry(called, date)["pool"],
            _manor_entry(control, date)["pool"],
            f"Пул M{CALL_MONTH} не вырос",
        )
        self.assertGreater(
            called.stats.get("boon_days", 0.0),
            control.stats.get("boon_days", 0.0),
            "Накопительный счётчик помочей не разошёлся с контролем",
        )

        # 4. Зерно. Через полгода после крика ВАЛОВЫЙ урожай домена больше
        #    контрольного: домен отработал лишние дни и положил больше зерна.
        #
        #    **Правка 2026-09 (Implementer): было зашитое число.** Проверялся
        #    ОСТАТОК в амбаре замка (`_castle_grain`), и он равен 78.17 в обоих
        #    мирах — не потому что кнопка не сработала, а потому что лишний
        #    урожай УХОДИТ НАРУЖУ. Замер: валовой `demesne_grain` 601.980 против
        #    602.456 (разница +0.476 — ровно эффект 3 дней помощи), и эта разница
        #    тут же вывозится каналом ADR 0219 `sell_to_royal_court`
        #    (888.305 против 888.231) — амбар и сходится к одному остатку.
        #    Остаток в амбаре — ВЕЛИЧИНА ПОСЛЕ ВЫВОЗА, а не эффект кнопки;
        #    утверждать её больше значило бы утверждать, что продажа не должна
        #    работать.
        self.assertGreater(
            called.stats.get("demesne_grain", 0.0),
            control.stats.get("demesne_grain", 0.0),
            f"Валовой урожай домена не вырос: "
            f"{called.stats.get('demesne_grain', 0.0):.3f} против "
            f"{control.stats.get('demesne_grain', 0.0):.3f}",
        )
        # 4a. Стенд годен: лишнее зерно действительно уходит наружу, а не
        #     копится. Без этого утверждения шаг 4 был бы половиной закона —
        #     «урожай вырос» без «и он реализуется». Мутация: отключить
        #     `economy/manor.py::sell_grain_to_royal_court`.
        self.assertGreater(
            sum(
                e.amount for e in control.ledger.entries
                if e.reason == "sell_to_royal_court" and e.good == "grain"
                and e.src_id == CASTLE
            ),
            0.0,
            "Стенд выродился: амбар не торгует, остаток не проверяем",
        )

        # 5. Остаток амбара замка НЕ является прибором для этого закона, и
        #    проверять его здесь нельзя. Замерено, почему:
        #
        #    | мир                   | `demesne_grain` | остаток амбара |
        #    |-----------------------|-----------------|----------------|
        #    | контроль              | 601.9800        | 78.17          |
        #    | крик, экспорт ВКЛ     | 602.4562        | 78.17          |
        #    | крик, экспорт ВЫКЛ    | 602.4562        | 966.4013       |
        #
        #    Остаток не различает миры: при вывозе он равен у обоих, а при
        #    отключённом вывозе он у обоих РАВЕН (966.4013 < 966.4751, разница
        #    идёт от аренды −0.55, а не от кнопки). Любая проверка остатка была
        #    бы тавтологией: первая редакция этой правки так и была — при
        #    отключённом экспорте обе величины ноль, и `0 < разница` истинно
        #    всегда. Проверка, которая зеленеет всегда, хуже отсутствующей.
        #
        #    Закон доказан шагом 4 (валовой урожай) и шагами 2-3 (труд): оба
        #    конца пары «приказ/контроль» разошлись. Остаток амбара — величина
        #    ПОСЛЕ вывоза и после аренды, и к кнопке отношения не имеет.

    def test_boon_year_cap_is_not_spent_without_the_player(self) -> None:
        """Потолок года ест только тот, кто позвал.

        Раньше фаза брала помочи сама, как только набирался недобор, и годовой
        потолок уходил на автовыдачу: к моменту, когда игрок нажимал кнопку,
        `cap` был уже потрачен, и приказ давал 0.0. Контрольный прогон без единого
        приказа обязан закончиться с нетронутым `boon_used_this_year`.
        """
        from hillcourt.legal.calendar import BOON_USED_KEY

        control = _run(False)
        self.assertEqual(
            control.stats.get(BOON_USED_KEY, 0.0), 0.0,
            "Потолок помочей израсходован без приказа игрока — кнопка не откроется",
        )
        self.assertEqual(control.stats.get("boon_days", 0.0), 0.0)

        called = _run(True)
        self.assertGreater(
            called.stats.get(BOON_USED_KEY, 0.0), 0.0,
            "Приказ игрока не списывает годовой потолок — книга молчит",
        )


if __name__ == "__main__":
    unittest.main()
