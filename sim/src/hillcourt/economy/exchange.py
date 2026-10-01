"""Локальный обмен, подмога и продажа зерна из амбара барона (ADR 0139).

Обмен ничего не создаёт: это `Ledger.transfer` между дворами. Свинья не берётся
из воздуха — её покупают за серебро у двора с приплодом; зерно выменивают на
серебро или получают подмогой от более сытого соседа.

Амбар барона — **товар**, и канал продажи **один** (ADR 0153): прилавок
`offer_manor_surplus` → `sell_listed_grain`. Месяц остаток амбара выставляется
(`economy/manor.py::_offer_surplus`, после пайка, подмоги и посева — ADR 0148), а
`phase_exchange` продаёт его дворам **по цене каталога** и **по мере надобности**:
не больше своего недобора и не больше остатка месячного потолка закупки. Двор платит
из кармана — серебром, а при его нехватке натуральным хозяйством, — и только
переносят зерно из амбара в сток двора: дельта материи 0. Подача голодному
(`apply_relief`) — отдельная вещь и продажей не заменяется.

ПОДАЧА — ПРАВОВОЙ АКТ, А НЕ СТАТИСТИКА (ADR 0169). Зерно, ушедшее из амбара
сеньора по `reason="relief"`, порождает **ровно одну** запись `Obligation` в
`world.obligations` (`record_relief`): месяц в id, `household_id` — двор сеньора
(повинность на сеньора), `due_good="grain"`, `due_amount` — весь недобор,
`paid_total` — выданное зерно, `arrears` — невыданный остаток. Сумма `paid_total`
по книге равна сумме проводок `relief`; при пустом амбаре запись всё равно
появляется (`paid_total = 0`, `arrears` = недобор) — сеньор видит долг, а не
тишину. Зерно — достаточная единица счёта, серебро для записи не нужно: цена
зерна для отчёта берётся из каталога (`relief_cost` → `price_of`). Счётчик
`world.stats["relief_given"]` остался диагностикой и законом не является. Вид
повинности, её товар, период и основание читаются из каталога
`obligations.yml::obligations[relief_granted]` (`relief_obligation_template`), а не
из строк в этом файле: имя вида жило в двух местах, и это ADR 0157 п. 1 в другом
классе. Каталог здесь не правится — он юриста.

Отдельной продажи «барон назвал цену» больше нет: **цена зерна — только число
каталога** (`goods.yml::grain.price_silver`, ADR 0101/0153). Обойти её было нельзя:
параметра цены в продаже нет вовсе, а `sell_listed_grain` берёт `price_of`.

СЕРЕБРО ПРИХОДИТ ИЗВНЕ, А НЕ ЧЕКАНИТСЯ (ADR 0219). Закон хозяина: «само серебро
не чеканится в баронстве — только королевский двор». Отсюда три следствия, и
каждое проверяется обвинителем, а не доверием к коду:

1. **Единственный источник серебра — продажа наружу.** `sell_grain_to_royal_court`
   последним шагом `sell_listed_grain`: амбар продаёт хлеб ЗА ПРЕДЕЛЫ карты и
   получает серебро обратно в тот же амбар. Внутри баронства источника нет:
   рецепта серебра нет, календарного роста серебра нет.
2. **Серебро = пара проводок, а не одна.** Зерно уходит `external_out`,
   серебро входит `external_in` с непустым `rule_id` правила
   `spawn_rules.yml::royal_court_buys_grain`. Одна вторая проводка была бы
   вечным двигателем: серебра в мире больше, а зерна столько же.
3. **У серебра есть видимый источник, а не абстрактный `external_in`.**
   `params.source` правила — имя плательщика (`royal_court`), и обвинитель
   требует его у каждой проводки серебра.

ПОКУПАТЕЛЬ, КОТОРЫЙ ПЛАТИТ. Серебро появляется у БАРОНА, в его амбаре. Двор
серебром не зарабатывает: единственный канал, где серебро уходит ко двору, —
жалованье найму (`manor.py::_hire`, `wage_silver_per_day`), и оно платится
только если найм состоялся. Пока найма нет, серебром платить по каталожной цене
некому, и продажа дворам остаётся натуральным обменом — это не поломка закона,
а его текущее следствие (см. отчёт экономиста и ADR 0219 §Что осталось красным).

ЦЕНА ЗЕРНА — ЧИСЛО КАТАЛОГА (ADR 0101, ADR 0144 п. 1). В коде цены зерна нет:
`price_of(world, "grain")` читает `goods.yml::grain.price_silver`, и этим же
числом считается и соседский торг, и продажа из амбара барона. Осознанное
исключение — `PIG_PRICE_SILVER`: в `goods.yml:24-25` сказано прямо, что животные
цены не имеют (рецепты разведения замкнуты).

ПОТОЛОК ЗАКУПКИ (ADR 0144 п. 2-3). Двор покупает у барона не больше
`monthly_food_need` зерна в месяц — своей месячной нормы. Канал один, но
счётчик `manor_grain_bought_<hid>` остаётся общим: он не даёт накопить покупки
через любые будущие пути и делает закон проверяемым числами. Подача (relief)
счётчик не тратит — это отдельный правовой акт (ADR 0139 п. 5). Счётчик живёт в
`world.stats` и ведётся по штампу месяца, поэтому новый месяц обнуляет его сам, а
лишних записей в `state_hash` не создаёт: метка ставится только в месяц реальной
покупки.
"""

from __future__ import annotations

from ..engine.hexgrid import neighbor_ids
from ..engine.manor import manor_of_household, manor_stock, root_manor
from ..ontology import (
    Household,
    Obligation,
    ObligationTemplate,
    SimDate,
    SpawnRule,
    Stock,
)
from ..world import World
from .needs import food_months, food_shortfall, monthly_food_need

