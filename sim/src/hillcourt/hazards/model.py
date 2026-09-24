"""Модель опасности: популяция, сытость и шанс потерять человека.

Сытый зверь не нападает; голодный — нападает. Отряд защищается не линейно, а
квадратично по числу (строй): шанс потерять одного много меньше у троих, чем у
одиночки.
"""

from __future__ import annotations

from ..ontology import Hazard
from ..world import World

DEFAULT_TRAVEL_RISK = 0.1
SATIETY_GAIN_PER_MEAL = 0.4
SATIETY_DECAY_PER_MONTH = 0.1


def hunger(hazard: Hazard) -> float:
    """Голод опасности: 1 — очень голодна, 0 — сыта."""
    return max(0.0, min(1.0, 1.0 - hazard.satiety))


def per_person_loss_risk(hazard: Hazard, group_size: int, base_risk: float) -> float:
    """Шанс потерять конкретного человека из группы данного размера.

    `pressure` — напор опасности (база × популяция × голод); `defense` —
    защита строя, квадрат числа людей. Одиночка против того же напора гибнет
    заметно чаще, чем трое.
    """
    pressure = base_risk * max(0.0, hazard.population) * hunger(hazard)
    defense = float(max(1, group_size) ** 2)
    if pressure + defense <= 0:
        return 0.0
    return min(0.95, pressure / (pressure + defense))


def base_risk_for(world: World, kind: str) -> float:
    """Базовый риск опасности данного вида из каталога."""
    rule = world.catalogs.hazard_rules.get(kind)
    if rule is None:
        return DEFAULT_TRAVEL_RISK
    return float(rule.params.get("travel_risk", DEFAULT_TRAVEL_RISK))


def strongest_hazard(world: World, tile_id: str) -> Hazard | None:
    """Самая опасная активная угроза клетки (популяция × интенсивность)."""
    contenders = [
        hazard
        for hazard in world.hazards.values()
        if hazard.active and hazard.tile_id == tile_id
    ]
    if not contenders:
        return None
    return max(
        sorted(contenders, key=lambda h: h.id),
        key=lambda h: h.population * h.intensity,
    )


def settle(hazard: Hazard, ate: bool) -> None:
    """Обновить сытость: после добычи растёт, без добычи медленно падает."""
    if ate:
        hazard.satiety = min(1.0, hazard.satiety + SATIETY_GAIN_PER_MEAL)
    else:
        hazard.satiety = max(0.0, hazard.satiety - SATIETY_DECAY_PER_MONTH)
