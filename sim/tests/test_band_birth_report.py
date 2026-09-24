"""Info-гейт шага ватаги (ADR 0045): рождение только с вестью — структурно.

Рождение — новая СТОЯЧАЯ ватага (`band_camp`, `mode: spawn`): топь, гейт
`min_households`, `intensity_init` 1.0 / `population_init` 3.0, потолки
(`report_required: true` — рождение без вести врёт И-3). Фаза (Implementer)
метит рождение штампом `<rule_id>_birth_<hazard_id>` в `world.stats` и зовёт
`news/threats.py::report_pending_births`; нет вести в ответе — рождение
откатывается. Весть — та же форма, что у топ-апа (`messenger`,
`population_approx`, `tile` = about, дедуп, без новых каналов/задержек).
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.tick import phase_hazard
from hillcourt.info.sources import ALL_SOURCES, MESSENGER
from hillcourt.news.threats import (
    make_birth_report,
    report_pending_births,
    report_pending_topups,
)
from hillcourt.news.views import build_player_view
from hillcourt.ontology import Hazard
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SEED = 1729
MARSH_TILE = "t_02_03"


def _give_birth(world):
    """Родить ватагу по контракту (как сделает фаза): угроза + метка месяца."""
    rule = world.catalogs.spawn_rules["band_camp"]
    assert world.tiles[MARSH_TILE].terrain == "marsh", "Контракт: рождение на топи"
    hazard = Hazard(
        id="haz_band_test",
        kind="band",
        tile_id=MARSH_TILE,
        intensity=float(rule.params["intensity_init"]),
        population=float(rule.params["population_init"]),
        spawn_rule_id="band_camp",
    )
    world.hazards[hazard.id] = hazard
    world.tiles[MARSH_TILE].hazard_ids.append(hazard.id)
    stamp = float(world.clock.year * 12 + world.clock.month)
    world.stats["band_camp_births"] = world.stats.get("band_camp_births", 0.0) + 1.0
    world.stats[f"band_camp_birth_{hazard.id}"] = stamp
    return hazard


def _seat_three_on_marsh(world):
    """Посадить три живых двора на топь (гейт `min_households`, стенд)."""
    moved = 0
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None:
            continue
        household.current_tile_id = MARSH_TILE
        moved += 1
        if moved == 3:
            break
    assert moved == 3, "Не хватило живых дворов для гейта"


class TestBandBirthReport(unittest.TestCase):
    """Весть о народившейся ватаге рождается и доходит до знания игрока."""

    def test_birth_reports_band_in_player_view(self) -> None:
        world = load_scenario(SCENARIO, seed=SEED)
        ids_before = set(world.hazards)
        hazard = _give_birth(world)
        self.assertEqual(set(world.hazards) - ids_before, {hazard.id})

        produced = report_pending_births(world)

        self.assertEqual(len(produced), 1, "Рождение осталось без вести")
        report = produced[0]
        self.assertEqual(report.source, MESSENGER)
        self.assertEqual(report.subject_kind, "tile")
        self.assertEqual(report.subject_id, MARSH_TILE)
        self.assertEqual(report.facts.get("tile"), MARSH_TILE)
        heard = report.facts.get("hazard")
        self.assertIsInstance(heard, dict)
        self.assertEqual(heard.get("kind"), "band")
        self.assertAlmostEqual(
            float(heard.get("population_approx", 0.0)),
            hazard.population,
            delta=0.15 * hazard.population + 0.01,
        )
        self.assertIn(MARSH_TILE, report.content)
        self.assertIn("band", report.content)

        view = build_player_view(world, world.clock.date)
        entries = [e for e in view.entries if e.id == report.id]
        self.assertTrue(entries, "Рождение не дошло до знания игрока")
        self.assertEqual(entries[0].facts.get("tile"), MARSH_TILE)
        self.assertEqual(entries[0].facts.get("hazard", {}).get("kind"), "band")

        self.assertLess(abs(world.ledger.delta(world.total_matter())), 1e-6)

    def test_phase_birth_sticks_only_with_report(self) -> None:
        """Структурный гейт: фаза родила — угроза висит только с вестью."""
        world = load_scenario(SCENARIO, seed=SEED)
        _seat_three_on_marsh(world)
        world.catalogs.spawn_rules["band_camp"].params["base_prob"] = 1.0
        ids_before = set(world.hazards)

        phase_hazard(world)

        newborn = [
            hid
            for hid in set(world.hazards) - ids_before
            if world.hazards[hid].kind == "band"
        ]
        self.assertEqual(len(newborn), 1, "Фаза не родила ватагу за гейтом")
        hazard = world.hazards[newborn[0]]
        self.assertEqual(hazard.tile_id, MARSH_TILE)
        self.assertEqual(world.stats.get("band_camp_births"), 1.0)
        reports = [
            r
            for r in world.reports
            if r.source == MESSENGER and r.subject_id == MARSH_TILE
        ]
        self.assertTrue(reports, "Рождение откатилось или промолчало: вести нет")
        self.assertEqual(reports[-1].facts.get("hazard", {}).get("kind"), "band")
        view = build_player_view(world, world.clock.date)
        self.assertTrue(
            [e for e in view.entries if e.id == reports[-1].id],
            "Рождение не видно в знании игрока",
        )
        self.assertLess(abs(world.ledger.delta(world.total_matter())), 1e-6)

    def test_silence_without_birth(self) -> None:
        """Рождения не было — вести нет: тишина, а не «всё хорошо»."""
        world = load_scenario(SCENARIO, seed=SEED)
        self.assertEqual(report_pending_births(world), [])
        self.assertEqual(report_pending_topups(world), [])
        self.assertFalse(
            [
                r
                for r in world.reports
                if r.source == MESSENGER and r.subject_id == MARSH_TILE
            ],
            "Весть о ватаге без рождения",
        )

        stale = float(world.clock.year * 12 + world.clock.month) - 1.0
        world.stats["band_camp_birth_haz_band_test"] = stale
        self.assertEqual(report_pending_births(world), [], "Опоздавшая метка ожила")

    def test_birth_report_uses_existing_channel_only(self) -> None:
        """Новых каналов/задержек нет: весть — `messenger`, задержка 0."""
        world = load_scenario(SCENARIO, seed=SEED)
        hazard = _give_birth(world)
        report = make_birth_report(world, hazard, world.clock.date)
        self.assertIn(report.source, ALL_SOURCES)
        self.assertEqual(report.source, MESSENGER)
        self.assertEqual(report.delivery_date, world.clock.date)
        self.assertEqual(report.confidence, 0.5)
        self.assertEqual(report.noise, 0.15)


if __name__ == "__main__":
    unittest.main()
