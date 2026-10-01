"""Баронство 100×100: 600 человек, 150 дворов, четыре специализированных поселения.

Прежняя редакция сценария объявляла 600 ДВОРОВ (1800 человек) и двенадцать
поселений, девять из которых стояли на одинаковых пятнадцати пашнях под именами
«Лесная», «Мельничная», «Речная». Оба числа были ошибкой, и оба проверяются
здесь как числа, а не как намерение: `TestBaronyPopulation` и
`TestSpecialisation` — обвинители, роняющиеся при возврате старой карты.
"""

from __future__ import annotations

import time
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import yaml

from hillcourt.engine.growth import index_growth_tiles, run_detailed_growth
from hillcourt.engine.hexgrid import axial_is_neighbor, offset_to_axial
from hillcourt.engine.manor import set_tile_regime
from hillcourt.engine.terrain import WILD_TERRAINS
from hillcourt.engine.tick import phase_growth, run_month
from hillcourt.ontology import Right
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_barony_100.yml"

#: Цель баронства, объявленная хозяином студии: 600 ЖИТЕЛЕЙ, не 600 дворов.
POPULATION = 600
LATE_STAGE_HOUSEHOLDS = 200

#: Кап жилых дворов на гекс (`scenario._household_cap`, ADR 0083/0132).
RURAL_CAP = 5
CITY_CAP = 20

#: Что поселение ОБЕЩАЕТ рельефом своих `works_tiles`. Не украшение, а проверка:
#: двор работает на клетке, только если она в угодьях его поселения
#: (`docs/07_legal.md:113`), поэтому рельеф `works_tiles` и есть продукт.
#: Ключ —settlement_id, значение — (доля рельефа, рецепты, которые должны идти).
SPECIALISATION = {
    "hill_court": (
        "field",
        0.6,
        ("harvest_grain",),
    ),
    "village_forest": (
        "forest",
        0.3,
        ("cut_wood", "cut_firewood"),
    ),
    "native_village": (
        "pasture",
        0.4,
        ("gather_hay",),
    ),
    "salt_village": (
        "salt_flat",
        0.6,
        ("boil_salt",),
    ),
}

#: Слово в `name` поселения → рельеф, который оно обещает. Обвинитель на
#: «поселение названо лесным, а все его works_tiles — пашни»: переименование
#: без смены рельефа роняет тест, и это ровно тот брак, что был в сцене.
NAME_PROMISES_TERRAIN = {
    "лесн": "forest",
    "лес": "forest",
    "сол": "salt_flat",
    "соля": "salt_flat",
    "луг": "pasture",
    "пашн": "field",
}


def _data() -> dict:
    with SCENARIO.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _river_cells(data: dict) -> set[str]:
    flows = {
        str(river["id"]): [(int(point[0]), int(point[1])) for point in river["flow_order"]]
        for river in data["rivers"]
    }
    return {f"t_{x:02d}_{y:02d}" for flow in flows.values() for x, y in flow}


