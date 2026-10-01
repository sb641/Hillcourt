"""Повинности: натуральная рента (доля от урожая) и трудовая повинность.

Вассал-вассала в v0 нет: `Obligation` всегда связывает двор с держателем земли,
которому он подчинён напрямую, и не порождает цепочку повинностей.
"""

from __future__ import annotations

from ..ontology import Obligation, ObligationTemplate
from ..world import World

RENT_KIND = "rent"
RENT_GOOD = "grain"
# База оброка — урожай двора по проводкам этого оборота, а не остаток амбара
# (ADR 0183, ADR 0185). Подача сюда не попадает: она идёт как `transfer`.
RENT_BASE_REASON = "harvest_grain"
# ОКНО базы: сколько последних месяцев урожая попадает в счёт (ADR 0192 п. 1).
# Окно 1 = ровно прошлый месяц, и это единственное чтение, при котором оброк
# остаётся ДОЛЕЙ: каждое зерно облагается один раз, и сумма счетов за игру равна
# `default_share × весь урожай`. Окно шире — старый урожай облагается снова, и
# счёт становится пропорционален НЕ доле, а ширине окна: замер `start_stand`,
# 24 мес, сид 1729, доля 0.1, «выставлено / выращено»:
#   окно 1 мес — 84.902 / 857.225 =  9.9 %  ← закон
#   окно 2 мес — 162.409 / 858.875 = 18.9 %
#   окно 3 мес — 231.360 / 859.850 = 26.9 %
#   окно 6 мес — 387.621 / 861.500 = 45.0 %
#   окно 12 мес — 616.932 / 861.500 = 71.6 %
#   без нижней границы (только верхняя) — 760.966 / 861.500 = 88.3 %  ← бывший дефект
# Ровно 10 % даёт только ширина 1; дальше идёт примерно 10 % × ширина, то есть
# старый урожай обложен дважды. Без нижней границы это уже не доля, а аннуитет на
# прошлое: счёт растёт всю жизнь двора, и недоимка растёт монотонно — на том же
# замере выставлено 760.966 против 861.500 выращенного (88.3 %), уплачено 291.465,
# недоимка 385.218, то есть 78.5 % всего зерна ушло в ренту.
# Число живёт здесь, а не в каталоге: поля `base_window_months` у
# `ObligationTemplate` нет, а `ontology.py`/`catalogs.py` — не зона Legal.
RENT_BASE_WINDOW_MONTHS = 1

# Основание промысла (ADR 0189). Направление обратное всем остальным видам:
# не «двор должен сеньору», а «сеньор кладёт своих работников». `due_good` пуст:
# цена основания — люди и простой, а не зерно, поэтому платить нечем и не нужно.
WORKS_KIND = "works_founded"
WORKS_BASIS = "founded"
WORKS_PREFIX = "obl_works_"

# Поля СЧЁТА повинности: что́ должен двор по закону. Их переписывает новый
# материал, потому что счёт следует закону, а закон может измениться.
BILL_FIELDS = ("kind", "due_good", "due_amount", "period_months", "basis", "right_id")


def register_obligation(world: World, household, obligation: Obligation) -> Obligation:
    """Возвращает повинность двора по `obligation.id`, создавая её не более раза.

    **Единственное место в `legal/`, где id повинности попадает в
    `household.obligation_ids`.** Все прочие пути (`bundles`, `actions`, `service`)
    идут через него, поэтому список не может набрать один и тот же id дважды
    (ADR 0210).

    Три закона, и все три — про то, что повинность не переписывается задним
    числом:

    1. **Тождество — это id, а не объект.** Запись с этим `id` уже есть →
       возвращаем её. Создание новой повинности «на всякий случай» стёрло бы
       `paid_total`/`arrears`, то есть простило бы долг правкой данных: сеньор
       правит YAML — и недоимка исчезает. Это недопустимо.
    2. **Счёт следует закону, история — факт.** При повторной материализации
       поля счёта (`BILL_FIELDS`) обновляются из нового материала, а поля
       истории (`paid_total`, `arrears`, `corvee_days`, `duty_days`,
       `call_status`) не трогаются. Отменённый закон не должен вешать на двор
       старый счёт (ADR 0185, 0192: счёт выводится, а не хранится), а уже
       возникший долг не должен исчезать от того, что закон изменился.
    3. **Дубль в списке — дефект, а не факт.** Если id уже значится дважды,
       список сводится к одному вхождению с сохранением порядка (И-6). Запись
       без этого осталась бы висеть после починки: вылечить должен обход, а не
       тихий расчёт.
    """
    existing = world.obligations.get(obligation.id)
    if existing is None:
        world.obligations[obligation.id] = obligation
        existing = obligation
    else:
        for name in BILL_FIELDS:
            setattr(existing, name, getattr(obligation, name))
    ids = household.obligation_ids
    if obligation.id not in ids:
        ids.append(obligation.id)
    elif ids.count(obligation.id) > 1:
        # Порядок первого появления сохранён: `dict.fromkeys` детерминирован (И-6).
        household.obligation_ids = list(dict.fromkeys(ids))
    return existing


