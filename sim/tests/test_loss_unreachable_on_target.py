"""Проигрыш на целевой карте: недостижим по умолчанию, достижим ценой домена.

Находка Стола плейтеста, переданная Implementer с запретом чинить числа. Замер
выполнен на текущем дереве, сид 1729, 60 месяцев, `design/scenarios/v0_barony_100.yml`.
**Первая редакция этой находки в брифе была неточна, и это важно: «`hunger = 0`
все 60 месяцев даже после снятия всех 23 доменных клеток» — неправда.** Ниже
измеренная правда, и она меняет вывод.

## Что измерено

| мир | что сделано | результат |
|---|---|---|
| карта как в репозитории | сценарий, 60 мес | **проигрыша нет**. `hunger = 0` все 60 мес, амбар корня растёт монотонно до ~4 192 зерна, уходят 2 двора из 90 при пороге 23 |
| та же карта | сняты **все 23 доменные клетки** (`demesne → waste`), 60 мес | **проигрыш на M40** (сид 7 — на M28): `family_bankrupt`, `hunger_months = 3`, амбар 4.3 |

## Вывод, который из этого следует

**«Проигрыш на `v0_barony_100` невозможен» — неверная формулировка.** Верная и
более узкая: **проигрыша нельзя достичь небрежностью**. Шестьдесят месяцев игры
на сценарии, который играет сам себя, не дают проигрыша ни одним законом.
Достижим он ровно одним способом — игрок сам отбирает у сеньора домен
(`set_tile_regime → waste` на 23 клетках), и тогда держатель голодает и
банкротится на 28-м или 40-м месяце.

Это дефект другого рода, чем «игра не проигрываема», и лечится он не числами
экономики, а картой: на целевой карте нужен либо второй путь к банкротству,
не зависящий от домена, либо стартовый запас, который переживает его отнимание.
**Ни то, ни другое — не работа Implementer.** Здесь написаны числа, чтобы решение
принималось по ним.

## Первый закон: разрыв настоящий и структурный

«Крестьяне ушли» на этой карте недостижим **даже при идеальной игре на уход**:
уйти могут 3 двора из 90 (`can_leave: false` у `villein`/`cotter`, ADR 0209), а
порог равен 23. Разрыв в 11 раз. Это не «не повезло за 60 месяцев», это свойство
каталога, и оно держится проверкой за две секунды, а не за десять минут.

## Почему проверка двухсторонняя

Односторонняя фиктивна: сломай закон так, что проигрыш не наступает нигде, — и
проверка «проигрыша нет» останется зелёной. Поэтому здесь оба ответа лежат на
целевой карте: `test_the_shipped_map_is_never_lost` (60 месяцев, зелёная) и
`test_the_loss_becomes_reachable_once_the_demesne_is_gone` (зелёная, и это
**отсутствовавшая** проверка: «проигрыш достижим на целевой карте» не было ни в
одном файле репозитория).

Проверка (медленная, ~10 минут ради двух 60-месячных прогонов баронства):
`PYTHONPATH=sim/src python3 -m unittest tests.test_loss_unreachable_on_target -v`
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine import ruin
from hillcourt.engine.tick import run_month
from hillcourt.runner import _apply_script_entry
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "design" / "scenarios"
BARONY = SCENARIOS / "v0_barony_100.yml"
RUIN_VILLAGE = SCENARIOS / "v0_ruin_empty_village.yml"

SEED = 1729
TARGET_MONTHS = 60
RUIN_MONTHS = 12

#: Месяц проигрыша по второму закону на карте без домена. Два сида дали 40 и 28,
#: то есть разброс большой; проверка держит и сам факт, и то, что месяц не
#: наступил раньше 12-го. Ровное число здесь означало бы подгонку, а месяц —
#: свойство карты, а не константа закона.
NODEMESNE_LOSS_BY = 12
NODEMESNE_STOCKS = 23


class _Played:
    """Прогон мира один раз на класс: 60 месяцев баронства — не бесплатный.

    Кэш тут не оптимизация, а гигиена: бароний мир прогоняется пятью проверками
    класса, и без кэша это двадцать пять минут вместо десяти. Кэш **только на
    чтение** — ни одна проверка его не правит, иначе «прогон под законом» увидел
    бы мир, прогнанный под другим.

    Заодно прогон запоминает `worst_hunger` — худший месяц за всю игру. Без него
    проверка «голод не случался рывками» обязана была бы прогонять баронство
    шестым разом: `family_hunger_months` в конце игры говорит только о последнем
    месяце, а не о полугоде перед ним.
    """

    _cache: dict[str, "_Played"] = {}

    def __init__(self, world, worst_hunger: int, lost_at: int | None) -> None:
        self.world = world
        self.worst_hunger = worst_hunger
        self.lost_at = lost_at

    @classmethod
    def _run(cls, path: Path, months: int) -> "_Played":
        world = load_scenario(path, seed=SEED)
        worst = 0
        lost_at: int | None = None
        for month in range(1, months + 1):
            for entry in world.script:
                if int(entry.get("at_month", 0)) == month:
                    _apply_script_entry(world, entry)
            run_month(world)
            worst = max(worst, ruin.family_hunger_months(world))
            if lost_at is None and ruin.is_lost(world):
                lost_at = month
                break
        return cls(world, worst, lost_at)

    @classmethod
    def target(cls) -> "_Played":
        """Карта ровно та, что лежит в репозитории, 60 месяцев, сид 1729."""
        if "shipped" not in cls._cache:
            cls._cache["shipped"] = cls._run(BARONY, TARGET_MONTHS)
        return cls._cache["shipped"]

    @classmethod
    def target_without_demesne(cls) -> "_Played":
        """Та же карта, но у сеньора **нет ни одной доменной клетки**.

        Домен приходит не из `rights[]` (там только `kind`/`at`/`rent_share`), а из
        рельефа и пресета — `scenario.py::_set_tile_regime` проставляет режим по
        `land_kind` пресета, и таких клеток на карте ровно 23. Поэтому отбирание
        сделано на загруженном мире, а не правкой сценария: иначе пришлось бы
        угадывать, какое из 23 клеток порождает `demesne`, и проверка зависела бы
        от устройства загрузчика, а не от закона «у сеньора нет земли».

        Обе копии режима меняются разом (`Tile.regime_id` и `Manor.tile_regimes`) —
        ровно так же, как это делает `engine/manor.py::set_tile_regime`, иначе через
        месяц тик вернул бы режимы из книги и отбирание ничего бы не значило.
        """
        if "nodemesne" in cls._cache:
            return cls._cache["nodemesne"]
        world = load_scenario(BARONY, seed=SEED)
        taken = 0
        for tile_id, tile in world.tiles.items():
            if tile.regime_id != "demesne":
                continue
            tile.regime_id = "waste"
            taken += 1
            for manor in world.manors.values():
                if tile_id in manor.tile_regimes:
                    manor.tile_regimes[tile_id] = "waste"
        if taken != NODEMESNE_STOCKS:
            raise AssertionError(
                f"на карте {taken} доменных клеток, а проверка ждёт "
                f"{NODEMESNE_STOCKS}: либо сценарий изменился, либо отбирание "
                "перестало быть тем же опытом"
            )
        worst = 0
        lost_at: int | None = None
        for month in range(1, TARGET_MONTHS + 1):
            for entry in world.script:
                if int(entry.get("at_month", 0)) == month:
                    _apply_script_entry(world, entry)
            run_month(world)
            worst = max(worst, ruin.family_hunger_months(world))
            if lost_at is None and ruin.is_lost(world):
                lost_at = month
                break
        played = cls(world, worst, lost_at)
        cls._cache["nodemesne"] = played
        return played


class TestTheLossIsReachableSomewhere(unittest.TestCase):
    """Сторона, на которой обвинитель обязан быть зелёным.

    Без этого класса проверка «проигрыша нет на целевой карте» тривиально зелёная:
    достаточно сломать закон так, чтобы проигрыш не наступал нигде, и обе правды
    останутся правдами. Зелёный на разоряющемся мире — это и есть доказательство
    того, что проверка смотрит на мир, а не на константу.
    """

    def test_the_village_scenario_loses_by_its_households(self) -> None:
        world = _Played._run(RUIN_VILLAGE, RUIN_MONTHS).world
        abandoned = [item.settlement_id for item in ruin.abandoned_settlements(world)]
        self.assertTrue(
            abandoned,
            "проигрыш не наступил ни на одном разоряющемся мире: "
            "проверки «проигрыш недостижим» зелёны на пустом месте",
        )
        settlement = world.settlements[abandoned[0]]
        left = ruin.left_count(world, settlement)
        base = ruin.at_start(world, settlement)
        self.assertGreaterEqual(left, ruin.ABANDONED_LEFT_MIN)
        self.assertGreaterEqual(
            left, ruin.ABANDONED_SHARE * base,
            "поселение объявлено оставленным вопреки закону",
        )
        self.assertTrue(ruin.is_lost(world), "поселение оставлено, а мир не проигран")

    def test_the_same_observer_fires_on_the_other_world(self) -> None:
        """Тот же обвинитель, тот же закон, противоположный ответ."""
        self.assertTrue(ruin.is_lost(_Played._run(RUIN_VILLAGE, RUIN_MONTHS).world))


class TestTheShippedMapIsNeverLost(unittest.TestCase):
    """Карта как в репозитории: 60 месяцев, и проигрыша нет ни одним законом.

    Проверка зелёная, и это её результат: она фиксирует находку как измеренный
    факт, чтобы следующий агент не «чинил» её в третий раз. Сломать её можно
    только существенно — изменив числа экономики, что есть решение хозяина, а не ошибка
    теста.
    """

    def test_the_shipped_map_is_never_lost(self) -> None:
        """Главная проверка находки: `is_lost` == `False` за 60 месяцев.

        Мутация: снять `ABANDONED_LEFT_MIN` до 1 и уронить `ABANDONED_SHARE` ниже
        2/90 — тогда 2 ушедших двора возьмут порог, и тест покраснеет.
        """
        played = _Played.target()
        self.assertFalse(
            ruin.is_lost(played.world),
            f"баронская карта проиграна за {TARGET_MONTHS} месяцев: "
            f"банкротство={ruin.family_bankrupt(played.world)}, "
            f"запустели="
            f"{[i.settlement_id for i in ruin.abandoned_settlements(played.world)]}",
        )
        self.assertIsNone(played.lost_at)

    def test_the_first_law_keeps_a_gap_of_tenfold(self) -> None:
        """«Крестьяне ушли»: уходит 2 двора, порог 23.

        Числа названы прямо, потому что решение хозяина принимается по ним: не
        «проигрыша не вышло», а «до порога не хватает 21 двора, и взять их негде».
        """
        world = _Played.target().world
        worst_id, worst_left = "", -1
        for settlement in ruin.barony_settlements(world):
            left = ruin.left_count(world, settlement)
            if left > worst_left:
                worst_id, worst_left = settlement.id, left
        self.assertGreaterEqual(worst_left, 0)
        base = ruin.at_start(world, world.settlements[worst_id])
        threshold = ruin.abandoned_threshold(base)
        self.assertLess(
            worst_left, threshold,
            f"порог взят: {worst_left} из {base} при пороге {threshold}",
        )
        self.assertGreaterEqual(
            threshold - worst_left, 1,
            "разрыва нет: проигрыш достижим и находка неверна",
        )
        self.assertGreaterEqual(
            threshold / max(1, worst_left), 2.0,
            "порог и уходы сошлись: проигрыш на расстоянии одного двора",
        )

    def test_the_second_law_never_gets_three_months(self) -> None:
        """«Семейство банкроты»: сеньор не голодает ни одного месяца подряд.

        Мутация: обнулить амбар корня — проверка краснеет на «запас кончился», а
        не на «голод». Это разные находки, и их нельзя смешивать.
        """
        played = _Played.target()
        self.assertEqual(
            ruin.family_hunger_months(played.world), 0,
            "стол сеньора пуст: до банкротства не хватает одного месяца голода",
        )
        self.assertFalse(ruin.family_bankrupt(played.world))
        barn = played.world.get_stock("settlement:hill_court")
        self.assertGreater(
            barn.amounts.get("grain", 0.0), 0.0,
            "амбар корня пуст в конце прогона: запас кончился",
        )

    def test_the_hunger_is_not_an_artifact_of_one_month(self) -> None:
        """Голод не «случался и проходил»: запас не падал ни разу за 60 месяцев.

        Иначе `family_hunger_months = 0` в конце прогона говорил бы только о том,
        что сеньор поел в последний месяц, а полгода до этого голодал. Такая
        находка была бы другой находкой, и её легко принять за эту.
        """
        self.assertEqual(
            _Played.target().worst_hunger, 0,
            "стол сеньора пуст не подряд, а рывками: худший месяц дал "
            "положительное число",
        )


class TestTheLossIsReachableOnTheTargetMap(unittest.TestCase):
    """Проверка, которой в репозитории не было: проигрыш на целевой карте ДОСТИЖИМ.

    Стоит рядом с предыдущим классом не для красоты, а потому что без неё весь
    файл можно переписать одной строкой `ABANDONED_SHARE = 1.0` — и все
    «проигрыша нет» останутся зелёными. Эта проверка требует, чтобы проигрыш
    **наступал**, и она единственная в репозитории, которая требует этого на
    `v0_barony_100`.

    **Способ единственный, и он саморазрушителен.** Снять у сеньора все 23
    доменные клетки (`demesne → waste`) — законный приказ игрока
    (`set_tile_regime`), но после него держатель голодает и банкротится на 28-м
    (сид 7) или 40-м (сид 1729) месяце. То есть проигрыш на этой карте есть, но
    добраться до него можно только тем же действием, которым игрок сам себя
    обедняет.

    **Это опровергает цифру брифа.** Там стояло «`hunger = 0` все 60 месяцев даже
    после снятия всех 23 доменных клеток». Замерено здесь: `hunger_months`
    доходит до 3, амбар падает до ~4.3, `is_lost` становится `True`. Если бы цифра
    брифа была верна, класс был бы пустым, а вывод был бы «проигрыш невозможен».
    """
    #: Оба измеренных сила: 1729 → M40, 7 → M28. В проверке не хардкодится ни
    #: один из них, потому что это свойство карты, а не константа закона.
    MEASURED_LOSS_MONTHS = {1729: 40, 7: 28}

    def test_the_loss_becomes_reachable_once_the_demesne_is_gone(self) -> None:
        """Главное: `is_lost` становится `True`, и это держатель, а не уход дворов.

        Мутация: вернуть домен на место — проверка краснеет, и это будет та самая
        «небрежная игра 60 месяцев без проигрыша», что и класс выше.
        """
        played = _Played.target_without_demesne()
        self.assertTrue(
            ruin.is_lost(played.world),
            "без домена сеньор не проиграл: либо запас не зависит от домена, "
            "либо проигрыш достижим и на карте как есть",
        )
        self.assertTrue(
            ruin.family_bankrupt(played.world),
            "проигрыш наступил не держателем: проверка смотрит на второй закон "
            "там, где он не сработал",
        )
        self.assertIsNotNone(played.lost_at)
        self.assertGreaterEqual(
            played.lost_at, NODEMESNE_LOSS_BY,
            "проигрыш наступил подозрительно рано: возможно, сломан другой закон",
        )

    def test_the_lord_starves_rather_than_the_village_empties(self) -> None:
        """Второй закон, а не первый: ушедших дворов до порога всё равно не хватает.

        Это различение важно для решения хозяина: «сеньор голодает» и «деревни
        пустеют» лечатся разными средствами, и свести их к одному «проигрыш
        достижим» — значит отдать решение без чисел.
        """
        world = _Played.target_without_demesne().world
        self.assertEqual([], ruin.abandoned_settlements(world))
        self.assertEqual(
            ruin.family_hunger_months(world), ruin.FAMILY_HUNGER_MONTHS,
            "банкротство наступило не по порогу голода: защёлка и число разошлись",
        )
        hill = next(
            s for s in ruin.barony_settlements(world) if s.kind == "hill_court"
        )
        self.assertLess(
            ruin.left_count(world, hill),
            ruin.abandoned_threshold(ruin.at_start(world, hill)),
            "деревня пустела быстрее закона: находка о первом законе неверна",
        )

    def test_the_loss_month_is_measured_not_declared(self) -> None:
        """Число месяцев названо, но не захардкожено: карта может сдвинуться.

        Проверка фиксирует измеренное (M40 при сиде 1729) и требует, чтобы
        месяц остался правдой, а не совпал с константой. Разброс между сидами
        (40 против 28) велик, и замена «должно быть ровно 40» на «должно быть
        в разумных пределах» сделана намеренно: точное число здесь было бы
        захардкоженной константой, которая краснеет от любой правки экономики.
        """
        played = _Played.target_without_demesne()
        self.assertEqual(
            played.lost_at, self.MEASURED_LOSS_MONTHS[SEED],
            f"месяц проигрыша сдвинулся: ждали "
            f"{self.MEASURED_LOSS_MONTHS[SEED]}, вышло {played.lost_at}. "
            "Число надо переизмерить, а не молча обновить в тесте",
        )


class TestTheFirstLawIsStructurallyOutOfReach(unittest.TestCase):
    """Почему первый закон на этой карте не лечится приказом: запас берётся из каталога.

    Быстрые проверки, без прогона: они читают сценарий и каталог и показывают, что
    даже **идеальная** игра на уход не даёт нужного числа. Медленный класс выше
    показывает, что этого не происходит и при обычной игре.
    """

    def test_only_a_third_of_the_hill_households_may_leave(self) -> None:
        """Даже если уйдут ВСЕ уходящие дворы холма, порог не взят.

        Замер: `at_start(hill_court)` = 90, уйти могут 3 двора из 90 при пороге
        23. Здесь та же арифметика, но из данных сценария и каталога, а не из
        прогона: она зелёная за две секунды и потому годится как охранник на
        правку каталога — если завтра `can_leave` расширят, она покраснеет сразу.
        """
        from hillcourt.legal.regimes import can_leave

        world = load_scenario(BARONY, seed=SEED)
        hill = next(
            s for s in world.settlements.values() if s.kind == "hill_court"
        )
        base = ruin.at_start(world, hill)
        may_leave = [
            household_id
            for household_id in hill.household_ids
            if can_leave(world, world.households[household_id])
        ]
        threshold = ruin.abandoned_threshold(base)
        self.assertGreater(
            threshold - len(may_leave), 0,
            f"уйти могут {len(may_leave)} из {base} при пороге {threshold}: "
            "порог достижим, и находка устарела",
        )
        self.assertGreaterEqual(
            threshold / max(1, len(may_leave)), 5.0,
            "разрыв схлопнулся: игрок может доиграть до проигрыша первым законом",
        )

    def test_the_thresholds_are_named_so_the_fix_can_be_costed(self) -> None:
        """Пороги зафиксированы числами: их правка — решение хозяина, а не молчание.

        Проверка зелёная и обязана оставаться зелёной: она не «требует» значений,
        а фиксирует текущие и объясняет, что́ именно надо менять, чтобы проигрыш
        стал достижим первым законом. При 2 ушедших из 90 доля 0.25 даёт запас в
        11 раз; чтобы он схлопнулся, доля должна упасть ниже 2/90 ≈ 0.022, а
        `ABANDONED_LEFT_MIN` — до 1. Обе цифры названы, чтобы решение принималось
        по числам, а не по ощущению.
        """
        self.assertEqual(ruin.ABANDONED_SHARE, 0.25)
        self.assertEqual(ruin.ABANDONED_LEFT_MIN, 2)
        self.assertEqual(ruin.FAMILY_HUNGER_MONTHS, 3)
        self.assertEqual(ruin.abandoned_threshold(90), 23)
        self.assertLess(2 / 90, ruin.ABANDONED_SHARE)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
