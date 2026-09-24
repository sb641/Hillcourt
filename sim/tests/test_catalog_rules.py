"""Проверка данных каталогов: id, ссылки, массовый баланс."""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.catalogs import load_catalogs

ROOT = Path(__file__).resolve().parents[2]

VALID_CATEGORY = {"food", "fuel", "material", "lux", "livestock"}
VALID_STORAGE = {"granary", "cellar", "barn", "pack", "open"}
VALID_TERRAIN = {
    "hill", "field", "pasture", "forest", "marsh", "heath", "salt_flat", "ruin", "water",
}
VALID_TARGET = {"hazard", "pack", "good", "ruin"}


class TestCatalogRules(unittest.TestCase):
    """DoD каталога: id/name, storage, баланс рецептов, ссылки, руина."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.catalogs = load_catalogs(ROOT)

    def test_goods(self) -> None:
        self.assertTrue(self.catalogs.goods, "Каталог товаров пуст")
        for gid in sorted(self.catalogs.goods):
            good = self.catalogs.goods[gid]
            self.assertTrue(good.id, "У товара нет id")
            self.assertTrue(good.name, f"У товара {gid} нет name")
            self.assertIn(good.storage, VALID_STORAGE, f"{gid}: плохой storage")
            self.assertIn(good.category, VALID_CATEGORY, f"{gid}: плохая category")

    def test_recipes(self) -> None:
        self.assertTrue(self.catalogs.recipes, "Каталог рецептов пуст")
        for rid in sorted(self.catalogs.recipes):
            recipe = self.catalogs.recipes[rid]
            left = sum(recipe.inputs.values()) + sum(recipe.draws_standing.values())
            right = sum(recipe.outputs.values()) + sum(recipe.loss.values())
            self.assertAlmostEqual(left, right, places=6, msg=f"{rid}: нет баланса")
            self.assertGreater(recipe.labor_days, 0.0, f"{rid}: labor_days <= 0")
            for good in (
                list(recipe.inputs)
                + list(recipe.draws_standing)
                + list(recipe.outputs)
                + list(recipe.loss)
            ):
                self.assertIn(good, self.catalogs.goods, f"{rid}: неизвестный товар {good}")
            for terrain in recipe.requires_terrain:
                self.assertIn(terrain, VALID_TERRAIN, f"{rid}: плохой террейн {terrain}")
            if not recipe.transform:
                sources = set(recipe.inputs) | set(recipe.draws_standing)
                for good in list(recipe.outputs) + list(recipe.loss):
                    self.assertIn(
                        good,
                        sources,
                        f"{rid}: товар '{good}' не объявлен на входе и рецепт "
                        f"не помечен transform",
                    )

    def test_spawn_rules(self) -> None:
        self.assertTrue(self.catalogs.spawn_rules, "Каталог появления пуст")
        for sid in sorted(self.catalogs.spawn_rules):
            rule = self.catalogs.spawn_rules[sid]
            self.assertIn(rule.target, VALID_TARGET, f"{sid}: плохой target")
            if rule.target == "good":
                self.assertIn("good", rule.params, f"{sid}: нет params.good")
                self.assertIn(
                    rule.params["good"], self.catalogs.goods, f"{sid}: товар не существует"
                )
                self.assertIn("terrain", rule.params, f"{sid}: нет params.terrain")

    def test_obligation_and_right_templates(self) -> None:
        for tid in sorted(self.catalogs.obligation_templates):
            template = self.catalogs.obligation_templates[tid]
            self.assertTrue(template.id and template.name and template.kind)
            if template.default_share is not None:
                self.assertGreaterEqual(template.default_share, 0.0)
                self.assertLessEqual(template.default_share, 1.0)
        for tid in sorted(self.catalogs.right_templates):
            template = self.catalogs.right_templates[tid]
            self.assertTrue(template.id and template.name and template.kind)
            if template.default_rent_share is not None:
                self.assertGreaterEqual(template.default_rent_share, 0.0)
                self.assertLessEqual(template.default_rent_share, 1.0)

    def test_ruin_rule_is_unknown_without_loot(self) -> None:
        ruins = [
            rule
            for rule in self.catalogs.spawn_rules.values()
            if rule.target == "ruin"
        ]
        self.assertTrue(ruins, "Нет правила появления руины")
        rule = ruins[0]
        self.assertEqual(rule.params.get("mode"), "unknown")
        self.assertIn(rule.params.get("loot"), (None, "none"))
        self.assertIn(rule.params.get("relic"), (None, "none"))


class TestWolvesDenRule(unittest.TestCase):
    """Допуск №4 (ADR 0042): топ-ап волков — контракт правила, фаза живая.

    Правило только дотягивает СУЩЕСТВУЮЩИХ волков (mode=topup, новых `Hazard`
    нет — иначе нужна весть ниоткуда), бросок — через `rng.hazard`, потолки
    держат (`intensity_cap`/`population_cap`, иначе бесконечные волки), рост
    требует `Report` (рождение вести — за Info; фаза метит рост в
    `world.stats`). Механика фазы — `sim.tests.test_wolves_den_phase`.
    """

    def test_dens_topup_existing_wolves_only(self) -> None:
        from pathlib import Path as _Path

        from hillcourt.scenario import load_scenario

        catalogs = load_scenario(
            _Path(__file__).resolve().parents[2]
            / "design" / "scenarios" / "v0_shire.yml"
        ).catalogs
        rule = catalogs.spawn_rules["wolves_den"]
        self.assertEqual(rule.target, "hazard")
        self.assertEqual(rule.kind, "probabilistic")
        self.assertEqual(rule.params.get("mode"), "topup")
        self.assertNotIn("spawn", str(rule.params.get("mode")))
        self.assertEqual(rule.params.get("kind"), "wolves")
        self.assertIn("wolves", catalogs.hazard_rules)
        self.assertEqual(rule.params.get("terrain"), "forest")
        self.assertGreater(float(rule.params.get("base_prob", 0.0)), 0.0)
        self.assertGreater(float(rule.params.get("intensity_gain", 0.0)), 0.0)
        self.assertGreater(float(rule.params.get("population_gain", 0.0)), 0.0)
        self.assertGreaterEqual(
            float(rule.params.get("intensity_cap", 0.0)), 0.6 + 0.2,
            "Потолок ниже живых волков сценария — правило мёртво",
        )
        self.assertGreater(
            float(rule.params.get("population_cap", 0.0)), 0.6,
            "Потолок ниже живой стаи — правило мёртво",
        )
        self.assertEqual(rule.params.get("rng_stream"), "hazard")
        self.assertTrue(rule.params.get("report_required"), "Рост без вести врёт И-3")

    def test_phase_topup_only_no_spawn_caps_hold(self) -> None:
        from pathlib import Path as _Path

        from hillcourt.engine.tick import run_month
        from hillcourt.scenario import load_scenario

        world = load_scenario(
            _Path(__file__).resolve().parents[2]
            / "design" / "scenarios" / "v0_shire.yml",
            seed=1729,
        )
        rule = world.catalogs.spawn_rules["wolves_den"]
        intensity_cap = float(rule.params.get("intensity_cap", 0.0))
        population_cap = float(rule.params.get("population_cap", 0.0))
        ids_before = set(world.hazards)
        for _ in range(12):
            run_month(world)
        self.assertEqual(set(world.hazards), ids_before, "Правило родило угрозу")
        for hid, hazard in world.hazards.items():
            if hazard.kind != "wolves":
                continue
            self.assertLessEqual(
                hazard.intensity, intensity_cap + 1e-9, f"{hid}: потолок пробит"
            )
            self.assertLessEqual(
                hazard.population, population_cap + 1e-9, f"{hid}: потолок пробит"
            )
        for key in world.stats:
            if key.startswith("wolves_den_topup_"):
                self.assertIn(
                    key[len("wolves_den_topup_") :],
                    ids_before,
                    f"Метка {key} — про несуществующую угрозу",
                )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


class TestBandCampRule(unittest.TestCase):
    """Шаг 1 pipeline ватаги (ADR 0045, образец — ADR 0042): контракт правила.

    Появление — условное (`terrain: marsh` + `min_households: 3`; гейт сейчас
    не стреляет нигде — дворов на топях 0, запинено). Новая сущность — только
    с потолками (`intensity_cap`/`population_cap`) и только с вестью
    (`report_required` — рождение за Info при фазе, иначе И-3). Бросок — через
    `rng.hazard`. Исполнителя для ватаги в тике нет: 12 месяцев шира — тишина.
    """

    SCENARIOS = (
        "v0_hill_and_salt.yml",
        "v0_two_settlements.yml",
        "v0_shire.yml",
    )

    def _load(self, name: str, seed: int | None = None):
        from pathlib import Path as _Path

        from hillcourt.scenario import load_scenario

        return load_scenario(
            _Path(__file__).resolve().parents[2] / "design" / "scenarios" / name,
            seed=seed,
        )

    def test_band_spawns_conditional_with_caps_and_report(self) -> None:
        world = self._load("v0_shire.yml")
        rule = world.catalogs.spawn_rules["band_camp"]
        self.assertEqual(rule.target, "hazard")
        self.assertEqual(rule.kind, "conditional")
        self.assertEqual(rule.params.get("mode"), "spawn")
        self.assertEqual(rule.params.get("kind"), "band")
        self.assertIn("band", world.catalogs.hazard_rules)
        self.assertEqual(rule.params.get("terrain"), "marsh")
        self.assertEqual(int(rule.params.get("min_households", 0)), 3)
        prob = float(rule.params.get("base_prob", 0.0))
        self.assertGreater(prob, 0.0)
        self.assertLess(prob, 1.0)
        self.assertGreater(float(rule.params.get("intensity_init", 0.0)), 0.0)
        self.assertGreater(float(rule.params.get("population_init", 0.0)), 0.0)
        self.assertGreaterEqual(
            float(rule.params.get("intensity_cap", 0.0)),
            float(rule.params.get("intensity_init", 0.0)),
            "Потолок ниже рождения — правило мёртво",
        )
        self.assertGreaterEqual(
            float(rule.params.get("population_cap", 0.0)),
            float(rule.params.get("population_init", 0.0)),
            "Потолок ниже рождения — правило мёртво",
        )
        self.assertEqual(rule.params.get("rng_stream"), "hazard")
        self.assertTrue(rule.params.get("report_required"), "Ватага без вести врёт И-3")

    def test_gate_fires_nowhere(self) -> None:
        for name in self.SCENARIOS:
            world = self._load(name)
            rule = world.catalogs.spawn_rules["band_camp"]
            need = int(rule.params.get("min_households", 0))
            top = 0
            for tile in world.tiles.values():
                if tile.terrain != rule.params.get("terrain"):
                    continue
                count = sum(
                    1
                    for household in world.households.values()
                    if household.left_at is None
                    and household.current_tile_id == tile.id
                )
                top = max(top, count)
            self.assertLess(
                top, need, f"{name}: гейт стреляет ({top} дворов на топи)",
            )

    def test_rule_is_silent_without_phase(self) -> None:
        from hillcourt.engine.tick import run_month

        world = self._load("v0_shire.yml", seed=1729)
        others_before = {
            hid: (h.population, h.intensity)
            for hid, h in world.hazards.items()
            if h.kind != "wolves"
        }
        for _ in range(12):
            run_month(world)
        bands = [h for h in world.hazards.values() if h.kind == "band"]
        self.assertEqual(bands, [], "Правило родило ватагу без фазы")
        others_after = {
            hid: (world.hazards[hid].population, world.hazards[hid].intensity)
            for hid in others_before
        }
        self.assertEqual(others_after, others_before, "Чужая угроза дрейфует без фазы")
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