class TestBaronyMap(unittest.TestCase):
    """Скелет, река и точки мира являются сценарными данными."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = load_scenario(SCENARIO, seed=1729)
        cls.data = _data()

    def test_grid_has_ten_thousand_hexes(self) -> None:
        self.assertEqual(len(self.world.tiles), 10_000)

    def test_river_is_six_connected_metadata(self) -> None:
        cells = _river_cells(self.data)
        self.assertEqual(
            {str(r["id"]): len(r["flow_order"]) for r in self.data["rivers"]},
            {"main_river": 126, "north_tributary": 41, "south_tributary": 41},
        )
        self.assertEqual(len(cells), 199)
        for river in self.data["rivers"]:
            flow = [(int(p[0]), int(p[1])) for p in river["flow_order"]]
            for before, after in zip(flow, flow[1:]):
                self.assertTrue(axial_is_neighbor(offset_to_axial(*before), offset_to_axial(*after)))
        self.assertTrue(all(self.world.tiles[tid].terrain == "water" for tid in cells))

    def test_forests_pastures_wilds_and_marks(self) -> None:
        terrains = Counter(tile.terrain for tile in self.world.tiles.values())
        for name in ("forest", "pasture", "heath", "marsh", "salt_flat", "field"):
            self.assertGreater(terrains[name], 0, f"на карте нет рельефа {name}")
        for mark, point in (
            ("hermitage", (15, 65)),
            ("waystation", (80, 15)),
            ("smoke", (35, 60)),
            ("smoke", (60, 20)),
            ("lost_caravan", (60, 85)),
            ("mine", (25, 75)),
            ("mooring", (55, 95)),
        ):
            self.assertTrue(
                getattr(self.world.tiles[f"t_{point[0]:02d}_{point[1]:02d}"], mark, False),
                f"метка {mark} в [{point[0]}, {point[1]}] не стоит",
            )


class TestBaronyPopulation(unittest.TestCase):
    """ГЛАВНОЕ ЧИСЛО: 600 человек, 150 дворов, четыре поселения.

    Мутация, которая обязана ронять тест: вернуть в сцену `count: 96` у
    квартальных дворов (прежние 600 дворов = 1800 человек). Тогда падают и
    `households`, и `people`, и `four_person_households` сразу.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = load_scenario(SCENARIO, seed=1729)

    def test_population_is_six_hundred_people_not_six_hundred_households(self) -> None:
        self.assertEqual(len(self.world.households), 150)
        self.assertEqual(len(self.world.persons), POPULATION)

    def test_four_person_households(self) -> None:
        """4 человека на двор: 2 взрослых + 2 ребёнка, и это объяснено в шапке.

        Мутация: `children: 1` у групп даёт 450 человек — тест падает.
        """
        counts = Counter(
            world_person.age_class for world_person in self.world.persons.values()
        )
        self.assertEqual(counts["adult"], 300)
        self.assertEqual(counts["child"], 300)
        # Ровно один человек двора в разведке: `pack_scout_hill` уводит `_p2`
        # генеата в `Pack` и убирает его из `member_ids` двора (закон Pack, а не
        # неполная семья). Поэтому дворов с тремя людьми ровно один, и он тот.
        in_pack = {
            person_id
            for pack in self.world.packs.values()
            for person_id in pack.member_ids
        }
        self.assertEqual(len(in_pack), 1, "в поход ушёл не один человек")
        sizes = Counter(
            len(household.member_ids) for household in self.world.households.values()
        )
        self.assertEqual(sizes[4], 149, "не у всех дворов по четыре человека")
        self.assertEqual(sizes[3], 1, "дворов без одного человека не ровно один")
        scout_owner = next(
            pack.owner_household_id
            for pack in self.world.packs.values()
            if pack.member_ids
        )
        self.assertEqual(
            len(self.world.households[scout_owner].member_ids), 3,
            "уменьшенный двор — не тот, чей человек в походе",
        )

    def test_four_settlements(self) -> None:
        inhabited = {
            sid for sid, s in self.world.settlements.items() if s.household_ids
        }
        self.assertEqual(
            inhabited,
            {"hill_court", "village_forest", "native_village", "salt_village"},
            "поселений не 4 или одно из них пусто: карта разъехалась по числу рук",
        )
        self.assertEqual(len(self.world.settlements), 4)

    def test_the_capital_is_clearly_the_biggest(self) -> None:
        """Замок должен читаться столицей, иначе это просто деревня побольше."""
        sizes = {
            sid: len(s.household_ids) for sid, s in self.world.settlements.items()
        }
        capital = sizes["hill_court"]
        for sid, size in sizes.items():
            if sid == "hill_court":
                continue
            self.assertGreaterEqual(
                capital, size * 2,
                f"столица {capital} дворов не вдвое больше {sid} ({size})",
            )

    def test_room_to_reach_the_late_stage(self) -> None:
        """Поздняя стадия — до 200 дворов. Место должно хватать БЕЗ новой карты.

        Проверяется не «сейчас есть 150», а «кап гексов держит 200».
        """
        capacity = sum(
            (CITY_CAP if s.kind == "hill_court" else RURAL_CAP) * self._home_tiles(s.id)
            for s in self.world.settlements.values()
        )
        self.assertGreaterEqual(
            capacity, LATE_STAGE_HOUSEHOLDS,
            f"мест {capacity}, а поздняя стадия просит {LATE_STAGE_HOUSEHOLDS}",
        )
        self.assertLess(len(self.world.households), LATE_STAGE_HOUSEHOLDS)

    def _home_tiles(self, settlement_id: str) -> int:
        entry = next(
            entry for entry in _data()["settlements"] if entry["id"] == settlement_id
        )
        return len(entry.get("household_tiles") or []) or 1


