"""Манор как книга земли: корневой манор игрока и вложенный манор тэна.

Тэн — не класс юнита, а вложенный манор с другим долгом вверх
(`upward_bundle: thegn_service`). Игрок в v0 владеет одним корневым манором;
вложенность depth ≤ 1, дальше не жалуют. Действия игрока месячные и пишутся
в `world.player_actions`; земля и дворы переходят между манорами, стоки не
дублируются (материю никто не двигает — двигается только право).
"""

from __future__ import annotations

import random

from ..legal.actions import grant_tenure, revoke_tenure as _revoke_tenure
from ..legal.calendar import monthly_labor_days
from ..info.sources import CHANNELS, MESSENGER
from ..news.propagation import make_report
from ..ontology import Household, Manor, Person, Right, Stock
from ..world import World
from .hexgrid import neighbor_ids

THEGN_MAX_TILES = 3
THEGN_MAX_HOUSEHOLDS = 3
THEGN_UPWARD_BUNDLE = "thegn_service"
KIT_GOOD = "war_kit"
KIT_STOCK = "iron_bloom"
KIT_MIN_HOLDER = 1.0
REVOKE_BAD_MONTHS = 6
EPSILON = 1e-9


def _log(world: World, action: str, **fields) -> dict:
    """Записать месячное действие игрока в лог."""
    record = {"date": str(world.clock.date), "month": world.clock.month, "action": action}
    record.update(fields)
    world.player_actions.append(record)
    return record


#: Счётчик отказов приказов в `World.stats`. ADR 0215 §3: отказ — факт СОСТОЯНИЯ,
#: а не строка журнала, поэтому он обязан попадать в `state_hash` сам по себе.
ORDERS_REFUSED_KEY = "orders_refused"


def log_refused_order(world: World, action: str, reason: str, **fields) -> dict:
    """Записать ОТКАЗ приказа в лог и посчитать его — тем же путём, что у runner.

    Единая точка для обоих видов отказа, потому что отказов два вида и различать
    их нечем (ADR 0215 §Дыра 2):

    * приказ **бросил** `ValueError`/`PermissionError` — запись строит
      `runner._log_refusal`, перехватив исключение;
    * приказ **сам знает**, что не исполнился, и не может бросить исключение
      (он уже записал что-то в лог или вернул значение) — запись строит эта
      функция.

    Оба пишут `refused=True` + `reason` и двигают `world.stats["orders_refused"]`.
    Раньше второй вид отказа не помечался вовсе, и игрок читал в логе строку
    вида `grant_thegn_rejected reason=grants_limit` — то есть отказ, который
    выглядит как обычное действие. Хуже отсутствия записи: причина названа, но
    не помечена, и ни один обвинитель её не считает.
    """
    record = _log(world, action, refused=True, reason=reason, **fields)
    world.bump(ORDERS_REFUSED_KEY)
    return record


def root_manor(world: World) -> Manor | None:
    """Корневой манор игрока (parent_manor_id = None)."""
    if world.player_manor_id is None:
        return None
    return world.manors.get(world.player_manor_id)


def nested_manors(world: World) -> list[Manor]:
    """Вложенные маноры (тэны), по id."""
    return sorted(
        (m for m in world.manors.values() if m.parent_manor_id is not None),
        key=lambda m: m.id,
    )


def manor_of_household(world: World, household_id: str) -> Manor | None:
    """Манор, в книге которого числится двор: единая книга — `household.manor_id`.

    Если поле не задано (соляной держатель — равный, вне тяглой книги), двор
    не числится ни в одном маноре.
    """
    household = world.households.get(household_id)
    if household is None:
        return None
    if household.manor_id is not None:
        return world.manors.get(household.manor_id)
    for manor_id in sorted(world.manors):
        manor = world.manors[manor_id]
        if household_id in manor.household_ids:
            return manor
    return None


def manor_stock(world: World, manor: Manor) -> Stock | None:
    """Амбар манора (`Manor.stock_id`), или None, если сток не заведён."""
    if not manor.stock_id:
        return None
    return world.stocks.get(manor.stock_id)


def _create_manor_stock(world: World, manor_id: str) -> str:
    """Завести амбар тэна `manor:<id>`; без него пожалование запрещено.

    Если id занят не манором — отказ: третьего пути у амбара нет.
    """
    stock_id = f"manor:{manor_id}"
    existing = world.stocks.get(stock_id)
    if existing is not None:
        if existing.owner_kind != "manor":
            raise ValueError(f"Сток '{stock_id}' занят не манором")
        return stock_id
    world.add_stock(
        Stock(id=stock_id, owner_kind="manor", owner_id=manor_id, amounts={})
    )
    return stock_id


def manor_of_tile(world: World, tile_id: str) -> Manor | None:
    """Манор, в книге которого числится клетка."""
    for manor_id in sorted(world.manors):
        manor = world.manors[manor_id]
        if tile_id in manor.tile_ids:
            return manor
    return None


def manor_depth(world: World, manor: Manor) -> int:
    """Глубина вложенности: корень 0, тэн 1. Дальше в v0 не жалуют."""
    depth = 0
    seen: set[str] = set()
    parent_id = manor.parent_manor_id
    while parent_id is not None and parent_id not in seen:
        seen.add(parent_id)
        parent = world.manors.get(parent_id)
        if parent is None:
            break
        depth += 1
        parent_id = parent.parent_manor_id
    return depth


def _may_hold_land(world: World, household: Household) -> bool:
    """Вправе ли двор быть держателем клетки — те же два условия, что у найма.

    Постоянное держание не может достаться `landless`, а без вергельда надел не
    выдаётся вовсе (ADR 0193). Условия **не новые**: это ровно те, по которым
    `legal/actions.py::grant_tenure` отказал бы тому же двору. Приказ назначает
    держателем того, кому право выдать можно, и никого другого.
    """
    preset = world.catalogs.legal_statuses.get(household.legal_status_id)
    if preset is None:
        return False
    if not preset.wergeld:
        return False
    return preset.land_relation != "landless"


