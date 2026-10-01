"""Основать поселение и поселить в него двор: два приказа барона (ADR 0222).

Задача хозяина: «развивать баронство через повинности, инвестируя общий труд в
строительство дорог, **других деревень**, формирование доходов баронства на
продажу». Дороги (`engine/roadworks.py`) и повинности (`legal/actions.py`) в игре
есть, а **основать деревню** нельзя было: `settle_household` жил в
`engine/tile_view.py` как правило места, в `ACTION_HANDLERS` его не было, и
`docs/03_ontology.md` это признавал словами «отдельного приказа
`settle_household` нет». Третья ступень цепочки отсутствовала как действие.

## Почему два приказа, а не один

`found_village` и `settle_household` — **разные рычаги с разными отказами**, и
склеивать их в одну кнопку было бы браком по трём причинам:

1. **Разные проверки места.** Основание требует свободный гекс в книге корня и
   годное угодье; посадка требует `can_settle` — то есть свободное место под
   двор. Отказ второго внутри первого означал бы кнопку, которая то работает, то
   нет, и игрок не знает, какой из двух отказов его остановил.
2. **Разные цены и разные покупатели.** Основание платит за САМ участок
   (бревно в землю), посадка — за СЕМЬЮ, которую барон туда переселяет (зерно в
   амбар двора). Сметать две разные траты в одну — значит показать игроку одну
   цену за два разных действия.
3. **Разный такт.** Участок основывают один раз, а дворы сажают по одному, пока
   кап гекса (5) не кончится. Одна кнопка «основать деревню» заставила бы
   жать её пять раз подряд, и второй-третий раз она была бы отказом.

Обратная сторона честна и названа: игрок жмёт две кнопки там, где мечтал одну.
Это цена разделения отказов, и она мала — обе кнопки в одном месяце сценария.

## Откуда люди нового поселения

**Приказ не создаёт людей.** Это сознательный отказ от Population-механики:

* двор — существующая `Household`, а новая `Person` из приказа была бы новой
  сущностью без `SpawnRule` (И-7), а каталог `spawn_rules.yml` — зона
  Economist (`AGENTS.md` §2), куда Implementer не пишет;
* `external_in` без `rule_id` запрещён (И-1), а с `rule_id` пришлось бы
  выдумать правило появления — то же самое;
* люди нового поселения приходят **из баронства**: это отселок, двор от
  существующего поселения, который барон переводит на новый участок
  (`settle_household` двигает `Household.settlement_id` и место жительства).

Тот же двор-закон есть у разведки (`engine/scouting.py::settle_reported_discoveries`:
`Settlement.kind="farmstead"` + двор + `external_in` по правилу `ruin_site`), но
у неё разрешающий сигнал — **доставленный `Report`**, то есть открытие со
стороны. Здесь сигнал — приказ самого барона, и открытие его собственной
земли; материю он не создаёт, а перекладывает.

## works_tiles выбирает игрок

Рычаг приказа — `works_tiles` списком в записи сценария. Молчаливого закона по
рельефу не выдумывается: если бы клетки подбирались тихо по terrain, игрок
получал бы деревню, у которой угодья не те, и узнавал бы об этом по пустому
урожаю. Приказ проверяет, а не выбирает: клетки существуют, годное это место
(тот же гейт, что у участка — потому что `field_yield_factor` даёт ноль на
режиме без `plough`), клетки в книге корня и ещё не угодье чужого поселения
(двойное владение общиной — ложь, а не ошибка).

Полная цепочка приказов на новую деревню — четыре нажатия, и каждое из них
уже было в игре:

    set_tile_regime (участок и угодья) → found_village → settle_household → grant_tenure

## Чем стоит

Две траты, обе — `Ledger.transfer`, обе видны в журнале материи, и ни одна не
`external_in`: `matter_delta` остаётся нулём намертво.

| приказ | товар | цена | куда | почему столько |
|---|---|---|---|---|
| `found_village` | `log` | `FOUNDING_LOG = 6.0` | `sink:waste` | делёж и расчистка участка. 6.0 = две дороги по 3.0 (`roadworks.MATERIAL_AMOUNT`, ADR 0039) — цена не выдумана, а взята у соседнего приказа по стройке |
| `settle_household` | `grain` | `monthly_food_need` × `SEATING_MONTHS = 3.0` | сток двора | посев и первые три месяца хлеба, пока надел не родит. Три месяца — не наше число: ровно столько кладёт сценарий новому двору (`starting_food_months: 3.0` в `v0_barony_100.yml`) и ровно столько же возит `arrive_household` |

Бревно идёт в `sink:waste`, а не на склад поселения: бревно участка уходит в
землю и срубы, ровно как материал дороги (`engine/roadworks.py::start_work`).
Класть его в `Settlement.stores_stock_id` было бы мёртвым полем — общинная
кладовая поселения не источник подачи, и этот закон **отменён** (общинных
кладовых не заводим; живое правило читается в
`economy/exchange.py::relief_sources`), а новый амбар всё равно никто бы не
читал.

**Инвестиция общего труда отдельной строкой не нужна:** переселённый двор
работает угодьями СВОЕГО поселения (`economy/labor.py::_own_tiles_scan` отдаёт
двору `settlement.works_tiles`), поэтому его труд идёт на пашню новой деревни
сам, без новой механики.

## Гейт места — не лазейка

`settle_household` как правило (`engine/tile_view.py`) проверял только кап и
мог посадить двор на воду или в руину: рельеф и режим земли оставались на
совести вызывающего. Приказ зовёт `can_settle` сам и отказывает с **причиной по
причине** (вода / руина без жилья / режим `waste` / нет места), а не «не
вышло». `can_settle` остаётся единственной истиной о месте: приказ не
переписывает её правил, а объясняет её отказ.

## Проверка

```bash
PYTHONPATH=sim/src python3 -m unittest sim.tests.test_village_founding -v
```

Проверяет: приказ основания и приказ посадки в `ACTION_HANDLERS`; отказ посадки
на воду/руину/`waste`/полный гекс; цену в `Ledger` и `matter_delta == 0`;
один seed — один `state_hash`; мёртвый рычаг приказа (правка приказа без
создания поселения не двигает состояние мира).
"""

