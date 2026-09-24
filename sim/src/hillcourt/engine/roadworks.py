"""Стройка пути: дорога, брод и мост как проект барщины, а не рисунок карты.

Дорога не рисуется в редакторе. Игрок (корень) ставит работу на клетке своей
книги: эта клетка станет грунтовой дорогой, брод укрепят, через реку наведут
мост. Дни идут из уже существующего пула труда (остаток `Household.labor_days`
после барщины манора, до работы двора на своём наделе), материал — переводом
из амбара манора, а не из воздуха. Пока проект не завершён, клетка старая:
`Tile.road`/`ford`/`bridge` не меняются, цена хода та же. Цены клетки — допуск
ADR 0039 (дорога 20, брод 14, мост 28 дней; log 3/2/4); кап стройки — 40 дн/мес
(допуск рук №1, ADR 0067: при достаточных руках клетка встаёт за ≤1 месяц).

Готовая дорога меняет стоимость хода тем же полем, что читает
`engine/terrain.py::entry_cost` и `engine/path.py::find_path`: отдельный A*
здесь не копируется, водный контур (`engine/river.py`, профили `water_*`)
не переписывается — клетка просто поднимает tag `road`/`ford`/`bridge`.

Право: корень начинает работу только на клетке своей книги; клетка в книге
вложенного тэна требует сначала отзыва пожалования (как `set_tile_regime`).
Служба тэна, скот, вид тайла (кап пяти дворов) и yield не тронуты: вид только
читается, урожайность не поднимается.
"""

from __future__ import annotations

from ..world import World

WORK_KINDS: tuple[str, ...] = ("road", "ford", "bridge")

REQUIRED_LABOR_DAYS: dict[str, float] = {
    "road": 20.0,
    "ford": 14.0,
    "bridge": 28.0,
}

MATERIAL_GOOD: str = "log"
MATERIAL_AMOUNT: dict[str, float] = {
    "road": 3.0,
    "ford": 2.0,
    "bridge": 4.0,
}

# Допуск рук №1 (ADR 0067): кап стройки 20 → 40 трудодней/мес на проект.
# Врали не руки (в книге их сотни), а кап: тракт строился «поколением на месяц».
# Дни и материалы клетки (ADR 0039: 20/14/28 и log 3/2/4) не тронуты.
MONTHLY_LABOR_CAP: float = 40.0

WASTE_STOCK_ID = "sink:waste"
EPSILON = 1e-9

ACTION_BY_KIND: dict[str, str] = {
    "road": "work_road",
    "ford": "work_ford",
    "bridge": "work_bridge",
}


def works(world: World) -> dict:
    """Реестр строек пути; лениво заводится, чтобы не ломать старые миры."""
    store = getattr(world, "roadworks", None)
    if store is None:
        store = {}
        world.roadworks = store
    return store


def active_works(world: World) -> list[dict]:
    """Активные (незавершённые) работы по id клетки."""
    return sorted(
        (w for w in works(world).values() if w.get("status") == "active"),
        key=lambda w: w.get("tile_id", ""),
    )


def _log(world: World, action: str, **fields) -> dict:
    """Записать действие игрока в лог (`World.player_actions`)."""
    record = {"date": str(world.clock.date), "month": world.clock.month, "action": action}
    record.update(fields)
    world.player_actions.append(record)
    return record


def _root_manor(world: World):
    """Корневой манор игрока или None."""
    if world.player_manor_id is None:
        return None
    return world.manors.get(world.player_manor_id)


def _manor_of_tile(world: World, tile_id: str):
    """Манор, в книге которого числится клетка (как `engine/manor.py`)."""
    for manor_id in sorted(world.manors):
        manor = world.manors[manor_id]
        if tile_id in manor.tile_ids:
            return manor
    return None


def _manor_of_household(world: World, household_id: str):
    """Манор, в книге которого числится двор."""
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


