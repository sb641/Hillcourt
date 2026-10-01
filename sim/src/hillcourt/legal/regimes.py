"""Режимы земли и права действия двора на клетке (docs/07_legal.md).

Режим клетки — свойство земли (`Tile.regime_id`). Режим `foreign` не хранится
на клетке: он выводится, когда действие совершает двор, не держащий эту землю.
Функция `allowed_actions` отвечает, что двор МОЖЕТ на клетке с учётом своего
правового статуса.
"""

from __future__ import annotations

from ..ontology import Household, Stock, Tile
from ..world import World

FOREIGN_REGIME_ID = "foreign"

# Общинные клетки сбора: пустошь и заповедный лес. Это не держание, а право
# доступа: с них дворы собирают сообща, и ёмкость клетки у них общая.
COMMUNAL_COLLECTION_REGIMES = ("waste", "reserved_wood")
SMALL_GAME_GOODS = frozenset({"rabbit", "squirrel"})
LARGE_GAME_GOODS = frozenset({"deer", "boar", "wolf", "meat"})
GAME_GOODS = SMALL_GAME_GOODS | LARGE_GAME_GOODS


def holding_settlement_id(world: World, tile: Tile) -> str | None:
    """Поселение, держащее клетку: своя клетка или клетка из works_tiles."""
    if tile.settlement_id is not None:
        return tile.settlement_id
    for settlement in world.settlements.values():
        if tile.id in settlement.works_tiles:
            return settlement.id
    return None


def is_communal_collection_tile(tile: Tile) -> bool:
    """Общинная клетка сбора: режим `waste`/`reserved_wood` (пустошь, лес).

    Такая клетка не отдана двору в держание (`Right`/усадьба): дворы
    пользуются ею по праву доступа, а ёмкость клетки — общая. Режим `tenement`
    (общинное поле) сюда не входит: пашня/жатва держимой клетки делится как
    в G3, а общинные поля по-новому не шарится.
    """
    return tile.regime_id in COMMUNAL_COLLECTION_REGIMES


def has_access_to_communal_tile(world: World, household: Household, tile: Tile) -> bool:
    """Вправе ли двор собирать с общинной клетки по праву, а не по геометрии.

    Доступ дают только `works_tiles` поселения двора и `Right.kind=common`,
    выданный поселению или этому двору. Соседство не является экономическим
    правом. `common` даёт общий доступ, но не индивидуальное держание.
    """
    settlement = world.settlements.get(household.settlement_id or "")
    if settlement is not None and tile.id in settlement.works_tiles:
        return True
    for right in world.rights.values():
        if right.tile_id == tile.id and right.kind == "common" and (
            right.holder_household_id == household.id
            or right.holder_household_id == household.settlement_id
        ):
            return True
    return False


def _game_arguments(
    world: World,
    tile: Tile | str | None,
    good_id: str | None,
    game_id: str | None,
) -> tuple[Tile | None, str | None]:
    if game_id is not None:
        good_id = game_id
    if isinstance(tile, str):
        if tile in GAME_GOODS and good_id is None:
            good_id = tile
            tile = None
        else:
            resolved = world.tiles.get(tile)
            if resolved is None:
                return None, good_id
            tile = resolved
    return tile, good_id


def _has_common_right(
    world: World, household: Household, tile: Tile | None
) -> bool:
    if tile is not None:
        return has_access_to_communal_tile(world, household, tile)
    settlement = world.settlements.get(household.settlement_id or "")
    if settlement is not None and settlement.works_tiles:
        return True
    return any(
        right.kind == "common"
        and right.tile_id in world.tiles
        and (
            right.holder_household_id == household.id
            or right.holder_household_id == household.settlement_id
        )
        for right in world.rights.values()
    )


def _is_baron_or_retinue(world: World, household: Household) -> bool:
    status = preset_of(world, household)
    return bool(
        status is not None
        and (status.id == "holder" or status.can_be_taken_on_expedition)
    )


def can_take_game(
    world: World,
    household: Household,
    tile: Tile | str | None = None,
    good_id: str | None = None,
    *,
    game_id: str | None = None,
) -> bool:
    """Разрешена ли добыча конкретного товара дичи этому двору."""
    tile, good_id = _game_arguments(world, tile, good_id, game_id)
    if good_id is None:
        good_id = "rabbit"
    if good_id not in GAME_GOODS:
        return False
    if _is_baron_or_retinue(world, household):
        return True
    return good_id in SMALL_GAME_GOODS and _has_common_right(world, household, tile)


def hunt_destination_stock_id(
    world: World,
    household: Household,
    tile: Tile | str | None = None,
    good_id: str | None = None,
    *,
    game_id: str | None = None,
) -> str | None:
    """Вернуть сток, в который законная добыча идёт по размеру дичи."""
    tile, good_id = _game_arguments(world, tile, good_id, game_id)
    if not can_take_game(world, household, tile, good_id):
        return None
    if _is_baron_or_retinue(world, household):
        manor = world.manors.get(world.player_manor_id) if world.player_manor_id else None
        if manor is not None and manor.stock_id:
            return manor.stock_id
        court = world.settlements.get(world.player.court_settlement_id)
        return court.stores_stock_id if court is not None else None
    if good_id in SMALL_GAME_GOODS and _has_common_right(world, household, tile):
        return household.stock_id
    return None


