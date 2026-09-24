"""Манор как книга земли: корневой манор игрока и вложенный манор тэна.

Тэн — не класс юнита, а вложенный манор с другим долгом вверх
(`upward_bundle: thegn_service`). Игрок в v0 владеет одним корневым манором;
вложенность depth ≤ 1, дальше не жалуют. Действия игрока месячные и пишутся
в `world.player_actions`; земля и дворы переходят между манорами, стоки не
дублируются (материю никто не двигает — двигается только право).
"""

from __future__ import annotations

from ..legal.calendar import monthly_labor_days
from ..info.sources import CHANNELS, MESSENGER
from ..news.propagation import make_report
from ..ontology import Manor, Right, Stock
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


def set_tile_regime(world: World, tile_id: str, regime_id: str) -> bool:
    """Сменить режим/надел клетки (действие игрока)."""
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
    _log(world, "set_tile_regime", tile=tile_id, regime=regime_id)
    return True


def grant_tenement(world: World, household_id: str, tile_ids: list[str]) -> list[Right]:
    """Дать двору держание на клетки: режим надела, право, запись в книгу."""
    household = world.households.get(household_id)
    if household is None:
        raise ValueError(f"Нет двора '{household_id}'")
    if household.land_relation == "landless":
        raise PermissionError(f"Двор '{household_id}' не держит землю")
    manor = manor_of_household(world, household_id) or root_manor(world)
    if manor is None:
        raise ValueError("Нет манора для держания")
    rights: list[Right] = []
    preset = world.catalogs.legal_statuses.get(household.legal_status_id)
    land_kind = preset.land_kind if preset is not None else "tenement"
    if land_kind not in world.catalogs.land_regimes:
        land_kind = "tenement"
    seen: set[str] = set()
    for tile_id in tile_ids:
        if tile_id in seen:
            raise ValueError(f"Клетка '{tile_id}' повторяется в держании")
        seen.add(tile_id)
        tile = world.tiles.get(tile_id)
        if tile is None:
            raise ValueError(f"Нет клетки '{tile_id}'")
        owner = manor_of_tile(world, tile_id)
        if owner is not None and owner.id != manor.id:
            raise PermissionError(
                f"Клетка '{tile_id}' в книге '{owner.id}': нельзя отдать дважды"
            )
        if tile_id not in manor.tile_ids:
            manor.tile_ids.append(tile_id)
        tile.regime_id = land_kind
        manor.tile_regimes[tile_id] = land_kind
        right = Right(
            id=f"right_tenement_{household_id}_{tile_id}",
            holder_household_id=household_id,
            tile_id=tile_id,
            kind="tenure",
            granted_date=world.clock.date,
            rent_share=0.0,
        )
        world.rights[right.id] = right
        rights.append(right)
    _log(
        world,
        "grant_tenement",
        household=household_id,
        tiles=list(tile_ids),
        manor=manor.id,
    )
    return rights


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


def call_boon(world: World, month: int | None = None) -> bool:
    """Крикнуть помочь (bene) в месяц с `boon_allowed`; иначе отказ."""
    number = month if month is not None else world.clock.month
    calendar_month = world.calendar.get(number)
    if calendar_month is None or not calendar_month.boon_allowed:
        return False
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
    `grant_thegn_rejected reason=grants_limit`. Глубина вложенности — явный гейт:
    новый манор был бы на `manor_depth(root) + 1`, глубже 1 не жалуют — отказ
    с записью `grant_thegn_rejected reason=depth` (раньше то же держалось
    неявно: жаловать можно только клетки/дворы из корневой книги).
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
        _log(world, "grant_thegn_rejected", person=person_id, reason="depth")
        return None
    max_grants = int(world.stats.get("thegn_limit_grants", 1))
    if len(root.grant_ids) >= max_grants:
        _log(world, "grant_thegn_rejected", person=person_id, reason="grants_limit")
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
    """
    manor = world.manors.get(manor_id)
    if manor is None or manor.parent_manor_id is None:
        return False
    parent = world.manors.get(manor.parent_manor_id)
    if parent is None:
        return False

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
