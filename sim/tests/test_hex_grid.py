"""Гекс-сетка: аксиальные координаты и ровно шесть соседей (ADR 0071).

Топология, не экономика: правил здесь нет. Пины:
- у каждого гекса ровно 6 направлений соседства, каждый сосед — взаимный;
- координаты аксиальные `(q, r)`, обратное преобразование без потерь;
- соседство мира идёт через `engine/hexgrid.py` (единственный источник);
- граница карты — это «нет клетки», а не другой топологии;
- вода без переправы остаётся непроходимой (регрессия `find_path`).
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.hexgrid import (
    HEX_DIRECTIONS,
    axial_distance,
    axial_is_neighbor,
    axial_to_offset,
    hex_distance,
    hex_neighbors,
    is_neighbor,
    neighbor_ids,
    offset_to_axial,
    tile_id_at,
    tile_id_of,
)
from hillcourt.engine.path import find_path
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
CENTER = "t_03_03"


class TestHexTopology(unittest.TestCase):
    """Шесть направлений, взаимность соседства, обратимость координат."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO, seed=1729)

    def test_six_directions(self) -> None:
        self.assertEqual(len(HEX_DIRECTIONS), 6)
        self.assertEqual(
            sorted(HEX_DIRECTIONS),
            sorted(
                [
                    (1, 0),
                    (1, -1),
                    (0, -1),
                    (-1, 0),
                    (-1, 1),
                    (0, 1),
                ]
            ),
        )
        self.assertEqual(len(set(HEX_DIRECTIONS)), 6, "Направления повторяются")

    def test_every_interior_hex_has_six_neighbours(self) -> None:
        for tile in self.world.tiles.values():
            if not (1 <= tile.coord[0] <= 5 and 1 <= tile.coord[1] <= 4):
                continue
            found = neighbor_ids(self.world, tile)
            self.assertEqual(
                len(found), 6, f"{tile.id} (coord {tile.coord}): соседей {len(found)}"
            )
            self.assertEqual(len(set(found)), 6, f"{tile.id}: сосед повторяется")

    def test_neighbours_are_symmetric(self) -> None:
        for tile in self.world.tiles.values():
            for tile_id in neighbor_ids(self.world, tile):
                other = self.world.tiles[tile_id]
                self.assertIn(
                    tile.id, neighbor_ids(self.world, other), "Соседство не взаимно"
                )
                self.assertTrue(is_neighbor(tile, other))
                self.assertTrue(axial_is_neighbor(tile.coord, other.coord))

    def test_no_local_neighbour_math_in_world_modules(self) -> None:
        """Соседство — только хелпер: в коде мира нет своих переборов."""
        import hillcourt

        root = Path(hillcourt.__file__).resolve().parent
        offenders: list[str] = []
        for path in sorted(root.rglob("*.py")):
            if path.name in ("hexgrid.py", "__init__.py"):
                continue
            text = path.read_text(encoding="utf-8")
            for needle in (
                "(1, 0), (-1, 0), (0, 1), (0, -1)",
                "for dx, dy in ((1, 0)",
                "abs(tx - x) + abs(ty - y)",
            ):
                if needle in text:
                    offenders.append(f"{path.relative_to(root)}: {needle}")
        self.assertEqual(offenders, [], "Локальное соседство в коде: " + "; ".join(offenders))

    def test_coordinates_roundtrip(self) -> None:
        for col in range(0, 9):
            for row in range(0, 7):
                axial = offset_to_axial(col, row)
                self.assertEqual(axial_to_offset(*axial), (col, row))
        for tile in self.world.tiles.values():
            self.assertEqual(tile_id_of(tile), tile.id)
            self.assertEqual(
                tile_id_at(*axial_to_offset(*tile.coord)),
                tile.id,
                "id клетки разошёлся с координатой",
            )

    def test_hex_neighbors_are_unit_steps(self) -> None:
        tile = self.world.tiles[CENTER]
        coords = hex_neighbors(tile)
        self.assertEqual(len(coords), 6)
        for coord in coords:
            self.assertEqual(axial_distance(tile.coord, coord), 1)
            self.assertNotIn(coord, [tile.coord])

    def test_border_hex_has_fewer_cells_not_other_topology(self) -> None:
        corner = self.world.tiles["t_00_00"]
        self.assertLessEqual(len(neighbor_ids(self.world, corner)), 6)
        self.assertEqual(len(hex_neighbors(corner)), 6, "У края всё ещё 6 направлений")

    def test_distance_metric(self) -> None:
        a = self.world.tiles["t_00_00"]
        b = self.world.tiles["t_02_02"]
        self.assertEqual(hex_distance(a, a), 0)
        self.assertGreater(hex_distance(a, b), 0)
        self.assertEqual(hex_distance(a, b), axial_distance(a.coord, b.coord))

    def test_water_without_crossing_still_blocks(self) -> None:
        world = load_scenario(SCENARIO, seed=1729)
        target = world.tiles["t_06_03"]
        for tile_id in neighbor_ids(world, target):
            world.tiles[tile_id].terrain = "water"
            world.tiles[tile_id].ford = False
            world.tiles[tile_id].bridge = False
        self.assertIsNone(find_path(world, "t_01_01", "t_06_03", "caravan"))


if __name__ == "__main__":
    unittest.main()
