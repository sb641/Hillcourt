"""Местность и профили хода (задел большой карты, боя нет).

Закрытый список `TERRAINS` — те же 9 террейнов онтологии (`docs/03_ontology.md`),
не 40 биомов. Неизвестный террейн — `ValueError`, а не молчаливое допущение.

Профили хода (`MOVEMENT_PROFILES`) — строки-идентификаторы, не классы юнитов
(И-10: нет `Knight`/`Explorer`). Цена — дни входа в клетку:
  `caravan` — обоз с телегой: чаща и топь дороги, идёт дорогой/полем;
  `foot` — пешая свита без обоза: ровные цены;
  `hunters` — охотники/малая пешая разведка: чаща дёшева;
   `mounted` — колонна с конями и древками: чаща и топь дороже пеших;
   `arms` — воины в броне: пешие цены ×1.25 (строка, не класс, И-10);
   `rider` — всадник-гонец: поле дёшево (0.6), чаща/топь дорого (4.0/6.0),
     брод дорого (4.0);
   `water_raft` — плот: вода дёшева (1.0), суша дорога (по воде, не по чаще);
   `water_boat` — малая лодка: вода ещё дешевле (0.8), суша дорога.

Время пути измеряется часами на гекс (`FIELD_HOURS`, ADR 0071): `entry_hours`
— та же цена входа, что `entry_cost` в днях, умноженная на `hours_scale`.
Пропорции террейнов прежние, порядок маршрутов не меняется.
Сухопутные профили (`caravan`/`foot`/`hunters`/`mounted`): вода без брода/моста
непроходима (`IMPASSABLE`). Речные (`water_*`): вода проходима и без брода —
судно идёт рекой; без судна большой груз не едет (проверяет `engine/river.py`,
а не цена клетки). Течения нет: вверх/вниз одинаково (первая итерация).
"""

from __future__ import annotations

TERRAINS: tuple[str, ...] = (
    "hill",
    "field",
    "pasture",
    "forest",
    "marsh",
    "heath",
    "salt_flat",
    "ruin",
    "water",
)

IMPASSABLE: float = float("inf")

DAYS_PER_MONTH: float = 30.0
HOURS_PER_DAY: float = 24.0

# Время пути в часах на гекс поля (ADR 0071 п.3, калибровка 0070): нога 0.65,
# конь 0.40, обоз 0.85, `arms` = нога ×1.25. Водные профили — свой якорь.
# Пропорции террейнов НЫНЕШНИЕ: часы = дни × (якорь / дни_поля_профиля), то
# же отношение, поэтому порядок маршрутов (Дейкстра) не меняется и фураж по дням
# остаётся честным (его перевод на часы — отдельная задача Economist).
FIELD_HOURS: dict[str, float] = {
    "caravan": 0.85,
    "foot": 0.65,
    "hunters": 0.65,
    "mounted": 0.40,
    "arms": 0.8125,
    "rider": 0.40,
    "water_raft": 0.85,
    "water_boat": 0.65,
}

# Тропы от ходьбы (этап троп, ведёт `engine/trails.py`): износ пути по клетке.
# Пороги уровня: 3.0 — тропа, 12.0 — грунтовка; строеная дорога — только
# `tile.road` через стройку (`engine/roadworks.py`), автоматом никогда.
# Скидка входа по уровню (слабее строеной дороги): тропа ×0.9, грунтовка ×0.8.
TRAIL_WEAR_TRAIL: float = 3.0
TRAIL_WEAR_DIRT: float = 12.0
TRAIL_COST_MULT: dict[int, float] = {0: 1.0, 1: 0.9, 2: 0.8}


def trail_level_for(wear: float) -> int:
    """Уровень пути по износу: 0 — целина, 1 — тропа, 2 — грунтовка."""
    if wear >= TRAIL_WEAR_DIRT:
        return 2
    if wear >= TRAIL_WEAR_TRAIL:
        return 1
    return 0


