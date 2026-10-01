"""Отказ приказа — ход игры, а не авария; соседство клеток — одна правда.

Два закона, которые проверяются здесь, и оба родились из замеров Стола плейтеста.

## 1. Отказ не роняет прогон (ADR 0202)

`runner._apply_script_entry` вызывал `handler(world, entry)` без `try`.
Замер: `start_stand`, сид 1729, 24 месяца, приказ `grant_grain 99999` на 5-м месяце —
`ValueError: У лорда нет зерна: в амбаре 620.24, просят 99999.0`, `EXIT=1`,
**месяцев напечатано 4 из 24**. Двадцать месяцев игры не состоялись из-за одного
приказа, который лорд нажал в неподходящий момент, а узнал об этом из traceback.

Граница двух отказов — главное в этом файле, и она проведена ДО `try`:

| что случилось | кто отказал | что делает прогон |
|---|---|---|
| опечатка в сценарии (нет `work`, расходятся `at_month`/`month`, нет такого приказа) | автор сценария | **падает** — сломанный сценарий не должен выглядеть как отказ игроку |
| закон не дал (нет зерна, двор вне книги, клетка не соседняя, пресет не даёт дела) | игра | **логируется и идёт дальше** — это ход игры |

Проверка падает без механики: убрать `except ORDER_REFUSALS` — и
`test_a_refused_order_does_not_stop_the_run` падает на `ValueError`.

## 2. Соседство — одна правда, и она в `hexgrid` (ADR 0203)

Смежность клеток считалась двумя способами. `legal/actions.py::_adjacent` брала
манхэттен по координатам карты, мир при этом гекс (ADR 0071). Замер по всем
парам клеток: `start_stand` — **12 из 46** настоящих соседств отвергнуто (26 %),
`v0_shire` — **160 из 516** (31 %). Следствие, измеренное Столом: `send_party`
отказывает на гексе, который сосед, а `send_sally` на тот же гекс принимает.

Правда теперь одна — `engine/hexgrid.tile_ids_are_neighbor`. Проверка падает без
механики: подставить в неё манхэттен — и роняются оба теста класса (это проверка
не «числа совпали», а «отвергается ноль настоящих соседств»).

Реестр долга `KNOWN_LOCAL_TILE_ID_PARSERS` после ADR 0206 **пуст**: `legal/_adjacent`
задачу выполнил и модуль разбирать `t_XX_YY` больше негде. Запись снимает владелец
файла, и снятие обязано быть заметным — на месте снятой записи стоит проверка
обвинителя на синтетическом дереве, потому что счётчик, который перестал видеть
нарушителя, выглядит снаружи точно так же, как счётчик, которому работа кончена.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from hillcourt.engine.hexgrid import (
    axial_is_neighbor,
    parse_tile_id,
    tile_ids_are_neighbor,
)
from hillcourt.runner import _apply_script_entry, run
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
STAND = ROOT / "design" / "scenarios" / "start_stand.yml"
SHIRE = ROOT / "design" / "scenarios" / "v0_shire.yml"
SEED = 1729
MONTHS = 24
VILLAGER = "hh_family_01"
COURT = "settlement:hill_court"
GRAIN = "grain"

#: Приказ, который закон отвергнет: зерна в амбаре лорда заведомо меньше.
IMPOSSIBLE_GRAIN = 99999.0
REFUSAL_MONTH = 5

#: Реестр долга: модули вне `hexgrid`, которые разбирают id клетки сами (ADR 0203).
#:
#: **Пуст.** Последняя запись — `legal/actions.py` — снята по ADR 0206 §Закон 1:
#: `legal/_adjacent` теперь одна строка `tile_ids_are_neighbor(origin, destination)`,
#: и модуль не разбирает `t_XX_YY` нигде. Запись была не помилкой, а счётчиком
#: долга: она требовала, чтобы долг был **записан и посчитан**, и падала сама,
#: когда запись переставала быть нужной. По этой причине реестр нельзя наполнять
#: задним числом — снятая запись, которую не убрали, сама наказывает за выполненную
#: работу.
#:
#: Пустой реестр — не отсутствие обвинителя, а обвинитель в самом строгом виде:
#: `test_tile_id_parsing_lives_in_hexgrid_only` падает на **любом** модуле вне
#: `hexgrid.py`, который разбирает id клетки. Сторож на то, что пустой список
#: действительно всё ловит, — мутация в проверку ADR 0206 §Проверка.
KNOWN_LOCAL_TILE_ID_PARSERS: tuple[tuple[str, str, str], ...] = ()

#: Разбор `t_XX_YY` в обход `hexgrid` — единственный признак второй правды о
#: формате id клетки (ADR 0203 §4). Кавычки и пробелы допускаются: запрет
#: относится к разбору, а не к стилю записи, и обвинитель, который требует от
#: автора кавычки, рано или поздно пропустит нарушителя, написавшего иначе.
_LOCAL_TILE_ID_SPLIT = re.compile(r"\.split\(\s*[\"']_[\"']\s*\)")


def _scan_local_tile_id_parsers(src_root: Path, report_root: Path | None = None) -> list[str]:
    """Найти модули под `src_root`, которые разбирают id клетки сами.

    Возвращает `путь:строка` относительно `report_root` (по умолчанию — самого
    `src_root`). `hexgrid.py` пропускается: это единственное место, где формат
    `t_XX_YY` разбирать законно (ADR 0203). Обход рекурсивный, потому что второй
    правдой может стать и новый пакет.

    Отдельная функция, а не тело теста, — чтобы сторож
    `test_the_registry_still_catches_a_new_offender` проверял **сам обвинитель**
    на синтетическом дереве. Иначе снятие записи из пустого реестра было бы
    неотличимо от поломки сканера: оба состояния дают зелёный тест на чистом
    дереве.
    """
    base = report_root or src_root
    offenders: list[str] = []
    for path in sorted(src_root.rglob("*.py")):
        if path.name == "hexgrid.py":
            continue
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if _LOCAL_TILE_ID_SPLIT.search(line):
                offenders.append(f"{path.relative_to(base)}:{number}")
    return offenders


def _refusing_action() -> dict:
    return {
        "at_month": REFUSAL_MONTH,
        "action": "grant_grain",
        "household": VILLAGER,
        "amount": IMPOSSIBLE_GRAIN,
    }


class TestARefusedOrderIsAGameMove(unittest.TestCase):
    """Отказ закона: прогон идёт дальше, отказ виден игроку."""

    def test_a_refused_order_does_not_stop_the_run(self) -> None:
        """Главное из замера: 24 месяца из 24, а не 4.

        Мутация, которая обязана ронять тест: убрать `except ORDER_REFUSALS` в
        `_apply_script_entry` — `run` вернёт `ValueError` на 5-м месяце.
        """
        result = run(STAND, MONTHS, SEED, actions=[_refusing_action()])
        self.assertEqual(
            len(result.monthly),
            MONTHS,
            f"отказ приказа оборвал прогон: напечатано месяцев "
            f"{len(result.monthly)} из {MONTHS}",
        )
        self.assertEqual(
            result.months, MONTHS, "прогон объявил меньше месяцев, чем его просили"
        )

    def test_the_refusal_is_in_the_player_log_with_its_reason(self) -> None:
        """Молчание хуже обрыва: игрок должен знать, что приказ не исполнился.

        Обрыв был «виден» — traceback'ом. Продолжение без записи было бы хуже:
        двадцать месяцев игры и ни одного слова о том, что кнопка не сработала.
        """
        result = run(STAND, MONTHS, SEED, actions=[_refusing_action()])
        refused = [r for r in result.player_actions if r.get("refused")]
        self.assertEqual(
            len(refused), 1, "отказ не попал в лог приказов: приказ молча не исполнился"
        )
        self.assertEqual(refused[0]["action"], "grant_grain")
        self.assertEqual(refused[0]["month"], REFUSAL_MONTH)
        self.assertIn("зерна", str(refused[0].get("reason", "")))
        self.assertIn(
            str(IMPOSSIBLE_GRAIN),
            str(refused[0].get("reason", "")),
            "отказ не назвал запрошенное: игрок не поймёт, о чём речь",
        )

    def test_the_refusal_keeps_what_the_player_asked_for(self) -> None:
        """В записи об отказе остаётся и просьба — иначе непонятно, что именно отказали."""
        result = run(STAND, MONTHS, SEED, actions=[_refusing_action()])
        refused = [r for r in result.player_actions if r.get("refused")][0]
        self.assertEqual(refused["household"], VILLAGER)
        self.assertAlmostEqual(float(refused["amount"]), IMPOSSIBLE_GRAIN, places=6)

    def test_a_refused_order_moves_no_matter(self) -> None:
        """Отказ — это не ход: материя не создаётся и не исчезает (И-1).

        Проверяется на ledger, а не на «вроде не упало»: приказ, который отказали,
        не должен был успеть что-то перевести.
        """
        result = run(STAND, MONTHS, SEED, actions=[_refusing_action()])
        self.assertAlmostEqual(result.matter_delta, 0.0, places=6)

    def test_the_refusal_changes_the_world_hash(self) -> None:
        """Мир, в котором приказ отказали, и мир, в котором его не было, — разные.

        Иначе отказ неотличим от приказа, который не исполнился по незнанию, и
        `state_hash` перестаёт быть проверкой И-6 на этой величине.
        """
        refused = run(STAND, MONTHS, SEED, actions=[_refusing_action()])
        bare = run(STAND, MONTHS, SEED)
        self.assertNotEqual(
            refused.state_hash, bare.state_hash,
            "отказ не попал в состояние мира: два разных мира сошлись хешем",
        )

    def test_the_refusal_is_counted_for_the_summary(self) -> None:
        """Сводка обязана считать отказы, иначе итог прогона их не покажет."""
        world = load_scenario(STAND, seed=SEED)
        _apply_script_entry(world, _refusing_action())
        self.assertEqual(world.stats.get("orders_refused"), 1.0)
        _apply_script_entry(world, _refusing_action())
        self.assertEqual(world.stats.get("orders_refused"), 2.0)

    def test_the_players_line_leads_with_the_refusal(self) -> None:
        """В строке лога «ОТКАЗ» и причина идут РАНЬШЕ рычагов приказа.

        Причина в конце, после `amount=99999.0`, читается как сноска к числу, а
        игрок обязан увидеть «не исполнилось» раньше, чем то, что он просил.
        """
        from hillcourt.runner import _format_player_action

        result = run(STAND, MONTHS, SEED, actions=[_refusing_action()])
        refused = [r for r in result.player_actions if r.get("refused")][0]
        line = _format_player_action(refused)
        self.assertIn("ОТКАЗ", line)
        self.assertLess(
            line.index("ОТКАЗ"), line.index("amount="),
            "причина напечатана после рычага: игрок прочтёт число и не увидит отказа",
        )


class TestAnInertLeverIsNamedInTheLog(unittest.TestCase):
    """Рычаг, который ничего не меняет, не должен выглядеть как результат (ADR 0204).

    ## Что было

    Замер Стола плейтеста, `start_stand`, сид 1729:

    * `grant_tenure` с `rent_share` = 0.1 / 0.5 / 0.9 / 1.0 даёт ренту
      **74.29 четыре раза подряд**; меняется только 0.0. Причина: размер берётся из
      каталога (`legal/obligations.py::rent_amount_for` → `template.default_share`),
      а `rent_share` в приказе живёт как «платит / не платит»
      (`if rent_share > 0.0` в `grant_tenure`);
    * `add_obligation` с `due_amount` = 0 / 1 / 5 / 20 / 100 даёт ренту 122.86,
      зерно 1188.0 и подачу 66.2 до копейки одинаково, потому что
      `refresh_rent_due` перезаписывает сумму каждый месяц.

    Тогда починка была честной, но неполной: оба рычага оставались в приказе, а
    лог **принимал их и подписывал** («переключатель», «пересчитывается»).
    ADR 0204 §Отменённое требовал пометки, и это требование выполнено было.

    ## Что стало (ADR 0206)

    Юрист починил оба рычага в своей зоне, и выражение принципа честного лога
    изменилось — **принцип не изменился**. ADR 0204 запрещал одно: показывать
    игроку число, которым нельзя управлять, как результат. Два выражения:

    | рычаг | было | стало |
    |---|---|---|
    | `grant_tenure.rent_share` | прими и честно пометь «это переключатель» | **прими**: теперь это ставка, и applied-величина читается из права |
    | `add_obligation.due_amount` вида «рента-доля» | прими и честно пометь «пересчитывается» | **откажи** и объясни, где задаётся настоящая величина |

    Отказ сильнее пометки: при пометке приказ всё равно исполнялся, и игрок уходил
    от стола с ощущением, что что-то сделал. При отказе он уходит, зная, что
    кнопка не сработала и куда надо идти вместо неё (ADR 0202, ADR 0206 §Закон 3).

    Честность лога проверяется здесь на **всех** путях, где величина ещё
    показывается игроку: где приказ отказывает — отказ с причиной; где величина
    по-прежнему принимается и остаётся живой, — без ложной пометки; где величина
    показывается, но не выбиралась игроком, — с пометкой «пересчитывается».
    """

    def _tenure(self, share: float) -> dict:
        world = load_scenario(STAND, seed=SEED)
        for entry in world.script:
            if int(entry.get("at_month", 0)) == 1:
                _apply_script_entry(world, entry)
        records = _apply_script_entry(
            world,
            {"at_month": 1, "action": "grant_tenure", "household": VILLAGER,
             "tile": "t_02_00", "kind": "grazing", "rent_share": share},
        )
        grants = [r for r in records if r.get("action") == "grant_tenure"]
        self.assertEqual(len(grants), 1, "приказ выдачи надела не записан")
        return grants[0]

    def test_the_applied_rate_is_the_rate_the_world_read(self) -> None:
        """Applied-ставка — та, что прочитал мир, то есть `Right.rent_share`.

        ADR 0206 §Закон 2: размер оброка считает ставка **права**, а каталог держит
        умолчание для наделов без назначенной доли. Раньше здесь стояло
        `template.default_share` (0.1), и лог врал дважды: показывал чужую долю и
        подписывал её как переключатель.

        Мутация, которая обязана ронять тест: вернуть
        `rent_template(world).default_share` в `_annotate_rent_share_rate`.
        """
        for share in (0.5, 0.9):
            with self.subTest(rent_share=share):
                record = self._tenure(share)
                self.assertIn(
                    "rent_share_applied", record,
                    "лог не сказал, какую ставку применил мир: игрок гадает",
                )
                self.assertAlmostEqual(
                    float(record["rent_share_applied"]), share, places=9,
                    msg="applied-ставка разошлась с выданной: в логе вторая правда",
                )

    def test_the_applied_rate_is_read_from_the_right_not_from_a_constant(self) -> None:
        """Applied-ставка читает **право**, а не константу и не каталог.

        Иначе завтрашняя правка надела разошлась бы с логом молча. Сторож
        подставляет в `_annotate_rent_share_rate` две константы, которые когда-то
        стояли там по недосмотру, и требует, чтобы обе были пойманы.
        """
        from hillcourt.legal.obligations import rent_template

        world = load_scenario(STAND, seed=SEED)
        catalog = float(rent_template(world).default_share)
        record = self._tenure(0.9)
        applied = float(record["rent_share_applied"])
        self.assertNotAlmostEqual(
            applied, catalog, places=9,
            msg="applied-ставка взята из каталога: каталог — умолчание, а не "
            "применённая ставка (ADR 0206)",
        )
        self.assertNotAlmostEqual(
            applied, 0.1, places=9,
            msg="applied-ставка — константа 0.1: это ADR 0204-временная вторая правда",
        )

    def test_the_dead_lever_note_is_gone_from_the_tenure_order(self) -> None:
        """Сторож: пометка «переключатель» не должна вернуться.

        После ADR 0206 `rent_share` — живая ставка, и надпись «это переключатель»
        стала бы ложью другого рода: лог отнимал бы у рычага его силу и учил бы
        игрока не крутить то, что работает. ADR 0206 §Отменено требует, чтобы
        annotator либо читал ставку права, либо был снят вместе с пометкой, — и
        сделано первое, поэтому поля быть не должно.

        Мутация, которая обязана ронять тест: вернуть
        `record["rent_share_is_switch_only"] = True` в annotator.
        """
        record = self._tenure(0.9)
        self.assertNotIn(
            "rent_share_is_switch_only", record,
            "лог называет живую ставку переключателем: игрок перестанет ей пользоваться",
        )

    def test_the_requested_share_is_still_shown(self) -> None:
        """Рычаг не выбрасывается: игрок видит, что именно он просил.

        `rent_share` остаётся в записи и остаётся рычагом — теперь настоящим, а не
        переключателем (ADR 0206). Пометка добавляется рядом, а не вместо.
        """
        record = self._tenure(0.9)
        self.assertAlmostEqual(float(record["rent_share"]), 0.9, places=9)

    def test_the_rent_order_with_a_number_is_refused(self) -> None:
        """Главное из ADR 0206 §Закон 3: сумма оброка — не приказ, а производная.

        Игрок вводил `due_amount: 100` и получал ровно то же, что при `0`. Пометить
        это «пересчитывается» было мало: приказ исполнялся, и игрок уходил со
        стола, решив, что записал повинность. Теперь приказ **отказывает**, и в
        логе игрока лежит отказ с причиной.

        Мутация, которая обязана ронять тест: убрать `raise ValueError` в
        `legal/actions.py::add_obligation` (число снова примут молча и выбросят) —
        тест упадёт на `refused`.
        """
        world = load_scenario(STAND, seed=SEED)
        for entry in world.script:
            if int(entry.get("at_month", 0)) == 1:
                _apply_script_entry(world, entry)
        before = set(world.households[VILLAGER].obligation_ids)
        records = _apply_script_entry(
            world,
            {"at_month": 1, "action": "add_obligation", "household": VILLAGER,
             "kind": "rent", "due_good": GRAIN, "due_amount": 100.0},
        )
        self.assertEqual(len(records), 1, "приказ не записан в лог вовсе")
        refused = records[0]
        self.assertTrue(
            refused.get("refused"),
            f"приказ оброка принят числом: игрок снова крутит мёртвый рычаг — {refused}",
        )
        self.assertEqual(
            set(world.households[VILLAGER].obligation_ids), before,
            "отказанный приказ оброка всё же завёл повинность: игрока обманули и здесь",
        )
        self.assertAlmostEqual(
            float(refused["due_amount"]), 100.0, places=6,
            msg="отказ не сохранил, что игрок просил: он не поймёт, о чём речь",
        )

    def test_the_refusal_names_where_the_rate_is_set(self) -> None:
        """Отказ обязан быть **полезным**: назвать, куда идти за ставкой.

        «Число не принимается» — это отказ, который ничего не объясняет: игрок
        уберёт из приказа поле и останется с двором без оброка, не зная, что
        ставка задаётся другим приказом. ADR 0206 §Закон 3 называет этот приказ
        прямо, и причина обязана называть его тоже.

        Мутация, которая обязана ронять тест: убрать из текста отказа
        `grant_tenure` / `rent_share`.
        """
        world = load_scenario(STAND, seed=SEED)
        for entry in world.script:
            if int(entry.get("at_month", 0)) == 1:
                _apply_script_entry(world, entry)
        records = _apply_script_entry(
            world,
            {"at_month": 1, "action": "add_obligation", "household": VILLAGER,
             "kind": "rent", "due_good": GRAIN, "due_amount": 100.0},
        )
        reason = str(records[0].get("reason", ""))
        self.assertIn("grant_tenure", reason, "отказ не назвал приказ, которым задаётся ставка")
        self.assertIn("rent_share", reason, "отказ не назвал поле, которым задаётся ставка")
        self.assertIn(
            str(100.0), reason,
            "отказ не повторил, что именно было отвергнуто: игрок не поймёт, о чём речь",
        )

    def test_the_refused_rent_order_is_visible_in_the_players_log(self) -> None:
        """Отказ виден игроку в его строке, а не только в возврате приказа.

        `_apply_script_entry` возвращает отказ, но игрок читает лог. Проверяется
        сквозной путь: запись обязана лежать в `world.player_actions` и печататься
        с пометкой «ОТКАЗ» раньше рычагов приказа (ADR 0202).
        """
        from hillcourt.runner import _format_player_action

        world = load_scenario(STAND, seed=SEED)
        for entry in world.script:
            if int(entry.get("at_month", 0)) == 1:
                _apply_script_entry(world, entry)
        _apply_script_entry(
            world,
            {"at_month": 1, "action": "add_obligation", "household": VILLAGER,
             "kind": "rent", "due_good": GRAIN, "due_amount": 100.0},
        )
        rent_records = [
            r for r in world.player_actions
            if r.get("action") == "add_obligation" and r.get("due_good") == GRAIN
        ]
        self.assertTrue(rent_records, "отказ оброка не попал в лог игрока")
        refusal = [r for r in rent_records if r.get("refused")]
        self.assertEqual(len(refusal), 1, "в логе игрока нет ровно одного отказа оброка")
        line = _format_player_action(refusal[0])
        self.assertIn("ОТКАЗ", line)
        self.assertLess(
            line.index("ОТКАЗ"), line.index("due_amount="),
            "причина напечатана после отвергнутого числа: игрок прочтёт его и не увидит отказа",
        )

    def test_a_rent_order_without_a_number_still_opens_the_obligation(self) -> None:
        """Сторож в другую сторону: отказ не превратился в «оброк завести нельзя».

        Закон 0206 разводит два случая: с числом — отказ, без числа — оброк
        заводится по праву. Если бы отвергли и второе, игрок лишился бы оброка
        вообще, а это не то, что говорит ADR, и не то, что нужно экономике.

        Мутация, которая обязана ронять тест: заменить `_open_rent_obligation` в
        `add_obligation` на `raise` — тест упадёт на отказе.
        """
        world = load_scenario(STAND, seed=SEED)
        for entry in world.script:
            if int(entry.get("at_month", 0)) == 1:
                _apply_script_entry(world, entry)
        records = _apply_script_entry(
            world,
            {"at_month": 1, "action": "add_obligation", "household": VILLAGER,
             "kind": "rent", "due_good": GRAIN, "right_id": None},
        )
        added = [r for r in records if r.get("action") == "add_obligation"]
        self.assertEqual(len(added), 1, "оброк без суммы не заведён")
        self.assertFalse(
            added[0].get("refused"),
            f"оброк без суммы отвергнут: {added[0].get('reason')}",
        )

    def test_the_derived_amount_shown_by_the_tenure_order_is_marked(self) -> None:
        """Честность лога на единственном живом пути пометки «пересчитывается».

        Оброк, который открывает `grant_tenure` сам, появляется в логе вложенной
        записью с числом, которого игрок **не задавал**: оно выведено из ставки
        права и урожая прошлого месяца. Показывать такое число без пометки —
        ровно тот брак, который ADR 0204 запрещал: число выглядит назначением.
        Пометка обязана остаться здесь живой, иначе отказ ушёл в одну сторону, а
        ложь осталась в другой.

        Мутация, которая обязана ронять тест: убрать
        `DEAD_LEVER_ANNOTATORS["add_obligation"]`.
        """
        world = load_scenario(STAND, seed=SEED)
        for entry in world.script:
            if int(entry.get("at_month", 0)) == 1:
                _apply_script_entry(world, entry)
        _apply_script_entry(
            world,
            {"at_month": 1, "action": "grant_tenure", "household": VILLAGER,
             "tile": "t_02_00", "kind": "grazing", "rent_share": 0.9},
        )
        nested = [
            r for r in world.player_actions
            if r.get("action") == "add_obligation" and r.get("due_good") == GRAIN
        ]
        self.assertTrue(nested, "подготовка не удалась: вложенного оброка нет в логе")
        for record in nested:
            self.assertTrue(
                record.get("due_amount_recomputed_monthly"),
                "лог не сказал, что показанную сумму оброка пересчитывают: игрок "
                "примет её за назначение",
            )

    def test_a_fixed_obligation_is_not_marked_as_derived(self) -> None:
        """Сторож: пометка не деградировала в «всё помесячно производное».

        Не-оброк (`kind: labor_duty`) пересчётом не живой: его сумму
        `refresh_rent_due` не трогает, и называть её производной — враньё. Здесь
        величина **принимается** и применяется как задано, поэтому честный лог
        здесь — отсутствие пометки (ADR 0204 §«Что запрещено»).
        """
        world = load_scenario(STAND, seed=SEED)
        for entry in world.script:
            if int(entry.get("at_month", 0)) == 1:
                _apply_script_entry(world, entry)
        records = _apply_script_entry(
            world,
            {
                "at_month": 1, "action": "add_obligation",
                "household": VILLAGER, "kind": "labor_duty", "due_amount": 8.0,
            },
        )
        added = [r for r in records if r.get("action") == "add_obligation"]
        self.assertEqual(len(added), 1)
        self.assertIsNone(
            added[0].get("due_amount_recomputed_monthly"),
            "не-оброк помечен как помесячно производный: помечка ничего не значит",
        )
        self.assertAlmostEqual(
            float(added[0]["due_amount"]), 8.0, places=9,
            msg="принятую величину выбросили: пометка уехала, а вместе с ней и число",
        )

    def test_a_non_rent_order_without_a_number_is_refused(self) -> None:
        """Сторож на границе: без числа отказывает и не-оброк.

        Закон 0206 §Закон 3 отказывает не только оброку с числом, но и **любому**
        виду без суммы: у `labor_duty` число и есть долг (ADR 0131, ADR 0149), и
        без него запись бессмысленна. Отказ здесь — та же честная форма, что и
        выше, и она обязана называть недостающее поле.
        """
        world = load_scenario(STAND, seed=SEED)
        for entry in world.script:
            if int(entry.get("at_month", 0)) == 1:
                _apply_script_entry(world, entry)
        records = _apply_script_entry(
            world,
            {"at_month": 1, "action": "add_obligation",
             "household": VILLAGER, "kind": "labor_duty"},
        )
        self.assertTrue(
            records[0].get("refused"),
            f"не-оброк без суммы принят: повинность без долга — {records[0]}",
        )
        self.assertIn("due_amount", str(records[0].get("reason", "")))

    def test_a_nested_record_is_annotated_by_its_own_order(self) -> None:
        """Вложенная запись получает правду о СВОЁМ рычаге, а не о внешнем.

        `grant_tenure` сам вызывает `add_obligation` (ADR 0196), и та запись — про
        оброк, а не про выдачу надела. Если бы правило выбиралось по имени внешнего
        приказа, оброк получил бы `rent_share_applied`, которого в нём нет, и
        игрок прочитал бы у оброка рычаг, который не задавал.

        Мутация, которая обязана ронять тест: вернуть выбор правила по имени
        внешнего приказа (`_annotate_dead_levers(world, action, record)`).
        """
        world = load_scenario(STAND, seed=SEED)
        for entry in world.script:
            if int(entry.get("at_month", 0)) == 1:
                _apply_script_entry(world, entry)
        nested = [
            r for r in world.player_actions
            if r.get("action") == "add_obligation" and "rent_share" not in r
        ]
        self.assertTrue(nested, "подготовка не удалась: вложенного оброка нет в логе")
        for record in nested:
            self.assertNotIn(
                "rent_share_applied", record,
                "оброк помечен рычагом выдачи надела: игрок прочтёт то, чего не просил",
            )
        tenure = [r for r in world.player_actions if r.get("action") == "grant_tenure"]
        self.assertTrue(tenure)
        for record in tenure:
            self.assertIn(
                "rent_share_applied", record,
                "выдача надела потеряла свою пометку: правило выбрано не по записи",
            )

    def test_other_orders_carry_no_lever_noise(self) -> None:
        """Пометка дописывается только там, где рычаг мёртвый.

        Мутация, которая обязана ронять тест: повесить annotator на
        `assign_work` — тест упадёт на лишнем поле.
        """
        world = load_scenario(STAND, seed=SEED)
        for entry in world.script:
            if int(entry.get("at_month", 0)) == 1:
                _apply_script_entry(world, entry)
        records = _apply_script_entry(
            world,
            {"at_month": 1, "action": "assign_work", "household": VILLAGER,
             "work": "work_plot"},
        )
        for record in records:
            self.assertNotIn("rent_share_applied", record)
            self.assertNotIn("rent_share_is_switch_only", record)
            self.assertNotIn("due_amount_recomputed_monthly", record)


class TestAMistypedScenarioStillStopsTheRun(unittest.TestCase):
    """Опечатка автора — не отказ игроку. Этот закон держит границу ADR 0202."""

    def test_an_unknown_order_still_raises(self) -> None:
        world = load_scenario(STAND, seed=SEED)
        with self.assertRaises(ValueError):
            _apply_script_entry(world, {"at_month": 1, "action": "assign_nothing"})

    def test_a_missing_work_lever_still_raises(self) -> None:
        """Опечатка `work` обязана падать, а не превращаться в «лорд не смог».

        Мутация, которая обязана ронять тест: убрать `ENTRY_SHAPE_CHECKS` из
        `_apply_script_entry` — тогда `ValueError` уйдёт в перехват, запись об
        отказе появится в логе, и тест упадёт на `assertRaises`.
        """
        world = load_scenario(STAND, seed=SEED)
        with self.assertRaises(ValueError):
            _apply_script_entry(
                world,
                {"at_month": 1, "action": "assign_work", "household": VILLAGER},
            )
        self.assertEqual(
            [r for r in world.player_actions if r.get("refused")],
            [],
            "опечатка сценария записалась как отказ закона: тихая поломка",
        )

    def test_a_missing_month_lever_still_raises(self) -> None:
        """Ровно тот же закон для месяца: `call_boon` без месяца падает вслух."""
        world = load_scenario(STAND, seed=SEED)
        with self.assertRaises(ValueError):
            _apply_script_entry(world, {"action": "call_boon"})

    def test_a_broken_handler_is_not_disguised_as_a_refusal(self) -> None:
        """Дефект кода не имеет права выглядеть как решение игры.

        `KeyError`/`TypeError` — не отказ закона. Если бы перехват был широким
        (`except Exception`), любой баг приказа стал бы «отказом» и тихо уехал в
        лог; прогон продолжался бы, а причина молчала. Мутация: заменить
        `ORDER_REFUSALS` на `Exception` — тест падает.
        """
        from hillcourt import runner

        self.assertEqual(
            set(runner.ORDER_REFUSALS),
            {PermissionError, ValueError},
            "перехват отказов расширен: дефект кода едет в лог как отказ закона",
        )
        for broken in (KeyError, TypeError, AttributeError, IndexError):
            with self.subTest(exception=broken.__name__):
                self.assertNotIn(broken, runner.ORDER_REFUSALS)


class TestNeighbourIsOneTruth(unittest.TestCase):
    """Смежность клеток — одна функция, и она гексовая (ADR 0203)."""

    def _world_tile_ids(self, path: Path) -> list[str]:
        return sorted(load_scenario(path, seed=SEED).tiles)

    def test_no_true_neighbour_pair_is_rejected(self) -> None:
        """Главное из замера: отвергается НОЛЬ настоящих соседств.

        До правки: `start_stand` — 12 из 46 (26 %), `v0_shire` — 160 из 516 (31 %).
        Проверка идёт по всем упорядоченным парам клеток, а не по выборочным
        примерам: выборочный пример проходит и на сломанной функции, еслиexample
        попал в четыре «карточных» направления.

        Мутация, которая обязана ронять тест: подставить в
        `tile_ids_are_neighbor` манхэттен по координатам карты.
        """
        for path, before_rejected in ((STAND, 12), (SHIRE, 160)):
            with self.subTest(scenario=path.name):
                world = load_scenario(path, seed=SEED)
                ids = sorted(world.tiles)
                rejected: list[tuple[str, str]] = []
                for origin in ids:
                    for destination in ids:
                        if origin == destination:
                            continue
                        truth = axial_is_neighbor(
                            world.tiles[origin].coord, world.tiles[destination].coord
                        )
                        if truth and not tile_ids_are_neighbor(origin, destination):
                            rejected.append((origin, destination))
                self.assertEqual(
                    rejected, [],
                    f"{path.name}: отвергнуто {len(rejected)} настоящих соседств "
                    f"(было {before_rejected}); первые: {rejected[:4]}",
                )

    def test_every_hex_neighbour_is_accepted_on_a_live_world(self) -> None:
        """То же, но «снизу вверх»: из шести направлений принимаются все шесть.

        Не тавтология над первой проверкой: эталон здесь — `HEX_DIRECTIONS`
        в `hexgrid`, то есть объявленный закон сетки, а не результат самой
        функции. Манхэттен проходит бы четырьмя из шести и на такой проверке
        поймал бы 2/6 направлений на любой клетке.
        """
        from hillcourt.engine.hexgrid import HEX_DIRECTIONS, axial_to_offset, tile_id_at

        world = load_scenario(SHIRE, seed=SEED)
        checked = 0
        for tile_id in sorted(world.tiles):
            q, r = world.tiles[tile_id].coord
            for dq, dr in HEX_DIRECTIONS:
                neighbour = tile_id_at(*axial_to_offset(q + dq, r + dr))
                if neighbour not in world.tiles:
                    continue
                checked += 1
                self.assertTrue(
                    tile_ids_are_neighbor(tile_id, neighbour),
                    f"{tile_id} и {neighbour} — соседи по сетке, а вопрос сказал «нет»",
                )
        self.assertGreater(checked, 400, "проверка не набрала пар: она ничего не меряет")

    def test_a_cell_is_not_its_own_neighbour(self) -> None:
        """Сторож: правило не деградировало в «сосед — это кто угодно»."""
        self.assertFalse(tile_ids_are_neighbor("t_02_00", "t_02_00"))

    def test_a_non_neighbour_is_refused(self) -> None:
        """Сторож в другую сторону: функция не разрешила всё подряд.

        Расстояние 2 по гексу — не соседство, даже если по карте рядом.
        """
        self.assertFalse(tile_ids_are_neighbor("t_02_00", "t_04_00"))
        self.assertFalse(tile_ids_are_neighbor("t_02_00", "t_02_02"))

    def test_a_name_that_is_not_a_cell_is_not_a_neighbour(self) -> None:
        """Несуществующая клетка соседом быть не может — иначе приказ уведёт в пустоту."""
        self.assertFalse(tile_ids_are_neighbor("t_02_00", "household:hh_family_01"))
        self.assertFalse(tile_ids_are_neighbor("t_02_00", "t_xx_yy"))
        self.assertFalse(tile_ids_are_neighbor("t_02_00", None))

    def test_the_answer_is_symmetric(self) -> None:
        """Соседство — отношение, а не направление: A→B и B→A обязаны совпадать.

        Манхэттен по координатам карты симметричен, поэтому этот тест зелёный и на
        сломанной версии — он не ловит дыру ADR 0203, он запрещает «починку»
        сравнением только в одну сторону.
        """
        world = load_scenario(STAND, seed=SEED)
        for origin in sorted(world.tiles):
            for destination in sorted(world.tiles):
                self.assertEqual(
                    tile_ids_are_neighbor(origin, destination),
                    tile_ids_are_neighbor(destination, origin),
                    f"{origin}/{destination}: соседство несимметрично",
                )

    def test_tile_id_parsing_lives_in_hexgrid_only(self) -> None:
        """Формат id клетки разбирает один модуль — иначе вернётся вторая правда.

        Мутация, которая обязана ронять тест: вернуть локальный разбор
        `id.split("_")` в любой другой модуль `sim/src` — тест упадёт на новом
        файле, которого нет в реестре долга.

        **Реестр долга, а не помилка, и он пуст не потому, что обвинителя нет.**
        Реестр — счётчик: запись обязана быть у нарушителя, и она же падает, когда
        нарушитель закончил (`subTest` ниже). Запись про `legal/actions.py` была
        снята по ADR 0206 §Закон 1: `_adjacent` спрашивает `hexgrid`, и модуль не
        разбирает `t_XX_YY` нигде. Оставленная снятая запись наказывала бы
        владельца Legal за выполненную работу — поэтому пустой реестр и есть
        правильное состояние, а не ослабление проверки: сторож
        `test_the_registry_still_catches_a_new_offender` проверяет обвинитель на
        синтетическом дереве, чтобы пустой список нельзя было спутать со
        неработающим сканером.
        """
        offenders = _scan_local_tile_id_parsers(ROOT / "sim" / "src", ROOT)
        known_files = {entry[0] for entry in KNOWN_LOCAL_TILE_ID_PARSERS}
        self.assertEqual(
            {o.split(":")[0] for o in offenders} - known_files,
            set(),
            "id клетки разбирается вне hexgrid и вне реестра долга: "
            + "; ".join(o for o in offenders if o.split(":")[0] not in known_files)
            + " (ADR 0203)",
        )
        for file_path, owner, what in KNOWN_LOCAL_TILE_ID_PARSERS:
            with self.subTest(debt=file_path):
                self.assertTrue(
                    any(o.startswith(file_path + ":") for o in offenders),
                    f"{file_path} больше не разбирает id клетки — снимите запись из "
                    f"реестра долга ({what}, владелец {owner})",
                )

    def test_the_registry_still_catches_a_new_offender(self) -> None:
        """Сторож на пустой реестр: снятая запись — не снятый обвинитель.

        Реестр `KNOWN_LOCAL_TILE_ID_PARSERS` пуст, и это единственное состояние,
        в котором он может быть пустым: счётчик, который перестал видеть
        нарушителя, молча соглашается со всем. Поэтому обвинитель проверяется на
        **синтетическом** дереве, а не на `sim/src` (где нарушителей нет и быть
        не должно): во временный каталог кладётся `hexgrid.py` — единственное
        место, где разбор формата id законен — и `rogue.py`, который разбирает id
        сам. Сканер обязан увидеть `rogue.py` и **не** увидеть `hexgrid.py`.

        Мутации, которые обязаны ронять тест:
        * исключить из обхода любой файл по имени целиком (например, вернуть
          `if path.name in ("hexgrid.py", "rogue.py")`) — сканер перестанет видеть
          нарушителя;
        * сузить регулярку до `\\.split\\(\"_\"\\)` — форма с одинарными кавычками
          или пробелом перестанет ловиться, и вторая правда вернётся молча.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            src.mkdir()
            (src / "hexgrid.py").write_text(
                'def parse(tile_id):\n    return tuple(int(p) for p in tile_id.split("_")[1:])\n',
                encoding="utf-8",
            )
            (src / "rogue.py").write_text(
                "def coord(tile_id):\n"
                #: кавычки-одинарные и пробелы ВНУТРИ скобок: запрет относится к
                #: разбору, а не к стилю записи.
                "    ox = tile_id.split('_')[1]\n"
                "    oy = tile_id.split( '_' )[2]\n",
                encoding="utf-8",
            )
            offenders = _scan_local_tile_id_parsers(src)
        caught = {o.split(":")[0] for o in offenders}
        self.assertIn(
            "rogue.py", caught,
            "сканер перестал видеть нарушителя: пустой реестр больше не обвинитель",
        )
        self.assertNotIn(
            "hexgrid.py", caught,
            "сканер считает нарушителем единственное место, где разбор законен",
        )
        self.assertEqual(
            sorted(o for o in offenders if o.startswith("rogue.py:")),
            ["rogue.py:2", "rogue.py:3"],
            f"сканер пропустил строку разбора в rogue.py: {offenders}",
        )

    def test_the_engine_neighbour_asks_hexgrid(self) -> None:
        """`engine/seat.py` спрашивает `hexgrid`, а не держит свою копию вопроса.

        Своя копия смежности в движке — это ровно тот дефект, который чинился:
        `send_sally` по гексу и `send_party` по манхэттену на одних данных.
        """
        from hillcourt.engine import seat

        self.assertIs(
            seat._adjacent("t_00_01", "t_01_00"),
            tile_ids_are_neighbor("t_00_01", "t_01_00"),
        )
        self.assertFalse(
            seat._adjacent("t_00_00", "t_02_00"),
            "движок вернулся к манхэттену: у клетки появилось соседство, которого нет",
        )

    def test_parsing_rejects_a_foreign_name(self) -> None:
        """`parse_tile_id` — единственный разбор формата, и он строгий."""
        self.assertEqual(parse_tile_id("t_02_03"), parse_tile_id("t_02_03"))
        self.assertIsNone(parse_tile_id("household:hh_family_01"))
        self.assertIsNone(parse_tile_id("t_2"))
        self.assertIsNone(parse_tile_id(""))
        self.assertIsNone(parse_tile_id(42))


