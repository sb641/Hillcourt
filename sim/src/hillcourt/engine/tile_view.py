"""Наполнение тайла и контракт вида (снимок для иконки, не рендерер).

Клетка — не бесконечная коммуна: жилых дворов на клетке не больше
`TILE_MAX_HOUSEHOLDS` (ориентир 5). Вид клетки СЛЕДУЕТ из состояния, а не из
ручной краски: пустая — поле/лес/топь-железница/пустошь по terrain, один двор —
дом с огородом, несколько — деревня, seat тэна — усадьба держателя, seat корня —
зал на холме, поле господина — чистый деменский клин, переправа — брод/мост,
походные лагеря — возы в пути (`Pack` в `in_transit` к клетке). Городского тайла
нет: городов в v0 нет.

Снимок — чистая функция мира (материю и труд не трогает, И-1/И-5): из него
потом рисуют иконку. Игрок-стратег видит form в логе/дампе runner без
открытия RimWorld-слоя; знанием по И-3 снимок не является (знание — только
`Report`), это отладочный вид истины для карты.

Кап пашни (`plot_batch_cap_per_tile`, G3) живёт отдельно и здесь не меняется:
кап дворов — про место жительства (`Household.current_tile_id`), а не про
партии жатвы. Лесные промыслы (дрова/валка/сбор) — не тайловые: идут по соседним
клеткам рандомом (`forage_wild`/`cut_*`), вид леса не меняют (как и охота).

Земля баронства (видение, не закон v0): каталог форм держит вид будущих
сущностей — пристань, тропа/строеная дорога, перевалочная стоянка, место под
таверну, шахта, большая деревня, деревня племени, замок барона. Всё новое —
вид и данные без бонусов: ни yield, ни storage, ни механика тика не меняются
(см. `PASTURE_FORAGE_CAPACITY`, `LAND_MARKS`, ADR 0054). Маркеры — именованная
разметка будущей карты (сценарий видения ставит их полями клетки); без маркера
вид совпадает со старым побайтово.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .terrain import trail_level_for

# Вместимость жилых дворов на клетку (Legal: кап, явный отказ сверх).
# Эквивалент `tile.max_households`: поле на Tile не заводим, чтобы не менять
# онтологию ради константы; per-tile переопределение — через атрибут
# `Tile.max_households`, если он когда-нибудь появится (см. tile_max_households).
TILE_MAX_HOUSEHOLDS = 5

# Каталог форм (вид, не экономика; городов нет).
# id (english snake_case) -> русское имя + правило.
FORMS: dict[str, dict[str, str]] = {
    "open_field": {
        "name": "Открытое поле",
        "rule": "0 дворов, terrain hill/field/pasture",
    },
    "forest": {
        "name": "Лес",
        "rule": "0 дворов, terrain forest",
    },
    "waste": {
        "name": "Пустошь",
        "rule": "0 дворов, terrain heath/salt_flat/ruin/water",
    },
    "single_homestead": {
        "name": "Одинокий двор",
        "rule": "1 двор, без seat (дом с огородом в ландшафте)",
    },
    "tent_earth_homestead": {
        "name": "Палатка или землянка",
        "rule": "1 двор + маркер dwelling=tent_earth, без seat",
    },
    "timber_house": {
        "name": "Деревянный дом",
        "rule": "1 двор + маркер dwelling=house, без seat",
    },
    "multi_storey_house": {
        "name": "Дом в 2–3 этажа",
        "rule": "1 двор + маркер dwelling=multi_storey, без seat",
    },
    "village": {
        "name": "Деревня",
        "rule": "2+ дворов, без seat (деревенский тайл)",
    },
    "thegn_estate": {
        "name": "Усадьба держателя",
        "rule": "seat тэна: дом+ограда/конюшня, дворы вокруг как наполнение",
    },
    "hall_on_hill": {
        "name": "Зал на холме",
        "rule": "seat корня: зал/двор на холме",
    },
    # Поле господина: чистый деменский клин (режим demesne, terrain field,
    # 0 жилых дворов, не seat). С работниками вид уже не «чистое поле».
    "lord_field": {
        "name": "Поле господина",
        "rule": "режим demesne + terrain field + 0 дворов, не seat",
    },
    # Переправа: брод/мост (флаги Tile). Плоты и лодки (товары raft/boat,
    # рецепты craft_raft/craft_boat) — те же ворота через воду: вода без
    # брода/моста непроходима (engine/terrain.py).
    "crossing": {
        "name": "Переправа",
        "rule": "ford/bridge на клетке (брод/мост; пристань плотов и лодок)",
    },
    # Болотная железница: топь, где берут болотное железо (рецепт mine_iron,
    # правило роста grow_iron, товар iron). Шахты-штольни в v0 нет: железо
    # болотное, а не рудное.
    "bog_iron": {
        "name": "Болотная железница",
        "rule": "0 дворов, terrain marsh",
    },
    # Каменоломня — РЕЗЕРВ иконки: камня в экономике нет (нет good/recipe),
    # триггер появится только с сущностью у Economist. tile_form её не возвращает.
    "quarry": {
        "name": "Каменоломня",
        "rule": "РЕЗЕРВ: камня в экономике v0 нет, вид не выводится",
    },
    # Походные лагеря: воз в пути к клетке (Pack in_transit, destination —
    # клетка). Тип — по роду воза и силе отряда, пороги как правила вида
    # (аналог 0/1/много у дворов), детерминированы. Только in_transit:
    # дошедшие/погибшие следов-лагерей не оставляют.
    "foot_pot_camp": {
        "name": "Пеший лагерь с котелком",
        "rule": "in_transit party ≤2 человек (дозор, не лорд)",
    },
    "campfire_camp": {
        "name": "Лагерь с костром",
        "rule": "in_transit party ровно 3 человека (ночлег, не лорд)",
    },
    "tent_camp": {
        "name": "Лагерь с палатками",
        "rule": "in_transit household_move (переселенцы)",
    },
    "pavilion_camp": {
        "name": "Лагерь с шатрами",
        "rule": "in_transit party держателя/тэна (пресет holder/thegn)",
    },
    "wagon_camp": {
        "name": "Лагерь с повозками",
        "rule": "in_transit caravan (обоз с телегами)",
    },
    "fortified_camp": {
        "name": "Укреплённый лагерь с частоколом",
        "rule": "in_transit party ≥4 человек (большая рать окапывается)",
    },
    # Земля баронства (видение, вид без бонуса; города v0 нет — city в каталог
    # не входит, см. test_catalog_has_no_city):
    # пристань — именная переправа (маркер mooring + брод/мост);
    # тропа/грунтовка — данные `Tile.trail_wear`; строеная дорога — только
    # `tile.road`, приоритет выше и не смешивается с грунтом;
    # перевалочная стоянка — место ночлега на маршруте (маркер waystation);
    # место под таверну — двор у пути (маркер tavern, вид, не постройка);
    # железная шахта — точка добычи (маркер mine; добыча остаётся рецептом
    # mine_iron и приростом grow_iron, вид не добавляет);
    # большая деревня — стадия роста видом (10+ дворов, без seat);
    # деревня племени — форма из `Settlement.kind=native_village`; старый
    # маркер tribal остаётся подсказкой сценария, но не источником вида;
    # замок барона — стол видения (маркер castle, поверх любого места).
    "pier": {
        "name": "Пристань или порт",
        "rule": "маркер mooring + вода/переправа (порт — Settlement, не сущность)",
    },
    "trail": {
        "name": "Тропа",
        "rule": "trail_wear на пороге тропы + 0 дворов (хоженая земля)",
    },
    "dirt_road": {
        "name": "Грунтовая дорога",
        "rule": "trail_wear на пороге грунта + 0 дворов (топтаная земля)",
    },
    "built_road": {
        "name": "Строеная дорога",
        "rule": "tile.road + 0 дворов (завершённая работа; жилая — жильё)",
    },
    "waystation": {
        "name": "Перевалочная стоянка",
        "rule": "маркер waystation (ночлег на маршруте, вид, не бонус)",
    },
    "tavern_site": {
        "name": "Место под таверну",
        "rule": "маркер tavern (двор у пути, вид, не постройка)",
    },
    "iron_mine": {
        "name": "Железная шахта",
        "rule": "маркер mine (точка добычи; добыча — рецепт, не вид)",
    },
    "salt_settlement": {
        "name": "Соляное поселение",
        "rule": "Settlement.kind salt_village + живые дворы (вид без бонуса)",
    },
    "ruin_site": {
        "name": "Руина",
        "rule": "terrain ruin или Tile.ruin_id (вид места, не событие)",
    },
    "hermitage": {
        "name": "Отшельник или хижина",
        "rule": "маркер hermitage (вид без поселения-механики)",
    },
    "smoke_site": {
        "name": "Дым",
        "rule": "маркер smoke (наблюдаемый признак, не знание игрока)",
    },
    "lost_caravan": {
        "name": "Потерянный караван",
        "rule": "маркер lost_caravan (следствие, не Pack в пути)",
    },
    "city_quarter": {
        "name": "Городской квартал",
        "rule": "маркер urban + живые дворы (один Settlement, данные квартала)",
    },
    "large_village": {
        "name": "Большая деревня",
        "rule": "10+ дворов, без seat, Settlement.kind не native_village",
    },
    "tribal_village": {
        "name": "Деревня племени",
        "rule": "Settlement.kind native_village + 1+ дворов (чужое жильё)",
    },
    "baron_castle": {
        "name": "Замок барона",
        "rule": "маркер castle (стол видения, поверх любого места)",
    },
}

# Порог «большой рати» для частокола (правило вида, см. pavilion/campfire ниже).
FORTIFIED_MIN_MEMBERS = 4
# Пресеты, чей отряд идёт с шатрами (командный шатёр, не палатка).
PAVILION_PRESETS = ("holder", "thegn")

# Порог стадии роста видом: 10+ жилых дворов — большая деревня (без seat,
# без маркера tribal). Далеко от legacy-6 (6 остаётся village) и от тестовых
# 1/4/5. Города нет: city в каталог не входит (закон v0).
LARGE_VILLAGE_MIN_HOUSEHOLDS = 10

    # Маркеры земли баронства (видение): именованная разметка будущей карты.
    # На `Tile` полей под них нет (онтология не меняется ради вида): читаются
    # через `getattr(tile, mark, False)`, но источник `trail` — trail_wear, а
    # источник `tribal_village` — Settlement.kind. Мёртвый маркер `tribal`
    # вычищен (ADR 0064): вид и текст вести его не читают.

LAND_MARKS: tuple[str, ...] = (
    "trail",  # тропа: хоженая земля без стройки
    "mooring",  # пристань: именная переправа (с ford/bridge)
    "waystation",  # перевалочная стоянка на маршруте
    "tavern",  # место под таверну у пути
    "mine",  # шахта: точка добычи железа
    "castle",  # замок барона: стол видения
    "urban",  # городской квартал: вид без новой Settlement
    "hermitage",  # отшельник/хижина в wilds
    "smoke",  # дым/признак наблюдения
    "lost_caravan",  # след потерянного каравана
)

DWELLING_FORMS: dict[str, str] = {
    "tent_earth": "tent_earth_homestead",
    "house": "timber_house",
    "multi_storey": "multi_storey_house",
}

# Кормовая ёмкость пастбищ — ДАННЫЕ для сезонной модели Economist (числа only).
# Единица: условные единицы подножного корма в месяц пика сезона на клетку.
# База pasture = месячный прирост стоячего сена `grow_hay` (amount 2.0,
# `design/catalogs/spawn_rules.yml`); где стоячего корма нет в каталоге — 0.0
# (расширяет Economist своим ADR, не этот модуль). Тик это не читает:
# ни yield, ни storage, ни механика не меняются; формулу ест его модель.
PASTURE_FORAGE_CAPACITY: dict[str, float] = {
    "hill": 0.0,
    "field": 0.0,
    "pasture": 2.0,
    "forest": 0.0,
    "marsh": 0.0,
    "heath": 0.0,
    "salt_flat": 0.0,
    "ruin": 0.0,
    "water": 0.0,
}

# Пустая клетка по terrain (топь — железница, см. bog_iron; вода без переправы —
# пустошь: путник видит «не деревня»).
EMPTY_FORM_BY_TERRAIN: dict[str, str] = {
    "hill": "open_field",
    "field": "open_field",
    "pasture": "open_field",
    "forest": "forest",
    "marsh": "bog_iron",
    "heath": "waste",
    "salt_flat": "waste",
    "ruin": "waste",
    "water": "waste",
}


@dataclass
class TileView:
    """Снимок клетки для иконки стратегической карты (чистое чтение)."""

    tile_id: str
    terrain: str
    form: str
    household_count: int
    has_seat: bool
    has_thegn_hall: bool

    def as_dict(self) -> dict[str, Any]:
        """Словарь для лога/дампа (игрок видит form без RimWorld-слоя)."""
        return {
            "tile": self.tile_id,
            "terrain": self.terrain,
            "form": self.form,
            "household_count": self.household_count,
            "has_seat": self.has_seat,
            "has_thegn_hall": self.has_thegn_hall,
        }


@dataclass
class CoarseMapView:
    """Снимок coarse-карты: координаты, terrain и form каждой клетки."""

    width: int
    height: int
    cells: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        """Данные карты для картографа; порядок клеток детерминирован."""
        return {"width": self.width, "height": self.height, "cells": self.cells}


def households_on_tile(world: Any, tile_id: str) -> list:
    """Живые дворы, чьё место жительства — клетка (`left_at is None`, люди есть)."""
    out = []
    for hid in sorted(world.households):
        hh = world.households[hid]
        if hh.left_at is not None or not hh.member_ids:
            continue
        if hh.current_tile_id == tile_id:
            out.append(hh)
    return out


def household_count(world: Any, tile_id: str) -> int:
    """Число жилых дворов на клетке (для вида и капа)."""
    return len(households_on_tile(world, tile_id))


def tile_max_households(world: Any, tile_id: str) -> int:
    """Эквивалент `tile.max_households`: кап жилых дворов на клетку.

    По умолчанию `TILE_MAX_HOUSEHOLDS` (5). Если у `Tile` появится поле
    `max_households` — берётся оно (хук без смены онтологии сейчас).
    """
    tile = world.tiles.get(tile_id)
    if tile is None:
        raise ValueError(f"Нет клетки '{tile_id}'")
    override = getattr(tile, "max_households", None)
    if override is None:
        return int(TILE_MAX_HOUSEHOLDS)
    return int(override)


def can_settle(world: Any, tile_id: str) -> bool:
    """Влезет ли ещё один жилой двор на клетку (строгий кап)."""
    return household_count(world, tile_id) < tile_max_households(world, tile_id)


def has_mark(world: Any, tile_id: str, mark: str) -> bool:
    """Стоит ли на клетке маркер земли баронства (разметка видения).

    Маркер — именованное поле клетки из сценария видения (`LAND_MARKS`).
    На `Tile` таких полей нет: чтение через `getattr` с дефолтом `False`,
    поэтому без разметки вид совпадает со старым побайтово. Чистое чтение.
    """
    if mark not in LAND_MARKS:
        raise ValueError(f"Маркер '{mark}' вне {list(LAND_MARKS)}")
    tile = world.tiles.get(tile_id)
    if tile is None:
        raise ValueError(f"Нет клетки '{tile_id}'")
    return bool(getattr(tile, mark, False))


def dwelling_form(world: Any, tile_id: str) -> str | None:
    """Форма дома по сценарному значению маркера `dwelling`.

    Маркер хранится как данные клетки, не как поле мира; неизвестное значение —
    ошибка данных, а не молчаливый fallback. Для нескольких дворов уровень не
    выбирается: смешанная деревня остаётся формой поселения.
    """
    tile = world.tiles.get(tile_id)
    if tile is None:
        raise ValueError(f"Нет клетки '{tile_id}'")
    level = getattr(tile, "dwelling", None)
    if level is None:
        return None
    try:
        return DWELLING_FORMS[str(level)]
    except KeyError as exc:
        raise ValueError(
            f"Уровень дома '{level}' вне {sorted(DWELLING_FORMS)}"
        ) from exc


def pasture_forage_capacity(world: Any, tile_id: str) -> float:
    """Кормовая ёмкость клетки — данные для сезонной модели Economist.

    Число из `PASTURE_FORAGE_CAPACITY` по террейну клетки (пик сезона).
    Чистое чтение: стоки, труд, режимы не меняются; тик функцию не вызывает —
    формулу сезонности ест модель Economist своим ADR. Неизвестный террейн —
    `ValueError`, как в `engine/terrain.py`.
    """
    tile = world.tiles.get(tile_id)
    if tile is None:
        raise ValueError(f"Нет клетки '{tile_id}'")
    try:
        return float(PASTURE_FORAGE_CAPACITY[tile.terrain])
    except KeyError as exc:
        raise ValueError(
            f"Террейн '{tile.terrain}' вне {sorted(PASTURE_FORAGE_CAPACITY)}"
        ) from exc


def mine_sites(world: Any) -> list[str]:
    """Клетки-шахты (маркер mine): точки добычи железа как данные.

    Добыча остаётся рецептом и приростом (`mine_iron`, `grow_iron`); список —
    только вид/учёт для карты видения. Порядок детерминирован (по id).
    Сегодня пусто: разметки нет ни в одном сценарии v0.
    """
    return sorted(
        tid for tid in world.tiles if has_mark(world, tid, "mine")
    )


def is_native_settlement(world: Any, tile_id: str) -> bool:
    """Клетка входит в поселение племени (`Settlement.kind=native_village`)."""
    tile = world.tiles.get(tile_id)
    if tile is None or tile.settlement_id is None:
        return False
    settlement = world.settlements.get(tile.settlement_id)
    return settlement is not None and settlement.kind == "native_village"


def is_salt_settlement(world: Any, tile_id: str) -> bool:
    """Клетка входит в соляное поселение (`Settlement.kind=salt_village`)."""
    tile = world.tiles.get(tile_id)
    if tile is None or tile.settlement_id is None:
        return False
    settlement = world.settlements.get(tile.settlement_id)
    return bool(
        settlement is not None
        and settlement.kind == "salt_village"
        and household_count(world, tile_id) > 0
    )


def is_ruin_site(world: Any, tile_id: str) -> bool:
    """Руина как вид места: terrain `ruin` или существующий `Tile.ruin_id`."""
    tile = world.tiles.get(tile_id)
    if tile is None:
        return False
    return tile.terrain == "ruin" or bool(getattr(tile, "ruin_id", None))


def is_city_quarter(world: Any, tile_id: str) -> bool:
    """Городской квартал по сценарному маркеру и живым дворам."""
    return bool(
        has_mark(world, tile_id, "urban")
        and household_count(world, tile_id) > 0
    )


def is_root_seat(world: Any, tile_id: str) -> bool:
    """Seat корня: живое поселение `kind == hill_court` (зал на холме)."""
    tile = world.tiles.get(tile_id)
    if tile is None:
        return False
    if tile.settlement_id is None:
        return False
    settlement = world.settlements.get(tile.settlement_id)
    return bool(
        settlement is not None
        and settlement.kind == "hill_court"
        and household_count(world, tile_id) > 0
    )


def thegn_seat_tiles(world: Any) -> set[str]:
    """По одному seat на вложенный манор тэна (усадьба занимает клетку).

    Seat — первая доменная клетка книги в порядке `manor.tile_ids`
    (дом, где стоит зал), fallback — первая клетка книги. Остальные клетки
    фьефа — угодья: их вид считается по счётчику дворов, а не «ещё одна
    усадьба». Вид берётся по книге (`manor_of_tile`), а не по тому, где спит
    двор держателя (`current_tile_id` может остаться на холме).
    """
    seats: set[str] = set()
    for manor_id in sorted(world.manors):
        manor = world.manors[manor_id]
        if manor.parent_manor_id is None:
            continue
        if not manor.tile_ids:
            continue
        seat: str | None = None
        for tid in list(manor.tile_ids):
            tile = world.tiles.get(tid)
            if tile is not None and tile.regime_id == "demesne":
                seat = tid
                break
        if seat is None:
            seat = list(manor.tile_ids)[0]
        seats.add(seat)
    return seats


def is_thegn_seat(world: Any, tile_id: str) -> bool:
    """Клетка — seat тэна (усадьба держателя, не хутор)."""
    return tile_id in thegn_seat_tiles(world)


def inbound_packs(world: Any, tile_id: str) -> list:
    """Возы в пути к клетке (`in_transit`, destination — клетка).

    Порядок детерминирован: по роду воза (`kind`), затем по id. Первый в списке
    задаёт лагерь, если их несколько.
    """
    packs = [
        pack
        for pack in world.packs.values()
        if pack.status == "in_transit" and pack.destination_tile_id == tile_id
    ]
    return sorted(packs, key=lambda p: (p.kind, p.id))


def camp_form_for_pack(world: Any, pack) -> str | None:
    """Лагерь по роду воза (вид, не приказ и не бонус).

    `caravan` — повозки; `household_move` — палатки переселенцев; `party` —
    по владельцу и силе: шатёр лорда (`holder`/`thegn`), частокол большой рати
    (≥ `FORTIFIED_MIN_MEMBERS`), котелок дозора (≤2), костёр ночлега (ровно 3).
    Прочие роды возов лагерей не ставят (вид молчит, не выдумывает).
    """
    if pack.kind == "caravan":
        return "wagon_camp"
    if pack.kind == "household_move":
        return "tent_camp"
    if pack.kind != "party":
        return None
    owner = world.households.get(pack.owner_household_id or "")
    if owner is not None and owner.legal_status_id in PAVILION_PRESETS:
        return "pavilion_camp"
    size = len(pack.member_ids)
    if size >= FORTIFIED_MIN_MEMBERS:
        return "fortified_camp"
    if size <= 2:
        return "foot_pot_camp"
    return "campfire_camp"


def camp_form(world: Any, tile_id: str) -> str | None:
    """Походный лагерь на клетке или None (возов в пути к ней нет)."""
    packs = inbound_packs(world, tile_id)
    if not packs:
        return None
    return camp_form_for_pack(world, packs[0])


def is_crossing(world: Any, tile_id: str) -> bool:
    """Переправа: брод или мост на клетке (ворота через воду)."""
    tile = world.tiles.get(tile_id)
    return bool(tile is not None and (tile.ford or tile.bridge))


def is_pier(world: Any, tile_id: str) -> bool:
    """Пристань или порт: маркер `mooring` у воды/переправы.

    Это только форма. Вода без `ford`/`bridge` остаётся непроходимой в
    `entry_cost`; наличие `mooring` не меняет проходимость.
    """
    tile = world.tiles.get(tile_id)
    return bool(
        tile is not None
        and has_mark(world, tile_id, "mooring")
        and (tile.terrain == "water" or is_crossing(world, tile_id))
    )


def is_built_road(world: Any, tile_id: str) -> bool:
    """Строеная дорога: завершённая работа на пустой клетке.

    `tile.road` ставит только `start_work` через барщину (не краска).
    Жилая клетка показывает жильё, а не дорогу (счётчик важнее пути).
    """
    tile = world.tiles.get(tile_id)
    if tile is None:
        return False
    return bool(tile.road) and household_count(world, tile_id) <= 0


def is_dirt_road(world: Any, tile_id: str) -> bool:
    """Грунтовая дорога: износ тропы достиг порога грунта, клетка пуста.

    Вид только читает `Tile.trail_wear`; `tile.road` имеет отдельный приоритет
    и не смешивается с грунтом. Вода и заселённая клетка не показывают путь.
    """
    tile = world.tiles.get(tile_id)
    if tile is None or tile.terrain == "water" or tile.road:
        return False
    return bool(
        trail_level_for(tile.trail_wear) >= 2
        and household_count(world, tile_id) <= 0
    )


def is_trail(world: Any, tile_id: str) -> bool:
    """Тропа: износ ниже порога грунта, клетка пуста.

    Источник — только `Tile.trail_wear`; скрытый маркер сценария не читается.
    """
    tile = world.tiles.get(tile_id)
    if tile is None or tile.terrain == "water" or tile.road:
        return False
    return bool(
        trail_level_for(tile.trail_wear) == 1
        and household_count(world, tile_id) <= 0
    )


def is_lord_field(world: Any, tile_id: str) -> bool:
    """Чистое поле господина: деменский клин без жителей и без seat."""
    tile = world.tiles.get(tile_id)
    if tile is None:
        return False
    if tile.regime_id != "demesne" or tile.terrain != "field":
        return False
    if is_root_seat(world, tile_id) or is_thegn_seat(world, tile_id):
        return False
    return household_count(world, tile_id) <= 0


def tile_form(world: Any, tile_id: str) -> str:
    """Форма клетки из состояния (seat важнее счётчика, экономики не касается).

    Приоритет: замок барона (маркер) > зал корня > усадьба тэна > пристань/порт >
    переправа > походный лагерь > стоянка > дым > отшельник > потерянный караван >
    место под таверну > шахта > соляное поселение > руина > поле господина >
    строеная дорога > грунтовая дорога > тропа > Settlement.kind=native_village >
    счётчик дворов. Маркер `urban` превращает населённый гекс в городской
    квартал; это форма данных одного Settlement, не новая сущность.
    Для одного двора маркер dwelling уточняет жильё до формы дома; для 2+ дворов
    уровень не выбирается, остаётся деревня. Пусто — по terrain.
    Переполненные legacy-клетки (6–9 дворов) — тоже `village`: вид не штрафует
    и не премирует, только называет. `quarry` (резерв) здесь не возвращается:
    камня в экономике нет. Живое важнее размеченного: лагерь воза бьёт
    маркер стоянки, жильё бьёт дорогу/тропу.
    """
    tile = world.tiles.get(tile_id)
    if tile is None:
        raise ValueError(f"Нет клетки '{tile_id}'")
    if has_mark(world, tile_id, "castle"):
        return "baron_castle"
    if is_root_seat(world, tile_id):
        return "hall_on_hill"
    if is_thegn_seat(world, tile_id):
        return "thegn_estate"
    if is_pier(world, tile_id):
        return "pier"
    if is_crossing(world, tile_id):
        return "crossing"
    camp = camp_form(world, tile_id)
    if camp is not None:
        return camp
    if has_mark(world, tile_id, "waystation"):
        return "waystation"
    if has_mark(world, tile_id, "smoke"):
        return "smoke_site"
    if has_mark(world, tile_id, "hermitage"):
        return "hermitage"
    if has_mark(world, tile_id, "lost_caravan"):
        return "lost_caravan"
    if has_mark(world, tile_id, "tavern"):
        return "tavern_site"
    if has_mark(world, tile_id, "mine"):
        return "iron_mine"
    if is_salt_settlement(world, tile_id):
        return "salt_settlement"
    if is_ruin_site(world, tile_id):
        return "ruin_site"
    if is_lord_field(world, tile_id):
        return "lord_field"
    if is_built_road(world, tile_id):
        return "built_road"
    if is_dirt_road(world, tile_id):
        return "dirt_road"
    if is_trail(world, tile_id):
        return "trail"
    count = household_count(world, tile_id)
    if count <= 0:
        return EMPTY_FORM_BY_TERRAIN.get(tile.terrain, "waste")
    if is_native_settlement(world, tile_id):
        return "tribal_village"
    if is_city_quarter(world, tile_id):
        return "city_quarter"
    if count == 1:
        return dwelling_form(world, tile_id) or "single_homestead"
    if count >= LARGE_VILLAGE_MIN_HOUSEHOLDS:
        return "large_village"
    return "village"


def tile_view(world: Any, tile_id: str) -> TileView:
    """Снимок клетки: form, household_count, has_seat, has_thegn_hall, terrain.

    Чистое чтение: стоки, труд, режимы не меняются (form бонуса не даёт).
    """
    tile = world.tiles.get(tile_id)
    if tile is None:
        raise ValueError(f"Нет клетки '{tile_id}'")
    has_seat = bool(is_root_seat(world, tile_id))
    has_hall = bool(is_thegn_seat(world, tile_id))
    return TileView(
        tile_id=tile_id,
        terrain=tile.terrain,
        form=tile_form(world, tile_id),
        household_count=household_count(world, tile_id),
        has_seat=has_seat,
        has_thegn_hall=has_hall,
    )


def coarse_map_view(world: Any) -> CoarseMapView:
    """Собрать coarse-вид карты из данных клеток и форм, без новых вычислений.

    Размеры определяются координатами загруженных `Tile`, поэтому снимок
    одинаково работает для сетки 100×100 и для тестовых сценариев. Вода,
    лес, wilds и все маркеры отображаются только там, где они уже являются
    данными клетки; функция не раскрывает невидимые сведения и не меняет мир.
    """
    coordinates = [tile.coord for tile in world.tiles.values()]
    if not coordinates:
        return CoarseMapView(0, 0, [])
    width = max(coord[0] for coord in coordinates) + 1
    height = max(coord[1] for coord in coordinates) + 1
    cells = [
        tile_view(world, tile_id).as_dict()
        for tile_id in sorted(
            world.tiles,
            key=lambda tid: (world.tiles[tid].coord[1], world.tiles[tid].coord[0]),
        )
    ]
    return CoarseMapView(width, height, cells)


def dump_coarse_map_view(world: Any) -> dict[str, Any]:
    """Дамп coarse-карты в словаре без изменения состояния мира."""
    return coarse_map_view(world).as_dict()


def dump_tile_views(world: Any, tile_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Дамп клеток для лога/игрока: form виден без RimWorld-слоя.

    Порядок детерминирован (по id клетки). Материю/труд не трогает.
    """
    ids = sorted(tile_ids) if tile_ids is not None else sorted(world.tiles)
    return [tile_view(world, tid).as_dict() for tid in ids]


