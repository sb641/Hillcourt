"""Вести о проигрыше: игрок узнаёт о нём известием, а не строкой в логе (ADR 0209).

Инвариант И-3 («известие ≠ мир») делает выбор однозначным: проигрыш, о котором
игрок не узнал, — это не проигрыш, а состояние, о котором знает только отладчик.
Словарь `Report` при этом **не расширяется**: новая сущность онтологии не заводится
(И-8), потому что она и не нужна. `Report` уже несёт `source`, `subject_kind`,
`subject_id`, `facts` — а «поселение оставлено» и «семья не ест N месяцев» это
два значения `facts` и два значения `subject_kind`, не больше.

**Каналы — существующие, новых нет** (`info/sources.py::ALL_SOURCES` — закрытый
словарь v0, `test_news_no_omniscience::test_source_vocabulary_is_v0_set`):

| весть | канал | чей это голос | задержка | `confidence` | `noise` |
|---|---|---|---|---|---|
| стол сеньора пуст | `eye_from_hill` | сам держатель, свидетель — он | 0 | 1.0 | 0.0 |
| поселение оставлено | `messenger` | шериф баронии | 0 | 0.5 | 0.15 |
| **до пропажи осталось N** (ADR 0215 §Дыра 4) | `messenger` | шериф баронии | **1** | 0.5 | 0.15 |

Третья строка — правка ADR 0215. Задержка у неё не 0, а `CHANNELS[MESSENGER].
delay_months`: предупреждение, которое приходит в том же месяце, что и событие,
уже не предупреждение. Сам некролог «поселение оставлено» задержку 0 сохраняет —
это ADR 0209, и он прав: событие наступило, известить о нём можно в том же
месяце, а предупреждение приходит месяцем раньше.

Стол сеньора идёт через `eye_from_hill` с `confidence` 1.0 не по недосмотру, а по
ADR 0205: свидетель — сам держатель, и весть обязана называть **то же, что читает**.
Она читает `Household.hunger_days` двора сеньора и называет число месяцев недобора,
а не «зерно на столе» и не «подача». Молчание об этом невозможно: у двора лорда нет
другого канала, и «холм», который читает зерно живых дворов клетки, о столе держателя
не говорит вовсе.

Шерифская весть — событийная, поэтому задержка 0 (как весть о рождении стаи,
`news/threats.py`). Числа **слуховые**: тот же дрейф ±1 на счётчик, что уже стоит у
`_report_messenger` для «дворов с недоимкой», и один и тот же дрейф применяется к
итогу и к разбивке по поселениям — иначе итог и список в одном отчёте противоречили
бы друг другу. Истина мира в `facts` не попадает: только плоские целые.

**Нужен ли новый вид вести — «уход двора» — или хватает счётчика?** Хватает, и это
проверяемо. Проигрыш обязан быть виден **по ходу**, а не постфактум, а по ходу
игрок читает ежемесячный отчёт шерифа (`info/briefing.py::_report_messenger`), куда
эта сторона добавляет `departed_households` и `left_by_settlement`. Отдельный отчёт
на каждый уход был бы двадцатью семью `messenger`-строками в месяц на `v0_shire` —
это шум, а не известие, и игрок перестал бы их читать. Пороговое событие («поселение
ОСТАВЛЕНО») — наоборот, событие редкое и по определению важное, поэтому оно и
рождается отдельной вестью, один раз на поселение.

**Дедуп.** Повторный `phase_inform` (или дважды вызванный за месяц) не плодит вести:
обе проверяют `world.reports` по `facts["ruin"]`, а не по флажку. Прецедент —
`_has_report` в `info/briefing.py` и `report_caravan_arrivals` в `engine/tick.py`.
"""

from __future__ import annotations

