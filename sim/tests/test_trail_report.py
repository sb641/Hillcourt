"""Info-гейт этапа троп (цикл 27): глаз — весть, даль — молчание.

Новая тропа в радиусе глаза — `eye_from_hill` с задержкой 0; вдали — молчание
до первого прохода своего воза (проход освещают существующие отчёты обоза
и память дорог); тропа не расширяет `known_tiles` (знание без вести — запрет,
И-3). Механика метит новорождённую тропу штампами `trail_born_<tile>` и
`dirt_born_<tile>` в `world.stats`; весть рождает
`news/trails.py::report_new_trails`.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.path import known_tiles
from hillcourt.engine.seat import eye_tiles
from hillcourt.engine.terrain import TRAIL_WEAR_DIRT, TRAIL_WEAR_TRAIL
from hillcourt.engine.tick import phase_inform
from hillcourt.engine.trails import tread_tile
from hillcourt.info.sources import ALL_SOURCES, EYE_FROM_HILL
from hillcourt.news.trails import make_trail_report, report_new_trails
from hillcourt.news.views import build_player_view
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SEED = 1729


def _eye_and_far(world):
    """Глазная и дальняя клетки мира (чтение места, не правка)."""
    eye = sorted(set(eye_tiles(world)))
    assert eye, "У корня нет глаза"
    far = next(t for t in sorted(world.tiles) if t not in set(eye))
    return eye[0], far


def _stamp(world, tile_id: str, wear: float = TRAIL_WEAR_TRAIL) -> None:
    """Родить путь настоящим вызовом механики."""
    world.tiles[tile_id].trail_wear = 0.0
    level = tread_tile(world, tile_id, wear)
    expected = 2 if wear >= TRAIL_WEAR_DIRT else 1
    if level != expected:
        raise AssertionError("Механика не родила ожидаемый уровень пути")


class TestTrailReport(unittest.TestCase):
    """Весть о тропе: глаз видит, даль молчит, знание без вести запрещено."""

    def test_eye_trail_reports_news(self) -> None:
        world = load_scenario(SCENARIO, seed=SEED)
        eye, _ = _eye_and_far(world)
        _stamp(world, eye)

        phase_inform(world)

        view = build_player_view(world, world.clock.date)
        entries = [
            entry
            for entry in view.entries
            if entry.source == EYE_FROM_HILL
            and entry.subject_id == eye
            and entry.facts.get("trail")
        ]
        self.assertEqual(len(entries), 1, "Тропа в глазу осталась без вести")
        report = entries[0]
        self.assertIn(report.source, ALL_SOURCES)
        self.assertEqual(report.subject_kind, "tile")
        self.assertEqual(report.subject_id, eye)
        self.assertEqual(report.delivery_date, world.clock.date)
        self.assertEqual(report.confidence, 1.0)
        self.assertEqual(report.facts.get("tile"), eye)
        self.assertIn(eye, report.content)

        self.assertLess(abs(world.ledger.delta(world.total_matter())), 1e-6)

    def test_dirt_road_uses_engine_marker(self) -> None:
        world = load_scenario(SCENARIO, seed=SEED)
        eye, _ = _eye_and_far(world)
        _stamp(world, eye, TRAIL_WEAR_DIRT)

        produced = report_new_trails(world)

        self.assertEqual(len(produced), 1, "Грунтовка в глазу осталась без вести")
        self.assertEqual(produced[0].subject_id, eye)
        entry_ids = [
            entry.id for entry in build_player_view(world, world.clock.date).entries
        ]
        self.assertIn(produced[0].id, entry_ids)

    def test_far_trail_stays_silent(self) -> None:
        """Дальняя тропа — молчание: ни вести, ни клетки в `known_tiles`."""
        world = load_scenario(SCENARIO, seed=SEED)
        _, far = _eye_and_far(world)
        _stamp(world, far)

        self.assertEqual(report_new_trails(world), [])
        self.assertFalse(
            [r for r in world.reports if r.subject_id == far],
            "Дальняя тропа породила весть без прохода воза",
        )
        self.assertNotIn(
            far, known_tiles(world), "Тропа расширила known_tiles без вести"
        )
        self.assertLess(abs(world.ledger.delta(world.total_matter())), 1e-6)

    def test_trail_does_not_expand_known_and_dedups(self) -> None:
        world = load_scenario(SCENARIO, seed=SEED)
        phase_inform(world)
        eye, _ = _eye_and_far(world)
        before = known_tiles(world)
        self.assertIn(eye, before, "Глазная клетка должна быть известна глазу")
        _stamp(world, eye)

        first = report_new_trails(world)

        self.assertEqual(len(first), 1)
        self.assertEqual(known_tiles(world), before, "Весть расширила known_tiles")
        self.assertEqual(report_new_trails(world), [], "Весть задублировалась")

    def test_stale_trail_mark_stays_silent(self) -> None:
        """Опоздавшая метка вести не рождает."""
        world = load_scenario(SCENARIO, seed=SEED)
        eye, _ = _eye_and_far(world)
        _stamp(world, eye)
        world.stats[f"trail_born_{eye}"] = (
            float(world.clock.year * 12 + world.clock.month) - 1.0
        )
        self.assertEqual(report_new_trails(world), [], "Опоздавшая метка ожила")

    def test_trail_report_form(self) -> None:
        """Форма вести — глазная, существующих каналов достаточно."""
        world = load_scenario(SCENARIO, seed=SEED)
        eye, _ = _eye_and_far(world)
        report = make_trail_report(world, eye, world.clock.date)
        self.assertIn(report.source, ALL_SOURCES)
        self.assertEqual(report.delivery_date, world.clock.date)
        self.assertFalse(report.distorted)


if __name__ == "__main__":
    unittest.main()
