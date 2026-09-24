"""Проверка инварианта И-1: материя не берётся из ничего."""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.tick import run_month
from hillcourt.ledger import Ledger
from hillcourt.ontology import SimDate, Stock
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"


class TestMatterConservation(unittest.TestCase):
    """Сумма материи с учётом внешних потоков обязана сохраняться."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO)

    def test_delta_zero_after_each_month(self) -> None:
        for _ in range(12):
            run_month(self.world)
            delta = self.world.ledger.delta(self.world.total_matter())
            self.assertAlmostEqual(delta, 0.0, places=6)

    def test_no_negative_stock(self) -> None:
        for _ in range(12):
            run_month(self.world)
        for sid in sorted(self.world.stocks):
            for good, amount in self.world.stocks[sid].amounts.items():
                self.assertGreaterEqual(
                    amount, -1e-9, f"Отрицательный остаток {good} в {sid}"
                )

    def test_emit_refuses_undeclared_good(self) -> None:
        ledger = Ledger()
        date = SimDate(1, 1)
        pool = Stock("sink:processing", "sink", "processing", {})
        out = Stock("household:h", "household", "h", {})
        ledger.external_in(pool, "peat", 3.0, "seed", None, date)
        with self.assertRaises(ValueError):
            ledger.emit(pool, out, "gold", 3.0, "recipe", date, {"peat"})

    def test_ledger_has_external_in_and_transfer(self) -> None:
        for _ in range(12):
            run_month(self.world)
        entries = self.world.ledger.entries
        self.assertTrue(
            any(e.kind == "external_in" and e.rule_id for e in entries),
            "Нет внешнего прихода с rule_id",
        )
        self.assertTrue(any(e.kind == "transfer" for e in entries), "Нет переводов")


if __name__ == "__main__":
    unittest.main()
