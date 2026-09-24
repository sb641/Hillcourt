"""Info-гейт племенной деревни (ADR 0059/0060): тексты по виду поселения.

Племя — `Settlement.kind=native_village` (данные, ADR 0060), не ручной маркер
`tribal` (тот остаётся подсказкой вида). Весть приходит теми же каналами
(`eye_from_hill`/`adjacent_daily`/`caravan`/`silence`), тем же форматом и теми
же числами; меняется только формулировка. `known_tiles` племенем не растёт.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.path import known_tiles
from hillcourt.info.sources import ADJACENT_DAILY, CARAVAN, EYE_FROM_HILL, SILENCE
from hillcourt.news.tribe import (
    is_native_tile,
    make_village_report,
    tribe_content,
)
from hillcourt.news.views import build_player_view
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_two_settlements.yml"
SEED = 1729
TILE = "t_02_07"
VILLAGE = "ash_village"
VILLAGE_NAME = "Деревня у ясеня"
OLD_CARAVAN = f"Дальняя деревня {VILLAGE_NAME}: зерна, сказывают, около 30."
CHANNEL = (CARAVAN, 2, 0.4, 0.4)


def _world():
    return load_scenario(SCENARIO, seed=SEED)


def _after(date, months: int):
    for _ in range(months):
        date = date.advance()
    return date


def _make_native(world):
    world.settlements[VILLAGE].kind = "native_village"


def _make_tribal(world):
    world.tiles[TILE].tribal = True


class TestNativeVillageNews(unittest.TestCase):
    """Племенная деревня говорит племенным текстом; обычная — прежним."""

    def _report(self, world):
        source, delay, confidence, noise = CHANNEL
        return make_village_report(
            world,
            source,
            TILE,
            world.clock.date,
            OLD_CARAVAN,
            {"grain_approx": 30.0},
            delay,
            confidence,
            noise=noise,
            distorted=False,
        )

    def test_native_village_carries_tribe_text_in_player_view(self) -> None:
        world = _world()
        _make_native(world)
        self.assertTrue(is_native_tile(world, TILE))

        report = self._report(world)

        self.assertEqual(report.source, CARAVAN)
        self.assertEqual(report.subject_kind, "tile")
        self.assertEqual(report.subject_id, TILE)
        self.assertIn("племен", report.content)
        self.assertIn(VILLAGE_NAME, report.content)
        self.assertIn("30", report.content)
        self.assertEqual(report.facts, {"grain_approx": 30.0})
        self.assertEqual(report.delivery_date, _after(world.clock.date, 2))
        self.assertEqual(report.confidence, 0.4)
        self.assertEqual(report.noise, 0.4)

        view = build_player_view(world, _after(world.clock.date, 2))
        entries = [e for e in view.entries if e.id == report.id]
        self.assertTrue(entries, "Племенная весть не дошла до знания игрока")
        self.assertIn("племен", entries[0].content)
        self.assertLess(abs(world.ledger.delta(world.total_matter())), 1e-6)

    def test_ordinary_village_keeps_previous_text(self) -> None:
        world = _world()
        self.assertEqual(world.settlements[VILLAGE].kind, "village")
        report = self._report(world)
        self.assertEqual(report.content, OLD_CARAVAN)
        self.assertEqual(report.facts, {"grain_approx": 30.0})

    def test_tribal_marker_alone_keeps_previous_text(self) -> None:
        """Маркер — подсказка вида, не источник истины (ADR 0060)."""
        world = _world()
        _make_tribal(world)
        report = self._report(world)
        self.assertEqual(report.content, OLD_CARAVAN)
        self.assertNotIn("племен", report.content)

    def test_native_kind_wins_over_missing_marker(self) -> None:
        """Вид поселения — источник истины; маркер для текста не нужен."""
        world = _world()
        _make_native(world)
        report = self._report(world)
        self.assertIn("племен", report.content)

    def test_every_tribe_channel_has_own_wording(self) -> None:
        world = _world()
        _make_native(world)
        facts = {"grain_approx": 30.0}
        eye = make_village_report(
            world,
            EYE_FROM_HILL,
            TILE,
            world.clock.date,
            "Видно с холма: зерна около 30.",
            facts,
            0,
            1.0,
        )
        adjacent = make_village_report(
            world,
            ADJACENT_DAILY,
            TILE,
            world.clock.date,
            "Сосед: зерна, сказывают, около 30.",
            facts,
            1,
            0.7,
            noise=0.3,
        )
        silence = make_village_report(
            world,
            SILENCE,
            TILE,
            world.clock.date,
            "Из дальней деревни вестей нет.",
            {},
            0,
            0.3,
        )
        self.assertIn("племен", eye.content)
        self.assertIn("племен", adjacent.content)
        self.assertIn("племен", silence.content)
        self.assertEqual(eye.delivery_date, world.clock.date)
        self.assertEqual(adjacent.delivery_date, _after(world.clock.date, 1))
        self.assertEqual(silence.facts, {})
        for report in (eye, adjacent, silence):
            self.assertEqual(report.facts, facts if report is not silence else {})

    def test_tribe_does_not_expand_known_tiles(self) -> None:
        """Племя само по себе клетку в знание не тащит (И-3)."""
        world = _world()
        _make_native(world)
        _make_tribal(world)
        self.assertNotIn(TILE, known_tiles(world))

        self._report(world)

        self.assertIn(TILE, known_tiles(world, _after(world.clock.date, 2)))

    def test_tribe_content_passes_other_channels_unchanged(self) -> None:
        """Не племенные рассказы (угроза, тропа) не переписываются."""
        world = _world()
        _make_native(world)
        text = "Гонец сказывает: на клетке 't_02_07' стая 'wolves' подросла."
        self.assertEqual(
            tribe_content(world, TILE, "messenger", text, {"population_approx": 4.0}),
            text,
        )
        self.assertEqual(
            tribe_content(world, "t_01_01", CARAVAN, text, {}), text
        )


if __name__ == "__main__":
    unittest.main()