from ..engine.ruin import (
    FAMILY_HUNGER_MONTHS,
    RUIN_ABANDONED_PREFIX,
    RUIN_FAMILY_BANKRUPT_MONTH,
    abandoned_threshold,
    at_start,
    barony_settlements,
    family_bankrupt,
    family_household,
    left_count,
    live_count,
)
from ..engine.manor import manor_stock, root_manor
from ..info.sources import CHANNELS, EYE_FROM_HILL, MESSENGER
from ..ontology import Report
from ..world import World
from .propagation import make_report

#: Ключ `facts`, по которым вести о проигрыше отличаются друг от друга.
RUIN_FACT_KEY = "ruin"
RUIN_SETTLEMENT = "settlement_abandoned"
RUIN_FAMILY = "family_bankrupt"
#: **Предупреждение**: «до пропажи осталось N» (ADR 0215 §Дыра 4). Отдельное
#: значение, а не флаг у `RUIN_SETTLEMENT`: иначе дедуп по
#: `(facts["ruin"], subject_id)` погасил бы либо то, либо другое, потому что
#: пара ключей у них одна и та же.
RUIN_WARNING = "settlement_at_risk"

#: Дрейф счёта гонца на ±1 двор — тот же закон, что у «дворов с недоимкой»
#: (`info/briefing.py::_report_messenger`). Один бросок на весь отчёт: итог и
#: разбивка обязаны сходиться между собой, иначе весть сама себе противоречит.
COUNT_DRIFT = (-1, 0, 0, 0, 1)


def months_phrase(count: int) -> str:
    """`3` → «месяцев», `2` → «месяца», `1` → «месяц». По-русски, без обходных путей.

    Отдельной функцией, а не константой рядом с `FAMILY_HUNGER_MONTHS`: текст вести
    обязан оставаться грамотным при любом пороге, а не только при том, который стоит
    сегодня. Смена порога не должна превращать весть в «1 месяцев».
    """
    tail_100 = count % 100
    if 11 <= tail_100 <= 14:
        return "месяцев"
    tail = count % 10
    if tail == 1:
        return "месяц"
    if tail in (2, 3, 4):
        return "месяца"
    return "месяцев"


def households_phrase(count: int) -> str:
    """`1` → «двор», `2` → «двора», `5` → «дворов». Закон тот же, что у `months_phrase`.

    Отдельная функция, потому что порог `ABANDONED_LEFT_MIN` и счётчик ушедших —
    числа, которые игрок читает, и «порог — 2 дворов» в тексте вести читается как
    опечатка в законе, а не как число закона. Назвать число можно только вместе
    с его формой, поэтому форма и число ходят рядом всегда.
    """
    tail_100 = count % 100
    if 11 <= tail_100 <= 14:
        return "дворов"
    tail = count % 10
    if tail == 1:
        return "двор"
    if tail in (2, 3, 4):
        return "двора"
    return "дворов"


def barn_grain(world: World) -> float:
    """Зерно в книге корня — назвать ли в вести о разорении.

    Третья величина, не вторая: паёк стола лорда и подача идут из этого же амбара
    (`economy/manor.py::_board`, `economy/exchange.py::relief_sources`), поэтому
    «амбар пуст» — честное **причина**, а «семья голодает» — **следствие**, и путать
    их в одной вести нельзя (ADR 0205 — весть обязана называть то, что читает).
    """
    manor = root_manor(world)
    stock = manor_stock(world, manor) if manor is not None else None
    return float(stock.amounts.get("grain", 0.0)) if stock is not None else 0.0


def _already_reported(world: World, mark: str, subject_id: str) -> bool:
    """Рождалась ли весть о таком проигрыше раньше.

    Ключ — пара `(facts["ruin"], subject_id)`, а не только вид проигрыша: «поселение
    оставлено» случается один раз **на каждое поселение**, и поиск по одному виду
    отсекал бы все поселения после первого. Прецедент дедупа — `_has_report` в
    `info/briefing.py` (там пара `source + subject_id + event_date`).
    """
    return any(
        report.facts.get(RUIN_FACT_KEY) == mark and report.subject_id == subject_id
        for report in world.reports
    )