EPSILON = 1e-9
# Свинья — единственное исключение из «цены в каталоге»: в `goods.yml:24-25`
# сказано прямо, что животные (pig, hen, duck, goose) цены не имеют, потому что
# рецепты разведения замкнуты. Попытка завести цену свиньи в каталог — отдельная
# задача (ADR 0144, «Исключение, которое остаётся»).
PIG_PRICE_SILVER = 3.0
TRADE_GRAIN_MAX = 4.0
# Соседский торг (допуск, ADR 0057): носитель — сам двор (пеший шаг в соседнюю
# клетку, Pack не вводится). Чужая сделка (покупатель и продавец на разных
# клетках кластера): те же фикс-цены; цена носителя — сбор PORTER_FEE там, где
# покупатель сыт (свиньи/серебро, брёвна/скот/зерно), а на голодном зерне —
# укус носильщика PORTER_BITE с богатой стороны (продавец даёт сверх, укус —
# в отход, покупатель платит чистую цену: голодного сбором не облагать);
# зерно через клетку — не больше NEIGHBOR_GRAIN_MAX (кап против
# перераспределения голода). Дар — без цены (неимущим).
PORTER_FEE = 0.5
PORTER_BITE = 0.25
NEIGHBOR_GRAIN_MAX = 2.0
WASTE_STOCK_ID = "sink:waste"
# Цены покупки (зерно за единицу): бревно — стройка и топливо; скот — тягло.
# Это НЕ цена зерна: здесь зерно — единица платежа, а не товар. Цена самого
# зерна живёт в `goods.yml::grain.price_silver` и читается `price_of` (ADR 0101,
# ADR 0144 п. 1) — константы цены зерна в коде не осталось.
LOG_PRICE_GRAIN = 0.5
TRADE_LOG_MAX = 4.0
LIVESTOCK_PRICE_GRAIN = {
    "ox_m": 8.0, "ox_f": 8.0,
    "donkey_m": 5.0, "donkey_f": 5.0,
    "horse_m": 12.0, "horse_f": 12.0,
    "goat": 2.5, "sheep": 3.0, "hen": 1.0, "duck": 1.5, "goose": 2.0,
    "ox_calf": 4.0, "donkey_foal": 2.5, "horse_foal": 6.0,
}
YOUNG_GOODS = frozenset({"ox_calf", "donkey_foal", "horse_foal"})
SELL_GOOD = "grain"


def price_of(world: World, good: str) -> float:
    """Цена единицы хранения из каталога (ADR 0101). Ноль — цены в каталоге нет.

    Единственный источник цены: число в коде ценой не является, иначе каталог
    перестаёт быть правдой о мире. Цена зерна (`goods.yml::grain.price_silver`)
    читается отсюда и в соседском торге, и в продаже амбара барона — одна цена,
    одно место (ADR 0144).
    """
    rule = world.catalogs.goods.get(good)
    return float(rule.price_silver) if rule is not None else 0.0


def _month_stamp(world: World) -> float:
    """Штамп текущего месяца для месячных счётчиков (`year * 12 + month`).

    Целое число, поэтому сравнение штампов точное (И-6, детерминизм).
    """
    return float(world.clock.year * 12 + world.clock.month)


def _bought_key(household_id: str) -> str:
    """Ключ месячного счётчика закупки: сколько зерна двор взял из амбара барона."""
    return f"manor_grain_bought_{household_id}"


def _bought_month_key(household_id: str) -> str:
    """Ключ штампа месяца счётчика закупки: счётчик протухает вместе с месяцем."""
    return f"manor_grain_bought_month_{household_id}"


def manor_grain_bought(world: World, household: Household) -> float:
    """Сколько зерна двор уже купил у барона в этом месяце (оба канала, оба способа оплаты)."""
    if float(world.stats.get(_bought_month_key(household.id), -1.0)) != _month_stamp(world):
        return 0.0
    return max(0.0, float(world.stats.get(_bought_key(household.id), 0.0)))


def purchase_allowance(world: World, household: Household) -> float:
    """Месячный потолок закупки зерна у барона в единицах хранения (ADR 0144 п. 2).

    Потолок — своя месячная норма двора, `monthly_food_need` (никакого нового поля
    онтологии), отношение 1.0. Норма считается в «рот-единицах», а зерно — в
    единицах хранения, поэтому делим на питательность товара, как это делает подача
    (`apply_relief`) и прилавок (`_listed_amount`); у зерна `nutrition = 1.0`, так
    что деление — тождество.
    """
    good = world.catalogs.goods.get(SELL_GOOD)
    nutrition = float(good.nutrition) if good is not None and good.nutrition > 0 else 1.0
    return max(0.0, monthly_food_need(world, household) / nutrition)


def purchase_allowance_left(world: World, household: Household) -> float:
    """Остаток месячного потолка закупки: сколько зерна двор ещё может взять у барона."""
    return max(0.0, purchase_allowance(world, household) - manor_grain_bought(world, household))


def _count_purchase(world: World, household: Household, amount: float) -> None:
    """Записать зерно, ушедшее из амбара барона двору, в месячный счётчик закупки.

    Счётчик общий для обоих каналов продажи и для обоих способов оплаты
    (ADR 0144 п. 3), иначе натуральный обмен стал бы обходом потолка. Подача
    (`apply_relief`) сюда не пишет: она не покупка (ADR 0139 п. 5).
    """
    if amount <= EPSILON:
        return
    world.stats[_bought_key(household.id)] = manor_grain_bought(world, household) + amount
    world.stats[_bought_month_key(household.id)] = _month_stamp(world)


def hidden_stock_id(household: Household) -> str:
    """id потайного стока двора (для действия hide_stores)."""
    return f"household:{household.id}:hidden"


def _hidden_stock(world: World, household: Household) -> Stock:
    sid = hidden_stock_id(household)
    if sid not in world.stocks:
        world.stocks[sid] = Stock(
            id=sid, owner_kind="household", owner_id=household.id, amounts={}
        )
    return world.stocks[sid]


RELIEF_OBLIGATION_TEMPLATE = "relief_granted"
"""id ШАБЛОНА повинности подачи в `obligations.yml` — указатель, а не имя вида.

Вид повинности (`Obligation.kind`) в коде **больше не зашит**: он читается из
каталога, `ObligationTemplate.kind`. Константа с именем вида подачи рядом с
записью `obligations.yml` была вторым источником истины для одного имени
(ADR 0157 п. 1: «один факт — одно место»), и каталог-экономист rightfully на неё
смотреть не мог: переименуй запись в каталоге — код продолжит писать старое имя, и
расхождения никто не заметит.

Что осталось в коде — **id шаблона**, то есть ключ поиска, а не значение: он
указывает, КАКУЮ запись каталога читать (ровно как `FORAGE_RECIPE` указывает на
рецепт сбора). Само значение живёт в каталоге, и запись без него не создаётся
(см. `relief_obligation_template`).
"""


