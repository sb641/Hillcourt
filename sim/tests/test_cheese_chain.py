"""Сыроварение: молоко перерабатывается с потерей и затратой труда."""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.catalogs import load_catalogs
from hillcourt.ledger import Ledger
from hillcourt.ontology import SimDate, Stock

ROOT = Path(__file__).resolve().parents[2]
DATE = SimDate(1, 1, 1)


class TestCheeseChain(unittest.TestCase):
    """Проверка полного каталожного контракта сыроварения."""

    def setUp(self) -> None:
        self.recipe = load_catalogs(ROOT).recipes["make_cheese"]
        self.source = Stock("household:test", "household", "test")
        self.processing = Stock("pack:test", "pack", "test")
        self.sink = Stock("sink:waste", "sink", "waste")
        self.ledger = Ledger()
        self.ledger.capture_initial(self.source.total())

    def _produce(self) -> None:
        """Списать партию молока и выдать сыр из пула обработки."""
        self.ledger.transfer(
            self.source,
            self.processing,
            "milk",
            self.recipe.inputs["milk"],
            "cheese_input",
            DATE,
        )
        self.ledger.emit(
            self.processing,
            self.source,
            "cheese",
            self.recipe.outputs["cheese"],
            "make_cheese",
            DATE,
            set(self.recipe.outputs),
        )
        self.ledger.transfer(
            self.processing,
            self.sink,
            "milk",
            self.recipe.loss["milk"],
            "cheese_loss",
            DATE,
        )

    def test_milk_becomes_cheese_with_loss_and_labor(self) -> None:
        self.source.add("milk", self.recipe.inputs["milk"])
        self.ledger.capture_initial(self.source.total())
        self._produce()
        self.assertAlmostEqual(self.source.amounts.get("cheese", 0.0), 2.2, places=6)
        self.assertAlmostEqual(self.sink.amounts.get("milk", 0.0), 1.8, places=6)
        self.assertEqual(self.recipe.labor_days, 8.0)
        self.assertTrue(self.recipe.transform)

    def test_no_milk_means_no_cheese(self) -> None:
        with self.assertRaises(ValueError):
            self._produce()
        self.assertAlmostEqual(self.source.amounts.get("cheese", 0.0), 0.0, places=6)

    def test_mass_delta_is_zero(self) -> None:
        self.source.add("milk", self.recipe.inputs["milk"])
        self.ledger.capture_initial(self.source.total())
        self._produce()
        current = self.source.total() + self.processing.total() + self.sink.total()
        self.assertAlmostEqual(self.ledger.delta(current), 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
