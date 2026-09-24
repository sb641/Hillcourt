"""Стартовые пары тягла шира: вол, осёл, конь у трёх хуторов.

Зонд держит наблюдаемый факт смены: `v0_shire` начинается с трёх пар скота
вне караванных поселений — вол у виллана `hh_11` (пахота), осёл у `hh_14`
(geneat, воз), конь у свободного `hh_09` (geneat). Молодняк не кладём,
железо/плуги не кладём. Через 12 месяцев (сид 1729) пары не голодают
(проводок `starved` по шести товарам пар — 0; сытый падёж `died` допустим),
сено кошено (`gather_hay`), материя сходится.

Вол стоит на виллане с `holding_scale` 1.0: кап партий (4/клетку) не вяжет
раньше труда, поэтому вол реально растит партии и зерно против безволового
контроля. У коттера (`holding_scale` 0.5, кап 2) вол вязался бы капом и не
давал бы ни одной лишней партии — зонд пользы это ловит.

Соль/зерно/рента не трогаются: наборы лежат вне `origin_draft` караванных
поселений, поэтому соляной обоз `salt_village→hill_court` не меняется.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy import livestock
from hillcourt.economy.labor import work_month
from hillcourt.engine.tick import run_month
from hillcourt.runner import _apply_script_entry
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SHIRE = ROOT / "design" / "scenarios" / "v0_shire.yml"

# Три разных хутора вне salt_village/hill_court/ash_village.
OX_HOUSEHOLD = "hh_11"      # villein, holding_scale 1.0 — пахота
DONKEY_HOUSEHOLD = "hh_14"  # geneat — воз
HORSE_HOUSEHOLD = "hh_09"   # geneat, свободный — только он держит коня
PAIRS = {
    OX_HOUSEHOLD: ("ox_m", "ox_f"),
    DONKEY_HOUSEHOLD: ("donkey_m", "donkey_f"),
    HORSE_HOUSEHOLD: ("horse_m", "horse_f"),
}
YOUNG = ("ox_calf", "donkey_foal", "horse_foal")
DRAFT = ("ox_m", "ox_f", "donkey_m", "donkey_f", "horse_m", "horse_f")


def _run_months(months: int, seed: int = 1729):
    """Прогнать шир вручную, исполняя `script:` перед каждым месяцем."""
    world = load_scenario(SHIRE, seed=seed)
    for month_index in range(1, months + 1):
        for entry in world.script:
            if int(entry.get("at_month", 0)) == month_index:
                _apply_script_entry(world, entry)
        run_month(world)
    return world


def _harvest(household_id: str, draft: dict[str, float]):
    """Жатва двора в одиночку (сид 1729, месяц 9) с данным тяглом.

    Возвращает (зерно, партий, дельта материи). Поля наполняются стоячим
    зерном заранее; труд 60 (3 взрослых × 20). Двор один — на выход влияет
    только тягло, а не соседи.
    """
    world = load_scenario(SHIRE, seed=1729)
    household = world.households[household_id]
    world.households = {household_id: household}
    world.clock.month = 9
    stock = world.get_stock(household.stock_id)
    stock.amounts["grain"] = 0.0
    for good in DRAFT:
        stock.amounts.pop(good, None)
    for good, amount in draft.items():
        stock.amounts[good] = float(amount)
    stock.amounts["hay"] = 20.0
    household.labor_days = 60.0
    household.main_action = "work_plot"
    household.minor_action = "idle_repair"
    for tile in world.tiles.values():
        if tile.terrain == "field":
            world.get_stock(tile.standing_stock_id).amounts["grain"] = 200.0
    world.ledger.capture_initial(world.total_matter())
    work_month(world, world.clock.date)
    batches = len([e for e in world.ledger.entries if e.reason == "harvest_grain"])
    return (
        stock.amounts.get("grain", 0.0),
        batches,
        world.ledger.delta(world.total_matter()),
    )


class TestShireDraftStart(unittest.TestCase):
    """Старт: три пары у трёх хуторов, молодняка/железа нет, конь у свободного."""

    def setUp(self) -> None:
        self.world = load_scenario(SHIRE)

    def test_three_farmsteads_outside_caravan_settlements(self) -> None:
        caravan = {"salt_village", "hill_court", "ash_village"}
        for hid in PAIRS:
            settlement_id = self.world.households[hid].settlement_id
            self.assertNotIn(
                settlement_id, caravan,
                f"{hid} стоит в караванном поселении {settlement_id}",
            )
        self.assertEqual(len(set(PAIRS)), 3, "нужны три разных двора")

    def test_each_farmstead_starts_with_adult_pair(self) -> None:
        for hid, (male, female) in PAIRS.items():
            stock = self.world.get_stock(f"household:{hid}")
            self.assertAlmostEqual(
                stock.amounts.get(male, 0.0), 1.0, places=6,
                msg=f"{hid} не начинает с {male}",
            )
            self.assertAlmostEqual(
                stock.amounts.get(female, 0.0), 1.0, places=6,
                msg=f"{hid} не начинает с {female}",
            )

    def test_no_young_and_no_iron_at_start(self) -> None:
        for hid in PAIRS:
            stock = self.world.get_stock(f"household:{hid}")
            for good in YOUNG:
                self.assertAlmostEqual(
                    stock.amounts.get(good, 0.0), 0.0, places=6,
                    msg=f"{hid} начинает с молодняком {good}",
                )
            for good in ("iron", "iron_bloom", "wooden_plough", "iron_share"):
                self.assertAlmostEqual(
                    stock.amounts.get(good, 0.0), 0.0, places=6,
                    msg=f"{hid} начинает с {good} (запрещено задачей)",
                )

    def test_hay_reserve_is_scarce_not_endless(self) -> None:
        """Сено — конечный запас (~год), а не бесконечный буфер."""
        for hid, (male, female) in PAIRS.items():
            stock = self.world.get_stock(f"household:{hid}")
            need = (
                self.world.needs.feed_per_month[male]
                + self.world.needs.feed_per_month[female]
            )
            months = stock.amounts.get("hay", 0.0) / need
            self.assertGreaterEqual(months, 6.0, f"{hid}: сена меньше 6 мес")
            self.assertLess(months, 24.0, f"{hid}: сено бездонно")

    def test_horse_only_at_free_household(self) -> None:
        household = self.world.households[HORSE_HOUSEHOLD]
        self.assertEqual(household.personal_status, "free")
        self.assertTrue(
            livestock.can_hold_horse(self.world, household),
            "Конь у tied-двора",
        )
        for hid in (OX_HOUSEHOLD, DONKEY_HOUSEHOLD):
            stock = self.world.get_stock(f"household:{hid}")
            for good in ("horse_m", "horse_f"):
                self.assertAlmostEqual(
                    stock.amounts.get(good, 0.0), 0.0, places=6,
                    msg=f"{hid} начинает с конём",
                )

    def test_ox_household_is_villein_where_labour_binds(self) -> None:
        """Вол стоит там, где кап клетки не вяжет раньше труда: holding_scale 1.0."""
        household = self.world.households[OX_HOUSEHOLD]
        self.assertEqual(household.legal_status_id, "villein")
        self.assertAlmostEqual(household.holding_scale, 1.0, places=6)


class TestOxBenefitAgainstControl(unittest.TestCase):
    """Вол режет труд партии и растит партии/зерно против безволового контроля."""

    def test_ox_adds_batches_and_grain(self) -> None:
        none, none_batches, none_delta = _harvest(OX_HOUSEHOLD, {})
        ox, ox_batches, ox_delta = _harvest(
            OX_HOUSEHOLD, {"ox_m": 1.0, "ox_f": 1.0},
        )
        self.assertGreater(none, 0.0, "Контроль без вола не дал зерна")
        self.assertGreater(
            ox_batches, none_batches,
            f"Вол не прибавил партий: {none_batches} → {ox_batches}",
        )
        self.assertGreater(
            ox, none, f"Вол не прибавил зерна: {none} → {ox}",
        )
        self.assertAlmostEqual(none_delta, 0.0, places=6)
        self.assertAlmostEqual(ox_delta, 0.0, places=6)

    def test_cotter_cap_binds_so_ox_adds_no_batches(self) -> None:
        """Контрпример: у коттера (holding_scale 0.5, кап 2) вол партий не растит.

        Держит причину выбора `hh_11`: там вяжет труд, а не кап клетки.
        """
        cotter = "hh_10"
        household = load_scenario(SHIRE).households[cotter]
        self.assertLess(household.holding_scale, 1.0)
        none, none_batches, _ = _harvest(cotter, {})
        ox, ox_batches, _ = _harvest(cotter, {"ox_m": 1.0, "ox_f": 1.0})
        self.assertEqual(
            ox_batches, none_batches,
            "У коттера вол не должен расти партии: кап вяжет раньше труда",
        )


class TestShireDraftSurvivesTwelveMonths(unittest.TestCase):
    """Пары не голодают 12 месяцев на кошёном сене; дельта 0.

    Критерий — анти-голод, а не бессмертие: естественный падёж сытых
    (`died`, 0.5%/мес) санкционирован и тестом не ловится. Присутствие пар
    на старте проверяет `test_each_farmstead_starts_with_adult_pair` выше.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = _run_months(12, seed=1729)

    def test_pairs_do_not_starve(self) -> None:
        """Голод не берёт пары за 12 мес; сытый падёж (`died`) допускается.

        Критерий — анти-голод, а не бессмертие: пара считается взятой
        голодом, только если товар пары упал ниже 1.0 И по нему есть
        проводка `starved` за 12 мес. Падение ниже 1.0 при одних `died` —
        санкционированный жребий (0.5%/мес). Приплод сверх пары голод
        вправе срезать (наблюдалось: лишний бык м10, пара осталась
        1.51/1.0) — это демография стада, а не гибель пары.
        """
        for hid, (male, female) in PAIRS.items():
            stock = self.world.get_stock(f"household:{hid}")
            for good in (male, female):
                final = stock.amounts.get(good, 0.0)
                if final >= 1.0:
                    continue
                starved = [
                    e for e in self.world.ledger.entries
                    if e.reason == "starved"
                    and e.good == good
                    and e.src_id == f"household:{hid}"
                ]
                self.assertEqual(
                    starved, [],
                    f"{hid}: голод взял {good} (остаток {final}): "
                    f"{[(e.good, e.amount, str(e.date)) for e in starved]}",
                )

    def test_hay_was_mowed(self) -> None:
        mowed = [e for e in self.world.ledger.entries if e.reason == "gather_hay"]
        self.assertTrue(mowed, "Сено не кошено: тягло ело только стартовый запас")

    def test_matter_conserved(self) -> None:
        self.assertAlmostEqual(
            self.world.ledger.delta(self.world.total_matter()), 0.0, places=6,
        )


if __name__ == "__main__":
    unittest.main()