from __future__ import annotations

from ..economy.needs import monthly_food_need
from ..info.sources import EYE_FROM_HILL, MESSENGER
from ..news.propagation import make_report
from ..ontology import Household, Settlement, Stock
from ..world import World
from .hexgrid import axial_to_offset
from .manor import manor_of_tile, manor_stock, root_manor
from .tile_view import can_settle, household_count, tile_max_households

#: Правило места (`engine/tile_view.py::settle_household`) вызывается как
#: `tile_view.settle_household`, а не через `from ... import`: имя приказа и
#: имя правила совпадают, и прямой импорт дал бы рекурсию — приказ позвал бы
#: сам себя. Модуль импортирован целиком, и подстановка видна в строке вызова.
from . import tile_view

#: Виды поселения, которые барон может основать приказом.
#:
#: Закрытый список, а не «любой из пяти онтологии»:
#: * `hill_court` — корень один, `seat` зала на холме (`docs/03_ontology.md`);
#: * `native_village` — биекция с `Tribe` (ADR 0064): основание без племени
#:   сломало бы её;
#: * `salt_village` — солеварня требует ванн и запаса соли, а это отдельное
#:   предприятие с правилами найма; приказа на него в `PlayerAction` нет.
FOUNDABLE_KINDS: tuple[str, ...] = ("village", "farmstead")

#: Виды, которые основать нельзя, и причина по виду (видна игроку в отказе).
KIND_REFUSAL: dict[str, str] = {
    "hill_court": "зал на холме корень один, второго холма не бывает",
    "native_village": "племенная деревня живёт в биекции с Tribe (ADR 0064), а приказа на род нет",
    "salt_village": "солеварня — отдельное предприятие с ваннами и запасами соли, приказа на него нет",
}

#: Цена основания в бревне: делёж и расчистка участка уходят в землю.
#:
#: 6.0 = две дороги по 3.0 (`engine/roadworks.py::MATERIAL_AMOUNT["road"]`,
#: ADR 0039). Число не выбрано на глаз: цена стройки уже названа в том же
#: словаре, и участок под деревню — это две дороги работы плюс срубы.
FOUNDING_GOOD = "log"
FOUNDING_LOG = 6.0

