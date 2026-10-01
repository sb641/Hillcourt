"""Гекс-сетка: аксиальные координаты `(q, r)` и шесть соседей (ADR 0071).

Только топология: соседство, преобразование координат и расстояние. Ни одного
правила мира здесь нет — выходы, труд, материя, окна, профили, экономика не
знают про этот модуль.

Модель: аксиальные координаты `(q, r)`, шесть направлений
(`HEX_DIRECTIONS`). Соседство — единственный источник правды о смежности: его
импортируют и движок (`engine/path.py`, `engine/seat.py`, `engine/tick.py`,
`engine/manor.py`), и чужие зоны (`economy/*`, `legal/regimes.py`) — локальных
переборов «своих» соседей в коде быть не должно (смешанная 4/6 топология
запрещена).

Данные сценария остаются в привычных координатах карты: `map.rows` —
прямоугольная легенда (`col`/`row`), `at:`/`works_tiles` — те же. Загрузчик
переводит их в аксиальные через `offset_to_axial` (odd-r раскладка), поэтому
`Tile.coord` — аксиальная пара `(q, r)`, а `tile.id` остаётся
`t_<col>_<row>` (данные, YAML и тесты не едут). `Settlement.coord` хранит
`[col, row]` карты, как раньше: из него клетка строится `tile_id_at`.

Правила соседства (топология, не баланс):
- `HEX_DIRECTIONS` — шесть единичных шагов аксиальной сетки;
- `axial_neighbors(tile)` — `(q+1,r) (q-1,r) (q,r+1) (q,r-1) (q+1,r-1) (q-1,r+1)`;
- `neighbor_ids(world, tile)` — существующие клетки мира по этому списку
  (шесть, не шесть минус границы: край — это «нет клетки»);
- `hex_distance(a, b)` — шаги по аксиальной сетке (замена манхэттена);
- `is_neighbor(a, b)` — смежность как отношение координат;
- `tile_ids_are_neighbor(origin, destination)` — **единственная** истина о
  смежности для клеток, названных id `t_<col>_<row>` (ADR 0203).

## Почему вопрос задан по id, а не по объектам

Клетки сравнивают между собой и приказы, и вести: `send_party` спрашивает
«соседняя ли клетка», имея на руках только два id из записи сценария. Значит
смежность обязана быть спрашиваема по id, иначе каждый вызывающий вынужден заново
разбирать формат строки.

`tile_ids_are_neighbor` — **единственное** определение такого вопроса во всём
`sim/`. Локальный разбор `origin.split("_")` с проверкой
`abs(ox-dx) + abs(oy-dy) == 1` — это смешанная 4/6 топология: она отвергает
настоящих соседей и принимает не-соседей, потому что манхэттен по координатам
карты (odd-r) не совпадает с аксиальной метрикой. Замер: `start_stand` — 12 из 46
настоящих соседств отвергнуто (26 %), `v0_shire` — 160 из 516 (31 %).
"""

from __future__ import annotations

from typing import Any

# Шесть направлений аксиальной сетки `(q, r)`.
HEX_DIRECTIONS: tuple[tuple[int, int], ...] = (
    (1, 0),
    (1, -1),
    (0, -1),
    (-1, 0),
    (-1, 1),
    (0, 1),
)


def offset_to_axial(col: int, row: int) -> tuple[int, int]:
    """Прямоугольные координаты карты → аксиальные `(q, r)` (odd-r раскладка).

    Нечётные строки сдвинуты вправо: `q = col - (row - (row & 1)) / 2`. Это
    единственное место, где `Tile.coord` становится аксиальной парой.
    """
    return int(col) - (int(row) - (int(row) & 1)) // 2, int(row)


def axial_to_offset(q: int, r: int) -> tuple[int, int]:
    """Аксиальные `(q, r)` → прямоугольные координаты карты (обратно)."""
    return int(q) + (int(r) - (int(r) & 1)) // 2, int(r)


def tile_id_at(col: int, row: int) -> str:
    """Id клетки по прямоугольным координатам карты (как в данных)."""
    return f"t_{int(col):02d}_{int(row):02d}"


def tile_id_of(tile: Any) -> str:
    """Id клетки из её аксиальных координар (через обратное преобразование)."""
    q, r = tile.coord
    col, row = axial_to_offset(q, r)
    return tile_id_at(col, row)


def hex_neighbors(tile: Any) -> list[tuple[int, int]]:
    """Шесть аксиальных координат-соседей клетки (без учёта границы мира)."""
    q, r = tile.coord
    return [(q + dq, r + dr) for dq, dr in HEX_DIRECTIONS]


def is_neighbor(a: Any, b: Any) -> bool:
    """Смежны ли две клетки по аксиальной сетке."""
    return axial_is_neighbor(
        (int(a.coord[0]), int(a.coord[1])),
        (int(b.coord[0]), int(b.coord[1])),
    )


def axial_is_neighbor(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """Смежны ли две аксиальные пары (без объектов клеток)."""
    dq = int(a[0]) - int(b[0])
    dr = int(a[1]) - int(b[1])
    return (dq, dr) in HEX_DIRECTIONS or (-dq, -dr) in HEX_DIRECTIONS


def hex_distance(a: Any, b: Any) -> int:
    """Число шагов по гексу между клетками (аксиальная метрика)."""
    return axial_distance(
        (int(a.coord[0]), int(a.coord[1])),
        (int(b.coord[0]), int(b.coord[1])),
    )


def axial_distance(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Число шагов по гексу между аксиальными парами: `(abs + abs + abs) // 2`."""
    dq = int(a[0]) - int(b[0])
    dr = int(a[1]) - int(b[1])
    return (abs(dq) + abs(dr) + abs(dq + dr)) // 2


def parse_tile_id(tile_id: Any) -> tuple[int, int] | None:
    """Аксиальные координаты клетки из id `t_<col>_<row>`; None — не наш формат.

    Единственное место, где строка `t_XX_YY` разбирается в координаты: и
    `tile_ids_are_neighbor`, и `engine/seat.py` берут разбор отсюда, поэтому
    «как выглядит id клетки» не знает никто, кроме этого модуля.
    """
    if not isinstance(tile_id, str):
        return None
    parts = tile_id.split("_")
    if len(parts) != 3 or parts[0] != "t":
        return None
    try:
        col, row = int(parts[1]), int(parts[2])
    except ValueError:
        return None
    return offset_to_axial(col, row)


def tile_ids_are_neighbor(origin: Any, destination: Any) -> bool:
    """Соседние ли клетки, названные id `t_<col>_<row>` (ADR 0203).

    Единственная истина о смежности по id: шесть аксиальных направлений, без
    манхэттена. Клетка не существует в мире — вопрос не решается в пользу
    соседства (`False`): несуществующая клетка соседом быть не может, иначе
    приказ отправит людей в пустоту.
    """
    a = parse_tile_id(origin)
    b = parse_tile_id(destination)
    if a is None or b is None:
        return False
    return axial_is_neighbor(a, b)


def neighbor_ids(world: Any, tile: Any) -> list[str]:
    """Id существующих соседей клетки в порядке `HEX_DIRECTIONS` (до шести).

    Чистое чтение мира: соседства по координатам, без правил. Ячейки, которых
    в мире нет (граница карты), просто не возвращаются. Поиск по id — O(1):
    id клетки выводится из её аксиальных координат.
    """
    found: list[str] = []
    for q, r in hex_neighbors(tile):
        tile_id = tile_id_at(*axial_to_offset(q, r))
        if tile_id in world.tiles:
            found.append(tile_id)
    return found
