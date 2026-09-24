"""Info-гейт допуска №4 (ADR 0042): топ-ап без вести не молчит.

Топ-ап дотягивает СУЩЕСТВУЮЩИХ волков (`wolves_den`, `mode: topup`): новых
`Hazard` нет, прирост — в капах (`population_cap` 6.0 / `intensity_cap` 1.0).
Контракт требует `report_required: true` — рост без вести врёт И-3, поэтому
фаза зовёт `news.threats.make_threat_report` до прироста. Тест гонит путь фазы
(прирост + весть) и пинит: угроза видна в `PlayerView`, канал — существующий
`messenger` (новых каналов/задержек/сущностей нет), материя цела.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.tick import phase_hazard
from hillcourt.info.sources import ALL_SOURCES, MESSENGER
from hillcourt.news.threats import make_threat_report, report_pending_topups
from hillcourt.news.views import build_player_view
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SEED = 1729
WOLVES_TILE = "t_00_01"


class TestThreatReport(unittest.TestCase):
    """Весть о подросшей стае рождается и доходит до знания игрока."""

    def _world_with_topup(self):
        """Мир + топ-ап по контракту (прирост в капах, как сделает фаза)."""
        world = load_scenario(SCENARIO, seed=SEED)
        rule = world.catalogs.spawn_rules["wolves_den"]
        hazard = next(
            h
            for h in world.hazards.values()
            if h.tile_id == WOLVES_TILE and h.kind == "wolves"
        )
        ids_before = set(world.hazards)
        hazard.population = min(
            float(rule.params["population_cap"]),
            hazard.population + float(rule.params["population_gain"]),
        )
        hazard.intensity = min(
            float(rule.params["intensity_cap"]),
            hazard.intensity + float(rule.params["intensity_gain"]),
        )
        return world, hazard, ids_before

    def test_topup_grows_threat_and_report_reaches_player_view(self) -> None:
        world, hazard, ids_before = self._world_with_topup()
        self.assertEqual(
            set(world.hazards), ids_before, "Топ-ап родил новую угрозу"
        )
        self.assertLessEqual(hazard.population, 6.0)
        self.assertLessEqual(hazard.intensity, 1.0)

        report = make_threat_report(world, hazard, world.clock.date)

        self.assertEqual(report.source, MESSENGER)
        self.assertEqual(report.subject_kind, "tile")
        self.assertEqual(report.subject_id, WOLVES_TILE)
        self.assertEqual(report.facts.get("tile"), WOLVES_TILE)
        heard = report.facts.get("hazard")
        self.assertIsInstance(heard, dict)
        self.assertEqual(heard.get("kind"), "wolves")
        self.assertAlmostEqual(
            float(heard.get("population_approx", 0.0)),
            hazard.population,
            delta=0.15 * hazard.population + 0.01,
        )
        self.assertIn(WOLVES_TILE, report.content)
        self.assertIn("wolves", report.content)

        view = build_player_view(world, world.clock.date)
        entries = [e for e in view.entries if e.id == report.id]
        self.assertTrue(entries, "Весть о новой угрозе не дошла до знания игрока")
        self.assertEqual(entries[0].facts.get("tile"), WOLVES_TILE)
        self.assertEqual(entries[0].facts.get("hazard", {}).get("kind"), "wolves")

        self.assertLess(abs(world.ledger.delta(world.total_matter())), 1e-6)

    def test_threat_report_uses_existing_channel_only(self) -> None:
        """Новых каналов/задержек нет: весть — `messenger`, задержка 0."""
        world, hazard, _ = self._world_with_topup()
        report = make_threat_report(world, hazard, world.clock.date)
        self.assertIn(report.source, ALL_SOURCES)
        self.assertEqual(report.source, MESSENGER)
        self.assertEqual(report.delivery_date, world.clock.date)
        self.assertEqual(report.confidence, 0.5)
        self.assertEqual(report.noise, 0.15)

    def test_phase_topup_mark_reports_threat_in_player_view(self) -> None:
        """Гейт: метка фазы из `phase_hazard` превращается в весть в `PlayerView`."""
        world = load_scenario(SCENARIO, seed=SEED)
        world.catalogs.spawn_rules["wolves_den"].params["base_prob"] = 1.0
        wolves = next(h for h in world.hazards.values() if h.kind == "wolves")
        phase_hazard(world)
        self.assertIn(f"wolves_den_topup_{wolves.id}", world.stats)

        produced = report_pending_topups(world)

        self.assertEqual(len(produced), 1, "Метка топ-апа осталась без вести")
        report = produced[0]
        self.assertEqual(report.source, MESSENGER)
        self.assertEqual(report.subject_id, wolves.tile_id)
        self.assertEqual(report.facts.get("tile"), wolves.tile_id)
        self.assertEqual(report.facts.get("hazard", {}).get("kind"), "wolves")
        view = build_player_view(world, world.clock.date)
        entries = [e for e in view.entries if e.id == report.id]
        self.assertTrue(entries, "Топ-ап промолчал: угрозы нет в знании игрока")
        self.assertLess(abs(world.ledger.delta(world.total_matter())), 1e-6)

    def test_reader_ignores_stale_marks_and_dedups(self) -> None:
        """Опоздавшая метка вести не рождает; повтор в том же месяце не дублирует."""
        world = load_scenario(SCENARIO, seed=SEED)
        wolves = next(h for h in world.hazards.values() if h.kind == "wolves")
        stale = float(world.clock.year * 12 + world.clock.month) - 1.0
        world.stats[f"wolves_den_topup_{wolves.id}"] = stale
        self.assertEqual(report_pending_topups(world), [])

        world.catalogs.spawn_rules["wolves_den"].params["base_prob"] = 1.0
        phase_hazard(world)
        first = report_pending_topups(world)
        self.assertEqual(len(first), 1)
        self.assertEqual(report_pending_topups(world), [], "Весть задублировалась")


if __name__ == "__main__":
    unittest.main()
