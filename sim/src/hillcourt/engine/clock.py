"""Календарь: месяц — основной тик симуляции."""

from __future__ import annotations

from ..ontology import SimDate


class Clock:
    """Счётчик месяцев с возможностью отдать текущую дату."""

    def __init__(
        self, year: int, month: int, day: int = 1, months_per_year: int = 12
    ) -> None:
        self.year = year
        self.month = month
        self.day = day
        self.months_per_year = months_per_year

    @property
    def date(self) -> SimDate:
        """Текущая дата симуляции."""
        return SimDate(self.year, self.month, self.day)

    def advance_month(self) -> None:
        """Сдвинуть календарь на один месяц."""
        nxt = self.date.advance(self.months_per_year)
        self.year, self.month = nxt.year, nxt.month
