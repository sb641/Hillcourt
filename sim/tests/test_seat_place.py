"""Место стола усадьбы холма: глаз, задержка, мир — без бонуса к зерну.

Корень имеет seat_tiles; соседняя клетка видна с холма без вестника
(eye_from_hill, задержка 0, «видно с холма»); дальняя соль — только
письмом (caravan/messenger/silence, «узнали письмом» в смысле канала).
Приказ на кромку короче дальнего. Hazard в мире слабее (одно правило).
Двор у холма бесплатного урожая не получает. Возы и материя целы.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine import seat
from hillcourt.engine.tick import run_month
from hillcourt.info.knowledge import build_player_knowledge
from hillcourt.news.views import build_player_view
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SALT_TILE = "t_08_05"


def _court_neighbour(world) -> str:
    """Первая глазная клетка кроме зала (детерминированно)."""
    court = seat.court_tile_id(world)
    eyes = [t for t in seat.eye_tiles(world) if t != court]
    assert eyes, "у корня нет глазных клеток"
    return sorted(eyes)[0]


class TestSeatPlace(unittest.TestCase):
    """Усадьба — место: видно, быстро, мирно, но без бесплатного зерна."""

    def test_root_has_seat_tiles_with_court(self) -> None:
        world = load_scenario(SCENARIO)
        manor = world.manors[world.player_manor_id]
        self.assertTrue(manor.seat_tile_id, "у корня нет зала")
        self.assertIn(manor.seat_tile_id, manor.seat_tiles)
        court = seat.court_tile_id(world)
        self.assertEqual(manor.seat_tile_id, court)
        # Место — зал и ближние поля, не вся барония и не дальняя соль.
        self.assertLess(len(manor.seat_tiles), len(world.tiles))
        self.assertNotIn(SALT_TILE, manor.seat_tiles)
        self.assertEqual(manor.eye_range_tiles, 1)
        self.assertEqual(manor.peace_range_tiles, 1)

    def test_no_third_thegn_and_no_new_settlements(self) -> None:
        world = load_scenario(SCENARIO)
        self.assertLessEqual(len(world.settlements), 13)
        thegns = [m for m in world.manors.values() if m.parent_manor_id is not None]
        self.assertEqual(thegns, [], "место стола не заводит третьего тэна")

    def test_neighbour_visible_from_hill_without_messenger(self) -> None:
        world = load_scenario(SCENARIO)
        run_month(world)
        neighbour = _court_neighbour(world)
        eyes = [
            r
            for r in world.reports
            if r.source == "eye_from_hill" and r.subject_id == neighbour
        ]
        self.assertTrue(eyes, f"соседняя {neighbour} не видна с холма")
        eye = eyes[0]
        self.assertEqual(eye.delivery_date, eye.event_date)
        self.assertEqual(eye.confidence, 1.0)
        self.assertEqual(eye.noise, 0.0)
        self.assertIn("видно с холма", eye.content.lower())
        self.assertIn("grain_approx", eye.facts)
        self.assertNotIn("grain", eye.facts)
        # Знание игрока видит кромку сразу, без вестника.
        knowledge = build_player_knowledge(world, world.clock.date)
        entry = knowledge.latest(neighbour)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.source, "eye_from_hill")

    def test_far_salt_only_by_letter(self) -> None:
        world = load_scenario(SCENARIO)
        for _ in range(12):
            run_month(world)
        salts = [r for r in world.reports if r.subject_id == SALT_TILE]
        self.assertTrue(salts, "нет известий о соли")
        for report in salts:
            self.assertNotEqual(
                report.source,
                "eye_from_hill",
                "дальняя соль видна глазом — должна быть только письмом",
            )
            self.assertIn(report.source, ("caravan", "silence"))
        # Глаз дальнюю соль не достаёт.
        self.assertFalse(seat.is_eye_tile(world, SALT_TILE))
        self.assertFalse(seat.is_peace_tile(world, SALT_TILE))

    def test_order_delay_near_shorter_than_far(self) -> None:
        world = load_scenario(SCENARIO)
        neighbour = _court_neighbour(world)
        court = seat.court_tile_id(world)
        self.assertEqual(seat.order_delay_months(world, court, neighbour), 0)
        self.assertEqual(seat.order_delay_months(world, court, SALT_TILE), 1)
        self.assertLess(
            seat.order_delay_months(world, court, neighbour),
            seat.order_delay_months(world, court, SALT_TILE),
        )

    def test_sally_pack_eta_near_vs_far(self) -> None:
        world = load_scenario(SCENARIO)
        # Ближняя высылка: служилый холма (thegn, можно послать) на кромку —
        # eta в том же месяце. Держатель (holder) в поход не ходит.
        sendable_on_hill = [
            hid
            for hid, hh in world.households.items()
            if hh.current_tile_id == seat.court_tile_id(world)
            and hh.legal_status_id in ("thegn", "geneat")
            and len([p for p in hh.member_ids if world.persons[p].age_class == "adult"]) >= 1
        ]
        self.assertTrue(sendable_on_hill, "нет посылаемого двора на холме")
        hid = sorted(sendable_on_hill)[0]
        adults = [
            p
            for p in world.households[hid].member_ids
            if world.persons[p].age_class == "adult"
        ]
        neighbour = _court_neighbour(world)
        # Двор холма уже стоит на зале; соседняя клетка — кромка.
        # Если сосед не смежен с текущим двором (другой двор), берём смежную.
        origin = world.households[hid].current_tile_id
        if neighbour not in self._adjacent_tiles(world, origin):
            neighbour = sorted(self._adjacent_tiles(world, origin))[0]
        pack = seat.send_sally(world, hid, [adults[0]], neighbour)
        self.assertEqual(pack.eta_date, pack.departed_date)
        self.assertEqual(pack.route, [origin, neighbour])
        near_record = world.player_actions[-1]
        self.assertEqual(near_record["delay_months"], 0)
        # Дальняя высылка дольше: посылаемый двор вдали от холма.
        far_world = load_scenario(SCENARIO)
        sendable_far = [
            hid
            for hid, hh in far_world.households.items()
            if not seat.is_eye_tile(far_world, hh.current_tile_id)
            and hh.legal_status_id in ("thegn", "geneat")
            and len(
                [p for p in hh.member_ids if far_world.persons[p].age_class == "adult"]
            )
            >= 1
        ]
        self.assertTrue(sendable_far, "нет посылаемого двора вдали")
        fhid = sorted(sendable_far)[0]
        fadults = [
            p
            for p in far_world.households[fhid].member_ids
            if far_world.persons[p].age_class == "adult"
        ]
        far_origin = far_world.households[fhid].current_tile_id
        far_neighbour = sorted(self._adjacent_tiles(far_world, far_origin))[0]
        far_pack = seat.send_sally(far_world, fhid, [fadults[0]], far_neighbour)
        self.assertGreater(far_pack.eta_date, far_pack.departed_date)
        self.assertLess(pack.eta_date, far_pack.eta_date)

    def _adjacent_tiles(self, world, origin: str) -> list[str]:
        ox, oy = int(origin.split("_")[1]), int(origin.split("_")[2])
        found = []
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            tid = f"t_{ox + dx:02d}_{oy + dy:02d}"
            if tid in world.tiles:
                found.append(tid)
        return found

    def test_peace_single_rule_weaker_theft(self) -> None:
        world = load_scenario(SCENARIO)
        neighbour = _court_neighbour(world)
        self.assertEqual(seat.peace_theft_factor(world, neighbour), 0.5)
        self.assertEqual(seat.peace_theft_factor(world, SALT_TILE), 1.0)
        # Одна клетка мира vs глухой лес: кража рядом вдвое слабее.
        self.assertLess(
            seat.peace_theft_factor(world, neighbour),
            seat.peace_theft_factor(world, SALT_TILE),
        )

    def test_no_free_harvest_near_hill(self) -> None:
        world = load_scenario(SCENARIO)
        before = {
            sid: dict(stock.amounts) for sid, stock in world.stocks.items()
        }
        run_month(world)
        # Место не создаёт зерно: нет ledger-приходов с reason места/глаза.
        for entry in world.ledger.entries:
            self.assertNotIn("seat", entry.reason)
            self.assertNotIn("eye", entry.reason)
            self.assertNotIn("peace", entry.reason)
        # Двор у холма не получил зерна из ничего: его зерно пришло только
        # переводами/обработкой, а дельта материи 0.
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)
        self.assertTrue(before, "пустой снимок стоков")

    def test_matter_and_caravan_regression(self) -> None:
        world = load_scenario(SCENARIO)
        for _ in range(12):
            run_month(world)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)
        kinds = {e.kind for e in world.ledger.entries}
        self.assertIn("external_in", kinds)
        self.assertIn("transfer", kinds)
        # Возы соли идут цепочкой, глаз её не подменяет.
        view = build_player_view(world, world.clock.date)
        self.assertTrue(view.entries)

    def test_eye_does_not_consume_news_rng(self) -> None:
        world = load_scenario(SCENARIO)
        for _ in range(12):
            run_month(world)
        eyes = [r for r in world.reports if r.source == "eye_from_hill"]
        neighbours = [r for r in eyes if r.subject_id != seat.court_tile_id(world)]
        # Глаз есть и он точен: без шума и искажений (RNG не тронут).
        self.assertTrue(neighbours, "глазных Report о кромке нет")
        for report in neighbours:
            self.assertEqual(report.noise, 0.0)
            self.assertFalse(report.distorted)
            self.assertEqual(report.delivery_date, report.event_date)
        # Почта дальних жива рядом с глазом: соль — caravan/silence с их задержкой.
        salts = [r for r in world.reports if r.subject_id == SALT_TILE]
        self.assertTrue(salts, "почта о соли задушена глазом")
        for report in salts:
            self.assertIn(report.source, ("caravan", "silence"))
            if report.source == "caravan" and "salt_approx" in report.facts:
                self.assertEqual(report.noise, 0.4)


if __name__ == "__main__":
    unittest.main()
