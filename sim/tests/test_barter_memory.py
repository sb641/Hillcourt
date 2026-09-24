"""G4: память сделки вместо биржи — Pack смотрит память и Report, не истину.

`World.barter_memory` хранит долю доехавшего груза (`delivered / carried`) по
ключу `"origin->destination:good"`, а не цену. Отправка обоза смотрит:
свою память (последняя сделка хуже `min_ratio` — не везти) и ДОСТАВЛЕННЫЕ
`Report` о поселении назначения (свежий отчёт об избытке зерна/соли — везти
нечего). Стоки и клетки назначения при решении не читаются: подмена истины
назначения не меняет вердикт. Память входит в `state_hash`.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy import caravan
from hillcourt.engine.tick import run_month
from hillcourt.info.sources import CARAVAN
from hillcourt.news.propagation import make_report
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_two_settlements.yml"
ASH_TILE = "t_02_07"
ASH_STORES = "settlement:ash_village"
GRAIN_KEY = "hill_court->ash_village:grain"
SALT_KEY = "salt_village->hill_court:salt"
DISPATCH_MONTH = 4
WINDOW_AFTER_MONTH = 12


class _AlwaysHazard:
    """Заглушка потока опасности: бросок 0.0 — риск рассеяния срабатывает всегда."""

    def random(self) -> float:
        return 0.0


def _run_months(world, months: int) -> None:
    for _ in range(months):
        run_month(world)


def _grain_packs(packs) -> list:
    return [pack for pack in packs if pack.cargo.amounts.get("grain", 0.0) > 0.0]


def _pack_signature(packs) -> list:
    return sorted(
        (pack.id, tuple(sorted(pack.cargo.amounts.items()))) for pack in packs
    )


class TestBarterMemory(unittest.TestCase):
    """Память сделки, отчёт об избытке и недоверие к истине дальней деревни."""

    def test_memory_records_carried_delivered_ratio_and_date(self) -> None:
        world = load_scenario(SCENARIO)
        _run_months(world, 3)
        # Изоляция механики: сбрасываем отчёты, чтобы избыток не помешал рейсу.
        world.reports.clear()
        date = world.clock.date
        dispatched = _grain_packs(caravan.dispatch_caravans(world, date))
        self.assertEqual(len(dispatched), 1, "Грузовой обоз не отправился")
        pack = dispatched[0]
        pack.eta_date = date
        # Новый маршрут `find_path` обходит топь (2,4): переносим стартовую
        # опасность на клетку фактического пути, иначе рассеяния нет. Катастрофу
        # выключаем, чтобы зонд проверял частичную потерю и память сделки.
        world.catalogs.spawn_rules["caravan_grain_to_ash"].params[
            "catastrophe_chance"
        ] = 0.0
        hazard = world.hazards["haz_002"]
        world.tiles[hazard.tile_id].hazard_ids.remove(hazard.id)
        hazard.tile_id = pack.route[1]
        world.tiles[hazard.tile_id].hazard_ids.append(hazard.id)
        world.rng.hazard = _AlwaysHazard()
        caravan.resolve_caravans(world, date)

        memory = world.barter_memory.get(GRAIN_KEY)
        self.assertIsNotNone(memory, "Память сделки не заполнена после разгрузки")
        self.assertIn("date", memory)
        self.assertGreater(memory["carried"], 0.0)
        self.assertGreater(memory["delivered"], 0.0)
        self.assertGreater(memory["lost"], 0.0, "Рассеяние не попало в память")
        self.assertLess(memory["ratio"], 1.0, "Рассеяние не снизило долю груза")
        self.assertAlmostEqual(
            memory["ratio"], memory["delivered"] / memory["carried"], places=9
        )
        self.assertLessEqual(memory["ratio"], 1.0)

    def test_destination_truth_does_not_change_dispatch(self) -> None:
        first = load_scenario(SCENARIO, seed=1729)
        second = load_scenario(SCENARIO, seed=1729)
        _run_months(first, 3)
        _run_months(second, 3)
        first.reports.clear()
        second.reports.clear()

        second.get_stock(ASH_STORES).amounts["grain"] = 9999.0
        second.get_stock("household:av_01").amounts["grain"] = 9999.0
        second.get_stock("tile:" + ASH_TILE).amounts["grain"] = 9999.0

        date = first.clock.date
        clean = caravan.dispatch_caravans(first, date)
        spoofed = caravan.dispatch_caravans(second, date)

        self.assertTrue(_grain_packs(clean), "Без подмены обоз не отправился")
        self.assertEqual(
            _pack_signature(clean),
            _pack_signature(spoofed),
            "Подмена стоков назначения изменила решение dispatch: Pack читает истину",
        )

    def test_fresh_surplus_report_skips_then_window_expires(self) -> None:
        world = load_scenario(SCENARIO)
        world.clock.month = DISPATCH_MONTH
        date = world.clock.date
        make_report(
            world,
            CARAVAN,
            "tile",
            ASH_TILE,
            "Дальняя деревня: зерна, сказывают, около 999.",
            {"grain_approx": 999.0},
            date,
            0,
            0.4,
        )
        skipped = _grain_packs(caravan.dispatch_caravans(world, date))
        self.assertEqual(skipped, [], "Избыток у назначения не отменил рейс")
        self.assertEqual(world.stats.get("caravan_skipped_surplus"), 1.0)
        self.assertEqual(
            world.barter_memory[GRAIN_KEY]["last_skip_reason"],
            "caravan_skipped_surplus",
        )

        world.clock.month = WINDOW_AFTER_MONTH
        later = world.clock.date
        resumed = _grain_packs(caravan.dispatch_caravans(world, later))
        self.assertEqual(
            len(resumed), 1, "После окна отчёта рейс не возобновился"
        )

    def test_bad_deal_memory_skips_next_trip(self) -> None:
        world = load_scenario(SCENARIO)
        world.clock.month = DISPATCH_MONTH
        world.barter_memory[GRAIN_KEY] = {
            "date": "Y1-M01",
            "carried": 9.0,
            "delivered": 1.8,
            "ratio": 0.2,
            "lost": 7.2,
        }
        skipped = _grain_packs(caravan.dispatch_caravans(world, world.clock.date))
        self.assertEqual(skipped, [], "Плохая сделка в памяти не отменила рейс")
        self.assertEqual(world.stats.get("caravan_skipped_bad_deal"), 1.0)
        self.assertEqual(
            world.barter_memory[GRAIN_KEY]["last_skip_reason"],
            "caravan_skipped_bad_deal",
        )

    def test_matter_conserved_and_state_hash_covers_memory(self) -> None:
        first = load_scenario(SCENARIO, seed=1729)
        second = load_scenario(SCENARIO, seed=1729)
        _run_months(first, 36)
        _run_months(second, 36)

        self.assertAlmostEqual(first.ledger.delta(first.total_matter()), 0.0, places=6)
        self.assertTrue(first.barter_memory, "За 36 месяцев память сделок пуста")
        self.assertIn(SALT_KEY, first.barter_memory)
        self.assertEqual(
            first.state_hash(), second.state_hash(), "state_hash не воспроизводится"
        )

        before = first.state_hash()
        first.barter_memory["probe->probe:grain"] = {
            "date": "Y1-M01",
            "carried": 1.0,
            "delivered": 0.5,
            "ratio": 0.5,
            "lost": 0.5,
        }
        self.assertNotEqual(
            before, first.state_hash(), "Память сделки не входит в state_hash"
        )


if __name__ == "__main__":
    unittest.main()
