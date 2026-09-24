"""Приказ дальнего хода: цель существующему отряду, маршрут — по земле.

Игрок (или держатель) задаёт цель уже существующему двору: `send_march`
строит маршрут `engine/path.py` по профилю, сажает людей в `Pack kind="party"`
(тот же разбор похода, что у `send_party`, — `hazards/travel.resolve_packs`:
риск клетки тем `Hazard`, что уже есть) и пишет приказ в лог. Боя нет:
дошёл / не дошёл / дольше.

Прямого приказа `Person` нет (И-2): приказ создаёт `Pack`, а идут взрослые
двора. Материя не движется (люди без груза); труд двора снимается, как у
`send_party`. Путь игрока строится по его известиям (`known_tiles` +
запомненные дороги), а не по истине мира (И-3).
"""

from __future__ import annotations

from ..info.sources import CHANNELS, MESSENGER
from ..legal.actions import world_pack_count
from ..legal.regimes import can_be_sent
from ..news.propagation import make_report
from ..ontology import Pack, Stock
from ..world import World
from .path import (
    find_path,
    known_tiles,
    remembered_road_tiles,
    travel_days,
    travel_months,
)
from .terrain import DAYS_PER_MONTH, get_profile


def _log(world: World, action: str, **fields) -> dict:
    """Записать приказ игрока в лог (`World.player_actions`)."""
    record = {"date": str(world.clock.date), "month": world.clock.month, "action": action}
    record.update(fields)
    world.player_actions.append(record)
    return record


def _add_months(date, months: int, months_per_year: int):
    """Сдвинуть дату на N месяцев вперёд (срок похода)."""
    result = date
    for _ in range(max(0, months)):
        result = result.advance(months_per_year)
    return result


def send_march(
    world: World,
    household_id: str,
    member_ids: list[str],
    destination_tile_id: str,
    profile_id: str = "foot",
    kind: str = "party",
) -> Pack:
    """Послать двор дальним маршрутом: цель — любая клетка с путём по земле.

    Маршрут — `find_path` по профилю (`caravan`/`foot`/`hunters`/`mounted`/
    `arms`/`rider`), время — в часах (`travel_hours`, ADR 0071), срок — сутки
    (`travel_days`, `eta_date` с точностью до дня) и производные месяцы. Нет
    пути (река без брода, стена тумана) — `ValueError`, а не телепорт. В логе
    приказа — маршрут, `travel_hours`, сутки, месяцы и профиль; разбор похода —
    существующий `resolve_packs`.
    """
    get_profile(profile_id)
    household = world.households.get(household_id)
    if household is None:
        raise ValueError(f"Нет двора '{household_id}'")
    if not can_be_sent(world, household):
        raise PermissionError(
            f"Двор '{household_id}' ({household.legal_status_id}) нельзя послать"
        )
    origin = household.current_tile_id
    if destination_tile_id not in world.tiles:
        raise ValueError(f"Нет клетки '{destination_tile_id}'")
    if destination_tile_id == origin:
        raise ValueError("Цель похода — своя же клетка: посылать некого")

    known = known_tiles(world)
    known.add(origin)
    found = find_path(
        world,
        origin,
        destination_tile_id,
        profile_id,
        known=known,
        memory_roads=remembered_road_tiles(world),
    )
    if found is None:
        raise ValueError(
            f"От '{origin}' до '{destination_tile_id}' профилем '{profile_id}' "
            f"пути нет (река без брода или неизвестная земля)"
        )
    route, hours = found
    days = travel_days(hours)
    months = travel_months(days)

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
        route=list(route),
        member_ids=going,
        cargo=cargo,
        departed_date=date,
        eta_date=date.advance_days(days, DAYS_PER_MONTH, world.clock.months_per_year),
        status="in_transit",
        owner_household_id=household_id,
    )
    world.packs[pack.id] = pack

    for pid in going:
        household.member_ids.remove(pid)
        world.persons[pid].location_tile_id = destination_tile_id
    household.labor_days = max(0.0, household.labor_days - 20.0 * len(going))
    _log(
        world,
        "send_march",
        household=household_id,
        destination=destination_tile_id,
        members=list(going),
        pack=pack.id,
        profile=profile_id,
        route=list(route),
        travel_hours=round(hours, 3),
        days=days,
        months=months,
    )
    channel = CHANNELS[MESSENGER]
    make_report(
        world,
        source=MESSENGER,
        subject_kind="pack",
        subject_id=pack.id,
        content=(
            f"Отряд послан дальним ходом из '{origin}' на клетку "
            f"'{destination_tile_id}' ({profile_id}, {months} мес)"
        ),
        facts={"destination": destination_tile_id, "members": list(going)},
        event_date=world.clock.date,
        delay_months=channel.delay_months,
        confidence=channel.confidence,
        noise=channel.noise,
    )
    return pack
