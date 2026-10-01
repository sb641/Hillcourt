"""Закон в движке: сценарий — данные, а не особый код (ADR 0132 п. 4).

Проверяет, что в `sim/src/hillcourt/engine/` не осталось сценарных веток и
мёртвых кусков расчёта, а надел и ёмкость клетки приходят из пресета и
режима клетки (ADR 0132 п. 1–3, ADR 0124).
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from hillcourt.engine import yield_law
from hillcourt.engine.growth import run_detailed_growth
from hillcourt.engine.tick import run_month
from hillcourt.engine.tile_view import TILE_MAX_HOUSEHOLDS, URBAN_MAX_HOUSEHOLDS
from hillcourt.runner import _apply_script_entry
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "sim" / "src" / "hillcourt" / "engine"
SRC = ROOT / "sim" / "src" / "hillcourt"
SCENARIO = ROOT / "design" / "scenarios" / "start_stand.yml"
SEED = 1729
TENANTS = (
    "hh_family_01",
    "hh_family_02",
    "hh_family_03",
    "hh_family_04",
    "hh_family_05",
    "hh_wave_01",
)


def _python_sources(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _ast_string_constants(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


class TestEngineHasNoScenarioBranch(unittest.TestCase):
    """Сценарий — данные: движок не знает имён сценариев, дворов и отрядов."""

    def test_engine_never_mentions_a_scenario_id(self) -> None:
        for path in _python_sources(ENGINE):
            self.assertNotIn(
                "scenario_id",
                _ast_string_constants(path),
                f"{path.name}: чтение scenario_id — сценарная ветка в движке",
            )
            source = path.read_text(encoding="utf-8")
            self.assertNotIn(
                ".scenario_id", source, f"{path.name}: чтение scenario_id"
            )

    def test_engine_hardcodes_no_scenario_household_or_pack(self) -> None:
        for name in ("start_stand", "retinue_hunt", "start_caravan", "hh_retinue"):
            for path in _python_sources(ENGINE):
                self.assertNotIn(
                    name,
                    _ast_string_constants(path),
                    f"{path.name}: сценарное имя '{name}' зашито в движок",
                )

    def test_yield_law_has_no_dead_function(self) -> None:
        defined = {
            name
            for name, value in vars(yield_law).items()
            if not name.startswith("_")
            and callable(value)
            and getattr(value, "__module__", "") == yield_law.__name__
        }
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in _python_sources(SRC)
            if path.name != "yield_law.py"
        )
        for name in defined:
            self.assertIn(
                f"{name}(",
                sources,
                f"engine/yield_law.py::{name} никем не читается — мёртвый код",
            )
        self.assertEqual(
            defined,
            {"field_yield_factor", "growth_season_factor", "demesne_field_factor"},
            "Набор функций yield_law разошёлся с кодом: либо мёртвая, либо потеряна",
        )


class TestHoldingAndCapComeFromData(unittest.TestCase):
    """Надел — доля пресета, плотность — кап клетки (ADR 0132 п. 1 и 3)."""

    def _stand(self, months: int = 1):
        world = load_scenario(SCENARIO, seed=SEED)
        for month in range(1, months + 1):
            for entry in world.script:
                if int(entry.get("at_month", 0)) == month:
                    _apply_script_entry(world, entry)
            run_month(world)
        return world

    def test_holding_scale_equals_the_preset_share(self) -> None:
        world = self._stand(6)
        shares = {
            land_kind: value
            for land_kind, value in world.manor.holding_tiles_by_land_kind.items()
        }
        self.assertEqual(shares["villein_tenement"], 1.0)
        self.assertEqual(shares["cotter_plot"], 0.5)
        for household_id in TENANTS:
            if household_id not in world.households:
                continue
            household = world.households[household_id]
            preset = world.catalogs.legal_statuses[household.legal_status_id]
            self.assertEqual(
                household.holding_scale,
                shares[preset.land_kind],
                f"{household_id}: надел не из пресета",
            )
        for household_id in TENANTS:
            if household_id not in world.households:
                continue
            self.assertNotAlmostEqual(
                world.households[household_id].holding_scale,
                1.0 / len(TENANTS),
                places=6,
            )

    def test_tile_cap_is_five_and_urban_is_twenty(self) -> None:
        self.assertEqual(TILE_MAX_HOUSEHOLDS, 5)
        self.assertEqual(URBAN_MAX_HOUSEHOLDS, 20)

    def test_holding_scale_is_never_rewritten_by_a_hex_share(self) -> None:
        world = load_scenario(SCENARIO, seed=SEED)
        before = {
            household_id: household.holding_scale
            for household_id, household in world.households.items()
        }
        for month in range(1, 8):
            for entry in world.script:
                if int(entry.get("at_month", 0)) == month:
                    _apply_script_entry(world, entry)
            run_month(world)
        for household_id, household in world.households.items():
            if household.legal_status_id not in {"villein", "cotter"}:
                continue
            preset = world.catalogs.legal_statuses[household.legal_status_id]
            self.assertEqual(
                household.holding_scale,
                world.manor.holding_tiles_by_land_kind[preset.land_kind],
            )
        self.assertGreater(len(before), 0)

    def test_growth_reads_tile_resource_productivity(self) -> None:
        world = load_scenario(SCENARIO, seed=SEED)
        field = world.tiles["t_01_00"]
        before = world.get_stock(field.standing_stock_id).amounts.get("grain", 0.0)
        world.tiles["t_01_00"].resource_productivity = 3.0
        run_detailed_growth(world, 1.0)
        after = world.get_stock(field.standing_stock_id).amounts.get("grain", 0.0)
        self.assertGreater(after, before)


if __name__ == "__main__":
    unittest.main()