def _profile(
    profile_id: str,
    name: str,
    costs: dict[str, float],
    road_cost: float,
    crossing_cost: float,
) -> dict:
    """Собрать запись профиля: цена каждого террейна обязана быть задана."""
    missing = [terrain for terrain in TERRAINS if terrain not in costs]
    if missing:
        raise ValueError(f"Профиль '{profile_id}': нет цен {missing}")
    return {
        "id": profile_id,
        "name": name,
        "costs": dict(costs),
        "road_cost": road_cost,
        "crossing_cost": crossing_cost,
    }


MOVEMENT_PROFILES: dict[str, dict] = {
    "caravan": _profile(
        "caravan",
        "Обоз с телегой",
        {
            "hill": 3.0,
            "field": 1.0,
            "pasture": 1.0,
            "forest": 8.0,
            "marsh": 6.0,
            "heath": 1.5,
            "salt_flat": 2.0,
            "ruin": 2.5,
            "water": IMPASSABLE,
        },
        road_cost=0.8,
        crossing_cost=4.0,
    ),
    "foot": _profile(
        "foot",
        "Пешая свита",
        {
            "hill": 2.0,
            "field": 1.0,
            "pasture": 1.0,
            "forest": 3.0,
            "marsh": 4.0,
            "heath": 1.2,
            "salt_flat": 1.5,
            "ruin": 1.5,
            "water": IMPASSABLE,
        },
        road_cost=0.8,
        crossing_cost=2.5,
    ),
    "hunters": _profile(
        "hunters",
        "Охотники и разведка",
        {
            "hill": 1.5,
            "field": 1.0,
            "pasture": 1.0,
            "forest": 1.5,
            "marsh": 3.0,
            "heath": 1.0,
            "salt_flat": 1.5,
            "ruin": 1.5,
            "water": IMPASSABLE,
        },
        road_cost=0.9,
        crossing_cost=2.0,
    ),
    "mounted": _profile(
        "mounted",
        "Конная колонна",
        {
            "hill": 2.5,
            "field": 0.8,
            "pasture": 0.8,
            "forest": 6.0,
            "marsh": 8.0,
            "heath": 1.0,
            "salt_flat": 2.0,
            "ruin": 2.0,
            "water": IMPASSABLE,
        },
        road_cost=0.6,
        crossing_cost=5.0,
    ),
    "arms": _profile(
        "arms",
        "Воины в броне",
        {
            "hill": 2.5,
            "field": 1.25,
            "pasture": 1.25,
            "forest": 3.75,
            "marsh": 5.0,
            "heath": 1.5,
            "salt_flat": 1.875,
            "ruin": 1.875,
            "water": IMPASSABLE,
        },
        road_cost=1.0,
        crossing_cost=3.125,
    ),
    "rider": _profile(
        "rider",
        "Всадник-гонец",
        {
            "hill": 2.0,
            "field": 0.6,
            "pasture": 0.6,
            "forest": 4.0,
            "marsh": 6.0,
            "heath": 1.0,
            "salt_flat": 1.2,
            "ruin": 1.5,
            "water": IMPASSABLE,
        },
        road_cost=0.5,
        crossing_cost=4.0,
    ),
    "water_raft": _profile(
        "water_raft",
        "Плот по реке",
        {
            "hill": 6.0,
            "field": 5.0,
            "pasture": 5.0,
            "forest": 10.0,
            "marsh": 8.0,
            "heath": 5.0,
            "salt_flat": 6.0,
            "ruin": 6.0,
            "water": 1.0,
        },
        road_cost=4.0,
        crossing_cost=1.0,
    ),
    "water_boat": _profile(
        "water_boat",
        "Малая лодка по реке",
        {
            "hill": 6.0,
            "field": 5.0,
            "pasture": 5.0,
            "forest": 10.0,
            "marsh": 8.0,
            "heath": 5.0,
            "salt_flat": 6.0,
            "ruin": 6.0,
            "water": 0.8,
        },
        road_cost=3.0,
        crossing_cost=0.8,
    ),
}