#: Сколько месяцев хлеба барон кладёт переселённому двору (И-2: приказ меняет
#: стол дома, а не работу человека).
#:
#: 3.0 — не наше число: столько же сценарий кладёт новому двору
#: (`starting_food_months: 3.0`, `v0_barony_100.yml`) и столько же
#: `engine/manor.py::arrive_household` возит в `starting_stocks`.
SEATING_GOOD = "grain"
SEATING_MONTHS = 3.0

EPSILON = 1e-9
WASTE_STOCK_ID = "sink:waste"


def _log(world: World, action: str, **fields) -> dict:
    """Запись приказа в `World.player_actions` (та же форма, что у соседей)."""
    record = {
        "date": str(world.clock.date),
        "month": world.clock.month,
        "action": action,
    }
    record.update(fields)
    world.player_actions.append(record)
    return record


def _refusal_detail(world: World, tile_id: str) -> str:
    """Человеческая причина отказа `can_settle` — детализация, а не второй закон.

    Порядок — от физики к учёту (вода → руина → режим земли → кап), и каждая
    строка повторяет ровно то условие, которое `can_settle` уже отверг. Если
    ни одно не подошло (а `can_settle` всё равно False), возвращается честная
    «не сходится с правилом места» — выдумывать шестую причину нельзя.
    """
    tile = world.tiles[tile_id]
    if tile.terrain == "water":
        return f"клетка '{tile_id}' под водой: на воде ни поселение, ни двор не стоят"
    if (tile.terrain == "ruin" or bool(getattr(tile, "ruin_id", None))) and getattr(
        tile, "dwelling", None
    ) is None:
        return f"клетка '{tile_id}' — руина без жилья: селить не на что"
    if tile.regime_id in ("waste", "reserved_wood", "foreign"):
        return (
            f"клетка '{tile_id}': режим земли '{tile.regime_id}' места не даёт. "
            f"Сначала приказ set_tile_regime"
        )
    cap = tile_max_households(world, tile_id)
    return (
        f"на клетке '{tile_id}' дворов {household_count(world, tile_id)} "
        f"при капе {cap}: места нет"
    )


def settlement_refusal(world: World, tile_id: str) -> str | None:
    """Почему на клетке нельзя ни основать поселение, ни посадить двор.

    Одна функция на оба приказа, потому что отказ один: и `found_village`, и
    `settle_household` требуют места под поселение. `None` — место годное.

    **Истина о месте — `can_settle`, а не эта функция.** Здесь только слово:
    игрок обязан знать, ЧТО его остановило (вода / руина / режим `waste` / нет
    места), и молчаливый `False` был бы тем же браком, что ненажимаемая кнопка
    (ADR 0202). Расхождение между этой функцией и `can_settle` невозможно по
    построению: она вызывается только когда `can_settle` уже вернул `False`, и
    проверяет оба состояния одним и тем же вызовом
    (`test_refusal_agrees_with_can_settle_on_every_tile`).
    """
    tile = world.tiles.get(tile_id)
    if tile is None:
        return f"нет клетки '{tile_id}'"
    if can_settle(world, tile_id):
        return None
    return _refusal_detail(world, tile_id)


def _book_of_root(world: World, tile_id: str) -> bool:
    """В книге ли корневого манора клетка (закон `set_tile_regime`/`work_road`)."""
    owner = manor_of_tile(world, tile_id)
    root = root_manor(world)
    return owner is not None and root is not None and owner.id == root.id


