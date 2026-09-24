"""Разбор походов: посылка дошла, погибла или не вернулась.

Поход разрешается, когда наступает `eta_date`. Если на клетке есть опасность,
каждый человек рискует по отдельности (см. `encounter`). Погиб весь отряд —
игрок получает `silence`, а не пересказ волка: известия не приходят из пустоты.
"""

from __future__ import annotations

from ..news.propagation import make_report
from ..engine.tile_view import can_settle
from ..info.sources import MESSENGER, SILENCE
from ..ontology import Report, SimDate
from ..world import World
from .encounter import roll_losses
from .model import base_risk_for, hunger, settle, strongest_hazard

NOISE_FOR_SURVIVORS = 0.2
EPSILON = 1e-9


def _return_survivors(world: World, pack) -> None:
    """Вернуть уцелевших в их двор: обратно в `member_ids`, труд восстановлен."""
    owner = (
        world.households.get(pack.owner_household_id)
        if pack.owner_household_id
        else None
    )
    if owner is None or owner.left_at is not None:
        return
    for pid in sorted(pack.member_ids):
        if pid not in owner.member_ids:
            owner.member_ids.append(pid)
        world.persons[pid].location_tile_id = owner.current_tile_id
    owner.member_ids.sort()
    owner.labor_days += 20.0 * len(pack.member_ids)


def resolve_packs(world: World, date: SimDate) -> list[Report]:
    """Разрешить все дошедшие до срока посылки; вернуть порождённые известия."""
    produced: list[Report] = []
    for pack_id in sorted(world.packs):
        pack = world.packs[pack_id]
        if pack.kind in ("caravan", "household_move"):
            continue
        if pack.status != "in_transit" or date < pack.eta_date:
            continue
        destination = pack.destination_tile_id
        hazard = strongest_hazard(world, destination)

        if hazard is None:
            pack.status = "arrived"
            _return_survivors(world, pack)
            continue

        members = [pid for pid in pack.member_ids if world.persons.get(pid) is not None]
        base_risk = base_risk_for(world, hazard.kind)
        lost = roll_losses(world.rng.hazard, hazard, members, base_risk)
        for pid in lost:
            world.persons[pid].health = 0.0
            pack.member_ids.remove(pid)
        settle(hazard, ate=bool(lost))

        if not pack.member_ids:
            pack.status = "lost"
            produced.append(
                make_report(
                    world,
                    SILENCE,
                    "tile",
                    destination,
                    "Отряд не вернулся. Известий о нём нет.",
                    {},
                    date,
                    0,
                    0.3,
                    distorted=False,
                    noise=0.0,
                )
            )
            continue

        pack.status = "arrived"
        _return_survivors(world, pack)

        pressure = hazard.population * hunger(hazard)
        reported = pressure * (1.0 + world.rng.hazard.uniform(-0.2, 0.2))
        produced.append(
            make_report(
                world,
                MESSENGER,
                "tile",
                destination,
                f"Отряд дошёл, при нём {len(pack.member_ids)}.",
                {"hazard": {"kind": hazard.kind, "population_approx": round(reported, 2)}},
                date,
                0,
                0.6,
                distorted=abs(reported - pressure) > 1e-9,
                noise=NOISE_FOR_SURVIVORS,
            )
        )
    return produced


def _move_cargo(world: World, cargo, destination_stock, reason: str, date: SimDate) -> None:
    """Перевести весь груз из стока воза в целевой сток (материя не исчезает)."""
    for good in sorted(cargo.amounts):
        amount = cargo.amounts.get(good, 0.0)
        if amount <= EPSILON:
            continue
        world.ledger.transfer(cargo, destination_stock, good, amount, reason, date)


def _return_migrants(world: World, pack, destination: str, date: SimDate) -> None:
    """Вернуть дошедший двор: груз в сток двора, люди на клетку назначения."""
    household = world.households.get(pack.owner_household_id or "")
    if household is None:
        return
    _move_cargo(
        world, pack.cargo, world.get_stock(household.stock_id), "arrival", date
    )
    household.member_ids = sorted(pack.member_ids)
    for pid in pack.member_ids:
        person = world.persons.get(pid)
        if person is not None:
            person.location_tile_id = destination
    household.current_tile_id = destination


def _detach_from_manor_books(world: World, household) -> None:
    """Снять дошедший двор со ВСЕХ книг маноров.

    Ушедший `left_at`-двор не должен ни пахать, ни есть по старой книге:
    у него нет ни барщины, ни пайка, ни гэфоля, ни урока, ни права на
    держание. Сток остаётся его собственным — материя не создаётся.
    """
    for manor_id in sorted(world.manors):
        manor = world.manors[manor_id]
        while household.id in manor.household_ids:
            manor.household_ids.remove(household.id)
    household.manor_id = None


