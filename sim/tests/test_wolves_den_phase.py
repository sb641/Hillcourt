"""Фаза топ-апа волков: рост по `wolves_den`, потолки, новых `Hazard` нет.

Контракт правила — ADR 0042 (только topup существующих волков, бросок через
`rng.hazard`, потолки 1.0/6.0); исполнитель — `_apply_hazard_topups` в
`phase_hazard`. Весть о росте рождает Info (метка в `world.stats`), здесь —
только рост и метка.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.tick import phase_hazard, run_month
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SHIRE = ROOT / "design" / "scenarios" / "v0_shire.yml"


def _wolves(world):
    return next(h for h in world.hazards.values() if h.kind == "wolves")


def _force_topup(world):
    world.catalogs.spawn_rules["wolves_den"].params["base_prob"] = 1.0


class TestWolvesDenPhase(unittest.TestCase):
    """Топ-ап дотягивает живых волков на их террейне и метит рост для Info."""

    def test_topup_grows_existing_wolves_and_marks(self) -> None:
        world = load_scenario(SCENARIO, seed=1729)
        wolves = _wolves(world)
        before = (wolves.population, wolves.intensity)
        ids_before = set(world.hazards)
        _force_topup(world)
        phase_hazard(world)
        self.assertEqual(set(world.hazards), ids_before, "Топ-ап родил угрозу")
        self.assertAlmostEqual(wolves.intensity, before[1] + 0.2, places=9)
        self.assertAlmostEqual(wolves.population, before[0] + 1.0, places=9)
        self.assertEqual(world.stats.get("wolves_den_topup"), 1.0)
        self.assertIn(f"wolves_den_topup_{wolves.id}", world.stats)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_caps_hold_and_capped_growth_leaves_no_mark(self) -> None:
        world = load_scenario(SCENARIO, seed=1729)
        wolves = _wolves(world)
        wolves.intensity = 0.95
        wolves.population = 5.5
        _force_topup(world)
        phase_hazard(world)
        self.assertAlmostEqual(wolves.intensity, 1.0, places=9, msg="Потолок пробит")
        self.assertAlmostEqual(wolves.population, 6.0, places=9, msg="Потолок пробит")
        marks = world.stats.get("wolves_den_topup")
        self.assertEqual(marks, 1.0)
        phase_hazard(world)
        self.assertAlmostEqual(wolves.intensity, 1.0, places=9)
        self.assertAlmostEqual(wolves.population, 6.0, places=9)
        self.assertEqual(
            world.stats.get("wolves_den_topup"), 1.0, "Рост упёрся — метки быть не должно"
        )

    def test_only_active_wolves_on_rule_terrain(self) -> None:
        from hillcourt.ontology import Hazard

        world = load_scenario(SCENARIO, seed=1729)
        bog = next(h for h in world.hazards.values() if h.kind == "bog")
        bog_before = (bog.population, bog.intensity)
        stray = Hazard(
            id="haz_stray",
            kind="wolves",
            tile_id=bog.tile_id,  # топь, а не лес правила
            intensity=0.6,
            active=True,
            spawn_rule_id=None,
            population=0.6,
            satiety=0.4,
        )
        world.hazards[stray.id] = stray
        world.tiles[stray.tile_id].hazard_ids.append(stray.id)
        wolves = _wolves(world)
        wolves.active = False
        wolves_before = (wolves.population, wolves.intensity)
        _force_topup(world)
        phase_hazard(world)
        self.assertEqual(
            (bog.population, bog.intensity), bog_before, "Топь не волки — не трогать"
        )
        self.assertEqual(
            (stray.population, stray.intensity), (0.6, 0.6), "Чужой террейн — не трогать"
        )
        self.assertEqual(
            (wolves.population, wolves.intensity),
            wolves_before,
            "Погасшая угроза не растёт",
        )
        self.assertNotIn("wolves_den_topup", world.stats, "Роста не было — метки нет")

    def test_no_new_hazards_over_time(self) -> None:
        for seed in (1729, 42, 7):
            world = load_scenario(SHIRE, seed=seed)
            ids_before = set(world.hazards)
            for _ in range(24):
                run_month(world)
            self.assertEqual(
                set(world.hazards), ids_before, f"seed {seed}: правило родило угрозу"
            )

    def test_deterministic_named_stream(self) -> None:
        def run(seed: int, months: int):
            world = load_scenario(SHIRE, seed=seed)
            for _ in range(months):
                run_month(world)
            state = {
                hid: (h.population, h.intensity) for hid, h in world.hazards.items()
            }
            marks = {
                key: value
                for key, value in world.stats.items()
                if key.startswith("wolves_den_topup")
            }
            return state, marks

        self.assertEqual(run(1729, 24), run(1729, 24), "Тот же seed — другой результат")


class TestThreatWiring(unittest.TestCase):
    """Проводка вести в живом тике: топ-ап → весть в `PlayerView`.

    Тексты и канал — зона Info (`news/threats.py`); здесь только пин связки:
    живой месяц с топ-апом даёт ровно одну весть об угрозе в знании игрока,
    месяц без топ-апа — тишину, повторный заход `phase_inform` — без дублей.
    """

    def _threat_entries(self, world, view):
        from hillcourt.info.sources import MESSENGER

        return [
            entry
            for entry in view.entries
            if entry.source == MESSENGER
            and entry.subject_id == _wolves(world).tile_id
            and isinstance(entry.facts.get("hazard"), dict)
        ]

    def test_live_tick_topup_reaches_player_view(self) -> None:
        from hillcourt.news.views import build_player_view

        world = load_scenario(SCENARIO, seed=1729)
        _force_topup(world)
        run_month(world)
        view = build_player_view(world, world.clock.date)
        entries = self._threat_entries(world, view)
        self.assertEqual(len(entries), 1, "Топ-ап промолчал в живом тике")
        self.assertEqual(entries[0].source, "messenger")
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_live_tick_without_topup_is_silent(self) -> None:
        from hillcourt.news.views import build_player_view

        world = load_scenario(SCENARIO, seed=1729)
        world.catalogs.spawn_rules["wolves_den"].params["base_prob"] = 0.0
        run_month(world)
        self.assertNotIn("wolves_den_topup", world.stats)
        view = build_player_view(world, world.clock.date)
        self.assertEqual(
            self._threat_entries(world, view), [], "Топ-апа не было — весть взялась"
        )

    def test_reentered_inform_does_not_duplicate(self) -> None:
        from hillcourt.engine.tick import phase_hazard, phase_inform
        from hillcourt.news.views import build_player_view

        world = load_scenario(SCENARIO, seed=1729)
        _force_topup(world)
        phase_hazard(world)
        phase_inform(world)
        phase_inform(world)
        view = build_player_view(world, world.clock.date)
        self.assertEqual(
            len(self._threat_entries(world, view)),
            1,
            "Повторный заход продублировал весть",
        )


if __name__ == "__main__":
    unittest.main()