def _claimable_works_tiles(
    world: World, works_tiles: list[str], site_tile_id: str
) -> list[str]:
    """Проверить угодья, которые игрок назвал приказом, и вернуть их по порядку.

    Проверяет шесть вещей и ни одной не чинит: список не пуст, клетка есть, клетка
    не место чужого поселения, клетка годное место (тот же гейт
    `settlement_refusal`, что и у участка), клетка в книге корня, клетка ещё не
    угодье чужого поселения. Повтор в списке — тоже отказ, иначе в
    `Settlement.works_tiles` попадёт два одинаковых id, а
    `economy/labor.py::_own_tiles_scan` дедуплицирует их молча, и игрок увидит в
    приказе два угодья, а в мире — одно.

    **Пустой список угодья — отказ, а не «основать пустое».** Поселение без
    угодья не пашет ничего, кроме собственного гекса: это было бы кнопкой без
    смысла, и игрок узнал бы о ней пустым урожаем через год. Закон назван
    прямо в отказе.

    **Угодье проходит тот же гейт места, что и участок.** Причина не в
    симметрии, а в урожае: `engine/yield_law.py::field_yield_factor` даёт 0 на
    режиме без `plough` (`waste`, `reserved_wood`), то есть угодье на пустоши
    не растёт НИЧЕГО. Если бы приказ молча принимал такое угодье, игрок узнал
    бы о нём пустым амбаром через год — ровно тот брак «ненажимаемой кнопки»,
    который репозиторий уже запретил в другом месте. Правильный путь тот же,
    что и для участка: приказ `set_tile_regime` сначала делает землю годной.
    """
    if not works_tiles:
        raise ValueError(
            "Приказ found_village без угодья: назови works_tiles — поселению не на чем "
            "работать, и клетки в индексе роста (`engine/growth.py`) не дадут"
        )
    claimed: dict[str, str] = {}
    for settlement in world.settlements.values():
        for tile_id in settlement.works_tiles:
            claimed.setdefault(tile_id, settlement.id)
    out: list[str] = []
    seen: set[str] = set()
    for raw in works_tiles:
        tile_id = str(raw)
        if tile_id in seen:
            raise ValueError(f"Угодье '{tile_id}' названо дважды в одном приказе")
        seen.add(tile_id)
        tile = world.tiles.get(tile_id)
        if tile is None:
            raise ValueError(f"Нет угодья '{tile_id}'")
        if tile_id == site_tile_id:
            raise ValueError(
                f"Угодье '{tile_id}' — сам участок поселения: он и так ухожен"
            )
        if tile.settlement_id is not None:
            raise PermissionError(
                f"Угодье '{tile_id}' — место поселения '{tile.settlement_id}': "
                f"у чужого поселения своим угодьям не отдают"
            )
        if tile.terrain == "water":
            raise ValueError(f"Угодье '{tile_id}' под водой")
        owner = claimed.get(tile_id)
        if owner is not None:
            raise ValueError(
                f"Угодье '{tile_id}' уже в угодьях поселения '{owner}': "
                f"община не бывает у двух поселений сразу"
            )
        refusal = settlement_refusal(world, tile_id)
        if refusal is not None:
            raise PermissionError(f"Угодье '{tile_id}' не годится: {refusal}")
        if not _book_of_root(world, tile_id):
            holder = manor_of_tile(world, tile_id)
            who = holder.id if holder is not None else "ничья"
            raise PermissionError(
                f"Угодье '{tile_id}' не в книге корня ('{who}'): "
                f"искать надо, а не сеять"
            )
        out.append(tile_id)
    return out


def _charge_founding(world: World, root_stock_id: str) -> float:
    """Списать бревно за участок с амбара корня в `sink:waste` (И-1: перевод).

    Отказ при нехватке — **до** любой правки мира: частичной выдачи не бывает,
    и matter при отказе не шевелится.
    """
    source = world.stocks.get(root_stock_id)
    if source is None:
        raise ValueError("У корневого манора нет амбара")
    available = source.amounts.get(FOUNDING_GOOD, 0.0)
    if available + EPSILON < FOUNDING_LOG:
        raise ValueError(
            f"В амбаре корня нет '{FOUNDING_GOOD}': есть {available}, "
            f"а основание участка стоит {FOUNDING_LOG}"
        )
    world.ledger.transfer(
        source,
        world.get_stock(WASTE_STOCK_ID),
        FOUNDING_GOOD,
        FOUNDING_LOG,
        "village_founding",
        world.clock.date,
    )
    return FOUNDING_LOG


