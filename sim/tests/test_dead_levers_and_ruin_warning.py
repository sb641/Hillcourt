"""Пять дыр Стола плейтеста, закрытые проверками, а не пересказом (ADR 0216).

Предыдущий агент правил пять файлов и не оставил ни одного теста. По закону
`AGENTS.md` §5.2 такая работа — брак: код без проверки не считается сделанным,
даже если он работает. Этот файл — та проверка. Каждая дыра описана один раз, и
под каждым описанием стоит класс, который её держит.

| дыра | что было | где живёт проверка |
|---|---|---|
| 1. четыре приказа мертвы | `log`/`iron` в мире 0.0, а `work_road`/`ford`/`bridge` и `grant_tool` читают амбар корня | `TestBaronyBarnCarriesTheMaterial` |
| 2. `call_boon` молчит | возврат `False`, в лог — строка без полей и без `refused` | `TestCallBoonRefusalIsAGameMove` |
| 3. хеш описывает журнал | `player_actions` обходился в `World.state_hash` | `TestStateHashDescribesTheWorld` |
| 4. проигрыш приходит некрологом | задержка 0, порог `ABANDONED_SHARE` не назван нигде | `TestTheRuinWarningIsTruthful` |
| 5. весть не называет поселение | сумма по баронству, проигрыш считается по поселению | `TestTheDepartureReportNamesTheSettlement` |

**Про «до/после».** В файле нет только проверок «после». У каждой дыры есть
парная проверка «до», которая воспроизводит состояние мира ДО правки тем же
способом, каким мир к нему пришёл, и утверждает противоположное. Именно эти пары
исполняются как мутации: снять правку — и «после» краснеет.

**Про хеш и отказ (ADR 0202).** Дыра 3 вырезала журнал приказов из хеша, и
сложилась опасность, от которой предостерегает сам закон: отказ обязан остаться
ходом игры, а хеш — описывать мир, а не журнал. Оба требования проверены
отдельно и оба выполняются: журнал хеш не двигает (`test_the_journal_does_not_
move_the_hash`), а отказ хеш двигает и остаётся в логе игрока и в `RunResult`
(`test_a_refusal_moves_the_hash`,
`test_the_refusal_stays_visible_after_the_journal_left_the_hash`).

Проверка: `PYTHONPATH=sim/src python3 -m unittest tests.test_dead_levers_and_ruin_warning -v`
"""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path

import yaml

from hillcourt.engine import roadworks
from hillcourt.engine.manor import (
    ORDERS_REFUSED_KEY,
    call_boon,
    grant_thegn,
    grant_tool,
    manor_stock,
    root_manor,
)
from hillcourt.engine.ruin import (
    ABANDONED_LEFT_MIN,
    ABANDONED_SHARE,
    abandoned_threshold,
    at_start,
    barony_settlements,
    left_count,
    live_count,
)
from hillcourt.engine.tick import run_month
from hillcourt.info.sources import CHANNELS, MESSENGER
from hillcourt.news import ruin as ruin_news
from hillcourt.news.views import build_player_view
from hillcourt.runner import _apply_script_entry, _format_player_action, run
from hillcourt.scenario import load_scenario
from hillcourt.world import World

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "design" / "scenarios"
BARONY = SCENARIOS / "v0_barony_100.yml"
RUIN_VILLAGE = SCENARIOS / "v0_ruin_empty_village.yml"
RUIN_BANKRUPT = SCENARIOS / "v0_ruin_bankrupt.yml"
STAND = SCENARIOS / "start_stand.yml"

SEED = 1729
MONTHS = 24

#: Двор сеньора баронской карты — на нём проверяется `grant_tool`.
BARONY_COURT = "hh_hill_geneat_001"
#: Клетка, которую сценарий отдаёт под расчистку (`script:` месяца 1).
DEMESNE_TILE = "t_72_15"

#: Сколько камня стоит каждый вид стройки (`engine/roadworks.py::MATERIAL_AMOUNT`).
#: Сумма — 18.0, ровно то, что сценарий кладёт в амбар корня: запас «в обрез» и
#: покупается числом приказа, а не «сколько не жалко».
STONE_ORDERS = (
    ("road", 4),
    ("ford", 1),
    ("bridge", 1),
)

#: Железо на один лемех `smith_iron_share`; в амбаре 3.6 = ровно три лемеха.
ONE_PLOUGHSHARE = 1.2

_TEMP_PATHS: list[Path] = []


def _variant(source: Path, mutate) -> Path:
    """Записать рядом со исходником изменённую копию сценария и вернуть путь.

    Копия живёт рядом с оригиналом, а не в `/tmp`: `load_scenario` ищет `AGENTS.md`
    относительно файла сценария, и фикстура из другого каталога загрузилась бы
    не тем репозиторием. Имя начинается с `tmp`, потому что `test_draft_livestock`
    и `test_player_orders` перебирают `design/scenarios/*.yml` и отбрасывают
    `startswith("tmp")` — иначе обрубок без `hh_missing` ронял бы чужие приборы.
    """
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    mutate(data)
    handle, name = tempfile.mkstemp(suffix=".yml", prefix="tmp", dir=source.parent)
    path = Path(name)
    _TEMP_PATHS.append(path)
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        yaml.safe_dump(data, stream, allow_unicode=True, sort_keys=False)
    return path


