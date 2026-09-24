"""Слух авантюриста: риск оценивается по СВОЕМУ известию, а не по истине.

Авантюрист решает идти, опираясь на последний доехавший `Report` о клетке.
Если слуха нет — он не знает опасности и потому не может её бояться: риск в
решении занижен. Истинный `Hazard` в решении не читается вовсе.
"""

from __future__ import annotations

from .knowledge import PlayerKnowledge

UNKNOWN_HAZARD_RISK = 0.02


def perceived_hazard(knowledge: PlayerKnowledge, about: str) -> dict:
    """Услышанная опасность места: `facts['hazard']` последнего известия."""
    entry = knowledge.latest(about)
    if entry is None:
        return {}
    hazard = entry.facts.get("hazard")
    return dict(hazard) if isinstance(hazard, dict) else {}


def perceived_risk(
    knowledge: PlayerKnowledge,
    about: str,
    group_size: int,
    hazard_rules: dict,
) -> float:
    """Оценка риска по слуху: базовый риск опасности × размер ватаги / группа.

    Без слуха риск считается низким (`UNKNOWN_HAZARD_RISK`), а не истинным.
    """
    rumor = perceived_hazard(knowledge, about)
    if not rumor:
        return UNKNOWN_HAZARD_RISK
    kind = rumor.get("kind")
    rule = hazard_rules.get(kind)
    base = 0.05
    if rule is not None:
        base = float(rule.params.get("travel_risk", base))
    population = float(rumor.get("population_approx", 1.0))
    pressure = base * max(0.0, population)
    defense = max(1, group_size) ** 2
    return min(0.95, pressure / (pressure + defense)) if pressure + defense else 0.0
