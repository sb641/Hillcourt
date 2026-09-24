"""Демография: сытый двор растит семью — дети становятся руками.

Ядро средневековой мотивации: больше детей = больше рабочих рук. Двор рожает,
если сыт несколько месяцев подряд (излишек еды), и не рожает в голод. Ребёнок —
рот сразу (0.7), руки — через `birth_maturity_months` (игровая абстракция:
недоросль входит в тягло). Люди — не материя: рождение не трогает Ledger.
"""

from __future__ import annotations

from ..ontology import Household, Person, SimDate
from ..world import World
from .needs import food_months, member_counts

EPSILON = 1e-9


def _config(world: World):
    return world.needs


def demography_month(world: World, date: SimDate) -> None:
    """Состарить всех на месяц, повысить недорослей, родить у сытых дворов."""
    cfg = _config(world)
    if cfg is None:
        return
    for pid in sorted(world.persons):
        person = world.persons[pid]
        person.age_months = (person.age_months or 0) + 1
        if person.age_class == "child" and person.age_months >= cfg.birth_maturity_months:
            person.age_class = "adult"
            world.bump("maturations")
    _die_by_age(world)
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None:
            continue
        months = food_months(world, household)
        if months + EPSILON >= cfg.birth_food_months:
            household.food_streak += 1
        else:
            household.food_streak = 0
        if household.food_streak < cfg.birth_streak_months:
            continue
        adults, _, _ = member_counts(world, household)
        if adults < cfg.birth_adults_required:
            continue
        if len(household.member_ids) >= cfg.birth_max_household:
            continue
        _give_birth(world, household, date)
        household.food_streak = 0


def _die_by_age(world: World) -> None:
    """Естественная смертность по возрасту: рты не копятся бесконечно.

    Сценарные взрослые в расцвете не мрут случайно (иначе гранты падают):
    мрут младенцы, старики и старые. Люди — не материя: Ledger не трогаем.
    Детерминизм — поток `rng.economy`, порядок по `id`.
    """
    cfg = _config(world)
    dead: list[str] = []
    for pid in sorted(world.persons):
        person = world.persons[pid]
        age = person.age_months or 0
        if person.age_class == "child":
            rate = cfg.death_child_per_month
        elif person.age_class == "elder":
            rate = cfg.death_elder_per_month
        elif age >= cfg.death_old_age_months:
            rate = cfg.death_adult_per_month + cfg.death_old_age_extra
        else:
            rate = 0.0
        if rate > 0 and world.rng.economy.random() < rate:
            dead.append(pid)
    for pid in dead:
        person = world.persons.pop(pid, None)
        if person is None:
            continue
        household = world.households.get(person.household_id)
        if household is not None and pid in household.member_ids:
            household.member_ids.remove(pid)
        world.bump("deaths")


def _give_birth(world: World, household: Household, date: SimDate) -> Person:
    """Родиться во дворе: новый ребёнок с детерминированным характером.

    Характер — из хеша id, а не из `rng.economy`: роды не сдвигают общий поток
    случайности, иначе жребий трио и караванов реролится (соль/окна плывут).
    """
    import hashlib

    household.birth_count += 1
    pid = f"{household.id}_b{household.birth_count}"
    digest = hashlib.md5(pid.encode("utf-8")).digest()
    person = Person(
        id=pid,
        name=pid,
        household_id=household.id,
        age_class="child",
        curiosity=(digest[0] / 255.0),
        fear=(digest[1] / 255.0),
        health=1.0,
        location_tile_id=household.current_tile_id,
        personal_status=household.personal_status,
        land_relation=household.land_relation,
        obligation_bundle=None,
        age_months=0,
    )
    world.persons[pid] = person
    household.member_ids.append(pid)
    world.bump("births")
    return person