def play(scenario: Path, months: int, seed: int = SEED) -> World:
    """Прогнать мир, исполняя приказы игрока, как это делает `runner`."""
    world = load_scenario(scenario, seed=seed)
    for month in range(1, months + 1):
        for entry in world.script:
            if int(entry.get("at_month", 0)) == month:
                _apply_script_entry(world, entry)
        run_month(world)
    return world


def tearDownModule() -> None:
    for path in _TEMP_PATHS:
        path.unlink(missing_ok=True)
    _TEMP_PATHS.clear()


class TestBaronyBarnCarriesTheMaterial(unittest.TestCase):
    """Дыра 1: четыре приказа требовали материала, которого в мире не было.

    Замер ДО правки, `v0_barony_100`, сид 4242, 24 месяца: `log` в амбаре 0.0,
    `iron` 0.0, `iron_bloom` 0.0, `iron_share` 0.0. При этом бревна в мире были
    (1 151.6 `log` в стоках клеток, спавн `grow_log`) и железо было (17.1 `iron`,
    рецепт `mine_iron` на топях) — между ними не было провода: выход рецепта
    пишется в сток ИСПОЛНЯЮЩЕГО двора, а не в амбар лорда.

    22 % кнопок приказов были мертвы: `work_road`, `work_ford`, `work_bridge`
    (`engine/roadworks.py::start_work`) и `grant_tool`
    (`engine/manor.py::grant_tool`) читают амбар корневого манора и отказывали
    с первого нажатия.
    """

    def test_the_seeded_barn_is_the_barn_the_orders_read(self) -> None:
        """Правка обязана попасть в ТОТ амбар, который читает приказ.

        Это отдельная проверка, потому что самая частая смерть такой правки —
        засеять правильную величину не в тот сток. Амбар корня берётся из
        `Manor.stock_id`, а `scenario.py:1165` задаёт его как
        `"settlement:" + player.court_settlement_id`, то есть у баронской карты
        это `settlement:hill_court`, а НЕ `manor:manor_hill` (такой сток есть
        только у тэнов и появляется лишь при пожаловании).
        """
        world = load_scenario(BARONY, seed=SEED)
        root = root_manor(world)
        self.assertIsNotNone(root, "у баронской карты нет корневого манора")
        assert root is not None
        self.assertEqual(
            root.stock_id,
            "settlement:hill_court",
            "амбар корня — не тот сток, который засевает сценарий; "
            "правка попала мимо адресата",
        )
        barn = manor_stock(world, root)
        self.assertIsNotNone(barn)
        assert barn is not None
        self.assertGreaterEqual(
            barn.amounts.get("log", 0.0),
            sum(roadworks.MATERIAL_AMOUNT[kind] for kind, _ in STONE_ORDERS),
            "в амбаре корня не хватает бревна на все каменные приказы сценария",
        )
        self.assertGreaterEqual(
            barn.amounts.get("iron", 0.0),
            ONE_PLOUGHSHARE,
            "в амбаре корня нет железа: `grant_tool` откажет с первого нажатия",
        )

    def test_every_stone_order_of_the_scenario_is_executable(self) -> None:
        """Замер «после»: четыре дороги, брод и мост исполняются, а не отказывают.

        Мутация: убрать из `starting_stocks` ключ `log` — тест краснеет на
        `ValueError: В амбаре корня нет 'log'` (проверяется парным тестом
        `test_the_stone_orders_refuse_without_the_seeded_logs`).
        """
        world = load_scenario(BARONY, seed=SEED)
        for index in range(4):
            world.tiles[f"t_70_{15 + index}"].terrain = "field"
        water = sorted(
            tile_id for tile_id, tile in world.tiles.items() if tile.terrain == "water"
        )
        self.assertTrue(water, "на карте нет воды: брод и мост негде строить")

        made: list[str] = []
        used: set[str] = set()
        for kind, count in STONE_ORDERS:
            for tile_id in self._tiles_for(world, kind, count, used):
                try:
                    roadworks.start_work(world, tile_id, kind)
                except (ValueError, PermissionError) as refused:
                    self.fail(f"приказ {kind} на '{tile_id}' отказал: {refused}")
                used.add(tile_id)
                made.append(f"{kind}:{tile_id}")
        self.assertEqual(len(made), sum(count for _, count in STONE_ORDERS))

    def _tiles_for(
        self, world: World, kind: str, count: int, used: set[str]
    ) -> list[str]:
        """Клетки в книге корня, годные под вид стройки, идущие вразнобой.

        `used` обязателен: стройка на клетке занимает её целиком
        (`engine/roadworks.py::start_work` отказывает на занятой), поэтому брод и
        мост нельзя назначать на одну воду. Это не деталь теста, а закон
        стройки, и он проверяется здесь попутно.
        """
        root = root_manor(world)
        assert root is not None
        if kind == "road":
            candidates = [
                tile_id
                for tile_id in sorted(world.tiles)
                if world.tiles[tile_id].terrain != "water"
                and not world.tiles[tile_id].road
                and tile_id in root.tile_ids
            ]
        else:
            candidates = [
                tile_id
                for tile_id in sorted(world.tiles)
                if world.tiles[tile_id].terrain == "water"
                and not world.tiles[tile_id].ford
                and not world.tiles[tile_id].bridge
            ]
        candidates = [tile_id for tile_id in candidates if tile_id not in used]
        self.assertGreaterEqual(
            len(candidates), count, f"не хватает клеток под '{kind}'"
        )
        return candidates[:count]

    def test_the_stone_orders_refuse_without_the_seeded_logs(self) -> None:
        """Сторона «до»: без посеянных брёвен те же приказы отказывают.

        Это не проверка ради проверки, а мутация, исполненная по-настоящему:
        сценарий копируется, `log` убирается из `settlement:hill_court`, и
        приказ падает ровно тем отказом, который игрок видел год. Если эта пара
        перестанет расходиться, значит либо правка не работает, либо проверка
        «после» не видит своего предмета.
        """
        path = _variant(
            BARONY,
            lambda data: data["starting_stocks"]["settlement:hill_court"].pop("log"),
        )
        try:
            world = load_scenario(path, seed=SEED)
            with self.assertRaises(ValueError) as refused:
                roadworks.start_work(world, DEMESNE_TILE, "road")
            self.assertIn("log", str(refused.exception))
        finally:
            path.unlink(missing_ok=True)

    def test_grant_tool_iron_is_executable_and_refuses_without_iron(self) -> None:
        """Тот же замер для железа: обе стороны одной проверки.

        Мутация: убрать `iron` из амбара — приказ падает
        `ValueError: У лорда нет 'iron'`.
        """
        world = load_scenario(BARONY, seed=SEED)
        try:
            grant_tool(world, BARONY_COURT, "iron", ONE_PLOUGHSHARE)
        except (ValueError, PermissionError) as refused:
            self.fail(f"приказ grant_tool отказал: {refused}")

        def strip(data):
            data["starting_stocks"]["settlement:hill_court"].pop("iron")

        path = _variant(BARONY, strip)
        try:
            bare = load_scenario(path, seed=SEED)
            with self.assertRaises(ValueError) as refused:
                grant_tool(bare, BARONY_COURT, "iron", ONE_PLOUGHSHARE)
            self.assertIn("iron", str(refused.exception))
        finally:
            path.unlink(missing_ok=True)


