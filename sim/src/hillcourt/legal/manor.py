"""Манор в правилах: домен требует трудодней, надел кормит двор.

Домен и надел не смешивают стоки. Трудодни — не материя: они не идут в
`Ledger`, а перекладываются из двора в пул домена, поэтому «из воздуха» их
взять нельзя (`render_labor` не отдаёт больше, чем у двора есть).
"""

from __future__ import annotations

from ..ontology import Tile
from ..world import World

DEMESNE_LABOR_KEY = "demesne_labor_days"


def land_requires_labor_days(world: World, tile: Tile) -> bool:
    """Домен: работа на клетке требует трудодней."""
    regime = world.catalogs.land_regimes.get(tile.regime_id)
    return bool(regime and regime.requires_labor_days)


def land_feeds_household(world: World, tile: Tile) -> bool:
    """Надел: клетка кормит двор (в отличие от домена)."""
    regime = world.catalogs.land_regimes.get(tile.regime_id)
    return bool(regime and regime.feeds_household)


def render_labor(world: World, household, days: float) -> float:
    """Списать трудодни двора в пул домена; вернуть, сколько удалось.

    Больше, чем у двора есть, списать нельзя: труд не берётся из воздуха.
    """
    if days <= 0:
        return 0.0
    rendered = min(max(0.0, household.labor_days), float(days))
    household.labor_days -= rendered
    world.bump(DEMESNE_LABOR_KEY, rendered)
    return rendered


def demesne_labor_pool(world: World) -> float:
    """Накопленные трудодни домена."""
    return float(world.stats.get(DEMESNE_LABOR_KEY, 0.0))