def format_tile_view(view: TileView | dict[str, Any]) -> str:
    """Одна строка дампа: `t_01_01 hill hall_on_hill дворов=2 seat=1 hall=0`."""
    if isinstance(view, dict):
        tile = view.get("tile", "?")
        terrain = view.get("terrain", "?")
        form = view.get("form", "?")
        count = view.get("household_count", "?")
        seat = int(bool(view.get("has_seat")))
        hall = int(bool(view.get("has_thegn_hall")))
    else:
        tile, terrain, form, count = view.tile_id, view.terrain, view.form, view.household_count
        seat, hall = int(view.has_seat), int(view.has_thegn_hall)
    return f"{tile} {terrain} {form} дворов={count} seat={seat} hall={hall}"


def _log(world: Any, action: str, **fields: Any) -> dict:
    """Запись в лог действий игрока (вид и отказы видны в логе)."""
    record: dict[str, Any] = {"date": str(world.clock.date), "month": world.clock.month, "action": action}
    record.update(fields)
    world.player_actions.append(record)
    return record


def settle_household(world: Any, household_id: str, tile_id: str) -> TileView:
    """Посадить жилой двор на клетку: сверх капа — явный отказ, не тихий перегруз.

    Успех двигает только место жительства (`Household.current_tile_id` + точки
    людей): стоки, труд, режимы, права, книги маноров не меняются, поэтому
    материя и урожай те же (form бонуса не даёт). Отказ — `PermissionError`
    с записью `settle_rejected reason=tile_full` в лог: шестой двор на клетку
    при капе 5 не садится.
    """
    household = world.households.get(household_id)
    if household is None:
        raise ValueError(f"Нет двора '{household_id}'")
    if tile_id not in world.tiles:
        raise ValueError(f"Нет клетки '{tile_id}'")
    if household.left_at is not None:
        raise PermissionError(f"Двор '{household_id}' ушёл и не садится")
    if household.current_tile_id == tile_id:
        return tile_view(world, tile_id)
    cap = tile_max_households(world, tile_id)
    count = household_count(world, tile_id)
    if count >= cap:
        _log(
            world,
            "settle_rejected",
            household=household_id,
            tile=tile_id,
            reason="tile_full",
            count=count,
            cap=cap,
        )
        raise PermissionError(
            f"Клетка '{tile_id}' полна: дворов {count} при капе {cap}"
        )
    household.current_tile_id = tile_id
    for pid in list(household.member_ids):
        person = world.persons.get(pid)
        if person is not None:
            person.location_tile_id = tile_id
    _log(world, "settle_household", household=household_id, tile=tile_id)
    return tile_view(world, tile_id)
