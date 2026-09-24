"""Повинности: натуральная рента (доля или мешок) и трудовая повинность.

Вассал-вассала в v0 нет: `Obligation` всегда связывает двор с держателем земли,
которому он подчинён напрямую, и не порождает цепочку повинностей.
"""

from __future__ import annotations

from ..ontology import Obligation, ObligationTemplate

SHARE_BASE_SACK = 6.0


def rent_amount_for(template: ObligationTemplate, rent_share: float) -> float:
    """Размер натуральной ренты: фиксированный мешок или доля базового сбора."""
    if template.basis == "fixed":
        return float(template.default_amount or 0.0)
    return round(rent_share * SHARE_BASE_SACK, 3)


def is_corvee(obligation: Obligation) -> bool:
    """Трудовая повинность (на частокол), а не рента товаром."""
    return obligation.kind == "labor_duty"


def accrue_corvee(obligation: Obligation, labor_days: float) -> float:
    """Записать отработанные дни повинности в счётчик `corvee_days`."""
    obligation.corvee_days += float(labor_days)
    return obligation.corvee_days
