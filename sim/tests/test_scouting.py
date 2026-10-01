"""Движковые правила разведки ADR 0074."""

from __future__ import annotations

import random
import unittest
from pathlib import Path
from unittest import mock

from hillcourt.engine import scouting
from hillcourt.engine.events import month_events
from hillcourt.engine.path import known_tiles
from hillcourt.engine.scouting import (
    PROFILE_CHANCE,
    observe_scout_packs,
    observation_chance,
)
from hillcourt.engine.tick import run_month
from hillcourt.news.propagation import delivered_reports
from hillcourt.news.scouting import report_scout_observations
from hillcourt.ontology import Pack, Stock
from hillcourt.runner import _apply_script_entry
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
HILL = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
TWO = ROOT / "design" / "scenarios" / "v0_two_settlements.yml"
SHIRE = ROOT / "design" / "scenarios" / "v0_shire.yml"
BARONY = ROOT / "design" / "scenarios" / "v0_barony_100.yml"
CENTER = "t_08_05"
FOREST = "t_08_04"
FIELD = "t_08_06"
DISTANCE_TWO = "t_07_03"
DISTANCE_THREE = "t_07_02"


class _ConstantRng:
    """Поток с постоянным броском для проверки порога вероятности."""

    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        """Вернуть заданный бросок."""
        return self.value

    def choice(self, values):
        """Вернуть первый детерминированный вариант."""
        return values[0]


class _NoRandomRng:
    """Поток, запрещающий любой бросок."""

    def random(self) -> float:
        """Сообщить о неожиданном броске."""
        raise AssertionError("Наблюдение факта не должно бросать кость")

    def choice(self, values):
        """Сообщить о неожиданном выборе."""
        raise AssertionError("Наблюдение факта не должно выбирать слух")


class _NoChoiceRng:
    def __init__(self, seed: int) -> None:
        self._random = random.Random(seed)

    def random(self) -> float:
        return self._random.random()

    def choice(self, values):
        raise AssertionError("Физический поток не должен выбирать текст слуха")