def works_obligation_id(works_id: str, date) -> str:
    """Id повинности основания: промысел, сеньор, месяц.

    Месяц в идентификаторе — та же договоренность, что у подачи (ADR 0185): у
    `Obligation` нет поля даты, а основание привязано к месяцу, и тройка
    (промысел, сеньор, месяц) и есть идентичность записи.
    """
    return f"{WORKS_PREFIX}{works_id}_{date.year}M{date.month:02d}"


def works_due_for(template: ObligationTemplate) -> float:
    """Цена основания из шаблона: сколько работников сеньор кладёт.

    Проверяет, что вид объявлен именно как основание и что цена объявлена: молча
    подставлять ноль нельзя — основание без работников это не промысел.
    """
    if template.kind != WORKS_KIND or template.basis != WORKS_BASIS:
        raise ValueError(
            f"Шаблон '{template.id}':kind '{template.kind}'/basis '{template.basis}', "
            f"а основание промысла — {WORKS_KIND}/{WORKS_BASIS} (ADR 0189)"
        )
    if template.default_amount is None:
        raise ValueError(
            f"Основание промысла '{template.id}': не объявлено число работников "
            "(default_amount) — основать нечем"
        )
    if template.due_good is not None:
        raise ValueError(
            f"Основание промысла '{template.id}': due_good '{template.due_good}' — "
            "цена основания это люди, а не товар (ADR 0189)"
        )
    return float(template.default_amount)


def works_template(world: World) -> ObligationTemplate:
    """Шаблон основания промысла из каталога — единственный источник цены."""
    for template in world.catalogs.obligation_templates.values():
        if template.kind == WORKS_KIND:
            return template
    raise ValueError(
        "Каталог повинностей: нет записи вида 'works_founded' — основание промысла "
        "не объявлено (ADR 0189)"
    )

def rent_template(world: World) -> ObligationTemplate:
    """Шаблон оброка из каталога — единственный источник доли (ADR 0183).

    Доли нет в данных сцены: `Right.rent_share` решает, понесёт ли наделение
    оброк вообще, но **размер** объявляет каталог. Нет записи вида `rent` —
    падение, а не подстановка числа: молчаливая ставка хуже её отсутствия.
    """
    for template in world.catalogs.obligation_templates.values():
        if template.kind == RENT_KIND:
            return template
    raise ValueError(
        "Каталог повинностей: нет записи вида 'rent' — доля оброка не объявлена"
    )


def _window_bounds(world: World, window_months: int) -> tuple[tuple[int, int], tuple[int, int]]:
    """Границы окна базы по `(год, месяц)`: последние `window_months` месяцев,
    кончающиеся прошлым месяцем.

    Текущий месяц в окно не входит: оброк не берётся с зерна, которое ещё не
    обмолочено (ADR 0192). Во время `phase_obligations` часы мира ещё стоят на
    обрабатываемом месяце (`advance_month` делает последняя фаза, `phase_record`),
    поэтому «прошлый месяц» — это `date - 1`, а не `date`: счёт выставляется в
    месяце M за урожай, собранный в M−1. Месяц января считается по календарю
    года, а не вычитанием единицы из номера месяца, поэтому декабрь прошлого года —
    это `(год-1, 12)`.
    """
    months_per_year = world.clock.months_per_year
    this_month = world.clock.date.year * months_per_year + (world.clock.date.month - 1)
    end = this_month - 1
    start = end - max(1, int(window_months)) + 1
    start_year, start_index = divmod(start, months_per_year)
    end_year, end_index = divmod(end, months_per_year)
    return (start_year, start_index + 1), (end_year, end_index + 1)


