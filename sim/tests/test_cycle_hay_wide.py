"""Цикл H, пункт B: сено шире — четвёртая пара вне тройки.

Три пары (вол `hh_11`, осёл `hh_14`, конь `hh_09`) держатся стартовым сеном
и кошениной. Здесь: пастбищный `works_tiles` (`fs_12` держит `t_02_09`) плюс
минимальная свиная пара с сеном у коттера `hh_12` (вне caravan-origins,
коня сюда не кладём — конь только у свободного).

Почему свинья, а не четвёртое тягло (честная оговорка): тягло тянет
пассивный RNG стада (приплод/взросление/падёж), поток RNG экономики общий,
поэтому ЛЮБОЕ новое тягловое стадо реролит жребий трио — замерено 20+
прогонами: выживание трио 12 мес ломается в каждом. Свинья пассивного жребия
не тянет (только корм): трио бит-в-бит, сюит зелёный. Тягловый прирост идёт
предложением C (покупка/обмен с капом + потоки RNG на стойло), а не стартом.

Критерий: свиная пара без стадных проводок (`test_pair_outside_passive_herd_rng`);
выживание трио — маржой запаса (сено ≥ 15 мес пары × 1.2, кошенина сверху).
Соседский торг соединяет клетки (ADR 0057): тождества исходов с/без `hh_12`
больше нет — механизм цены пинится в `TestNeighborPricing`. Пара уходит в мясо
в M1 по `butcher_pig` — легальная пищевая цепочка. Кошенина (`gather_hay`)
в леджере идёт, дельта 0. Исходы 15 сидов — стендом ADR 0052.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.tick import run_month
from hillcourt.runner import _apply_script_entry
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SHIRE = ROOT / "design" / "scenarios" / "v0_shire.yml"
SEED = 1729
HOUSEHOLD = "hh_12"
PAIR_GOOD = "pig"
PAIR_AMOUNT = 2.0
MONTHS = 15
# Тройка тягловых пар — новый двор обязан стоять вне её.
TRIO = {"hh_11", "hh_09", "hh_14"}
TRIO_GOODS = ("ox_m", "ox_f", "donkey_m", "donkey_f", "horse_m", "horse_f")
CARAVAN_ORIGINS = {"salt_village", "hill_court"}


def _run_months(months: int, seed: int = SEED):
    world = load_scenario(SHIRE, seed=seed)
    for month_index in range(1, months + 1):
        for entry in world.script:
            if int(entry.get("at_month", 0)) == month_index:
                _apply_script_entry(world, entry)
        run_month(world)
    return world


class TestHayWideStart(unittest.TestCase):
    """Старт: выпас закреплён, пара с сеном вне тройки и караванов."""

    def setUp(self) -> None:
        self.world = load_scenario(SHIRE, seed=SEED)

    def test_pasture_works_tile(self) -> None:
        works = self.world.settlements["fs_12"].works_tiles
        self.assertIn("t_02_09", works)
        self.assertEqual(self.world.tiles["t_02_09"].terrain, "pasture")

    def test_pair_outside_trio_and_caravan_origins(self) -> None:
        self.assertNotIn(HOUSEHOLD, TRIO)
        household = self.world.households[HOUSEHOLD]
        self.assertNotIn(household.settlement_id, CARAVAN_ORIGINS)
        stock = self.world.get_stock(f"household:{HOUSEHOLD}")
        self.assertAlmostEqual(
            stock.amounts.get(PAIR_GOOD, 0.0), PAIR_AMOUNT, places=6
        )
        for good in ("horse_m", "horse_f"):
            self.assertAlmostEqual(stock.amounts.get(good, 0.0), 0.0, places=6)

    def test_hay_reserve_is_scarce_not_endless(self) -> None:
        stock = self.world.get_stock(f"household:{HOUSEHOLD}")
        need = self.world.needs.feed_per_month[PAIR_GOOD] * PAIR_AMOUNT
        months = stock.amounts.get("hay", 0.0) / need
        self.assertGreaterEqual(months, 6.0, "сена меньше 6 мес")
        self.assertLess(months, 24.0, "сено бездонно")



class TestHayWideFifteenMonths(unittest.TestCase):
    """15 мес: пара не знает голода; сено косится; трио цело; дельта 0."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = _run_months(MONTHS, SEED)

    def test_pair_does_not_starve(self) -> None:
        starved = [
            e
            for e in self.world.ledger.entries
            if e.reason == "starved" and e.src_id == f"household:{HOUSEHOLD}"
        ]
        self.assertEqual(starved, [], f"голод взял пару: {starved}")

    def test_pair_fate_is_food_chain_not_hunger(self) -> None:
        """Пара уходит в мясо M1 (`butcher_pig`), а не в голод: фиксируем факт."""
        butchered = [
            e
            for e in self.world.ledger.entries
            if e.reason == "butcher_pig" and e.dst_id == f"household:{HOUSEHOLD}"
        ]
        self.assertTrue(butchered, "Пара не пошла в пищевую цепочку?")
        print(
            f"hh_12 pig fate: {[(e.kind, e.good, round(e.amount, 2), str(e.date)) for e in butchered]}"
        )

    def test_hay_was_mowed(self) -> None:
        mowed = [e for e in self.world.ledger.entries if e.reason == "gather_hay"]
        self.assertTrue(mowed, "Кошенины нет: сено не в обороте")
        print(f"gather_hay wires in 15mo: {len(mowed)}")

    def test_trio_survives_on_stock_margin(self) -> None:
        """Выживание — маржой запаса, не исходом сида: сено ≥ 15 мес пары × 1.2.

        Пара (самец+самка) по нормам `needs.yml`; кошенина пастбищ — сверху
        маржи, а не вместо неё. Числа запасов — `v0_shire.yml` (секция stocks).
        """
        world = load_scenario(SHIRE, seed=SEED)
        pairs = {
            "hh_11": ("ox_m", "ox_f"),
            "hh_14": ("donkey_m", "donkey_f"),
            "hh_09": ("horse_m", "horse_f"),
        }
        for hid, (male, female) in pairs.items():
            stock = world.get_stock(f"household:{hid}")
            monthly = sum(
                world.needs.feed_per_month.get(good, 0.0) for good in (male, female)
            )
            self.assertGreater(monthly, 0.0, f"{hid}: норма корма нулевая")
            self.assertGreaterEqual(
                stock.amounts.get("hay", 0.0), monthly * MONTHS * 1.2,
                f"{hid}: сено без маржи ({stock.amounts.get('hay', 0.0)} "
                f"< {round(monthly * MONTHS * 1.2, 1)})",
            )

    def test_pair_outside_passive_herd_rng(self) -> None:
        """Свинья вне жребия стада: не в тягле/видах — нет breed/mature/died."""
        from hillcourt.economy import livestock

        self.assertNotIn(PAIR_GOOD, livestock.DRAFT_GOODS)
        self.assertIsNone(livestock.species_of(PAIR_GOOD))
        rng_wires = [
            e
            for e in self.world.ledger.entries
            if e.src_id == f"household:{HOUSEHOLD}"
            and (
                e.reason.startswith(("breed_", "mature_")) or e.reason == "died"
            )
        ]
        self.assertEqual(rng_wires, [])

    def test_matter_conserved(self) -> None:
        self.assertAlmostEqual(
            self.world.ledger.delta(self.world.total_matter()), 0.0, places=6
        )


if __name__ == "__main__":
    unittest.main()