class TestScoutingEngine(unittest.TestCase):
    """Проверки физического наблюдения без доступа к знанию игрока."""

    def setUp(self) -> None:
        self.world = load_scenario(HILL, seed=42)
        self.person_id = sorted(self.world.persons)[0]

    def _pack(
        self,
        world,
        *,
        purpose: str = "scout",
        profile_id: str = "hunters",
        location_id: str = CENTER,
        route: tuple[str, ...] | list[str] | None = None,
        pack_id: str = "pack_scout_test",
    ) -> Pack:
        """Поставить тестовую партию в клетку без движения материи."""
        world.persons[self.person_id].location_tile_id = location_id
        pack = Pack(
            id=pack_id,
            kind="party",
            origin_tile_id=location_id,
            destination_tile_id=location_id,
            route=list(route or [location_id]),
            member_ids=[self.person_id],
            cargo=Stock(
                id=f"pack:{pack_id}",
                owner_kind="pack",
                owner_id=pack_id,
                amounts={},
            ),
            departed_date=world.clock.date,
            eta_date=world.clock.date.advance(world.clock.months_per_year),
            purpose=purpose,
            profile_id=profile_id,
        )
        world.packs[pack_id] = pack
        return pack

    def test_forest_is_noticed_more_often_than_field(self) -> None:
        forest_world = load_scenario(HILL, seed=42)
        field_world = load_scenario(HILL, seed=42)
        self._pack(forest_world)
        self._pack(field_world)
        field_world.tiles[FIELD].terrain = "field"
        forest_world.rng.world = _ConstantRng(0.5)
        field_world.rng.world = _ConstantRng(0.5)

        forest_events = observe_scout_packs(forest_world)
        field_events = observe_scout_packs(field_world)
        self.assertIn(FOREST, {event["tile_id"] for event in forest_events})
        self.assertNotIn(FIELD, {event["tile_id"] for event in field_events})

    def test_distance_two_is_only_a_rumor(self) -> None:
        self._pack(self.world)
        self.world.rng.world = _ConstantRng(0.99)
        events = {
            event["tile_id"]: event for event in observe_scout_packs(self.world)
        }
        signal = events[DISTANCE_TWO]
        self.assertFalse(signal["fact"])
        self.assertIs(signal["rumor"], True)
        self.assertAlmostEqual(signal["confidence"], 0.5)
        self.assertEqual(signal["source"], "scout")

    def test_rumor_wording_cannot_affect_observations_or_world_hash(self) -> None:
        variants = (
            ("дым", "след", "костёр"),
            ("костёр", "дым", "след"),
            ("молва", "тень", "знак"),
        )
        baseline: list[dict] | None = None
        baseline_counts: tuple[int, int] | None = None
        baseline_hash: str | None = None
        for wording in variants:
            with self.subTest(wording=wording):
                world = load_scenario(HILL, seed=42)
                self._pack(world, pack_id="rumor_proof")
                world.rng.world = _NoChoiceRng(42)
                before_hash = world.state_hash()
                with mock.patch.object(scouting, "RUMORS", wording, create=True):
                    events = observe_scout_packs(world)
                self.assertEqual(world.state_hash(), before_hash)
                counts = (
                    sum(event["fact"] is True for event in events),
                    sum(event["fact"] is False for event in events),
                )
                if baseline is None:
                    baseline = events
                    baseline_counts = counts
                    baseline_hash = before_hash
                self.assertEqual(events, baseline)
                self.assertEqual(counts, baseline_counts)
                self.assertEqual(before_hash, baseline_hash)
                self.assertGreater(counts[0], 0)
                self.assertGreater(counts[1], 0)

    def test_profile_thresholds_and_cover_are_separate_axes(self) -> None:
        self.assertEqual(
            PROFILE_CHANCE,
            {"hunters": 0.85, "forester": 0.75, "party": 0.55},
        )
        pack = self._pack(self.world)
        target = self.world.tiles[FOREST]
        target.hazard_ids = []

        target.terrain = "forest"
        pack.profile_id = "forester"
        forest_forester = observation_chance(pack, self.world, target.id)
        pack.profile_id = "party"
        forest_party = observation_chance(pack, self.world, target.id)
        target.terrain = "field"
        forester = observation_chance(pack, self.world, target.id)
        pack.profile_id = "hunters"
        hunters = observation_chance(pack, self.world, target.id)
        pack.profile_id = "party"
        field_party = observation_chance(pack, self.world, target.id)

        self.assertGreaterEqual(forest_forester, forest_party)
        self.assertGreater(hunters, forester)
        self.assertGreater(forest_party, field_party)

    def test_distance_three_or_more_is_silent(self) -> None:
        self._pack(self.world)
        self.world.rng.world = _ConstantRng(0.0)
        observed = {event["tile_id"] for event in observe_scout_packs(self.world)}
        self.assertNotIn(DISTANCE_THREE, observed)

    def test_without_scout_pack_observation_is_zero(self) -> None:
        self.assertEqual(observe_scout_packs(self.world), [])

    def test_same_tile_is_fact_without_random_roll(self) -> None:
        self._pack(self.world)
        for tile_id in list(self.world.tiles):
            if tile_id != CENTER:
                self.world.tiles.pop(tile_id)
        self.world.rng.world = _NoRandomRng()
        events = observe_scout_packs(self.world)
        self.assertEqual([event["tile_id"] for event in events], [CENTER])
        self.assertTrue(events[0]["fact"])
        self.assertEqual(events[0]["confidence"], 1.0)

    def test_event_observer_is_party_and_news_owns_knowledge(self) -> None:
        pack = self._pack(self.world)
        before = known_tiles(self.world)
        events = observe_scout_packs(self.world)
        self.assertEqual(known_tiles(self.world), before)
        self.assertTrue(all(event["observer_id"] == pack.id for event in events))
        self.world.month_events = events
        self.assertIn(CENTER, {event["tile_id"] for event in month_events(self.world)})
        reports = report_scout_observations(self.world)
        scout_reports = [report for report in reports if report.source == "scout"]
        self.assertTrue(scout_reports)
        self.assertTrue(all(report.observer_id == pack.id for report in scout_reports))
        self.assertIn(CENTER, known_tiles(self.world))

    def test_hazard_strictly_worsens_observation(self) -> None:
        pack = self._pack(self.world)
        target = self.world.tiles[FIELD]
        before = observation_chance(pack, self.world, target.id)
        hazard = next(iter(self.world.hazards.values()))
        hazard.active = True
        hazard.intensity = 1.0
        hazard.tile_id = target.id
        target.hazard_ids = [hazard.id]
        self.assertLess(observation_chance(pack, self.world, target.id), before)

    def test_repeat_with_same_seed_is_deterministic(self) -> None:
        first = load_scenario(HILL, seed=99)
        second = load_scenario(HILL, seed=99)
        first_pack = self._pack(first)
        second_pack = self._pack(second)
        self.assertEqual(
            observe_scout_packs(first),
            observe_scout_packs(second),
        )
        self.assertEqual(first.state_hash(), second.state_hash())
        self.assertEqual(first_pack.purpose, second_pack.purpose)
        self.assertEqual(first_pack.profile_id, second_pack.profile_id)

    def test_observation_does_not_change_matter(self) -> None:
        self._pack(self.world)
        before = self.world.total_matter()
        observe_scout_packs(self.world)
        self.assertAlmostEqual(
            self.world.ledger.delta(self.world.total_matter()), 0.0, places=9
        )
        self.assertAlmostEqual(self.world.total_matter(), before, places=9)

    def test_pack_purpose_and_profile_are_closed_and_hashed(self) -> None:
        self._pack(self.world, pack_id="hash_scout")
        original = self.world.state_hash()
        self.world.packs["hash_scout"].purpose = "party"
        ordinary = self.world.state_hash()
        self.world.packs["hash_scout"].profile_id = "forester"
        profile = self.world.state_hash()
        self.assertEqual(len({original, ordinary, profile}), 3)
        with self.assertRaises(ValueError):
            self._pack(self.world, purpose="tower", pack_id="bad_purpose")
        with self.assertRaises(ValueError):
            self._pack(self.world, profile_id="tower", pack_id="bad_profile")

    def test_route_is_visible_but_third_ring_without_route_is_not(self) -> None:
        self._pack(self.world, route=(CENTER, DISTANCE_THREE))
        observed = {event["tile_id"] for event in observe_scout_packs(self.world)}
        self.assertIn(DISTANCE_THREE, observed)
        self.assertNotIn("t_06_03", observed)