def ruin_threshold(settlement_left: int, at_first_seen: int) -> int:
    """Сколько дворов должно уйти, чтобы поселение признали оставленным.

    **Арифметика — не здесь, а в `engine/ruin.py::abandoned_threshold`**
    (ADR 0216 §Дыра 4). Эта функция стояла здесь же, и доктрин её оправдывала
    именно отсутствием второй копии закона: «функция существует отдельно, чтобы
    весть и закон не считались каждый сам по себе». Но закон
    (`engine/ruin.py::settlement_is_abandoned`) считал порог у себя, прямой строкой
    `left >= ABANDONED_SHARE * base`, — то есть гарантия была пустой, и совпадение
    держали два независимых места. Теперь весть читает порог оттуда же, откуда
    его читает закон, и разойтись им больше негде.

    Первый аргумент оставлен для честности подписи: он показывает, что при
    `settlement_left >= порога` вызывать весть-предупреждение нельзя, и новое
    отсечение в `report_ruin_warning` именно это и проверяет.
    """
    return abandoned_threshold(at_first_seen)


def report_ruin_warning(world: World) -> list[Report]:
    """Весть шерифа ЗАРАНЕЕ: сколько ещё дворов до пропажи поселения.

    **Зачем это весть, а не правка вести о проигрыше (ADR 0215 §Дыра 4).**
    Замер стола плейтеста: «Крестьяне ушли» приходил в том же месяце, в котором
    проигрыш уже наступил (`news/ruin.py`, задержка 0), а порог
    `ABANDONED_SHARE = 0.25` не был назван **ни в одной вести** — игрок физически
    не мог узнать, сколько ещё осталось. Плейтест это доказал дорогой ценой:
    проигрыш оказался **обратим** (землю вернули на 7-м месяце из 11-го, деревня
    выжила), то есть игра была проиграна без нужды, потому что игрок не видел
    приближения. Некролог не предупреждение.

    Поэтому весть о проигрыше **не трогается** — ADR 0209 её и так починил, она
    и должна приходить в месяц события, — а рядом с ней появляется
    **предупреждение**: первая весть шерифа о том, что дворы из поселения уходят,
    называет и число ушедших, и сам порог, и сколько дворов осталось до пропажи.

    **Один раз на поселение, не каждый месяц.** Предупреждение — не счётчик
    («ушёл ещё один»), счётчиком остаётся ежемесячный отчёт шерифа
    (`info/briefing.py::_report_messenger`), который к тому же **называет
    поселение** (ADR 0215 §Дыра 5). Повторять порог каждый месяц — значит
    завалить игрока строкой, которую он всё равно не сможет сверить с уходящим
    двором, которого он не знает.

    **Числа слуховые, и весть говорит об этом словами.** Тот же дрейф ±1 на
    счётчик, что у остальных отчётов шерифа, и та же оговорка в тексте: «шериф
    считает на слух». Молчаливая неточность хуже отсутствия вести, потому что
    игрок принял бы её за закон. Истина мира в `facts` не попадает — только
    плоские целые (И-3).

    **Обещание, которое нельзя сдержать, вестью не называется (правка приёмки
    ADR 0215).** Первая версия этого предупреждения ругалась на «до пропажи
    осталось 1» у хутора из одного двора — и это была ложь вдвойне:

    * `at_start = 1`, `threshold = max(ABANDONED_LEFT_MIN, ceil(0.25)) = 2`, а
      живых дворов 0. Порог недостижим **в принципе**: уйти больше неоткуда, и
      предупреждение обещало игроку пропажу, которой не случится никогда;
    * `remaining` считался от `reported`, то есть от числа **на слух**. При
      `left = 1`, дрейфе `+1` и `threshold = 2` выходило «до пропажи осталось 0»
      — то есть весть, которая по смыслу обязана предупреждать, сообщала, что
      предупреждать уже не о чем. Игрок читал «0» и считал, что Settlement
      оставлен, хотя защёлки не стояло.

    Обе беды лечатся одним правилом, и оно не про арифметику, а про смысл:
    **предупреждение обязано быть выполнимым, а счёт — нижней границей, а не
    суммой.** Отсюда два отсечения и одно пол:

    * `threshold - left > live_count` → порог недостижим, вести нет (молчание
      честнее обещания, которого мир не сдержит);
    * `left >= threshold` → это уже не предупреждение (осталось как было);
    * `remaining = max(1, threshold - reported)` — «не хватает **хотя бы** N».
      Единица снизу правдива всегда: раз `left < threshold`, то реально не
      хватает минимум одного двора, сколько бы шериф ни насчитал.
    """
    produced: list[Report] = []
    channel = CHANNELS[MESSENGER]
    for settlement in barony_settlements(world):
        if world.stats.get(RUIN_ABANDONED_PREFIX + settlement.id, 0.0) >= 1.0:
            continue
        left = left_count(world, settlement)
        if left <= 0:
            continue
        at_first_seen = at_start(world, settlement)
        if at_first_seen <= 0:
            continue
        if _already_reported(world, RUIN_WARNING, settlement.id):
            continue
        threshold = ruin_threshold(left, at_first_seen)
        if left >= threshold:
            # Порог уже взят, но защёлки ещё нет (она ставится фазой этого же
            # месяца). Предупреждение не выдаёт себя за некролог и не выдаёт
            # пропажу за приближение: молчание здесь честнее.
            continue
        if threshold - left > live_count(world, settlement):
            # Порог недостижим: уходить больше некому. Обещание, которое мир не
            # сдержит, хуже молчания — игрок будет ждать пропажи, которой не
            # будет, и это молчание обернётся ложью позже, когда он решит, что
            # «проглядел» уход.
            continue
        reported = max(0, left + world.rng.news.choice(COUNT_DRIFT))
        remaining = max(1, threshold - reported)
        produced.append(
            make_report(
                world,
                MESSENGER,
                "settlement",
                settlement.id,
                f"Шериф сказывает: поселение «{settlement.name}» — ушло дворов "
                f"{reported} из {at_first_seen}; до пропажи не хватает {remaining} "
                f"{households_phrase(remaining)} (порог — {threshold} "
                f"{households_phrase(threshold)}, а дальше поселение не его). "
                f"Счёт на слух, шериф сам говорит, что считает на глаз.",
                {
                    RUIN_FACT_KEY: RUIN_WARNING,
                    "settlement": settlement.id,
                    "settlement_name": settlement.name,
                    "left_reported": reported,
                    "at_start": at_first_seen,
                    "threshold": threshold,
                    "remaining": remaining,
                },
                world.clock.date,
                channel.delay_months,
                channel.confidence,
                distorted=reported != left,
                noise=channel.noise,
            )
        )
    return produced