def relief_obligation_template(world: World) -> ObligationTemplate:
    """Шаблон повинности подачи из `obligations.yml` — единственный источник имён.

    Импорта загрузчика каталогов здесь нет и не нужно: `world.catalogs` уже
    разобран к моменту тика, поэтому цикла импорта не возникает by construction —
    `economy/exchange.py` знает про `world`, а не про `catalogs.py`.

    Шаблона нет — значит подача не может быть записана, и это `ValueError`, а не
    откат на строку в коде: молчащая подмена была бы тем же браком, от которого
    этот резолвер и лечит (ADR 0169 п. 1, ADR 0157 п. 1).
    """
    template = world.catalogs.obligation_templates.get(RELIEF_OBLIGATION_TEMPLATE)
    if template is None:
        raise ValueError(
            f"В каталоге нет шаблона повинности '{RELIEF_OBLIGATION_TEMPLATE}': "
            "подача не может быть записана (ADR 0169 п. 1)"
        )
    return template


def relief_obligation_kind(world: World) -> str:
    """Вид повинности подачи — слово из каталога, не из кода."""
    return relief_obligation_template(world).kind


def relief_obligation_id(debtor_id: str, recipient_id: str, date: SimDate) -> str:
    """Id записи о подаче: месяц, сеньор-должник, двор-получатель.

    Месяц в id, потому что у `Obligation` нет поля даты (онтология не моя зона):
    запись обязана быть помечена месяцем, и единственное место для этого — id.
    Штамп месяца стоит **первым**: id дворов содержат `_`, поэтому месяц в конце
    нельзя отделить от получателя, а спереди — можно, и `relief_recipient`
    восстанавливает получателя без регулярок. Id детерминирован и входит в
    `state_hash`, поэтому книга манора сравнима прогонами.
    """
    return f"relief_Y{date.year}M{date.month:02d}_{debtor_id}_{recipient_id}"


def relief_recipient(obligation: Obligation) -> str:
    """Двор-получатель из id записи о подаче (должник известен самой записью)."""
    marker = f"_{obligation.household_id}_"
    if not obligation.id.startswith("relief_") or marker not in obligation.id:
        return ""
    return obligation.id.partition(marker)[2]


def relief_debtor(world: World, source: Stock) -> str:
    """Двор, на котором книга числит долг за подачу: хозяин амбара-источника.

    Источник подачи — сток манора (`manor:<id>` тэна или `settlement:<id>` корня),
    а хозяин манора известен: `Manor.holder_person_id` → двор. Запасной путь —
    `world.player`: корень без держателя отдать подачу не может, но запись о долге
    должна появиться в книге (ADR 0169 п. 3). Итоговый `source.id` — если не
    разрешился никто: запись остаётся инертной (`phase_obligations` её не видит).
    """
    for manor in world.manors.values():
        if manor.stock_id and manor.stock_id == source.id:
            person = world.persons.get(manor.holder_person_id)
            if person is not None and person.household_id:
                return person.household_id
            break
    if world.player.household_id:
        return world.player.household_id
    return source.id


def record_relief(
    world: World,
    source: Stock,
    recipient: Household,
    due_grain: float,
    paid_grain: float,
    date: SimDate,
) -> Obligation:
    """Записать подачу в книгу сеньора — одна запись на пару (сеньор, двор, месяц).

    Поля записи (ADR 0169 п. 1-3):
      * `household_id` — двор сеньора: повинность **на сеньора**, а не на двор-получателя;
      * `due_good` = `grain`, `due_amount` — весь недобор месяца в единицах хранения
        (потолок 6.0 режет выдачу, но не стирает нужду: остаток — тоже долг сеньора);
      * `paid_total` — зерно, реально ушедшее из амбара (сумма `paid_total` по книге
        равна сумме проводок `reason="relief"`, это и есть проверка закона);
      * `arrears` = `due_amount - paid_total`: при пустом амбаре запись всё равно
        появляется, и сеньор видит недоимку, а не тишину.

    В `household.obligation_ids` запись **не** попадает намеренно: `phase_obligations`
    платит повинности из стока двора-должника, и запись в списке означала бы вторую
    выплату подачи — из кармана сеньора мимо его амбара. Проверка/сбор идут по
    `world.obligations` (отчёт барона суммирует `paid_total` оттуда).
    """
    debtor_id = relief_debtor(world, source)
    template = relief_obligation_template(world)
    oid = relief_obligation_id(debtor_id, recipient.id, date)
    due = max(0.0, float(due_grain))
    paid = max(0.0, float(paid_grain))
    known = world.obligations.get(oid)
    if known is not None:
        known.due_amount = max(known.due_amount, due)
        known.paid_total += paid
    else:
        world.obligations[oid] = Obligation(
            id=oid,
            household_id=debtor_id,
            kind=template.kind,
            due_good=template.due_good,
            due_amount=due,
            period_months=template.period_months,
            paid_total=paid,
            arrears=0.0,
            right_id=None,
            basis=template.basis,
            corvee_days=0.0,
            duty_days=0.0,
        )
        world.bump("relief_records", 1.0)
    obligation = world.obligations[oid]
    obligation.arrears = max(0.0, obligation.due_amount - obligation.paid_total)
    return obligation


def relief_obligations(world: World) -> list[Obligation]:
    """Записи о подаче в книге сеньора, по id — для отчёта и проверок."""
    kind = relief_obligation_kind(world)
    return [
        world.obligations[oid]
        for oid in sorted(world.obligations)
        if world.obligations[oid].kind == kind
    ]


def relief_cost(world: World) -> tuple[float, float]:
    """Сколько подачи стоила сеньору: (зерно, серебро по каталожной цене).

    Зерно — достаточная единица счёта (ADR 0169 п. 1), серебро здесь только цена
    из `goods.yml::grain.price_silver` (`price_of`), второго числа в коде нет.
    `arrears` в сумму не входят: это ещё не выданное зерно, платить его нельзя.
    """
    grain = sum(o.paid_total for o in relief_obligations(world))
    return grain, grain * price_of(world, relief_obligation_template(world).due_good)