class TestBaronyScouting(unittest.TestCase):
    """Стартовый разведывательный Pack баронства проходит полный цикл."""

    def test_twelve_months_delivers_scout_reports(self) -> None:
        world = load_scenario(BARONY, seed=4242)
        pack = world.packs["pack_scout_hill"]
        before = len(known_tiles(world))
        self.assertEqual(pack.eta_date.day, 2)
        for _ in range(12):
            run_month(world)
        reports = [
            report
            for report in delivered_reports(world, world.clock.date)
            if report.source == "scout"
        ]
        self.assertGreater(len(reports), 0)
        self.assertGreater(len(known_tiles(world)), before)
        self.assertEqual(pack.status, "arrived")

    def test_observation_without_report_does_not_change_knowledge(self) -> None:
        world = load_scenario(BARONY, seed=4242)
        before = known_tiles(world)
        events = observe_scout_packs(world, world.clock.date)
        self.assertTrue(events)
        self.assertEqual(known_tiles(world), before)

    def test_same_seed_same_state_hash_after_twelve_months(self) -> None:
        worlds = [load_scenario(BARONY, seed=4242) for _ in range(2)]
        for world in worlds:
            for _ in range(12):
                run_month(world)
        self.assertEqual(worlds[0].state_hash(), worlds[1].state_hash())


def _run_canonical(scenario, months: int, seed: int):
    """Прогнать канонический сценарий и вернуть `(мир, ряд месячного голода)`.

    Свой ряд нужен для проверки счётчика: `RunResult` отдаёт только итог, а
    закон «счётчик не убывает и сходится с книгой» без ряда не проверяется.
    """
    world = load_scenario(scenario, seed=seed)
    script = list(world.script)
    hunger: list[int] = []
    for month in range(1, months + 1):
        for entry in script:
            if int(entry.get("at_month", 0)) == month:
                _apply_script_entry(world, entry)
        run_month(world)
        hunger.append(int(world.stats.get("hunger_months", 0)))
    return world, hunger


