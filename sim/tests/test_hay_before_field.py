"""Косьба идёт до поля: `phase_hay` между манором и стройкой/полем.

Косьба вызывается ранней фазой тика (`phase_hay` сразу после `phase_manor`,
до `phase_roadworks` и `phase_labor`), поэтому тягловый двор докашивает сено
из остатка труда до того, как жадное поле съест руки. Косьба единственная
(ADR 0050): поздний добор удалён (замер — за 60 мес шира накосил 0, а резерв
под него жал поле). Материя движется только переводом/рецептом (`gather_hay`),
дельта 0.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy import livestock
from hillcourt.engine.tick import PHASES, run_month
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
HILL_SALT = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SEED = 1729
HOUSEHOLD = "hh_09"  # свободный двор с доступным пастбищем


def _grant_pasture_access(world, household, tile_id: str) -> None:
    """Открыть двору пастбище по закону: клетка в `works_tiles` поселения.

    ADR 0068 (Legal): communal-доступ дают только `works_tiles` поселения и
    `Right.kind=common`; одного соседства больше не хватает. Фикстура теста
    оформляет право честно, данными сценария не трогая.
    """
    settlement = world.settlements.get(household.settlement_id or "")
    if settlement is not None and tile_id not in settlement.works_tiles:
        settlement.works_tiles.append(tile_id)


def _setup_hungry_draft(world):
    """Голодное тягло и полное пастбище: косьбе есть что взять."""
    household = world.households[HOUSEHOLD]
    stock = world.get_stock(household.stock_id)
    stock.amounts.clear()
    stock.amounts["ox_m"] = 1.0
    _grant_pasture_access(world, household, "t_05_03")
    pastures = livestock.hay_pastures(world, household)
    assert pastures, "У свободного двора нет доступного пастбища"
    for tile in pastures:
        world.get_stock(tile.standing_stock_id).amounts["hay"] = 20.0
    return household, stock


class TestHayPhaseOrder(unittest.TestCase):
    """Порядок фаз: косьба после манора, до стройки и поля."""

    def test_hay_between_manor_and_field(self) -> None:
        names = [phase.__name__ for phase in PHASES]
        self.assertLess(names.index("phase_manor"), names.index("phase_hay"))
        self.assertLess(names.index("phase_hay"), names.index("phase_roadworks"))
        self.assertLess(names.index("phase_hay"), names.index("phase_labor"))

    def test_phase_log_keeps_hay_before_labor(self) -> None:
        world = load_scenario(HILL_SALT, seed=SEED)
        _setup_hungry_draft(world)
        run_month(world)
        self.assertTrue(world.phase_log, "Тик не записал порядок фаз")
        names = world.phase_log[-1][1]
        self.assertLess(names.index("phase_hay"), names.index("phase_labor"))
        self.assertLess(names.index("phase_hay"), names.index("phase_roadworks"))


class TestHayMowedBeforeGreedyField(unittest.TestCase):
    """Жадное поле не объедает косьбу: сено накосили, руки полю тоже ушли."""

    def test_hay_mowed_and_field_worked_delta_zero(self) -> None:
        world = load_scenario(HILL_SALT, seed=SEED)
        household, stock = _setup_hungry_draft(world)
        labor_before = household.labor_days
        world.ledger.capture_initial(world.total_matter())
        run_month(world)
        self.assertGreater(
            stock.amounts.get("hay", 0.0), 0.0, "Косьба до поля не сработала"
        )
        mowed = [e for e in world.ledger.entries if e.reason == "gather_hay"]
        self.assertTrue(mowed, "Нет проводки заготовки сена")
        self.assertLess(
            household.labor_days,
            labor_before,
            "Поле не взяло труд: жадности не было, гонка не доказана",
        )
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )


if __name__ == "__main__":
    unittest.main()