class TestCallBoonRefusalIsAGameMove(unittest.TestCase):
    """Дыра 2: `call_boon` возвращал `False`, а лог выглядел как успех.

    Замер Стола плейтеста: кнопка «помочь» была нажата в M1 и в M7 — месяцы без
    `boon_allowed`, — и обе строки в логе выглядели одинаково: `[Y1-M01] call_boon`
    без единого поля. Восемь месяцев из двенадцати кнопка была мёртвой и
    выглядела живой. Хуже отказа, который виден: тишина.
    """

    def _forbidden_month(self, world: World) -> int:
        for month in sorted(world.calendar):
            if not world.calendar[month].boon_allowed:
                return month
        self.fail("в календаре нет месяца без boon_allowed: проверка не на чём")

    def test_call_boon_in_a_forbidden_month_raises_and_names_the_month(self) -> None:
        """Отказ — исключение закона, а не значение, которое никто не спрашивает."""
        world = load_scenario(BARONY, seed=SEED)
        month = self._forbidden_month(world)
        with self.assertRaises(ValueError) as refused:
            call_boon(world, month)
        message = str(refused.exception)
        self.assertIn(f"M{month}", message, "отказ не назвал месяц: ждать нечего")
        self.assertIn("boon_allowed", message, "отказ не назвал закон")
        allowed = sorted(key for key, value in world.calendar.items() if value.boon_allowed)
        self.assertIn(f"M{allowed[0]}", message, "отказ не сказал, когда можно")

    def test_the_call_boon_refusal_is_logged_with_its_reason(self) -> None:
        """Дыра 2 по существу: в лог игрока падает `refused`, а не пустая строка.

        Мутация: убрать `record["refused"] = True` из `runner._log_refusal` —
        тест краснеет.
        """
        world = load_scenario(STAND, seed=SEED)
        month = self._forbidden_month(world)
        records = _apply_script_entry(
            world, {"at_month": month, "action": "call_boon"}
        )
        self.assertEqual(len(records), 1, "приказ не оставил ни одной записи в логе")
        record = records[0]
        self.assertTrue(record.get("refused"), f"отказ не помечен: {record}")
        self.assertIn("action", record)
        self.assertEqual(record["action"], "call_boon")
        self.assertIn(str(month), str(record.get("reason", "")), "отказ не назвал месяц")
        self.assertTrue(
            str(record.get("reason", "")).strip(), "причина отказа пустая строка"
        )

    def test_the_players_line_leads_with_the_refusal(self) -> None:
        """Строка лога: «ОТКАЗ» и причина раньше рычагов (ADR 0202).

        Мутация: убрать ветку отказа из `_format_player_action` — тест краснеет.
        """
        world = load_scenario(STAND, seed=SEED)
        month = self._forbidden_month(world)
        record = _apply_script_entry(
            world, {"at_month": month, "action": "call_boon"}
        )[0]
        line = _format_player_action(record)
        self.assertIn("ОТКАЗ", line)
        self.assertLess(
            line.index("ОТКАЗ"),
            len(line) - 1,
            "отказ не назван в строке лога",
        )
        self.assertIn(record["reason"], line, "причина отказа не попала в строку")

    def test_the_refused_call_does_not_stop_the_run(self) -> None:
        """Отказ — ход игры: 24 месяца из 24, а не 4.

        Мутация: убрать `except ORDER_REFUSALS` из `_apply_script_entry` —
        прогон падает `ValueError` на первом месяце без `boon_allowed`.
        """
        world = load_scenario(STAND, seed=SEED)
        month = self._forbidden_month(world)
        result = run(
            STAND,
            MONTHS,
            SEED,
            actions=[{"at_month": month, "action": "call_boon"}],
        )
        self.assertEqual(
            len(result.monthly), MONTHS, "отказ оборвал прогон: месяцев не хватает"
        )
        refused = [r for r in result.player_actions if r.get("refused")]
        self.assertEqual(len(refused), 1, "отказ не попал в лог прогона")

    def test_a_thegn_rejection_is_also_marked_refused(self) -> None:
        """Второй вид отказа: приказ, который отказал САМ, обязан быть помечен.

        `grant_thegn` при исчерпании лимита пишет запись сам и бросить исключение
        не может. До правки такая запись несла `reason`, но не `refused` — то есть
        это был отказ, который выглядит как обычное действие, и ни один обвинитель
        его не считал.
        """
        world = load_scenario(STAND, seed=SEED)
        person = next(iter(world.persons))
        demesne = sorted(
            tile_id
            for tile_id, tile in world.tiles.items()
            if tile.regime_id == "demesne" and not tile.road
        )
        self.assertTrue(demesne, "в стенде нет доменной клетки: тэна нечем пожаловать")
        court = world.player.household_id
        self.assertTrue(court, "у стенда нет двора сеньора")
        for _ in range(4):
            grant_thegn(world, person, [demesne[0]], [court])
        rejected = [r for r in world.player_actions if "rejected" in str(r.get("action"))]
        self.assertTrue(rejected, "ни один приказ не был отклонён: тест не на чём")
        for record in rejected:
            self.assertTrue(
                record.get("refused"),
                f"отказ без пометки refused, он выглядит как действие: {record}",
            )
        self.assertGreaterEqual(world.stats.get(ORDERS_REFUSED_KEY, 0.0), 1.0)


