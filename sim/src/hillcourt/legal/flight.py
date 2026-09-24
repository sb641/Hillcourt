"""Бегство прикреплённого: отдельный риск, а не легальный уход.

`tied` не уходит без приказа. Бегство — своя вероятность; пойманного на земле
лорда возвращают (он остаётся). В тик v0 бегство не подключено: это вызов для
приказа/события, а не автоматический уход (см. ADR 0006).
"""

from __future__ import annotations

from ..world import World
from .regimes import can_leave

FLIGHT_CHANCE = 0.1


def attempt_flight(world: World, household, rng=None) -> bool:
    """Попытка бегства. True — ушёл, False — пойман/не убегал.

    Свободный не «бежит» (он уходит легально через `phase_migrate`), поэтому
    для него попытка не имеет смысла и возвращает False.
    """
    if can_leave(world, household):
        return False
    source = rng if rng is not None else world.rng.world
    if source.random() < FLIGHT_CHANCE:
        household.intent = "leave"
        household.left_at = world.clock.date
        household.settlement_id = None
        return True
    return False