def _run_barony(months: int, *, drop_scout: bool):
    """Прогнать эталонный мир `v0_barony_100` и вернуть живой мир.

    `drop_scout=True` убирает партию `pack_scout_hill` ДО тика, не трогая
    сценарий на диске: два мира должны отличаться ровно партией.
    """
    world = load_scenario(BARONY, seed=4242)
    if drop_scout:
        world.packs.pop("pack_scout_hill", None)
    for month in range(1, months + 1):
        for entry in world.script:
            if int(entry.get("at_month", 0)) == month:
                _apply_script_entry(world, entry)
        run_month(world)
    return world


def _material_fingerprint(world) -> dict[str, float]:
    """Всё, что разведка НЕ имеет права менять: материя, дворы, голод, деньги.

    Клетки игрока (`known_tiles`) и хеш мира сюда НЕ входят намеренно: знание —
    единственное, что разведка обязана менять (ADR 0074, ADR 0087).
    """
    court = world.player.court_settlement_id
    stores = world.get_stock(f"settlement:{court}")
    return {
        "living": sum(1 for hh in world.households.values() if hh.left_at is None),
        "households_left": float(world.stats.get("households_left", 0)),
        "persons_left": float(world.stats.get("persons_left", 0)),
        "hunger_months": float(world.stats.get("hunger_months", 0)),
        "grain": stores.amounts.get("grain", 0.0),
        "salt": stores.amounts.get("salt", 0.0),
        "silver": stores.amounts.get("silver", 0.0),
        "rent_collected": float(world.stats.get("rent_collected", 0.0)),
        "relief_given": float(world.stats.get("relief_given", 0.0)),
        "total_matter": world.total_matter(),
    }


