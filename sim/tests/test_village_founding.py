"""Основать деревню приказом: две кнопки, гейт места и цена (ADR 0222).

Задача хозяина: «развивать баронство через повинности, инвестируя общий труд в
строительство дорог, **других деревень**, формирование доходов баронства на
продажу». Третья ступень отсутствовала как действие: `settle_household` жил в
`engine/tile_view.py` как правило места, в `ACTION_HANDLERS` его не было, и
`docs/03_ontology.md` признавал словами «отдельного приказа `settle_household`
нет».

## Что здесь проверяется и чем держится

| проверка | чем держится (что ломает при подмене) |
|---|---|
| оба приказа есть в таблице приказов | `ACTION_HANDLERS`: убрать запись — тест падает. Молчаливый «приказ есть, а нажать нельзя» здесь уже лечили (`test_script_month_key`) |
| поселений 4 → 5 | замер на `start_stand`; без `world.settlements[...] = ...` приказ создаёт пустышку и счёт не растёт |
| угодье — воля игрока | два мира, один и тот же участок, разные `works_tiles` → разные поселения. Подмена «закон подбирает клетки» обрушит равенство |
| цена основания видна в `Ledger` | запись `reason="village_founding"`: src — амбар корня, dst — `sink:waste`. Убрать перевод — падает и сумма бревна, и запись |
| цена посадки видна в `Ledger` | `reason="village_seating"`: src — амбар корня, dst — сток двора, сумма = `monthly_food_need × 3` |
| отказ без бревна не двигает мир | ни поселений, ни бревна, ни материи до отказа |
| **приказ посадки зовёт `can_settle`** | вода / руина / режим `waste` / полный гекс → отказ с причиной по причине. Убрать вызов `settlement_refusal` — падают четыре проверки сразу |
| отказ совпадает с `can_settle` на **каждой** клетке двух миров | равенство `refusal is None ⟺ can_settle` на 12 + 10 000 клеток. Своя копия правила места вместо `can_settle` рассыпается тут же |
| приказ переносит двор между поселениями | `settlement_id`, ростеры обоих поселений, места людей |
| деревня живёт | клетки угодья вошли в `worked_land_tile_ids`, за 12 месяцев посев на них вырос, двор не голодал, оброк платится |
| один seed — один `state_hash` | два прогона одного сида; и хеш мира с приказом отличается от мира без приказа |
| `matter_delta == 0` | `world.ledger.delta(world.total_matter())` после 24 месяцев с приказом |

Каждая проверка без приказа даёт конкретный красный тест, а не «зелёное всегда».
Мутации, которые роняют этот файл, перечислены в `test_mutation_notes` и
проверены вживую (см. отчёт агента): убрать гейт места, убрать перевод за
участок, не положить угодье в поселение, вернуть в ростер прежнее поселение.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.growth import worked_land_tile_ids
from hillcourt.engine.manor import manor_stock, root_manor, set_tile_regime
from hillcourt.engine.tile_view import can_settle, household_count, tile_max_households
from hillcourt.engine.tick import run_month
from hillcourt.engine.village import (
    FOUNDABLE_KINDS,
    FOUNDING_GOOD,
    FOUNDING_LOG,
    KIND_REFUSAL,
    SEATING_GOOD,
    SEATING_MONTHS,
    found_village,
    settle_household,
    settlement_refusal,
)
from hillcourt.economy.labor import own_tiles
from hillcourt.economy.needs import monthly_food_need
from hillcourt.legal.actions import grant_tenure
from hillcourt.runner import ACTION_HANDLERS, run
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
STAND = ROOT / "design" / "scenarios" / "start_stand.yml"
BARONY = ROOT / "design" / "scenarios" / "v0_barony_100.yml"
SEED = 1729

#: Участок под новую деревню и его угодья на стенде. Все три клетки свободны
#: и не в угодьях ни одного поселения (замерено), режим земли по умолчанию —
#: `waste`, то есть приказ `set_tile_regime` обязан идти ПЕРВЫМ. Это не
#: inconvenience, а проверка: молча сделав землю годной, приказ украл бы у
#: игрока приказ `set_tile_regime`.
SITE = "t_00_01"
WORKS = ["t_00_02", "t_01_01"]
#: Угодье, уже занятое поселением: `hill_court` объявляет `t_01_00` своим.
CLAIMED_BY_COURT = "t_01_00"
#: Свободная, но пустошная клетка стенда (режим `waste` из загрузчика сценария).
WASTE_TILE = "t_02_00"
#: Двор, который барон переселяет в новую деревню: он из `farm`, то есть
#: переезд идёт ВНУТРИ баронства, а не из воздуха.
MOVER = "hh_family_01"
MOVER_HOME = "t_03_01"

#: Три причины, по которым гейт места обязан отказать. Клетки взяты из разных
#: миров, потому что в `start_stand` нет ни воды, ни руины.
WATER_TILE = "t_90_05"
RUIN_TILE = "t_15_35"


def _prepared(seed: int = SEED, scenario: Path = STAND):
    """Стенд, у которого земля под деревню уже приведена в порядок.

    Три приказа `set_tile_regime` — ровно то, что обязан сделать игрок. Если
    тест начнёт готовить мир иначе, он перестанет проверять приказ и начнёт
    проверять свою посадку; порядок приказов записан в ADR 0222 и в отчёте.
    """
    world = load_scenario(scenario, seed=seed)
    for tile_id in (SITE, *WORKS):
        set_tile_regime(world, tile_id, "villein_tenement")
    return world


class TestOrdersAreRealButtons(unittest.TestCase):
    """Приказ существует в таблице, а не только в модуле."""

    def test_both_orders_are_registered(self) -> None:
        for action in ("found_village", "settle_household"):
            self.assertIn(
                action,
                ACTION_HANDLERS,
                f"Приказ '{action}' не заведён в ACTION_HANDLERS: кнопки нет",
            )

    def test_found_village_needs_its_lever(self) -> None:
        """Рычаг приказа — `works_tiles`; опечатка роняет прогон, а не игру.

        Проверяется не «ключ есть в таблице», а разница: с ключом порядок
        исполняется, без ключа падает ИМЕННО проверка формы записи, а не
        молчаливое поселение без угодья.
        """
        world = load_scenario(STAND, seed=SEED)
        for tile_id in (SITE, *WORKS):
            set_tile_regime(world, tile_id, "villein_tenement")
        from hillcourt.runner import _apply_script_entry

        entry = {
            "at_month": 1,
            "action": "found_village",
            "tile": SITE,
            "works_tiles": list(WORKS),
        }
        _apply_script_entry(world, entry)
        self.assertEqual(len(world.settlements), 5)
        with self.assertRaises(KeyError):
            _apply_script_entry(world, {"at_month": 2, "action": "found_village", "tile": "t_00_02"})


class TestSettlementsCount(unittest.TestCase):
    """Замер «до и после»: 4 поселения в целевом мире, 5 после приказа."""

    def test_four_before_five_after(self) -> None:
        before = load_scenario(STAND, seed=SEED)
        self.assertEqual(len(before.settlements), 4)
        after = _prepared()
        founded = found_village(after, SITE, list(WORKS), name="Новая слобода")
        self.assertEqual(len(after.settlements), 5)
        self.assertIn(founded.id, after.settlements)
        self.assertEqual(
            sorted(after.settlements),
            sorted([*before.settlements, founded.id]),
            "Прежние поселения тронуты: основание обязано ТОЛЬКО добавлять",
        )

    def test_founding_claims_site_and_works_tiles(self) -> None:
        world = _prepared()
        settlement = found_village(world, SITE, list(WORKS))
        self.assertEqual(world.tiles[SITE].settlement_id, settlement.id)
        self.assertEqual(settlement.coord, (0, 1), "coord — прямоугольные, как у загрузчика")
        self.assertEqual(
            settlement.works_tiles,
            list(WORKS),
            "угодье записано не в том виде или не в том порядке, как игрок назвал",
        )
        self.assertEqual(settlement.kind, "village")
        self.assertEqual(settlement.household_ids, [], "приказ основания не селит дворы")
        self.assertIn(settlement.stores_stock_id, world.stocks)

    def test_works_tiles_are_the_players_choice(self) -> None:
        """Два мира, один участок, разные угодья — разные поселения."""
        one = _prepared()
        set_tile_regime(one, "t_01_01", "villein_tenement")
        two = _prepared()
        set_tile_regime(two, "t_01_01", "villein_tenement")
        a = found_village(one, SITE, ["t_00_02"])
        b = found_village(two, SITE, ["t_01_01"])
        self.assertNotEqual(a.works_tiles, b.works_tiles)
        self.assertEqual(a.works_tiles, ["t_00_02"])
        self.assertEqual(b.works_tiles, ["t_01_01"])
        self.assertEqual(a.id, b.id, "Участок один — поселение должно быть то же")


class TestPrice(unittest.TestCase):
    """Основание и посадка стоят материи, и это видно в `Ledger` (И-1)."""

    def test_founding_costs_timber_from_the_barons_barn(self) -> None:
        world = _prepared()
        barn = manor_stock(world, root_manor(world))
        before = barn.amounts.get(FOUNDING_GOOD, 0.0)
        entries = len(world.ledger.entries)
        found_village(world, SITE, list(WORKS))
        self.assertAlmostEqual(
            barn.amounts.get(FOUNDING_GOOD, 0.0),
            before - FOUNDING_LOG,
            places=9,
            msg="Бревно за участок не списано",
        )
        charges = world.ledger.entries[entries:]
        self.assertEqual(len(charges), 1, "Основание должно быть ровно одной проводкой")
        charge = charges[0]
        self.assertEqual(charge.kind, "transfer")
        self.assertEqual(charge.reason, "village_founding")
        self.assertEqual(charge.good, FOUNDING_GOOD)
        self.assertAlmostEqual(charge.amount, FOUNDING_LOG, places=9)
        self.assertEqual(charge.src_id, barn.id)
        self.assertEqual(charge.dst_id, "sink:waste")
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_seating_costs_seed_grain_into_the_household(self) -> None:
        world = _prepared()
        found_village(world, SITE, list(WORKS))
        household = world.households[MOVER]
        barn = manor_stock(world, root_manor(world))
        expected = float(monthly_food_need(world, household)) * SEATING_MONTHS
        stock = world.get_stock(household.stock_id)
        before_grain = barn.amounts.get(SEATING_GOOD, 0.0)
        before_house = stock.amounts.get(SEATING_GOOD, 0.0)
        entries = len(world.ledger.entries)
        settle_household(world, MOVER, SITE)
        self.assertAlmostEqual(
            barn.amounts.get(SEATING_GOOD, 0.0), before_grain - expected, places=9
        )
        self.assertAlmostEqual(
            stock.amounts.get(SEATING_GOOD, 0.0), before_house + expected, places=9
        )
        charge = world.ledger.entries[entries]
        self.assertEqual(charge.reason, "village_seating")
        self.assertEqual(charge.dst_id, household.stock_id)
        self.assertAlmostEqual(charge.amount, expected, places=9)
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_founding_without_timber_is_refused_and_moves_nothing(self) -> None:
        world = _prepared()
        barn = manor_stock(world, root_manor(world))
        barn.amounts[FOUNDING_GOOD] = FOUNDING_LOG - 1.0
        matter = world.total_matter()
        with self.assertRaises(ValueError) as caught:
            found_village(world, SITE, list(WORKS))
        self.assertIn(FOUNDING_GOOD, str(caught.exception))
        self.assertEqual(len(world.settlements), 4, "Отказ основал поселение")
        self.assertIsNone(world.tiles[SITE].settlement_id)
        self.assertAlmostEqual(barn.amounts[FOUNDING_GOOD], FOUNDING_LOG - 1.0, places=9)
        self.assertAlmostEqual(world.total_matter(), matter, places=9)

    def test_seating_without_grain_is_refused_and_moves_nothing(self) -> None:
        world = _prepared()
        found_village(world, SITE, list(WORKS))
        barn = manor_stock(world, root_manor(world))
        barn.amounts[SEATING_GOOD] = 0.0
        household = world.households[MOVER]
        with self.assertRaises(ValueError):
            settle_household(world, MOVER, SITE)
        self.assertEqual(household.current_tile_id, MOVER_HOME, "Отказ переселил двор")
        self.assertEqual(household.settlement_id, "farm")

    def test_price_is_visible_in_the_order_log(self) -> None:
        """Рычаг приказа виден игроку в строке лога, а не только в амбаре."""
        world = _prepared()
        found_village(world, SITE, list(WORKS))
        settle_household(world, MOVER, SITE)
        found = [r for r in world.player_actions if r.get("action") == "found_village"]
        seated = [r for r in world.player_actions if r.get("action") == "settle_household"]
        self.assertEqual(len(found), 1, "Основание записано в лог не один раз")
        self.assertEqual(found[0]["price"], {FOUNDING_GOOD: FOUNDING_LOG})
        self.assertEqual(len(seated), 1, "Посадка записана в лог не один раз")
        self.assertEqual(seated[0]["settlement"], "village_00_01")
        self.assertEqual(seated[0]["moved_from"], "farm")
        self.assertIn(SEATING_GOOD, seated[0]["price"])


class TestWorksTilesGates(unittest.TestCase):
    """Угодье — рычаг игрока, но приказ проверяет и никогда не чинит."""

    def test_empty_works_tiles_is_refused(self) -> None:
        world = _prepared()
        with self.assertRaises(ValueError) as caught:
            found_village(world, SITE, [])
        self.assertIn("works_tiles", str(caught.exception))
        self.assertEqual(len(world.settlements), 4)

    def test_works_tile_of_another_settlement_is_refused(self) -> None:
        world = _prepared()
        with self.assertRaises(ValueError) as caught:
            found_village(world, SITE, [CLAIMED_BY_COURT])
        self.assertIn("hill_court", str(caught.exception))
        self.assertEqual(len(world.settlements), 4)

    def test_works_tile_on_waste_is_refused_naming_the_order_to_press(self) -> None:
        world = _prepared()
        with self.assertRaises(PermissionError) as caught:
            found_village(world, SITE, [WASTE_TILE])
        self.assertIn("set_tile_regime", str(caught.exception))
        self.assertEqual(len(world.settlements), 4)
        # Тот же приказ после смены режима проходит — отказ был о земле, а не
        # о кнопке.
        set_tile_regime(world, WASTE_TILE, "villein_tenement")
        settlement = found_village(world, SITE, [WASTE_TILE])
        self.assertEqual(settlement.works_tiles, [WASTE_TILE])

    def test_repeated_works_tile_is_refused(self) -> None:
        world = _prepared()
        with self.assertRaises(ValueError):
            found_village(world, SITE, ["t_00_02", "t_00_02"])

    def test_only_farmstead_and_village_may_be_founded(self) -> None:
        """`native_village` без племени сломало бы биекцию ADR 0064."""
        world = _prepared()
        for kind in KIND_REFUSAL:
            with self.assertRaises(ValueError) as caught:
                found_village(world, SITE, list(WORKS), kind=kind)
            self.assertIn(kind, str(caught.exception))
            self.assertEqual(len(world.settlements), 4)
        self.assertEqual(FOUNDABLE_KINDS, ("village", "farmstead"))
        farmstead = found_village(_prepared(), SITE, list(WORKS), kind="farmstead")
        self.assertEqual(farmstead.kind, "farmstead")


class TestPlaceGate(unittest.TestCase):
    """Приказ посадки зовёт `can_settle`: вода, руина, пустошь и полнота.

    Отказ проверяется не «по факту `False`», а **по причине в тексте**: игрок
    обязан знать, что именно его остановило (ADR 0202 — отказ это ход игры, а
    не авария и не молчание). Поэтому каждая проверка ищет в сообщении слово
    причины, и подмена `_refusal_detail` на общую отговорку роняет их все.
    """

    def _order_on(self, scenario: Path, tile_id: str, message_part: str) -> None:
        """Приказ посадки на клетку, уже помеченную поселением сценария.

        Метку ставит тест, а не приказ: нужно доказать, что гейт места держит
        даже там, где клетка УЖЕ в поселении (иначе проверялось бы только
        «поселения там нет», а не «на воду не садят»).
        """
        world = load_scenario(scenario, seed=SEED)
        settlement = next(
            s for s in sorted(world.settlements.values(), key=lambda s: s.id)
        )
        tile = world.tiles[tile_id]
        tile.settlement_id = settlement.id
        household = next(
            h for h in sorted(world.households.values(), key=lambda h: h.id)
            if h.left_at is None and h.settlement_id != settlement.id
        )
        before_tile = household.current_tile_id
        before_settlement = household.settlement_id
        with self.assertRaises(PermissionError) as caught:
            settle_household(world, household.id, tile_id)
        self.assertIn(message_part, str(caught.exception))
        self.assertEqual(
            household.current_tile_id, before_tile, "Отказ всё же переселил двор"
        )
        self.assertEqual(household.settlement_id, before_settlement)

    def test_water_is_refused_by_name(self) -> None:
        self._order_on(BARONY, WATER_TILE, "вод")

    def test_ruin_is_refused_by_name(self) -> None:
        self._order_on(BARONY, RUIN_TILE, "руина")

    def test_founding_on_water_is_refused_by_name(self) -> None:
        """Основание на воде — тот же отказ, тем же словом."""
        world = load_scenario(BARONY, seed=SEED)
        with self.assertRaises(PermissionError) as caught:
            found_village(world, WATER_TILE, ["t_41_53"])
        self.assertIn("вод", str(caught.exception))
        self.assertEqual(len(world.settlements), 4)

    def test_waste_regime_is_refused_by_name(self) -> None:
        """Пустошь не принимает двор, и отказ называет приказ, который нужен.

        Порядок гейтов проверяется здесь же: на клетке без поселения игрок
        сначала должен услышать про `set_tile_regime` (земля), а уже потом про
        `found_village` (поселение). Наоборот — приказ обрушил бы игрока в
        отказ не про ту причину.
        """
        world = _prepared()
        with self.assertRaises(PermissionError) as caught:
            settle_household(world, MOVER, WASTE_TILE)
        self.assertIn("set_tile_regime", str(caught.exception))
        self.assertEqual(world.households[MOVER].settlement_id, "farm")

    def test_land_without_a_settlement_is_refused_naming_the_order(self) -> None:
        """Годная земля без поселения — отказ про `found_village`, а не про землю."""
        world = _prepared()
        with self.assertRaises(PermissionError) as caught:
            settle_household(world, MOVER, WORKS[0])
        self.assertIn("found_village", str(caught.exception))
        self.assertEqual(world.households[MOVER].settlement_id, "farm")

    def test_full_tile_is_refused_by_name(self) -> None:
        world = _prepared()
        found_village(world, SITE, list(WORKS))
        cap = tile_max_households(world, SITE)
        outside = [
            h.id
            for h in sorted(world.households.values(), key=lambda h: h.id)
            if h.settlement_id != "village_00_01" and h.left_at is None
        ]
        self.assertGreater(
            len(outside), cap, "Не хватает дворов, чтобы набить гекс и проверить полноту"
        )
        for household_id in outside[:cap]:
            settle_household(world, household_id, SITE)
        self.assertEqual(household_count(world, SITE), cap)
        self.assertFalse(can_settle(world, SITE))
        with self.assertRaises(PermissionError) as caught:
            settle_household(world, outside[cap], SITE)
        self.assertIn("места нет", str(caught.exception))

    def test_refusal_agrees_with_can_settle_on_every_tile(self) -> None:
        """Отказ приказа и правило места — одна правда на КАЖДОЙ клетке.

        Если завтра `can_settle` починят, а приказ будет смотреть на свою
        копию условий, этот тест разойдётся на первой же клетке из десяти
        тысяч — то есть раньше, чем игрок сядет на руину.
        """
        checked = 0
        for scenario in (STAND, BARONY):
            world = load_scenario(scenario, seed=SEED)
            for tile_id in sorted(world.tiles):
                expected_none = can_settle(world, tile_id)
                actual = settlement_refusal(world, tile_id)
                self.assertEqual(
                    actual is None,
                    expected_none,
                    f"{scenario.name}/{tile_id}: can_settle={expected_none}, "
                    f"отказ приказа={actual!r}",
                )
                checked += 1
        self.assertGreater(
            checked, 10_000, "Проверено слишком мало клеток: баронство не поймано"
        )


class TestHouseholdMovesBetweenSettlements(unittest.TestCase):
    """Посадка — это переезд двора внутри баронства, а не появление из воздуха."""

    def test_rosters_and_residence_move(self) -> None:
        world = _prepared()
        found_village(world, SITE, list(WORKS))
        household = world.households[MOVER]
        settle_household(world, MOVER, SITE)
        self.assertEqual(household.settlement_id, "village_00_01")
        self.assertEqual(household.current_tile_id, SITE)
        self.assertIn(MOVER, world.settlements["village_00_01"].household_ids)
        self.assertNotIn(
            MOVER,
            world.settlements["farm"].household_ids,
            "Двор остался в прежнем поселении: это не переезд, а копия",
        )
        for person_id in household.member_ids:
            self.assertEqual(world.persons[person_id].location_tile_id, SITE)

    def test_founding_creates_no_person_and_no_matter_from_nowhere(self) -> None:
        world = _prepared()
        people = len(world.persons)
        households = len(world.households)
        found_village(world, SITE, list(WORKS))
        self.assertEqual(len(world.persons), people, "Основание выдумало людей")
        self.assertEqual(len(world.households), households)
        for entry in world.ledger.entries:
            self.assertEqual(
                entry.kind, "transfer", f"Внешний поток из приказа: {entry}"
            )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


class TestVillageLives(unittest.TestCase):
    """Замер живой деревни: дворы, оброк, хлеб и клетки в индексе роста."""

    def test_land_of_the_new_village_enters_the_growth_index(self) -> None:
        before = _prepared()
        before_index = worked_land_tile_ids(before)
        after = _prepared()
        found_village(after, SITE, list(WORKS))
        settle_household(after, MOVER, SITE)
        index = worked_land_tile_ids(after)
        for tile_id in (SITE, *WORKS):
            self.assertIn(
                tile_id, index, f"Клетка {tile_id} угодья не даёт роста: деревня мертва"
            )
        self.assertEqual(len(index) - len(before_index), len(WORKS) + 1)

    def test_household_works_the_commons_of_its_own_settlement(self) -> None:
        world = _prepared()
        found_village(world, SITE, list(WORKS))
        household = settle_household(world, MOVER, SITE)
        held = [tile.id for tile in own_tiles(world, household)]
        for tile_id in (SITE, *WORKS):
            self.assertIn(tile_id, held, f"Двор не пашет угодье {tile_id}")

    def test_village_pays_rent_feeds_itself_and_grows_grain(self) -> None:
        """24 месяца тика: клетки растут, двор ест, оброк барону платится."""
        world = _prepared()
        found_village(world, SITE, list(WORKS))
        household = settle_household(world, MOVER, SITE)
        grant_tenure(world, MOVER, WORKS[0], kind="tenure", rent_share=0.15)
        sown = {
            tile_id: world.get_stock(
                world.tiles[tile_id].standing_stock_id
            ).amounts.get("grain", 0.0)
            for tile_id in WORKS
        }
        for _ in range(24):
            run_month(world)
        grown = {
            tile_id: world.get_stock(
                world.tiles[tile_id].standing_stock_id
            ).amounts.get("grain", 0.0)
            for tile_id in WORKS
        }
        for tile_id in WORKS:
            self.assertGreater(
                grown[tile_id],
                sown[tile_id],
                f"Угодье {tile_id} за 24 месяца не выросло: деревня не живёт",
            )
        rent = sum(
            obligation.paid_total
            for obligation in world.obligations.values()
            if obligation.household_id == MOVER and obligation.kind == "rent"
        )
        self.assertGreater(rent, 0.0, "Новая деревня не платит оброк")
        self.assertEqual(
            household.hunger_days, 0, "Переселённый двор голодает в новой деревне"
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


class TestRunnerAndDeterminism(unittest.TestCase):
    """Приказ проходит через `runner`: отказ не роняет прогон (ADR 0202)."""

    ACTIONS = [
        {"at_month": 1, "action": "set_tile_regime", "tile": SITE, "regime": "villein_tenement"},
        {"at_month": 1, "action": "set_tile_regime", "tile": WORKS[0], "regime": "villein_tenement"},
        {"at_month": 1, "action": "set_tile_regime", "tile": WORKS[1], "regime": "villein_tenement"},
        {"at_month": 2, "action": "found_village", "tile": SITE, "works_tiles": list(WORKS), "name": "Новая слобода"},
        {"at_month": 2, "action": "settle_household", "household": MOVER, "tile": SITE},
        {"at_month": 3, "action": "grant_tenure", "household": MOVER, "tile": WORKS[0], "kind": "tenure", "rent_share": 0.15},
        # Бревна в амбаре корня ровно на одно основание: второй участок обязан
        # отказать, и прогон обязан пойти дальше (ADR 0202).
        {"at_month": 4, "action": "found_village", "tile": "t_02_01", "works_tiles": ["t_02_00"]},
    ]

    def test_run_with_the_orders_keeps_matter_and_refuses_the_second_village(self) -> None:
        result = run(STAND, 24, seed=SEED, actions=[dict(a) for a in self.ACTIONS])
        self.assertEqual(len(result.monthly), 24, "Отказ приказа оборвал прогон")
        self.assertAlmostEqual(result.matter_delta, 0.0, places=6)
        refused = [r for r in result.player_actions if r.get("refused")]
        self.assertEqual(len(refused), 1, "Второе основание обязано было отказать")
        self.assertEqual(refused[0]["action"], "found_village")
        self.assertIn(FOUNDING_GOOD, refused[0]["reason"])

    def test_one_seed_twice_one_state_hash(self) -> None:
        first = run(STAND, 12, seed=SEED, actions=[dict(a) for a in self.ACTIONS])
        second = run(STAND, 12, seed=SEED, actions=[dict(a) for a in self.ACTIONS])
        self.assertEqual(first.state_hash, second.state_hash, "И-6 нарушена")
        bare = run(STAND, 12, seed=SEED)
        self.assertNotEqual(
            first.state_hash,
            bare.state_hash,
            "Приказ основания не двинул мир: цена и переезд должны попасть в хеш",
        )

    def test_baseline_world_has_four_settlements(self) -> None:
        """Число «до» из отчёта агента: четыре поселения в целевом мире.

        Отдельно от `run`, потому что `RunResult.living_households` считает
        живых дворов и к 12-му месяцу их уже восемь (приход `hh_wave_01` на
        7-м месяце) — сравнивать его с числом дворов загрузки бессмысленно.
        """
        world = load_scenario(STAND, seed=SEED)
        self.assertEqual(len(world.settlements), 4)
        result = run(STAND, 12, seed=SEED, actions=[dict(a) for a in self.ACTIONS])
        self.assertEqual(len(result.monthly), 12)


if __name__ == "__main__":
    unittest.main()