def relief_sources(world: World, household: Household) -> list[Stock]:
    """Источники подачи двора: наличный хлеб сеньора, затем обмен (ADR 0111 отменён).

    Приоритета «общая кладовая поселения первой» больше нет: хозяин отменил ADR
    0111, и общий амбар поселения (`Settlement.stores`) подачей не является.
    Единственный источник подачи — амбар книги сеньора (`manor:<id>`): хлеб, который
    у сеньора реально лежит. Двор без книги источников не имеет. Обмен — отдельный
    путь (рынок за деньги), он подачу не подменяет и в этот список не входит.
    Ничего не создаётся: подача только переносит наличное (И-1).
    """
    out: list[Stock] = []
    book = manor_of_household(world, household.id)
    if book is not None:
        private = manor_stock(world, book)
        if private is not None and all(private.id != stock.id for stock in out):
            out.append(private)
    return out


def apply_relief(world: World, date: SimDate) -> None:
    """Просящие дворы получают зерно по фактической нужде из амбара сеньора.

    Порядок выплат — по нужде, а не по книге и не по id: сначала дворы, которые
    без помощи уйдут или умрут (`hunger_days` по убыванию, порог ухода — 3,
    ADR 0097), затем остальные; при равенстве — по id, чтобы тик оставался
    детерминированным (ADR 0111 п. 3, 0098 п. 7). Сумма — **фактический недобор**
    двора до месячной нормы, а не фиксированная порция (ADR 0114 п. 1);
    `relief.cap_grain_per_month` — потолок на двор, а не доля. Двор, покрывающий
    нужду, помощи не получает. Материя только переносится, ничего не создаётся
    (И-1).     Подача **не тратит** месячный счётчик закупки (ADR 0144 п. 2): это
    отдельный правовой акт, а не покупка, иначе потолок закупки отменял бы подачу
    голодному.

    ПОДАЧА ЗАПИСАНА, А НЕ ПОСЧИТАНА (ADR 0169). Каждый просящий двор с недобором
    получает запись в книгу сеньора (`record_relief`): месяц, должник-сеньор,
    получатель, весь недобор и выданное зерно. Запись появляется **и при пустом
    амбаре** — тогда `paid_total = 0`, а `arrears` равен недобору: сеньор видит
    долг, а не тишину. Счётчик `relief_given` остался как диагностика и законом
    не является: судить о подаче по нему нельзя (ADR 0169 п. 2). Соседский дар
    сюда не попадает и в книгу не пишется: плательщик другой (`neighbour_gift`
    платит сосед), цена ноль, нужда не проверяется (ADR 0169 п. 6).
    """
    needs = world.needs
    if needs is None:
        return
    asking = [
        household
        for hid in sorted(world.households)
        if (household := world.households[hid]).left_at is None
        and "request_relief" in (household.main_action, household.minor_action)
    ]
    asking.sort(key=lambda household: (-household.hunger_days, household.id))
    for household in asking:
        shortfall = food_shortfall(world, household)
        if shortfall <= EPSILON:
            continue
        grain = world.catalogs.goods.get("grain")
        nutrition = grain.nutrition if grain is not None and grain.nutrition > 0 else 1.0
        want = shortfall / nutrition
        debt_source: Stock | None = None
        paid = 0.0
        for source in relief_sources(world, household):
            if debt_source is None:
                debt_source = source
            available = source.amounts.get("grain", 0.0)
            if available < needs.relief_min_court_grain:
                continue
            amount = min(want, needs.relief_amount, available)
            if amount <= EPSILON:
                continue
            world.ledger.transfer(
                source, world.get_stock(household.stock_id), "grain", amount,
                "relief", date,
            )
            world.bump("relief_given", amount)
            paid += amount
            break
        if debt_source is not None:
            record_relief(world, debt_source, household, want, paid, date)


def apply_hide_stores(world: World, date: SimDate) -> None:
    """Прятать часть зерна от волков в потайной сток."""
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None or household.minor_action != "hide_stores":
            continue
        stock = world.get_stock(household.stock_id)
        grain = stock.amounts.get("grain", 0.0)
        hide = grain * 0.5
        if hide <= EPSILON:
            continue
        world.ledger.transfer(stock, _hidden_stock(world, household), "grain", hide,
                              "hide", date)


def _by_tile(world: World) -> dict[str, list[Household]]:
    groups: dict[str, list[Household]] = {}
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None:
            continue
        groups.setdefault(household.current_tile_id, []).append(household)
    return groups


def _trading_allowed(world: World, household: Household) -> bool:
    """Может ли двор в этом месяце торговать (ADR 0140 п. 3).

    Все дворы торгуют всегда, кроме племенных при **военных отношениях** с
    племенем: закон хозяина — «по умолчанию с ними можно торговать», и закрыта
    торговля только при войне. Независимость племени поводом для закрытия не
    является (наоборот: независимое племя торгует свободно).
    """
    from .tribe import tribe_of_household, tribe_trade_open

    tribe = tribe_of_household(world, household)
    if tribe is None:
        return True
    return tribe_trade_open(world, tribe)


def _tradeable(world: World, households: list[Household]) -> list[Household]:
    return [h for h in households if _trading_allowed(world, h)]


def _tile_id(x: int, y: int) -> str:
    return f"t_{x:02d}_{y:02d}"


def _neighbor_clusters(world: World) -> dict[str, list[Household]]:
    """Кластеры соседского торга: разбиение (клетка + ортогональные соседи).

    Жадное разбиение по id: каждая жилая клетка ровно в одном кластере
    (двойных сделок нет). Порядок детерминирован. Дальний торг — только
    Pack-обозом (не этот проект). Дворы, которым торговля закрыта (племя при
    военных отношениях, ADR 0140 п. 3), в кластеры не входят вовсе — иначе они
    ещё и продавали бы.
    """
    base: dict[str, list[Household]] = {}
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is None and _trading_allowed(world, household):
            base.setdefault(household.current_tile_id, []).append(household)
    ids = set(base)
    adjacent: dict[str, set[str]] = {}
    for tid in ids:
        tile = world.tiles.get(tid)
        # Соседство — общий хелпер гекс-сетки (ADR 0071), шесть направлений.
        adjacent[tid] = set(neighbor_ids(world, tile)) & ids if tile is not None else set()
    done: set[str] = set()
    out: dict[str, list[Household]] = {}
    for tid in sorted(base):
        if tid in done:
            continue
        members: list[Household] = []
        for cell in sorted({tid} | adjacent[tid]):
            members.extend(base[cell])
            done.add(cell)
        out[tid] = members
    return out


