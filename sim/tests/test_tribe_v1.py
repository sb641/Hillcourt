"""Племя v1 по ADR 0064: 7-полейная (перечень 0064) сущность, биекция, guard ухода.

Сущность тонкая: ровно перечисленные в ADR 0064 поля, состав/земля вычисляются,
своей книги нет. Загрузчик держит биекцию `Tribe ↔ Settlement.kind=native_village`
(два племени на деревню или деревня без племени — отказ). `phase_migrate` не
выселяет дворы племени (ни голод, ни недоимка) — обратный lookup по поселению и
составу, без `Household.tribe_id`. Экономика (оброк/вызов) — зона Economist, здесь
не выдумывается; числа в сценарии — дефолты.
"""

from __future__ import annotations

import dataclasses
import unittest
from pathlib import Path

from hillcourt.engine.tile_view import LAND_MARKS, has_mark, tile_form
from hillcourt.engine.tick import phase_migrate, run_month
from hillcourt.ontology import TRIBE_STANCES, Tribe
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_native_village.yml"
TRIBE = "tribe_village"
VILLAGE = "tribal_village"
VILLAGE_TILE = "t_05_02"


def _data() -> dict:
    import yaml

    with SCENARIO.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _write(data: dict) -> Path:
    import tempfile

    import yaml

    with tempfile.NamedTemporaryFile(
        "w", suffix=".yml", delete=False, encoding="utf-8"
    ) as fh:
        yaml.safe_dump(data, fh, allow_unicode=True)
        return Path(fh.name)


class TestTribeEntity(unittest.TestCase):
    """`Tribe` — тонкая политическая надстройка, полей-кэшей нет."""

    def test_exact_field_set(self) -> None:
        self.assertEqual(
            [f.name for f in dataclasses.fields(Tribe)],
            [
                "id",
                "name",
                "stance",
                "settlement_id",
                "tribute_grain",
                "muster_kits",
            ],
            "Набор полей Tribe разошёлся с ADR 0064",
        )
        for forbidden in (
            "household_ids",
            "land_tile_ids",
            "granted_land",
            "loyalty",
            "own_economy",
            "law_profile_id",
            "chief_person_id",
            "annexed",
            "muster_profile",
        ):
            self.assertFalse(
                hasattr(Tribe, forbidden), f"Tribe получил запрещённое поле {forbidden}"
            )

    def test_stances_closed_list(self) -> None:
        self.assertEqual(TRIBE_STANCES, ("independent", "allied", "vassal"))


