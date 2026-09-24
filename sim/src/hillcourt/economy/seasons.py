"""Сезонная урожайность: выход с 1 оставшегося трудодня по месяцам (seasons.yml).

Календарь барщины — Legal (`calendar_v0.yml`). Здесь только урожайность:
сколько зерна даёт труд, который двор не отдал домену. Зимой поле почти не
даёт (едят амбар), пахота — задел на осень, жатва — пик.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ..world import World


@dataclass
class SeasonYield:
    """Урожайность месяца: множитель к рецепту жатвы на наделе и на домене."""

    month: int
    season: str
    plot_yield: float
    demesne_yield: float


def load_seasons(path: str | Path) -> dict[int, SeasonYield]:
    """Прочитать seasons.yml в словарь {номер месяца: SeasonYield}."""
    with Path(path).open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"seasons.yml '{path}': ожидался словарь верхнего уровня")
    unknown = sorted(set(data) - {"version", "seasons"})
    if unknown:
        raise ValueError(f"seasons.yml '{path}': неизвестные разделы {unknown}")
    out: dict[int, SeasonYield] = {}
    for record in data.get("seasons", []):
        month = int(record["month"])
        if month in out:
            raise ValueError(f"seasons.yml: месяц {month} повторяется")
        out[month] = SeasonYield(
            month=month,
            season=str(record.get("season", "")),
            plot_yield=float(record["plot_yield"]),
            demesne_yield=float(record["demesne_yield"]),
        )
    if set(out) != set(range(1, 13)):
        raise ValueError(f"seasons.yml: нужны месяцы 1..12, есть {sorted(out)}")
    return out


def plot_yield(world: World, month: int) -> float:
    """Множитель урожайности надела в месяце (1.0, если календаря нет)."""
    entry = getattr(world, "seasons", {}).get(month)
    return entry.plot_yield if entry is not None else 1.0


def demesne_yield(world: World, month: int) -> float:
    """Множитель урожайности домена в месяце (1.0, если календаря нет)."""
    entry = getattr(world, "seasons", {}).get(month)
    return entry.demesne_yield if entry is not None else 1.0
