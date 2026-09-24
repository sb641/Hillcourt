"""Известия: рождение Report и выдача доставленных игроку."""

from __future__ import annotations

from typing import Any, Optional

from ..ontology import Report, SimDate
from ..world import World


def make_report(
    world: World,
    source: str,
    subject_kind: str,
    subject_id: str,
    content: str,
    facts: dict[str, Any],
    event_date: SimDate,
    delay_months: int,
    confidence: float,
    distorted: bool = False,
    noise: float = 0.0,
) -> Report:
    """Создать Report, присвоить детерминированный id и добавить в мир."""
    delivery = event_date
    for _ in range(max(0, delay_months)):
        delivery = delivery.advance(world.clock.months_per_year)
    report = Report(
        id=f"rep_{len(world.reports) + 1:04d}",
        source=source,
        subject_kind=subject_kind,
        subject_id=subject_id,
        content=content,
        facts=dict(facts),
        event_date=event_date,
        delivery_date=delivery,
        confidence=confidence,
        distorted=distorted,
        observer_id="player",
        noise=noise,
    )
    world.reports.append(report)
    return report


def delivered_reports(world: World, on_or_before: SimDate) -> list[Report]:
    """Доставленные к дате отчёты, отсортированные по (дата, id)."""
    delivered = [r for r in world.reports if r.delivery_date <= on_or_before]
    delivered.sort(key=lambda r: (r.delivery_date, r.id))
    return delivered