def found_village(
    world: World,
    tile_id: str,
    works_tiles: list[str],
    name: str | None = None,
    kind: str = "village",
) -> Settlement:
    """Основать поселение на свободном гексе: участок, угодья, общий амбар.

    Что меняет приказ: `Settlement` (новое поселение), `Tile.settlement_id`
    участка, `Ledger` (бревно в `sink:waste`) и `World.player_actions`.
    **Ничего больше**: ни стоков дворов, ни прав, ни повинностей. Дворов
    приказ не создаёт — их сажает приказ `settle_household`, и люди приходят из
    баронства, а не из воздуха (см. модуль).

    Отказы (ход игры, не авария — их ловит `runner.ORDER_REFUSALS`):
    * клетки нет / она занята / вода / руина / режим `waste` → `settlement_refusal`;
    * `kind` не в `FOUNDABLE_KINDS` → `ValueError` с причиной из `KIND_REFUSAL`;
    * угодья пустые / не годное место / место чужого поселения / вода / не в
      книге корня / уже чужое угодье / повтор в списке;
    * в амбаре корня меньше `FOUNDING_LOG` бревна.
    """
    if kind in KIND_REFUSAL:
        raise ValueError(f"Вид '{kind}' не основать: {KIND_REFUSAL[kind]}")
    if kind not in FOUNDABLE_KINDS:
        raise ValueError(
            f"Вид '{kind}' вне {list(FOUNDABLE_KINDS)}: основать можно только их"
        )
    tile = world.tiles.get(tile_id)
    if tile is None:
        raise ValueError(f"Нет клетки '{tile_id}'")
    if tile.settlement_id is not None:
        raise PermissionError(
            f"На клетке '{tile_id}' уже поселение '{tile.settlement_id}'"
        )
    root = root_manor(world)
    if root is None:
        raise ValueError("Нет корневого манора игрока")
    if not _book_of_root(world, tile_id):
        owner = manor_of_tile(world, tile_id)
        who = owner.id if owner is not None else "ничья"
        raise PermissionError(
            f"Клетка '{tile_id}' не в книге корня ('{who}'): "
            f"на чужой земле поселение не заводят"
        )
    refusal = settlement_refusal(world, tile_id)
    if refusal is not None:
        raise PermissionError(refusal)
    site_works = _claimable_works_tiles(world, works_tiles, tile_id)

    settlement_id = f"village_{tile_id.removeprefix('t_')}"
    if settlement_id in world.settlements:
        raise ValueError(f"Поселение '{settlement_id}' уже есть")
    settlement_stock = Stock(
        id=f"settlement:{settlement_id}",
        owner_kind="settlement",
        owner_id=settlement_id,
    )
    _charge_founding(world, root.stock_id)
    world.stocks[settlement_stock.id] = settlement_stock
    settlement = Settlement(
        id=settlement_id,
        name=name or f"Деревня {tile_id}",
        kind=kind,
        coord=axial_to_offset(int(tile.coord[0]), int(tile.coord[1])),
        household_ids=[],
        stores_stock_id=settlement_stock.id,
        works_tiles=site_works,
    )
    world.settlements[settlement_id] = settlement
    tile.settlement_id = settlement_id
    _log(
        world,
        "found_village",
        settlement=settlement_id,
        tile=tile_id,
        kind=kind,
        name=settlement.name,
        works_tiles=list(site_works),
        price={FOUNDING_GOOD: FOUNDING_LOG},
    )
    make_report(
        world,
        EYE_FROM_HILL,
        "settlement",
        settlement_id,
        f"На клетке '{tile_id}' заложена деревня «{settlement.name}».",
        {
            "event": "village_founded",
            "settlement_id": settlement_id,
            "tile": tile_id,
            "kind": kind,
            "works_tiles": list(site_works),
        },
        world.clock.date,
        0,
        1.0,
        distorted=False,
        noise=0.0,
        observer_id=world.player.household_id or "player",
    )
    return settlement