def _cross_ok(buyer: Household, seller: Household, cross: bool) -> bool:
    """Годится ли продавец: чужой двор, а при `cross` — ещё и чужая клетка."""
    if seller.id == buyer.id:
        return False
    if cross and seller.current_tile_id == buyer.current_tile_id:
        return False
    return True


def _trade_pigs(
    world: World, members: list[Household], date: SimDate, cross: bool = False
) -> None:
    from .livestock import has_animal_access

    fee = PORTER_FEE if cross else 0.0
    for buyer in members:
        bstock = world.get_stock(buyer.stock_id)
        if bstock.amounts.get("pig", 0.0) > EPSILON:
            continue
        if not has_animal_access(world, buyer, bstock):
            continue
        if bstock.amounts.get("silver", 0.0) < PIG_PRICE_SILVER + fee:
            continue
        if food_months(world, buyer) < 1.0:
            continue
        seller = next(
            (
                s
                for s in members
                if _cross_ok(buyer, s, cross)
                and world.get_stock(s.stock_id).amounts.get("pig", 0.0) >= 2.0
            ),
            None,
        )
        if seller is None:
            continue
        sstock = world.get_stock(seller.stock_id)
        world.ledger.transfer(
            bstock, sstock, "silver", PIG_PRICE_SILVER + fee, "buy_pig", date
        )
        world.ledger.transfer(sstock, bstock, "pig", 1.0, "buy_pig", date)
        world.bump("pigs_bought")


def _trade_grain(
    world: World, members: list[Household], date: SimDate, cross: bool = False
) -> None:
    """Соседский торг (двор ↔ двор) по цене каталога (ADR 0144 п. 1).

    Цена зерна — `goods.yml::grain.price_silver`, то же число, что и в продаже
    амбара барона: дубля цены в коде не осталось. Соседский торг **не** берёт
    зерно из амбара барона, поэтому потолок закупки (ADR 0144 п. 2-3) к нему не
    относится: потолок держит голод на закупке у барона, а не цену на рынке
    между соседями.
    """
    price_of_grain = price_of(world, SELL_GOOD)
    if price_of_grain <= EPSILON:
        return
    for buyer in members:
        if food_months(world, buyer) >= 0.5:
            continue
        bstock = world.get_stock(buyer.stock_id)
        seller = next(
            (
                s
                for s in members
                if _cross_ok(buyer, s, cross) and food_months(world, s) > 2.5
            ),
            None,
        )
        if seller is None:
            continue
        sstock = world.get_stock(seller.stock_id)
        want = NEIGHBOR_GRAIN_MAX if cross else TRADE_GRAIN_MAX
        affordable = bstock.amounts.get("silver", 0.0) / price_of_grain
        amount = min(want, affordable, sstock.amounts.get("grain", 0.0) * 0.5)
        if amount > EPSILON:
            price = amount * price_of_grain
            bite = min(PORTER_BITE, sstock.amounts.get("grain", 0.0) - amount) if cross else 0.0
            world.ledger.transfer(bstock, sstock, "silver", price, "buy_grain", date)
            world.ledger.transfer(sstock, bstock, "grain", amount, "buy_grain", date)
            if bite > EPSILON:
                world.ledger.transfer(
                    sstock, world.get_stock(WASTE_STOCK_ID), "grain", bite,
                    "porter_loss", date,
                )
            world.bump("grain_bought", amount)
        elif sstock.amounts.get("grain", 0.0) > 3.0 and world.rng.economy.random() < 0.3:
            world.ledger.transfer(sstock, bstock, "grain", 1.0, "neighbour_gift", date)
            world.bump("grain_gifted")


def _trade_logs(
    world: World, members: list[Household], date: SimDate, cross: bool = False
) -> None:
    """Брёвна за зерно: стройка и топливо без рубки своим трудом."""
    price_of_log = LOG_PRICE_GRAIN
    fee = PORTER_FEE if cross else 0.0
    for buyer in members:
        bstock = world.get_stock(buyer.stock_id)
        if bstock.amounts.get("log", 0.0) >= 1.0:
            continue
        if food_months(world, buyer) < 1.0:
            continue
        seller = next(
            (
                s
                for s in members
                if _cross_ok(buyer, s, cross)
                and world.get_stock(s.stock_id).amounts.get("log", 0.0) >= 4.0
            ),
            None,
        )
        if seller is None:
            continue
        sstock = world.get_stock(seller.stock_id)
        want = min(
            TRADE_LOG_MAX,
            max(0.0, bstock.amounts.get("grain", 0.0) - fee) / price_of_log,
            sstock.amounts.get("log", 0.0),
        )
        if want <= EPSILON:
            continue
        world.ledger.transfer(
            bstock, sstock, "grain", want * price_of_log + fee, "buy_log", date
        )
        world.ledger.transfer(sstock, bstock, "log", want, "buy_log", date)
        world.bump("logs_bought", want)


def _mate_of(good: str) -> str | None:
    """Парный пол для проверки, что продажа не разбивает пару."""
    if good.endswith("_m"):
        return good[:-2] + "_f"
    if good.endswith("_f"):
        return good[:-2] + "_m"
    return None


def _keeps_pair(world: World, seller: Household, good: str) -> bool:
    """Можно ли продать голову, не разбив пару и не продав последнее рабочее.

    Разрешено, если после продажи останется пара (самец+самка) либо хотя бы
    две головы того же пола (будущая пара). Последнее и служебное не трогаем.
    """
    stock = world.get_stock(seller.stock_id)
    have = stock.amounts.get(good, 0.0)
    if have < 2.0 - EPSILON:
        return False
    mate = _mate_of(good)
    if mate is not None and stock.amounts.get(mate, 0.0) >= 1.0 - EPSILON:
        return True
    return have >= 3.0 - EPSILON


def _sellable(world: World, seller: Household, good: str) -> bool:
    """Можно ли продать голову: молодняк — излишек, взрослых — только сверх пары."""
    if good in YOUNG_GOODS:
        return world.get_stock(seller.stock_id).amounts.get(good, 0.0) >= 1.0 - EPSILON
    return _keeps_pair(world, seller, good)