class TestScoutingCanons(unittest.TestCase):
    """Канон: разведка — чистый наблюдатель материи, а знание ведёт людей.

    **Эталон как таковой отменён, и это не перенос чисел** (ADR 0223). Прежний
    тест `test_canon_households_hunger_salt_and_delta` держал
    `living_households` 13 / 17 / 27, `hunger_months` 28 / 361 / 507 и
    `castle_stores["salt"] = 15.6` на трёх сценариях. Это не законы разведки,
    а окна приёмки экономики, прибитые к составу мира; они краснели на каждой
    пересборке и требовали переноса вместо проверки. Имя теста сохранено — но
    теперь каждое из четырёх слов в нём проверяется как правило.

    Хуже того: имя класса было ложным, и замер это показал. «Разведка не меняет
    числа мира» **неверно по замыслу**, а не устарело:

    * месяцы 1–3 на эталонном мире `v0_barony_100` (сид 4242) с партией
      `pack_scout_hill` и без неё материально совпадают до последнего знака —
      наблюдение не двигает ни грамма; `known_tiles` при этом 18 против 7;
    * с 4-го месяца миры расходятся: `move_0001_03_hh_hill_sokeman_001`
      идёт на `t_46_51` — клетку, которую открыла разведка, — вместо
      `t_46_50`, и с этого месяца расходится оброк.

    То есть знание доезжает до экономики **без приказа игрока**: оно рулит
    маршрутом переселенца. Это игра, а не дефект, и новый канон говорит
    именно об этом, а не «разведка инертна».

    **Что стало эталоном.** Эталон — эталонный МИР `v0_barony_100.yml`, а не
    набор чисел. Абсолютные числа о его composition живут в
    `tests/test_barony_100.py` (150 дворов, 600 человек, 4 поселения, И-1,
    детерминизм; ADR 0207) и в `design/scenarios/ACCEPTANCE.md`; здесь они не
    дублируются, потому что дублирование и породило шесть красных.
    """

    def test_canon_households_hunger_salt_and_delta(self) -> None:
        """ЗАКОН 1 и 2: четыре предмета канона проверяются как ЗАКОНЫ, не числа.

        Имя теста сохранено, и теперь оно правдиво: каждое из четырёх слов
        проверяется, но не значением, а правилом. Прежде тест держал
        `living_households` 13 / 17 / 27, `hunger_months` 28 / 361 / 507 и
        `castle_stores["salt"] = 15.6` — это окна приёмки экономики, прибитые к
        composition мира (ADR 0223).

        **Дворы — книжная сверка.** `живых + ушедших == заявлено в сценарии`.
        Состав читается из YAML, а не из памяти агента, и проверка ловит
        настоящую дыру: двор, исчезнувший из `households` без записи в
        `households_left`.

        **Голод — счётчик, а не итог.** Ряд месячных значений не убывает, не
        отрицателен и сходится с `stats["hunger_months"]`. Мутация «счётчик
        голода сбросился» роняет пункт.

        **Соль — только обозом извне.** Вся соль, дошедшая до усадьбы, приходит
        переводом `caravan_unload` из `pack:*`, и ни одного зерна соли не
        появилось в усадьбе извне (`external_in`) или телепортом из другого
        поселения. Мутация «соль перелетела из `settlement:salt_village`»
        роняет пункт.

        **Дельта — ноль, и мир читает свой seed.** И-1 на всех прогонах; пара
        seed'ов обязана и совпасть, и разойтись. Вторая часть не перестраховка:
        пара «два прогона с ОДНИМ seed» по построению не ловит мир, который
        seed игнорирует. Именно эту слепую зону держала зашитая константа
        `state_hash` в `test_start_stand`, и именно она заменена в обоих тестах
        явной парой seed (ADR 0223 закон 2).
        """
        cases = (
            (HILL, 36, 1729),
            (TWO, 60, 42),
            (SHIRE, 60, 1729),
        )
        for scenario, months, seed in cases:
            with self.subTest(scenario=scenario.name):
                pristine = load_scenario(scenario, seed=seed)
                declared = len(pristine.households)
                court_id = f"settlement:{pristine.player.court_settlement_id}"
                salt_at_start = pristine.get_stock(court_id).amounts.get("salt", 0.0)
                first, hunger = _run_canonical(scenario, months, seed)
                second, _ = _run_canonical(scenario, months, seed)
                other_seed, _ = _run_canonical(scenario, months, seed + 1)

                for label, world in (
                    ("seed", first), ("seed повторно", second),
                    ("другой seed", other_seed),
                ):
                    self.assertAlmostEqual(
                        world.ledger.delta(world.total_matter()), 0.0, places=6,
                        msg=f"И-1: материя появилась из ничего ({label})",
                    )
                    self.assertGreater(world.total_matter(), 0.0)

                self.assertEqual(
                    first.state_hash(), second.state_hash(),
                    "Тот же seed дал разные миры",
                )
                self.assertNotEqual(
                    first.state_hash(), other_seed.state_hash(),
                    f"{scenario.name} не читает seed: пара одинаковых прогонов "
                    f"этого не видит",
                )

                living = sum(
                    1 for hh in first.households.values() if hh.left_at is None
                )
                left = int(first.stats.get("households_left", 0))
                self.assertEqual(
                    living + left, declared,
                    f"Дворы теряются между счётом и книгой: живых {living} + "
                    f"ушедших {left} != заявлено {declared}",
                )

                self.assertTrue(
                    all(value >= 0 for value in hunger),
                    f"Счётчик голода ушёл в минус: {hunger}",
                )
                self.assertTrue(
                    all(
                        after >= before
                        for before, after in zip(hunger, hunger[1:])
                    ),
                    f"Счётчик голода убывает — он не счётчик: {hunger}",
                )
                self.assertEqual(
                    hunger[-1], int(first.stats.get("hunger_months", 0)),
                    "Счётчик голода в книге разошёлся с месячной сводкой",
                )

                court_id = f"settlement:{first.player.court_settlement_id}"
                salt = [
                    entry
                    for entry in first.ledger.entries
                    if entry.dst_id == court_id and entry.good == "salt"
                ]
                self.assertTrue(salt, "Ни грамма соли не дошло до усадьбы")
                for entry in salt:
                    with self.subTest(kind=entry.kind, src=entry.src_id):
                        self.assertEqual(entry.kind, "transfer")
                        self.assertEqual(
                            entry.reason, "caravan_unload",
                            "Соль дошла до усадьбы не обозом",
                        )
                        self.assertTrue(
                            (entry.src_id or "").startswith("pack:"),
                            f"Соль пришла не извне, а из {entry.src_id}: "
                            f"телепорт между поселениями",
                        )
                self.assertAlmostEqual(
                    first.get_stock(court_id).amounts.get("salt", 0.0),
                    salt_at_start + sum(entry.amount for entry in salt),
                    places=6,
                    msg="Соль в амбаре усадьбы разошлась с реестром "
                        "(стартовый запас + приходы обозов)",
                )


    def test_scouting_moves_no_matter_but_knowledge_steers_people(self) -> None:
        """ЗАКОН 3: разведка не двигает материю. Закон 4: знание ведёт людей.

        **Закон 3** — эталонный мир `v0_barony_100`, сид 4242, три месяца.
        Два одинаковых мира, из одного убрана партия `pack_scout_hill`. Живых
        дворов, голодных месяцев, зерна/соли/серебра усадьбы, оброка, подачи и
        ВСЕЙ материи мира должно быть ровно столько же. Мутация «наблюдение
        создаёт зерно» роняет пункт с первого месяца.

        **Закон 4** — то же самое, но про знание. Расхождение `known_tiles`
        обязано быть ненулевым, иначе проверка закона 3 пустая: если бы партия
        ничего не открыла, совпадение material ничего не доказывало бы. Ровно
        это и было сломанным каноном — «разведка не меняет мир», — поэтому
        закон 4 стоит рядом и говорит, где заканчивается невинность разведки.
        """
        with_scout = _run_barony(3, drop_scout=False)
        without_scout = _run_barony(3, drop_scout=True)

        # Закон 3: материальная часть совпадает.
        self.assertEqual(
            _material_fingerprint(with_scout), _material_fingerprint(without_scout),
            "Партия разведки сдвинула материю мира",
        )
        for world in (with_scout, without_scout):
            self.assertAlmostEqual(
                world.ledger.delta(world.total_matter()), 0.0, places=6,
                msg="И-1: материя появилась из ничего",
            )

        # Закон 4: знание — нет, и разница обязана быть настоящей.
        self.assertGreater(
            len(known_tiles(with_scout)), len(known_tiles(without_scout)),
            "Партия разведки не открыла ни одной клетки: проверка закона 3 "
            "пустая — совпадать не о чем",
        )

    def test_knowledge_reaches_the_economy_through_migration_not_by_matter(self) -> None:
        """ЗАКОН 4, вторая половина: с 4-го месяца знание УЖЕ ведёт людей.

        Это замер, записанный законом, а не реестром. На эталонном мире без
        разведки партия переселенцев идёт на `t_46_50`, с разведкой — на
        `t_46_51`, клетку, которую открыл дозор; с этого месяца расходится
        оброк. Мир не сломан — знание доехало до маршрута без приказа игрока.

        Тест не закрепляет ни клетку, ни месяц, ни сумму: он требует, чтобы
        РАЗНИЦА была объяснима разведкой, и краснеет, если разведка начнёт
        менять материю сама. Если следующий агент захочет запретить разведке
        влиять на маршруты — это отдельное решение с отдельным ADR, и молча
        править этот тест нельзя.
        """
        with_scout = _run_barony(4, drop_scout=False)
        without_scout = _run_barony(4, drop_scout=True)
        routes_with = {
            pack_id: tuple(pack.route) for pack_id, pack in with_scout.packs.items()
        }
        routes_without = {
            pack_id: tuple(pack.route) for pack_id, pack in without_scout.packs.items()
        }
        self.assertTrue(
            set(routes_with) & set(routes_without),
            "Партий переселенцев не осталось ни в одном мире",
        )
        shared = sorted(set(routes_with) & set(routes_without))
        moved = [pack_id for pack_id in shared if routes_with[pack_id] != routes_without[pack_id]]
        self.assertTrue(
            moved,
            "Ни один переселенец не сменил маршрут из-за разведки: прошлый "
            "замер (4-й месяц, `t_46_51` против `t_46_50`) больше не повторяется "
            "и канон надо переписать заново",
        )
        # Разница маршрутов обязана быть разницей ЗНАНИЯ, а не материи.
        for pack_id in moved:
            opened = set(known_tiles(with_scout)) - set(known_tiles(without_scout))
            arrived_only_with_scout = set(routes_with[pack_id]) - set(routes_without[pack_id])
            self.assertTrue(
                arrived_only_with_scout & opened,
                f"{pack_id} сменил маршрут не на ту клетку, которую открыла "
                f"разведка: {sorted(arrived_only_with_scout)} против "
                f"{sorted(opened)}",
            )
        for world in (with_scout, without_scout):
            self.assertAlmostEqual(
                world.ledger.delta(world.total_matter()), 0.0, places=6,
                msg="И-1: материя появилась из ничего",
            )


if __name__ == "__main__":
    unittest.main()
