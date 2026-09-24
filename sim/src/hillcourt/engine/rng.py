"""Именованные потоки случайности для детерминизма (И-6)."""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass
class RngStreams:
    """Четыре независимых потока: экономика, мир, известия, опасность."""

    economy: random.Random
    world: random.Random
    news: random.Random
    hazard: random.Random

    @classmethod
    def from_seed(cls, seed: int) -> "RngStreams":
        """Собрать потоки из одного seed по именам."""
        return cls(
            economy=random.Random(f"{seed}:economy"),
            world=random.Random(f"{seed}:world"),
            news=random.Random(f"{seed}:news"),
            hazard=random.Random(f"{seed}:hazard"),
        )