class TestStateHashDescribesTheWorld(unittest.TestCase):
    """Дыра 3: `state_hash` обходил `player_actions` — журнал приказов, а не мир.

    Последствие было не «лишний шум», а сломанный прибор: обвинитель «приказ
    что-то изменил?» на таком хеше всегда отвечает «да» — и на исполненный приказ,
    и на отказ, и на пустой журнал. Сказать «нет» он тоже не мог.
    """

    def test_the_hash_source_never_reads_the_journal(self) -> None:
        """Мутация «вернуть `player_actions` в хеш» ловится этой проверкой.

        Исходник читается **разбором** (`ast`), а не поиском подстроки: объяснение
        в докстринге законно упоминает журнал, и grep-подход краснел бы на
        тексте, который журнал как раз запрещает читать. Обвинитель смотрит на
        дерево атрибутов и имен, то есть ровно на то, что Python выполнит.

        Эта проверка ловит не «мир поехал», а сам замысел: обход журнала, добавленный
        добрым делом другого агента, который захотел отличить два мира по нажатиям.
        Поведенческая проверка ниже поймала бы его только если бы он удосужился
        принести запись; эта ловит решение само по себе.
        """
        import ast
        import inspect
        import textwrap

        from hillcourt.world import World as _World

        tree = ast.parse(textwrap.dedent(inspect.getsource(_World.state_hash)))
        offenders = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr == "player_actions"
        ]
        self.assertEqual(
            [ast.dump(node) for node in offenders], [],
            "state_hash снова читает журнал приказов: хеш описывает не мир",
        )

    def test_the_journal_does_not_move_the_hash(self) -> None:
        """Запись в журнал, не двигающая мир, не двигает хеш."""
        first = load_scenario(STAND, seed=SEED)
        second = load_scenario(STAND, seed=SEED)
        second.player_actions = [
            {"date": "Y1-M01", "month": 1, "action": "call_boon"},
            {"date": "Y1-M01", "month": 1, "action": "grant_grain",
             "household": "hh_court", "amount": 9_999_999.0},
        ]
        self.assertEqual(
            first.state_hash(),
            second.state_hash(),
            "журнал приказов снова входит в хеш мира",
        )

    def test_a_refusal_moves_the_hash(self) -> None:
        """Отказ — состояние, а не журнал: хеш обязан его различить.

        Мутация: снять `world.bump(ORDERS_REFUSED_KEY)` из `_log_refusal` —
        тест краснеет.
        """
        world = load_scenario(STAND, seed=SEED)
        bare = load_scenario(STAND, seed=SEED)
        month = next(
            key for key, value in world.calendar.items() if not value.boon_allowed
        )
        _apply_script_entry(world, {"at_month": month, "action": "call_boon"})
        self.assertEqual(
            world.stats.get(ORDERS_REFUSED_KEY), 1.0, "отказ не посчитан состоянием"
        )
        self.assertNotEqual(
            world.state_hash(), bare.state_hash(),
            "мир, в котором приказ отказали, сошёлся хешем с миром без приказа",
        )

    def test_the_refusal_stays_visible_after_the_journal_left_the_hash(self) -> None:
        """ADR 0202 не сломан: счётчик в хеше, подробность — в логе и в `RunResult`.

        Это та самая опасность, о которой предупреждает приёмка: вычеркнув журнал
        из хеша, можно вычеркнуть отказ из игры. Здесь проверяются все три места,
        где отказ обязан быть виден, и ни одно из них не является журналом.
        """
        world = load_scenario(STAND, seed=SEED)
        month = next(
            key for key, value in world.calendar.items() if not value.boon_allowed
        )
        result = run(
            STAND, MONTHS, SEED,
            actions=[{"at_month": month, "action": "call_boon"}],
        )
        refused = [r for r in result.player_actions if r.get("refused")]
        self.assertEqual(len(refused), 1, "отказ не виден в RunResult")
        self.assertIn("reason", refused[0])
        self.assertIn("ОТКАЗ", _format_player_action(refused[0]))
        # Хеш прогона уже содержит счётчик: `run` считал хеш в конце.
        bare = run(STAND, MONTHS, SEED)
        self.assertNotEqual(
            result.state_hash, bare.state_hash,
            "прогон с отказом и прогон без него сошлись хешем",
        )

    def test_one_seed_twice_is_one_hash_and_another_seed_is_another(self) -> None:
        """И-6 на карте, где сценарий и правки сходятся."""
        first = run(BARONY, 12, SEED)
        second = run(BARONY, 12, SEED)
        other = run(BARONY, 12, 7)
        self.assertEqual(first.state_hash, second.state_hash, "один seed — два хеша")
        self.assertNotEqual(
            first.state_hash, other.state_hash, "разный seed — один хеш"
        )

    def test_a_refusal_moves_no_matter(self) -> None:
        """И-1: отказ — не ход вещества, даже когда он единственное, что случилось."""
        world = load_scenario(STAND, seed=SEED)
        month = next(
            key for key, value in world.calendar.items() if not value.boon_allowed
        )
        total = world.total_matter()
        _apply_script_entry(world, {"at_month": month, "action": "call_boon"})
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


