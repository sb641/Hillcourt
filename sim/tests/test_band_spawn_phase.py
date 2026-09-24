"""Фаза рождения ватаги: гейт per-tick, рождение только с вестью.

Контракт правила — ADR 0045 (`band_camp`, `mode: spawn`, гейт `marsh` + 3 двора,
бросок `rng.hazard`, рождение 1.0/3.0, потолки 1.0/6.0, кража 2.0 из
`hazards.yml`); исполнитель — `_apply_hazard_spawns` в `phase_hazard`.
Наказы Critic: рождение новой сущности — только вместе с вестью, структурно
(выигрыш без читателя вести откатывается); гейт считается каждый тик, кэша нет.
Весть рождает Info (`news.threats.report_pending_births`, его зона): здесь —
только протокол связки (вызов читателя + проверка вести), тексты не трогаем.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from hillcourt.engine.tick import phase_hazard, run_month
from hillcourt.news import threats as threats_module
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SHIRE = ROOT / "design" / "scenarios" / "v0_shire.yml"
HOOK = "report_pending_births"


class _FakeBirths:
    """Дубль будущего читателя Info: вести по свежим меткам рождения.

    Возвращает заглушки с полями, которые проверяет фаза
    (`subject_id`, `event_date`), — тексты/канал не фабрикует.
    """

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, world):
        self.calls += 1
        stamp = float(world.clock.year * 12 + world.clock.month)
        produced = []
        for hazard_id in sorted(world.hazards):
            if world.stats.get(f"band_camp_birth_{hazard_id}") != stamp:
                continue
            hazard = world.hazards[hazard_id]
            produced.append(
                SimpleNamespace(
                    subject_id=hazard.tile_id, event_date=world.clock.date
                )
            )
        return produced


class TestBandSpawnPhase(unittest.TestCase):
    """Ватага рождается на топи с дворами — и только парой с вестью."""

    def _marsh_tile(self, world) -> str:
        tile = next(t for t in world.tiles.values() if t.terrain == "marsh")
        return tile.id

    def _settle(self, world, tile_id: str, count: int) -> list:
        moved = []
        for hid in sorted(world.households):
            if len(moved) >= count:
                break
            household = world.households[hid]
            if household.left_at is not None:
                continue
            household.current_tile_id = tile_id
            moved.append(hid)
        return moved

    def _force(self, world) -> None:
        world.catalogs.spawn_rules["band_camp"].params["base_prob"] = 1.0

    def _install_fake(self):
        saved = getattr(threats_module, HOOK, None)
        had = hasattr(threats_module, HOOK)
        fake = _FakeBirths()
        setattr(threats_module, HOOK, fake)
        return saved, had, fake

    def _restore_hook(self, saved, had) -> None:
        if had:
            setattr(threats_module, HOOK, saved)
        else:
            try:
                delattr(threats_module, HOOK)
            except AttributeError:
                pass

    def _bands(self, world):
        return [h for h in world.hazards.values() if h.kind == "band"]

    def test_no_reader_no_birth(self) -> None:
        """Без вести не рожать (кодом): читателя нет — выигрыш откатывается."""
        saved = getattr(threats_module, HOOK, None)
        had = hasattr(threats_module, HOOK)
        if had:
            delattr(threats_module, HOOK)
        try:
            world = load_scenario(SCENARIO, seed=1729)
            tile_id = self._marsh_tile(world)
            self._settle(world, tile_id, 3)
            self._force(world)
            ids_before = set(world.hazards)
            phase_hazard(world)
            self.assertEqual(set(world.hazards), ids_before, "Родилась без вести")
            self.assertNotIn("band_camp_births", world.stats)
            self.assertFalse(
                [key for key in world.stats if key.startswith("band_camp_birth_")],
                "Метка висит без вести",
            )
            self.assertAlmostEqual(
                world.ledger.delta(world.total_matter()), 0.0, places=6
            )
        finally:
            self._restore_hook(saved, had)

    def test_gate_counts_live_households_per_tick(self) -> None:
        """Гейт — по живым дворам этого тика, не кэш: уход закрывает сев."""
        saved, had, fake = self._install_fake()
        try:
            world = load_scenario(SCENARIO, seed=1729)
            tile_id = self._marsh_tile(world)
            moved = self._settle(world, tile_id, 2)
            self._force(world)
            phase_hazard(world)
            self.assertEqual(self._bands(world), [], "Гейт 2 < 3 — сева нет")
            self._settle(world, tile_id, 3)
            phase_hazard(world)
            bands = self._bands(world)
            self.assertEqual(len(bands), 1, "Гейт 3 — сева нет")
            band = bands[0]
            self.assertEqual(band.tile_id, tile_id)
            self.assertEqual(band.spawn_rule_id, "band_camp")
            self.assertTrue(band.active)
            self.assertAlmostEqual(band.intensity, 1.0, places=9)
            self.assertAlmostEqual(band.population, 3.0, places=9)
            world.households[moved[0]].left_at = world.clock.date
            phase_hazard(world)
            self.assertEqual(
                len(self._bands(world)), 1, "Уход mid-run сев не закрыл (кэш?)"
            )
        finally:
            self._restore_hook(saved, had)

    def test_caps_clamp_birth(self) -> None:
        """Рождение режется потолками, даже если каталог врёт инитами."""
        saved, had, _fake = self._install_fake()
        try:
            world = load_scenario(SCENARIO, seed=1729)
            params = world.catalogs.spawn_rules["band_camp"].params
            params["intensity_init"] = 5.0
            params["population_init"] = 9.0
            self._settle(world, self._marsh_tile(world), 3)
            self._force(world)
            phase_hazard(world)
            bands = self._bands(world)
            self.assertEqual(len(bands), 1)
            self.assertLessEqual(bands[0].intensity, 1.0, "Потолок пробит рождением")
            self.assertLessEqual(bands[0].population, 6.0, "Потолок пробит рождением")
        finally:
            self._restore_hook(saved, had)

    def test_no_second_band_on_occupied_tile(self) -> None:
        """Одна стоячая угроза на клетку: рост сверх рождения — будущим решением."""
        saved, had, _fake = self._install_fake()
        try:
            world = load_scenario(SCENARIO, seed=1729)
            self._settle(world, self._marsh_tile(world), 3)
            self._force(world)
            phase_hazard(world)
            phase_hazard(world)
            self.assertEqual(len(self._bands(world)), 1, "Ватаги плодятся на клетке")
        finally:
            self._restore_hook(saved, had)

    def test_theft_uses_band_rate_and_kind(self) -> None:
        """Кража новорождённой — 2.0 из `hazards.yml`, проводка честно `band`."""
        from hillcourt.engine.seat import peace_theft_factor

        saved, had, _fake = self._install_fake()
        try:
            world = load_scenario(SCENARIO, seed=1729)
            tile_id = self._marsh_tile(world)
            moved = self._settle(world, tile_id, 3)
            household = world.households[moved[0]]
            stock = world.get_stock(household.stock_id)
            available = float(stock.amounts.get("grain", 0.0))
            self._force(world)
            entries_before = len(world.ledger.entries)
            phase_hazard(world)
            bands = self._bands(world)
            self.assertEqual(len(bands), 1)
            expected = min(
                available, bands[0].intensity * 2.0 * peace_theft_factor(world, tile_id)
            )
            self.assertGreater(expected, 0.0, "Тест без зерна — красть нечего")
            self.assertAlmostEqual(
                stock.amounts.get("grain", 0.0), available - expected, places=9
            )
            fresh = world.ledger.entries[entries_before:]
            band_wires = [
                entry
                for entry in fresh
                if entry.good == "grain" and entry.reason == "band"
            ]
            self.assertTrue(band_wires, "Кража ватаги проведена как 'wolves'")
            self.assertAlmostEqual(
                world.ledger.delta(world.total_matter()), 0.0, places=6
            )
        finally:
            self._restore_hook(saved, had)

    def test_live_birth_with_real_reader_reports_to_player_view(self) -> None:
        """Связка живьём: настоящий читатель Info — рождение + весть в знании.

        Тексты/канал — зона Info; здесь только структура: угроза висит,
        весть о её клетке с датой сегодня — в `PlayerView`.
        """
        from hillcourt.info.sources import MESSENGER
        from hillcourt.news.views import build_player_view

        self.assertTrue(
            hasattr(threats_module, HOOK), "Читатель Info отсутствует — связка грязна"
        )
        world = load_scenario(SCENARIO, seed=1729)
        tile_id = self._marsh_tile(world)
        self._settle(world, tile_id, 3)
        self._force(world)
        phase_hazard(world)
        bands = self._bands(world)
        self.assertEqual(len(bands), 1, "Рождение с вестью не устояло")
        view = build_player_view(world, world.clock.date)
        entries = [
            entry
            for entry in view.entries
            if entry.source == MESSENGER
            and entry.subject_id == tile_id
            and entry.event_date == world.clock.date
            and isinstance(entry.facts.get("hazard"), dict)
        ]
        self.assertEqual(len(entries), 1, "Рождение без вести в знании игрока")
        self.assertEqual(entries[0].facts["hazard"].get("kind"), "band")
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )

    def test_deterministic_named_stream(self) -> None:
        def run(seed: int):
            saved = getattr(threats_module, HOOK, None)
            had = hasattr(threats_module, HOOK)
            fake = _FakeBirths()
            setattr(threats_module, HOOK, fake)
            try:
                world = load_scenario(SHIRE, seed=seed)
                tile_id = next(
                    t.id for t in world.tiles.values() if t.terrain == "marsh"
                )
                living = [
                    hid
                    for hid in sorted(world.households)
                    if world.households[hid].left_at is None
                ][:3]
                for hid in living:
                    world.households[hid].current_tile_id = tile_id
                world.catalogs.spawn_rules["band_camp"].params["base_prob"] = 1.0
                for _ in range(6):
                    run_month(world)
                state = {
                    hid: (h.kind, h.tile_id, h.population, h.intensity)
                    for hid, h in world.hazards.items()
                }
                marks = {
                    key: value
                    for key, value in world.stats.items()
                    if key.startswith("band_camp_")
                }
                return state, marks
            finally:
                if had:
                    setattr(threats_module, HOOK, saved)
                else:
                    try:
                        delattr(threats_module, HOOK)
                    except AttributeError:
                        pass

        self.assertEqual(run(1729), run(1729), "Тот же seed — другой результат")


if __name__ == "__main__":
    unittest.main()
