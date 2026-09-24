"""Приём плоских событий месяца для формирования известий.

События — входной сигнал `phase_inform`, а не источник истины для игрока.
Модуль только фильтрует и суммирует уже переданные записи; он не читает
склады, не меняет `Report` и не вводит новые мировые сущности.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

REPORTABLE_KINDS = frozenset(
    {
        "caravan_arrival",
        "caravan_departure",
        "pack_arrival",
        "pack_departure",
        "harvest",
        "hay_mowed",
        "graze",
    }
)


def reportable_events(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Вернуть копии событий, допустимых для отчётов, в стабильном порядке.

    Принимает плоские записи тика с `kind`, `good` и `amount`. Неизвестные
    виды и записи без обязательных полей отбрасываются, поэтому принимающая
    сторона не строит `Report` из частично сформированного события.
    """
    result: list[dict[str, Any]] = []
    for event in events:
        if event.get("kind") not in REPORTABLE_KINDS:
            continue
        if not event.get("good") or event.get("amount") is None:
            continue
        result.append(dict(event))
    result.sort(
        key=lambda event: (
            str(event.get("date", "")),
            str(event.get("kind", "")),
            str(event.get("good", "")),
            str(event.get("src_id", "")),
            str(event.get("dst_id", "")),
        )
    )
    return result


def event_amount(
    events: Iterable[Mapping[str, Any]],
    kind: str,
    good: str,
    settlement_id: str | None = None,
) -> float:
    """Сумма количества одного вида события для товара и поселения."""
    return sum(
        float(event.get("amount", 0.0))
        for event in reportable_events(events)
        if event.get("kind") == kind
        and event.get("good") == good
        and (settlement_id is None or event.get("settlement_id") == settlement_id)
    )
