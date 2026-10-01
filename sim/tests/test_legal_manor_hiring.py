"""Legal-контракт найма через книгу манора."""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy.manor import _hire
from hillcourt.engine.manor import grant_tenement, revoke_tenure as engine_revoke_tenure
from hillcourt.engine.tick import phase_migrate, run_month
from hillcourt.legal.obligations import household_harvest_total, rent_amount_for
from hillcourt.runner import _apply_script_entry
from hillcourt.legal.actions import grant_tenure, revoke_tenure
from hillcourt.legal.manor import hire_out_allowed, is_in_manor_book
from hillcourt.legal.regimes import can_leave
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
BARONY = ROOT / "design" / "scenarios" / "v0_barony_100.yml"
NATIVE = ROOT / "design" / "scenarios" / "v0_native_village.yml"
STAND = ROOT / "design" / "scenarios" / "start_stand.yml"


class TestLegalManorHiring(unittest.TestCase):
    def test_household_outside_manor_book_has_no_hire_out(self) -> None:
        world = load_scenario(BARONY, seed=1729)
        household = world.households["hh_native_001"]
        manor = world.manors["manor_hill"]
        for other_id, other in world.households.items():
            if other_id != household.id:
                other.manor_id = None
        manor.household_ids = []
        household.manor_id = None
        before = world.stats.get("hired_days", 0.0)
        hired = _hire(world, manor, 0.0, 10.0, world.clock.date)
        self.assertFalse(is_in_manor_book(world, household))
        self.assertFalse(hire_out_allowed(world, household))
        self.assertEqual(hired, 0.0)
        self.assertEqual(world.stats.get("hired_days", 0.0), before)

    def test_household_in_book_with_nonempty_employer_receives_hire(self) -> None:
        world = load_scenario(BARONY, seed=1729)
        household = world.households["hh_native_001"]
        manor = world.manors["manor_hill"]
        manor.household_ids = [household.id]
        household.manor_id = manor.id
        employer_stock = world.get_stock(manor.stock_id)
        employer_stock.add("grain", 100.0)
        world.get_stock(household.stock_id).amounts["grain"] = 0.0
        hired = _hire(world, manor, 0.0, 10.0, world.clock.date)
        self.assertTrue(is_in_manor_book(world, household))
        self.assertTrue(hire_out_allowed(world, household))
        self.assertGreater(hired, 0.0)
        self.assertGreater(
            world.get_stock(household.stock_id).amounts.get("grain", 0.0),
            0.0,
        )

    def test_native_households_have_no_manor_obligations(self) -> None:
        world = load_scenario(NATIVE, seed=1729)
        settlement = world.settlements["tribal_village"]
        for hid in settlement.household_ids:
            household = world.households[hid]
            self.assertFalse(is_in_manor_book(world, household))
            self.assertIsNone(household.manor_id)
            self.assertEqual(household.obligation_ids, [])

    def test_native_village_household_is_not_evicted(self) -> None:
        world = load_scenario(NATIVE, seed=1729)
        household = world.households["hh_tribe_01"]
        household.hunger_days = 5
        phase_migrate(world)
        self.assertIsNone(household.left_at)

    def test_native_village_household_can_choose_leave(self) -> None:
        world = load_scenario(NATIVE, seed=1729)
        household = world.households["hh_tribe_01"]
        self.assertTrue(can_leave(world, household))

    def test_grant_tenure_creates_villein_status_and_rent(self) -> None:
        world = load_scenario(NATIVE, seed=1729)
        household = world.households["hh_02"]
        self.assertEqual(household.legal_status_id, "free_landless")
        self.assertTrue(
            any(
                world.obligations[oid].kind == "labor_duty"
                for oid in household.obligation_ids
            )
        )
        right = grant_tenure(
            world,
            household.id,
            "t_01_02",
            rent_share=0.1,
            status_id="villein",
        )
        self.assertEqual(household.legal_status_id, "villein")
        self.assertEqual(household.personal_status, "tied")
        self.assertEqual(household.land_relation, "tenement")
        self.assertEqual(world.tiles["t_01_02"].regime_id, "villein_tenement")
        self.assertTrue(
            any(
                world.obligations[oid].kind == "labor_duty"
                for oid in household.obligation_ids
            )
        )
        rent = next(
            world.obligations[oid]
            for oid in household.obligation_ids
            if world.obligations[oid].kind == "rent"
        )
        self.assertEqual(rent.right_id, right.id)
        self._assert_rent_is_share_of_harvest(world, household, rent)

    def _assert_rent_is_share_of_harvest(self, world, household, rent) -> None:
        """ЗАКОН (ADR 0183): оброк — доля от урожая, а не коэффициент-сумма.

        Прежде здесь стояло `assertAlmostEqual(rent.due_amount, 0.1)` — то есть
        проверка ЗАКРЕПЛЯЛА коэффициент как сумму. Именно поэтому мутация
        `rent_amount_for` (вернуть «никто не зовёт» и считать константой) проходила
        незамеченной: под старым поведением `due` и был 0.1. Тест проверяет закон
        пропорцией: вдвое больше урожая — вдвое больше оброка.
        """
        template = next(
            t for t in world.catalogs.obligation_templates.values()
            if t.kind == "rent"
        )
        share = template.default_share
        self.assertIsNotNone(share, "У оброка не объявлена доля (ADR 0183)")
        # ADR 0192: база оброка — УРОЖАЙ (проводки `harvest_grain`), а не остаток
        # амбара. Двор в этом стенде ничего не вырастил, поэтому оброк законно
        # 0.0. Прежнее ожидание 2.0 кодировало отменённую норму «оброк от остатка»:
        # подарок 20 зерна в амбар не может сделать двор должником.
        harvest = sum(
            entry.amount
            for entry in world.ledger.entries
            if entry.reason == "harvest_grain"
            and entry.kind == "process"
            and entry.good == "grain"
            and entry.dst_id == household.stock_id
        )
        self.assertAlmostEqual(
            rent.due_amount, round(share * harvest, 3), places=6,
            msg="Оброк — не доля от урожая двора (ADR 0192)",
        )
        self.assertAlmostEqual(
            rent.due_amount, 0.0, places=6,
            msg="Двор без урожая платит оброк: база взята с остатка амбара",
        )
        # Пропорция — закон чистой функции: ×2 зерна → ×2 оброк.
        for grain, want in ((10.0, 1.0), (20.0, 2.0), (40.0, 4.0)):
            self.assertAlmostEqual(
                rent_amount_for(template, grain), want, places=6,
                msg=f"Доля перестала быть пропорцией на урожае {grain}",
            )

    def test_rent_is_share_of_harvest_not_of_stock(self) -> None:
        """ЗАКОН (ADR 0185, формулировка владельца): оброк — доля от УРОЖАЯ.

        Две части закона, и обе падающие:

        1. **Лаг в месяц — правильное поведение.** Оброк считается от урожая
           ПРОШЕДШЕГО месяца: нельзя брать оброк с зерна, которое ещё не
           обмолочено, а месяц запаса означает, что двор не требует подати с
           урожая, посыпавшегося в середине сезона. Оброк — долг за то, что двор
           дал в долг, а не предъявление счёта в тот же день.
        2. **База — урожай, а не остаток в амбаре.** С остатка оброк платился бы и
           за привезённое, и за старое, и за подачу. Поэтому подача зерном в амбар
           двора **не должна** двигать оброк.

        Проверка первая сверяет долю с урожаем предыдущего месяца, вторая отделяет
        урожай от остатка подарком. Если код считает от остатка — вторая упадёт, и
        это следующий красный, а не тихий регресс.
        """
        world = load_scenario(NATIVE, seed=1729)
        household = world.households["hh_02"]
        stock = world.get_stock(household.stock_id)
        template = next(
            t for t in world.catalogs.obligation_templates.values() if t.kind == "rent"
        )
        share = template.default_share
        grant_tenure(world, household.id, "t_01_02", rent_share=0.1, status_id="villein")
        rent = next(
            world.obligations[oid]
            for oid in household.obligation_ids
            if world.obligations[oid].kind == "rent"
        )

        def harvest_total() -> float:
            """Урожай, а не остаток: проводки `harvest_grain` в амбар двора."""
            return sum(
                entry.amount
                for entry in world.ledger.entries
                if entry.reason == "harvest_grain"
                and entry.kind == "process"
                and entry.good == "grain"
                and entry.dst_id == household.stock_id
            )

        # Даём двору зерно, которого он не выращивал, — остаток растёт, урожай нет.
        stock.amounts["grain"] = stock.amounts.get("grain", 0.0) + 500.0
        before_due = rent.due_amount
        before_harvest = harvest_total()
        run_month(world)
        with self.subTest("база — урожай, а не остаток"):
            self.assertGreater(
                household_harvest_total(world, household), before_harvest,
                "Подача зерном не увеличила амбар — проверка бессмысленна",
            )
            self.assertAlmostEqual(
                rent.due_amount, before_due, places=6,
                msg=(
                    "Оброк вырос вслед за ОСТАТКОМ амбара: база оброка — не урожай. "
                    "С остатка платится и за привезённое, и за старое, и за подачу"
                ),
            )
        # Часть первая: оброк = доля от урожая ПРОШЕДШЕГО месяца.
        with self.subTest("лаг в месяц — закон"):
            self.assertAlmostEqual(
                rent.due_amount,
                round(share * before_harvest, 3),
                places=6,
                msg=(
                    f"оброк {rent.due_amount} не равен доле от урожая прошлого "
                    f"месяца {before_harvest} × {share}"
                ),
            )

    def test_temporary_plot_keeps_free_landless_status(self) -> None:
        world = load_scenario(NATIVE, seed=1729)
        household = world.households["hh_02"]
        right = grant_tenure(
            world,
            household.id,
            "t_01_02",
            temporary=True,
        )
        self.assertIn(right.id, world.rights)
        self.assertEqual(household.legal_status_id, "free_landless")
        self.assertEqual(household.land_relation, "landless")
        self.assertEqual(household.obligation_ids, [])
        self.assertEqual(household.holding_scale, 0.5)
        self.assertEqual(world.tiles["t_01_02"].regime_id, "cotter_plot")

    def test_revoke_tenure_restores_free_landless(self) -> None:
        world = load_scenario(NATIVE, seed=1729)
        household = world.households["hh_02"]
        right = grant_tenure(
            world,
            household.id,
            "t_01_02",
            rent_share=0.1,
            status_id="villein",
        )
        revoked = revoke_tenure(world, household.id, right.id)
        self.assertEqual(revoked, [right.id])
        self.assertNotIn(right.id, world.rights)
        self.assertEqual(household.legal_status_id, "free_landless")
        self.assertEqual(household.land_relation, "landless")
        self.assertEqual(household.obligation_ids, [])
        self.assertIsNone(household.manor_id)

    def test_initially_unemployed_keeps_land_validation_but_no_hire_out(self) -> None:
        world = load_scenario(NATIVE, seed=1729)
        household = world.households["hh_02"]
        self.assertEqual(household.legal_status_id, "free_landless")
        self.assertEqual(household.land_relation, "landless")