def start_work(world: World, tile_id: str, kind: str) -> dict:
    """Начать стройку пути на клетке книги корня.

    Проверки (порядок — от дешёвых к дорогим):
    вид работы, клетка, террейн (дорога — не на воду; брод/мост — только
    на воду), отсутствие готового tag и активной стройки, книга корня
    (клетка тэна — только через отзыв), материал в амбаре корня.
    Материал уходит переводом в `sink:waste` (гать/мост ложится в землю,
    материя сходится переводом, а не созданием). Труд в долг не берётся:
    дни доберёт месячная фаза из остатка рук.
    """
    if kind not in WORK_KINDS:
        raise ValueError(f"Вид стройки '{kind}' вне {list(WORK_KINDS)}")
    tile = world.tiles.get(tile_id)
    if tile is None:
        raise ValueError(f"Нет клетки '{tile_id}'")
    if tile.terrain == "water" and kind == "road":
        raise ValueError(f"Дорога на воду '{tile_id}': нужен брод или мост")
    if tile.terrain != "water" and kind in ("ford", "bridge"):
        raise ValueError(f"Брод/мост не на воду '{tile_id}': террейн {tile.terrain}")
    if kind == "road" and tile.road:
        raise ValueError(f"На клетке '{tile_id}' дорога уже есть")
    if kind == "ford" and tile.ford:
        raise ValueError(f"На клетке '{tile_id}' брод уже есть")
    if kind == "bridge" and tile.bridge:
        raise ValueError(f"На клетке '{tile_id}' мост уже есть")
    store = works(world)
    existing = store.get(tile_id)
    if existing is not None and existing.get("status") == "active":
        raise ValueError(f"На клетке '{tile_id}' стройка уже идёт")
    root = _root_manor(world)
    if root is None:
        raise ValueError("Нет корневого манора игрока")
    owner = _manor_of_tile(world, tile_id)
    if owner is None or owner.id != root.id:
        holder = owner.id if owner is not None else "ничья"
        raise PermissionError(
            f"Клетка '{tile_id}' не в книге корня ('{holder}'): "
            f"тэн — только на своей, корень — только на своей"
        )
    required = float(REQUIRED_LABOR_DAYS[kind])
    material = float(MATERIAL_AMOUNT[kind])
    source = world.stocks.get(root.stock_id)
    if source is None:
        raise ValueError("У корневого манора нет амбара")
    available = source.amounts.get(MATERIAL_GOOD, 0.0)
    if available + EPSILON < material:
        raise ValueError(
            f"В амбаре корня нет '{MATERIAL_GOOD}': есть {available}, "
            f"надо {material} для '{kind}' на '{tile_id}'"
        )
    waste = world.get_stock(WASTE_STOCK_ID)
    world.ledger.transfer(
        source, waste, MATERIAL_GOOD, material, "roadwork_material", world.clock.date
    )
    work = {
        "tile_id": tile_id,
        "kind": kind,
        "required_days": required,
        "done_days": 0.0,
        "material_good": MATERIAL_GOOD,
        "material_amount": material,
        "status": "active",
        "started": str(world.clock.date),
        "manor_id": root.id,
    }
    store[tile_id] = work
    _log(
        world,
        ACTION_BY_KIND[kind],
        tile=tile_id,
        kind=kind,
        required_days=required,
        material={MATERIAL_GOOD: material},
    )
    return work


def _root_labor_households(world: World, manor_id: str) -> list:
    """Живые дворы книги корня по id (руки стройки — только свои)."""
    out = []
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None:
            continue
        book = _manor_of_household(world, hid)
        if book is None or book.id != manor_id:
            continue
        out.append(household)
    return out


def advance_roadworks(world: World, date=None) -> dict[str, float]:
    """Добрать труд в активные стройки из остатка рук после барщины.

    Вызывается месячной фазой после `phase_manor`, до `phase_labor`: дни,
    ушедшие сюда, двор уже не отработает на своём наделе (поле худеет
    честно). На одну стройку — не больше `MONTHLY_LABOR_CAP` (40 дн/мес,
    допуск ADR 0067) в месяц: при достаточных руках дорога/брод/мост (20/14/28
    дн, ADR 0039) встают за ≤1 месяц. Без рук (`labor_days` нули)
    прогресса нет и завершения нет. Завершённая стройка поднимает tag
    клетки (`road`/`ford`/`bridge`) один раз и пишет `work_done` в лог.
    Возврат — {tile_id: добрано_дней} за этот вызов.
    """
    when = date if date is not None else world.clock.date
    progressed: dict[str, float] = {}
    for work in active_works(world):
        tile_id = work["tile_id"]
        need = float(work["required_days"]) - float(work.get("done_days", 0.0))
        if need <= EPSILON:
            continue
        budget = min(float(MONTHLY_LABOR_CAP), need)
        taken = 0.0
        for household in _root_labor_households(world, work.get("manor_id", "")):
            if budget <= EPSILON:
                break
            give = min(float(household.labor_days), budget)
            if give <= EPSILON:
                continue
            household.labor_days = max(0.0, float(household.labor_days) - give)
            budget -= give
            taken += give
            world.bump("roadwork_days", give)
        work["done_days"] = float(work.get("done_days", 0.0)) + taken
        progressed[tile_id] = taken
        world.stats[f"roadwork_done_{tile_id}"] = float(work["done_days"])
        world.stats[f"roadwork_need_{tile_id}"] = float(work["required_days"])
        if float(work["done_days"]) + EPSILON >= float(work["required_days"]):
            tile = world.tiles.get(tile_id)
            if tile is not None:
                if work["kind"] == "road":
                    tile.road = True
                elif work["kind"] == "ford":
                    tile.ford = True
                elif work["kind"] == "bridge":
                    tile.bridge = True
            work["status"] = "done"
            work["done"] = str(when)
            world.bump("roadwork_completed")
            _log(
                world,
                "work_done",
                tile=tile_id,
                kind=work["kind"],
                done_days=round(float(work["done_days"]), 3),
            )
    return progressed


def phase_roadworks(world: World) -> None:
    """Месячная фаза стройки пути: добрать труд после барщины, до поля."""
    advance_roadworks(world, world.clock.date)
