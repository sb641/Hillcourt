"""Вести о смене занятия двора без раскрытия его текущего состояния."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ..ontology import Report, SimDate
from ..world import World
from .propagation import make_report

ACTION_CHANGE_KIND = "household_action_changed"
ACTION_CHANGE_DELAY_DAYS = 3
ACTION_CHANGE_NOISE = 0.3
ACTION_CHANGE_SOURCE = "adjacent_daily"


def _action_name(world: World, action_id: str) -> str:
    action = world.household_actions.get(action_id)
    return str(action.name) if action is not None else action_id


def make_action_change_report(
    world: World, event: Mapping[str, Any]
) -> Report | None:
    """Создать отложенную весть о смене занятия одного двора.

    Источник истины — событие смены, а не текущий склад или `Household`.
    Доставка через `delay_days` не меняет состояние мира и не раскрывает нынешнее
    занятие двора до получения `Report`.
    """
    if event.get("kind") != ACTION_CHANGE_KIND:
        return None
    household_id = str(event.get("household_id", ""))
    old_action = str(event.get("old_action", ""))
    new_action = str(event.get("new_action", ""))
    date = event.get("date")
    if not household_id or not old_action or not new_action:
        return None
    if not isinstance(date, SimDate) or old_action == new_action:
        return None
    delay_days = max(0, int(event.get("delay_days", ACTION_CHANGE_DELAY_DAYS)))
    observer_id = str(event.get("observer_id", "neighbor"))
    report = make_report(
        world,
        ACTION_CHANGE_SOURCE,
        "household",
        household_id,
        f"Соседи говорят: двор {household_id} сменил занятие с "
        f"{_action_name(world, old_action)} на {_action_name(world, new_action)}.",
        {
            "household_id": household_id,
            "old_action": old_action,
            "new_action": new_action,
            "change": "action_changed",
            "delay_days": delay_days,
        },
        date,
        0,
        0.7,
        distorted=False,
        noise=ACTION_CHANGE_NOISE,
        observer_id=observer_id,
    )
    report.delivery_date = date.advance_days(delay_days)
    return report


def report_action_changes(
    world: World, events: Iterable[Mapping[str, Any]] | None = None
) -> list[Report]:
    """Оформить все события смены занятия месяца в отложенные вести."""
    source = world.month_events if events is None else events
    reports: list[Report] = []
    for event in source:
        if event.get("kind") != ACTION_CHANGE_KIND:
            continue
        report = make_action_change_report(world, event)
        if report is not None:
            reports.append(report)
    return reports