class TestTribeLoading(unittest.TestCase):
    """Биекция: одно племя на одну native_village, без племянных деревень без племени."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO, seed=1729)

    def test_one_tribe_per_native_village(self) -> None:
        self.assertEqual(sorted(self.world.tribes), [TRIBE])
        tribe = self.world.tribes[TRIBE]
        self.assertEqual(tribe.settlement_id, VILLAGE)
        self.assertEqual(tribe.stance, "independent")
        self.assertIn(tribe.stance, TRIBE_STANCES)
        self.assertEqual(self.world.settlements[VILLAGE].kind, "native_village")
        # Состав вычисляется, а не хранится в племени.
        self.assertEqual(
            sorted(self.world.settlements[VILLAGE].household_ids),
            ["hh_tribe_01", "hh_tribe_02", "hh_tribe_03"],
        )

    def test_numbers_are_stubs_not_invented(self) -> None:
        tribe = self.world.tribes[TRIBE]
        self.assertEqual(tribe.tribute_grain, 0.0)
        self.assertEqual(tribe.muster_kits, 0.0)

    def test_two_tribes_on_one_village_rejected(self) -> None:
        data = _data()
        data["tribes"].append(
            {
                "id": "tribe_second",
                "name": "Племя",
                "stance": "allied",
                "settlement_id": VILLAGE,
            }
        )
        with self.assertRaises(ValueError):
            load_scenario(_write(data), seed=1729)

    def test_native_village_without_tribe_rejected(self) -> None:
        data = _data()
        data["tribes"] = []
        with self.assertRaises(ValueError):
            load_scenario(_write(data), seed=1729)

    def test_tribe_on_ordinary_settlement_rejected(self) -> None:
        data = _data()
        data["tribes"][0]["settlement_id"] = "fs_01"
        with self.assertRaises(ValueError):
            load_scenario(_write(data), seed=1729)

    def test_unknown_stance_rejected(self) -> None:
        data = _data()
        data["tribes"][0]["stance"] = "hostile"
        with self.assertRaises(ValueError):
            load_scenario(_write(data), seed=1729)

    def test_canon_scenarios_have_no_tribes(self) -> None:
        for name in ("v0_hill_and_salt.yml", "v0_two_settlements.yml", "v0_shire.yml"):
            world = load_scenario(ROOT / "design" / "scenarios" / name, seed=1729)
            self.assertEqual(world.tribes, {}, f"{name}: племя вне своего мира")


class TestTribeDepartureGuard(unittest.TestCase):
    """Главное: племя не выселяется тиком — ни голодом, ни недоимкой."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO, seed=1729)

    def _starve(self, household) -> None:
        household.hunger_days = 5
        for oid in household.obligation_ids:
            obligation = self.world.obligations.get(oid)
            if obligation is not None and obligation.due_amount > 0:
                obligation.arrears = 100.0 * obligation.due_amount

    def test_hungry_tribe_households_stay(self) -> None:
        tribe_ids = list(self.world.settlements[VILLAGE].household_ids)
        for hid in tribe_ids:
            self._starve(self.world.households[hid])
        packs_before = set(self.world.packs)
        phase_migrate(self.world)
        for hid in tribe_ids:
            household = self.world.households[hid]
            self.assertIsNone(household.left_at, f"{hid}: племя выселилась")
            self.assertEqual(household.current_tile_id, VILLAGE_TILE)
        self.assertEqual(set(self.world.packs), packs_before, "Племя собрало вещи")

    def test_ordinary_household_still_leaves(self) -> None:
        """Guard точен: такой же голод обычный свободный двор уводит."""
        household = self.world.households["hh_02"]
        self.assertNotEqual(household.settlement_id, VILLAGE)
        household.hunger_days = 5
        phase_migrate(self.world)
        self.assertIsNotNone(household.left_at, "Обычный двор перестал уходить")

    def test_lookalike_settlement_id_without_membership_still_leaves(self) -> None:
        """Подделка `settlement_id` без членства в составе — не племя."""
        household = self.world.households["hh_02"]
        household.settlement_id = VILLAGE
        household.hunger_days = 5
        phase_migrate(self.world)
        self.assertIsNotNone(
            household.left_at, "Двор не в составе племени защищён как племенной"
        )


class TestTribeViewMarkerCleaned(unittest.TestCase):
    """Мёртвый маркер `tribal` вычищен; вид племени — из `Settlement.kind`."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO, seed=1729)

    def test_marker_gone_form_intact(self) -> None:
        self.assertNotIn("tribal", LAND_MARKS)
        with self.assertRaises(ValueError):
            has_mark(self.world, VILLAGE_TILE, "tribal")
        self.assertEqual(tile_form(self.world, VILLAGE_TILE), "tribal_village")


class TestTribeDeterminism(unittest.TestCase):
    """Племя в хеше состояния; тот же seed — тот же мир; дельта 0."""

    def test_same_seed_same_hash_and_delta_zero(self) -> None:
        hashes = []
        for _ in range(2):
            world = load_scenario(SCENARIO, seed=1729)
            for _ in range(12):
                run_month(world)
            hashes.append(world.state_hash())
            self.assertAlmostEqual(
                world.ledger.delta(world.total_matter()), 0.0, places=6
            )
            alive = [
                hid
                for hid in world.settlements[VILLAGE].household_ids
                if world.households[hid].left_at is None
            ]
            self.assertEqual(len(alive), 3, "Племя вымерло за 12 месяцев")
        self.assertEqual(hashes[0], hashes[1], "Тот же seed — разный мир")


if __name__ == "__main__":
    unittest.main()