class TestTheOrderTheLordsOwnStillMovesGrain(unittest.TestCase):
    """Сторож: правка отказа не превратила неисполненный приказ в исполнившийся."""

    def test_a_possible_grant_still_moves_grain(self) -> None:
        """Отказ не должен стать «всё проходит молча»: годный приказ переводит зерно.

        Мутация, которая обязана ронять тест: заменить перехват на
        `except Exception: pass` и забыть про перевод — тест упадёт на амбаре.
        """
        world = load_scenario(STAND, seed=SEED)
        for entry in world.script:
            if int(entry.get("at_month", 0)) == 1:
                _apply_script_entry(world, entry)
        before = world.get_stock(COURT).amounts.get(GRAIN, 0.0)
        _apply_script_entry(
            world,
            {"at_month": 1, "action": "grant_grain", "household": VILLAGER,
             "amount": 60.0},
        )
        records = [r for r in world.player_actions if r.get("action") == "grant_grain"]
        self.assertEqual(len(records), 1)
        self.assertFalse(
            records[0].get("refused"),
            f"годный приказ отказан: {records[0].get('reason')}",
        )
        self.assertLess(
            world.get_stock(COURT).amounts.get(GRAIN, 0.0), before,
            "годный приказ не исполнился, а записался как исполненный",
        )


if __name__ == "__main__":
    unittest.main()
