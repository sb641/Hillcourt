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
- `is_neighbor(a, b)` — смежность как отношение координат.
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