def _trade_livestock(
    world: World, members: list[Household], date: SimDate, cross: bool = False
) -> None:
    """Скот за зерно: тягло покупается, а не появляется. Конь — только свободному."""
    from .livestock import can_hold_horse, has_animal_access, small_livestock_cap

    fee = PORTER_FEE if cross else 0.0
    # Кап мелкого скота НЕ зависит от товара и не меняется от сделок внутри этого
    # цикла: он выводится из надела, `works_tiles` и `world.rights`, а проводки
    # меняют только стоки. Поэтому считаем его ОДИН раз на двор кластера, а не
    # по разу на каждую пару (товар, двор): замер `v0_barony_100`, 200 дворов —
    # 13 товаров × 501 двор = 6513 расчётов капа против 501 нужного.
    # Лениво (по первому обращению): двор, у которого уже есть нужный товар,
    # до капа не доходит вовсе.
    caps: dict[str, int] = {}
    for good, price in sorted(LIVESTOCK_PRICE_GRAIN.items()):
        for buyer in members:
            bstock = world.get_stock(buyer.stock_id)
            if bstock.amounts.get(good, 0.0) > EPSILON:
                continue
            if good.startswith("horse_") and not can_hold_horse(world, buyer):
                continue
            cap = caps.get(buyer.id)
            if cap is None:
                cap = caps[buyer.id] = small_livestock_cap(world, buyer)
            if not has_animal_access(world, buyer, bstock, cap):
                continue
            if food_months(world, buyer) < 1.0:
                continue
            if bstock.amounts.get("grain", 0.0) < price + fee:
                continue
            seller = next(
                (
                    s
                    for s in members
                    if _cross_ok(buyer, s, cross)
                    and _keeps_pair(world, s, good)
                ),
                None,
            )
            if seller is None:
                continue
            sstock = world.get_stock(seller.stock_id)
            world.ledger.transfer(bstock, sstock, "grain", price + fee, "buy_beast", date)
            world.ledger.transfer(sstock, bstock, good, 1.0, "buy_beast", date)
            world.bump("beasts_bought")


def local_exchange(world: World, date: SimDate) -> None:
    """Обмен на клетке и с соседями: покупатель и продавец — свои или рядом.

    Свиньи за серебро, зерно, брёвна и скот за зерно — сначала соклеточники
    (те же цены, без сбора), затем соседский кластер (те же цены + сбор
    носильщику, зерно капом; дар — без сбора). Дальний торг — только
    Pack-обозом. Межклеточный торг — допуск ADR 0057.
    """
    for _tile_id, members in sorted(_by_tile(world).items()):
        trading = _tradeable(world, members)
        if len(trading) < 2:
            continue
        _trade_pigs(world, trading, date)
        _trade_grain(world, trading, date)
        _trade_logs(world, trading, date)
        _trade_livestock(world, trading, date)
    for _cluster_id, members in sorted(_neighbor_clusters(world).items()):
        if len(members) < 2:
            continue
        _trade_pigs(world, members, date, cross=True)
        _trade_grain(world, members, date, cross=True)
        _trade_logs(world, members, date, cross=True)
        _trade_livestock(world, members, date, cross=True)


# --- Продажа зерна из амбара барона (ADR 0139) ---


def _offer_key(manor_id: str) -> str:
    """Ключ прилавка в `world.stats`: сколько зерна выставлено на продажу."""
    return f"manor_offer_grain_{manor_id}"


def _manor_key(world: World, manor_id: str | None) -> str:
    """Ключ прилавка по книге: указанный манор или корень (амбар лорда)."""
    if manor_id is not None:
        return manor_id
    return str(getattr(root_manor(world), "id", ""))


def _book_manor(world: World, manor_id: str | None) -> Stock | None:
    """Амбар манора для продажи: указанная книга или корень (амбар лорда)."""
    if manor_id is not None:
        manor = world.manors.get(manor_id)
    else:
        manor = root_manor(world)
    if manor is None:
        return None
    return manor_stock(world, manor)


def _natural_plan(world: World, stock: Stock, due: float) -> dict[str, float]:
    """План натурального платежа: сколько какого товара двор отдаст на `due`.

    Товары берутся по алфавиту, по каталожной цене (`price_of`); товар, который
    продаётся, и серебро платёжом не являются. План ничего не двигает — его
    исполняет `_settle_sale`, поэтому покрытие платёжа и платёж считаются
    одним и тем же кодом.
    """
    plan: dict[str, float] = {}
    left = float(due)
    for good in sorted(stock.amounts):
        if good in (SELL_GOOD, "silver") or left <= EPSILON:
            continue
        price = price_of(world, good)
        have = stock.amounts.get(good, 0.0)
        if price <= EPSILON or have <= EPSILON:
            continue
        take = min(have, left / price)
        if take <= EPSILON:
            continue
        plan[good] = plan.get(good, 0.0) + take
        left -= take * price
    return plan


def _settle_sale(
    world: World,
    barn: Stock,
    buyer: Stock,
    amount: float,
    price: float,
    date: SimDate,
    reason: str,
) -> bool:
    """Сделка «зерно у барона → двору» по цене `price` за единицу.

    Покупатель платит из своего кармана: серебром, а на остаток — натуральным
    хозяйством (ADR 0139 п. 3). Всё или ничего: не хватило серебра и товара —
    сделка не состоялась, амбар не тронут. Зерно уходит двору в его сток, а не
    «на паёк» (ADR 0131, п. 4). Материя только переносится: дельта 0.
    """
    if price <= EPSILON or amount <= EPSILON:
        return False
    if barn.amounts.get(SELL_GOOD, 0.0) + EPSILON < amount:
        return False
    due = amount * price
    silver = buyer.amounts.get("silver", 0.0)
    silver_paid = min(silver, due)
    plan = _natural_plan(world, buyer, due - silver_paid)
    covered = silver_paid + sum(
        qty * price_of(world, good) for good, qty in sorted(plan.items())
    )
    if covered + EPSILON < due:
        return False
    if silver_paid > EPSILON:
        world.ledger.transfer(buyer, barn, "silver", silver_paid, reason, date)
    for good in sorted(plan):
        qty = plan[good]
        if qty > EPSILON:
            world.ledger.transfer(buyer, barn, good, qty, reason, date)
    world.ledger.transfer(barn, buyer, SELL_GOOD, amount, reason, date)
    return True