def report_abandoned_settlements(world: World) -> list[Report]:
    """Весть шерифа: поселение оставлено. Одна на поселение, один раз.

    Читает защёлку `ruin_abandoned_<settlement_id>` из `engine/ruin.py`, а не пересчитывает
    долю заново: пересчёт был бы проверкой не того, о чём весть, и завтрашняя правка
    константы тихо изменила бы форму вести.
    """
    produced: list[Report] = []
    for settlement in sorted(world.settlements.values(), key=lambda s: s.id):
        mark = RUIN_ABANDONED_PREFIX + settlement.id
        if world.stats.get(mark, 0.0) < 1.0:
            continue
        if _already_reported(world, RUIN_SETTLEMENT, settlement.id):
            continue
        left = left_count(world, settlement)
        # Знаменатель берётся функцией `at_start`, а не чтением `world.stats` под
        # ключом: имя ключа — деталь закона, и весть не должна знать его руками.
        at_first_seen = at_start(world, settlement)
        reported = max(0, left + world.rng.news.choice(COUNT_DRIFT))
        produced.append(
            make_report(
                world,
                MESSENGER,
                "settlement",
                settlement.id,
                f"Шериф сказывает: {settlement.name} запустела — дворов ушло "
                f"{reported} из {at_first_seen}, осталось {max(0, at_first_seen - reported)}.",
                {
                    RUIN_FACT_KEY: RUIN_SETTLEMENT,
                    "settlement": settlement.id,
                    "settlement_name": settlement.name,
                    "left_reported": reported,
                    "at_start": at_first_seen,
                },
                world.clock.date,
                0,
                0.5,
                distorted=reported != left,
                noise=0.15,
            )
        )
    return produced


