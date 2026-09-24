"""Каналы известия: кто свидетель, сколько идёт, насколько врёт.

Словарь `source` узкий и фиксированный (docs/05_information.md):
    eye_from_hill  — сам двор игрока, задержки нет
    adjacent_daily — соседние дворы, месяц ходу
    messenger      — гонец/шериф, месяц ходу
    caravan        — проходящий обоз, два месяца ходу
    silence        — канал не пришёл: отдельный сигнал, а не «всё хорошо»
"""

from __future__ import annotations

from dataclasses import dataclass

EYE_FROM_HILL = "eye_from_hill"
ADJACENT_DAILY = "adjacent_daily"
MESSENGER = "messenger"
CARAVAN = "caravan"
SILENCE = "silence"

ALL_SOURCES: tuple[str, ...] = (
    EYE_FROM_HILL,
    ADJACENT_DAILY,
    MESSENGER,
    CARAVAN,
    SILENCE,
)


@dataclass(frozen=True)
class Channel:
    """Свойства канала: задержка в месяцах, уверенность и типичный шум."""

    delay_months: int
    confidence: float
    noise: float


CHANNELS: dict[str, Channel] = {
    EYE_FROM_HILL: Channel(0, 1.0, 0.0),
    ADJACENT_DAILY: Channel(1, 0.7, 0.3),
    MESSENGER: Channel(1, 0.5, 0.15),
    CARAVAN: Channel(2, 0.4, 0.4),
    SILENCE: Channel(0, 0.3, 0.0),
}