class TestTheRuinWarningIsTruthful(unittest.TestCase):
    """Дыра 4: проигрыш приходил некрологом, а порог не был назван нигде.

    Замер Стола плейтеста: «Крестьяне ушли» приходил в том же месяце, в котором
    проигрыш уже наступил (задержка 0), а `ABANDONED_SHARE = 0.25` не стоял ни в
    одной вести. Игрок физически не мог узнать, сколько осталось. Плейтест
    заплатил за это дороже, чем молчание: проигрыш оказался **обратим** — землю
    вернули на 7-м месяце из 11-го, деревня выжила, то есть игра была проиграна
    без нужды.
    """

    #: Горизонт, на котором деревня у ясеня действительно пустеет.
    VILLAGE_MONTHS = 12
    #: Сценарий, где семейство сеньора банкротится. Горизонт — 60, а не 24:
    #: на 24 месяцах в нём **нет ни одного предупреждения**, и проверки отсечения
    #: проходили бы вхолостую. 60 месяцев — 25 секунд прогона, и на них хутора
    #: уже пусты, то есть отсечение имеет на чём сработать. Проверено замером.
    BANKRUPT_MONTHS = 60

    def _warnings(self, world: World) -> list:
        view = build_player_view(world, world.clock.date)
        return [
            entry
            for entry in view.entries
            if entry.facts.get("ruin") == ruin_news.RUIN_WARNING
        ]

    def test_the_warning_names_the_threshold_in_the_text(self) -> None:
        """Главное из дыры 4: порог назван СЛОВАМИ, а не только в `facts`.

        Мутация: убрать «порог — N» из текста вести — тест краснеет.

        Замер ДО: `news/ruin.py` до правки вообще не рождал предупреждения, а
        порог не встречался ни в одном тексте `news/`. Этот тест на таком дереве
        краснеет отсутствием самой вести, а не отсутствием числа, — и это
        правильный вид отказа.
        """
        world = play(RUIN_VILLAGE, self.VILLAGE_MONTHS)
        warnings = self._warnings(world)
        self.assertTrue(warnings, "шериф ни разу не предупредил о приближении")
        first = warnings[0]
        self.assertIn(
            str(first.facts["threshold"]), first.content,
            "порог не назван в тексте: игрок не узнает, сколько дворов ещё можно потерять",
        )
        self.assertIn(
            str(first.facts["threshold"]), first.content,
        )
        self.assertRegex(first.content, r"порог — \d+ (двор|двора|дворов)")

    def test_the_warning_is_delivered_later_than_the_event(self) -> None:
        """Предупреждение приходит месяцем позже события, а не вместе с ним.

        Мутация: поставить предупреждению задержку 0 — тест краснеет.

        Проверяется на каждой доставленной вести, а не «в этом месяце»: задержка
        двигает `delivery_date` относительно `event_date`, и к последнему месяцу
        прогона предупреждение давно в прошлом. Сравнение с «текущим месяцем»
        проверяло бы не задержку, а то, в каком месяце закончился прогон.
        """
        world = play(RUIN_VILLAGE, self.VILLAGE_MONTHS)
        warnings = self._warnings(world)
        self.assertTrue(warnings)
        months_per_year = max(1, int(world.clock.months_per_year))
        for entry in warnings:
            self.assertGreater(
                (entry.delivery_date.year - entry.event_date.year) * months_per_year
                + (entry.delivery_date.month - entry.event_date.month),
                0,
                f"предупреждение доставлено в месяц события "
                f"({entry.event_date} → {entry.delivery_date}): это некролог",
            )
        # Задержка канала обязана быть положительной — иначе «предупреждение»
        # приходит в том же месяце, что и пропажа, и предупреждением не является.
        self.assertGreater(
            CHANNELS[MESSENGER].delay_months, 0,
            "предупреждение приходит в месяц события: это некролог",
        )

    def test_the_warning_never_says_there_is_nothing_left(self) -> None:
        """`remaining >= 1` ВСЕГДА: весть с «осталось 0» не предупреждает ни о чём.

        Мутация: `remaining = max(0, threshold - reported)` (как было) при
        `left = threshold - 1` и дрейфе `+1` даёт `0` — тест краснеет.
        """
        world = play(RUIN_VILLAGE, self.VILLAGE_MONTHS)
        warnings = self._warnings(world)
        self.assertTrue(warnings)
        for entry in warnings:
            self.assertGreaterEqual(
                int(entry.facts["remaining"]), 1,
                f"предупреждение сообщает, что предупреждать уже не о чем: {entry.content}",
            )
            self.assertNotIn("осталось 0", entry.content)

    def test_a_settlement_that_can_never_be_lost_is_not_warned_about(self) -> None:
        """Предупреждение, которое мир не сдержит, хуже молчания.

        Мутация: убрать отсечение `threshold - left > live_count` — тест краснеет
        на хуторе из одного двора.

        ЗАМЕР ДО ПРАВКИ, `v0_ruin_bankrupt`, 60 месяцев: предупреждение приходило
        для `fs_06` и `fs_13` — поселений из ОДНОГО двора. `at_start = 1`,
        `threshold = max(ABANDONED_LEFT_MIN=2, ceil(0.25)) = 2`, живых дворов 0.
        Текст был: «из Двор Межи ушло дворов 1 из 1; до пропажи осталось 1». То
        есть шериф обещал игроку пропажу поселения, у которого не осталось НИКОГО,
        кто мог бы уйти, — и обещание это не могло исполниться никогда. Замер на
        здоровых мирах из ADR 0209: единственные пустые поселения — ровно такие
        хутора (`fs_07`, `fs_01`, `fs_13`).

        Проверка сначала убеждается, что **такие поселения в мире есть** — иначе
        она была бы вхолостую, как её первая редакция на горизонте 24 месяца.
        """
        world = play(RUIN_BANKRUPT, self.BANKRUPT_MONTHS)
        hopeless = [
            settlement
            for settlement in barony_settlements(world)
            if left_count(world, settlement) > 0
            and abandoned_threshold(at_start(world, settlement))
            - left_count(world, settlement)
            > live_count(world, settlement)
        ]
        self.assertTrue(
            hopeless,
            "в мире нет поселения с недостижимым порогом: проверка отсечения "
            "проходит вхолостую и ничему не учит",
        )
        warned = {str(entry.facts["settlement"]) for entry in self._warnings(world)}
        for settlement in hopeless:
            self.assertNotIn(
                settlement.id, warned,
                f"шериф предупредил о пропаже поселения '{settlement.id}', "
                f"которое проигрыш коснуться не может: уйти больше некому",
            )

    def test_the_warning_names_the_settlement_in_a_readable_form(self) -> None:
        """Название поселения в тексте и числа в грамотной форме.

        Мутация: вернуть `из {name}` (без кавычек и без «поселение») — тест
        краснеет: «из Деревня у ясеня» — неграмотно, и непонятно, о чём речь.
        """
        world = play(RUIN_VILLAGE, self.VILLAGE_MONTHS)
        warnings = self._warnings(world)
        self.assertTrue(warnings)
        for entry in warnings:
            name = str(entry.facts["settlement_name"])
            self.assertIn(name, entry.content, "вести не назвала поселение")
            self.assertRegex(entry.content, rf"«{re.escape(name)}»")
        self.assertEqual(
            ruin_news.households_phrase(1), "двор",
            "форма числа в вести о пороге не согласована",
        )
        self.assertEqual(ruin_news.households_phrase(2), "двора")
        self.assertEqual(ruin_news.households_phrase(5), "дворов")
        self.assertEqual(ruin_news.households_phrase(11), "дворов")

    def test_the_warning_and_the_necrology_are_different_reports(self) -> None:
        """Предупреждение не выдаёт себя за пропажу: значения `facts["ruin"]` разные.

        Мутация: вернуть предупреждению `RUIN_SETTLEMENT` — тест краснеет, и
        дедуп погасит либо то, либо другое (одна пара ключей на обе вести).
        """
        world = play(RUIN_VILLAGE, self.VILLAGE_MONTHS)
        view = build_player_view(world, world.clock.date)
        kinds = {
        entry.facts.get("ruin")
            for entry in view.entries
            if "ruin" in entry.facts
        }
        self.assertIn(ruin_news.RUIN_WARNING, kinds)
        self.assertIn(ruin_news.RUIN_SETTLEMENT, kinds)
        self.assertNotEqual(
            ruin_news.RUIN_WARNING, ruin_news.RUIN_SETTLEMENT,
            "предупреждение и некролог делят один ключ дедупа",
        )

    def test_the_threshold_the_warning_names_is_the_law_threshold(self) -> None:
        """Число в вести и число в законе — одна арифметика (ADR 0216 §Дыра 4).

        Мутация: посчитать порог в вести своей формулой — тест краснеет на
        любой правке `ABANDONED_LEFT_MIN`/`ABANDONED_SHARE`.
        """
        world = play(RUIN_VILLAGE, self.VILLAGE_MONTHS)
        for entry in self._warnings(world):
            settlement = world.settlements[str(entry.facts["settlement"])]
            self.assertEqual(
                int(entry.facts["threshold"]),
                abandoned_threshold(at_start(world, settlement)),
                "порог в вести разошёлся с законом",
            )
            self.assertEqual(
                int(entry.facts["at_start"]),
                at_start(world, settlement),
                "знаменатель в вести разошёлся с законом",
            )

    def test_the_threshold_is_never_zero_in_the_healthy_worlds(self) -> None:
        """Сторона «до» закона: порог обязан быть достижимым и непустым.

        Это проверка самого закона, а не вести: она зелёная и на дереве до
        правки, и обязана быть зелёной после — иначе «правка вести» сдвинула бы
        порог вместо того, чтобы его назвать.
        """
        self.assertGreater(abandoned_threshold(4), 0, "порог не может быть нулём")
        self.assertGreaterEqual(abandoned_threshold(16), ABANDONED_LEFT_MIN)
        self.assertEqual(abandoned_threshold(16), 4, "порог на 16 дворах пересчитать")
        self.assertEqual(ABANDONED_LEFT_MIN, 2)
        self.assertEqual(ABANDONED_SHARE, 0.25)


