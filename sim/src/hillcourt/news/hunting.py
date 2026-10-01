"""Вести о добыче дичи без передачи игроку истинного склада.

Канал охоты — `adjacent_daily`: он врёт по закону (шум 0.3), поэтому весть несёт
приближённое число, а не точное. Внутренние id контейнеров в `facts` не попадают
ни при каком вызывающем: id склада переводится в публичное слово о получателе.
"""

from __future__ import annotations

from typing import Any

from ..info.sources import ADJACENT_DAILY, CHANNELS
from ..ontology import Report, SimDate
from ..world import World
from .propagation import make_report

HUNT_CHANNEL = CHANNELS[ADJACENT_DAILY]

HOLDERS: tuple[str, ...] = ("household", "baron", "unknown")

_DESTINATION_TAIL: dict[str, str] = {
    "household": "добыча идёт во двор",
    "baron": "добыча идёт барону",
    "unknown": "добыча — неизвестно куда",
}


def destination_holder(world: World, destination_stock_id: str) -> str:
    """Кому добыча ушла по словам игрока, без внутренних id складов.

    Любой переданный id — известный, чужой или подсунутый — переводится в одно
    из слов `HOLDERS`: сток двора, сток барона или `unknown`. Неизвестный id даёт
    `unknown`, а не эхо самого id, поэтому в `facts` не может утечь ни один
    внутренний идентификатор контейнера.
    """
    for manor_id in sorted(world.manors):
        if getattr(world.manors[manor_id], "stock_id", None) == destination_stock_id:
            return "baron"
    for household_id in sorted(world.households):
        if world.households[household_id].stock_id == destination_stock_id:
            return "household"
    return "unknown"


def make_hunt_report(
    world: World,
    household_id: str,
    tile_id: str,
    good_id: str,
    amount: float,
    destination_stock_id: str,
    date: SimDate,
    recipe_id: str | None = None,
    observer_id: str | None = None,
) -> Report:
    """Создать отложенную весть об одном исполнении охотничьего рецепта."""
    true_amount = float(amount)
    reported = true_amount * (
        1.0 + world.rng.news.uniform(-HUNT_CHANNEL.noise, HUNT_CHANNEL.noise)
    )
    holder = destination_holder(world, destination_stock_id)
    facts: dict[str, Any] = {
        "event": "hunt",
        "tile": tile_id,
        "good": good_id,
        "amount_approx": round(reported, 1),
        "to": holder,
    }
    if recipe_id is not None:
        facts["recipe_id"] = recipe_id
    return make_report(
        world,
        ADJACENT_DAILY,
        "hunt",
        tile_id,
        f"Соседи говорят: добыта '{good_id}' на клетке '{tile_id}', "
        f"сказывают, около {reported:.1f}; {_DESTINATION_TAIL[holder]}.",
        facts,
        date,
        HUNT_CHANNEL.delay_months,
        HUNT_CHANNEL.confidence,
        distorted=abs(reported - true_amount) > 1e-9,
        noise=HUNT_CHANNEL.noise,
        observer_id=observer_id or household_id,
    )


def report_hunt(
    world: World,
    household_id: str,
    tile_id: str,
    good_id: str,
    amount: float,
    destination_stock_id: str,
    date: SimDate,
    recipe_id: str | None = None,
    observer_id: str | None = None,
) -> Report:
    """Оформить охоту как отдельное событие месяца."""
    return make_hunt_report(
        world,
        household_id,
        tile_id,
        good_id,
        amount,
        destination_stock_id,
        date,
        recipe_id=recipe_id,
        observer_id=observer_id,
    )
