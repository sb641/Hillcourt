"""Розыгрыш встречи с опасностью по группе людей."""

from __future__ import annotations

import random

from ..ontology import Hazard
from .model import base_risk_for, per_person_loss_risk


def roll_losses(
    rng: random.Random,
    hazard: Hazard,
    member_ids: list[str],
    base_risk: float,
) -> list[str]:
    """Кто из группы не выжил. Итог детерминирован при фиксированном `rng`.

    Каждый человек проверяется по отдельности, поэтому отряд из трёх может
    потерять всех, одного или никого. Порядок — по `id`, ради И-6.
    """
    group_size = len(member_ids)
    risk = per_person_loss_risk(hazard, group_size, base_risk)
    return [pid for pid in sorted(member_ids) if rng.random() < risk]
