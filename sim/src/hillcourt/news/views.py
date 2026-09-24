"""Представление игрока: только доставленные Report, без чтения истины мира.

Игрок не держит ссылок на `Tile`/`Household`; его знание — это список
`ReportView`, выведенных из `Report`. Устаревшее известие помечается `stale`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..ontology import Report, SimDate
from .propagation import delivered_reports

STALE_AFTER_MONTHS = 2


@dataclass
class ReportView:
    """Одна строка знания игрока, выведенная из Report."""

    id: str
    source: str
    subject_kind: str
    subject_id: str
    content: str
    facts: dict[str, Any]
    event_date: SimDate
    delivery_date: SimDate
    confidence: float
    distorted: bool
    stale: bool


@dataclass
class PlayerView:
    """Знание игрока на дату: только известия, ничего больше."""

    as_of: SimDate
    entries: list[ReportView]

    def sources(self) -> list[str]:
        """Список источников, встречающихся в знании игрока."""
        return sorted({entry.source for entry in self.entries})


def _absolute_month(date: SimDate, months_per_year: int) -> int:
    return date.year * months_per_year + date.month


def _to_view(report: Report, as_of: SimDate, months_per_year: int) -> ReportView:
    age = _absolute_month(as_of, months_per_year) - _absolute_month(
        report.event_date, months_per_year
    )
    return ReportView(
        id=report.id,
        source=report.source,
        subject_kind=report.subject_kind,
        subject_id=report.subject_id,
        content=report.content,
        facts=dict(report.facts),
        event_date=report.event_date,
        delivery_date=report.delivery_date,
        confidence=report.confidence,
        distorted=report.distorted,
        stale=age > STALE_AFTER_MONTHS,
    )


def build_player_view(world, on_or_before: SimDate | None = None) -> PlayerView:
    """Собрать знание игрока из доставленных Report на дату (по умолчанию — текущую)."""
    as_of = on_or_before or world.clock.date
    months_per_year = world.clock.months_per_year
    entries = [
        _to_view(report, as_of, months_per_year)
        for report in delivered_reports(world, as_of)
    ]
    return PlayerView(as_of=as_of, entries=entries)
