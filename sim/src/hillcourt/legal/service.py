"""Правовой контур службы 60–90 суток и племенного вызова."""

from __future__ import annotations

from typing import Optional

from ..ontology import Obligation, Pack, SimDate, Stock
from ..world import World

MUSTER_PERIODS = (2, 3)


def _add_months(date: SimDate, months: int) -> SimDate:
    total = (date.year - 1) * 12 + date.month - 1 + months
    return SimDate(total // 12 + 1, total % 12 + 1, date.day)


def _adult_ids(world: World, household) -> list[str]:
    return [
        pid
        for pid in household.member_ids
        if (person := world.persons.get(pid)) is not None
        and person.age_class == "adult"
        and person.health > 0
    ]


def _outbound_pack(world: World, obligation: Obligation) -> Pack:
    pack = world.packs.get(f"pack_{obligation.id}_out")
    if pack is None:
        raise ValueError(f"У повинности '{obligation.id}' нет outbound Pack")
    return pack


def _return_pack(world: World, obligation: Obligation) -> Pack:
    pack = world.packs.get(f"pack_{obligation.id}_return")
    if pack is None:
        raise ValueError(f"У повинности '{obligation.id}' нет return Pack")
    return pack


def create_muster_obligation(
    world: World,
    household_id: str,
    required_persons_count: int,
    period_months: int = 2,
) -> Obligation:
    """Создать повинность вызова на 2 или 3 месяца для двора."""
    household = world.households.get(household_id)
    if household is None:
        raise ValueError(f"Нет двора '{household_id}'")
    if int(period_months) not in MUSTER_PERIODS:
        raise ValueError(f"Срок службы должен быть 2 или 3 месяца, не {period_months}")
    count = int(required_persons_count)
    if count < 0:
        raise ValueError("Число необходимых голов не может быть отрицательным")
    muster_number = sum(
        1
        for oid in household.obligation_ids
        if world.obligations.get(oid) is not None
        and world.obligations[oid].kind == "muster"
    ) + 1
    obligation_id = f"obl_{household_id}_muster_{muster_number}"
    obligation = Obligation(
        id=obligation_id,
        household_id=household_id,
        kind="muster",
        due_good=None,
        due_amount=float(count),
        period_months=int(period_months),
        paid_total=0.0,
        arrears=0.0,
        basis="fixed",
        call_status="pending",
    )
    world.obligations[obligation.id] = obligation
    household.obligation_ids.append(obligation.id)
    return obligation


def service_deadline(world: World, obligation: Obligation) -> SimDate:
    """Вернуть дедлайн службы от даты выхода outbound Pack."""
    if obligation.kind != "muster":
        raise ValueError(f"Повинность '{obligation.id}' не является muster")
    outbound = _outbound_pack(world, obligation)
    return _add_months(outbound.departed_date, obligation.period_months)


def _new_pack(
    world: World,
    obligation: Obligation,
    suffix: str,
    origin_tile_id: str,
    destination_tile_id: str,
    member_ids: list[str],
) -> Pack:
    pack_id = f"pack_{obligation.id}_{suffix}"
    if pack_id in world.packs:
        raise ValueError(f"Pack '{pack_id}' уже существует")
    cargo_id = f"pack:{pack_id}"
    cargo = Stock(id=cargo_id, owner_kind="pack", owner_id=pack_id, amounts={})
    world.add_stock(cargo)
    pack = Pack(
        id=pack_id,
        kind="party",
        origin_tile_id=origin_tile_id,
        destination_tile_id=destination_tile_id,
        route=[origin_tile_id, destination_tile_id],
        member_ids=list(member_ids),
        cargo=cargo,
        departed_date=world.clock.date,
        eta_date=_add_months(world.clock.date, obligation.period_months),
        status="in_transit",
        owner_household_id=obligation.household_id,
        obligation_id=obligation.id,
    )
    world.packs[pack.id] = pack
    return pack


def start_muster_service(
    world: World,
    obligation: Obligation,
    destination_tile_id: str,
    member_ids: Optional[list[str]] = None,
) -> Optional[Pack]:
    """Начать службу фактически вышедшими людьми; без людей будет unable."""
    if obligation.kind != "muster":
        raise ValueError(f"Повинность '{obligation.id}' не является muster")
    if obligation.call_status != "pending":
        raise ValueError(
            f"Служба '{obligation.id}' уже имеет статус '{obligation.call_status}'"
        )
    household = world.households.get(obligation.household_id)
    if household is None:
        raise ValueError(f"Нет двора '{obligation.household_id}'")
    allowed = set(_adult_ids(world, household))
    going = list(_adult_ids(world, household) if member_ids is None else member_ids)
    if any(pid not in allowed for pid in going):
        raise ValueError("В службу можно отправить только живых взрослых своего двора")
    if not going:
        obligation.call_status = "unable"
        return None
    origin = household.current_tile_id
    pack = _new_pack(
        world, obligation, "out", origin, destination_tile_id, going
    )
    for pid in going:
        household.member_ids.remove(pid)
        person = world.persons[pid]
        person.location_tile_id = destination_tile_id
    obligation.call_status = "in_service"
    return pack


def create_return_pack(
    world: World,
    obligation: Obligation,
    member_ids: Optional[list[str]] = None,
) -> Optional[Pack]:
    """Создать обратный Pack после прибытия outbound Pack."""
    if obligation.kind != "muster":
        raise ValueError(f"Повинность '{obligation.id}' не является muster")
    if obligation.call_status != "in_service":
        raise ValueError(
            f"Служба '{obligation.id}' не находится в in_service"
        )
    outbound = _outbound_pack(world, obligation)
    if outbound.status != "arrived":
        raise ValueError("Возврат возможен только после прибытия outbound Pack")
    if member_ids is None:
        member_ids = list(outbound.member_ids)
    if not member_ids:
        obligation.call_status = "unable"
        return None
    return _new_pack(
        world,
        obligation,
        "return",
        outbound.destination_tile_id,
        outbound.origin_tile_id,
        list(member_ids),
    )


def complete_muster_return(world: World, obligation: Obligation) -> None:
    """Зафиксировать прибытие return Pack и вернуть людей в двор."""
    if obligation.kind != "muster":
        raise ValueError(f"Повинность '{obligation.id}' не является muster")
    return_pack = _return_pack(world, obligation)
    if return_pack.status != "arrived":
        raise ValueError("Return Pack ещё не прибыл")
    household = world.households.get(obligation.household_id)
    if household is None:
        raise ValueError(f"Нет двора '{obligation.household_id}'")
    household.member_ids = sorted(set(household.member_ids) | set(return_pack.member_ids))
    for pid in return_pack.member_ids:
        person = world.persons.get(pid)
        if person is not None:
            person.location_tile_id = return_pack.destination_tile_id
    household.current_tile_id = return_pack.destination_tile_id
    obligation.call_status = "returned"


def mark_muster_overdue(
    world: World, obligation: Obligation, as_of: Optional[SimDate] = None
) -> bool:
    """Перевести незавершённую службу в overdue после дедлайна."""
    if obligation.kind != "muster" or obligation.call_status != "in_service":
        return False
    date = as_of or world.clock.date
    deadline = service_deadline(world, obligation)
    if (date.year, date.month, date.day) <= (
        deadline.year,
        deadline.month,
        deadline.day,
    ):
        return False
    obligation.call_status = "overdue"
    return True


def record_muster_lost(world: World, obligation: Obligation, pack_id: str) -> None:
    """Потерянный Pack означает unable, а не refused."""
    pack = world.packs.get(pack_id)
    if pack is None or pack.obligation_id != obligation.id:
        raise ValueError(f"Pack '{pack_id}' не связан с '{obligation.id}'")
    if pack.status != "lost":
        raise ValueError(f"Pack '{pack_id}' не потерян")
    obligation.call_status = "unable"


def refuse_muster(world: World, obligation: Obligation) -> None:
    """Зафиксировать активное решение племени об отказе от вызова."""
    if obligation.kind != "muster":
        raise ValueError(f"Повинность '{obligation.id}' не является muster")
    if obligation.call_status not in {"pending", "in_service"}:
        raise ValueError(
            f"Нельзя отказаться из статуса '{obligation.call_status}'"
        )
    obligation.call_status = "refused"


def call_tribe_muster(
    world: World,
    tribe_id: str,
    destination_tile_id: str,
    period_months: int = 2,
) -> list[tuple[Obligation, Optional[Pack]]]:
    """Создать повинности и outbound Pack для дворов союзного племени."""
    tribe = world.tribes.get(tribe_id)
    if tribe is None:
        raise ValueError(f"Нет племени '{tribe_id}'")
    if tribe.stance not in {"allied", "vassal"}:
        raise PermissionError(f"Племя '{tribe_id}' не обязано отвечать на вызов")
    settlement = world.settlements.get(tribe.settlement_id)
    if settlement is None:
        raise ValueError(f"Нет поселения '{tribe.settlement_id}'")
    result: list[tuple[Obligation, Optional[Pack]]] = []
    for household_id in sorted(settlement.household_ids):
        household = world.households[household_id]
        obligation = create_muster_obligation(
            world, household_id, len(_adult_ids(world, household)), period_months
        )
        pack = start_muster_service(
            world, obligation, destination_tile_id, _adult_ids(world, household)
        )
        result.append((obligation, pack))
    return result


def call_manor_muster(
    world: World,
    manor_id: str,
    destination_tile_id: str,
    period_months: int = 2,
) -> tuple[Obligation, Optional[Pack]]:
    """Создать повинность и outbound Pack для книжного двора тэна."""
    manor = world.manors.get(manor_id)
    if manor is None or manor.parent_manor_id is None:
        raise ValueError(f"Нет вложенный manor '{manor_id}'")
    person = world.persons.get(manor.holder_person_id)
    if person is None:
        raise ValueError(f"У манора '{manor_id}' нет держателя")
    household = world.households[person.household_id]
    if household.legal_status_id != "thegn":
        raise PermissionError(f"Двор '{household.id}' не является тэном")
    obligation = create_muster_obligation(
        world, household.id, len(_adult_ids(world, household)), period_months
    )
    pack = start_muster_service(
        world, obligation, destination_tile_id, _adult_ids(world, household)
    )
    return obligation, pack
