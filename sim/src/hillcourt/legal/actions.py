"""Действия игрока, меняющие право/повинность/посылку (И-2).

Прямого приказа `Person` здесь нет: `send_party` создаёт `Pack`, а кто именно
пойдёт — решает двор. Право и повинность оформляются через `Right`/`Obligation`.
"""

from __future__ import annotations

from ..info.sources import CHANNELS, EYE_FROM_HILL, MESSENGER
from ..news.propagation import make_report
from ..ontology import Household, Obligation, Pack, Right, SimDate, Stock
from ..world import World
from .regimes import can_be_sent, holding_settlement_id


def _log(world: World, action: str, **fields) -> dict:
    """Записать действие игрока в лог (`World.player_actions`)."""
    record = {"date": str(world.clock.date), "month": world.clock.month, "action": action}
    record.update(fields)
    world.player_actions.append(record)
    return record


def _report(
    world: World,
    source: str,
    subject_kind: str,
    subject_id: str,
    content: str,
    facts: dict,
) -> None:
    """Породить Report по каналу источника: задержка и шум — из `info.sources`."""
    channel = CHANNELS[source]
    make_report(
        world,
        source=source,
        subject_kind=subject_kind,
        subject_id=subject_id,
        content=content,
        facts=facts,
        event_date=world.clock.date,
        delay_months=channel.delay_months,
        confidence=channel.confidence,
        noise=channel.noise,
    )


def _tile_id(x: int, y: int) -> str:
    return f"t_{x:02d}_{y:02d}"


def _adjacent(origin: str, destination: str) -> bool:
    ox, oy = int(origin.split("_")[1]), int(origin.split("_")[2])
    dx, dy = int(destination.split("_")[1]), int(destination.split("_")[2])
    return abs(ox - dx) + abs(oy - dy) == 1


def send_party(
    world: World,
    household_id: str,
    member_ids: list[str],
    destination_tile_id: str,
    kind: str = "party",
    actor: str = "player",
) -> Pack:
    """Послать людей двором на соседнюю клетку; без pathfinding (только шаг).

    Послать можно только двор со статусом `can_be_sent`. Уходят взрослые члены
    этого двора; путь — один шаг до соседней клетки. Разбор похода — в
    `hillcourt.hazards.travel.resolve_packs`. Весть — гонцом с задержкой
    в обоих случаях; но в лог приказов игрока (`player_actions`) пишется только
    его собственный приказ: вылазка тэна (`actor="thegn"`) — не приказ барона.
    """
    household = world.households[household_id]
    if not can_be_sent(world, household):
        raise PermissionError(
            f"Двор '{household_id}' ({household.legal_status_id}) нельзя послать"
        )
    origin = household.current_tile_id
    if not _adjacent(origin, destination_tile_id):
        raise ValueError(
            f"Клетка '{destination_tile_id}' не соседняя с '{origin}' "
            f"(pathfinding в v0 запрещён)"
        )

    adults = {
        pid
        for pid in household.member_ids
        if world.persons.get(pid) is not None and world.persons[pid].age_class == "adult"
    }
    going = [pid for pid in member_ids if pid in adults]
    if not going:
        raise ValueError(f"У двора '{household_id}' не выбрано ни одного взрослого")

    date = world.clock.date
    number = world_pack_count(world) + 1
    cargo = Stock(
        id=f"pack:{number:04d}",
        owner_kind="pack",
        owner_id=f"pack_{number:04d}",
        amounts={},
    )
    world.add_stock(cargo)
    pack = Pack(
        id=f"pack_{number:04d}",
        kind=kind,
        origin_tile_id=origin,
        destination_tile_id=destination_tile_id,
        route=[origin, destination_tile_id],
        member_ids=going,
        cargo=cargo,
        departed_date=date,
        eta_date=date.advance(world.clock.months_per_year),
        status="in_transit",
        owner_household_id=household_id,
    )
    world.packs[pack.id] = pack

    for pid in going:
        household.member_ids.remove(pid)
        world.persons[pid].location_tile_id = destination_tile_id
    household.labor_days = max(0.0, household.labor_days - 20.0 * len(going))
    if actor == "player":
        _log(
            world,
            "send_party",
            household=household_id,
            destination=destination_tile_id,
            members=list(going),
            pack=pack.id,
        )
    _report(
        world,
        MESSENGER,
        "pack",
        pack.id,
        f"Отряд послан из '{origin}' на клетку '{destination_tile_id}'",
        {"destination": destination_tile_id, "members": list(going)},
    )
    return pack


def world_pack_count(world: World) -> int:
    """Число созданных посылок; служит детерминированным счётчиком id."""
    return len(world.packs)


def grant_tenure(
    world: World,
    household_id: str,
    tile_id: str,
    kind: str = "tenure",
    rent_share: float = 0.1,
) -> Right:
    """Выдать двору держание на клетку и (если так надо) повинность ренты."""
    household = world.households[household_id]
    tile = world.tiles[tile_id]
    right = Right(
        id=f"right_{household_id}_{tile_id}",
        holder_household_id=household_id,
        tile_id=tile_id,
        kind=kind,
        granted_date=world.clock.date,
        rent_share=rent_share,
    )
    world.rights[right.id] = right
    holder = holding_settlement_id(world, tile)
    if holder is None:
        tile.regime_id = "tenement"
    _log(
        world,
        "grant_tenure",
        household=household_id,
        tile=tile_id,
        kind=kind,
        rent_share=rent_share,
        right=right.id,
    )
    _report(
        world,
        EYE_FROM_HILL,
        "right",
        right.id,
        f"Двору '{household_id}' выдано держание на клетку '{tile_id}'",
        {
            "household": household_id,
            "tile": tile_id,
            "kind": kind,
            "rent_share": rent_share,
        },
    )
    return right


def add_obligation(
    world: World,
    household_id: str,
    kind: str,
    due_good: str | None,
    due_amount: float,
    period_months: int = 1,
    basis: str = "share",
    corvee_days: float = 0.0,
    right_id: str | None = None,
) -> Obligation:
    """Оформить повинность двора (рента-доля, фиксированный мешок, повинность)."""
    obligation = Obligation(
        id=f"obl_{household_id}_{kind}",
        household_id=household_id,
        kind=kind,
        due_good=due_good,
        due_amount=due_amount,
        period_months=period_months,
        paid_total=0.0,
        arrears=0.0,
        right_id=right_id,
        basis=basis,
        corvee_days=corvee_days,
    )
    world.obligations[obligation.id] = obligation
    world.households[household_id].obligation_ids.append(obligation.id)
    _log(
        world,
        "add_obligation",
        household=household_id,
        kind=kind,
        due_good=due_good,
        due_amount=due_amount,
        obligation=obligation.id,
    )
    _report(
        world,
        EYE_FROM_HILL,
        "obligation",
        obligation.id,
        f"Двору '{household_id}' оформлена повинность '{kind}'",
        {
            "household": household_id,
            "kind": kind,
            "due_good": due_good,
            "due_amount": due_amount,
            "obligation": obligation.id,
        },
    )
    return obligation