def _harvest_grain(world: World, household, window_months: int | None) -> float:
    """Урожай двора по проводкам `harvest_grain` в окне месяцев — не остаток амбара.

    `window_months=1` — база начисления: урожай **прошлого месяца** (ADR 0192
    п. 1). Окно оканчивается прошлым месяцем всегда: нельзя брать оброк с зерна,
    которое ещё не обмолочено, а месяц запаса означает, что двор не требует подачи
    с урожая, посыпавшегося в середине сезона.

    `window_months=None` — урожай за всю историю, без нижней границы. Это НЕ база
    оброка и не «запасной» путь: такая величина нужна, чтобы показать игроку
    разницу («вырастил за два года столько-то, а счёт за месяц — вот столько»), и
    чтобы тест закона отличал долю от аннуитета. Начисление идёт только из
    `household_rent_due_base`.

    Отбор строго по `reason="harvest_grain"`, поэтому **подача в базу не
    входит**: подача — долг сеньора (ADR 0169), и остаток, набитый подачей,
    оброком не облагается. Отсюда прямое следствие закона: сеньор, который
    кормит голодного, **не увеличивает его долг**.
    """
    bounds = None
    if window_months is not None:
        bounds = _window_bounds(world, window_months)
    total = 0.0
    for entry in world.ledger.entries:
        if entry.reason != RENT_BASE_REASON or entry.kind != "process":
            continue
        if entry.good != RENT_GOOD or entry.dst_id != household.stock_id:
            continue
        if bounds is not None:
            first, last = bounds
            stamp = (entry.date.year, entry.date.month)
            if stamp < first or stamp > last:
                continue
        total += entry.amount
    return total


def household_harvest_total(world: World, household) -> float:
    """Урожай двора за всю историю — видимая величина, не база оброка.

    Это то, что двор **вырастил**, а не что имеет: в амбаре лежит и привезённое,
    и старое, и подарок, и подача. Оброк отсюда не берётся (ADR 0192 п. 1) — и
    именно поэтому функция названа harvest, а не base: имя `..._base` у
    безоконной суммы и было причиной, по которой оброк однажды стали считать от
    архива.
    """
    return _harvest_grain(world, household, None)


def household_rent_due_base(world: World, household) -> float:
    """База начисления: урожай двора за окно, кончающееся прошлым месяцем.

    Окно — `RENT_BASE_WINDOW_MONTHS` (1), то есть ровно прошлый месяц. Считать
    «всё до текущего месяца» нельзя: получится не доля, а аннуитет на прошлое,
    где каждый старый урожай облагается снова (ADR 0192 п. 1).
    """
    return _harvest_grain(world, household, RENT_BASE_WINDOW_MONTHS)


def rent_rate_for_right(world: World, right_id: str | None) -> float | None:
    """Ставка оброка, назначенная правом: `Right.rent_share`, либо `None`.

    `None` означает «ставка права не объявлена» (правом не заведено, либо оно не
    найдено) — и тогда действует умолчание каталога. Молчаливого нуля здесь нет:
    ноль доли означал бы «оброк не заводится» (ADR 0206), и спутать его с
    «умолчание неизвестно» нельзя, потому что это противоположные законы.
    """
    if not right_id:
        return None
    right = world.rights.get(right_id)
    if right is None:
        return None
    return float(right.rent_share)


def rent_rate_for(world: World, obligation: Obligation) -> float:
    """Действующая доля оброка по повинности: право, иначе умолчание каталога.

    Источник ставки — **право** (`Right.rent_share`), потому что доля задаётся
    игроком при выдаче надела, а каталог объявляет умолчание для наделов без
    назначенной доли (ADR 0206). Ставка читается здесь, а не в
    `engine/tick.py::refresh_rent_due`, потому что тик зовёт `refresh_rent_due`
    с той же сигнатурой, а менять вызывающий нельзя: `engine/` — не зона Legal.
    """
    rate = rent_rate_for_right(world, obligation.right_id)
    if rate is None:
        template = rent_template(world)
        if template.default_share is None:
            raise ValueError(
                f"Оброк '{template.id}': не объявлена доля (default_share) — платить нечем"
            )
        rate = float(template.default_share)
    if rate < 0.0 or rate > 1.0:
        raise ValueError(
            f"Ставка оброка {rate} вне доли: `rent_share` задаётся в [0.0, 1.0], "
            f"где 0.0 — «оброк не заводится» (ADR 0206)"
        )
    return rate


