"""События месяца для отчётов: что ПРОИЗОШЛО, а не снимок склада (ADR 0079).

Правило владельца: «отчёт — события месяца, не снимок склада». Обоз с коротким
путём приходит в том же месяце и увозит соль из деревни ДО `phase_inform` —
снимок склада честно пуст, и отчёт молчит об оттоке. Источник истины для
отчёта — список событий месяца.

События не новая мировая сущность: плоский список словарей в
`World.month_events`, собирается из УЖЕ посчитанного `Ledger` месяца
(проводки с `reason` и id стоков). Ни одна формула экономики не меняется и ни
одна чужая фаза не правится: `engine/events.py` только читает журнал материи.

Виды событий (плоские записи, `kind`):

| kind | когда | смысл |
|---|---|---|
| `caravan_departure` | `caravan_load` из стока поселения | груз ушёл из поселения |
| `caravan_arrival` | `caravan_unload` в сток поселения | груз пришёл в поселение |
| `pack_departure` | `caravan_load`/`departure_cargo` в сток воза | посылка вышла в путь |
| `pack_arrival` | `caravan_unload`/`arrival` из стока воза | посылка дошла и разгрузилась |
| `harvest` | `harvest_grain` | урожай зерна существующего тика |
| `hay_mowed` | `gather_hay` | сено скошено |
| `graze` | `graze` | скот поел сена на выпасе |

Запись: `{kind, good, amount, date, month, reason, src_id, dst_id}` плюс
`settlement_id`/`tile_id`, если из проводки выводятся. Порядок детерминирован:
по дате, затем `kind`, затем id — один seed даёт один порядок (И-6).
"""

from __future__ import annotations

from ..world import World

CARAVAN_LOAD = "caravan_load"
CARAVAN_UNLOAD = "caravan_unload"
DEPARTURE_CARGO = "departure_cargo"
ARRIVAL = "arrival"
HARVEST_GRAIN = "harvest_grain"
GATHER_HAY = "gather_hay"
GRAZE = "graze"

SETTLEMENT_PREFIX = "settlement:"
PACK_PREFIX = "pack:"
TILE_PREFIX = "tile:"

_EVENT_BY_REASON: dict[str, str] = {
    HARVEST_GRAIN: "harvest",
    GATHER_HAY: "hay_mowed",
    GRAZE: "graze",
}


def _owner_id(stock_id: str | None) -> str:
    """Id владельца стока из `owner_kind:owner_id` ('' — не наш контейнер)."""
    if not stock_id:
        return ""
    _, _, owner = stock_id.partition(":")
    return owner


def _settlement_of(stock_id: str | None) -> str | None:
    """Поселение-владелец стока или None (`settlement:*`)."""
    if not stock_id or not stock_id.startswith(SETTLEMENT_PREFIX):
        return None
    return stock_id[len(SETTLEMENT_PREFIX) :]


def _tile_of(stock_id: str | None) -> str | None:
    """Клетка-владелец стока или None (`tile:*`)."""
    if not stock_id or not stock_id.startswith(TILE_PREFIX):
        return None
    return stock_id[len(TILE_PREFIX) :]


HOUSEHOLD_PREFIX = "household:"


def _household_of(stock_id: str | None) -> str | None:
    """Двор-владелец стока или None (`household:*`)."""
    if not stock_id or not stock_id.startswith(HOUSEHOLD_PREFIX):
        return None
    return stock_id[len(HOUSEHOLD_PREFIX) :]


def _settlement_for(world: World, stock_id: str | None) -> str | None:
    """Поселение, которому принадлежит сток: `settlement:*` напрямую.

    `household:*` — по поселению двора: соль уходит со стоков ДВОРОВ деревни,
    поэтому событие ухода/прихода несёт поселение вместе с двором. Иначе отчёт
    приписал бы деревне груз, который у её стоков не стоял.
    """
    direct = _settlement_of(stock_id)
    if direct is not None:
        return direct
    hid = _household_of(stock_id)
    if hid is None:
        return None
    household = world.households.get(hid)
    return household.settlement_id if household is not None else None


def _entry_event(world: World, entry) -> dict | None:
    """Событие из проводки материи или None (не событие месяца)."""
    kind = _EVENT_BY_REASON.get(entry.reason)
    if kind is not None:
        return {
            "kind": kind,
            "good": entry.good,
            "amount": float(entry.amount),
            "date": entry.date,
            "month": (entry.date.year, entry.date.month),
            "reason": entry.reason,
            "src_id": entry.src_id,
            "dst_id": entry.dst_id,
        }

    if entry.reason not in (CARAVAN_LOAD, CARAVAN_UNLOAD, DEPARTURE_CARGO, ARRIVAL):
        return None

    src, dst = entry.src_id or "", entry.dst_id or ""
    src_settlement = _settlement_for(world, src)
    dst_settlement = _settlement_for(world, dst)
    src_pack = src.startswith(PACK_PREFIX)
    dst_pack = dst.startswith(PACK_PREFIX)

    if entry.reason == CARAVAN_UNLOAD and dst_settlement is not None:
        kind = "caravan_arrival"
    elif entry.reason == CARAVAN_LOAD and src_settlement is not None:
        kind = "caravan_departure"
    elif (entry.reason in (CARAVAN_LOAD, DEPARTURE_CARGO)) and dst_pack:
        kind = "pack_departure"
    elif entry.reason in (CARAVAN_UNLOAD, ARRIVAL) and src_pack:
        kind = "pack_arrival"
    else:
        return None

    return {
        "kind": kind,
        "good": entry.good,
        "amount": float(entry.amount),
        "date": entry.date,
        "month": (entry.date.year, entry.date.month),
        "reason": entry.reason,
        "src_id": src,
        "dst_id": dst,
        "settlement_id": src_settlement or dst_settlement,
        "household_id": _household_of(src) or _household_of(dst),
        "tile_id": _tile_of(src) or _tile_of(dst),
    }


def month_events(world: World, as_of=None) -> list[dict]:
    """События месяца по дате `as_of` (по умолчанию текущая), детерминированно.

    Читает только `world.ledger.entries` — то, что тик УЖЕ посчитал. Ничего не
    двигает, не создаёт и не меняет числа.
    """
    date = as_of or world.clock.date
    events: list[dict] = []
    for entry in world.ledger.entries:
        if entry.date.year != date.year or entry.date.month != date.month:
            continue
        event = _entry_event(world, entry)
        if event is not None:
            events.append(event)
    events.sort(
        key=lambda e: (
            e["date"].to_day_index(),
            e["kind"],
            e["good"],
            e["src_id"] or "",
            e["dst_id"] or "",
        )
    )
    return events


def event_total(
    events: list[dict], kind: str, good: str, settlement_id: str | None = None
) -> float:
    """Сумма событий вида `kind` по товару `good` (для поселения — если задан)."""
    total = 0.0
    for event in events:
        if event.get("kind") != kind or event.get("good") != good:
            continue
        if settlement_id is not None and event.get("settlement_id") != settlement_id:
            continue
        total += float(event.get("amount", 0.0))
    return total