class TestBaronyPlacement(unittest.TestCase):
    """Кап жилых дворов — закон, а не «поместилось»."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = load_scenario(SCENARIO, seed=1729)
        cls.data = _data()

    def test_no_hex_exceeds_the_household_cap(self) -> None:
        per_tile = Counter(
            self.world.households[hid].current_tile_id for hid in self.world.households
        )
        city_tiles = {
            f"t_{x:02d}_{y:02d}"
            for x, y in next(
                entry for entry in self.data["settlements"] if entry["id"] == "hill_court"
            )["household_tiles"]
        }
        for tile_id, count in per_tile.items():
            cap = CITY_CAP if tile_id in city_tiles else RURAL_CAP
            self.assertLessEqual(
                count, cap, f"гекс {tile_id}: {count} дворов при капе {cap}"
            )

    def test_every_settlement_fits_its_declared_home_tiles(self) -> None:
        """Дворы объявлены в `household_tiles` поселения, а не «куда влезло»."""
        for entry in self.data["settlements"]:
            sid = str(entry["id"])
            declared = {
                f"t_{int(p[0]):02d}_{int(p[1]):02d}"
                for p in (entry.get("household_tiles") or [])
            }
            if not declared:
                continue
            placed = {
                self.world.households[hid].current_tile_id
                for hid in self.world.settlements[sid].household_ids
            }
            self.assertTrue(
                placed <= declared,
                f"поселение {sid}: дворы стоят вне объявленных клеток {sorted(placed - declared)}",
            )

    def test_salt_and_native_workers_are_landless_and_employable(self) -> None:
        """Солеварня — наёмное предприятие, род — община; оба вне книги манора."""
        for sid in ("salt_village", "native_village"):
            for hid in self.world.settlements[sid].household_ids:
                self.assertEqual(
                    self.world.households[hid].legal_status_id,
                    "free_landless",
                    f"{hid}: не безземельный",
                )


class TestSpecialisation(unittest.TestCase):
    """ОБВИНИТЕЛЬ: название без рельефа — враньё.

    Прежняя сцена объявляла девять поселений, названных по специализации
    («Лесная», «Мельничная», «Речная», «Южная», …), и ВСЕ ДЕВЯТЬ стояли на
    пятнадцати пашнях: `Лесная` целиком на поле, `Мельничная` без воды,
    `Речная` без единой водной клетки. Здесь три проверки, каждая из которых
    роняется на возврат той карты.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = load_scenario(SCENARIO, seed=1729)
        cls.data = _data()

    def _terrain(self, settlement_id: str) -> Counter:
        settlement = self.world.settlements[settlement_id]
        return Counter(
            self.world.tiles[tile_id].terrain for tile_id in settlement.works_tiles
        )

    def test_no_two_settlements_are_the_same_place(self) -> None:
        """Обвинитель №1: два поселения с ОДИНАКОВЫМ рельефом — это одно поселение
        под двумя именами, и одна из специализаций враньё.

        Мутация: скопировать `works_tiles` одного поселения в другое.
        """
        signatures = {
            sid: tuple(sorted(self._terrain(sid).items()))
            for sid in self.world.settlements
        }
        seen: dict[tuple, str] = {}
        for sid, signature in sorted(signatures.items()):
            self.assertNotIn(
                signature, seen,
                f"поселения {seen.get(signature)} и {sid} стоят на одном рельефе {signature}",
            )
            seen[signature] = sid

    def test_settlement_name_promises_its_terrain(self) -> None:
        """Обвинитель №2: «Лесная» без лесной клетки.

        Мутация: переименовать `village_forest` в «Лесная» нельзя — он уже так
        называется; можно переставить `works_tiles` двух поселений местами, и
        тогда имя и рельеф разойдутся.
        """
        for entry in self.data["settlements"]:
            sid = str(entry["id"])
            name = str(entry["name"]).lower()
            promised = {
                terrain
                for word, terrain in NAME_PROMISES_TERRAIN.items()
                if word in name
            }
            if not promised:
                continue
            terrain = self._terrain(sid)
            for want in promised:
                self.assertGreater(
                    terrain[want], 0,
                    f"поселение '{entry['name']}' обещает рельеф {want}, "
                    f"а его works_tiles — {dict(terrain)}",
                )

    def test_each_settlement_carries_its_declared_product(self) -> None:
        """Обвинитель №3: заявленная специализация РЕАЛЬНО даёт товар.

        Не «террасы правильные», а «появились проводки `cut_wood`/`boil_salt`
        в `Ledger`». Назвать деревню лесной и не дать ей леса нельзя.
        """
        world = load_scenario(SCENARIO, seed=1729)
        for _ in range(6):
            run_month(world)
        reasons = {entry.reason for entry in world.ledger.entries}
        for settlement_id, (terrain, share, recipes) in SPECIALISATION.items():
            works = self.world.settlements[settlement_id].works_tiles
            got = self._terrain(settlement_id)[terrain]
            self.assertGreaterEqual(
                got / len(works), share,
                f"{settlement_id}: рельефа {terrain} {got} из {len(works)} клеток, "
                f"а обещано {share:.0%}",
            )
            for recipe in recipes:
                self.assertIn(
                    recipe, reasons,
                    f"{settlement_id} обещает {recipe}, а в леджере его нет за 6 месяцев",
                )

    def test_a_settlement_on_barren_ground_produces_nothing(self) -> None:
        """Обоснование закона выше, а не украшение.

        На `heath` и `water` не растёт НИЧЕГО: правил `grow_*` с таким рельефом
        в `spawn_rules.yml` нет. Значит поселение, у которого нет ни пашни, ни
        леса, ни луга, ни солончака, — не поселение, а подпись на карте. Такое
        запрещено, и проверка ловит его раньше, чем агент сдаст карту.
        """
        grows = {
            str(params["terrain"])
            for params in (rule.get("params") or {} for rule in _data_spawn_rules())
            if params.get("good") and params.get("terrain")
        }
        barren = {"heath", "water"} - grows
        self.assertTrue(barren, "правил роста не нашлось — проверка бессмысленна")
        for settlement_id in self.world.settlements:
            terrain = self._terrain(settlement_id)
            productive = sum(
                count for name, count in terrain.items() if name in grows
            )
            self.assertGreater(
                productive, len(self.world.settlements[settlement_id].works_tiles) // 2,
                f"{settlement_id}: угодья {dict(terrain)} — рельеф, на котором "
                f"не растёт ничто ({sorted(barren)})",
            )