def rent_amount_for(
    template: ObligationTemplate, harvest_grain: float, rate: float | None = None
) -> float:
    """Сумма оброка: `ставка × зерно двора` (ADR 0183, ADR 0206).

    Ставка приходит из права (`rent_rate_for`); `rate=None` означает «указанного
    ставки нет, бери каталожную» — так ею пользуются прежние вызовы и тест закона,
    который сравнивает сумму с долей каталога.

    Прежде здесь стоял `rent_share`, умноженный на базовый мешок 6.0. Это был
    недоделанный переход: результат всё равно выходил постоянной суммой, только
    названной долей. Оброк перестал зависеть от урожая и тем обесценивался при
    росте жатвы, поэтому база оброка теперь сам урожай, а базовый мешок удалён
    вместе с его мнимым чтением. Второй недоделанный переход был в другом: размер
    брался из каталога, и ставка игрока была переключателем. Теперь размер
    считает ставка права, а каталог держит умолчание.
    """
    if template.basis != "share":
        raise ValueError(
            f"Оброк '{template.id}': basis '{template.basis}', а оброк — доля (ADR 0183)"
        )
    if rate is None:
        if template.default_share is None:
            raise ValueError(
                f"Оброк '{template.id}': не объявлена доля (default_share) — платить нечем"
            )
        rate = template.default_share
    return round(float(rate) * max(0.0, float(harvest_grain)), 3)


def is_rent_share(obligation: Obligation) -> bool:
    """Оброк, считаемый долей от урожая: его сумма живёт, а не застывает."""
    return obligation.kind == RENT_KIND and obligation.basis == "share"


def refresh_rent_due(world: World, household, obligation: Obligation) -> float:
    """Пересчитать сумму оброка за текущий месяц (ADR 0185, ADR 0192).

    Сумма оброка — не назначение, а производная от базы, поэтому она обязана
    пересчитываться каждый месяц. Застывшая сумма мертвила наделение: двор,
    получивший землю в месяце без зерна, имел `due = 0.0` навсегда, и оброк не
    появлялся никогда.

    База берётся из окна `RENT_BASE_WINDOW_MONTHS`, а не из архива: счёт месяца —
    это доля от того, что двор собрал в прошлом месяце. Считать от архива нельзя
    не только потому, что это не доля, но и потому, что счёт тогда растёт всю
    жизнь двора, а недоимка — монотонно, то есть сеньор сам себе вяжет петлю.

    Нулевая сумма после пересчёта — **живой факт текущего месяца**, а не остаток
    прежнего: базы нет, брать нечего, и фаза не начисляет по такой повинности
    недоимку. Запись при этом не удаляется — `paid_total` хранит историю выплат,
    и оброк вернётся в месяц, когда зерно появится.

    Ставка — `rent_rate_for`: доля права, а без права доля каталога (ADR 0206).
    Пересчитываются оба слагаемых, то есть ставка игрока применяется **со дня
    выдачи надела**, а не с какого-то отдельного месяца. Сигнатура прежняя: её
    зовёт `engine/tick.py`, а чужую зону закон не переписывает.
    """
    template = rent_template(world)
    obligation.kind = template.kind
    obligation.due_good = template.due_good
    obligation.basis = template.basis
    obligation.period_months = template.period_months
    obligation.due_amount = rent_amount_for(
        template,
        household_rent_due_base(world, household),
        rate=rent_rate_for(world, obligation),
    )
    return obligation.due_amount


def is_corvee(obligation: Obligation) -> bool:
    """Трудовая повинность (на частокол), а не рента товаром."""
    return obligation.kind == "labor_duty"


def is_payable_duty(obligation: Obligation) -> bool:
    """Оплачивается ли корщина вместо отработки (ADR 0149 п. 2).

    Цену имеет только племя в солидарности: у него `due_good`/`due_amount` заданы
    (`economy.tribe`). У своих дворов баронства они пустые — ADR 0131: платить за
    барщину нечем, откупаться нельзя.
    """
    return is_corvee(obligation) and bool(obligation.due_good) and obligation.due_amount > 0.0


def accrue_corvee(
    obligation: Obligation, labor_days: float, paid_in_lieu: bool = False
) -> float:
    """Записать отработанные дни повинности в счётчик `corvee_days`.

    `paid_in_lieu` — период оплачен вместо отработки (ADR 0149 п. 2). Начисления
    нет: оплата погашает повинность за период, а не добавляется к ней, поэтому
    отработать и заплатить за одно и то же нельзя.
    """
    if paid_in_lieu:
        return obligation.corvee_days
    obligation.corvee_days += float(labor_days)
    return obligation.corvee_days