def settle_household(world: World, household_id: str, tile_id: str) -> Household:
    """Поселить существующий двор в поселение: место, гейт и посевной хлеб.

    **Гейт места — здесь, а не на совести вызывающего.** `can_settle` проверяет
    рельеф, режим земли и кап; приказ зовёт его сам и отказывает с причиной по
    причине (`settlement_refusal`), поэтому на воду, в руину и на гекс режима
    `waste` двор не сядет.

    Что меняет приказ: `Household.current_tile_id` и `Household.settlement_id`,
    ростеры двух поселений (`household_ids`), `Ledger` (зерно в амбар двора) и
    `World.player_actions`. Не меняет: стоки, труд, режимы земли, права.
    Право и оброк по-прежнему выдаёт приказ `grant_tenure` — это его работа, а
    не посадки (И-2: барон режет право, а не сажает людей).

    Отказы: двора нет / он ушёл / клетки нет / клетка не под поселение /
    гейт места (`settlement_refusal`) / в амбаре корня меньше посевного хлеба.
    """
    household = world.households.get(household_id)
    if household is None:
        raise ValueError(f"Нет двора '{household_id}'")
    if household.left_at is not None:
        raise PermissionError(f"Двор '{household_id}' ушёл и не селится")
    if tile_id not in world.tiles:
        raise ValueError(f"Нет клетки '{tile_id}'")
    tile = world.tiles[tile_id]
    # Гейт места идёт ПЕРВЫМ, даже для клетки без поселения. Порядок не
    # косметика: на клетке режима `waste` без поселения игрок должен услышать
    # «сначала set_tile_regime», а не «сначала found_village» — иначе он
    # заложит поселение, разберётся с режимом и вернётся к отказу, который
    # был напечатан раньше настоящей причины.
    refusal = settlement_refusal(world, tile_id)
    if refusal is not None:
        raise PermissionError(refusal)
    if tile.settlement_id is None:
        raise PermissionError(
            f"На клетке '{tile_id}' нет поселения: сперва приказ found_village"
        )
    settlement = world.settlements.get(tile.settlement_id)
    if settlement is None:
        raise ValueError(f"Клетка '{tile_id}': нет поселения '{tile.settlement_id}'")

    root = root_manor(world)
    if root is None:
        raise ValueError("Нет корневого манора игрока")
    source = manor_stock(world, root)
    if source is None:
        raise ValueError("У корневого манора нет амбара")
    ration = float(monthly_food_need(world, household)) * SEATING_MONTHS
    if ration <= 0.0:
        raise ValueError(
            f"Двор '{household_id}' без ртов, а поселять некого: посев не за что купить"
        )
    available = source.amounts.get(SEATING_GOOD, 0.0)
    if available + EPSILON < ration:
        raise ValueError(
            f"В амбаре корня нет '{SEATING_GOOD}' на посев двора '{household_id}': "
            f"есть {available}, нужно {ration}"
        )

    old_settlement = world.settlements.get(household.settlement_id or "")
    # Правило места пишет в журнал свою строку (`tile_view._log`). Приказ пишет
    # после неё свою — с ценой, прежним поселением и новым, а строка правила
    # убирается: одно нажатие — одна строка в логе игрока. Без этого
    # `player_actions` показывал «settle_household» дважды, в первой строке без
    # цены, и игрок не понимал, где его приказ, а где работа правила места.
    # Само правило не меняется и зовётся ровно как раньше — чистится журнал.
    logged_from = len(world.player_actions)
    tile_view.settle_household(world, household_id, tile_id)
    del world.player_actions[logged_from:]
    if old_settlement is not None and old_settlement.id != settlement.id:
        if household_id in old_settlement.household_ids:
            old_settlement.household_ids.remove(household_id)
    household.settlement_id = settlement.id
    if household_id not in settlement.household_ids:
        settlement.household_ids.append(household_id)
    world.ledger.transfer(
        source,
        world.get_stock(household.stock_id),
        SEATING_GOOD,
        ration,
        "village_seating",
        world.clock.date,
    )
    _log(
        world,
        "settle_household",
        household=household_id,
        tile=tile_id,
        settlement=settlement.id,
        moved_from=old_settlement.id if old_settlement is not None else None,
        price={SEATING_GOOD: ration},
    )
    make_report(
        world,
        MESSENGER,
        "household",
        household_id,
        f"Двор '{household_id}' переселён на клетку '{tile_id}' в поселение "
        f"«{settlement.name}».",
        {
            "event": "household_settled",
            "household_id": household_id,
            "tile": tile_id,
            "settlement_id": settlement.id,
            "planned": True,
        },
        world.clock.date,
        0,
        0.8,
        distorted=False,
        noise=0.0,
        observer_id=world.player.household_id or "player",
    )
    return household