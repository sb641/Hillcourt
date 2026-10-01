"""Расчистка леса должна быть хоть где-то исполнима (ADR 0182).

Замер Economist'а: лесных клеток 1298, из них `reserved_wood` 1297, а право
`clear_forest` даёт **только** `demesne` — значит рецепт `uproot_stumps` не мог
исполниться ни разу ни в одном мире, и право было призраком.

Вариант A (решение хозяина): по одной лесной клетке на мир переводится в режим
`demesne` **сценарием**; `land_regimes.yml` не трогаем — `demesne` уже выдаёт и
`clear_forest`, и `take_game`, а `_manor_demesne_fields` отбирает
`regime_id == "demesne" and terrain == "field"`, поэтому лес в пашни домена не попадает.

Тест держит **достижимость права**, а не исполнение: рецепт дополнительно упирается
в очередь рецептов двора (`economy/labor.py`), и его неисполнение — отдельная находка,
а не повод сделать этот тест зелёным на пустоте.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.hexgrid import neighbor_ids
from hillcourt.legal.regimes import allowed_actions
from hillcourt.runner import _apply_script_entry
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = (
    "v0_hill_and_salt",
    "v0_large_village",
    "v0_native_village",
    "v0_barony_100",
    "v0_two_settlements",
    "v0_shire",
    "start_stand",
)
SEED = 1729
CLEARING = "uproot_stumps"
CLEARING_RIGHT = "clear_forest"
TAKE_GAME = "take_game"


def _world_after_script(name: str):
    world = load_scenario(ROOT / "design" / "scenarios" / f"{name}.yml", seed=SEED)
    for entry in world.script:
        _apply_script_entry(world, entry)
    return world


class TestClearingForestIsReachable(unittest.TestCase):
    """В каждом мире есть клетка, где двор вправе и может выкорчевать пни."""

    def test_every_world_grants_the_clearing_right_somewhere(self) -> None:
        for name in SCENARIOS:
            with self.subTest(world=name):
                world = _world_after_script(name)
                grants = [
                    tile.id
                    for tile in world.tiles.values()
                    if CLEARING_RIGHT in world.catalogs.land_regimes[
                        tile.regime_id
                    ].allowed_actions
                    and tile.terrain == "forest"
                ]
                self.assertTrue(
                    grants,
                    f"{name}: ни одной лесной клетки с правом {CLEARING_RIGHT} — "
                    "расчистка снова недостижима",
                )

    def test_the_right_reaches_a_yard_on_that_cell(self) -> None:
        """Право должно достаться хоть одному двору, а не висеть на клетке."""
        for name in SCENARIOS:
            with self.subTest(world=name):
                world = _world_after_script(name)
                reachable = 0
                for tile in world.tiles.values():
                    if CLEARING_RIGHT not in world.catalogs.land_regimes[
                        tile.regime_id
                    ].allowed_actions or tile.terrain != "forest":
                        continue
                    for household in world.households.values():
                        if CLEARING_RIGHT in allowed_actions(world, household, tile):
                            reachable += 1
                self.assertGreater(
                    reachable, 0, f"{name}: право есть на клетке, но не досталось двору"
                )

    def test_clearing_cell_keeps_game_and_stays_out_of_the_demesne_fields(self) -> None:
        """Смена режима ничего не отняла: охота на месте, лес — не в пашне домена."""
        from hillcourt.economy.manor import _manor_demesne_fields

        for name in SCENARIOS:
            with self.subTest(world=name):
                world = _world_after_script(name)
                for manor in world.manors.values():
                    fields = _manor_demesne_fields(world, manor)
                    self.assertTrue(
                        all(tile.terrain == "field" for tile in fields),
                        f"{name}: в пашни домена попал не-пашенный рельеф",
                    )
                clearing_cells = [
                    tile for tile in world.tiles.values()
                    if tile.terrain == "forest"
                    and CLEARING_RIGHT in world.catalogs.land_regimes[
                        tile.regime_id
                    ].allowed_actions
                ]
                self.assertTrue(clearing_cells, f"{name}: нет клетки под расчистку")
                for tile in clearing_cells:
                    self.assertIn(
                        TAKE_GAME,
                        world.catalogs.land_regimes[tile.regime_id].allowed_actions,
                        f"{name}/{tile.id}: смена режима отняла охоту",
                    )
                    neighbours = {
                        world.tiles[n].id
                        for n in neighbor_ids(world, tile)
                        if n in world.tiles
                    }
                    self.assertTrue(
                        neighbours, f"{name}/{tile.id}: изолированная клетка"
                    )


if __name__ == "__main__":
    unittest.main()
