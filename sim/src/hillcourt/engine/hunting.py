"""Исполнение земельного действия охоты через каталог рецептов."""

from __future__ import annotations

from typing import Any

from ..catalogs import LAND_ACTION_RECIPES
from ..economy import labor
from ..legal.regimes import allowed_actions, hunt_destination_stock_id
from ..news.hunting import report_hunt
from ..ontology import Household, Recipe, SimDate, Tile
from ..world import World
from .hexgrid import neighbor_ids

EPSILON = 1e-9
HUNT_ACTION = "take_game"


def _recipe_ids(world: World) -> tuple[str, ...]:
    action = world.household_actions.get(HUNT_ACTION)
    if action is not None and action.recipes:
        return tuple(action.recipes)
    return LAND_ACTION_RECIPES[HUNT_ACTION]


def _recipe_good(recipe: Recipe) -> str | None:
    if len(recipe.outputs) != 1:
        return None
    return next(iter(recipe.outputs))


def _candidate_tiles(world: World, household: Household, tile: Tile | None) -> list[Tile]:
    if tile is not None:
        return [tile]
    ids: set[str] = {household.current_tile_id}
    settlement = world.settlements.get(household.settlement_id or "")
    if settlement is not None:
        ids.update(settlement.works_tiles)
    holders = {household.id}
    if household.settlement_id:
        holders.add(household.settlement_id)
    for right in world.rights.values():
        if right.holder_household_id in holders:
            ids.add(right.tile_id)
    for manor in world.manors.values():
        if household.manor_id == manor.id or household.id in manor.household_ids:
            ids.update(manor.tile_ids)
    current = world.tiles.get(household.current_tile_id)
    if current is not None:
        ids.update(neighbor_ids(world, current))
    return [world.tiles[tid] for tid in sorted(ids) if tid in world.tiles]


def _available_scale(
    world: World,
    household: Household,
    tile: Tile,
    recipe: Recipe,
    requested: float | None,
) -> tuple[float, float]:
    factor = labor._recipe_yield_factor(world, household, recipe, 1.0)
    scale = min(1.0, max(0.0, household.labor_days) / recipe.labor_days)
    source = world.get_stock(tile.standing_stock_id)
    for good, amount in recipe.draws_standing.items():
        if amount <= 0.0:
            continue
        scale = min(scale, source.amounts.get(good, 0.0) / (amount * factor))
    if requested is not None:
        scale = min(scale, max(0.0, requested))
    return max(0.0, scale), factor


def _execute_recipe(
    world: World,
    household: Household,
    tile: Tile,
    recipe: Recipe,
    date: SimDate,
    amount: float | None,
) -> dict[str, Any] | None:
    good = _recipe_good(recipe)
    if good is None or recipe.labor_days <= 0.0:
        return None
    if recipe.requires_terrain and tile.terrain not in recipe.requires_terrain:
        return None
    if "take_game" not in allowed_actions(world, household, tile):
        return None
    destination_id = hunt_destination_stock_id(world, household, tile, good)
    if destination_id is None:
        return None
    requested = None
    if amount is not None:
        output = float(recipe.outputs.get(good, 0.0))
        if output <= 0.0:
            return None
        factor = labor._recipe_yield_factor(world, household, recipe, 1.0)
        requested = float(amount) / (output * factor)
    scale, factor = _available_scale(world, household, tile, recipe, requested)
    if scale <= EPSILON:
        return None
    destination = world.get_stock(destination_id)
    labor.apply_recipe(
        world,
        household,
        tile,
        recipe,
        scale,
        date,
        output_stock=destination,
        yield_factor=1.0,
    )
    household.labor_days = max(0.0, household.labor_days - recipe.labor_days * scale)
    output_amount = float(recipe.outputs[good]) * scale * factor
    report = report_hunt(
        world,
        household.id,
        tile.id,
        good,
        output_amount,
        destination_id,
        date,
        recipe_id=recipe.id,
    )
    return {
        "recipe_id": recipe.id,
        "good": good,
        "amount": output_amount,
        "tile_id": tile.id,
        "destination_stock_id": destination_id,
        "report_id": report.id,
    }


def execute_hunt_action(
    world: World,
    household: Household,
    tile: Tile | str | None = None,
    good_id: str | None = None,
    amount: float | None = None,
    date: SimDate | None = None,
) -> list[dict[str, Any]]:
    """Исполнить `take_game` по доступным рецептам и вернуть результаты партий."""
    when = date or world.clock.date
    selected_tile: Tile | None
    if isinstance(tile, str):
        selected_tile = world.tiles.get(tile)
        if selected_tile is None:
            raise ValueError(f"Клетка '{tile}' не найдена")
    else:
        selected_tile = tile
    recipe_ids = _recipe_ids(world)
    if good_id is not None:
        recipe_ids = tuple(
            recipe_id
            for recipe_id in recipe_ids
            if (recipe := world.catalogs.recipes.get(recipe_id)) is not None
            and good_id in recipe.outputs
        )
        if not recipe_ids:
            raise ValueError(f"Для охоты нет рецепта товара '{good_id}'")
    results: list[dict[str, Any]] = []
    for recipe_id in recipe_ids:
        if household.labor_days <= EPSILON:
            break
        recipe = world.catalogs.recipes.get(recipe_id)
        if recipe is None:
            continue
        for target in _candidate_tiles(world, household, selected_tile):
            result = _execute_recipe(
                world,
                household,
                target,
                recipe,
                when,
                amount if good_id is not None else None,
            )
            if result is not None:
                results.append(result)
                break
    return results


def take_game_recipe(
    world: World,
    household: Household,
    tile: Tile | str,
    good_id: str,
    amount: float | None = None,
    date: SimDate | None = None,
) -> dict[str, Any] | None:
    """Исполнить один вид добычи и вернуть его проводку."""
    results = execute_hunt_action(
        world,
        household,
        tile=tile,
        good_id=good_id,
        amount=amount,
        date=date,
    )
    return results[0] if results else None


def run_hunt_actions(world: World, date: SimDate | None = None) -> list[dict[str, Any]]:
    """Исполнить действие охоты у всех дворов, выбравших его на месяц."""
    when = date or world.clock.date
    results: list[dict[str, Any]] = []
    for household_id in sorted(world.households):
        household = world.households[household_id]
        if household.left_at is not None:
            continue
        if HUNT_ACTION not in (household.main_action, household.minor_action):
            continue
        results.extend(execute_hunt_action(world, household, date=when))
    return results
