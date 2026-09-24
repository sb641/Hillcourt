"""Режимы земли и права действия двора на клетке (docs/07_legal.md).

Режим клетки — свойство земли (`Tile.regime_id`). Режим `foreign` не хранится
на клетке: он выводится, когда действие совершает двор, не держащий эту землю.
Функция `allowed_actions` отвечает, что двор МОЖЕТ на клетке с учётом своего
правового статуса.
"""

from __future__ import annotations

from ..ontology import Household, Tile
from ..world import World

FOREIGN_REGIME_ID = "foreign"

# Общинные клетки сбора: пустошь и заповедный лес. Это не держание, а право
# доступа: с них дворы собирают сообща, и ёмкость клетки у них общая.
COMMUNAL_COLLECTION_REGIMES = ("waste", "reserved_wood")


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
        return actions
    if not status.can_leave:
        actions.discard("leave")
    if not may_hold_land(status):
        actions.discard("plough")
        actions.discard("build_hut")
    if not status.ploughs:
        actions.discard("plough")
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