def profile_ids() -> list[str]:
    """Идентификаторы профилей хода (для приказов и проверок)."""
    return sorted(MOVEMENT_PROFILES)


def get_profile(profile_id: str) -> dict:
    """Запись профиля по id; неизвестный профиль — `ValueError`."""
    try:
        return MOVEMENT_PROFILES[profile_id]
    except KeyError as exc:
        raise ValueError(
            f"Неизвестный профиль хода '{profile_id}': {profile_ids()}"
        ) from exc


def entry_cost(
    profile_id: str,
    terrain: str,
    road: bool,
    ford: bool,
    bridge: bool,
    trail: int = 0,
) -> float:
    """Дни входа в клетку для профиля.

    Сухопутные профили: дорога дешевит вход (`min` с ценой террейна, хуже не
    бывает). Вода проходима только через брод/мост (`crossing_cost`), иначе
    `IMPASSABLE` — обоз с телегой реку в чаще не перепрыгивает.
    Речные (`water_raft`/`water_boat`): вода проходима всегда (судно идёт
    рекой, брод не нужен); суша дорога по таблице профиля. Течения нет.
    Тропа/грунтовка (`trail` 1/2) дешевят вход множителем `TRAIL_COST_MULT`
    (слабее строеной); вода и строеная дорога вне системы троп.
    Неизвестный террейн — `ValueError` (список закрыт).
    """
    if terrain not in TERRAINS:
        raise ValueError(f"Террейн '{terrain}' вне закрытого списка {list(TERRAINS)}")
    profile = get_profile(profile_id)
    if terrain == "water":
        if profile_id.startswith("water_"):
            cost = float(profile["costs"]["water"])
        elif ford or bridge:
            cost = float(profile["crossing_cost"])
        else:
            return IMPASSABLE
    else:
        cost = float(profile["costs"][terrain])
    if trail > 0 and not road:
        cost *= float(TRAIL_COST_MULT.get(trail, 1.0))
    if road:
        cost = min(cost, float(profile["road_cost"]))
    return cost


def hours_scale(profile_id: str) -> float:
    """Часы на день пути профиля: якорь поля / день поля профиля (ADR 0071).

    Часы вводит калибровка владельца, а пропорции террейнов остаются прежними:
    `entry_hours = entry_cost × hours_scale`. Неизвестный профиль — `ValueError`.
    """
    profile = get_profile(profile_id)
    try:
        anchor = float(FIELD_HOURS[profile_id])
    except KeyError as exc:  # pragma: no cover — профили и якоря заведены вместе
        raise ValueError(
            f"Профиль '{profile_id}' без якоря часов: {sorted(FIELD_HOURS)}"
        ) from exc
    return anchor / float(profile["costs"]["field"])


def entry_hours(
    profile_id: str,
    terrain: str,
    road: bool,
    ford: bool,
    bridge: bool,
    trail: int = 0,
) -> float:
    """Часы входа в клетку для профиля (ADR 0071): та же цена, что в днях.

    Часы = `entry_cost(...)` × `hours_scale(profile)`; вода без переправы и для
    сухопутных, и для водных остаётся непроходимостью. Часы плавающие: сутки
    ровно 24 часа, «сутки» как единица пути не используются.
    """
    days = entry_cost(profile_id, terrain, road, ford, bridge, trail)
    if days >= IMPASSABLE:
        return IMPASSABLE
    return days * hours_scale(profile_id)


def is_water_profile(profile_id: str) -> bool:
    """Речной ли профиль (`water_raft`/`water_boat`): идёт по воде без брода."""
    get_profile(profile_id)
    return profile_id.startswith("water_")


def is_land_profile(profile_id: str) -> bool:
    """Сухопутный ли профиль: вода только через брод/мост."""
    return not is_water_profile(profile_id)
