"""Потребности двора: рот, корм скота, дрова зимой, износ топора (needs.yml)."""

from __future__ import annotations

from pathlib import Path

import yaml

from ..ontology import Household, NeedConfig
from ..world import World

EPSILON = 1e-9
ADULT_LABOR_DAYS = 20.0


def load_needs(path: str | Path) -> NeedConfig:
    """Прочитать needs.yml и собрать строгий NeedConfig."""
    with Path(path).open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"needs.yml '{path}': ожидался словарь верхнего уровня")
    unknown = sorted(set(data) - {"version", "food", "fuel", "livestock", "tool", "relief", "birth", "death"})
    if unknown:
        raise ValueError(f"needs.yml '{path}': неизвестные разделы {unknown}")
    food = data.get("food") or {}
    fuel = data.get("fuel") or {}
    livestock = data.get("livestock") or {}
    tool = data.get("tool") or {}
    relief = data.get("relief") or {}
    birth = data.get("birth") or {}
    death = data.get("death") or {}
    config = NeedConfig(
        adult_food_per_month=float(food.get("adult_per_month", 1.0)),
        child_food_per_month=float(food.get("child_per_month", 0.7)),
        elder_food_per_month=float(food.get("elder_per_month", 0.8)),
        edible_order=list(food.get("edible_order", ["grain"])),
        winter_months=[int(m) for m in fuel.get("winter_months", [])],
        firewood_per_adult_winter_month=float(
            fuel.get("firewood_per_adult_winter_month", 0.0)
        ),
        feed_good=str(livestock.get("feed_good", "hay")),
        feed_per_month={
            str(k): float(v) for k, v in (livestock.get("feed_per_month") or {}).items()
        },
        axe_wear_per_batch=float(tool.get("wear_per_batch", 0.0)),
        axe_break_below=float(tool.get("break_below", 0.0)),
        wear_recipes=list(tool.get("wear_recipes", [])),
        relief_min_court_grain=float(relief.get("min_court_grain", 0.0)),
        relief_amount=float(relief.get("amount", 0.0)),
        birth_food_months=float(birth.get("food_months", 3.0)),
        birth_streak_months=int(birth.get("streak_months", 3)),
        birth_max_household=int(birth.get("max_household", 8)),
        birth_adults_required=int(birth.get("adults_required", 2)),
        birth_maturity_months=int(birth.get("maturity_months", 24)),
        death_child_per_month=float(death.get("child_per_month", 0.001)),
        death_adult_per_month=float(death.get("adult_per_month", 0.002)),
        death_elder_per_month=float(death.get("elder_per_month", 0.02)),
        death_old_age_months=int(death.get("old_age_months", 600)),
        death_old_age_extra=float(death.get("old_age_extra", 0.01)),
    )
    # Сезон выпаса — довесок вне полей `NeedConfig` (поле онтологии чужое):
    # месяцы подножного корма и доля зимней нормы сеном в них (допуск ADR 0053).
    config.graze_months = [int(m) for m in livestock.get("graze_months", [])]
    config.graze_hay_fraction = float(livestock.get("graze_hay_fraction", 1.0))
    return config


def hay_fraction(world: World, month: int) -> float:
    """Доля зимней нормы сена в месяц: выпас дешевит, стойло платит полностью.

    Летом (`graze_months`) стадо на подножном корму — сено только коэффициентом
    (`graze_hay_fraction`, рабочий и стойловый скот тоже с коэффициентом, не
    нулём). В остальные месяцы — полная норма.
    """
    needs = world.needs
    if needs is None:
        return 1.0
    if month in list(getattr(needs, "graze_months", []) or []):
        return float(getattr(needs, "graze_hay_fraction", 1.0))
    return 1.0


def hay_need_rate(world: World, animal: str, month: int) -> float:
    """Норма сена на голову в месяц с учётом выпаса."""
    needs = world.needs
    if needs is None:
        return 0.0
    return float(needs.feed_per_month.get(animal, 0.0)) * hay_fraction(world, month)


def member_counts(world: World, household: Household) -> tuple[int, int, int]:
    """Вернуть (взрослые, дети, старики) двора по age_class людей."""
    adults = children = elders = 0
    for pid in household.member_ids:
        person = world.persons.get(pid)
        if person is None:
            continue
        if person.age_class == "adult":
            adults += 1
        elif person.age_class == "child":
            children += 1
        elif person.age_class == "elder":
            elders += 1
    return adults, children, elders


def monthly_food_need(world: World, household: Household) -> float:
    """Сколько «рот-единиц» еды нужно двору в месяц."""
    needs = world.needs
    adults, children, elders = member_counts(world, household)
    if needs is None:
        return float(adults) + 0.7 * children
    return (
        needs.adult_food_per_month * adults
        + needs.child_food_per_month * children
        + needs.elder_food_per_month * elders
    )


def edible_mass(world: World, household: Household) -> float:
    """Суммарная масса съедобного в стоке двора (без учёта питательности)."""
    stock = world.get_stock(household.stock_id)
    order = world.needs.edible_order if world.needs else ["grain"]
    total = 0.0
    for good in order:
        total += stock.amounts.get(good, 0.0)
    return total


def food_months(world: World, household: Household) -> float:
    """На сколько месяцев хватит еды при текущем рте (0 — еды нет)."""
    need = monthly_food_need(world, household)
    if need <= EPSILON:
        return 12.0
    return edible_mass(world, household) / need