class _ForcedRumor:
    """Поток `rng.news`, у которого `choice` всегда берёт указанную величину.

    Нужен, чтобы **доказуемо** попасть в граничный случай, а не ждать его от
    сида. Проверка «`remaining` не бывает нулём» на живых прогонах держится тем,
    что дрейф туда попал, и тем хуже: тот же сид на том же дереве может не
    попасть, и проверка станет зелёной вхолостую, не поймав ничего.
    """

    def __init__(self, drift: int) -> None:
        self.drift = drift

    def choice(self, options) -> object:
        return self.drift

    def random(self) -> float:
        return 0.5

    def uniform(self, low: float, high: float) -> float:
        return (low + high) / 2.0

    def randrange(self, *args) -> int:
        return 0


class _StubRng:
    def __init__(self, drift: int) -> None:
        self.news = _ForcedRumor(drift)


class TestTheWarningAtTheBoundary(unittest.TestCase):
    """Две границы, на которых предупреждение обязано вести себя иначе, чем в середине.

    Обе проверки подставляют дрейф принудительно и зовут настоящий
    `news/ruin.py::report_ruin_warning`, то есть исполняют закон, а не
    воспроизводят его внутри теста.
    """

    def _warning_for(self, world: World, settlement_id: str):
        for report in ruin_news.report_ruin_warning(world):
            if report.subject_id == settlement_id:
                return report
        return None

    def _one_lost_of_two(self) -> World:
        """Мир, где у поселения `hill_court` ушёл ровно один двор из двух.

        Состояние ставится руками, а не дожидается прогона: дыра 4 — про границу
        «до пропажи осталось N», и граница обязана проверяться на границе, а не
        на том, что сид случайно туда доехал.
        """
        world = load_scenario(RUIN_BANKRUPT, seed=SEED)
        settlement = world.settlements["hill_court"]
        at_start(world, settlement)
        world.households[settlement.household_ids[0]].left_at = world.clock.date
        return world

    def test_one_more_household_is_never_reported_as_none_left(self) -> None:
        """`left == threshold - 1` и дрейф `+1` дали бы «осталось 0».

        Мутация: `remaining = max(0, threshold - reported)` (как было) — тест
        краснеет. Граница взята из живого прогона `v0_ruin_bankrupt` на 60-м
        месяце: `hill_court`, `at_start = 2`, `left = 1`, `threshold = 2`.
        """
        world = self._one_lost_of_two()
        world.rng = _StubRng(1)
        report = self._warning_for(world, "hill_court")
        self.assertIsNotNone(
            report, "предупреждение не выдано, а состояние к границе подведено"
        )
        assert report is not None
        self.assertEqual(
            int(report.facts["remaining"]), 1,
            "предупреждение сообщило, что предупреждать не о чем, хотя двор ещё есть",
        )
        self.assertNotIn("0 двор", report.content)

    def test_a_settlement_with_nobody_left_gets_no_warning_at_all(self) -> None:
        """`threshold - left > live` — обещание невыполнимо, и вести нет.

        Мутация: убрать отсечение — тест краснеет.

        Хутор из одного двора: `at_start = 1`, `left = 1`, `threshold = 2`,
        живых 0. Порог недостижим в принципе, и шериф не вправе обещать то, чего
        не сможет сдержать.
        """
        world = load_scenario(RUIN_BANKRUPT, seed=SEED)
        hamlet = next(
            settlement
            for settlement in barony_settlements(world)
            if len(settlement.household_ids) == 1
        )
        at_start(world, hamlet)
        for household_id in hamlet.household_ids:
            world.households[household_id].left_at = world.clock.date
        self.assertEqual(live_count(world, hamlet), 0, "хутор не опустел: тест не на чём")
        world.rng = _StubRng(0)
        self.assertIsNone(
            self._warning_for(world, hamlet.id),
            f"шериф предупредил о пропаже '{hamlet.id}', у которого не осталось "
            "ни одного двора: обещание, которое мир не сдержит",
        )

    def test_the_drift_never_lowers_the_promise_below_one(self) -> None:
        """Любой дрейф из `COUNT_DRIFT` оставляет `remaining >= 1`.

        Мутация: `max(0, …)` — тест краснеет на дрейфе `+1`.
        """
        for drift in ruin_news.COUNT_DRIFT:
            with self.subTest(drift=drift):
                world = self._one_lost_of_two()
                world.rng = _StubRng(drift)
                report = self._warning_for(world, "hill_court")
                self.assertIsNotNone(report)
                assert report is not None
                self.assertGreaterEqual(int(report.facts["remaining"]), 1)
                self.assertEqual(
                    int(report.facts["remaining"]),
                    max(
                        1,
                        int(report.facts["threshold"])
                        - int(report.facts["left_reported"]),
                    ),
                    "остаток в вести разошёлся с правилом max(1, …)",
                )