def _data_spawn_rules() -> list[dict]:
    rules = yaml.safe_load(
        (ROOT / "design" / "catalogs" / "spawn_rules.yml").read_text(encoding="utf-8")
    )
    return list(rules["spawn_rules"])


class TestBaronyLawIsWired(unittest.TestCase):
    """Наделы, обозы и соль — то, чего в сцене прежней не было вовсе."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = load_scenario(SCENARIO, seed=4242)

    def test_declared_holdings_pay_rent(self) -> None:
        """Обвинитель: надел без оброчной повинности — это титул, а не доход.

        Мутация: убрать блок `rights:` — повинностей оброка не будет вовсе,
        и `lord_income` останется нулём.
        """
        from hillcourt.runner import lord_income

        rent = [o for o in self.world.obligations.values() if o.kind == "rent"]
        self.assertGreaterEqual(len(rent), 10, "книга земли пуста: оброк некому платить")
        self.assertTrue(
            any(o.basis == "share" and o.right_id for o in rent),
            "нет оброка-доли, привязанного к праву (ADR 0206/0192)",
        )
        for _ in range(12):
            run_month(self.world)
        self.assertGreater(
            lord_income(self.world), 0.0, "за год ни один двор не заплатил оброка"
        )

    def test_a_sokeman_pays_fixed_rent_and_not_a_share(self) -> None:
        """Сокмен платит `fixed_rent_grain_or_pence`, а не долей: две разные повинности.

        Мутация: убрать `grant_tenure` из `_apply_declared_holdings` — сокмен
        получит оброчную долю, и тест упадёт на `basis`.
        """
        sokeman = self.world.obligations["obl_hh_hill_sokeman_001_fixed_rent_grain_or_pence"]
        self.assertEqual(sokeman.basis, "fixed")
        self.assertIsNone(sokeman.right_id)
        shares = [
            o for o in self.world.obligations.values()
            if o.kind == "rent" and o.basis == "share"
            and o.household_id and o.household_id.startswith("hh_hill_sokeman")
        ]
        self.assertEqual(shares, [], "сокмену завели оброчную долю поверх твёрдой ренты")

    def test_salt_reaches_the_castle_by_caravan_and_not_by_teleport(self) -> None:
        """Цепочка соли обязана быть `household → pack → settlement`.

        Мутация: телепорт `household → settlement:hill_court` или `external_in`
        любого количества роняют тест.
        """
        for _ in range(12):
            run_month(self.world)
        salt = [e for e in self.world.ledger.entries if e.good == "salt"]
        load = [e for e in salt if e.reason == "caravan_load"]
        unload = [e for e in salt if e.reason == "caravan_unload"]
        teleport = [
            e for e in salt
            if e.kind == "transfer"
            and e.src_id.startswith("household:")
            and e.dst_id == "settlement:hill_court"
        ]
        external = [e for e in salt if e.kind == "external_in"]
        self.assertTrue(load, "соль никуда не везли: обоз мёртв")
        self.assertTrue(unload, "соль не выгружали: обоз не дошёл")
        self.assertGreater(sum(e.amount for e in unload), 0.0)
        self.assertEqual(teleport, [], "соль перелетела из двора в замок телепортом")
        self.assertEqual(external, [], "соль взялась из ниоткуда (И-1)")
        for entry in unload:
            self.assertTrue(
                entry.src_id.startswith("pack:"),
                f"соль выгружена не из воза, а из {entry.src_id}",
            )

    def test_matter_is_conserved_over_a_year(self) -> None:
        world = load_scenario(SCENARIO, seed=4242)
        for _ in range(12):
            run_month(world)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


class TestBaronyRun(unittest.TestCase):
    """И-6: один seed дважды — один мир; другой seed — другой."""

    def test_replay_and_seed_divergence(self) -> None:
        first = load_scenario(SCENARIO, seed=4242)
        second = load_scenario(SCENARIO, seed=4242)
        other = load_scenario(SCENARIO, seed=1729)
        for _ in range(6):
            run_month(first)
            run_month(second)
            run_month(other)
        self.assertEqual(first.state_hash(), second.state_hash())
        self.assertNotEqual(
            first.state_hash(), other.state_hash(),
            "два разных seed дали один мир: случайность не идёт через именованные потоки",
        )

    def test_script_orders_are_not_refused(self) -> None:
        """Приказ сценария, который отказали, — это не приёмка (ADR 0202)."""
        from hillcourt.runner import _apply_script_entry

        world = load_scenario(SCENARIO, seed=4242)
        for month in range(1, 13):
            for entry in world.script:
                if int(entry.get("at_month", 0)) == month:
                    for record in _apply_script_entry(world, entry):
                        self.assertFalse(
                            record.get("refused"),
                            f"приказ отказан: {record.get('reason')}",
                        )
            run_month(world)


class TestLazyGrowth(unittest.TestCase):
    """Индекс роста зависит от поселений, а не от размера coarse-карты."""

    def test_index_covers_worked_land(self) -> None:
        world = load_scenario(SCENARIO, seed=1729)
        regimes = world.catalogs.land_regimes
        for settlement in world.settlements.values():
            self.assertIn(
                f"t_{settlement.coord[0]:02d}_{settlement.coord[1]:02d}",
                world.growth_tile_ids,
            )
            for tile_id in settlement.works_tiles:
                self.assertIn(tile_id, world.growth_tile_ids, f"угодье {tile_id} вне индекса")
        demesne = {
            tile_id
            for manor in world.manors.values()
            for tile_id in manor.tile_ids
            if (regime := regimes.get(world.tiles[tile_id].regime_id)) is not None
            and regime.requires_labor_days
        }
        self.assertTrue(demesne, "Ни одной доменной клетки — проверка пустая")
        for tile_id in sorted(demesne):
            self.assertIn(tile_id, world.growth_tile_ids, f"Домен {tile_id} вне индекса")
        self.assertEqual(world.growth_tile_ids, index_growth_tiles(world))
        self.assertLess(
            len(world.growth_tile_ids),
            len(world.tiles) // 10,
            "Индекс роста съел coarse-карту: ленивый рост перестал быть ленивым",
        )

    def test_only_a_real_holding_enters_land_into_growth(self) -> None:
        """Мутация: сменить `kind` права `tenure` → `common` и поставить обратно."""
        world = load_scenario(SCENARIO, seed=1729)
        outside = next(
            tile_id
            for tile_id in sorted(world.tiles)
            if tile_id not in set(index_growth_tiles(world))
        )
        right = Right(
            id="right_test_common",
            holder_household_id="hh_wood_probe",
            tile_id=outside,
            kind="common",
            granted_date=world.clock.date,
            rent_share=0.0,
        )
        world.rights[right.id] = right
        self.assertNotIn(outside, index_growth_tiles(world))
        right.kind = "tenure"
        self.assertIn(outside, index_growth_tiles(world))

    def test_land_regime_moves_land_into_and_out_of_growth(self) -> None:
        world = load_scenario(SCENARIO, seed=1729)
        regimes = world.catalogs.land_regimes
        labour = sorted(
            rid for rid, r in regimes.items() if r.requires_labor_days
        )
        idle = sorted(rid for rid, r in regimes.items() if not r.requires_labor_days)
        self.assertTrue(labour and idle)
        outside = next(
            tile_id
            for tile_id in sorted(world.tiles)
            if tile_id not in set(index_growth_tiles(world))
        )
        set_tile_regime(world, outside, labour[0])
        self.assertIn(outside, index_growth_tiles(world))
        set_tile_regime(world, outside, idle[0])
        self.assertNotIn(outside, index_growth_tiles(world))

    def test_growth_phase_does_not_scan_coarse_tiles(self) -> None:
        """Рост обходится индексом поселений, а не всей coarse-картой."""
        world = load_scenario(SCENARIO, seed=1729)
        small = SimpleNamespace(
            tiles={tid: world.tiles[tid] for tid in world.growth_tile_ids},
            growth_tile_ids=world.growth_tile_ids,
            catalogs=world.catalogs,
            ledger=world.ledger,
            clock=world.clock,
            get_stock=world.get_stock,
        )
        stock_lookup = world.get_stock
        initial = {
            tile_id: dict(world.get_stock(world.tiles[tile_id].standing_stock_id).amounts)
            for tile_id in world.growth_tile_ids
        }
        state = {"calls": 0}

        def counting_stock(stock_id: str):
            state["calls"] += 1
            return stock_lookup(stock_id)

        small.get_stock = counting_stock
        world.get_stock = counting_stock
        iterations = 30
        samples = 5

        def restore() -> None:
            for tile_id, amounts in initial.items():
                world.get_stock(world.tiles[tile_id].standing_stock_id).amounts = dict(amounts)

        def measure(target) -> float:
            restore()
            state["calls"] = 0
            started = time.perf_counter()
            for _ in range(iterations):
                run_detailed_growth(target, 0.000001)
            return time.perf_counter() - started

        def calls_for(target) -> int:
            restore()
            state["calls"] = 0
            run_detailed_growth(target, 0.000001)
            return state["calls"]

        self.assertEqual(calls_for(small), calls_for(world))
        small_seconds = min(measure(small) for _ in range(samples))
        large_seconds = min(measure(world) for _ in range(samples))
        self.assertLessEqual(large_seconds, small_seconds * 8.0 + 0.25)

    def test_only_indexed_field_receives_nature(self) -> None:
        world = load_scenario(SCENARIO, seed=1729)
        settlement = next(
            s for s in world.settlements.values() if s.works_tiles
        )
        indexed_field = next(
            tid for tid in world.growth_tile_ids if world.tiles[tid].terrain == "field"
        )
        coarse = next(
            tid for tid, tile in world.tiles.items()
            if tile.terrain == "field" and tid not in world.growth_tile_ids
        )
        before_indexed = world.get_stock(
            world.tiles[indexed_field].standing_stock_id
        ).amounts.get("grain", 0.0)
        before_coarse = world.get_stock(
            world.tiles[coarse].standing_stock_id
        ).amounts.get("grain", 0.0)
        phase_growth(world)
        self.assertGreater(
            world.get_stock(world.tiles[indexed_field].standing_stock_id).amounts.get("grain", 0.0),
            before_indexed,
        )
        self.assertEqual(
            world.get_stock(world.tiles[coarse].standing_stock_id).amounts.get("grain", 0.0),
            before_coarse,
            "coarse-пашня без угодья выросла: рост обошёл индекс",
        )
        self.assertTrue(settlement.works_tiles)


if __name__ == "__main__":
    unittest.main()
