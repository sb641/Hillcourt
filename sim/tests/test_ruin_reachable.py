"""Проигрыш — состояние, а не строчка в логе (ADR 0209).

Четыре обвинителя, и каждый закрывает одну из форм брака:

1. **Обе стороны.** `True` на разоряющемся мире и `False` на здоровом. Проверка
   только на одной стороне фиктивна, поэтому здесь оба мира — настоящие прогоны
   (`v0_ruin_empty_village` / `v0_ruin_bankrupt` против шести живых сценариев).
2. **Порог, а не константа «проигрыш = True».** Мутации, которые обязаны ронять
   тест, перечислены в `TestMutationsMustBreakTheCheck` и проверяются **исполнением**
   подменённого закона, а не декларацией: `ABANDONED_LEFT_MIN = 1`,
   `ABANDONED_SHARE = 0.0`, `FAMILY_HUNGER_MONTHS = 999` и защёлка, которая
   никогда не защёлкивается, обязаны оставить оба разоряющихся мира `False`.
   Тавтология (`if X > 0: X = 0`) проверкой не считается: подменяется ЗАКОН, а не
   результат.
3. **И-6.** Один seed дважды — один `state_hash` (проигрыш детерминирован), другой
   seed — другой.
4. **И-1.** `matter_delta` = 0 на обоих проигрышных мирах: проигрыш не имеет права
   двигать материю, даже когда проигрышную ситуацию создают приказы игрока.

Проверка: `PYTHONPATH=sim/src python3 -m unittest sim.tests.test_ruin_reachable -v`
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine import ruin
from hillcourt.engine.tick import PHASES, run_month
from hillcourt.news.views import build_player_view
from hillcourt.runner import _apply_script_entry
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "design" / "scenarios"

SEED = 1729
#: Горизонт приёмки: 24–60 месяцев (ADR 0209 §Замер; горизонт 60 обязателен,
#: потому что барщина недоимки открывает дверь ухода на 38-м месяце — ADR 0184 п. 1).
HORIZON_SHORT = 24
HORIZON_LONG = 60

#: Разоряющиеся миры — по одному на проигрыш. Оба написаны как ЧЕТЫРЕ кнопки игрока
#: в `script:`, а не как подкрученный старт, поэтому проигрыш достижим в игре.
RUIN_EMPTY_VILLAGE = SCENARIOS / "v0_ruin_empty_village.yml"
RUIN_BANKRUPT = SCENARIOS / "v0_ruin_bankrupt.yml"

#: Здоровые миры и горизонты. `v0_large_village` прогоняется на 24 месяцах, а не на
#: 60: в нём 36 дворов, и **ни один не может уйти** (`can_leave: false` у всех —
#: деревенские вилланы и коттеры). Это проверяется структурно и мгновенно
#: (`TestTheLossIsStructurallyUnreachableThere`), а не полутора минутами прогона.
HEALTHY_MATRIX: tuple[tuple[str, int, int], ...] = tuple(
    (name, months, seed)
    for name in (
        "start_stand",
        "v0_hill_and_salt",
        "v0_native_village",
        "v0_two_settlements",
        "v0_shire",
        "v0_large_village",
    )
    for months in (HORIZON_SHORT, HORIZON_LONG)
    for seed in (SEED, 7)
    if not (name == "v0_large_village" and months == HORIZON_LONG)
)


def play(scenario: Path, months: int, seed: int = SEED):
    """Прогнать мир сценария, исполняя приказы игрока, как это делает runner."""
    world = load_scenario(scenario, seed=seed)
    for month in range(1, months + 1):
        for entry in world.script:
            if int(entry.get("at_month", 0)) == month:
                _apply_script_entry(world, entry)
        run_month(world)
    return world


#: Кэш только для ЧТЕНИЯ. Мутации закона имеют право менять проверку, но не имеют
#: права видеть мир, прогнанный под другим законом, — иначе «мутация не поймана»
#: означала бы «проверка не посмотрела». Поэтому кэшируется только то, что проверка
#: затем только читает, и никогда не то, что она потом меняет.
_READONLY_CACHE: dict[tuple[str, int, int], object] = {}


def played_ro(scenario: Path, months: int, seed: int = SEED):
    """`play` с кэшем для ЗДОРОВЫХ миров (долгие прогоны, всегда одинаковые)."""
    key = (scenario.name, months, seed)
    world = _READONLY_CACHE.get(key)
    if world is None:
        world = play(scenario, months, seed)
        _READONLY_CACHE[key] = world
    return world


def first_month_where(predicate, world, months: int) -> int | None:
    """Первый месяц, на котором предикат стал истинным (0 — не стал)."""
    for month in range(1, months + 1):
        for entry in world.script:
            if int(entry.get("at_month", 0)) == month:
                _apply_script_entry(world, entry)
        run_month(world)
        if predicate(world):
            return month
    return None


class TestLossIsAStateOnARuinedWorld(unittest.TestCase):
    """Разорящийся мир: оба закона дают True, и это видно как состояние."""

    def test_emptied_village_loses_by_departure(self) -> None:
        """Сеньор отобрал надел у деревни — деревня запустела, проигрыш наступил."""
        world = play(RUIN_EMPTY_VILLAGE, HORIZON_SHORT)
        self.assertTrue(
            ruin.peasants_left(world),
            "деревня у ясеня потеряла дворов, а закон молчит: проигрыш не состояние",
        )
        self.assertIn(
            "ash_village",
            [item.settlement_id for item in ruin.abandoned_settlements(world)],
            "проигрыш защёлкнулся, но не на том поселении",
        )

    def test_empty_village_loss_is_latched_and_does_not_come_back(self) -> None:
        """Защёлка: проигрыш, случившись, не отменяется.

        Мгновенная проверка без защёлки позволяла бы отмену: `left_count` монотонен,
        так что здесь это проверяется таблицей, а не удачей прогона.
        """
        world = play(RUIN_EMPTY_VILLAGE, HORIZON_SHORT)
        crossed = first_month_where(
            lambda w: ruin.left_count(w, w.settlements["ash_village"]) >= 2,
            load_scenario(RUIN_EMPTY_VILLAGE, seed=SEED),
            HORIZON_SHORT,
        )
        self.assertIsNotNone(crossed, "деревня не потеряла ни двора — замер ADR 0209 не воспроизводится")
        self.assertTrue(ruin.peasants_left(world))
        self.assertEqual(
            world.stats.get(ruin.RUIN_PEASANTS_LEFT), 1.0,
            "защёлка проигрыша записана не тем значением",
        )

    def test_sold_demesne_bankrupts_the_family(self) -> None:
        """Сеньор распродал домен и кормил деревню из последнего — семья не ест."""
        world = play(RUIN_BANKRUPT, HORIZON_LONG)
        self.assertTrue(
            ruin.family_bankrupt(world),
            "двор сеньора не ел, а закон молчит: банкротство не состояние",
        )
        self.assertGreaterEqual(
            world.stats.get(ruin.RUIN_FAMILY_BANKRUPT_MONTH, 0.0), 1.0,
            "месяц банкротства не записан",
        )

    def test_families_and_villages_are_two_different_laws(self) -> None:
        """Проигрыши не сливаются: каждый мир теряет только своё."""
        empty = play(RUIN_EMPTY_VILLAGE, HORIZON_SHORT)
        bankrupt = play(RUIN_BANKRUPT, HORIZON_LONG)
        self.assertTrue(ruin.peasants_left(empty))
        self.assertFalse(ruin.family_bankrupt(empty))
        self.assertTrue(ruin.family_bankrupt(bankrupt))
        self.assertFalse(ruin.peasants_left(bankrupt))


class TestHealthyWorldDoesNotLose(unittest.TestCase):
    """Вторая половина приёмки: на здоровом мире проигрыша нет за 24–60 месяцев.

    Без этой половины доказано только, что проигрыш бывает, а не то, что игра вообще
    играбельна. Шесть сценариев, два горизонта, четыре сида.
    """

    def test_healthy_scenarios_do_not_lose(self) -> None:
        failures: list[str] = []
        for name, months, seed in HEALTHY_MATRIX:
            world = played_ro(SCENARIOS / f"{name}.yml", months, seed)
            if ruin.is_lost(world):
                failures.append(
                    f"{name} {months}м seed={seed}: "
                    f"family={ruin.family_bankrupt(world)} "
                    f"abandoned={[a.settlement_id for a in ruin.abandoned_settlements(world)]}"
                )
        self.assertEqual(
            failures, [],
            "проигрыш сработал на здоровом мире — порог занижен: " + "; ".join(failures),
        )

    def test_healthy_world_empties_only_single_household_hamlets(self) -> None:
        """Обоснование пола `ABANDONED_LEFT_MIN`: в здоровом мире пустеет хутор из
        ОДНОГО двора, и больше нигде.

        Если это перестанет быть так, пол надо менять — и тест обязан заметить, а не
        молча пропустить новую пустоту. Проверка идёт по каждому сценарию своему
        (`v0_native_village` пустеет не тот хутор, что `v0_hill_and_salt`).
        """
        worst: list[tuple[str, str, int]] = []
        for name in ("v0_hill_and_salt", "v0_native_village", "v0_shire"):
            world = played_ro(SCENARIOS / f"{name}.yml", HORIZON_LONG)
            for settlement in ruin.barony_settlements(world):
                left = ruin.left_count(world, settlement)
                if left > 0:
                    worst.append((name, settlement.id, left))
                    self.assertFalse(
                        ruin.settlement_is_abandoned(world, settlement.id),
                        f"{name}:{settlement.id} объявлен оставленным в здоровом мире",
                    )
        self.assertTrue(
            worst,
            "за 60 месяцев не пустеет ни один хутор — замер ADR 0209 не воспроизводится",
        )
        for name, settlement_id, left in worst:
            with self.subTest(scenario=name, settlement=settlement_id):
                self.assertLessEqual(
                    left, ruin.ABANDONED_LEFT_MIN - 1,
                    f"{name}:{settlement_id} пустеет сразу на {left} двора — пол "
                    "ABANDONED_LEFT_MIN ниже измеренного и пропускает проигрыш",
                )

    def test_sheriff_carries_a_departure_counter_from_month_one(self) -> None:
        """«По ходу» — значит по ходу: счётчик уходов есть в вести с первого месяца."""
        world = played_ro(SCENARIOS / "v0_shire.yml", HORIZON_SHORT)
        view = build_player_view(world, world.clock.date)
        sheriff = [
            entry
            for entry in view.entries
            if entry.facts.get("departed_households") is not None
        ]
        self.assertTrue(sheriff, "в отчёте шерифа нет счётчика уходов — игрок узнаёт постфактум")
        first = sheriff[0]
        self.assertEqual(first.facts["departed_households"], 0, "месяц 1: ушёл кто-то без причины")
        self.assertIn("left_by_settlement", first.facts, "шериф не называет, откуда ушли")


class TestBaronyDoesNotLose(unittest.TestCase):
    """Баронство 100×100 — самый большой мир, и самый медленный (600 жителей)."""

    def test_barony_100_does_not_lose(self) -> None:
        world = played_ro(SCENARIOS / "v0_barony_100.yml", HORIZON_SHORT, seed=SEED)
        self.assertFalse(
            ruin.is_lost(world),
            "баронство проиграло за 24 месяца: "
            f"family={ruin.family_bankrupt(world)} "
            f"abandoned={[a.settlement_id for a in ruin.abandoned_settlements(world)]}",
        )


def _clear_latches(world) -> None:
    """Стереть защёлки проигрыша, оставив мир как есть.

    Защёлка — часть проверки, а не часть мира-подложки: `family_bankrupt` и
    `peasants_left` читают ИМЕННО её. Чтобы мутация закона была видна, защёлку надо
    снять и пересчитать закон на том же мире (`ruin.phase_ruin`).
    """
    for key in [
        key for key in world.stats if key.startswith(ruin.RUIN_ABANDONED_PREFIX)
    ]:
        world.stats.pop(key, None)
    world.stats.pop(ruin.RUIN_PEASANTS_LEFT, None)
    world.stats.pop(ruin.RUIN_FAMILY_BANKRUPT, None)
    world.stats.pop(ruin.RUIN_FAMILY_BANKRUPT_MONTH, None)


def _mutated_law(world, name: str, value) -> bool:
    """Пересчитать закон с подменённой константой; вернуть «проигран ли мир»."""
    from contextlib import contextmanager

    @contextmanager
    def _patched():
        original = getattr(ruin, name)
        setattr(ruin, name, value)
        try:
            yield
        finally:
            setattr(ruin, name, original)

    _clear_latches(world)
    with _patched():
        ruin.phase_ruin(world)
        return ruin.is_lost(world)


class TestMutationsMustBreakTheCheck(unittest.TestCase):
    """Мутация закона обязана ронять проверку. Проверяется ИСПОЛНЕНИЕМ.

    Ни одной тавтологии: подменяется **константа закона**, а утверждается, что после
    подмены приёмка перестаёт быть зелёной. Мутация, после которой тест остался бы
    зелёным, хуже отсутствующей проверки.

    | мутация | что обязано сломаться |
    |---|---|
    | `ABANDONED_LEFT_MIN = 1` | ЗДОРОВЫЙ мир начнёт проигрывать (хутор из одного двора) |
    | `ABANDONED_LEFT_MIN = 0` и `ABANDONED_SHARE = 0.0` | ЗДОРОВЫЙ мир проиграет **всегда** — это и есть тавтология `X > 0 → X = 0` |
    | `ABANDONED_SHARE = 1.0` | РАЗОРЯЮЩИЙСЯ мир перестанет проигрывать (порог недостижим) |
    | `FAMILY_HUNGER_MONTHS = 999` | РАЗОРЯЮЩИЙСЯ мир перестанет проигрывать по банкротству |
    | защёлки нет вовсе | ни один мир не проиграет никогда |

    Отдельно замечено, почему в таблице нет мутации «`ABANDONED_SHARE = 0.0`
    в одиночку»: условия закона соединены **И**, и пол держит вторую границу, поэтому
    нулевая доля сама по себе мир не топит. Тавтологией становится только обнуление
    обоих — и она проверяется.

    Мутация применяется к СВЕЖЕМУ миру и пересчитывает закон через
    `ruin.phase_ruin`, а не поверх защёлки: иначе подмена константы была бы
    невидимой, и «мутация не поймана» означала бы «проверка не посмотрела».
    """

    def test_floor_of_one_makes_a_healthy_hamlet_a_loss(self) -> None:
        """Пол держит здоровый мир: без него запустевший хутор — проигрыш."""
        world = play(SCENARIOS / "v0_hill_and_salt.yml", HORIZON_LONG)
        self.assertFalse(ruin.is_lost(world), "холм и соль не проигрывают — база для мутации")
        emptied = [
            settlement.id
            for settlement in ruin.barony_settlements(world)
            if ruin.left_count(world, settlement) >= 1
        ]
        self.assertTrue(
            emptied,
            "за 60 месяцев не пустеет ни один хутор — замер ADR 0209 не воспроизводится",
        )
        self.assertTrue(
            _mutated_law(world, "ABANDONED_LEFT_MIN", 1),
            "снятый пол никого не поймал: проверку принимать нельзя, она фиктивна",
        )

    def test_both_thresholds_zeroed_is_a_tautology(self) -> None:
        """Пол и доля, обнулённые вместе, — это `if X > 0: X = 0.0` своими словами.

        Такую проверку нельзя принять: она даёт `True` всегда, то есть на любом мире,
        включая тот, где ни один двор не ушёл. Здесь она обязана быть поймана.
        """
        world = play(SCENARIOS / "v0_hill_and_salt.yml", HORIZON_LONG)
        self.assertFalse(ruin.is_lost(world))
        _clear_latches(world)
        original_min = ruin.ABANDONED_LEFT_MIN
        original_share = ruin.ABANDONED_SHARE
        ruin.ABANDONED_LEFT_MIN = 0
        ruin.ABANDONED_SHARE = 0.0
        try:
            ruin.phase_ruin(world)
            tautology = ruin.is_lost(world)
        finally:
            ruin.ABANDONED_LEFT_MIN = original_min
            ruin.ABANDONED_SHARE = original_share
        self.assertTrue(
            tautology,
            "обнулённый закон НЕ топит мир — значит, проверка всё равно смотрит "
            "на уход, и тавтологией её назвать было бы неверно",
        )
        _clear_latches(world)
        self.assertFalse(ruin.is_lost(world), "подготовка испорчена")

    def test_unreachable_share_stops_firing_and_that_is_detected(self) -> None:
        """Порог 1.0 недостижим по закону `can_leave` — и приёмка это замечает."""
        world = play(RUIN_EMPTY_VILLAGE, HORIZON_SHORT)
        self.assertTrue(ruin.is_lost(world), "база: деревня запустела")
        self.assertFalse(
            _mutated_law(world, "ABANDONED_SHARE", 1.0),
            "недостижимый порог 1.0 всё ещё объявляет проигрыш — константа фиктивна",
        )

    def test_absurd_family_threshold_is_detected(self) -> None:
        """Порог банкротства — не «любое число»: 999 месяцев не наступают."""
        world = play(RUIN_BANKRUPT, HORIZON_LONG)
        self.assertTrue(ruin.family_bankrupt(world), "база: семья разорилась")
        self.assertFalse(
            _mutated_law(world, "FAMILY_HUNGER_MONTHS", 999),
            "порог в 999 месяцев не пойман — значит, проверка не смотрит на голод",
        )

    def test_a_ruin_phase_that_latches_nothing_is_detected(self) -> None:
        """Самая честная мутация: защёлка не наступает НИКОГДА.

        Мгновенные меры при этом верны — двор лорда действительно голодал, деревня
        действительно пустела. Ломается только защёлка, то есть ровно то, что делает
        проигрыш состоянием. Если такое проходит, проверка измеряет не проигрыш.
        """
        world = play(RUIN_BANKRUPT, HORIZON_LONG)
        lost_month = int(world.stats[ruin.RUIN_FAMILY_BANKRUPT_MONTH])
        self.assertTrue(ruin.is_lost(world), "база: семья разорилась")
        self.assertGreater(lost_month, 0, "месяц банкротства не записан")
        _clear_latches(world)
        self.assertFalse(
            ruin.is_lost(world), "пустая защёлка не поймана — состояние не проверяется"
        )

    def test_the_moment_measure_and_the_latch_agree(self) -> None:
        """В месяц банкротства мгновенная мера обязана быть истинна.

        Без этого защёлку можно было бы защёлкивать по чему угодно — и тест прошёл бы,
        не показав, что проигрыш вырос из голода, а не из флажка.
        """
        world = load_scenario(RUIN_BANKRUPT, seed=SEED)
        lost_month = None
        event_date = None
        for month in range(1, HORIZON_LONG + 1):
            for entry in world.script:
                if int(entry.get("at_month", 0)) == month:
                    _apply_script_entry(world, entry)
            # Дата СОБЫТИЯ, а не календарь после тика: `phase_record` двигает часы
            # в конце месяца, и защёлка ставится по дате, которую тик обрабатывал.
            event_date = world.clock.date
            run_month(world)
            if ruin.family_bankrupt(world):
                lost_month = month
                break
        self.assertIsNotNone(lost_month, "банкротство не наступило за 60 месяцев")
        self.assertTrue(
            ruin.family_bankrupt_now(world),
            f"в {lost_month}-м месяце защёлка есть, а двор сеньора не голодает: "
            "защёлка не выросла из закона",
        )
        self.assertEqual(
            int(world.stats[ruin.RUIN_FAMILY_BANKRUPT_MONTH]),
            event_date.year * 12 + event_date.month,
            "месяц в защёлке не совпадает с датой события: весть укажет не тот месяц",
        )


class TestTheLossIsStructurallyUnreachableThere(unittest.TestCase):
    """Где проигрыш невозможен — не из-за порога, а из-за закона. Мгновенно.

    `can_leave: false` у `villein`/`cotter` (`design/catalogs/legal_statuses.yml`)
    означает, что прикреплённый двор не уходит сам никогда. Значит, баронство, где
    все дворы прикреплённые, **не может проиграть по уходу** ни при каком пороге.
    Это не дефект закона проигрыша, и обойти его можно только отменой `can_leave` —
    а она не в моей зоне. Тест фиксирует факт, чтобы следующий агент не искал порог,
    которого не существует, и не «чинил» это снятием закона о бегстве.
    """

    #: Измерено на дереве ADR 0209: сколько дворов книги корня могут уйти прочь.
    CAN_LEAVE_SHARE = {
        "start_stand": 1 / 7,
        "v0_hill_and_salt": 6 / 12,
        "v0_native_village": 2 / 3,
        "v0_two_settlements": 12 / 24,
        "v0_shire": 15 / 32,
        "v0_large_village": 0.0,
    }

    def test_can_leave_share_matches_the_measurement(self) -> None:
        from hillcourt.legal.regimes import can_leave

        for name, expected in sorted(self.CAN_LEAVE_SHARE.items()):
            with self.subTest(scenario=name):
                world = load_scenario(SCENARIOS / f"{name}.yml", seed=SEED)
                book = world.manors["manor_hill"].household_ids
                movable = [
                    hid for hid in book if can_leave(world, world.households[hid])
                ]
                self.assertEqual(
                    len(movable) / len(book), expected,
                    f"{name}: изменился состав книги — измерение достижимости устарело",
                )

    def test_a_barony_of_bond_people_cannot_lose_by_departure(self) -> None:
        """`v0_large_village`: 36 дворов, 0 могут уйти — порог тут ни при чём."""
        from hillcourt.legal.regimes import can_leave

        world = load_scenario(SCENARIOS / "v0_large_village.yml", seed=SEED)
        for settlement in ruin.barony_settlements(world):
            for household_id in settlement.household_ids:
                self.assertFalse(
                    can_leave(world, world.households[household_id]),
                    f"{household_id} вдруг стал уходить: баронство больше не заперто",
                )
        self.assertEqual(
            [sid for sid, share in self.CAN_LEAVE_SHARE.items() if share == 0.0],
            ["v0_large_village"],
            "нулевая доля ушла не из того сценария — таблица измерения поехала",
        )

    def test_no_barony_can_reach_a_share_of_one_half(self) -> None:
        """`ABANDONED_SHARE = 0.5` и выше недостижимы ни в одном баронстве.

        Это граница порога, а не вкус: у `v0_shire` в книге 32 двора, уйти могут 15,
        и доля 0.5 требует 16 ушедших. Поднимется ли она когда-нибудь — зависит от
        `can_leave`, а не от константы в `engine/ruin.py`.
        """
        reachable = {
            name: share for name, share in self.CAN_LEAVE_SHARE.items() if share >= 0.5
        }
        self.assertEqual(
            sorted(reachable), ["v0_hill_and_salt", "v0_native_village", "v0_two_settlements"],
            "набор баронств, где доля 0.5 достижима, изменился — границу порога надо пересмотреть",
        )
        self.assertLess(
            self.CAN_LEAVE_SHARE["v0_shire"], 0.5,
            "в шире доля 0.5 стала достижимой — верхняя граница порога устарела",
        )


class TestDeterminismAndMatter(unittest.TestCase):
    """И-6 и И-1 на обоих проигрышах."""

    def test_one_seed_two_runs_one_hash(self) -> None:
        for scenario, months in (
            (RUIN_EMPTY_VILLAGE, HORIZON_SHORT),
            (RUIN_BANKRUPT, HORIZON_LONG),
        ):
            with self.subTest(scenario=scenario.name):
                first = play(scenario, months)
                second = play(scenario, months)
                self.assertEqual(
                    first.state_hash(), second.state_hash(),
                    "один seed — разный мир: проигрыш недетерминирован (И-6)",
                )
                self.assertTrue(ruin.is_lost(first))

    def test_another_seed_another_world(self) -> None:
        for scenario, months in (
            (RUIN_EMPTY_VILLAGE, HORIZON_SHORT),
            (RUIN_BANKRUPT, HORIZON_LONG),
        ):
            with self.subTest(scenario=scenario.name):
                base = play(scenario, months, seed=SEED)
                other = play(scenario, months, seed=7)
                self.assertNotEqual(
                    base.state_hash(), other.state_hash(),
                    "разные сиды дали один мир (И-6)",
                )

    def test_ruin_moves_no_matter(self) -> None:
        """И-1: проигрыш — состояние, а не движение вещества."""
        for scenario, months in (
            (RUIN_EMPTY_VILLAGE, HORIZON_SHORT),
            (RUIN_BANKRUPT, HORIZON_LONG),
        ):
            with self.subTest(scenario=scenario.name):
                world = play(scenario, months)
                self.assertLess(
                    abs(world.ledger.delta(world.total_matter())), 1e-6,
                    "проигрыш сдвинул материю — он не состояние, а операция над веществом",
                )


class TestRuinPhaseIsWired(unittest.TestCase):
    """Фаза стоит в тике и после ухода, а не где попало."""

    def test_phase_ruin_runs_right_after_migrate(self) -> None:
        names = [phase.__name__ for phase in PHASES]
        self.assertIn("phase_ruin", names, "фазы проигрыша нет в тике")
        self.assertEqual(
            names.index("phase_ruin"), names.index("phase_migrate") + 1,
            "фаза проигрыша не сразу за уходом: уход этого месяца не учтён",
        )
        self.assertLess(
            names.index("phase_ruin"), names.index("phase_consume") + 99,
            "фаза проигрыша обязана быть до конца тика",
        )

    #: Виды вести, которые означают **сам проигрыш**. Предупреждение
    #: `settlement_at_risk` (ADR 0215 §Дыра 4) сюда НЕ входит: оно приходит
    #: раньше, по другому поводу и с другим смыслом, и наличие предупреждения не
    #: делает проигрыш «не единственным». Отбирать надо по виду, а не по
    #: наличию ключа `facts["ruin"]` — иначе добавление любой тревожной вести
    #: роняет проверку «проигрыш приходит один раз», то есть проверка запрещает
    #: будущие вести вместо того, чтобы их проверять.
    RUIN_MARKS = ("settlement_abandoned", "family_bankrupt")

    def test_ruin_news_reach_the_player_view(self) -> None:
        """И-3: проигрыш приходит вестью, а не строкой в логе прогона."""
        for scenario, months, mark in (
            (RUIN_EMPTY_VILLAGE, HORIZON_SHORT, ruin.RUIN_ABANDONED_PREFIX),
            (RUIN_BANKRUPT, HORIZON_LONG, ruin.RUIN_FAMILY_BANKRUPT),
        ):
            with self.subTest(scenario=scenario.name):
                world = play(scenario, months)
                view = build_player_view(world, world.clock.date)
                marks = [
                    entry
                    for entry in view.entries
                    if entry.facts.get("ruin") in self.RUIN_MARKS
                ]
                self.assertEqual(
                    len(marks), 1,
                    f"вести о проигрыше: {len(marks)} — должна быть ровно одна на проигрыш",
                )
                self.assertIn(
                    marks[0].source, ("messenger", "eye_from_hill"),
                    "весть о проигрыше пришла по несуществующему каналу",
                )
                self.assertLessEqual(
                    marks[0].delivery_date, world.clock.date,
                    "весть о проигрыше не доставлена игроку",
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