def _resident_holder(world: World, tile_id: str) -> Household | None:
    """Двор, которому режим вправе отдать клетку: тот, кто на ней стоит.

    Выбор детерминирован: дворы сортируются по id, порядок не зависит от порядка
    словаря (И-6). Двор, которому право держать землю нельзя, пропускается —
    приказ не назначает держателем того, кому `grant_tenure` отказал бы.
    """
    residents = sorted(
        (
            household
            for household in world.households.values()
            if household.current_tile_id == tile_id
        ),
        key=lambda household: household.id,
    )
    for household in residents:
        if _may_hold_land(world, household):
            return household
    return None


def _right_holder(world: World, tile_id: str) -> Right | None:
    """Индивидуальное право на клетку, если держатель уже назначен.

    `Right.kind == "common"` — общий доступ, а не держание (ADR 0060), поэтому
    он и не держит клетку за кем-либо, и не отменяет назначения.
    """
    found = sorted(
        (
            right
            for right in world.rights.values()
            if right.tile_id == tile_id and right.kind != "common"
        ),
        key=lambda right: right.id,
    )
    return found[0] if found else None


def appoint_regime_holder(world: World, tile_id: str) -> dict:
    """Назначить держателя клетки по её режиму (ADR 0198).

    «Кормит» в `land_regimes.yml` — это «кормит ДЕРЖАТЕЛЯ», а держателем клетки
    является двор, у которого на неё есть индивидуальное право (`Right`).
    Приказ, который меняет только `Tile.regime_id`, такого права не выдавал, и
    клетка уходила из домена вместе с `demesne_base_yield`, не доставаясь ни
    одному двору: все режимы на одной клетке давали одинаковый результат.

    Назначение — функция режима, и порядок разрешения один:

      * **режим не надел** (`demesne`/`waste`/`reserved_wood`/`foreign`) —
        назначать некого, приказ только пишет режим (`not_a_holding`);
      * **надел, а право уже есть** — держатель тот, кого игрок назначил
        раньше; приказ его не отбирает молча (`kept`);
      * **надел, права нет, на клетке стоит двор** — приказ выдаёт ему `Right`
        (`granted`);
      * **надел, права нет, двор никто не занимает** — клетка **ждёт** двора
        (`awaited`): режим обещает кормление держателю, которого ещё нет, и
        подменять его доменом или общиной значило бы выдумать третье состояние
        земли, которого нет в каталоге. Правом остаётся игрок: `grant_tenure`
        назначает двор, и клетка начинает кормить с той же клетки.

    Право выдаётся **без повинности**: `rent_share = 0.0`, потому что приказ
    меняет режим земли, а оброк оформляет `grant_tenure` вместе со сменой
    пресета. Назначение книгой дворов манора не занимается — это делает тот же
    `grant_tenure`.
    """
    tile = world.tiles.get(tile_id)
    if tile is None:
        return {"appointment": "not_a_holding", "holder": None, "right": None}
    regime = world.catalogs.land_regimes.get(tile.regime_id)
    if regime is None or not regime.feeds_household:
        return {"appointment": "not_a_holding", "holder": None, "right": None}
    existing = _right_holder(world, tile_id)
    if existing is not None:
        return {
            "appointment": "kept",
            "holder": existing.holder_household_id,
            "right": existing.id,
        }
    holder = _resident_holder(world, tile_id)
    if holder is None:
        return {"appointment": "awaited", "holder": None, "right": None}
    right = Right(
        id=f"right_regime_{tile_id}_{holder.id}",
        holder_household_id=holder.id,
        tile_id=tile_id,
        kind="tenure",
        granted_date=world.clock.date,
        rent_share=0.0,
    )
    world.rights[right.id] = right
    return {"appointment": "granted", "holder": holder.id, "right": right.id}


def set_tile_regime(world: World, tile_id: str, regime_id: str) -> bool:
    """Сменить режим/надел клетки (действие игрока) и назначить её держателя.

    Рельеф клетки в приказ попадает **всегда**, а не только когда он совпал с
    режимом. Причина — не вежливость, а отсутствие закона: `demesne` на лесу
    разрешён и обязателен, потому что `clear_forest` даёт только домен
    (ADR 0175 п. 5), поэтому «домен на лесу» — не ошибка приказа, а его
    содержание. Но раньше приказ проходил молча: в книге оставалось
    `demesne`, и нигде не было видно, что клетка под этим режимом остаётся
    лесом и кормит не пашней, а расчисткой. Рельеф в записи — это и есть
    разница между «пашня усадьбы» и «лес под расчистку»; без неё игрок читает
    свою же книгу и не понимает, почему лес не засеяли.

    **Режим и держатель — одна запись.** Надел без `Right` не кормит никого
    (ADR 0198), поэтому смена режима на наделную обязана назначить и держателя,
    иначе приказ выкидывал клетку из производства, никого не награждая. Само
    назначение — `appoint_regime_holder`; в лог приказа идёт его исход
    (`granted`/`kept`/`awaited`/`not_a_holding`), чтобы игрок видел, клетка ли
    теперь под кем-то стоит или ждёт двора.
    """
    if regime_id not in world.catalogs.land_regimes:
        raise ValueError(f"Неизвестный режим земли '{regime_id}'")
    tile = world.tiles.get(tile_id)
    if tile is None:
        raise ValueError(f"Нет клетки '{tile_id}'")
    manor = manor_of_tile(world, tile_id)
    if manor is not None and manor.parent_manor_id is not None:
        raise PermissionError(
            f"Клетка '{tile_id}' в книге тэна '{manor.id}': сначала отзови пожалование"
        )
    tile.regime_id = regime_id
    if manor is not None:
        manor.tile_regimes[tile_id] = regime_id
    _log(
        world,
        "set_tile_regime",
        tile=tile_id,
        regime=regime_id,
        terrain=tile.terrain,
        regime_on_other_terrain=tile.terrain != "field",
        **appoint_regime_holder(world, tile_id),
    )
    return True