def offer_manor_surplus(world: World, manor_id: str | None = None) -> float:
    """Выставить на прилавок остаток амбара манора — месячный шаг ADR 0148.

    Прилавок здесь показывает, что амбар предлагает **сейчас**: остаток заменяет
    прежнюю выставку, а не копится. Копить незачем — непроданное зерно остаётся в
    амбаре и будет предложено снова в следующем месяце, а материя не должна ни
    дублироваться, ни прятаться (И-1). Числа-резерва нет: предлагается остаток
    после пайка, подмоги и посева, и больше ничего (ADR 0148 п. 3).

    Продаёт прилавок существующий `sell_listed_grain` (фаза `phase_exchange` того же
    месяца): цена каталога, потолок `purchase_allowance` на двор, общий счётчик
    закупки на оба канала и оба способа оплаты, relief лимит не тратит. Проводка
    вызывается из `economy/manor.py::manor_month` — не из движка (ADR 0148 п. 5).
    Возвращает, сколько выставлено.
    """
    barn = _book_manor(world, manor_id)
    if barn is None:
        return 0.0
    remainder = float(barn.amounts.get(SELL_GOOD, 0.0))
    if remainder <= EPSILON:
        return 0.0
    world.stats[_offer_key(_manor_key(world, manor_id))] = remainder
    return remainder


def _listed_amount(world: World, household: Household, remaining: float) -> float:
    """Сколько зерна двор берёт с прилавка: не больше своего недобора.

    Недобор — в «рот-единицах» (`food_shortfall`), зерно измеряется в единицах
    хранения, поэтому делим на питательность товара, как это делает подача.
    """
    good = world.catalogs.goods.get(SELL_GOOD)
    nutrition = float(good.nutrition) if good is not None and good.nutrition > 0 else 1.0
    shortfall = food_shortfall(world, household) / nutrition
    if shortfall <= EPSILON:
        return 0.0
    return min(remaining, shortfall)


def _listed_buy(world: World, household: Household, remaining: float) -> float:
    """Сколько зерна двор берёт с прилавка с учётом потолка закупки (ADR 0144 п. 2-3).

    Прилавок не обходит потолок: двор берёт не больше своего недобора **и** не
    больше остатка месячной нормы закупки. Счётчик один на канал и на оба способа
    оплаты (серебро и натуральный обмен) — натуральный обмен потолок не обходит.
    """
    want = _listed_amount(world, household, remaining)
    if want <= EPSILON:
        return 0.0
    return min(want, purchase_allowance_left(world, household))


def sell_listed_grain(world: World, date: SimDate | None = None) -> None:
    """Дворы покупают зерно, выставленное бароном в деревне (ADR 0139/0153).

    Это **единственный** канал продажи зерна из амбара барона ВНУТРЬ баронства.
    Покупают дворы книги своего манора и только своё недоборное зерно («по мере
    надобности»), по цене каталога — параметра цены здесь нет и быть не может
    (ADR 0153), — из кармана: серебром, иначе натуральным хозяйством
    (ADR 0139 п. 3). Пометка в бухгалтерии — `sell_grain_market`. Прилавок
    наполняет месячный шаг `offer_manor_surplus` (ADR 0148): остаток амбара
    после пайка, подмоги и посева. Месячный потолок закупки (ADR 0144 п. 2-3)
    держит `_listed_buy`; подача потолок не тратит.

    Продажа НАРУЖУ (`sell_grain_to_royal_court`, ADR 0219) здесь **не** вызывается
    намеренно. Прилавок — канал барона СВОИМ дворам, и его эффект на амбар
    закреплён обвинителями (`test_baron_granary_sale`): «сделка не состоялась —
    амбар не тронут», «40.0 зерна на месте». Второй слив из того же амбра внутри
    этой функции размывал бы оба закона сразу. Наружу продаёт
    `economy/manor.py::manor_month` — после пайка и ДО прилавка, чтобы прилавок
    выставлял ровно то, что в амбаре осталось.
    """
    when = world.clock.date if date is None else date
    price = price_of(world, SELL_GOOD)
    if price <= EPSILON:
        return
    for manor_id in sorted(world.manors):
        key = _offer_key(manor_id)
        remaining = float(world.stats.get(key, 0.0))
        if remaining <= EPSILON:
            continue
        manor = world.manors[manor_id]
        barn = manor_stock(world, manor)
        if barn is None:
            continue
        sold = 0.0
        for hid in sorted(world.households):
            if remaining - sold <= EPSILON:
                break
            household = world.households[hid]
            if household.left_at is not None:
                continue
            if manor_of_household(world, hid) is not manor:
                continue
            if not _trading_allowed(world, household):
                continue
            want = _listed_buy(world, household, remaining - sold)
            if want <= EPSILON:
                continue
            if not _settle_sale(
                world, barn, world.get_stock(household.stock_id), want, price, when,
                "sell_grain_market",
            ):
                continue
            _count_purchase(world, household, want)
            sold += want
        world.stats[key] = max(0.0, remaining - sold)
        if sold > EPSILON:
            world.bump("manor_grain_sold_market", sold)


# --- Продажа наружу: королевский двор платит серебром (ADR 0219) ---

ROYAL_COURT_RULE = "royal_court_buys_grain"
"""id ПРАВИЛА закупки королевского двора в `spawn_rules.yml`.

Это id, а не источник: значение по silver лежит в самом каталоге
(`params.good`, `params.source`, `params.bought_good`). Правила нет в каталоге —
продажи наружу не существует, и это `ValueError`, а не молчаливый ноль: иначе
закон хозяина «серебро не чеканится, приходит от торговли» исполнился бы
незаметно (ADR 0157 п. 1).
"""

SALE_OUT_REASON = "sell_to_royal_court"


def royal_court_rule(world: World) -> SpawnRule:
    """Правило закупки королевского двора — единственный источник серебра.

    Правила нет в каталоге — значит торговать наружу нечем, и это `ValueError`:
    тихо продавать ноль серебра значило бы объявить, что торг есть, а его нет.
    """
    rule = world.catalogs.spawn_rules.get(ROYAL_COURT_RULE)
    if rule is None:
        raise ValueError(
            f"В каталоге нет правила '{ROYAL_COURT_RULE}': серебро неоткуда взять, "
            "а оно обязано приходить извне (ADR 0219)"
        )
    return rule


