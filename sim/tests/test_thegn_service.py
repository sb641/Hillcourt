"""Тэн как служба: норма, комплекты, кольцо, отзыв.

Зонды держат наблюдаемые факты (канон приёмки):
  - норма службы на маноре; кольцо = свои клетки + кромка; depth ≤ 1;
  - сытый без комплекта службу не закрывает (mustered ложен, gap > 0);
  - сытый с крицей собирает комплекты (трата излишка, не копление зерна);
  - голодный комплекты не собирает (еда раньше третьего комплекта);
  - выдача grant_tool с корня закрывает норму и уменьшает сток корня;
  - вылазки — только на своё кольцо (дальняя чужая клетка игнорируется),
    весть корню — по старым каналам, без мгновенной истины;
  - пустая служба + развал держания N месяцев → revoke доступен и отрабатывает,
    один плохой месяц — нет; бывший держатель жив, свободен без земли;
  - revoke_thegn исполняется из runner;
  - канонический шир 60 мес: первый фьеф норму иногда закрывает, второй
    честно нет; соль едет; материя сходится; третьего тэна нет; yield не поднят.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.manor import (
    REVOKE_BAD_MONTHS,
    fief_kits,
    grant_thegn,
    grant_tool,
    guard_ring,
    holder_kits,
    holding_ruined,
    manor_depth,
    manor_stock,
    nested_manors,
    revoke_due,
    revoke_thegn,
    root_manor,
)
from hillcourt.engine.tick import run_month
from hillcourt.ontology import Hazard
from hillcourt.runner import ACTION_HANDLERS, _apply_script_entry
from hillcourt.runner import run as run_scenario
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
HILL_SALT = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SHIRE = ROOT / "design" / "scenarios" / "v0_shire.yml"
SEED = 1729

FIRST_TILES = ["t_03_01", "t_05_02"]
FIRST_TENANT = "hh_02"
HOLDER_HOUSEHOLD = "hh_retinue"
HOLDER_PERSON = "hh_retinue_p1"


def _grant_hill_salt(world, person=HOLDER_PERSON, tiles=None, households=None):
    return grant_thegn(
        world, person, tiles or list(FIRST_TILES), households or [FIRST_TENANT]
    )


def _seed(world, stock_id: str, good: str, amount: float) -> None:
    """Положить благо внешним приходом: дельта материи не ломается."""
    world.ledger.external_in(
        world.get_stock(stock_id), good, amount, "probe_setup", None,
        world.clock.date,
    )


def _service_months(world, manor_id: str) -> tuple[int, int]:
    """(месяцев с service_met, всего записей service) по манору из manor_log."""
    met = total = 0
    for entry in world.manor_log:
        service = entry.get("service", {}).get(manor_id)
        if service is None:
            continue
        total += 1
        if service.get("service_met"):
            met += 1
    return met, total


class TestServiceNorm(unittest.TestCase):
    """Норма службы — на маноре: комплекты, явки, кольцо, сток, demesne."""

    def test_grant_sets_service_norm_and_ring(self) -> None:
        world = load_scenario(HILL_SALT, seed=SEED)
        manor = _grant_hill_salt(world)
        self.assertIsNotNone(manor)
        # Сам как тяжёлый + сервант с тяглого двора; явки — взрослые держателя.
        self.assertEqual(manor.service_kits_required, 2.0)
        self.assertGreaterEqual(manor.service_men_required, 1.0)
        self.assertEqual(manor.upward_bundle, "thegn_service")
        self.assertEqual(manor_depth(world, manor), 1)
        self.assertIn(manor.stock_id, world.stocks)
        self.assertTrue(
            any(world.tiles[tid].regime_id == "demesne" for tid in manor.tile_ids)
        )
        ring = guard_ring(world, manor)
        for tid in manor.tile_ids:
            self.assertIn(tid, ring, "Своя клетка вне кольца")
        # Кромка: сосед пожалованной клетки — в кольце, дальняя — нет.
        self.assertIn("t_02_01", ring, "Сосед фьефа не в кольце")
        self.assertNotIn("t_08_05", ring, "Дальняя клетка в кольце")


class TestFedWithoutKit(unittest.TestCase):
    """Сыт без комплекта → служба не закрыта (mustered ложен, gap > 0)."""

    def test_fed_holder_without_kit_has_service_gap(self) -> None:
        world = load_scenario(HILL_SALT, seed=SEED)
        manor = _grant_hill_salt(world)
        holder = world.households[HOLDER_HOUSEHOLD]
        _seed(world, manor.stock_id, "grain", 100.0)
        _seed(world, holder.stock_id, "grain", 10.0)
        for _ in range(3):
            run_month(world)
        self.assertEqual(holder.hunger_days, 0, "Двор должен быть сыт")
        self.assertAlmostEqual(fief_kits(world, manor), 0.0, places=6)
        self.assertFalse(manor.service_met, "Норма закрыта без комплектов")
        self.assertGreater(manor.service_gap, 0.0, "Разрыв службы не виден")
        self.assertFalse(manor.mustered, "mustered без комплекта на держателе")
        self.assertTrue(manor.iron_requested, "Тэн не запросил железо")
        service = [
            e for e in world.manor_log if "service" in e and manor.id in e["service"]
        ]
        self.assertTrue(service, "service_met/service_gap нет в логе манора")
        self.assertIn("season", service[-1]["service"][manor.id])


class TestKitSpending(unittest.TestCase):
    """Сытый с крицей собирает комплекты; голодный — нет (еда раньше)."""

    def _fed_grant(self, bloom: float):
        world = load_scenario(HILL_SALT, seed=SEED)
        manor = _grant_hill_salt(world)
        holder = world.households[HOLDER_HOUSEHOLD]
        _seed(world, manor.stock_id, "grain", 100.0)
        _seed(world, holder.stock_id, "grain", 10.0)
        _seed(world, manor.stock_id, "iron_bloom", bloom)
        return world, manor, holder

    def test_fed_thegn_assembles_kits_from_stock(self) -> None:
        world, manor, holder = self._fed_grant(2.0)
        for _ in range(4):
            run_month(world)
        smith = [
            e for e in world.ledger.entries if e.reason == "smith_kit"
        ]
        self.assertTrue(smith, "Сборка комплектов не шла при закрытом голоде")
        self.assertGreaterEqual(fief_kits(world, manor), 1.0)
        self.assertGreaterEqual(holder_kits(world, manor), 1.0, "Комплект не выдан на держателя")
        barn = manor_stock(world, manor)
        self.assertAlmostEqual(barn.amounts.get("iron_bloom", 0.0), 0.0, places=6,
                               msg="Излишек крицы не потрачен на комплекты")

    def test_hungry_thegn_does_not_buy_kits_before_food(self) -> None:
        world = load_scenario(HILL_SALT, seed=SEED)
        manor = _grant_hill_salt(world)
        holder = world.households[HOLDER_HOUSEHOLD]
        _seed(world, manor.stock_id, "iron_bloom", 2.0)
        # Голод честный: зерно двора и замка — в отходы переводом (материя цела).
        waste = world.get_stock("sink:waste")
        for stock_id in (holder.stock_id, "settlement:hill_court"):
            stock = world.get_stock(stock_id)
            grain = stock.amounts.get("grain", 0.0)
            if grain > 0:
                world.ledger.transfer(stock, waste, "grain", grain, "probe_ruin",
                                      world.clock.date)
        for _ in range(3):
            run_month(world)
        self.assertGreater(holder.hunger_days, 0, "Двор должен голодать")
        self.assertAlmostEqual(fief_kits(world, manor), 0.0, places=6,
                               msg="Голодный фьеф закупил комплекты раньше еды")
        self.assertFalse(
            [e for e in world.ledger.entries if e.reason == "smith_kit"],
            "Сборка шла при открытом голоде",
        )


class TestGrantToolBoost(unittest.TestCase):
    """Выдача корня закрывает норму фьефа и уменьшает сток корня."""

    def test_grant_iron_closes_service_norm(self) -> None:
        world = load_scenario(HILL_SALT, seed=SEED)
        manor = _grant_hill_salt(world)
        holder = world.households[HOLDER_HOUSEHOLD]
        castle = world.get_stock("settlement:hill_court")
        castle.amounts["iron_bloom"] = 5.0
        world.ledger.capture_initial(world.total_matter())
        grant_tool(world, HOLDER_HOUSEHOLD, "iron_bloom", 2.0)
        for _ in range(6):
            run_month(world)
        self.assertAlmostEqual(castle.amounts.get("iron_bloom", 0.0), 3.0, places=6,
                               msg="Сток корня не уменьшился на выдачу")
        self.assertGreaterEqual(fief_kits(world, manor), 2.0,
                                msg="Выдача не закрыла норму комплектов")
        met, _ = _service_months(world, manor.id)
        self.assertGreater(met, 0, "Норма ни разу не закрылась после выдачи")
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


class TestRevokePolicy(unittest.TestCase):
    """Пустая служба + развал N месяцев → revoke; один плохой месяц — нет."""

    def _ruined_world(self):
        world = load_scenario(HILL_SALT, seed=SEED)
        manor = _grant_hill_salt(world)
        holder = world.households[HOLDER_HOUSEHOLD]
        tenant = world.households[FIRST_TENANT]
        tenant.left_at = world.clock.date
        barn = manor_stock(world, manor)
        barn_stock = world.get_stock(barn.id)
        for good in list(barn_stock.amounts):
            amount = barn_stock.amounts.get(good, 0.0)
            if amount > 0:
                world.ledger.transfer(
                    barn_stock, world.get_stock("sink:waste"), good, amount,
                    "probe_ruin", world.clock.date,
                )
        holder_stock = world.get_stock(holder.stock_id)
        for good in list(holder_stock.amounts):
            amount = holder_stock.amounts.get(good, 0.0)
            if amount > 0:
                world.ledger.transfer(
                    holder_stock, world.get_stock("sink:waste"), good, amount,
                    "probe_ruin", world.clock.date,
                )
        holder.hunger_days = 5
        world.ledger.capture_initial(world.total_matter())
        return world, manor, holder

    def test_one_bad_month_is_not_due(self) -> None:
        world, manor, _ = self._ruined_world()
        self.assertTrue(holding_ruined(world, manor), "Держание должно быть развалено")
        run_month(world)
        self.assertFalse(manor.service_met)
        self.assertFalse(revoke_due(world, manor), "Один плохой месяц открыл revoke")

    def test_empty_service_and_ruin_open_revoke(self) -> None:
        world, manor, holder = self._ruined_world()
        for _ in range(REVOKE_BAD_MONTHS + 1):
            run_month(world)
        self.assertFalse(manor.service_met, "Служба должна быть пустой")
        self.assertTrue(holding_ruined(world, manor))
        self.assertTrue(revoke_due(world, manor), "revoke не доступен после N месяцев")
        total_before = world.total_matter()
        self.assertTrue(revoke_thegn(world, manor.id), "revoke не отработал")
        root = root_manor(world)
        self.assertNotIn(manor.id, world.manors, "Манор остался в книге")
        self.assertNotIn(manor.stock_id, world.stocks, "Мёртвый амбар остался")
        self.assertEqual(holder.manor_id, root.id, "Двор не вернулся в корень")
        self.assertIn(holder.id, root.household_ids)
        # Бывший держатель существует: жив, свободный без земли, в книге корня.
        self.assertGreater(world.persons[HOLDER_PERSON].health, 0.0)
        self.assertIsNone(holder.left_at)
        self.assertEqual(holder.legal_status_id, "free_landless")
        self.assertEqual(holder.personal_status, "free")
        self.assertEqual(holder.land_relation, "landless")
        self.assertAlmostEqual(world.total_matter(), total_before, places=6,
                               msg="revoke двоит или жжёт материю")
        self.assertEqual(manor_depth(world, root), 0)
        self.assertTrue(
            any(a["action"] == "revoke_thegn" for a in world.player_actions),
            "revoke не записан как действие корня",
        )


class TestRunnerRevoke(unittest.TestCase):
    """revoke_thegn исполняется из runner как действие корня."""

    def test_revoke_is_script_dispatchable(self) -> None:
        self.assertIn("revoke_thegn", ACTION_HANDLERS)
        world = load_scenario(SHIRE, seed=SEED)
        for month_index in range(1, 8):
            for entry in world.script:
                if int(entry.get("at_month", 0)) == month_index:
                    _apply_script_entry(world, entry)
            run_month(world)
        manor_id = "manor_hh_retinue_p1"
        self.assertIn(manor_id, world.manors)
        records = _apply_script_entry(
            world, {"at_month": 8, "action": "revoke_thegn", "manor": manor_id}
        )
        self.assertNotIn(manor_id, world.manors, "revoke из runner не отработал")
        self.assertTrue(
            any(r["action"] == "revoke_thegn" and r["manor"] == manor_id for r in records),
            "revoke не записан в лог действий",
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


class TestRingSorties(unittest.TestCase):
    """Угроза на кольце тянет вылазку на свою кромку, не на дальнюю чужую."""

    def _world_with_threats(self):
        world = load_scenario(SHIRE, seed=SEED)
        for month_index in range(1, 7):
            for entry in world.script:
                if int(entry.get("at_month", 0)) == month_index:
                    _apply_script_entry(world, entry)
            run_month(world)
        manor_id = "manor_hh_retinue_p1"
        manor = world.manors[manor_id]
        ring = guard_ring(world, manor)
        self.assertIn("t_02_01", ring)
        self.assertNotIn("t_07_07", ring)
        world.hazards["hz_ring"] = Hazard(
            id="hz_ring", kind="wolves", tile_id="t_02_01", intensity=0.3,
            active=True, population=0.2, satiety=0.9,
        )
        world.tiles["t_02_01"].hazard_ids.append("hz_ring")
        world.hazards["hz_far"] = Hazard(
            id="hz_far", kind="wolves", tile_id="t_07_07", intensity=1.0,
            active=True, population=1.0, satiety=0.1,
        )
        world.tiles["t_07_07"].hazard_ids.append("hz_far")
        holder = world.households[HOLDER_HOUSEHOLD]
        _seed(world, holder.stock_id, "war_kit", 1.0)
        _seed(world, manor.stock_id, "grain", 50.0)
        _seed(world, holder.stock_id, "grain", 10.0)
        return world, manor_id

    def test_sorties_go_to_own_ring_not_far_tile(self) -> None:
        world, manor_id = self._world_with_threats()
        for _ in range(12):
            run_month(world)
        own = [
            p for p in world.packs.values()
            if p.kind == "party" and p.owner_household_id == HOLDER_HOUSEHOLD
            and p.destination_tile_id == "t_02_01"
        ]
        far = [
            p for p in world.packs.values()
            if p.kind == "party" and p.destination_tile_id == "t_07_07"
        ]
        self.assertGreater(len(own), 0, "Тэн ни разу не вышел на своё кольцо")
        self.assertEqual(len(far), 0, "Тэн пошёл на дальнюю чужую клетку корня")
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_root_gets_no_instant_truth_about_sortie(self) -> None:
        """Весть о вылазке — гонец с задержкой; исход — гонец/молчание."""
        world, manor_id = self._world_with_threats()
        for _ in range(12):
            run_month(world)
        dispatches = [
            r for r in world.reports
            if r.subject_kind == "pack" and "послан" in r.content
            and "t_02_01" in str(r.facts.get("destination", ""))
        ]
        self.assertTrue(dispatches, "Нет вести об отправке вылазки")
        for report in dispatches:
            self.assertEqual(report.source, "messenger")
            self.assertGreater(
                report.delivery_date, report.event_date,
                "Весть о вылазке дошла мгновенно",
            )
            self.assertNotIn("grain", report.facts, "В вести — истина стока")
        outcomes = [
            r for r in world.reports
            if r.subject_kind == "tile" and r.subject_id == "t_02_01"
            and r.source in ("messenger", "silence")
        ]
        self.assertTrue(outcomes, "Исход вылазки не дошёл ни гонцом, ни молчанием")


class TestShireServiceSplit(unittest.TestCase):
    """Канонический шир 60 мес: различимость фьефов, соль, материя, запреты."""

    def test_sixty_months_two_fiefs_diverge(self) -> None:
        result = run_scenario(SHIRE, 60, seed=SEED)
        self.assertAlmostEqual(result.matter_delta, 0.0, places=6)
        world = load_scenario(SHIRE, seed=SEED)
        for month_index in range(1, 61):
            for entry in world.script:
                if int(entry.get("at_month", 0)) == month_index:
                    _apply_script_entry(world, entry)
            run_month(world)
        self.assertEqual(len(nested_manors(world)), 2, "Третий тэн появился")
        met1, _ = _service_months(world, "manor_hh_retinue_p1")
        met2, _ = _service_months(world, "manor_hh_03_p1")
        self.assertGreater(met1, 0, "Живой первый фьеф ни разу не закрыл норму")
        self.assertLess(met2, met1, "Бедный второй фьеф не отличается от первого")
        manor2 = world.manors["manor_hh_03_p1"]
        self.assertGreater(manor2.service_gap, 0.0, "Второй фьеф честно не закрыл норму")
        # Соль едет: обозные рейсы с долей доехавшего груза в памяти сделок.
        routes = [k for k in world.barter_memory if "salt" in k or "hill" in k]
        self.assertTrue(
            any(world.barter_memory[k].get("ratio") for k in routes),
            "Соль не едет обозами",
        )
        # Голод мира не вылечен подъёмом yield: рецепт жатвы прежний.
        harvest = world.catalogs.recipes["harvest_grain"]
        self.assertAlmostEqual(harvest.outputs.get("grain", 0.0), 5.0, places=6)
        self.assertAlmostEqual(harvest.labor_days, 20.0, places=6)
        # Знание дальней деревни — рассказ, а не её сток (поле истины не утекло).
        from hillcourt.info.knowledge import build_player_knowledge
        knowledge = build_player_knowledge(world, world.clock.date)
        ash_tile = "t_02_07"
        entry = knowledge.latest(ash_tile)
        if entry is not None:
            self.assertNotIn("amounts", entry.facts)


class TestReviewFollowups(unittest.TestCase):
    """Ответ на ревью 0022: явки в деле, ротация, атрибуция, вести барону."""

    def test_men_shortage_breaks_service_despite_kits(self) -> None:
        """M1: явки — не декорация: гибель серванта роняет норму при комплектах."""
        world = load_scenario(HILL_SALT, seed=SEED)
        manor = _grant_hill_salt(world)
        holder = world.households[HOLDER_HOUSEHOLD]
        self.assertEqual(manor.service_men_required, 3.0)
        _seed(world, manor.stock_id, "grain", 100.0)
        _seed(world, holder.stock_id, "grain", 10.0)
        _seed(world, manor.stock_id, "iron_bloom", 2.0)
        run_month(world)
        run_month(world)
        self.assertTrue(manor.service_met, "Норма должна была закрыться до потерь")
        dead = next(
            pid for pid in sorted(holder.member_ids) if pid != HOLDER_PERSON
        )
        world.persons[dead].health = 0.0
        run_month(world)
        self.assertEqual(manor.service_men_held, 2.0)
        self.assertFalse(manor.service_met, "Служба закрыта без явок")
        service = [
            e for e in world.manor_log if "service" in e and manor.id in e["service"]
        ]
        self.assertEqual(service[-1]["service"][manor.id]["men_held"], 2.0)

    def test_sortie_rotation_sends_servant_first(self) -> None:
        """M5: на вылазку идёт сервант, держатель — последним."""
        world, manor_id = TestRingSorties()._world_with_threats()
        for _ in range(12):
            run_month(world)
            first = next(
                (
                    p for p in world.packs.values()
                    if p.kind == "party" and p.owner_household_id == HOLDER_HOUSEHOLD
                ),
                None,
            )
            if first is not None:
                break
        self.assertIsNotNone(first, "Вылазки не было за 12 месяцев")
        self.assertNotIn(HOLDER_PERSON, first.member_ids,
                         "Держатель пошёл первым при живых сервантах")

    def test_thegn_sortie_is_not_logged_as_player_order(self) -> None:
        """M6: вылазка тэна — не приказ барона (нет записи в player_actions)."""
        world, manor_id = TestRingSorties()._world_with_threats()
        for _ in range(12):
            run_month(world)
        sorties = [
            p for p in world.packs.values()
            if p.kind == "party" and p.owner_household_id == HOLDER_HOUSEHOLD
        ]
        self.assertTrue(sorties, "Вылазок не было")
        forged = [
            a for a in world.player_actions
            if a.get("action") == "send_party" and a.get("household") == HOLDER_HOUSEHOLD
        ]
        self.assertEqual(forged, [], "Вылазка записана как приказ игрока")

    def test_iron_request_reaches_baron_by_messenger(self) -> None:
        """M2: просьба о железе доходит гонцом с задержкой, без истины стока."""
        world = load_scenario(HILL_SALT, seed=SEED)
        manor = _grant_hill_salt(world)
        holder = world.households[HOLDER_HOUSEHOLD]
        _seed(world, manor.stock_id, "grain", 100.0)
        _seed(world, holder.stock_id, "grain", 10.0)
        for _ in range(3):
            run_month(world)
        requests = [
            r for r in world.reports
            if r.subject_kind == "manor" and r.subject_id == manor.id
            and "железа" in r.content
        ]
        self.assertTrue(requests, "Барон не получил просьбу о железе")
        report = requests[0]
        self.assertEqual(report.source, "messenger")
        self.assertGreater(report.delivery_date, report.event_date)
        self.assertNotIn("amounts", report.facts)

    def test_revoke_due_reaches_baron_by_messenger(self) -> None:
        """M3: пустая служба N месяцев — весть, а не молчаливая ловушка."""
        world, manor, _ = TestRevokePolicy()._ruined_world()
        for _ in range(REVOKE_BAD_MONTHS + 1):
            run_month(world)
        self.assertTrue(revoke_due(world, manor))
        warnings = [
            r for r in world.reports
            if r.subject_kind == "manor" and r.subject_id == manor.id
            and "пуста" in r.content
        ]
        self.assertTrue(warnings, "Барон не предупреждён о пустой службе")
        self.assertEqual(warnings[0].source, "messenger")
        self.assertGreater(warnings[0].delivery_date, warnings[0].event_date)


if __name__ == "__main__":
    unittest.main()
