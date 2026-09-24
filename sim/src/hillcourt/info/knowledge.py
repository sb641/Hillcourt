"""Знание игрока по местам: последний доехавший Report, срок годности, молчание.

`build_player_knowledge` читает только доставленные `Report` (через
`news.propagation.delivered_reports`). Никакого доступа к `Tile`/`Household`/
`Stock` здесь нет: `about` — это идентификатор места из самого известия.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..ontology import Report, SimDate
from ..news.propagation import delivered_reports
from .sources import SILENCE

STALE_AFTER_MONTHS = 2


def absolute_month(date: SimDate, months_per_year: int = 12) -> int:
    """Абсолютный номер месяца: (год-1)*12 + месяц. Год 1, месяц 1 -> 0."""
    return (date.year - 1) * months_per_year + (date.month - 1)


@dataclass
class KnowledgeEntry:
    """Одна запись знания: `Report` глазами игрока, без полей истины мира."""

    about: str
    source: str
    observed_month: int
    arrived_month: int
    noise: float
    confidence: float
    distorted: bool
    content: str
    facts: dict[str, Any]
    stale: bool


@dataclass
class PlayerKnowledge:
    """Знание игрока на дату: записи, последнее по месту и сигналы молчания."""

    as_of: SimDate
    entries: list[KnowledgeEntry] = field(default_factory=list)
    by_about: dict[str, KnowledgeEntry] = field(default_factory=dict)

    def latest(self, about: str) -> KnowledgeEntry | None:
        """Последнее доехавшее известие о месте (по `arrived_month`)."""
        return self.by_about.get(about)

    def silences(self) -> list[KnowledgeEntry]:
        """Отдельные сигналы молчания: канал ожидался и не пришёл."""
        return [entry for entry in self.entries if entry.source == SILENCE]


def _entry_from_report(report: Report, as_of: SimDate, months_per_year: int) -> KnowledgeEntry:
    age = absolute_month(as_of, months_per_year) - absolute_month(
        report.event_date, months_per_year
    )
    return KnowledgeEntry(
        about=report.subject_id,
        source=report.source,
        observed_month=absolute_month(report.event_date, months_per_year),
        arrived_month=absolute_month(report.delivery_date, months_per_year),
        noise=report.noise,
        confidence=report.confidence,
        distorted=report.distorted,
        content=report.content,
        facts=dict(report.facts),
        stale=age > STALE_AFTER_MONTHS,
    )


def build_player_knowledge(world, as_of: SimDate | None = None) -> PlayerKnowledge:
    """Собрать знание игрока из доставленных известий на дату."""
    date = as_of or world.clock.date
    mpy = world.clock.months_per_year
    entries = [
        _entry_from_report(report, date, mpy)
        for report in delivered_reports(world, date)
    ]
    by_about: dict[str, KnowledgeEntry] = {}
    for entry in entries:
        previous = by_about.get(entry.about)
        if previous is None or entry.arrived_month >= previous.arrived_month:
            by_about[entry.about] = entry
    return PlayerKnowledge(as_of=date, entries=entries, by_about=by_about)