def _manor_food_need(world: World, manor) -> float:
    """Месячная нужда в еде дворов книги манора (рот-единицы, как у обоза).

    Тот же счёт, что у `caravan.py::origin_monthly_food_need`, но по книге
    манора, а не по поселению: буфер удерживает амбар, и дворы, кормящиеся из
    него, — это дворы этой книги.
    """
    total = 0.0
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None:
            continue
        if manor_of_household(world, hid) is not manor:
            continue
        total += monthly_food_need(world, household)
    return total


def _shipment_reserve(world: World, bought_good: str) -> float:
    """Зерно, обещанное прибывающим дворам и ещё не выданное (ADR 0219 §буфер).

    Амбар барона — не только продовольствие. Из него же идёт **обзаведение
    прибывшего двора**: `engine/manor.py::arrive_household` везёт новому tenant'у
    `starting_stocks` из стока двора лорда (`reason="arrival_cargo"`). Продажа
    наружу, которая съела бы этот груз, не принесла бы серебра — она сломала бы
    приход: `Ledger._charge` откажет, пожалование не состоится, и следующий
    приказ скрипта (`grant_tenure` тому же двору) упадёт с `KeyError`.

    Это нашлось замером, а не догадкой: на `start_stand` амбар к 7-му месяцу
    содержал 579.8 зерна без продажи и 58.0 с ней, а двору волны нужно 180.

    Резерв считается по сценарию (`world.script`), а не по новой сущности:
    обещание сценария — это и есть долг, который амбар обязан закрыть. Уже
    прибывшие дворы вычеркнуты, прошлые месяцы тоже: бронь нужна только на
    будущее.
    """
    total = 0.0
    # `at_month` — номер месяца ПРОГОНА (`runner.py::MONTH_KEY`, сравнивается с
    # `month_index`), а не `year * 12 + month`. Путать эти две шкалы нельзя: при
    # первом году `year * 12 + month` даёт 18 вместо 6, и вся бронь молча
    # отбрасывается как «прошедшая».
    current = (world.clock.year - 1) * world.clock.months_per_year + world.clock.month
    for entry in world.script:
        if str(entry.get("action", "")) != "grant_tenement":
            continue
        household_id = str(entry.get("household", ""))
        if not household_id or household_id in world.households:
            continue
        at_month = entry.get("at_month")
        if at_month is None:
            continue
        try:
            when = int(at_month)
        except (TypeError, ValueError):
            continue
        if when < current:
            continue
        stocks = entry.get("starting_stocks")
        if not isinstance(stocks, dict):
            continue
        try:
            total += float(stocks.get(bought_good, 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
    return max(0.0, total)


def sell_grain_to_royal_court(world: World, date: SimDate) -> float:
    """Продать королевскому двору излишек амбара: зерно наружу, серебро в амбар.

    ЗАКОН ХОЗЯИНА: серебро в баронстве не чеканится — оно приходит от торговли,
    и единственный покупатель снаружи — королевский двор. Здесь он **не**
    рецепт и не `spawn_rule`-крану материала: это обмен с видимой внешней
    стороной, и обе его половины проходят через бухгалтерию.

    Пара проводок, а не одна (И-1, ADR 0219):

    | проводка      | что                                   |
    |---------------|---------------------------------------|
    | `external_out`| зерно уходит из мира совсем          |
    | `external_in` | серебро входит с `rule_id` правила    |

    Одна только вторая проводка была бы кражей: серебра в мире стало бы больше,
    а зерна не меньше. Пара означает, что `matter_delta` остаётся нулём сам по
    себе, а сверх того серебра не может быть больше, чем ушло зерна, — этот
    порядок проверяет `test_silver_enters_only_from_outside.py`.

    Что НЕ ломает:
    * **не чеканится** — нет рецепта серебра, нет внутреннего источника;
    * **цена — каталог** — `price_of(world, params.price_of)`, числа в коде нет;
    * **буфер еды** — `keep_months_food` месяцев нужды дворов книги, как
      `need_buffer_months` у обоза: деревня не голодает, продаётся излишек;
    * **буфер обзаведения** — плюс зерно, обещанное прибывающим дворам
      (`_shipment_reserve`): амбар кормит не только своих, но и везёт новым
      tenant'ам `starting_stocks`, и продажа, съевшая этот груз, сломала бы
      приход, а не принесла серебра;
    * **своим дворам — первым** — вызывается последним шагом `sell_listed_grain`,
      после внутреннего прилавка (ADR 0148 п. 2);
    * **куда девается серебро** — в сток амбара того же манора (`manor:<id>`,
      корень — `settlement:<id>`). Новой сущности нет: кошелёк барона уже
      существует, это его амбар, и в нём серебро уже ожидалось — жалованье
      найму (`manor.py::_hire`) и гэфоль (`_gafol`).

    Возвращает, сколько серебра поступило.
    """
    rule = royal_court_rule(world)
    source_name = str(rule.params.get("source", "") or "")
    bought_good = str(rule.params.get("bought_good", SELL_GOOD) or SELL_GOOD)
    silver_good = str(rule.params.get("good", "silver") or "silver")
    price_key = str(rule.params.get("price_of", bought_good) or bought_good)
    keep_months = float(rule.params.get("keep_months_food", 0.0) or 0.0)
    price = price_of(world, price_key)
    if price <= EPSILON:
        return 0.0
    gained = 0.0
    for manor_id in sorted(world.manors):
        manor = world.manors[manor_id]
        barn = manor_stock(world, manor)
        if barn is None:
            continue
        keep = keep_months * _manor_food_need(world, manor) + _shipment_reserve(
            world, bought_good
        )
        surplus = barn.amounts.get(bought_good, 0.0) - keep
        if surplus <= EPSILON:
            continue
        golds = surplus * price
        if golds <= EPSILON:
            continue
        # Сначала зерно наружу, потом серебро внутрь: пока зерно в мире, платить
        # не за что, и бухгалтерия не даст списать его из воздуха (`_charge`).
        world.ledger.external_out(
            barn, bought_good, surplus, SALE_OUT_REASON, date
        )
        world.ledger.external_in(
            barn, silver_good, golds, SALE_OUT_REASON, rule.id, date
        )
        world.bump("royal_court_grain_sold", surplus)
        world.bump("royal_court_silver_bought", golds)
        if source_name:
            world.bump(f"royal_court_silver_from_{source_name}", golds)
        gained += golds
    return gained