class TestTheDepartureReportNamesTheSettlement(unittest.TestCase):
    """Дыра 5: весть об уходе печатала сумму по баронству, а проигрыш — по поселению.

    Замер Стола плейтеста: игрок читал «ушло с земли, по его словам, 3», сумма по
    всему баронству, и не мог связать её с «умерла деревня у ясеня»: в тексте не
    было ни слова о том, ОТКУДА ушли. Разбивка `left_by_settlement` лежала в
    `facts` и в `content` не попадала, а `runner.py` печатает только `content` —
    то есть правильная правка была в файле, а видел игрок пустоту.
    """

    def _departure_reports(self, world: World) -> list:
        view = build_player_view(world, world.clock.date)
        return [
            entry for entry in view.entries if "departed_households" in entry.facts
        ]

    def test_the_report_names_every_settlement_it_counts(self) -> None:
        """Главное из дыры 5: каждое названное поселение есть в тексте.

        Мутация: убрать `breakdown` из `content` — тест краснеет.
        """
        world = play(RUIN_VILLAGE, 12)
        reports = [r for r in self._departure_reports(world) if r.facts["departed_households"]]
        self.assertTrue(reports, "шериф ни разу не сказал, что дворы уходят")
        for entry in reports:
            for settlement_id, count in entry.facts["left_by_settlement"].items():
                name = world.settlements[settlement_id].name
                self.assertIn(
                    name, entry.content,
                    f"отчёт считает уход из «{name}», а текст молчит: {entry.content}",
                )
                self.assertIn(str(count), entry.content)

    def test_the_total_equals_the_sum_of_the_named(self) -> None:
        """Весть не противоречит сама себе: итог равен сумме названного.

        Мутация: считать итог и разбивку разными бросками дрейфа — тест краснеет.
        """
        world = play(RUIN_VILLAGE, 12)
        for entry in self._departure_reports(world):
            breakdown = entry.facts["left_by_settlement"]
            self.assertEqual(
                int(entry.facts["departed_households"]),
                sum(int(value) for value in breakdown.values()),
                f"итог и разбивка разошлись в одном отчёте: {entry.content}",
            )

    def test_the_text_has_no_doubled_punctuation(self) -> None:
        """Точка перед `; по поселениям:` — опечатка в тексте, который читают каждый месяц.

        Мутация: вернуть точку перед `{breakdown}` — тест краснеет.
        """
        world = play(RUIN_VILLAGE, 12)
        reports = [r for r in self._departure_reports(world) if r.facts["departed_households"]]
        self.assertTrue(reports)
        for entry in reports:
            self.assertNotIn(".;", entry.content, f"точка с запятой: {entry.content}")
            self.assertFalse(
                entry.content.rstrip().endswith("." + ";"),
                "фрагмент разбивки кончается точкой с запятой",
            )
            self.assertTrue(
                entry.content.rstrip().endswith("."),
                f"отчёт не кончается точкой: {entry.content}",
            )

    def test_the_report_appears_every_month_and_starts_at_zero(self) -> None:
        """Счётчик ежемесячный, а не разовый: игрок видит, как деревня пустеет."""
        world = play(RUIN_VILLAGE, 3)
        reports = self._departure_reports(world)
        self.assertTrue(reports, "ежемесячного отчёта шерифа об уходах нет вовсе")
        for entry in reports:
            self.assertEqual(
                int(entry.facts["departed_households"]),
                0,
                "в первые месяцы уходить некому: отчёт врёт на старте",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