def report_family_bankrupt(world: World) -> list[Report]:
    """Весть холма: стол сеньора пуст, семья не ет. Один раз, навсегда.

    Канал `eye_from_hill` с `confidence` 1.0 — по ADR 0205: свидетель сам держатель,
    и весть называет ровно то, что читает (`Household.hunger_days` его двора). Числа
    не искажаются, потому что искажать тут нечего: это его собственная кладовая, и
    `info/briefing.py::_report_manor_barn` уже отдаёт ему точный `barn_grain` из того
    же амбара. Расхождение двух вестей об одном амбаре было бы браком.

    **Почему весть говорит «не ест С МЕСЯЦА», а не «не ест N месяцев».** Семья может
    поесть и на следующий месяц: `hunger_days` обнуляется первым же сытым месяцем.
    Проигрыш — защёлка (`engine/ruin.py::family_bankrupt`), а не текущее число, и
    весть обязана это отражать. Формулировка «не ест с Y5-M04» остаётся правдой и
    через год; «не ест 0 месяцев» была бы ложью, которой сам модуль отказывается
    врать (ADR 0205: весть обязана называть то же, что читает).
    """
    if not family_bankrupt(world):
        return []
    household = family_household(world)
    household_id = household.id if household is not None else str(
        getattr(world.player, "household_id", "") or ""
    )
    if _already_reported(world, RUIN_FAMILY, household_id):
        return []
    months_per_year = max(1, int(world.clock.months_per_year))
    absolute = int(
        world.stats.get(RUIN_FAMILY_BANKRUPT_MONTH, float(world.clock.date.year * 12))
    )
    since = f"Y{absolute // months_per_year}-M{absolute % months_per_year:02d}"
    barn = barn_grain(world)
    name = household.name if household is not None else "сеньор"
    return [
        make_report(
            world,
            EYE_FROM_HILL,
            "household",
            household_id,
            f"Холм: стол пуст. {name} не ест с {since} — "
            f"{FAMILY_HUNGER_MONTHS} {months_phrase(FAMILY_HUNGER_MONTHS)} подряд; "
            f"в книге зерна около {barn:.0f}.",
            {
                RUIN_FACT_KEY: RUIN_FAMILY,
                "household": household_id,
                "household_name": name,
                "hunger_months": FAMILY_HUNGER_MONTHS,
                "since": since,
                "barn_grain": round(barn, 1),
            },
            world.clock.date,
            0,
            1.0,
            distorted=False,
            noise=0.0,
        )
    ]


def report_ruin(world: World) -> list[Report]:
    """Все вести о проигрыше за месяц. Зовётся из `phase_inform` после прочих вестей.

    Порядок — **предупреждение, потом некролог** (ADR 0215 §Дыра 4). Обе вести
    про поселение оставлены не могут: предупреждение гаснет, когда защёлка
    встала. Ставить предупреждение первым нужно, чтобы в списке доставленных
    за месяц, где игрок читает свежее, «сколько осталось» стояло выше «всё
    кончено» — но на практике в один месяц они не рождаются обе, так что порядок
    здесь читаемость, а не зависимость.
    """
    return [
        *report_ruin_warning(world),
        *report_abandoned_settlements(world),
        *report_family_bankrupt(world),
    ]
