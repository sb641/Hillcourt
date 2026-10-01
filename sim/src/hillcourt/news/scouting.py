"""Канал разведки: наблюдения партии превращаются в известия игрока."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ..ontology import Report, SimDate
from ..world import World
from .propagation import make_report

SCOUT_SOURCE = "scout"
RUMORS: tuple[str, ...] = ("дым", "след", "костёр")
RUMOR_NOISE = 0.15


def _has_report(world: World, tile_id: str, date: SimDate) -> bool:
    """Проверить дедупликацию наблюдения по каналу, месту и дате."""
    return any(
        report.source == SCOUT_SOURCE
        and report.subject_id == tile_id
        and report.event_date == date
        for report in world.reports
    )


def make_scout_report(world: World, event: Mapping[str, Any]) -> Report | None:
    """Создать `Report` разведки из события `scout_observe`.

    `fact=True` означает наблюдение на том же гексе. При `fact=False` строка
    `rumor` — старый формат и игнорируется: текст выбирается из `rng_news`.
    Наблюдатель — `observer_id` партии, а не игрок. Пустое или неизвестное событие
    не порождает Report.
    """
    tile_id = str(event.get("tile_id", ""))
    observer_id = str(event.get("observer_id", ""))
    event_date = event.get("date")
    fact = event.get("fact") is True
    rumor = event.get("rumor")
    confidence = event.get("confidence")
    if not tile_id or not observer_id or not isinstance(event_date, SimDate):
        return None
    if not fact and rumor is not True and not isinstance(rumor, str):
        return None
    if not isinstance(confidence, (int, float)):
        return None
    if _has_report(world, tile_id, event_date):
        return None
    if fact:
        content = f"Разведка {observer_id}: клетка {tile_id} осмотрена."
        facts: dict[str, Any] = {"tile": tile_id, "fact": True, "rumor": None}
        report_confidence = float(confidence)
        report_noise = 0.0
    else:
        rumor_text = (
            str(world.rng.news.choice(RUMORS))
            if world.rng is not None
            else RUMORS[0]
        )
        content = f"Разведка {observer_id}: для клетки {tile_id} ходит слух: {rumor_text}."
        facts = {"tile": tile_id, "fact": False, "rumor": rumor_text}
        report_confidence = 0.5
        report_noise = (
            world.rng.news.uniform(0.0, RUMOR_NOISE)
            if world.rng is not None
            else 0.0
        )
    report = make_report(
        world,
        SCOUT_SOURCE,
        "tile",
        tile_id,
        content,
        facts,
        event_date,
        0,
        report_confidence,
        distorted=False,
        noise=report_noise,
    )
    report.observer_id = observer_id
    return report


def report_scout_observations(
    world: World, events: Iterable[Mapping[str, Any]] | None = None
) -> list[Report]:
    """Превратить события месяца в доставляемые вести разведки.

    Функция не читает `Pack` или состояние клеток: физическое наблюдение уже
    произошло в `rng_world` Implementer'а, а этот слой только оформляет события.
    `known_tiles` не обновляется напрямую — клетка открывается только через
    доставленный `Report` в `engine.path.known_tiles`.
    """
    month_events = world.month_events if events is None else events
    reports: list[Report] = []
    for event in month_events:
        if event.get("kind") != "scout_observe":
            continue
        report = make_scout_report(world, event)
        if report is not None:
            reports.append(report)
    return reports