class TestEngineTenementDelegation(unittest.TestCase):
    def _result(self, world, household_id: str, right_id: str) -> dict:
        household = world.households[household_id]
        right = world.rights[right_id]
        manor = world.manors[world.player_manor_id]
        return {
            "right": vars(right),
            "regime": world.tiles[right.tile_id].regime_id,
            "household": (
                household.legal_status_id,
                household.personal_status,
                household.land_relation,
                household.obligation_bundle,
                household.holding_scale,
                household.manor_id,
            ),
            "book": sorted(manor.household_ids),
            "obligations": {
                oid: vars(world.obligations[oid])
                for oid in sorted(household.obligation_ids)
            },
        }

    def test_engine_grant_matches_legal_transition(self) -> None:
        legal = load_scenario(NATIVE, seed=1729)
        engine = load_scenario(NATIVE, seed=1729)
        legal_right = grant_tenure(
            legal,
            "hh_tribe_01",
            "t_04_02",
            rent_share=0.1,
            status_id="villein",
        )
        engine_rights = grant_tenement(
            engine,
            "hh_tribe_01",
            ["t_04_02"],
            rent_share=0.1,
            status_id="villein",
        )
        self.assertEqual(len(engine_rights), 1)
        self.assertEqual(
            self._result(legal, "hh_tribe_01", legal_right.id),
            self._result(engine, "hh_tribe_01", engine_rights[0].id),
        )

    def test_engine_revoke_rolls_back_transition(self) -> None:
        world = load_scenario(NATIVE, seed=1729)
        expected = load_scenario(NATIVE, seed=1729)
        legal_right = grant_tenure(
            expected,
            "hh_tribe_01",
            "t_04_02",
            rent_share=0.1,
            status_id="villein",
        )
        expected_revoked = revoke_tenure(expected, "hh_tribe_01", legal_right.id)
        right = grant_tenement(
            world,
            "hh_tribe_01",
            ["t_04_02"],
            rent_share=0.1,
            status_id="villein",
        )[0]
        self.assertEqual(
            engine_revoke_tenure(world, "hh_tribe_01", right.id),
            [right.id],
        )
        household = world.households["hh_tribe_01"]
        expected_household = expected.households["hh_tribe_01"]
        self.assertEqual(expected_revoked, [right.id])
        self.assertNotIn(right.id, world.rights)
        self.assertEqual(household.legal_status_id, expected_household.legal_status_id)
        self.assertEqual(household.land_relation, expected_household.land_relation)
        self.assertEqual(household.obligation_ids, expected_household.obligation_ids)
        self.assertEqual(household.manor_id, expected_household.manor_id)
        self.assertEqual(
            world.tiles["t_04_02"].regime_id,
            expected.tiles["t_04_02"].regime_id,
        )
        self.assertEqual(
            sorted(world.manors["manor_hill"].household_ids),
            sorted(expected.manors["manor_hill"].household_ids),
        )

    def test_engine_temporary_plot_does_not_create_permanent_tenure(self) -> None:
        world = load_scenario(NATIVE, seed=1729)
        right = grant_tenement(
            world,
            "hh_tribe_01",
            ["t_04_02"],
            temporary=True,
        )[0]
        household = world.households["hh_tribe_01"]
        self.assertEqual(household.legal_status_id, "free_landless")
        self.assertEqual(household.land_relation, "landless")
        self.assertEqual(household.obligation_ids, [])
        self.assertIsNone(household.manor_id)
        self.assertEqual(world.tiles["t_04_02"].regime_id, "cotter_plot")
        self.assertEqual(right.kind, "tenure")

    def test_engine_grant_is_deterministic_by_state_hash(self) -> None:
        first = load_scenario(NATIVE, seed=1729)
        second = load_scenario(NATIVE, seed=1729)
        grant_tenement(
            first,
            "hh_02",
            ["t_01_02"],
            rent_share=0.1,
            status_id="villein",
        )
        grant_tenement(
            second,
            "hh_02",
            ["t_01_02"],
            rent_share=0.1,
            status_id="villein",
        )
        self.assertEqual(first.state_hash(), second.state_hash())


if __name__ == "__main__":
    unittest.main()
