"""Headless-прогон сценария и сводка для игрока.

Лог прогона показывает: запасы замка, число живых дворов, сколько ренты собрано,
сколько раз дворы ходили на соседнюю клетку и сколько людей ушло и не вернулось.
Это состояние мира для отладки; игрок по-прежнему видит только `Report`.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .economy import exchange
from .engine.manor import (
    ORDERS_REFUSED_KEY,
    assign_work,
    call_boon,
    ease_week_work,
    grant_grain,
    grant_tool,
    grant_thegn,
    grant_tenement,
    revoke_thegn,
    set_tile_regime,
)

from .engine.march import send_march
from .engine.river import send_river_pack
from .engine.ruin import (
    abandoned_settlements,
    family_bankrupt,
    family_hunger_months,
    is_lost,
)
from .engine.tick import run_month
from .engine.tile_view import dump_tile_views, format_tile_view
from .engine.village import found_village, settle_household
from .legal.actions import add_obligation, grant_tenure, send_party
from .news.views import build_player_view
from .ontology import SimDate
from .scenario import load_scenario


# Виды повинностей, по которым ДВОР ДОЛЖЕН сеньору: только они — доход.
# `relief_granted` сюда не входит намеренно: у него `household_id` — двор сеньора,
# то есть запись означает «сеньор НЕДОДАЛ», а это его расход, а не поступление
# (ADR 0169 п. 6). Считать его вычитанием нельзя: переименуешь вид — ошибка вернётся.
INCOME_OBLIGATION_KINDS = frozenset({"rent", "labor_duty", "levy", "muster"})


def lord_income(world) -> float:
    """Сколько сеньору ДОЛЖНЫ дворы: сумма `paid_total` по видам `INCOME_OBLIGATION_KINDS`.

    Отдельной функцией, а не выражением в сводке, чтобы правило было одно и его
    можно было проверить: подача сюда не попадает ни при каких числах.
    """
    return sum(
        obligation.paid_total
        for obligation in world.obligations.values()
        if obligation.kind in INCOME_OBLIGATION_KINDS
    )


@dataclass
class MonthSnapshot:
    """Сводка одного месяца: запасы замка, живые дворы, рента, уход людей."""

    date: str
    court_grain: float
    court_salt: float
    living_households: int
    rent_collected: float
    households_left: int
    persons_left: int
    travel_events: int
    hunger_months: int


@dataclass
class RunResult:
    """Итог прогона: дата, известия, ушедшие дворы и баланс материи."""

    months: int
    final_date: SimDate
    reports_delivered: int
    households_left: int
    persons_left: int
    living_households: int
    rent_collected: float
    hunger_months: int
    travel_events: int
    castle_stores: dict[str, float]
    total_matter: float
    matter_delta: float
    state_hash: str
    # Цена подачи сеньору (ADR 0169 п. 6): зерно и его каталожная стоимость, отдельной
    # строкой отчёта и **не смешанная с даром соседям** — плательщик другой и цена ноль.
    relief_grain: float = 0.0
    relief_silver: float = 0.0
    # ПРОИГРЫШ — состояние, а не счёт очков (ADR 0209). Здесь он назван для
    # headless-сводки; игроку он приходит вестью (`news/ruin.py`), потому что у игрока
    # нет доступа к миру (И-3). Оба поля — ЗАЩЁЛКИ (`engine/ruin.py`), то есть
    # монотонны: True значит, что проигрыш уже случился, а не «случается сейчас».
    lost: bool = False
    family_bankrupt: bool = False
    family_hunger_months: int = 0
    abandoned_settlements: list[str] = field(default_factory=list)
    monthly: list[MonthSnapshot] = field(default_factory=list)
    player_actions: list[dict] = field(default_factory=list)


def _as_id_list(value) -> list[str]:
    """Привести список id из YAML к строкам."""
    return [str(item) for item in value]


def _dispatch_grant_thegn(world, entry: dict) -> None:
    grant_thegn(
        world,
        str(entry["person"]),
        _as_id_list(entry["tiles"]),
        _as_id_list(entry["households"]),
    )


def _dispatch_revoke_thegn(world, entry: dict) -> None:
    revoke_thegn(world, str(entry["manor"]))


def _dispatch_grant_tenement(world, entry: dict) -> None:
    grant_tenement(world, str(entry["household"]), _as_id_list(entry["tiles"]))


def _dispatch_set_tile_regime(world, entry: dict) -> None:
    set_tile_regime(world, str(entry["tile"]), str(entry["regime"]))


def _dispatch_grant_tool(world, entry: dict) -> None:
    grant_tool(
        world,
        str(entry["household"]),
        str(entry["good"]),
        float(entry["amount"]),
    )


def _dispatch_grant_grain(world, entry: dict) -> None:
    grant_grain(
        world,
        str(entry["household"]),
        float(entry["amount"]),
    )


#: Ключ рычага приказа `assign_work`: **имя дела**, а не имя приказа (ADR 0201).
#:
#: `action` в записи сценария — всегда ИМЯ ПРИКАЗА, и только оно: `_apply_script_entry`
#: ищет его в `ACTION_HANDLERS`. Один ключ с двумя смыслами («приказ `assign_work`» и
#: «дело `assign_work`») делал кнопку ненажимаемой: запись
#: `{at_month: 1, action: assign_work, household: ..., work: work_plot}` доходила до
#: `assign_work(world, hh, str(entry["action"]))` и падала
#: `ValueError: Неизвестное дело двора 'assign_work'` — приказ был в таблице, а нажать
#: его было нельзя. Рычаг вынесен в отдельный ключ `work`.
WORK_LEVER_KEY = "work"

#: Рычаг, который писали вместо `work` (историческое имя). Принимается **только**
#: когда канонического `work` нет, и не молча: расхождение двух рычагов — отказ с
#: обоими именами, а не «поберил любой из двух». Ровно тот закон, что у ключа месяца
#: (`_target_month`): одно имя, а расхождение называется.
LEGACY_WORK_LEVER_KEY = "main"


def _work_lever(entry: dict) -> str:
    """Имя дела двора из записи `assign_work`: `work`, и только он (ADR 0201).

    Ключ `work` потому, что `action` уже занят именем приказа, а `main` — именем
    поля в записи лога (`player_actions`), где оно значит «главное дело, к которому
    приказ привёл». Смешивать их нельзя: игрок, читающий лог, должен видеть в
    `main` результат приказа, а не то, что он и так написал в `action`.
    """
    action = str(entry.get("action") or "?")
    if WORK_LEVER_KEY in entry:
        lever = str(entry[WORK_LEVER_KEY])
        legacy = entry.get(LEGACY_WORK_LEVER_KEY)
        if legacy is not None and str(legacy) != lever:
            raise ValueError(
                f"Действие '{action}': '{LEGACY_WORK_LEVER_KEY}'={legacy} не совпадает "
                f"с '{WORK_LEVER_KEY}'={lever}. Это два разных дела, и второе не "
                f"сработает. Оставьте один ключ — '{WORK_LEVER_KEY}'."
            )
        return lever
    if LEGACY_WORK_LEVER_KEY in entry:
        return str(entry[LEGACY_WORK_LEVER_KEY])
    raise ValueError(
        f"Действие '{action}': не указано дело двора. Ключ сценария — "
        f"'{WORK_LEVER_KEY}', а 'action' означает ИМЯ ПРИКАЗА, а не дело "
        f"(ADR 0201). Запись должна выглядеть так: "
        f"{{at_month: 1, action: '{action}', household: 'hh_...', "
        f"{WORK_LEVER_KEY}: 'work_plot'}}"
    )


def _dispatch_assign_work(world, entry: dict) -> None:
    minor = entry.get("minor")
    assign_work(
        world,
        str(entry["household"]),
        _work_lever(entry),
        None if minor is None else str(minor),
    )


#: Ключ месяца в записи `script:` — `at_month`. Его же читает планировщик прогона
#: (`run`: `int(entry.get("at_month", 0)) == month_index`) и его пишут все
#: `design/scenarios/*.yml`. Это имя формата сценария, а не описание полезной
#: нагрузки действия, поэтому каноническое имя здесь ОДНО.
MONTH_KEY = "at_month"

#: Историческое имя того же месяца. Принимается только когда совпадает с
#: `at_month`; расхождение — ошибка, а не «поберил любой из двух» (см. `_target_month`).
LEGACY_MONTH_KEY = "month"


def _target_month(entry: dict) -> int:
    """Месяц действия по записи сценария: `at_month`, и только он.

    Почему одно имя, а не два. `ease_week_work(world, month, delta)` пишет
    `stats["eased_days_<год>_<month>"]`, а экономика читает `eased_days(world,
    world.clock.month)` — то есть месяц ТОГО тика, в котором запись исполняется.
    Второй ключ `month`, отличный от `at_month`, поэтому не разрывает жатву, а
    урезает барщину месяца, который никто не читает: кнопка нажимается, отвечает
    `True`, и не делает ровно ничего. Тихая поломка хуже падения.
    `call_boon` — тот же случай: месяц у него только спрашивает `boon_allowed`,
    а флаг `boon_called_month` тратится в `economy/manor.py` как «любой ненулевой».

    Историческое `month` принимается, только если равно `at_month`, — чтобы
    фикстуры, писавшие оба ключа, не сломались; молчаливое расхождение не
    принимается. Нет ни того, ни другого — `ValueError` с именем действия и
    примером записи, а не `KeyError: 'month'` в логе прогона.
    """
    action = str(entry.get("action") or "?")
    if MONTH_KEY in entry:
        month = int(entry[MONTH_KEY])
        legacy = entry.get(LEGACY_MONTH_KEY)
        if legacy is not None and int(legacy) != month:
            raise ValueError(
                f"Действие '{action}': '{LEGACY_MONTH_KEY}'={legacy} не совпадает с "
                f"'{MONTH_KEY}'={month}. Это два разных месяца, и второй не сработает: "
                "урезается барщина месяца, который никто не читает. "
                f"Оставьте один ключ — '{MONTH_KEY}'."
            )
        return month
    if LEGACY_MONTH_KEY in entry:
        return int(entry[LEGACY_MONTH_KEY])
    raise ValueError(
        f"Действие '{action}': не указан месяц. Ключ сценария — '{MONTH_KEY}' "
        f"(его же читает планировщик прогона), а в записи есть только {sorted(entry)}. "
        f"Запись должна выглядеть так: "
        f"{{at_month: 7, action: '{action}', ...}}"
    )


def _dispatch_call_boon(world, entry: dict) -> None:
    call_boon(world, _target_month(entry))


def _dispatch_ease_week_work(world, entry: dict) -> None:
    ease_week_work(world, _target_month(entry), float(entry["delta"]))


def _dispatch_grant_tenure(world, entry: dict) -> None:
    grant_tenure(
        world,
        str(entry["household"]),
        str(entry["tile"]),
        kind=str(entry.get("kind", "tenure")),
        rent_share=float(entry.get("rent_share", 0.1)),
    )


def _dispatch_add_obligation(world, entry: dict) -> None:
    """`due_amount` необязателен **в записи**: обязателен он в приказе не всегда.

    Раньше здесь стояло `float(entry["due_amount"])`, и запись без поля падала
    `KeyError` до входа в закон. После ADR 0206 §Закон 3 это неверно по двум
    причинам, и обе — про границу ADR 0202:

    * приказ оброка **без** суммы — законная запись (оброк выводится из ставки
      права и урожая), и `KeyError` обрывал прогон вместо того, чтобы приказ
      исполнился;
    * приказ не-оброка **без** суммы — отказ закона, который обязан быть виден
      игроку как ход игры, а не как опечатка сценария.

    Разбирать, обязательно ли поле, — не работа приказа: это решение
    `legal.actions.add_obligation`, и здесь оно только передаётся. `None` означает
    «игрок сумму не задавал», и закон отвечает за это сам (отказ с указанием, где
    задаётся ставка, либо требование суммы для не-оброчного вида).
    """
    raw = entry.get("due_amount")
    add_obligation(
        world,
        str(entry["household"]),
        str(entry["kind"]),
        entry.get("due_good"),
        None if raw is None else float(raw),
        period_months=int(entry.get("period_months", 1)),
        basis=str(entry.get("basis", "share")),
        corvee_days=float(entry.get("corvee_days", 0.0)),
        right_id=entry.get("right_id"),
    )


def _dispatch_send_party(world, entry: dict) -> None:
    send_party(
        world,
        str(entry["household"]),
        _as_id_list(entry["members"]),
        str(entry["destination"]),
    )


def _dispatch_send_march(world, entry: dict) -> None:
    send_march(
        world,
        str(entry["household"]),
        _as_id_list(entry["members"]),
        str(entry["destination"]),
        str(entry.get("profile", "foot")),
    )


def _dispatch_send_river(world, entry: dict) -> None:
    send_river_pack(
        world,
        str(entry["household"]),
        _as_id_list(entry.get("members", [])),
        str(entry["destination"]),
        str(entry.get("profile", "water_raft")),
        dict(entry.get("cargo") or {}),
    )


def _dispatch_send_sally(world, entry: dict) -> None:
    from .engine.seat import send_sally

    send_sally(
        world,
        str(entry["household"]),
        _as_id_list(entry["members"]),
        str(entry["destination"]),
    )


def _dispatch_work_road(world, entry: dict) -> None:
    from .engine.roadworks import start_work

    start_work(world, str(entry["tile"]), "road")


def _dispatch_work_ford(world, entry: dict) -> None:
    from .engine.roadworks import start_work

    start_work(world, str(entry["tile"]), "ford")


def _dispatch_work_bridge(world, entry: dict) -> None:
    from .engine.roadworks import start_work

    start_work(world, str(entry["tile"]), "bridge")


def _dispatch_found_village(world, entry: dict) -> None:
    """Основать поселение (ADR 0222).

    Угодье — рычаг с именем `works_tiles`, а не `works`: последнее уже занято
    приказом `assign_work` рычагом `work` (ADR 0201), и две разные вещи под
    одним именем делают запись ненажимаемой.
    """
    found_village(
        world,
        str(entry["tile"]),
        _as_id_list(entry["works_tiles"]),
        name=None if entry.get("name") is None else str(entry["name"]),
        kind=str(entry.get("kind", "village")),
    )


def _dispatch_settle_household(world, entry: dict) -> None:
    """Поселить существующий двор в поселение (ADR 0222)."""
    settle_household(world, str(entry["household"]), str(entry["tile"]))


ACTION_HANDLERS = {
    "grant_thegn": _dispatch_grant_thegn,
    "revoke_thegn": _dispatch_revoke_thegn,
    "grant_tenement": _dispatch_grant_tenement,
    "set_tile_regime": _dispatch_set_tile_regime,
    "grant_tool": _dispatch_grant_tool,
    "grant_grain": _dispatch_grant_grain,
    "assign_work": _dispatch_assign_work,
    "call_boon": _dispatch_call_boon,
    "ease_week_work": _dispatch_ease_week_work,
    "grant_tenure": _dispatch_grant_tenure,
    "add_obligation": _dispatch_add_obligation,
    "send_party": _dispatch_send_party,
    "send_march": _dispatch_send_march,
    "send_river": _dispatch_send_river,
    "send_sally": _dispatch_send_sally,
    "work_road": _dispatch_work_road,
    "work_ford": _dispatch_work_ford,
    "work_bridge": _dispatch_work_bridge,
    "found_village": _dispatch_found_village,
    "settle_household": _dispatch_settle_household,
}


#: Исключения, которыми приказ отказывает игроку, а не ломается (ADR 0202).
#:
#: `PermissionError` — гейт полномочия (двор вне книги, ушёл, пресет не даёт дела),
#: `ValueError` — отказ закона (нет зерна, клетка не соседняя, неизвестное дело).
#: Оба означают одно: **приказ не исполнился, и это ход игры, а не авария**.
#: Всё остальное (`KeyError`, `TypeError`, `AttributeError`) — дефект кода или
#: опечатка в сценарии, и такой отказ прогон обрывает: чинить надо причину, а не
#: научить тик молчать.
ORDER_REFUSALS = (PermissionError, ValueError)

#: Приказы, у которых рычаг читается из записи **до** разбора дела (ADR 0201/0202).
#:
#: Зачем отдельный шаг. Проверка формы записи и разбор отказа закона — разные вещи,
#: и смешивать их нельзя: `ValueError` на «не указан месяц» или «не указано дело»
#: означает опечатку в сценарии, а не игру. Если бы форма проверялась внутри `try`,
#: опечатка `work:` превратилась бы в «лорд не смог назначить работу» — и сценарий
#: с опечаткой тихо играл бы двадцать месяцев, не сказав ни слова. Поэтому форма
#: проверяется **до** `try` и падает вслух, а перехватывается только то, что
#: случилось уже внутри приказа.
ENTRY_SHAPE_CHECKS: dict[str, object] = {}


def _check_entry_shape(action: str, entry: dict) -> None:
    """Проверить форму записи до исполнения; опечатка — падение, а не отказ.

    Сейчас проверяется месяц у приказов, которые его читают. Проверка формы — это
    вопрос к **сценарию**, а не к миру, поэтому она обязана ронять прогон: сломанная
    запись не должна выглядеть как отказ игроку.
    """
    check = ENTRY_SHAPE_CHECKS.get(action)
    if check is not None:
        check(entry)


def _check_month_lev(entry: dict) -> None:
    """`_target_month` как проверка формы: месяц обязателен и единственен."""
    _target_month(entry)


ENTRY_SHAPE_CHECKS["call_boon"] = _check_month_lev
ENTRY_SHAPE_CHECKS["ease_week_work"] = _check_month_lev
ENTRY_SHAPE_CHECKS["assign_work"] = lambda entry: (
    _work_lever(entry),
    str(entry["household"]),
)
#: `found_village` (ADR 0222): рычаг приказа — список угодья под своим именем.
#: Пропущенный ключ — опечатка автора сценария, и она обязана **ронять прогон**,
#: а не превращаться в «поселение без земли» посреди двадцати месяцев игры.
ENTRY_SHAPE_CHECKS["found_village"] = lambda entry: (
    str(entry["tile"]),
    _as_id_list(entry["works_tiles"]),
)


def _apply_script_entry(world, entry: dict) -> list[dict]:
    """Выполнить одну запись `script:` и вернуть её запись в лог действий.

    Действия манора сами пишут в `World.player_actions`; действия права —
    нет, поэтому для них синтезируется запись. Неизвестное действие —
    `ValueError` с датой и именем: это опечатка в сценарии, а не отказ закона, и
    прогон на ней останавливается.

    **Отказ закона не роняет прогон** (ADR 0202). Приказ, которому не хватило
    зерна или который отправлен не на ту клетку, — это ход игры: лорд нажал кнопку,
    лорд получил отказ, месяцы пошли дальше. Раньше `handler(world, entry)` шёл
    без `try`, и один отказ на 5-м месяце обрывал прогон целиком: `grant_grain
    99999` давал traceback и четыре напечатанных месяца из двадцати четырёх.

    Отказ не проглатывается — он попадает в лог записью с `refused` и причиной, он
    виден игроку и попадает в `state_hash`, а прогон продолжается. Молчание было бы
    тем же браком, что и ненажимаемая кнопка: игрок не знает, что приказ не
    исполнился, и считает, что исполнился.

    Граница двух отказов намеренно разная и проведена ДО `try`:
    * **форма записи** (`_check_entry_shape`) — опечатка автора сценария, падает;
    * **отказ закона** (`ORDER_REFUSALS` внутри приказа) — ход игры, логируется.
    """
    action = str(entry.get("action") or "")
    handler = ACTION_HANDLERS.get(action)
    if handler is None:
        raise ValueError(f"{world.clock.date}: неизвестное действие '{action}'")
    _check_entry_shape(action, entry)
    before = len(world.player_actions)
    try:
        handler(world, entry)
    except ORDER_REFUSALS as refused:
        return [_log_refusal(world, entry, action, refused)]
    produced = world.player_actions[before:]
    if produced:
        for record in produced:
            _annotate_dead_levers(world, record)
        return list(produced)
    record = {"date": str(world.clock.date), "month": world.clock.month, "action": action}
    record.update({k: v for k, v in entry.items() if k not in ("at_month", "action")})
    _annotate_dead_levers(world, record)
    world.player_actions.append(record)
    return [record]


def _log_refusal(world, entry: dict, action: str, refused: Exception) -> dict:
    """Записать отказ приказа в лог и вернуть запись.

    Поля те же, что у исполненного приказа, плюс `refused` и `reason`: игрок
    должен видеть в логе **что он просил** и **почему не вышло**. Плоский список
    полей записи не меняется, поэтому `_format_player_action` печатает отказ
    без отдельной ветки.

    **Счётчик — единственный путь отказа в состояние мира** (ADR 0215 §Дыра 3).
    Раньше запись об отказе попадала в `World.state_hash` тем, что хеш ходил по
    всему `player_actions`, — и тем же путём туда попадал **любой** приказ, в том
    числе исполненный, то есть хеш описывал журнал нажатий, а не мир. Теперь
    хеш журнала не читает, и отказ остаётся в состоянии ровно одним числом:
    `world.stats["orders_refused"]`. Ключ общий с `engine.manor.log_refused_order`,
    чтобы второй вид отказа (приказ, который отказал сам, а не бросил) считался
    тем же счётчиком.
    """
    record = {"date": str(world.clock.date), "month": world.clock.month, "action": action}
    record.update({k: v for k, v in entry.items() if k not in ("at_month", "action")})
    record["refused"] = True
    record["reason"] = str(refused)
    world.player_actions.append(record)
    world.bump(ORDERS_REFUSED_KEY)
    return record


#: Приказы, у которых рычаг в записи НЕ равен тому, что мир делает (ADR 0204).
#:
#: Ключ — имя приказа, значение — функция, дописывающая в запись лога правду о
#: фактически применённой величине. Заполняется внизу модуля: функции читают
#: `legal.obligations`, и на момент объявления таблицы они ещё не определены.
DEAD_LEVER_ANNOTATORS: dict[str, object] = {}


def _annotate_dead_levers(world, record: dict) -> dict:
    """Дописать в запись приказа величину, которую мир действительно применил.

    Приказ, который ничего не изменил, не должен показывать игроку свой рычаг как
    результат (ADR 0204). `docs/01_player_fantasy.md` обещает рычаг «сколько
    платить», и приказ `grant_tenure` показывал в логе `rent_share=0.9`, хотя
    оброк считался по каталожной доле и от `rent_share` зависел только знак
    «платит / не платит». Число выглядело как результат и результатом не было.

    **Что поменял ADR 0206.** Выражение принципа сместилось, сам принцип тот же:

    * `grant_tenure`: `rent_share` — **настоящая ставка** доли урожая, рычаг
      ожил, и applied-величина читается из выданного права. Пометка
      «переключатель» снята: она стала ложной, и оставить её — значит отнять у
      живого рычага его силу;
    * `add_obligation` вида «рента-доля»: приказ с числом **отказывается**
      (`ValueError` с указанием, где задаётся ставка), и в лог игрока попадает
      отказ. Пометка «пересчитывается» осталась на единственном живом пути —
      вложенной записи оброка, которую открывает `grant_tenure` сам.

    Правда дописывается **рядом** с рычагом, а не вместо него: `rent_share=0.9`
    остаётся (игрок видит, что просил), и рядом появляется `rent_share_applied` —
    ставка, которую мир прочитал. Удалять рычаг нельзя: он и есть рычаг.

    Правило выбирается по `record["action"]`, то есть **по самой записи**, а не по
    имени внешнего приказа. Это не мелочь: `grant_tenure` сам вызывает
    `add_obligation` (ADR 0196), и та вложенная запись — про оброк, а не про
    выдачу надела. По имени внешнего приказа она получила бы пометку про
    `rent_share`, которой в ней нет, и игрок прочитал бы у оброка рычаг, который
    он не задавал.
    """
    annotate = DEAD_LEVER_ANNOTATORS.get(str(record.get("action") or ""))
    if annotate is None:
        return record
    annotate(world, record)
    return record


def _annotate_rent_share_rate(world, record: dict) -> None:
    """`grant_tenure`: показать applied-ставку оброка — ту, что прочитал мир.

    Источник — **право**, а не каталог (ADR 0206 §Закон 2): размер оброка считает
    `rent_rate_for`, то есть `Right.rent_share` выданного надела, и каталожная
    доля — лишь умолчание для наделов без назначенной ставки. Раньше здесь стояло
    `template.default_share` и `rent_share_is_switch_only=True`, то есть лог врал
    в другую сторону: он объявлял рычаг мёртвым переключателем и показывал
    чужую долю, пока приказ уже назначил свою. Читать право, а не каталог, —
    иначе завтрашняя правка надела разошлась бы с логом молча.

    Пометки `rent_share_is_switch_only` больше нет: она стала ложной (ADR 0206
    §Отменено), и оставить её было бы тем же браком с другой стороны — лог, который
    отнимает у живого рычага его силу. `rent_share` теперь применяется как есть,
    и applied-величина обязана совпадать с ним; расхождение означало бы, что где-то
    ещё завелась вторая правда о ставке, и `test_the_applied_rate_equals_the_granted_one`
    это ловит.
    """
    from .legal.obligations import rent_rate_for_right, rent_template

    rate = rent_rate_for_right(world, str(record.get("right") or "") or None)
    if rate is None:
        rate = rent_template(world).default_share
    record["rent_share_applied"] = float(rate)


def _annotate_due_amount_overwritten(world, record: dict) -> None:
    """`add_obligation`: сказать, что показанная сумма оброка — производная.

    **Кто теперь попадает в этот annotator (ADR 0206 §Закон 3).** Раньше сюда
    приходил приказ игрока `add_obligation kind: rent due_amount: 100`, и запись
    жила тем, что игрок показывал себе мёртвое число. Теперь такой приказ
    **отказ**: лог приказа
    не пишется вовсе, а в лог игрока попадает запись об отказе. Значит, у этого
    правила остался ровно один живой путь — **вложенная запись оброка, которую
    `grant_tenure` открывает сам** (`_open_rent_obligation`): там `due_amount`
    выведен из ставки права и урожая прошлого месяца, и игрок его не задавал.

    Пометка на этом пути нужна по-прежнему, и по той же причине: игрок видит в
    своей строке выдачи надела вложенное число, которое не выбирал, и должен
    знать, что оно пересчитывается ежемесячно (ADR 0185, ADR 0192) — иначе это
    число выглядит назначением. Саму сумму не подменяем: пересчёт остаётся в
    `legal/obligations.py`.
    """
    from .legal.obligations import is_rent_share

    obligation = world.obligations.get(str(record.get("obligation", "")))
    if obligation is None or not is_rent_share(obligation):
        return
    record["due_amount_recomputed_monthly"] = True
    record["due_amount_is_monthly_derived"] = True


DEAD_LEVER_ANNOTATORS["grant_tenure"] = _annotate_rent_share_rate
DEAD_LEVER_ANNOTATORS["add_obligation"] = _annotate_due_amount_overwritten


def _format_player_action(record: dict) -> str:
    """Строка лога действия игрока: `[Y1-M06] grant_thegn person=...`.

    Отказ приказа печатается с пометкой `ОТКАЗ` и причиной **первыми**, до полей
    рычагов (ADR 0202). Причина в конце строки, после `amount=99999.0`, читалась бы
    как сноска к числу, а игрок должен увидеть «не исполнилось» раньше, чем то,
    что он просил.
    """
    if record.get("refused"):
        head = f"ОТКАЗ: {record.get('reason', 'без причины')}"
        rest = " ".join(
            f"{key}={record[key]}"
            for key in sorted(record)
            if key not in ("date", "month", "action", "refused", "reason")
        )
        return f"[{record.get('date', '?')}] {record.get('action', '?')} {head} {rest}".rstrip()
    fields = " ".join(
        f"{key}={record[key]}"
        for key in sorted(record)
        if key not in ("date", "month", "action")
    )
    return f"[{record.get('date', '?')}] {record.get('action', '?')} {fields}".rstrip()


def _castle_stores(world) -> dict[str, float]:
    court = world.settlements.get(world.player.court_settlement_id)
    if court is None:
        return {}
    return dict(world.get_stock(court.stores_stock_id).amounts)


def load_actions_fixture(path: str | Path) -> list[dict]:
    """Загрузить внешнюю фикстуру действий игрока (B1).

    YAML — список записей `script:` (`at_month`, `action`, поля рычага)
    либо словарь с ключом `actions`. Исполняются существующими
    обработчиками `ACTION_HANDLERS`; новой механики здесь нет.
    """
    with Path(path).open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if data is None:
        return []
    if isinstance(data, dict):
        data = data.get("actions", [])
    if not isinstance(data, list):
        raise ValueError(f"Фикстура '{path}': ожидался список записей")
    return [dict(entry) for entry in data]


def run(
    scenario_path: str | Path,
    months: int,
    seed: int | None = None,
    print_log: bool = False,
    actions: list[dict] | None = None,
) -> RunResult:
    """Прогнать сценарий N месяцев и вернуть сводку.

    `seed` уходит в `load_scenario`, поэтому мир (черты людей и потоки RNG)
    целиком собран из одного seed. Перед i-м вызовом `run_month` (1-based)
    выполняются записи `script:` с `at_month == i`, затем — записи внешней
    фикстуры `actions` с тем же месяцем (B1). Каждая исполненная запись
    пишется в `world.player_actions` с датой (`_apply_script_entry`).
    """
    world = load_scenario(scenario_path, seed=seed)
    script = list(world.script) + [dict(entry) for entry in actions or []]

    player_actions: list[dict] = []
    monthly: list[MonthSnapshot] = []
    for month_index in range(1, months + 1):
        for entry in script:
            if int(entry.get("at_month", 0)) != month_index:
                continue
            records = _apply_script_entry(world, entry)
            player_actions.extend(records)
            if print_log:
                for record in records:
                    print(_format_player_action(record))
        start = str(world.clock.date)
        run_month(world)
        stores = _castle_stores(world)
        snapshot = MonthSnapshot(
            date=start,
            court_grain=stores.get("grain", 0.0),
            court_salt=stores.get("salt", 0.0),
            living_households=sum(
                1 for hh in world.households.values() if hh.left_at is None
            ),
            rent_collected=lord_income(world),
            households_left=int(world.stats.get("households_left", 0)),
            persons_left=int(world.stats.get("persons_left", 0)),
            travel_events=int(world.stats.get("travel_events", 0)),
            hunger_months=int(world.stats.get("hunger_months", 0)),
        )
        monthly.append(snapshot)
        if print_log:
            print(
                f"[{snapshot.date}] живых дворов {snapshot.living_households}, "
                f"замок зерно {snapshot.court_grain:.1f} соль {snapshot.court_salt:.1f}, "
                f"ренты собрано {snapshot.rent_collected:.1f}, "
                f"ходок на соседнюю клетку {snapshot.travel_events}, "
                f"ушло дворов {snapshot.households_left} "
                f"(людей {snapshot.persons_left}), "
                f"голодных месяцев {snapshot.hunger_months}"
            )

    final_date = world.clock.date
    view = build_player_view(world, final_date)
    if print_log:
        for entry in view.entries:
            marker = "?" if entry.distorted else " "
            stale = " [устарело]" if entry.stale else ""
            print(
                f"  [{entry.delivery_date}] ({entry.source}){marker} "
                f"{entry.content}{stale}"
            )
        print("  Клетки (вид без RimWorld-слоя):")
        for dump in dump_tile_views(world):
            print(f"    {format_tile_view(dump)}")

    total = world.total_matter()
    relief_grain, relief_silver = exchange.relief_cost(world)
    return RunResult(
        months=months,
        final_date=final_date,
        reports_delivered=len(view.entries),
        households_left=int(world.stats.get("households_left", 0)),
        persons_left=int(world.stats.get("persons_left", 0)),
        living_households=sum(
            1 for hh in world.households.values() if hh.left_at is None
        ),
        rent_collected=lord_income(world),
        hunger_months=int(world.stats.get("hunger_months", 0)),
        travel_events=int(world.stats.get("travel_events", 0)),
        castle_stores=_castle_stores(world),
        total_matter=total,
        matter_delta=world.ledger.delta(total),
        state_hash=world.state_hash(),
        relief_grain=relief_grain,
        relief_silver=relief_silver,
        lost=is_lost(world),
        family_bankrupt=family_bankrupt(world),
        family_hunger_months=family_hunger_months(world),
        abandoned_settlements=[
            item.settlement_id for item in abandoned_settlements(world)
        ],
        monthly=monthly,
        player_actions=player_actions,
    )


def main(argv: list[str] | None = None) -> int:
    """Разобрать аргументы и напечатать русскую сводку прогона."""
    parser = argparse.ArgumentParser(description="Headless-прогон Hillcourt")
    parser.add_argument("--scenario", required=True, help="путь к YAML сценария")
    parser.add_argument("--months", type=int, default=12, help="число месяцев")
    parser.add_argument("--seed", type=int, default=None, help="переопределить seed")
    parser.add_argument(
        "--print-log", action="store_true", help="печатать месячный лог и известия"
    )
    parser.add_argument(
        "--actions",
        default=None,
        help="YAML-фикстура действий игрока (B1): список записей script:",
    )
    args = parser.parse_args(argv)

    fixture = load_actions_fixture(args.actions) if args.actions else None
    result = run(args.scenario, args.months, args.seed, args.print_log, fixture)
    for record in result.player_actions:
        print(_format_player_action(record))
    refused = [r for r in result.player_actions if r.get("refused")]
    if refused:
        # Отказ — не сноска в конце лога, а итог прогона: игрок узнаёт о нём и из
        # строк выше, и из последней строки сводки (ADR 0202).
        print(
            f"ОТКАЗАНО приказов: {len(refused)} из {len(result.player_actions)} "
            f"(месяцев напечатано: {len(result.monthly)} из {result.months})"
        )
    grain = result.castle_stores.get("grain", 0.0)
    salt = result.castle_stores.get("salt", 0.0)
    if result.lost:
        # Проигрыш — последняя строка сводки, а не сноска: он перевешивает всё
        # остальное и должен читаться первым (ADR 0209).
        reasons: list[str] = []
        if result.family_bankrupt:
            reasons.append("семейство банкроты: двор сеньора не ест")
        if result.abandoned_settlements:
            reasons.append(
                "крестьяне ушли: запустели " + ", ".join(result.abandoned_settlements)
            )
        print("ПРОИГРЫШ: " + "; ".join(reasons))
    print(
        f"Сценарий пройден: {result.months} мес, дата {result.final_date}; "
        f"живых дворов {result.living_households}, ушло {result.households_left} "
        f"(людей {result.persons_left}); "
        f"замок: зерно {grain:.1f}, соль {salt:.1f}; "
        f"ренты собрано {result.rent_collected:.1f}; "
        f"подача {result.relief_grain:.2f} зерна = {result.relief_silver:.2f} серебра; "
        f"голодных месяцев {result.hunger_months}; "
        f"ходок на соседнюю клетку {result.travel_events}; "
        f"известий {result.reports_delivered}; "
        f"стол сеньора сейчас не ел {result.family_hunger_months} мес; "
        f"материи {result.total_matter:.3f}, дельта {result.matter_delta:.6f}, "
        f"хеш {result.state_hash[:12]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
