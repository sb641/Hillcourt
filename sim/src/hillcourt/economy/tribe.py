"""Экономика племени v1: натуральный оброк и потенциал вызова (ADR 0064).

Семь полей `Tribe` (ADR 0064), состав вычисляется из `Settlement.household_ids`.
Здесь только два рычага Economist и оба на существующих сущностях:

- **Оброк** (`tribute_grain`, по умолчанию 0.6 зерна/мес на двор, как сокмен) —
  существующий `levy`-механизм: по одной `Obligation(kind=levy)` на племенный
  двор при `stance ∈ {allied, vassal}`; оплату и `arrears` делает существующая
  фаза `phase_obligations` (перевод в амбар корня). При `independent` повинности
  нет. С пустым двором (нет живых людей) оброк не берётся: повинность не
  заводится, долг не растёт — племя без ртов не платит и не «должит».
- **Потенциал вызова** — данные, не действие (ADR 0066: вызов — v2): сколько
  комплектов племя может дать (`muster_kits` — потолок) и даёт ли (`war_kit` в
  стоках дворов при живых взрослых). Нового вызова/handler/`send_sally` нет,
  `war_kit` тэна нормой не считается.

Материя — только `Ledger.transfer` существующей фазы (И-1); новая фаза тика
не заводится (вызов `manor_month` в той же фазе, что и повинности).
"""

from __future__ import annotations

from ..ontology import Household, Obligation, SimDate, Tribe
from ..world import World

EPSILON = 1e-9
TRIBUTE_STANCE: frozenset[str] = frozenset({"allied", "vassal"})
KIT_GOOD = "war_kit"
LEVY_PREFIX = "levy_tribe_"


def tribe_households(world: World, tribe: Tribe) -> list[Household]:
    """Живые дворы племени: состав вычисляется из поселения (ADR 0064)."""
    settlement = world.settlements.get(tribe.settlement_id)
    if settlement is None:
        return []
    out: list[Household] = []
    for hid in settlement.household_ids:
        household = world.households.get(hid)
        if household is not None and household.left_at is None:
            out.append(household)
    return sorted(out, key=lambda h: h.id)


def _adults(world: World, household: Household) -> int:
    return sum(
        1
        for pid in household.member_ids
        if (person := world.persons.get(pid)) is not None
        and person.age_class == "adult"
    )


def _living_people(world: World, household: Household) -> int:
    return sum(1 for pid in household.member_ids if pid in world.persons)


def _levy_id(tribe: Tribe, household: Household) -> str:
    return f"{LEVY_PREFIX}{tribe.id}_{household.id}"


def _find_levy(world: World, tribe: Tribe, household: Household) -> Obligation | None:
    oid = _levy_id(tribe, household)
    obligation = world.obligations.get(oid)
    if obligation is not None:
        return obligation
    for known_id in household.obligation_ids:
        known = world.obligations.get(known_id)
        if known is not None and known.kind == "levy" and known_id == oid:
            return known
    return None


def tribe_tribute_month(world: World, date: SimDate) -> list[Obligation]:
    """Заводить/держать месячный оброк племени; вернуть повинности этого месяца.

    `stance ∈ {allied, vassal}` и есть живые люди в племенном дворе: одна
    `Obligation(kind=levy, due_good=grain, period_months=1, due_amount=
    tribute_grain)`. Оплата/`arrears` — существующей `phase_obligations`.
    `independent` или пустой двор (нет людей) — повинности нет; договор снятой
    повинности тоже снимает (долг не растёт вслед уходу).
    """
    out: list[Obligation] = []
    for tribe_id in sorted(getattr(world, "tribes", {})):
        tribe = world.tribes[tribe_id]
        households = tribe_households(world, tribe)
        owes = tribe.stance in TRIBUTE_STANCE and float(tribe.tribute_grain) > EPSILON
        for household in households:
            obligation = _find_levy(world, tribe, household)
            if not owes:
                # Стойка сменилась/оброк снят — повинности нет: договор снимает
                # и долг (иначе «независимое» племя копит долг как должник).
                if obligation is not None:
                    world.obligations.pop(obligation.id, None)
                    if obligation.id in household.obligation_ids:
                        household.obligation_ids.remove(obligation.id)
                    world.bump("tribe_levy_dropped", 1.0)
                continue
            if _living_people(world, household) <= 0:
                continue
            if obligation is None:
                obligation = Obligation(
                    id=_levy_id(tribe, household),
                    household_id=household.id,
                    kind="levy",
                    due_good="grain",
                    due_amount=float(tribe.tribute_grain),
                    period_months=1,
                    paid_total=0.0,
                    arrears=0.0,
                    right_id=None,
                    basis="fixed",
                )
                world.obligations[obligation.id] = obligation
                household.obligation_ids.append(obligation.id)
                world.bump("tribe_levy_created", 1.0)
            else:
                obligation.due_amount = float(tribe.tribute_grain)
                obligation.period_months = 1
                if obligation.due_good is None:
                    obligation.due_good = "grain"
            out.append(obligation)
    return out


def tribe_muster_kits(world: World, tribe: Tribe) -> float:
    """Сколько комплектов племя может дать: факт `war_kit` в стоках дворов.

    Потолок — `muster_kits` (потенциал из ADR 0064); факт ниже потолка, когда
    комплектов в руках меньше. Новый товар/юнит не заводится: `war_kit`
    уже в каталоге. Племя без стойки союза/вассала не даёт ничего.
    """
    if tribe.stance not in TRIBUTE_STANCE:
        return 0.0
    held = 0.0
    for household in tribe_households(world, tribe):
        stock = world.stocks.get(household.stock_id)
        if stock is not None:
            held += float(stock.amounts.get(KIT_GOOD, 0.0))
    return min(held, max(0.0, float(tribe.muster_kits)))


def tribe_can_muster(world: World, tribe: Tribe) -> bool:
    """Может ли племя дать вызов: есть взрослые и ≥1 комплект.

    Только проверка потенциала (ADR 0064/0066): вызова, `Pack` и handler нет.
    """
    if tribe.stance not in TRIBUTE_STANCE:
        return False
    if tribe_muster_kits(world, tribe) < 1.0 - EPSILON:
        return False
    return any(
        _adults(world, household) >= 1 for household in tribe_households(world, tribe)
    )
