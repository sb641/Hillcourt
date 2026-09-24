"""H1: буфер еды, живая телега и гибель воза на обозе.

Зерно (еда) не вывозится ниже `need_buffer_months` месячной нужды живых
дворов origin: воз уходит только с излишком, иначе не уходит вовсе
(`caravan_below_buffer`). Телега изнашивается на рейсе и ломается, дойдя до
`cart_break_below`, после чего бонус пропадает до новой телеги. При провале
риска с вероятностью `catastrophe_chance` воз гибнет целиком: весь груз —
в стоячий сток клетки назначения, статус `lost`, `silence`-Report; всё это
делает `hazards.travel.resolve_pack_loss`. Материя (и масса телеги) цела.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy import caravan
from hillcourt.engine.tick import run_month
from hillcourt.ontology import SpawnRule
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_two_settlements.yml"
SEEDS = (1729, 42, 7, 99)
MONTHS = 60

HILL = "hill_court"
ASH = "ash_village"
ASH_STORES = "settlement:ash_village"
COURT_STORES = "settlement:hill_court"
ASH_TILE = "t_02_07"
GRAIN_RULE = "caravan_grain_to_ash"
GRAIN_KEY = "hill_court->ash_village:grain"
GRAIN_FROM_HILL = "caravan_grain_to_ash"
WASTE = "sink:waste"
DISPATCH_MONTH = 4
PROBE_RULE = "caravan_cart_probe"


class _ZeroRng:
    """Заглушка потока: любой бросок — 0 (риск и катастрофа срабатывают)."""

    def random(self) -> float:
        return 0.0


def _run_months(world, months: int) -> None:
    for _ in range(months):
        run_month(world)


def _set_hill_grain(world, total: float) -> None:
    """Обнулить зерно холма и выдать `total` одному стоку (буфер не от стока)."""
    for stock in caravan.caravan_source_stocks(world, HILL):
        stock.amounts["grain"] = 0.0
    caravan.caravan_source_stocks(world, HILL)[0].amounts["grain"] = total


def _grain_packs(dispatched) -> list:
    return [pack for pack in dispatched if pack.cargo.amounts.get("grain", 0.0) > 0.0]


def _delivered(world, good: str, dst: str) -> float:
    return sum(
        entry.amount
        for entry in world.ledger.entries
        if entry.kind == "transfer"
        and entry.good == good
        and entry.reason == "caravan_unload"
        and entry.dst_id == dst
    )


def _catastrophize(world):
    """Отправить зерновой обоз на 4-м месяце и погубить его целиком.

    Возвращает (pack, expected_scatter): груз после фуража — это ровно то,
    что `resolve_pack_loss` кладёт в стоячий сток. `world.rng.hazard` и
    `world.rng.world` заглушены нулём: риск и катастрофа срабатывают.
    """
    world.clock.month = DISPATCH_MONTH
    dispatched = _grain_packs(caravan.dispatch_caravans(world, world.clock.date))
    assert len(dispatched) == 1, "зерновой обоз не отправился"
    pack = dispatched[0]
    loaded = pack.cargo.amounts.get("grain", 0.0)
    expected = loaded - caravan.fodder_for_pack(world, pack)
    # Новый маршрут `find_path` обходит топь (2,4): переносим стартовую
    # опасность на клетку фактического пути, иначе катастрофы нет.
    hazard = world.hazards["haz_002"]
    world.tiles[hazard.tile_id].hazard_ids.remove(hazard.id)
    hazard.tile_id = pack.route[1]
    world.tiles[hazard.tile_id].hazard_ids.append(hazard.id)
    pack.eta_date = world.clock.date
    world.rng.hazard = _ZeroRng()
    world.rng.world = _ZeroRng()
    caravan.resolve_caravans(world, world.clock.date)
    return pack, expected


class TestCaravanH(unittest.TestCase):
    """Буфер еды, износ телеги, катастрофа обоза и живая торговля."""

    def test_food_buffer_blocks_below_and_loads_only_surplus(self) -> None:
        world = load_scenario(SCENARIO)
        rule = world.catalogs.spawn_rules[GRAIN_RULE]
        buffer = caravan.origin_food_buffer(world, rule, HILL)
        self.assertGreater(buffer, 0.0, "Буфер зерна холма нулевой")

        _set_hill_grain(world, buffer - 1.0)
        world.clock.month = DISPATCH_MONTH
        dispatched = caravan.dispatch_caravans(world, world.clock.date)
        self.assertEqual(_grain_packs(dispatched), [], "Воз ушёл ниже буфера")
        self.assertFalse(
            [pid for pid in world.packs if GRAIN_FROM_HILL in pid],
            "Воз ниже буфера создан",
        )
        self.assertEqual(world.stats.get("caravan_below_buffer"), 1.0)

        world.clock.month = DISPATCH_MONTH
        surplus = 4.0
        _set_hill_grain(world, buffer + surplus)
        dispatched = _grain_packs(caravan.dispatch_caravans(world, world.clock.date))
        self.assertEqual(len(dispatched), 1, "Воз с излишком не отправился")
        self.assertAlmostEqual(
            dispatched[0].cargo.amounts.get("grain", 0.0),
            surplus,
            places=6,
            msg="Погружен не только излишек выше буфера",
        )
        self.assertEqual(
            world.stats.get("caravan_below_buffer"), 1.0, "Счётчик буфера вырос зря"
        )

    def test_cart_wears_and_breaks_without_losing_matter(self) -> None:
        world = load_scenario(SCENARIO)
        world.get_stock(COURT_STORES).amounts["grain"] = 100000.0
        world.ledger.capture_initial(world.total_matter())
        for rule_id in list(world.catalogs.spawn_rules):
            rule = world.catalogs.spawn_rules[rule_id]
            if rule.target == "pack" and str(rule.params.get("kind", "")) == "caravan":
                del world.catalogs.spawn_rules[rule_id]
        world.catalogs.spawn_rules[PROBE_RULE] = SpawnRule(
            id=PROBE_RULE,
            name="Обоз с телегой",
            target="pack",
            kind="calendric",
            params={
                "kind": "caravan",
                "every_months": 1,
                "origin": HILL,
                "destination": ASH,
                "cargo": "grain",
                "cargo_amount": 1.0,
                "cart_bonus": True,
                "cart_break_below": 0.5,
                "cart_wear_per_trip": 0.05,
                "need_buffer_months": 0.0,
                "catastrophe_chance": 0.0,
            },
        )
        self.assertAlmostEqual(caravan.origin_cart_mass(world, HILL), 1.0, places=6)

        trips = 0
        for month in range(1, 12):
            world.clock.month = month
            packs = [
                pack
                for pack in caravan.dispatch_caravans(world, world.clock.date)
                if pack.id.startswith(f"caravan_{PROBE_RULE}")
            ]
            if not packs:
                continue
            trips += 1
            if trips == 1:
                self.assertAlmostEqual(
                    packs[0].cargo.amounts.get("grain", 0.0),
                    1.5,
                    places=6,
                    msg="С телегой ёмкость не ×1.5",
                )
        self.assertEqual(trips, 11, "Число рейсов телеги не совпало")
        self.assertEqual(world.stats.get("cart_broken"), 1.0, "Телега не сломалась")
        self.assertAlmostEqual(
            caravan.origin_cart_mass(world, HILL), 0.0, places=6
        )
        self.assertAlmostEqual(
            world.get_stock(WASTE).amounts.get("cart", 0.0), 1.0, places=6
        )
        self.assertFalse(caravan.origin_has_cart(world, HILL), "Сломанная телега в строю")
        rule = world.catalogs.spawn_rules[PROBE_RULE]
        self.assertAlmostEqual(caravan.caravan_capacity(rule, False), 0.6, places=6)
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )

    def test_catastrophe_puts_all_cargo_in_standing_stock(self) -> None:
        world = load_scenario(SCENARIO)
        standing = world.get_stock(f"tile:{ASH_TILE}")
        before = standing.total()
        pack, expected = _catastrophize(world)

        self.assertEqual(pack.status, "lost", "Катастрофа не погубила воз")
        self.assertAlmostEqual(pack.cargo.total(), 0.0, places=9)
        self.assertAlmostEqual(
            standing.total(),
            before + expected,
            places=6,
            msg="В стоячий сток легла не вся масса груза",
        )
        silences = [
            report
            for report in world.reports
            if report.source == "silence" and report.subject_id == ASH_TILE
        ]
        self.assertTrue(silences, "Катастрофа не породила silence-Report")
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )

    def test_catastrophe_memory_ratio_and_state_hash(self) -> None:
        first = load_scenario(SCENARIO, seed=1729)
        second = load_scenario(SCENARIO, seed=1729)
        _catastrophize(first)
        _catastrophize(second)

        memory = first.barter_memory.get(GRAIN_KEY)
        self.assertIsNotNone(memory, "Память сделки не записана после гибели")
        self.assertEqual(memory["delivered"], 0.0)
        self.assertEqual(memory["ratio"], 0.0)
        self.assertGreater(memory["lost"], 0.0, "Потерянный груз не попал в память")
        self.assertEqual(
            first.state_hash(),
            second.state_hash(),
            "state_hash не воспроизводится после катастрофы",
        )

    def test_sixty_months_trade_alive_over_four_seeds(self) -> None:
        numbers: dict[int, tuple[float, float, float, float]] = {}
        for seed in SEEDS:
            world = load_scenario(SCENARIO, seed=seed)
            _run_months(world, MONTHS)
            grain = _delivered(world, "grain", ASH_STORES)
            salt = _delivered(world, "salt", COURT_STORES)
            hunger = float(world.stats.get("hunger_months", 0.0))
            ratio = world.barter_memory.get(GRAIN_KEY, {}).get("ratio")
            self.assertGreater(grain, 0.0, f"seed {seed}: зерно не дошло до ясеня")
            self.assertGreater(salt, 0.0, f"seed {seed}: соль не дошла до замка")
            self.assertGreater(hunger, 0.0, f"seed {seed}: обмен-бог убил голод")
            self.assertAlmostEqual(
                world.ledger.delta(world.total_matter()),
                0.0,
                places=6,
                msg=f"seed {seed}: материя не сохранилась",
            )
            self.assertIsNotNone(ratio, f"seed {seed}: нет памяти зерновой сделки")
            numbers[seed] = (grain, salt, hunger, ratio)

        print(f"h1 grain->ash: { {s: round(v[0], 3) for s, v in numbers.items()} }")
        print(f"h1 salt->hill: { {s: round(v[1], 3) for s, v in numbers.items()} }")
        print(f"h1 ratio: { {s: round(v[3], 4) for s, v in numbers.items()} }")
        self.assertGreaterEqual(
            sum(1 for value in numbers.values() if value[3] < 1.0),
            2,
            "Доля доехавшего не ниже 1: фураж/износ/рассеяние не действуют",
        )


if __name__ == "__main__":
    unittest.main()