def _arrival_template(world: World, household_id: str) -> dict | None:
    """Найти сценарные данные прибывающего двора в существующем списке script."""
    required = ("name", "settlement_id", "adults", "children", "legal_status")
    for entry in world.script:
        if str(entry.get("action")) != "grant_tenement":
            continue
        if str(entry.get("household")) != household_id:
            continue
        if all(key in entry for key in required):
            return entry
    return None


def arrive_household(world: World, household_id: str, tile_id: str) -> Household | None:
    """Создать двор и его людей в момент сценарного прихода."""
    if household_id in world.households:
        return world.households[household_id]
    template = _arrival_template(world, household_id)
    if template is None:
        return None
    if tile_id not in world.tiles:
        raise ValueError(f"Нет клетки прибытия '{tile_id}'")
    settlement_id = str(template["settlement_id"])
    settlement = world.settlements.get(settlement_id)
    if settlement is None:
        raise ValueError(f"Двор '{household_id}': нет поселения '{settlement_id}'")
    status_id = str(template["legal_status"])
    preset = world.catalogs.legal_statuses.get(status_id)
    if preset is None:
        raise ValueError(f"Двор '{household_id}': неизвестный пресет '{status_id}'")
    adults = int(template.get("adults", 0))
    children = int(template.get("children", 0))
    elders = int(template.get("elders", 0))
    if min(adults, children, elders) < 0:
        raise ValueError(f"Двор '{household_id}': отрицательное число людей")
    members: list[str] = []
    prng = random.Random(f"{world.seed}:persons:{household_id}")
    for number in range(1, adults + children + elders + 1):
        person_id = f"{household_id}_p{number}"
        if number <= adults:
            age_class = "adult"
            age_months = 240
        elif number <= adults + children:
            age_class = "child"
            age_months = 12
        else:
            age_class = "elder"
            age_months = 600
        world.persons[person_id] = Person(
            id=person_id,
            name=person_id,
            household_id=household_id,
            age_class=age_class,
            curiosity=prng.random(),
            fear=prng.random(),
            health=1.0,
            location_tile_id=tile_id,
            age_months=age_months,
        )
        members.append(person_id)
    stock = Stock(id=f"household:{household_id}", owner_kind="household", owner_id=household_id)
    world.add_stock(stock)
    starting_stocks = template.get("starting_stocks") or {}
    if not isinstance(starting_stocks, dict):
        raise ValueError(f"Двор '{household_id}': starting_stocks должен быть словарём")
    if starting_stocks:
        source = world.get_stock("settlement:" + world.player.court_settlement_id)
        for good, raw_amount in sorted(starting_stocks.items()):
            amount = float(raw_amount)
            if amount <= 0.0:
                raise ValueError(f"Двор '{household_id}': стартовый товар должен быть положительным")
            world.ledger.transfer(
                source, stock, str(good), amount, "arrival_cargo", world.clock.date
            )
    household = Household(
        id=household_id,
        name=str(template["name"]),
        settlement_id=settlement_id,
        member_ids=members,
        stock_id=stock.id,
        labor_days=float(adults) * 20.0,
        obligation_ids=[],
        hunger_days=0,
        arrears_days=0,
        mood=0.7,
        intent="stay",
        current_tile_id=tile_id,
        main_action="work_plot",
        minor_action="idle_repair",
        legal_status_id=status_id,
        personal_status=preset.personal_status,
        land_relation=preset.land_relation,
        obligation_bundle=preset.obligation_bundle,
        holding_scale=float(getattr(world.manor, "holding_tiles_by_land_kind", {}).get(preset.land_kind, 1.0)),
    )
    world.households[household_id] = household
    if household_id not in settlement.household_ids:
        settlement.household_ids.append(household_id)
    make_report(
        world,
        MESSENGER,
        "household",
        household_id,
        f"Семья {household_id} прибыла и ждёт надела.",
        {
            "event": "arrival",
            "household_id": household_id,
            "members": len(members),
            "tile": tile_id,
            "settlement_id": settlement_id,
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


def grant_tenement(
    world: World,
    household_id: str,
    tile_ids: list[str],
    kind: str = "tenure",
    rent_share: float = 0.1,
    status_id: str | None = None,
    temporary: bool = False,
) -> list[Right]:
    """Дать держание через единый переход права и повинностей."""
    household = world.households.get(household_id)
    if household is None:
        if not tile_ids:
            raise ValueError(f"Нет двора '{household_id}'")
        household = arrive_household(world, household_id, tile_ids[0])
    if household is None:
        raise ValueError(f"Нет двора '{household_id}'")
    manor = manor_of_household(world, household_id) or root_manor(world)
    if manor is None:
        raise ValueError("Нет манора для держания")
    seen: set[str] = set()
    for tile_id in tile_ids:
        if tile_id in seen:
            raise ValueError(f"Клетка '{tile_id}' повторяется в держании")
        seen.add(tile_id)
        if tile_id not in world.tiles:
            raise ValueError(f"Нет клетки '{tile_id}'")
        owner = manor_of_tile(world, tile_id)
        if owner is not None and owner.id != manor.id:
            raise PermissionError(
                f"Клетка '{tile_id}' в книге '{owner.id}': нельзя отдать дважды"
            )
    rights: list[Right] = []
    for tile_id in tile_ids:
        rights.append(
            grant_tenure(
                world,
                household_id,
                tile_id,
                kind=kind,
                rent_share=rent_share,
                status_id=status_id,
                temporary=temporary,
            )
        )
    _log(
        world,
        "grant_tenement",
        household=household_id,
        tiles=list(tile_ids),
        manor=manor.id,
    )
    return rights


def revoke_tenure(
    world: World,
    household_id: str,
    right_id: str | None = None,
) -> list[str]:
    """Отозвать держание через единый юридический переход."""
    return _revoke_tenure(world, household_id, right_id)


GRANT_TOOL_GOODS = ("iron_share", "iron", "iron_bloom")


def grant_tool(world: World, household_id: str, good: str, amount: float) -> None:
    """Выдать двору инструмент/железо из стока корневого манора (лорда).

    Инструмент — материя: это перевод `Ledger.transfer(..., "grant_tool")`,
    а не флаг. Источник — амбар корневого манора (`manor_stock`); получатель —
    сток двора. В v0 жалуют железо для службы: `iron_share` (лемех),
    сырое `iron` и `iron_bloom` (крицу — сырьё боевых комплектов `war_kit`).
    Если у лорда меньше
    запрошенного, отказ ДО любого перевода: частичной выдачи не бывает и
    материя не создаётся.
    """
    if good not in GRANT_TOOL_GOODS:
        raise ValueError(f"Действие v0 жалует только {GRANT_TOOL_GOODS}, а не '{good}'")
    if amount <= 0:
        raise ValueError("Количество инструмента должно быть положительным")
    household = world.households.get(household_id)
    if household is None:
        raise ValueError(f"Нет двора '{household_id}'")
    root = root_manor(world)
    if root is None:
        raise ValueError("Нет корневого манора игрока")
    source = manor_stock(world, root)
    if source is None:
        raise ValueError("У корневого манора нет амбара")
    available = source.amounts.get(good, 0.0)
    if available + EPSILON < amount:
        raise ValueError(
            f"У лорда нет '{good}': в амбаре {available}, просят {amount}"
        )
    target = world.get_stock(household.stock_id)
    world.ledger.transfer(source, target, good, amount, "grant_tool", world.clock.date)
    _log(world, "grant_tool", household=household_id, good=good, amount=amount)


GRAIN_GOOD = "grain"


def grant_grain(world: World, household_id: str, amount: float) -> None:
    """Выдать двору зерно из амбара лорда (действие игрока, приказ `grant_grain`).

    Зерно — материя: это перевод `Ledger.transfer(..., "grant_grain")`, а не флаг.
    Источник — амбар КОРНЕВОГО манора (`manor_stock(root_manor)`), тот же, что у
    `grant_tool`: приказ отдаёт лорд, и хлеб берётся из его книги, а не из
    общей кладовой поселения (подача по `relief_sources` бьёт по книге сеньора
    двора, приказ бьёт по книге корня — два разных адресата, и путать их нельзя).
    Получатель — сток ИМЕННО этого двора (`Household.stock_id`): зерно падает в
    амбар напротив его рта, а не в общий амбар поселения.

    Ручной разовой выдачи, а не подачи: повинность `relief_granted` не пишется и
    `relief_given` не растёт — плательщик другой (И-2: приказ меняет стол двора,
    а не приказывает человеку пахать). Если у лорда меньше запрошенного — отказ
    ДО любого перевода: частичной выдачи не бывает и материя не создаётся.

    **Почему всё-таки не в книгу подачи, хотя книга для этого и есть** (ADR 0169
    держал бы выдачу «подачей»): закон на книге подачи — `paid_total <= due_amount`
    по каждой записи, где `due_amount` есть **недобор** двора
    (`sim/tests/test_relief_is_recorded.py`). Выдача по приказу больше недобора по
    построению — в этом её смысл (плейтест выдал двору 60.0 зерна при недоборе
    4.4). Хуже, чем несовпадение: `apply_relief` в том же месяце дописывает свою
    долю в **ту же** запись (id — пара «сеньор, двор, месяц»), и тогда
    `paid_total` разошёлся бы с `due_amount` дважды. Своя проводка и запись в
    `player_actions` — цена лорда видна в книге учёта, а долг сеньора не растёт.

    **Два гейта, которых у `grant_tool` нет, а здесь необходимы:** ушедший двор
    кормить некому, а двор вне книги лорда (`manor_of_household` — None, соляной
    держатель) получает хлеб из чужого амбара. Подача из
    `economy/exchange.py::relief_sources` тот же отказ знает словом «источников
    нет», и приказ не должен быть щедрее закона.
    """
    if amount <= 0:
        raise ValueError("Количество зерна должно быть положительным")
    household = world.households.get(household_id)
    if household is None:
        raise ValueError(f"Нет двора '{household_id}'")
    if household.left_at is not None:
        raise PermissionError(f"Двор '{household_id}' ушёл, кормить некого")
    if manor_of_household(world, household_id) is None:
        raise PermissionError(
            f"Двор '{household_id}' не в книге лорда: из его амбара не кормят"
        )
    root = root_manor(world)
    if root is None:
        raise ValueError("Нет корневого манора игрока")
    source = manor_stock(world, root)
    if source is None:
        raise ValueError("У корневого манора нет амбара")
    available = source.amounts.get(GRAIN_GOOD, 0.0)
    if available + EPSILON < amount:
        raise ValueError(
            f"У лорда нет зерна: в амбаре {available}, просят {amount}"
        )
    target = world.get_stock(household.stock_id)
    world.ledger.transfer(
        source, target, GRAIN_GOOD, amount, "grant_grain", world.clock.date
    )
    _log(
        world,
        "grant_grain",
        household=household_id,
        good=GRAIN_GOOD,
        amount=amount,
    )
    # Двор узнаёт о хлебе с задержкой, как о любой вести: иначе зерно в его руках
    # появляется без следа, и «почему у меня 60 зерна» — вопрос без ответа.
    channel = CHANNELS[MESSENGER]
    make_report(
        world,
        MESSENGER,
        "household",
        household_id,
        f"Сеньор прислал двору '{household_id}' зерна: {amount:.1f}.",
        {"event": "grain_granted", "household_id": household_id, "amount": amount},
        world.clock.date,
        channel.delay_months,
        channel.confidence,
        distorted=False,
        noise=channel.noise,
    )


def assign_work(
    world: World,
    household_id: str,
    action: str,
    minor_action: str | None = None,
) -> tuple[str, str]:
    """Приказ лорда двору: какое дело главное (и какое мелкое) в этом месяце.

    **Первое настоящее действие игрока, которого не было.** Замер стенда
    (`start_stand`, сид 1729, 24 мес): пять дворов `INITIAL_FAMILIES` стояли в
    `request_relief` месяцами, а пахали единицы, потому что `request_relief`
    занимает **главный** слот, а минорный слот отдаётся пашне только когда на
    кормящем гексе двора **стоит урожай** (`economy/decisions.py`, ADR 0113 п. 1).
    У двора, который просит подачу и у которого на гексе ничего не выросло, оба
    слота не пашня, а лорд не имел ни одного рычага это прекратить: приказа
    назначить не было вообще. Двор без работы не собирает, двор без урожая
    просит подачу, подача не пашня — круг, из которого выхода нет.

    **Приказ месячный, как все приказы игрока.** `engine/tick.py::phase_decide` в
    конце месяца переписывает выбор двора (`decisions.plan_next_month`), поэтому
    приказ держит труд на текущий месяц, и чтобы держать двор на пашне, приказ
    повторяют каждый месяц — ровно как `set_tile_regime` и `grant_tool`. Скрытого
    «постоянного» состояния здесь нет намеренно: второе состояние без
    потребителя (ADR 0175 §6) — тот же брак, что флаг «расчистано».

    **Закон приказом не обходится.** Дело должно быть в `allowed_action_ids`
    двора, то есть в наборе его правового пресета: у раба одно дело
    (`demesne_labor`), лорд и тэн не пашут вовсе (`oversee` — их единственное
    дело), а двору вне книги приказать и нельзя. Клетку приказ не выбирает:
    `work_plot` безземельного двора — это его собственные кормящие гексы
    (ADR 0113 п. 1), а не чужой надел, и подставить клетку в приказ нечем.
    Приказ без закона — это читерство, а не власть.

    Приказ известен двору **с задержкой** (`MESSENGER`): иначе он невидим, и
    вопрос «лорд приказал, а двор не пашет» неразрешим. Материя не движется —
    приказ меняет дело месяца, а не хлеб.
    """
    from ..economy.decisions import allowed_action_ids

    household = world.households.get(household_id)
    if household is None:
        raise ValueError(f"Нет двора '{household_id}'")
    if household.left_at is not None:
        raise PermissionError(f"Двор '{household_id}' ушёл, приказывать ему некого")
    if manor_of_household(world, household_id) is None:
        raise PermissionError(
            f"Двор '{household_id}' не в книге лорда: приказывать некому"
        )
    if action not in world.household_actions:
        raise ValueError(f"Неизвестное дело двора '{action}'")
    allowed = allowed_action_ids(world, household)
    if action not in allowed:
        raise PermissionError(
            f"Двору '{household_id}' пресет '{household.legal_status_id}' не даёт "
            f"дела '{action}' (можно: {sorted(allowed)})"
        )
    minor = minor_action if minor_action is not None else household.minor_action
    if minor != "idle_repair":
        if minor not in world.household_actions:
            raise ValueError(f"Неизвестное мелкое дело двора '{minor}'")
        if minor not in allowed:
            raise PermissionError(
                f"Двору '{household_id}' пресет '{household.legal_status_id}' не даёт "
                f"мелкого дела '{minor}' (можно: {sorted(allowed)})"
            )
    household.main_action = action
    household.minor_action = minor
    _log(
        world,
        "assign_work",
        household=household_id,
        main=action,
        minor=minor,
        status=household.legal_status_id,
    )
    channel = CHANNELS[MESSENGER]
    make_report(
        world,
        MESSENGER,
        "household",
        household_id,
        f"Сеньор приказал двору '{household_id}': дело «{action}».",
        {
            "event": "work_assigned",
            "household_id": household_id,
            "main": action,
            "minor": minor,
        },
        world.clock.date,
        channel.delay_months,
        channel.confidence,
        distorted=False,
        noise=channel.noise,
    )
    return action, minor


def call_boon(world: World, month: int | None = None) -> bool:
    """Крикнуть помочь (bene) в месяц с `boon_allowed`; иначе **отказ закона**.

    **Почему отказ бросается, а не возвращается `False` (ADR 0215 §Дыра 2).**
    Раньше здесь стояло `return False`, и это был худший вид брака: приказ
    возвращал «не исполнилось», но **никто его не спрашивал**. `runner.
    _dispatch_call_boon` результат выбрасывал, `runner._apply_script_entry` видел,
    что приказ не записал в лог ничего, и дописывал синтетическую запись
    `{date, month, action}` — **без полей и без `refused`**. Игрок жал кнопку в
    M1, получал в логе ту же строку, что при успехе в M7, и не получал ничего:
    восемь месяцев из двенадцати кнопка «помочь» была мёртвой, а выглядела
    живой.

    Закон уже был — ADR 0202: отказ приказа есть ход игры, он логируется с
    причиной и не обрывает прогон. `call_boon` его обходил, потому что отказывал
    не исключением, а значением. Теперь отказ — `ValueError`, и он идёт **тем же**
    путём, что «у лорда нет зерна»: `runner._log_refusal` пишет `refused=True` и
    `reason`, сводка печатает `ОТКАЗ`, счётчик `orders_refused` растёт, прогон идёт
    дальше.

    Сообщение обязано называть **и месяц, и закон**: `boon_allowed` у месяца —
    единственная причина отказа, а «помочь нельзя» без числа не объясняет
    игроку, что делать дальше (ждать M6 или не ждать вовсе).
    """
    number = month if month is not None else world.clock.month
    calendar_month = world.calendar.get(number)
    if calendar_month is None:
        raise ValueError(
            f"Крик помощи: месяца '{number}' нет в календаре смены (1..{len(world.calendar)})"
        )
    if not calendar_month.boon_allowed:
        allowed = sorted(
            key for key, value in world.calendar.items() if value.boon_allowed
        )
        raise ValueError(
            f"Крик помочи (bene) в месяце M{number} законом не разрешён "
            f"(boon_allowed: false). Помочь можно в месяцах "
            f"{', '.join('M' + str(key) for key in allowed) or '—'}"
        )
    world.stats["boon_called_month"] = float(number)
    _log(world, "call_boon", month=number)
    return True


def ease_week_work(world: World, month: int, delta: float) -> float:
    """Урезать барщину пика на `delta` трудодней месяца (действие игрока).

    Значение в единицах месяца (не недели) хранится на мире и вычитается
    экономикой из сезонного долга; больше долга урезать нельзя.
    """
    if delta < 0:
        raise ValueError("delta не может быть отрицательной")
    if month not in world.calendar:
        raise ValueError(f"Нет месяца '{month}' в календаре")
    cap = max(
        monthly_labor_days(world, month, preset)
        for preset in ("villein", "cotter")
    )
    value = min(float(delta), cap)
    world.stats[f"eased_days_{world.clock.year}_{month}"] = value
    _log(world, "ease_week_work", month=month, delta=value)
    return value


def eased_days(world: World, month: int) -> float:
    """Сколько трудодней месяца урезано действием игрока (в этом году)."""
    return float(world.stats.get(f"eased_days_{world.clock.year}_{month}", 0.0))


def _set_thegn_preset(world: World, household_id: str) -> None:
    """Двор тэна получает пресет thegn (вложенный манор, долг вверх)."""
    household = world.households.get(household_id)
    preset = world.catalogs.legal_statuses.get("thegn")
    if household is None or preset is None:
        return
    household.legal_status_id = "thegn"
    household.personal_status = preset.personal_status
    household.land_relation = preset.land_relation
    household.obligation_bundle = preset.obligation_bundle


def _sweep_holder_iron_to_barn(world: World, manor: Manor) -> None:
    """Свести крицу двора держателя в амбар фьефа (военная казна).

    Держатель сам не куёт (его руки — служба, не наковальня): крица в его
    личном стоке пылилась бы без дела, а комплекты собираются из амбара.
    Перевод внутри одной книги, материя не создаётся.
    """
    person = world.persons.get(manor.holder_person_id)
    household = world.households.get(person.household_id) if person else None
    barn = manor_stock(world, manor)
    if household is None or barn is None:
        return
    source = world.get_stock(household.stock_id)
    amount = source.amounts.get(KIT_STOCK, 0.0)
    if amount > EPSILON:
        world.ledger.transfer(
            source, barn, KIT_STOCK, amount, "thegn_warchest", world.clock.date
        )


def holder_household(world: World, manor: Manor):
    """Двор держателя манора (его стол и его комплект решают службу)."""
    person = world.persons.get(manor.holder_person_id)
    if person is None:
        return None
    return world.households.get(person.household_id)


def holder_kits(world: World, manor: Manor) -> float:
    """Комплектов на самом держателе (сток его двора)."""
    household = holder_household(world, manor)
    if household is None:
        return 0.0
    return float(world.get_stock(household.stock_id).amounts.get(KIT_GOOD, 0.0))


def fief_kits(world: World, manor: Manor) -> float:
    """Комплектов в книге фьефа: амбар + двор держателя. Можно посчитать."""
    total = holder_kits(world, manor)
    barn = manor_stock(world, manor)
    if barn is not None:
        total += float(barn.amounts.get(KIT_GOOD, 0.0))
    return total


def men_held(world: World, manor: Manor) -> float:
    """Явки фьефа: живые взрослые двора держателя (его смена и серванты)."""
    household = holder_household(world, manor)
    if household is None:
        return 0.0
    return float(
        sum(
            1
            for pid in household.member_ids
            if (p := world.persons.get(pid)) is not None
            and p.age_class == "adult"
            and p.health > 0
        )
    )


def _tile_id(x: int, y: int) -> str:
    return f"t_{x:02d}_{y:02d}"


def guard_ring(world: World, manor: Manor) -> list[str]:
    """Охраняемое кольцо манора: его клетки + непосредственная кромка.

    Только своя земля и соседи, без которых амбар не живёт, — не вся барония.
    Вычисляется, не хранится: книга не дублирует карту. Соседство — хелпер
    гекс-сетки (ADR 0071).
    """
    ring: set[str] = set(manor.tile_ids)
    for tile_id in list(ring):
        tile = world.tiles.get(tile_id)
        if tile is None:
            continue
        ring.update(neighbor_ids(world, tile))
    return sorted(ring)


def holding_ruined(world: World, manor: Manor) -> bool:
    """Держание развалено: тяглых дворов не осталось или амбар пуст, а держатель голоден."""
    holder = holder_household(world, manor)
    holder_id = holder.id if holder is not None else None
    tenants = [
        hid
        for hid in manor.household_ids
        if hid != holder_id
        and (hh := world.households.get(hid)) is not None
        and hh.left_at is None
    ]
    if not tenants:
        return True
    barn = manor_stock(world, manor)
    grain = float(barn.amounts.get("grain", 0.0)) if barn is not None else 0.0
    return grain <= EPSILON and thegn_hungry(world, manor)


def revoke_due(world: World, manor: Manor, bad_months: int = REVOKE_BAD_MONTHS) -> bool:
    """Пора ли отзывать: пустая служба подряд и развал держания.

    Один плохой сезон отзыв не открывает: нужно `bad_months` (по умолчанию 6,
    около двух сезонов) подряд незакрытой нормы И разваленное держание.
    Это сигнал политики, а не автособытие: исполняет отзыв действие корня.
    """
    if manor.parent_manor_id is None:
        return False
    return manor.bad_service_months >= bad_months and holding_ruined(world, manor)


def grant_thegn(
    world: World,
    person_id: str,
    tile_ids: list[str],
    household_ids: list[str],
) -> Manor | None:
    """Пожаловать тэна: ≤ лимита сценария, depth ≤ 1.

    Число пожалований за прогон ограничено `world.stats["thegn_limit_grants"]`
    (по умолчанию 1); при исчерпании лимита — отказ с записью
    `grant_thegn_rejected reason=grants_limit refused=True`. Глубина вложенности — явный гейт:
    новый манор был бы на `manor_depth(root) + 1`, глубже 1 не жалуют — отказ
    с записью `grant_thegn_rejected reason=depth refused=True` (раньше то же держалось
    неявно: жаловать можно только клетки/дворы из корневой книги).

    **Почему запись отказа помечена `refused=True` (ADR 0215 §Дыра 2).** Раньше
    отказ писался в лог обычной строкой с полем `reason` и **не** помечался:
    причина названа, но ни строка лога, ни счётчик `orders_refused`, ни будущий
    обвинитель «приказ изменил мир?» её не видели — то есть это был отказ,
    который выглядит как действие. Теперь отказ идёт через `log_refused_order`,
    тем же путём, что и отказ, пойманный исключением в `runner._log_refusal`.
    Отозванное пожалование слот не
    освобождает: `root.grant_ids` хранит историю. Двор САМОГО держателя всегда
    входит в книгу тэна (его стол — амбар `manor:<id>`, а не замок холма) и в
    лимит пожалованных дворов не считается. Жаловать можно только двор из
    КОРНЕВОЙ книги: вложенный тэн не жалует дальше. Возвращает манор тэна или
    None, если пожалование отклонено по лимиту или глубине.
    """
    person = world.persons.get(person_id)
    if person is None or person.age_class != "adult" or person.health <= 0:
        raise ValueError(f"Нет живого взрослого '{person_id}'")
    root = root_manor(world)
    if root is None:
        raise ValueError("Нет корневого манора игрока")
    if manor_depth(world, root) + 1 > 1:
        log_refused_order(
            world, "grant_thegn_rejected", "depth", person=person_id
        )
        return None
    max_grants = int(world.stats.get("thegn_limit_grants", 1))
    if len(root.grant_ids) >= max_grants:
        log_refused_order(
            world,
            "grant_thegn_rejected",
            "grants_limit",
            person=person_id,
            granted=len(root.grant_ids),
            limit=max_grants,
        )
        return None
    if not tile_ids or not household_ids:
        raise ValueError("Пожалование тэна требует хотя бы одну клетку и один двор")
    if len(set(tile_ids)) != len(tile_ids) or len(set(household_ids)) != len(household_ids):
        raise ValueError("Повтор клетки или двора в пожаловании")
    person_household = world.households.get(person.household_id)
    tenant_ids = [hid for hid in household_ids if hid != person.household_id]
    max_tiles = min(int(world.stats.get("thegn_limit_tiles", THEGN_MAX_TILES)), THEGN_MAX_TILES)
    max_households = min(
        int(world.stats.get("thegn_limit_households", THEGN_MAX_HOUSEHOLDS)),
        THEGN_MAX_HOUSEHOLDS,
    )
    if len(tile_ids) > max_tiles or len(tenant_ids) > max_households:
        raise ValueError("Пожалование тэна превышает лимит сценария")
    if person_household is not None:
        person_settlement = world.settlements.get(person_household.settlement_id or "")
        if person_settlement is not None and person_settlement.kind == "salt_village":
            raise PermissionError("Соляной держатель — равный, не тэн")
    for tile_id in tile_ids:
        if tile_id not in root.tile_ids:
            raise ValueError(f"Клетка '{tile_id}' не в корневом маноре")
        settlement = world.settlements.get(world.tiles[tile_id].settlement_id or "")
        if settlement is not None and settlement.kind == "salt_village":
            raise PermissionError("Соляная деревня — клетки того же манора, не фьеф")
    # Двор САМОГО держателя входит в манор всегда: его стол — амбар тэна,
    # а не замок холма. Лимит сценария считает только пожалованные тяглые
    # дворы, поэтому держатель в него не входит.
    book_ids = list(household_ids)
    if person_household is not None and person.household_id not in book_ids:
        book_ids.append(person.household_id)
    for household_id in book_ids:
        if household_id not in root.household_ids:
            raise ValueError(f"Двор '{household_id}' не в корневом маноре")
        settlement = world.settlements.get(
            world.households[household_id].settlement_id or ""
        )
        if settlement is not None and settlement.kind == "salt_village":
            raise PermissionError("Соляной двор — не тяглый двор тэна")
    if not any(world.tiles[tid].regime_id == "demesne" for tid in tile_ids):
        raise ValueError(
            "Пожалование без доменной клетки: тэну нечего пахать (нет regime:demesne)"
        )

    manor_id = f"manor_{person_id}"
    stock_id = _create_manor_stock(world, manor_id)
    tenants = [hid for hid in book_ids if person_household is None or hid != person.household_id]
    holder_adults = 1
    if person_household is not None:
        holder_adults = max(
            1,
            sum(
                1
                for pid in person_household.member_ids
                if (p := world.persons.get(pid)) is not None and p.age_class == "adult"
            ),
        )
    manor = Manor(
        id=manor_id,
        holder_person_id=person_id,
        stock_id=stock_id,
        parent_manor_id=root.id,
        upward_bundle=THEGN_UPWARD_BUNDLE,
        prior_preset=(
            person_household.legal_status_id if person_household is not None else ""
        ),
        # Норма службы фьефа: сам как тяжёлый + по серванту с тяглого двора;
        # явок — по числу взрослых двора держателя.
        service_kits_required=float(1 + len(tenants)),
        service_men_required=float(holder_adults),
    )
    for tile_id in tile_ids:
        root.tile_ids.remove(tile_id)
        manor.tile_ids.append(tile_id)
        regime = root.tile_regimes.pop(tile_id, world.tiles[tile_id].regime_id)
        manor.tile_regimes[tile_id] = regime
    for household_id in book_ids:
        root.household_ids.remove(household_id)
        manor.household_ids.append(household_id)
        world.households[household_id].manor_id = manor_id
    grant_id = f"grant_{len(root.grant_ids) + 1:03d}"
    root.grant_ids.append(grant_id)
    manor.grant_ids.append(grant_id)
    world.manors[manor_id] = manor
    _set_thegn_preset(world, person.household_id)
    _sweep_holder_iron_to_barn(world, manor)
    _log(
        world,
        "grant_thegn",
        person=person_id,
        manor=manor_id,
        tiles=list(tile_ids),
        households=list(book_ids),
        grant=grant_id,
    )
    return manor


def revoke_thegn(world: World, manor_id: str) -> bool:
    """Отозвать вложенный манор: земля, дворы и АМБАР возвращаются в корень.

    Зерно не двоится и не сгорает: `manor:<id>` сливается в амбар родителя
    через `Ledger.transfer`, затем мёртвый сток снимается. Войны нет.
    Бывший держатель не удаляется: он остаётся в книге корня как свободный
    без земли (`free_landless`) — рот, нанимающийся за еду, а не стол лорда.

    **Отказ бросается, а не возвращается `False` (ADR 0215 §Дыра 2).** Раньше
    оба отказа — «такого фьефа нет» и «это не фьеф, а корень» — были тихими:
    приказ возвращал `False`, никто его не спрашивал, и `runner` дописывал
    синтетическую запись без полей и без `refused`. Это ровно тот же брак, что у
    `call_boon`, и он найден обходом всех приказов, а не догадкой: тихих нулей
    у приказов ровно три, и все три переведены на путь отказа ADR 0202.
    """
    manor = world.manors.get(manor_id)
    if manor is None:
        raise ValueError(
            f"Отзыв вложенного манора: '{manor_id}' нет в книге. Живы: "
            f"{sorted(world.manors)}"
        )
    if manor.parent_manor_id is None:
        raise PermissionError(
            f"Отозвать '{manor_id}' нельзя: это корень, а не фьеф. Корень не отзывается"
        )
    parent = world.manors.get(manor.parent_manor_id)
    if parent is None:
        raise ValueError(
            f"У вложенного манора '{manor_id}' нет родителя в книге "
            f"(parent_manor_id='{manor.parent_manor_id}')"
        )

    source = manor_stock(world, manor)
    target = manor_stock(world, parent)
    if source is not None and target is not None and source.id != target.id:
        for good in sorted(source.amounts):
            amount = source.amounts.get(good, 0.0)
            if amount > EPSILON:
                world.ledger.transfer(
                    source, target, good, amount, "revoke_thegn", world.clock.date
                )
    if manor.stock_id and manor.stock_id in world.stocks:
        del world.stocks[manor.stock_id]

    for tile_id in list(manor.tile_ids):
        if tile_id not in parent.tile_ids:
            parent.tile_ids.append(tile_id)
        parent.tile_regimes[tile_id] = manor.tile_regimes.get(
            tile_id, world.tiles[tile_id].regime_id
        )
    for household_id in list(manor.household_ids):
        if household_id not in parent.household_ids:
            parent.household_ids.append(household_id)
        world.households[household_id].manor_id = parent.id
    person = world.persons.get(manor.holder_person_id)
    if person is not None:
        household = world.households.get(person.household_id)
        preset = world.catalogs.legal_statuses.get("free_landless")
        if household is not None and preset is not None:
            household.legal_status_id = "free_landless"
            household.personal_status = preset.personal_status
            household.land_relation = preset.land_relation
            household.obligation_bundle = preset.obligation_bundle
    del world.manors[manor_id]
    _log(world, "revoke_thegn", manor=manor_id, tiles=list(manor.tile_ids))
    return True


def thegn_hungry(world: World, manor: Manor) -> bool:
    """Голод тэна — по СВОЕМУ двору/стоку держателя, не по замку холма.

    Ушедший двор (`left_at`) своего стола у книги уже не имеет: держатель не
    кормится и не может быть mustered — это честный False, а не замороженный
    голод, который маскировали бы замком.
    """
    person = world.persons.get(manor.holder_person_id)
    household = world.households.get(person.household_id) if person else None
    if household is None:
        return True
    if household.left_at is not None:
        return True
    return household.hunger_days > 0


def update_musters(world: World) -> dict[str, bool]:
    """Флаг `mustered` у вложенных маноров и состояние их службы.

    `mustered` — не голый «жив и сыт»: нужен ещё минимальный комплект на
    держателе (`war_kit` в стоке его двора). Сытый без комплекта — плохой
    тэн: `mustered` ложен. Заодно книга обновляет `service_met`/`service_gap`
    (норма комплектов + сытость за месяц) и счётчик `bad_service_months`;
    явка `men_held` (живые взрослые двора держателя) сверяется с нормой
    `service_men_required`: недобор гасит `service_met` наравне с нехваткой
    комплектов. Всё видно в `manor_log` рядом с сезоном.
    """
    result: dict[str, bool] = {}
    service: dict[str, dict] = {}
    season = ""
    month = world.calendar.get(world.clock.month)
    if month is not None:
        season = month.season
    for manor in nested_manors(world):
        person = world.persons.get(manor.holder_person_id)
        alive = person is not None and person.health > 0
        fed = not thegn_hungry(world, manor)
        kits = fief_kits(world, manor)
        men = men_held(world, manor)
        required = float(manor.service_kits_required)
        men_required = float(manor.service_men_required)
        met = bool(
            alive
            and fed
            and (required <= EPSILON or kits + EPSILON >= required)
            and (men_required <= EPSILON or men + EPSILON >= men_required)
        )
        manor.service_kits_held = kits
        manor.service_men_held = men
        manor.service_met = met
        manor.service_gap = max(0.0, required - kits)
        manor.bad_service_months = 0 if met else manor.bad_service_months + 1
        if manor.bad_service_months == REVOKE_BAD_MONTHS and holding_ruined(world, manor):
            # Сигнал, а не молчаливая ловушка: барон узнаёт гонцом с задержкой,
            # что служба пуста N месяцев, а держание развалено. Исполняет отзыв
            # он сам действием корня.
            channel = CHANNELS[MESSENGER]
            make_report(
                world,
                MESSENGER,
                "manor",
                manor.id,
                f"Служба {manor.id} пуста {REVOKE_BAD_MONTHS} месяцев, "
                f"держание развалено.",
                {"bad_service_months": manor.bad_service_months},
                world.clock.date,
                channel.delay_months,
                channel.confidence,
                distorted=False,
                noise=channel.noise,
            )
        manor.mustered = bool(alive and fed and holder_kits(world, manor) + EPSILON >= KIT_MIN_HOLDER)
        result[manor.id] = manor.mustered
        service[manor.id] = {
            "service_met": met,
            "service_gap": round(manor.service_gap, 3),
            "kits_held": round(kits, 3),
            "kits_required": required,
            "men_held": men,
            "men_required": men_required,
            "season": season,
        }
    if result:
        world.manor_log.append(
            {"date": str(world.clock.date), "season": season, "muster": result, "service": service}
        )
    return result