def take_game(
    world: World,
    household: Household,
    tile: Tile | str,
    good_id: str,
    amount: float | None = None,
) -> Stock:
    """Перевести добычу дичи из клетки в сток, определённый правом."""
    if isinstance(tile, str):
        resolved = world.tiles.get(tile)
        if resolved is None:
            raise ValueError(f"Клетка '{tile}' не найдена")
        tile = resolved
    if not can_take_game(world, household, tile, good_id):
        raise PermissionError(
            f"Двор '{household.id}' не может добыть '{good_id}'"
        )
    has_common_right = _has_common_right(world, household, tile)
    regime = world.catalogs.land_regimes.get(tile.regime_id)
    if not has_common_right and (
        regime is None or "take_game" not in regime.allowed_actions
    ):
        raise PermissionError(
            f"На клетке '{tile.id}' действие take_game запрещено"
        )
    source = world.get_stock(tile.standing_stock_id)
    available = float(source.amounts.get(good_id, 0.0))
    take = available if amount is None else float(amount)
    if take <= 0.0:
        raise ValueError("Добыча должна быть положительной")
    if take > available + 1e-9:
        raise ValueError(
            f"На клетке '{tile.id}' только {available} '{good_id}'"
        )
    destination_id = hunt_destination_stock_id(world, household, tile, good_id)
    if destination_id is None:
        raise PermissionError(
            f"Для добычи '{good_id}' нет разрешённого стока назначения"
        )
    destination = world.get_stock(destination_id)
    world.ledger.transfer(
        source, destination, good_id, take, "hunt", world.clock.date
    )
    from ..news.hunting import report_hunt

    report = report_hunt(
        world,
        household.id,
        tile.id,
        good_id,
        take,
        destination_id,
        world.clock.date,
        observer_id=household.id,
    )
    report.facts.pop("destination_stock_id", None)
    return destination


def effective_regime_id(world: World, household: Household, tile: Tile) -> str:
    """Режим клетки глазами конкретного двора.

    Если двор не держит клетку, для него это `foreign`; иначе — режим самой
    клетки (`demesne`/`tenement`/`waste`/`reserved_wood`).
    """
    holder = holding_settlement_id(world, tile)
    if holder is not None and household.settlement_id != holder:
        return FOREIGN_REGIME_ID
    return tile.regime_id


def allowed_actions(world: World, household: Household, tile: Tile) -> set[str]:
    """Множество действий, разрешённых двору на клетке.

    Режим задаёт базовый набор, правовой статус его урезает: прикреплённый
    работник не уходит сам и не держит землю.
    """
    regime = world.catalogs.land_regimes.get(effective_regime_id(world, household, tile))
    if regime is None:
        return set()
    status = preset_of(world, household)
    actions = set(regime.allowed_actions)
    if status is None:
        if not can_take_game(world, household, tile):
            actions.discard("take_game")
        elif _has_common_right(world, household, tile):
            actions.add("take_game")
        return actions
    if not status.can_leave:
        actions.discard("leave")
    if not may_hold_land(status):
        actions.discard("plough")
    if not status.ploughs:
        actions.discard("plough")
    if not can_take_game(world, household, tile):
        actions.discard("take_game")
    elif _has_common_right(world, household, tile):
        actions.add("take_game")
    return actions


def preset_of(world: World, household: Household):
    """Пресет двора из каталога по ярлыку `legal_status_id` (без классов в коде)."""
    return world.catalogs.legal_statuses.get(household.legal_status_id)


def may_hold_land(status) -> bool:
    """Держит ли пресет землю: ось `land_relation`, а не отдельный флаг."""
    return status.land_relation != "landless"


def owes_rent(status) -> bool:
    """Платит ли пресет натурально/пенсом: бандл не из пустых."""
    return status.obligation_bundle not in ("holder_none", "free_landless_none", "slave_ration")


def can_leave(world: World, household: Household) -> bool:
    """Может ли двор уйти сам, без приказа (по своему пресету).

    Держание держит человека: фьеф держит тэна, корень держит холм. Пока
    существует ЛЮБОЙ манор (вложенный или корневой), в чьём дворе числится
    держатель, двор не уходит сам. Хочешь уйти — сперва `revoke_thegn` или
    отказ от держания. Свободный двор вне книги уходит по своему пресету.
    """
    for manor in world.manors.values():
        if manor.holder_person_id in household.member_ids:
            return False
    status = preset_of(world, household)
    return bool(status and status.can_leave)


def can_be_sent(world: World, household: Household) -> bool:
    """Можно ли взять двор в поход как повинность (по своему пресету)."""
    status = preset_of(world, household)
    return bool(status and status.can_be_taken_on_expedition)


def vassalage_allowed() -> bool:
    """Вассал-вассала в v0 нет: соседний держатель — равный, не вассал."""
    return False