def resolve_pack_loss(world: World, pack, date: SimDate) -> Report:
    """Погубить воз: весь груз — в стоячий сток клетки назначения.

    Полная гибель обоза или ушедшего двора материю не удаляет: каждый товар
    из `pack.cargo` переводится в `standing_stock_id` клетки назначения
    (reason `scattered`), сам воз помечается `lost` и остаётся
    зарегистрированным пустым. Игрок получает `silence` — из пустоты весть
    не приходит. Возвращает рождённый Report (для сбора в фазе).
    """
    destination = pack.destination_tile_id
    pack.lost_date = date
    tile = world.tiles.get(destination)
    if tile is not None:
        _move_cargo(
            world,
            pack.cargo,
            world.get_stock(tile.standing_stock_id),
            "scattered",
            date,
        )
    pack.status = "lost"
    return make_report(
        world,
        SILENCE,
        "tile",
        destination,
        "Обоз не дошёл. Известий о нём нет.",
        {},
        date,
        0,
        0.3,
        distorted=False,
    )


def resolve_migrations(world: World, date: SimDate) -> list[Report]:
    """Разрешить уходы дворов, дошедшие до `eta_date`.

    Двор идёт через `Pack kind="household_move"`: клетка меняется только по
    прибытии, а не в тик решения. Опасность клетки назначения может унести
    людей; при полной гибели груз ложится в стоячий сток клетки (`scattered`),
    а не исчезает. Уцелевшие возвращают груз в сток двора (`arrival`).

    Кап жилых дворов (`engine/tile_view.py`): если клетка назначения к моменту
    прибытия полна (гонка двух возов), двор возвращается на исходную клетку
    (`origin`), а не уплотняется сверх капа. Груз цел (`arrival` в свой сток),
    в статистике `settle_rejected_arrival`, игроку — `messenger` о тесноте.

    Семантика `facts['tile']` во всех ветках ниже: это клетка рассказа
    (`about` = `subject_id` = клетка назначения `destination`), а не координата
    двора. При переполнении капа двор физически остаётся на `origin`
    (`effective = origin`), но весть рассказывает о тесноте клетки назначения —
    контент это говорит честно (`"дошёл до {destination}, но клетка полна"`).
    """
    produced: list[Report] = []
    for pack_id in sorted(world.packs):
        pack = world.packs[pack_id]
        if pack.kind != "household_move" or pack.status != "in_transit":
            continue
        if date < pack.eta_date:
            continue
        destination = pack.destination_tile_id
        lost: list[str] = []
        hazard = strongest_hazard(world, destination)
        if hazard is not None:
            members = [
                pid for pid in pack.member_ids if world.persons.get(pid) is not None
            ]
            lost = roll_losses(
                world.rng.hazard, hazard, members, base_risk_for(world, hazard.kind)
            )
            for pid in lost:
                world.persons[pid].health = 0.0
                pack.member_ids.remove(pid)
            settle(hazard, ate=bool(lost))

        if not pack.member_ids:
            produced.append(resolve_pack_loss(world, pack, date))
            continue

        pack.status = "arrived"
        effective = destination
        household_id = pack.owner_household_id or ""
        if not can_settle(world, destination):
            effective = pack.origin_tile_id
            _return_migrants(world, pack, effective, date)
            household = world.households.get(pack.owner_household_id or "")
            if household is not None:
                _detach_from_manor_books(world, household)
            world.bump("settle_rejected_arrival")
            # `tile` ниже — клетка рассказа (about = destination), а не
            # координата двора: сам двор физически на `origin` (см. `effective`).
            produced.append(
                make_report(
                    world,
                    MESSENGER,
                    "tile",
                    destination,
                    f"Двор {household_id} дошёл до {destination}, "
                    "но клетка полна и не села.",
                    {"tile": destination, "household_id": household_id},
                    date,
                    0,
                    0.5,
                    distorted=False,
                )
            )
            continue
        _return_migrants(world, pack, effective, date)
        household = world.households.get(pack.owner_household_id or "")
        if household is not None:
            _detach_from_manor_books(world, household)
        if lost:
            # Частичная потеря: `tile` — тоже about (destination); двор дошёл.
            produced.append(
                make_report(
                    world,
                    MESSENGER,
                    "tile",
                    destination,
                    f"Двор {household_id} дошёл до {destination}, "
                    f"потеряв {len(lost)}.",
                    {
                        "lost": len(lost),
                        "household_id": household_id,
                        "tile": destination,
                    },
                    date,
                    0,
                    0.5,
                    distorted=False,
                )
            )
        else:
            # Успешное прибытие: `tile` = about (destination) = координата двора.
            produced.append(
                make_report(
                    world,
                    MESSENGER,
                    "tile",
                    destination,
                    f"Двор {household_id} ушёл и дошёл до {destination}.",
                    {
                        "members": len(pack.member_ids),
                        "household_id": household_id,
                        "tile": destination,
                    },
                    date,
                    0,
                    0.5,
                    distorted=False,
                )
            )
    return produced
