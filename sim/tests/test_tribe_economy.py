"""Экономика племени: оброк `levy`, корщина и потенциал вызова (ADR 0064/0066/0140/0149).

Проверки:
  * `allied` (союз, но НЕ солидарность) → повинностей **0**: оброк при `allied`
    отменён ADR 0140, это и была регрессия юриста;
  * `vassal` (солидарность/подчинение) → повинность `levy` заведена, оплата идёт
    в амбар корня; при пустом стоке зерна — `arrears` растёт (не падёж, не телепорт);
  * `independent` → повинности нет;
  * пустой двор (нет живых людей) → оброк не берётся, долг не растёт;
  * вызов по союзу (`MUSTER_STANCE`) не отнят сужением оброка: потенциал
    честен — `war_kit` в стоках дворов + живые взрослые, потолок `muster_kits`;
    ни вызова, ни `send_sally`, ни нормы тэна;
  * корщина племени в солидарности настоящая (ADR 0149 п. 1), оплата отменяет
    начисление за период (ADR 0149 п. 2), а двойного взыскания нет.
Материя — только перевод существующей фазы (дельта 0).
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy import manor as manor_economy
from hillcourt.economy.manor import manor_month
from hillcourt.economy.needs import ADULT_LABOR_DAYS, member_counts
from hillcourt.economy.tribe import (
    tribe_can_muster,
    tribe_households,
    tribe_holding_tiles,
    tribe_muster_kits,
    tribe_tribute_month,
)
from hillcourt.engine.tick import phase_obligations, run_month
from hillcourt.legal.calendar import seasonal_labor_days
from hillcourt.legal.obligations import is_payable_duty
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_native_village.yml"
EPSILON = 1e-9
# Генеат (пресет племени в солидарности, ADR 0156 п. 2) барщит по календарю
# вызова: `labor_mod.geneat.callout` = 1 в месяцах 6-9, в остальных 0. Тесты про
# оплату вместо отработки обязаны идти в месяц, когда корщина реально есть, иначе
# они проверяют ноль (ADR 0155).
CORVEE_MONTH = 6


def _yards(world, tribe, why: str) -> list:
    """Дворы племени — или отказ. Пустой список здесь означает сломанную фикстуру.

    Без такой проверки `for household in yards` просто не исполняется, тест
    остаётся зелёным и не проверяет ничего: у племени без дворов оброк, корщина
    и потенциал вызова становятся неразличимы от «нуля у всех» (ADR 0155: проверка
    обязана быть неправдоподобной). Отказ поднимаем явно, а не `assert`, чтобы
    `python -O` его не вырезал.
    """
    households = tribe_households(world, tribe)
    if not households:
        raise AssertionError(f"У племени нет дворов — {why}")
    return households


def _world(stance: str = "allied"):
    world = load_scenario(SCENARIO, seed=1729)
    tribe = world.tribes["tribe_village"]
    tribe.stance = stance
    tribe.tribute_grain = 0.6
    tribe.muster_kits = 2.0
    return world, tribe


def _solidarity(rate: float = 0.6, grain: float = 10.0):
    """Племя в солидарности; `grain` — зерно в каждом племенном дворе.

    Месяц корщины (см. `CORVEE_MONTH`): генеат в солидарности должен реально
    долженеть корщину, иначе тесты проверяют ноль.
    """
    world, tribe = _world("vassal")
    tribe.tribute_grain = rate
    world.clock.month = CORVEE_MONTH
    for household in _yards(world, tribe, "фикстура солидарности без дворов"):
        world.get_stock(household.stock_id).amounts["grain"] = grain
    return world, tribe


def _monthly_duty_days(world, tribe, months: int) -> list[float]:
    """Корщина племени по месяцам — доказательство, что месяц не пустой."""
    seen: list[float] = []
    for _ in range(months):
        run_month(world)
        seen.extend(o.duty_days for o in _corvee_of(world, tribe))
    return seen


def _corvee_of(world, tribe) -> list:
    """Корщина племени — по одной `labor_duty` на каждый племенный двор."""
    yards = {household.id for household in tribe_households(world, tribe)}
    return [
        obligation
        for obligation in world.obligations.values()
        if obligation.kind == "labor_duty" and obligation.household_id in yards
    ]


def _root_grain(world) -> float:
    return world.get_stock("settlement:hill_court").amounts.get("grain", 0.0)


def _commuted(world) -> float:
    return sum(
        entry.amount
        for entry in world.ledger.entries
        if entry.reason == "corvee_commuted" and entry.good == "grain"
    )


class TestTribeTribute(unittest.TestCase):
    """Оброк 0.6/мес на двор — только в солидарности (ADR 0140)."""

    def test_allied_without_solidarity_has_no_obligation(self) -> None:
        """Регрессия юриста: при `allied` повинностей быть не должно."""
        world, tribe = _world("allied")
        households = tribe_households(world, tribe)
        self.assertTrue(households, "Племя без двора в сценарии")
        self.assertEqual(tribe_tribute_month(world, world.clock.date), [])
        self.assertEqual(
            [o for o in world.obligations.values() if o.kind == "levy"], []
        )
        for household in households:
            self.assertEqual(household.obligation_ids, [], "Союзник не платит")
        world.ledger.capture_initial(world.total_matter())
        phase_obligations(world)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_solidarity_levy_pays_to_root_barn(self) -> None:
        world, tribe = _world("vassal")
        households = tribe_households(world, tribe)
        self.assertTrue(households, "Племя без двора в сценарии")
        tribute = tribe_tribute_month(world, world.clock.date)
        self.assertEqual(len(tribute), len(households))
        for household in households:
            stock = world.get_stock(household.stock_id)
            stock.amounts["grain"] = 10.0
        before = {
            hid: world.get_stock(f"household:{hid}").amounts["grain"]
            for hid in sorted(world.households)
        }
        paid_before = {o.id: o.paid_total for o in tribute}
        root_before = _root_grain(world)
        corvee_before = {o.id: o.corvee_days for o in _corvee_of(world, tribe)}
        world.ledger.capture_initial(world.total_matter())
        phase_obligations(world)
        paid = sum(o.paid_total - paid_before[o.id] for o in tribute)
        self.assertAlmostEqual(paid, 0.6 * len(households), places=6)
        rent_grain = sum(
            e.amount for e in world.ledger.entries
            if e.reason == "rent" and e.good == "grain"
        )
        self.assertAlmostEqual(
            _root_grain(world),
            root_before + rent_grain + _commuted(world),
            places=6,
            msg="Оброк не в амбаре корня",
        )
        for hid, was in before.items():
            if tribe_households(world, tribe) and any(
                h.id == hid for h in tribe_households(world, tribe)
            ):
                self.assertAlmostEqual(
                    world.get_stock(f"household:{hid}").amounts["grain"],
                    was - 0.6 - 0.6,
                    places=6,
                    msg=f"{hid}: оброк и корщина не списаны с племенного двора",
                )
        self.assertEqual(
            [round(o.corvee_days, 6) for o in _corvee_of(world, tribe)],
            [round(v, 6) for v in corvee_before.values()],
            "Оплатив корщину, племя получило её отработкой сверху",
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_unpaid_tribute_grows_arrears_not_hunger(self) -> None:
        world, tribe = _world("vassal")
        households = _yards(world, tribe, "неоплата оброка проверяется по дворам")
        for household in households:
            world.get_stock(household.stock_id).amounts["grain"] = 0.0
        tribute = tribe_tribute_month(world, world.clock.date)
        world.ledger.capture_initial(world.total_matter())
        phase_obligations(world)
        for obligation in tribute:
            self.assertGreater(obligation.arrears, 0.0, "Неоплата не в долг")
            self.assertAlmostEqual(obligation.paid_total, 0.0, places=6)
        for household in households:
            self.assertEqual(household.hunger_days, 0, "Оброк превратился в голод")
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_independent_has_no_obligation(self) -> None:
        world, tribe = _world("independent")
        households = _yards(world, tribe, "независимое племя проверяется по дворам")
        self.assertEqual(tribe_tribute_month(world, world.clock.date), [])
        for household in households:
            self.assertEqual(
                [oid for oid in household.obligation_ids], [],
                "У независимого племени появилась повинность",
            )
        self.assertEqual(
            [o for o in world.obligations.values() if o.kind == "levy"], []
        )

    def test_empty_household_pays_nothing(self) -> None:
        world, tribe = _world("vassal")
        households = _yards(world, tribe, "пустой двор берётся из первого")
        empty = households[0]
        for pid in list(empty.member_ids):
            world.persons.pop(pid, None)
        empty.member_ids = []
        tribute = tribe_tribute_month(world, world.clock.date)
        self.assertNotIn(
            empty.id, [o.household_id for o in tribute],
            "С пустого двора племени взяли оброк",
        )
        self.assertEqual(empty.obligation_ids, [])
        world.ledger.capture_initial(world.total_matter())
        phase_obligations(world)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_stance_flip_drops_levy_growth(self) -> None:
        world, tribe = _world("vassal")
        tribute = tribe_tribute_month(world, world.clock.date)
        for obligation in tribute:
            obligation.arrears = 1.2
        tribe.stance = "independent"
        self.assertEqual(tribe_tribute_month(world, world.clock.date), [])
        for obligation in tribute:
            self.assertNotIn(
                obligation.id, world.obligations,
                "Договор снят, а повинность племени осталась в мире",
            )
        world.ledger.capture_initial(world.total_matter())
        phase_obligations(world)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


class TestTribeCorveeCommutation(unittest.TestCase):
    """Корщина племени в солидарности и оплата вместо отработки (ADR 0149)."""

    def test_no_corvee_before_solidarity(self) -> None:
        """До солидарности у племени нет ни корщины, ни платежей (ADR 0149 п. 5)."""
        for stance in ("independent", "allied"):
            with self.subTest(stance=stance):
                world, tribe = _world(stance)
                tribe_tribute_month(world, world.clock.date)
                self.assertEqual(_corvee_of(world, tribe), [], stance)
                world.ledger.capture_initial(world.total_matter())
                phase_obligations(world)
                self.assertEqual(_commuted(world), 0.0, stance)
                self.assertAlmostEqual(
                    world.ledger.delta(world.total_matter()), 0.0, places=6
                )

    def test_solidarity_grants_real_corvee(self) -> None:
        """ADR 0149 п. 1: в солидарности корщина настоящая, как у любого двора."""
        world, tribe = _solidarity()
        households = _yards(world, tribe, "корщина выдаётся на каждый двор")
        tribe_tribute_month(world, world.clock.date)
        corvee = _corvee_of(world, tribe)
        self.assertEqual(len(corvee), len(households))
        for obligation in corvee:
            self.assertEqual(obligation.basis, "duty")
            self.assertEqual(obligation.due_good, "grain")
            self.assertAlmostEqual(obligation.due_amount, 0.6, places=6)
            self.assertTrue(
                is_payable_duty(obligation),
                "Корщину племени оплатить нельзя — а ADR 0149 разрешает",
            )

    def test_paid_in_lieu_suppresses_corvee_accrual(self) -> None:
        """ADR 0149 п. 2: оплата отменяет начисление корщины за период."""
        world, tribe = _solidarity()
        households = _yards(world, tribe, "оплата корщины сверяется по дворам")
        before = {
            household.id: world.get_stock(household.stock_id).amounts["grain"]
            for household in households
        }
        root_before = _root_grain(world)
        tribe_tribute_month(world, world.clock.date)
        corvee = _corvee_of(world, tribe)
        self.assertTrue(corvee)
        world.ledger.capture_initial(world.total_matter())
        phase_obligations(world)
        for obligation in corvee:
            self.assertGreater(obligation.duty_days, 0.0, "Долг племени — не ноль")
            self.assertAlmostEqual(
                obligation.corvee_days, 0.0, places=6,
                msg="Оплата не отменила начисление корщины",
            )
            self.assertAlmostEqual(obligation.arrears, 0.0, places=6)
            self.assertAlmostEqual(obligation.paid_total, 0.6, places=6)
        self.assertAlmostEqual(
            _commuted(world), 0.6 * len(households), places=6,
            msg="Зерно за корщину не списано переводом",
        )
        root_growth = sum(
            entry.amount for entry in world.ledger.entries
            if entry.reason in {"rent", "corvee_commuted"} and entry.good == "grain"
        )
        self.assertAlmostEqual(
            _root_grain(world) - root_before, root_growth, places=6,
            msg="Платежи племени ушли не в амбар корня",
        )
        for household in households:
            self.assertAlmostEqual(
                world.get_stock(household.stock_id).amounts["grain"],
                before[household.id] - 0.6 - 0.6,
                places=6,
                msg="Оброк и корщина — две повинности, обе списаны",
            )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_worked_corvee_pays_nothing_but_levy_still_paid(self) -> None:
        """Отработало: корщина выросла, выплат 0, оброк платится (ADR 0149 п. 3)."""
        world, tribe = _solidarity(grain=0.6)
        households = _yards(world, tribe, "отработавшая корщина сверяется по дворам")
        tribute = tribe_tribute_month(world, world.clock.date)
        corvee = _corvee_of(world, tribe)
        world.ledger.capture_initial(world.total_matter())
        phase_obligations(world)
        for obligation in corvee:
            self.assertGreater(obligation.duty_days, 0.0)
            self.assertAlmostEqual(
                obligation.corvee_days, obligation.duty_days, places=6,
                msg="Отработанная корщина не записана",
            )
            self.assertAlmostEqual(obligation.paid_total, 0.0, places=6)
        self.assertEqual(_commuted(world), 0.0, "Не платив, племе нечего списать")
        for obligation in tribute:
            self.assertAlmostEqual(obligation.paid_total, 0.6, places=6)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)
        self.assertEqual([h.hunger_days for h in households if h.hunger_days], [])

    def test_idle_tribe_grows_corvee_and_arrears(self) -> None:
        """ADR 0149 п. 5: не сделало ничего — и корщина, и долг растут."""
        world, tribe = _solidarity(grain=0.0)
        households = _yards(world, tribe, "простой племени проверяется по дворам")
        tribe_tribute_month(world, world.clock.date)
        corvee = _corvee_of(world, tribe)
        world.ledger.capture_initial(world.total_matter())
        phase_obligations(world)
        for obligation in corvee:
            self.assertGreater(obligation.corvee_days, 0.0)
            self.assertGreater(obligation.arrears, 0.0)
            self.assertAlmostEqual(obligation.paid_total, 0.0, places=6)
        for household in households:
            self.assertEqual(household.hunger_days, 0, "Повинность стала голодом")
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_cannot_work_and_pay_for_same_period(self) -> None:
        """Платить второй месяц — не значит «отработать сверху» (ADR 0149 п. 2)."""
        world, tribe = _solidarity(grain=1000.0)
        world.ledger.capture_initial(world.total_matter())
        seen = _monthly_duty_days(world, tribe, 8)
        self.assertGreater(
            max(seen), 0.0, "Тест проверял бы нулевую корщину (ADR 0155)"
        )
        corvee = _corvee_of(world, tribe)
        self.assertTrue(corvee)
        for obligation in corvee:
            self.assertAlmostEqual(
                obligation.corvee_days, 0.0, places=6,
                msg="Оплата и отработка сложились в двойное взыскание",
            )
            self.assertGreater(obligation.paid_total, 1.2)
            self.assertAlmostEqual(obligation.arrears, 0.0, places=6)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_zero_rate_keeps_corvee_free_and_unpayable(self) -> None:
        """Ставки нет — платить нечем: корщина бесплатна, как у любого двора."""
        world, tribe = _solidarity(rate=0.0)
        households = _yards(world, tribe, "нулевая ставка проверяется по дворам")
        for household in households:
            world.get_stock(household.stock_id).amounts["grain"] = 10.0
        tribe_tribute_month(world, world.clock.date)
        corvee = _corvee_of(world, tribe)
        self.assertEqual(len(corvee), len(households), "Корщина в солидарности есть")
        self.assertEqual([o for o in world.obligations.values() if o.kind == "levy"], [])
        world.ledger.capture_initial(world.total_matter())
        phase_obligations(world)
        for obligation in corvee:
            self.assertFalse(is_payable_duty(obligation))
            self.assertAlmostEqual(obligation.paid_total, 0.0, places=6)
            self.assertGreater(obligation.corvee_days, 0.0)
        self.assertEqual(_commuted(world), 0.0)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_loss_of_solidarity_drops_corvee_and_debt(self) -> None:
        """ADR 0149: сняли солидарность — корщины и долга нет."""
        world, tribe = _solidarity(grain=0.0)
        tribe_tribute_month(world, world.clock.date)
        corvee = _corvee_of(world, tribe)
        self.assertTrue(corvee)
        for obligation in corvee:
            obligation.arrears = 1.2
        tribe.stance = "allied"
        self.assertEqual(tribe_tribute_month(world, world.clock.date), [])
        for obligation in corvee:
            self.assertNotIn(
                obligation.id, world.obligations,
                "Союзник остался с корщиной племени",
            )
        for household in _yards(world, tribe, "племя вне солидарности не должно быть должником"):
            self.assertEqual(household.obligation_ids, [], "Племя осталось должником")
        world.ledger.capture_initial(world.total_matter())
        phase_obligations(world)
        self.assertEqual(_commuted(world), 0.0)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_same_seed_same_result_with_commutation(self) -> None:
        """Один seed — один результат, дельта материи 0."""
        hashes = []
        for _ in range(2):
            world, tribe = _solidarity(grain=1000.0)
            world.ledger.capture_initial(world.total_matter())
            seen = _monthly_duty_days(world, tribe, 8)
            self.assertGreater(
                max(seen), 0.0, "Тест проверял бы нулевую корщину (ADR 0155)"
            )
            hashes.append(world.state_hash())
            self.assertAlmostEqual(
                world.ledger.delta(world.total_matter()), 0.0, places=6
            )
            for obligation in _corvee_of(world, tribe):
                self.assertGreater(obligation.paid_total, 1.2)
                self.assertAlmostEqual(obligation.corvee_days, 0.0, places=6)
        self.assertEqual(hashes[0], hashes[1], "Тот же seed — разный мир")


class TestTribeSolidarityHoldings(unittest.TestCase):
    """Солидарность даёт племени наделы и книгу (ADR 0156)."""

    def test_no_holdings_and_no_book_before_solidarity(self) -> None:
        """ADR 0156 п. 1 + регрессия ADR 0138 п. 2: вне солидарности ничего."""
        for stance in ("independent", "allied"):
            with self.subTest(stance=stance):
                world, tribe = _world(stance)
                tribe_tribute_month(world, world.clock.date)
                for household in _yards(
                    world, tribe, f"вне солидарности дворов нет ({stance})"
                ):
                    self.assertEqual(household.legal_status_id, "free_landless")
                    self.assertEqual(household.land_relation, "landless")
                    self.assertIsNone(household.manor_id, "Племя в книге без солидарности")
                    self.assertEqual(household.obligation_ids, [])
                    self.assertEqual(
                        [
                            right.id for right in world.rights.values()
                            if right.holder_household_id == household.id
                            and right.kind != "common"
                        ],
                        [],
                        "У племени взяли наделы без солидарности",
                    )
                yards = _yards(world, tribe, f"племя вне книги ({stance})")
                for manor in world.manors.values():
                    for household in yards:
                        self.assertNotIn(household.id, manor.household_ids)
                self.assertEqual(_corvee_of(world, tribe), [])
                self.assertEqual(
                    [o for o in world.obligations.values() if o.kind == "levy"], []
                )

    def test_solidarity_grants_holding_book_and_geneat(self) -> None:
        """ADR 0156 п. 1-2: надел, книга и пресет `geneat`."""
        world, tribe = _solidarity()
        households = _yards(world, tribe, "наделы и книга выдаются дворам")
        tiles = tribe_holding_tiles(world, tribe)
        tribe_tribute_month(world, world.clock.date)
        self.assertTrue(tiles, "У племенной деревни нет земли")
        for household in households:
            self.assertEqual(household.legal_status_id, "geneat", household.id)
            self.assertEqual(household.land_relation, "tenement", household.id)
            self.assertEqual(household.obligation_bundle, "geneat_service")
            self.assertEqual(household.manor_id, "manor_hill", household.id)
            held = [
                right for right in world.rights.values()
                if right.holder_household_id == household.id and right.kind != "common"
            ]
            self.assertEqual(len(held), 1, f"{household.id}: надел не выдан")
            self.assertIn(held[0].tile_id, tiles)
            regime = world.catalogs.land_regimes.get(world.tiles[held[0].tile_id].regime_id)
            self.assertIsNotNone(regime)
            self.assertTrue(
                regime.feeds_household, "Надел не кормит двор (ADR 0010)"
            )
        for manor in world.manors.values():
            for household in households:
                self.assertIn(household.id, manor.household_ids)
        self.assertEqual(len(households), len(tiles), "Наделов больше, чем земли")

    def test_solidarity_corvee_is_nonzero_and_from_land(self) -> None:
        """Корщина племени ненулевая и приходит от наделов, а не от пресета."""
        world, tribe = _solidarity(grain=0.0)
        households = _yards(world, tribe, "корщина племени считается по дворам")
        tribe_tribute_month(world, world.clock.date)
        for household in households:
            self.assertGreater(
                seasonal_labor_days(world, household, CORVEE_MONTH), 0.0,
                f"{household.id}: генeat не барщит в месяц вызова",
            )
        corvee = _corvee_of(world, tribe)
        self.assertEqual(len(corvee), len(households))
        world.ledger.capture_initial(world.total_matter())
        phase_obligations(world)
        for obligation in corvee:
            self.assertGreater(obligation.corvee_days, 0.0)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_tribe_labor_reaches_demesne_pool_of_its_manor(self) -> None:
        """ADR 0156: труд племени идёт в пул домена книги (ADR 0010).

        Проверка обязана падать без наделов: сравниваем вклад племени с пулом
        того же месяца в том же мире без солидарности.
        """
        world, tribe = _solidarity()
        households = _yards(world, tribe, "труд племени уходит в пул домена")
        tribe_tribute_month(world, world.clock.date)
        with_household = _yards(world, tribe, "книга племени берётся у первого двора")[0]
        self.assertIsNotNone(with_household.manor_id, "Двор племени без книги")
        manor = world.manors[with_household.manor_id]
        duty = {
            household.id: seasonal_labor_days(world, household, CORVEE_MONTH)
            for household in households
        }
        self.assertTrue(any(days > 0.0 for days in duty.values()))
        world.ledger.capture_initial(world.total_matter())
        manor_month(world, world.clock.date)
        for household in households:
            adults, _, _ = member_counts(world, household)
            self.assertAlmostEqual(
                household.labor_days,
                adults * ADULT_LABOR_DAYS - duty[household.id],
                places=6,
                msg=f"{household.id}: труд не снят в домен — палец остался в руке",
            )
        with_tribe = manor.demesne_labor_filled

        control, control_tribe = _world("allied")
        control.clock.month = CORVEE_MONTH
        tribe_tribute_month(control, control.clock.date)
        control_manor = control.manors["manor_hill"]
        # **Правка 2026-09 (Implementer).** Здесь стояло
        # `control.total_matter() - control_ledger_before == 0.0` — РАЗНОСТЬ
        # СЫРОЙ суммы стоков. После ADR 0219 такая разность ложно краснеет на
        # законной внешней торговле: `manor_month` продаёт хлеб королевскому
        # двору (`sell_to_royal_court`) — 24.692 зерна `external_out` против
        # 3.16465 серебра `external_in`. Сырая сумма считает зерно и серебро
        # одной и той же массой, поэтому выходит −21.53, хотя сделка законна и
        # пара «зерно наружу ↔ серебро внутрь» сходится по каталожной цене
        # (24.692 × 0.128165 = 3.16465).
        #
        # И-1 по Конституции проверяется `Ledger.delta`, который ВЫЧИТАЕТ внешние
        # потоки (`ledger.py:149-156`): внешний приход и расход — законные
        # движение материи, а не её исчезновение. Контроль теперь считается тем же
        # способом, что и сторона солидарности выше (строка 565), иначе две
        # половины одного закона измерялись двумя разными мерками.
        control.ledger.capture_initial(control.total_matter())
        manor_month(control, control.clock.date)
        control_yards = _yards(control, control_tribe, "контроль без солидарности")
        for household in control_yards:
            adults, _, _ = member_counts(control, household)
            self.assertAlmostEqual(
                household.labor_days, adults * ADULT_LABOR_DAYS, places=6,
                msg="Без солидарности труд племени почему-то сняли в домен",
            )
        self.assertAlmostEqual(
            with_tribe - control_manor.demesne_labor_filled,
            sum(duty.values()),
            places=6,
            msg="Вклад племени в пул домена не равен его корщине",
        )
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6
        )
        self.assertAlmostEqual(
            control.ledger.delta(control.total_matter()), 0.0, places=6
        )

    def test_loss_of_solidarity_takes_holdings_book_and_preset(self) -> None:
        """ADR 0156 п. 5: потеря солидарности отбирает землю, книгу, пресет и долг."""
        world, tribe = _solidarity(grain=0.0)
        households = _yards(world, tribe, "потеря солидарности отбирает дворы")
        tribe_tribute_month(world, world.clock.date)
        corvee = _corvee_of(world, tribe)
        self.assertTrue(corvee)
        for household in households:
            self.assertEqual(household.legal_status_id, "geneat")
        world.ledger.capture_initial(world.total_matter())
        phase_obligations(world)
        self.assertTrue(any(o.arrears > 0.0 for o in corvee), "Долга не было")

        tribe.stance = "allied"
        self.assertEqual(tribe_tribute_month(world, world.clock.date), [])
        for household in households:
            self.assertEqual(
                household.legal_status_id, "free_landless", household.id
            )
            self.assertEqual(household.land_relation, "landless", household.id)
            self.assertIsNone(household.manor_id, "Книгу не отняли")
            self.assertEqual(household.obligation_ids, [], "Племя осталось должником")
            self.assertEqual(
                [
                    right.id for right in world.rights.values()
                    if right.holder_household_id == household.id
                    and right.kind != "common"
                ],
                [],
                "Надел не отобран",
            )
        for manor in world.manors.values():
            for household in households:
                self.assertNotIn(household.id, manor.household_ids)
        self.assertEqual(_corvee_of(world, tribe), [])
        self.assertEqual(
            [o for o in world.obligations.values() if o.kind == "levy"], []
        )
        world.ledger.capture_initial(world.total_matter())
        phase_obligations(world)
        self.assertEqual(_commuted(world), 0.0)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


class TestTribeMusterPotential(unittest.TestCase):
    """Потенциал вызова — данные: комплекты в руках + живые взрослые."""

    def test_kits_capped_by_muster_potential(self) -> None:
        world, tribe = _world("allied")
        households = _yards(world, tribe, "потолок вызова считается по комплектам")
        for household in households:
            world.get_stock(household.stock_id).amounts["war_kit"] = 3.0
        self.assertAlmostEqual(tribe_muster_kits(world, tribe), 2.0, places=6)
        for household in households:
            world.get_stock(household.stock_id).amounts["war_kit"] = 0.5
        self.assertAlmostEqual(tribe_muster_kits(world, tribe), 0.5 * len(households), places=6)

    def test_can_muster_needs_kits_and_adults(self) -> None:
        world, tribe = _world("allied")
        households = _yards(world, tribe, "потенциал вызова считается по дворам")
        self.assertFalse(tribe_can_muster(world, tribe), "Без комплектов дают вызов")
        for household in households:
            world.get_stock(household.stock_id).amounts["war_kit"] = 1.0
        self.assertTrue(tribe_can_muster(world, tribe), "Взрослые + комплект — не могут")
        for household in households:
            for pid in list(household.member_ids):
                person = world.persons.get(pid)
                if person is not None and person.age_class == "adult":
                    person.age_class = "child"
        self.assertFalse(tribe_can_muster(world, tribe), "Без взрослых дают вызов")

    def test_independent_gives_nothing(self) -> None:
        world, tribe = _world("independent")
        for household in _yards(world, tribe, "независимое племя не копит комплекты"):
            world.get_stock(household.stock_id).amounts["war_kit"] = 5.0
        self.assertAlmostEqual(tribe_muster_kits(world, tribe), 0.0, places=6)
        self.assertFalse(tribe_can_muster(world, tribe))

    def test_tribute_hook_runs_without_manor_month_side_effects(self) -> None:
        """Крюк оброка не трогает труд/паёк: hook = только повинности."""
        world, tribe = _world("vassal")
        labor_before = {
            hid: household.labor_days for hid, household in world.households.items()
        }
        tribute = tribe_tribute_month(world, world.clock.date)
        self.assertTrue(tribute)
        for hid, was in labor_before.items():
            self.assertAlmostEqual(
                world.households[hid].labor_days, was, places=6
            )
        self.assertIsNotNone(manor_economy)
