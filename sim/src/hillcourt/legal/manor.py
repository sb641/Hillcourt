"""Манор в правилах: домен требует трудодней, надел кормит двор.

Домен и надел не смешивают стоки. Трудодни — не материя: они не идут в
`Ledger`, а перекладываются из двора в пул домена, поэтому «из воздуха» их
взять нельзя (`render_labor` не отдаёт больше, чем у двора есть).
"""

from __future__ import annotations

from ..ontology import Household, Tile
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


def is_in_manor_book(world: World, household: Household) -> bool:
    """Числится ли двор в книге существующего манора."""
    if household.manor_id is not None:
        return household.manor_id in world.manors
    return any(household.id in manor.household_ids for manor in world.manors.values())


def hire_out_allowed(world: World, household: Household) -> bool:
    """Разрешён ли двору подённый наём только из своей книги манора."""
    if not is_in_manor_book(world, household):
        return False
    status = world.catalogs.legal_statuses.get(household.legal_status_id)
    if status is None:
        return False
    return status.land_relation == "landless" or status.land_kind == "cotter_plot"
