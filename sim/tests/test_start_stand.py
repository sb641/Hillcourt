from __future__ import annotations

import unittest
from collections import defaultdict
from pathlib import Path

import yaml

from hillcourt.catalogs import LAND_ACTIONS
from hillcourt.economy.needs import monthly_food_need
from hillcourt.engine.roadworks import MATERIAL_AMOUNT, MATERIAL_GOOD, start_work
from hillcourt.engine.terrain import entry_cost
from hillcourt.engine.tile_view import tile_max_households
from hillcourt.engine.tick import run_month
from hillcourt.ontology import SimDate
from hillcourt.runner import _apply_script_entry, run
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "start_stand.yml"
#: Сценарий, где река объявлена ДАННЫМИ (`rivers:`), а не подделана тестом.
#: Нужен, чтобы отличать отказ «воды нет на доске» от отказа «нет материала».
RIVER_SCENARIO = ROOT / "design" / "scenarios" / "v0_shire.yml"
SEED = 1729
MONTHS = 24
COURT_TILE = "t_00_00"
DEMESNE_TILE = "t_01_00"
FOREST_TILE = "t_03_00"
PASTURE_TILE = "t_03_01"
HOLDING_BY_TENANT = {
    "hh_family_01": "t_02_00",
    "hh_family_02": "t_00_01",
    "hh_family_03": "t_01_01",
    "hh_family_04": "t_02_01",
    "hh_family_05": "t_00_02",
    "hh_wave_01": "t_01_02",
}
HOLDING_TILES = tuple(HOLDING_BY_TENANT.values())
INITIAL_FAMILIES = tuple(f"hh_family_{index:02d}" for index in range(1, 6))
WAVE = "hh_wave_01"
TENANTS = (*INITIAL_FAMILIES, WAVE)
VILLEIN = "villein"
COTTER = "cotter"
VILLEIN_HOLDING_SCALE = 1.0
COTTER_HOLDING_SCALE = 0.5
RURAL_TILE_CAP = 5
URBAN_TILE_CAP = 20
PLAYER = "hh_player"
COURT = "settlement:hill_court"


def _world():
    return load_scenario(SCENARIO, seed=SEED)


def _world_without_demesne_multiplier():
    """Тот же стенд, но с множителем выхода домена, сведённым к 1.0.

    Нужен для ДОКАЗАТЕЛЬСТВА, а не для реестра: «у домена свой множитель
    выхода» — это утверждение о том, что снятие множителя меняет урожай, и
    проверить его можно только сравнением двух миров. Никакое число при этом
    не зашивается, поэтому правка `grow_grain.amount` (ADR 0200) проверку не
    трогает.
    """
    world = load_scenario(SCENARIO, seed=SEED)
    world.catalogs.spawn_rules["grow_grain"].params["demesne_base_yield"] = 1.0
    return world


def _apply_script(world, month: int) -> None:
    for entry in world.script:
        if int(entry.get("at_month", 0)) == month:
            _apply_script_entry(world, entry)


def _run_scripted(world, months: int = MONTHS):
    for month in range(1, months + 1):
        _apply_script(world, month)
        run_month(world)
    return world


def _rights_for(world, household_id: str):
    return [
        right
        for right in world.rights.values()
        if right.holder_household_id == household_id
    ]


def _tenant_ids(world) -> tuple[str, ...]:
    return tuple(
        household_id
        for household_id in TENANTS
        if household_id in world.households
        and world.households[household_id].left_at is None
    )


def _harvest(world, dst_id: str, good: str) -> float:
    return sum(
        entry.amount
        for entry in world.ledger.entries
        if entry.reason == "harvest_grain"
        and entry.kind == "process"
        and entry.dst_id == dst_id
        and entry.good == good
    )


def _harvest_from_tiles(world) -> dict[str, dict[int, float]]:
    """Сколько зерна СНЯЛИ с клетки по месяцам года: {tile_id: {месяц: зерно}}.

    Именно снятие, а не выдача: урожай, который остался стоять на клетке, хлеба
    не дал, и месяц жатвы виден только по проводкам `tile:* → sink:processing`.
    """
    out: dict[str, dict[int, float]] = {
        tile.id: dict.fromkeys(range(1, 13), 0.0) for tile in world.tiles.values()
    }
    # Пересчёт по календарю: проводки хранят дату, а нужен месяц года.
    for entry in world.ledger.entries:
        if (
            entry.reason != "harvest_grain"
            or entry.kind != "transfer"
            or entry.good != "grain"
            or not entry.src_id
            or not entry.src_id.startswith("tile:")
        ):
            continue
        tile_id = entry.src_id[len("tile:") :]
        if tile_id in out:
            out[tile_id][entry.date.month] += entry.amount
    return out


def _sum_entries(entries, *, reason: str, good: str, dst_id: str | None = None):
    return sum(
        entry.amount
        for entry in entries
        if entry.reason == reason
        and entry.good == good
        and (dst_id is None or entry.dst_id == dst_id)
    )


def _corvee_days(world) -> float:
    return sum(
        obligation.corvee_days
        for obligation in world.obligations.values()
        if obligation.kind == "labor_duty"
    )


def _obligations_of(world, kind: str):
    """Все повинности одного вида, по порядку id (для сообщений об ошибке)."""
    return [
        world.obligations[obligation_id]
        for obligation_id in sorted(world.obligations)
        if world.obligations[obligation_id].kind == kind
    ]


def _harvest_to(world, dst_id: str) -> dict[str, float]:
    """Сколько зерна и соломы ушло при жатве в ОДИН амбар: {good: amount}.

    Именно `process`-проводки жатвы: они отвечают за «кто куда собрал», тогда
    как `transfer` с клетки отвечает за «с какой клетки сняли». Смешивать их
    нельзя — это две стороны одного события, и сумма по ним не равна урожаю.
    """
    out: dict[str, float] = defaultdict(float)
    for entry in world.ledger.entries:
        if entry.reason == "harvest_grain" and entry.kind == "process":
            if entry.dst_id == dst_id:
                out[entry.good] += entry.amount
    return dict(out)


def _rent_share_by_household(world) -> dict[str, float]:
    """Доля урожая, записанная правом держания: {household_id: rent_share}."""
    return {
        right.holder_household_id: right.rent_share
        for right in world.rights.values()
        if right.rent_share > 0.0
    }


class TestStartStand(unittest.TestCase):
    def test_granted_holding_grows_and_its_crop_comes_in_harvest_month(self) -> None:
        """ЗАКОН: выданный надел РАСТЁТ, и его урожай приходит в месяц пика.

        Основание 2 индекса роста (`engine/growth.py::worked_land_tile_ids`),
        проверенное там, где поломка и была замерена. Индекс раньше строился из
        координат поселений и `works_tiles`, и на стенде давал
        `('t_00_00', 't_01_00', 't_03_00', 't_03_01')` — из семи пашен в нём была
        одна, домен барона. Надел `t_02_00` съедал свои 8.0 стартового зерна за
        три зимних месяца при `plot_yield` 0.15 и с четвёртого месяца был пуст
        на 116 месяцев подряд, тогда как домен копил 788.9 зерна, которое никто
        не убирал. Приказ `grant_tenure`, который игрок жмёт на первом ходу,
        не давал ровно ничего.

        Три части, и каждая падает по-своему:

        1. **Надел в индексе роста** — иначе природа его не касается.
        2. **Клетка не умирает**: стоячее зерно на 12-м месяце больше нуля
           (было ровно 0.0 с четвёртого месяца).
        3. **Сезонность работает в правильную сторону**: снято с клетки за год
           БОЛЬШЕ в девятом месяце (`plot_yield` 1.6, пик `grow_grain`), чем в
           первом (0.15). До правки надел кормился только зимой, а сентября не
           видел вовсе — «деревня пашет зимой при 0.15 и не пашет в сентябре
           при 1.6».
        """
        world = _run_scripted(_world(), 12)
        for holding_tile in HOLDING_TILES:
            self.assertIn(
                holding_tile,
                world.growth_tile_ids,
                f"Надел {holding_tile} вне индекса роста: приказ игрока ничего не даёт",
            )
        standing = {
            tile_id: world.get_stock(
                world.tiles[tile_id].standing_stock_id
            ).amounts.get("grain", 0.0)
            for tile_id in HOLDING_TILES
        }
        for tile_id, amount in sorted(standing.items()):
            self.assertGreater(
                amount, 0.0,
                f"Клетка {tile_id} вымерла: стоячее зерно {amount} на 12-м месяце",
            )
        taken = _harvest_from_tiles(world)
        for tile_id in HOLDING_TILES:
            with self.subTest(tile=tile_id):
                self.assertGreater(
                    taken[tile_id][9], taken[tile_id][1],
                    f"{tile_id}: сентябрь (пик 1.6) дал меньше января (0.15) — "
                    f"сезонность работает наоборот: {taken[tile_id][9]:.1f} против "
                    f"{taken[tile_id][1]:.1f}",
                )

    def test_demesne_growth_is_untouched_by_the_index_fix(self) -> None:
        """Правка индекса не должна сдвинуть домен: у него свой множитель.

        Индекс решает только одно: **обойти ли клетку**. Сумма прироста на
        обойденной клетке зависит от её рельефа, режима и правила, а не от
        индекса, поэтому клетка домена, бывшая в индексе и до правки, даёт тот же
        прирост. Замер снят с двух сторон, 120 месяцев, сид 1729, сумма зерна
        `harvest_grain`, снятого с `t_01_00`: **было 7937.5, стало 7937.3** —
        разница в десятых тысячной, то есть печать округления. Наделы при этом
        выросли с 8.0 до 1670.7 (`t_02_00`).

        Проверяется не реестром (он уедет вместе с каталогами экономиста), а
        законом: у домена свой множитель `demesne_base_yield` (ADR 0137 п. 2),
        поэтому его жатва и месяц жатвы читаются свои.
        """
        world = _run_scripted(_world(), 12)
        self.assertIn(DEMESNE_TILE, world.growth_tile_ids)
        self.assertTrue(
            world.catalogs.spawn_rules["grow_grain"].params["demesne_base_yield"] > 1.0,
            "Домен потерял свой множитель выхода — правка индекса не при чём",
        )
        taken = _harvest_from_tiles(world)
        demesne = taken[DEMESNE_TILE]
        self.assertGreater(
            demesne[9], demesne[1],
            f"Домен жнёт не в пик: сентябрь {demesne[9]:.1f} против января "
            f"{demesne[1]:.1f}",
        )
        for holding_tile in HOLDING_TILES:
            with self.subTest(tile=holding_tile):
                self.assertGreater(
                    sum(demesne.values()), sum(taken[holding_tile].values()),
                    f"Домен ({sum(demesne.values()):.1f}) собрал не больше надела "
                    f"{holding_tile} ({sum(taken[holding_tile].values()):.1f}): "
                    f"множитель домена не действует",
                )

    def test_water_orders_fail_here_by_composition_not_by_code(self) -> None:
        """На стенде нет воды — и это состав доски, а не поломка приказа.

        `work_ford`, `work_bridge` и `send_river` на стенде отклоняются, и
        владелец спросил, чинить ли это. Ответ — по двум замерам, и оба здесь.

        1. **Стенда нет и быть не может «просто так».** Водных гексов ноль, а они
           появляются только из секции `rivers:` (`scenario.py::load_river_network`
           переводит `flow_order` в `terrain: water`), и в `start_stand.yml` такой
           секции нет. Отказ «не на воду: террейн field» — законная причина по
           составу доски.
        2. **На сценарии с рекой те же приказы доходят до материала**, то есть
           рельеф проходит: значит отказ на стенде — не «приказ сломан», а
           «воды нет». А вот материал — это уже дыра экономики, см. ADR 0195.

        Проверка вторая намеренно требует ИМЕННО отказа про `log`: если бы кто-то
        «починил» стенд, добавив реку, но не починив амбар лорда, тест бы это
        показал — водная клетка появилась бы, а приказ всё равно не прошёл бы.
        """
        world = _run_scripted(_world(), 1)
        self.assertEqual(
            [tile.id for tile in world.tiles.values() if tile.terrain == "water"], [],
            "На стенде появилась вода: ADR 0195 и это утверждение устарели",
        )
        with self.assertRaises(ValueError) as caught:
            start_work(world, HOLDING_TILES[0], "ford")
        self.assertIn("не на воду", str(caught.exception))

        river_world = load_scenario(RIVER_SCENARIO, seed=SEED)
        water = next(
            tile for tile in river_world.tiles.values() if tile.terrain == "water"
        )
        with self.assertRaises(ValueError) as caught:
            start_work(river_world, water.id, "ford")
        self.assertIn(
            MATERIAL_GOOD, str(caught.exception),
            "На сценарии с рекой приказ отклонён не материалом, а рельефом — "
            "классификация приказа в ADR 0195 неверна",
        )

    def test_lord_can_build_a_road_from_his_own_barn(self) -> None:
        """ЗАКОН: лорд строит дорогу из своего амбара, и приказ это стоит.

        Приказ `work_road` был мёртв на стенде — «в амбаре корня нет `log`: есть
        0.0, надо 3.0», — и мёртв он был **не из-за состава стенда**. Замерено по
        всем семи сценариям: `log` в амбаре корневого манора равен 0.0 всюду;
        `work_ford`/`work_bridge` падают с тем же сообщением на `v0_barony_100` и
        `v0_shire`, где река есть. То есть отсутствие воды на стенде — законная
        причина отказа для водных приказов, а отсутствие брёвен — нет.

        Четыре части, и каждая падает по-своему:

        1. **Приказ принимается** и списывает `MATERIAL_AMOUNT['road']` переводом
           (материя не создаётся, а исчезает в `sink:waste`).
        2. **Клетка остаётся старой до завершения проекта** (ADR 0172): tag `road`
           и цена входа не меняются в момент приказа.
        3. **Проект доезжает руками и поднимает tag** — цена входа падает.
        4. **Второй приказ проходит, третий отклоняется по материалу**: амбар
           на 6.0 = две дороги по 3.0 (ADR 0039), и стенд показывает цену.
        """
        world = _run_scripted(_world(), 1)
        road_tile = HOLDING_TILES[0]
        barn = world.get_stock(COURT)
        self.assertEqual(
            barn.amounts.get("log", 0.0), 2.0 * MATERIAL_AMOUNT["road"],
            "В амбаре лорда не две дороги — стенд не показывает цену приказа",
        )
        cost_before = entry_cost(
            "caravan", world.tiles[road_tile].terrain, False, False, False
        )
        start_work(world, road_tile, "road")
        self.assertAlmostEqual(
            barn.amounts.get("log", 0.0), MATERIAL_AMOUNT["road"], places=6,
            msg="Материал списан не переводом",
        )
        self.assertFalse(world.tiles[road_tile].road)
        self.assertAlmostEqual(
            entry_cost("caravan", world.tiles[road_tile].terrain, False, False, False),
            cost_before, places=6,
            msg="Клетка поехала до завершения проекта (ADR 0172)",
        )
        run_month(world)
        self.assertTrue(world.tiles[road_tile].road, "Дорога не достроилась руками")
        self.assertLess(
            entry_cost(
                "caravan", world.tiles[road_tile].terrain, world.tiles[road_tile].road,
                False, False,
            ),
            cost_before,
            msg="Готовая дорога не дешевит ход обоза",
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)
        start_work(world, HOLDING_TILES[1], "road")
        with self.assertRaises(ValueError):
            start_work(world, HOLDING_TILES[2], "road")

    def test_holding_is_a_preset_share_and_the_field_is_baron_only(self) -> None:
        with SCENARIO.open("r", encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
        self.assertTrue(
            all("holding_tiles" not in entry for entry in data["households"])
        )
        world = _run_scripted(_world(), 1)
        demesne = world.tiles[DEMESNE_TILE]
        self.assertEqual(demesne.terrain, "field")
        self.assertEqual(demesne.regime_id, "demesne")
        self.assertEqual(world.tiles[COURT_TILE].terrain, "hill")
        self.assertEqual(world.tiles[FOREST_TILE].terrain, "forest")
        self.assertEqual(world.tiles[PASTURE_TILE].terrain, "pasture")
        self.assertEqual(
            world.manors["manor_hill"].tile_regimes[DEMESNE_TILE], "demesne"
        )
        self.assertFalse(
            [
                right
                for right in world.rights.values()
                if right.tile_id == DEMESNE_TILE
            ]
        )
        for household_id in INITIAL_FAMILIES:
            household = world.households[household_id]
            rights = _rights_for(world, household_id)
            self.assertEqual(len(rights), 1)
            self.assertEqual(rights[0].kind, "grazing")
            self.assertEqual(rights[0].tile_id, HOLDING_BY_TENANT[household_id])
            regime = world.catalogs.land_regimes[
                world.tiles[rights[0].tile_id].regime_id
            ]
            self.assertTrue(regime.feeds_household)
            self.assertIn("plough", regime.allowed_actions)
            self.assertFalse(regime.requires_labor_days)

    def test_holding_scale_is_virgate_or_kloch_from_the_preset(self) -> None:
        world = _run_scripted(_world(), 6)
        for household_id in TENANTS:
            if household_id not in world.households:
                continue
            self.assertIn(
                world.households[household_id].legal_status_id, {VILLEIN, COTTER}
            )
        for household_id, expected in (
            ("hh_family_01", VILLEIN_HOLDING_SCALE),
            ("hh_family_02", VILLEIN_HOLDING_SCALE),
            ("hh_family_04", VILLEIN_HOLDING_SCALE),
            ("hh_family_03", COTTER_HOLDING_SCALE),
            ("hh_family_05", COTTER_HOLDING_SCALE),
        ):
            self.assertEqual(world.households[household_id].holding_scale, expected)
        for household_id in INITIAL_FAMILIES:
            holding_scale = world.households[household_id].holding_scale
            self.assertIn(holding_scale, (VILLEIN_HOLDING_SCALE, COTTER_HOLDING_SCALE))
            self.assertNotAlmostEqual(holding_scale, 1.0 / len(HOLDING_TILES))
        for household_id in INITIAL_FAMILIES:
            self.assertEqual(
                world.households[household_id].holding_scale,
                VILLEIN_HOLDING_SCALE
                if world.households[household_id].legal_status_id == VILLEIN
                else COTTER_HOLDING_SCALE,
            )

    def test_tile_capacity_is_five_farmers_and_twenty_urban(self) -> None:
        world = _run_scripted(_world(), 1)
        for tile_id in (COURT_TILE, DEMESNE_TILE, FOREST_TILE, PASTURE_TILE, *HOLDING_TILES):
            self.assertEqual(tile_max_households(world, tile_id), RURAL_TILE_CAP)
        tile = world.tiles[HOLDING_TILES[0]]
        tile.urban = True
        self.assertEqual(tile_max_households(world, tile.id), URBAN_TILE_CAP)
        tile.urban = False
        self.assertEqual(tile_max_households(world, tile.id), RURAL_TILE_CAP)

    def test_baron_field_rent_and_unpaid_corvee_are_separate(self) -> None:
        """ЗАКОН, часть третья: оброк и невыполненная барщина — РАЗНЫЕ числа.

        Тест был реестром: 1152.9703871 зерна, 230.59407741935482 соломы,
        5924.0 дней барщины, 74.29 оброка. Первые два числа были прибиты к
        `spawn_rules.yml::grow_grain.amount = 40.0` — значению, которое ADR 0200
        вернул в 50.0 по закону `ADR 0141:45-46`, и потому реестр ловил не
        закон, а состояние каталога. Ниже осталось только то, что верно при
        любой пересборке мира; ни одного числа урожая здесь больше нет.

        **1. Разные валюты, и ни одна не переходит в другую.** Оброк — зерно
        (`Obligation.due_good == "grain"`, `basis == "share"`), барщина — дни
        (`due_good is None`, `basis == "duty"`). Долг барщины не может иметь
        задолженности в зерне: `arrears == 0.0` у `labor_duty` — это не «долга
        нет», а «не в той валюте». Мутация «погасить барщину зерном» (поставить
        `due_good="grain"` и `paid_total > 0`) роняет пункт.

        **2. Барщина не выплачена и не отработана, и это видно по полям, а не по
        сумме.** `paid_total == 0.0` у каждой `labor_duty`, и
        `duty_days < corvee_days`: за два года ни один двор не отработал
        повинность в ноль. Реестр при этом пуст (`corvee_payments == []`) — то
        есть непогашенная барщина не подменена подачей. Мутация «отработать
        барщину» роняет пункт, и это правильный красный: смена закона, а не
        сдвиг окна.

        **3. Оброк — доля урожая ПРОШЛОГО месяца, а не календарь и не архив**
        (ADR 0183 п. 1, ADR 0192 п. 1, ADR 0206 закон 2). Собранный за месяц
        оброк ни разу не превышает `rent_share` × урожай прошлого месяца, и
        хотя бы один месяц закрыт в ноль (двор заплатил полностью). Мутация
        «база оброка = весь урожай двора за всю жизнь» — ровно тот баг, который
        чинил ADR 0192 — роняет пункт с первого месяца, в который есть урожай.

        **4. Повинность, реестр и счётчик говорят одно.** `paid_total` повинностей
        `rent`, сумма проводок `rent` в амбар барона и `stats["rent_collected"]`
        обязаны совпадать. Расхождение означает, что одно из трёх считает оброк
        мимо остальных двух, и никакое число их не спасёт.
        """
        world = _run_scripted(_world())
        harvest = _harvest_to(world, COURT)
        self.assertTrue(harvest, "Зал ничего не собрал: урожая нет вовсе")
        self.assertGreater(harvest.get("grain", 0.0), 0.0)
        self.assertGreater(harvest.get("straw", 0.0), 0.0)

        rent_obligations = _obligations_of(world, "rent")
        duties = _obligations_of(world, "labor_duty")
        self.assertTrue(rent_obligations, "Оброк не заведён ни на один надел")
        self.assertTrue(duties, "Барщина не заведена ни на один двор")

        # 1. Разные валюты.
        for obligation in rent_obligations:
            with self.subTest(obligation=obligation.id):
                self.assertEqual(obligation.due_good, "grain")
                self.assertEqual(obligation.basis, "share")
        for obligation in duties:
            with self.subTest(obligation=obligation.id):
                self.assertIsNone(
                    obligation.due_good,
                    f"{obligation.id}: у барщины появился товарный долг — "
                    f"валюты смешались",
                )
                self.assertEqual(obligation.basis, "duty")
                self.assertEqual(
                    obligation.arrears, 0.0,
                    f"{obligation.id}: задолженность в зерне у дней барщины",
                )

        # 2. Барщина не выплачена и не отработана.
        corvee_payments = [
            entry
            for entry in world.ledger.entries
            if entry.kind == "transfer"
            and entry.reason.startswith("corvee")
            and entry.dst_id
            and entry.dst_id.startswith("household:")
        ]
        self.assertEqual(corvee_payments, [])
        for obligation in duties:
            with self.subTest(obligation=obligation.id):
                self.assertEqual(
                    obligation.paid_total, 0.0,
                    f"{obligation.id}: барщина погашена товаром",
                )
                self.assertLess(
                    obligation.duty_days, obligation.corvee_days,
                    f"{obligation.id}: двор отработал повинность в ноль — тест "
                    f"называет барщину невыполненной, и отменять это надо "
                    f"решением, а не сдвигом числа",
                )
        self.assertGreater(_corvee_days(world), 0.0)

        # Ни одна валюта не переходит в другую: дни и зерно не сравнимы.
        self.assertNotAlmostEqual(
            _corvee_days(world),
            _sum_entries(
                world.ledger.entries, reason="rent", good="grain", dst_id=COURT,
            ),
            places=6,
            msg="Дни барщины и зерно оброка сошлись в одно число — валюты слиплись",
        )
        tenant_board = [
            entry
            for entry in world.ledger.entries
            if entry.reason == "board"
            and entry.dst_id
            and entry.dst_id.startswith("household:hh_family")
        ]
        self.assertEqual(tenant_board, [])

        # 3. Оброк — доля урожая прошлого месяца.
        shares = _rent_share_by_household(world)
        harvested: dict[tuple[int, int, str], float] = defaultdict(float)
        collected: dict[tuple[int, int, str], float] = defaultdict(float)
        for entry in world.ledger.entries:
            if (
                entry.reason == "harvest_grain"
                and entry.kind == "process"
                and entry.good == "grain"
                and entry.dst_id
                and entry.dst_id.startswith("household:")
            ):
                harvested[
                    (entry.date.year, entry.date.month, entry.dst_id[len("household:") :])
                ] += entry.amount
            if entry.reason == "rent" and entry.dst_id == COURT and entry.src_id:
                collected[
                    (
                        entry.date.year,
                        entry.date.month,
                        entry.src_id[len("household:") :],
                    )
                ] += entry.amount
        self.assertTrue(collected, "Оброк не собран ни разу")
        tight_months = 0
        for (year, month, household_id), amount in sorted(collected.items()):
            if (year, month) == (1, 1):
                # Первый месяц: урожая ещё нет, база оброка — стартовый амбар.
                continue
            previous_month = month - 1 if month > 1 else 12
            base = harvested.get((year, previous_month, household_id), 0.0)
            share = shares.get(household_id, 0.0)
            with self.subTest(year=year, month=month, household=household_id):
                self.assertLessEqual(
                    amount,
                    share * base + 1e-9,
                    f"оброк {amount:.6f} больше доли {share} от урожая прошлого "
                    f"месяца ({base:.6f}): база оброка не помесячный урожай "
                    f"(ADR 0192 п. 1)",
                )
            if abs(amount - share * base) <= 1e-9:
                tight_months += 1
        self.assertGreater(
            tight_months, 0,
            "Ни один месяц не закрыт в ноль: оброк не связан с урожаем вовсе",
        )

        # 4. Повинность, реестр и счётчик говорят одно.
        rent_paid = sum(obligation.paid_total for obligation in rent_obligations)
        rent_ledger = _sum_entries(
            world.ledger.entries, reason="rent", good="grain", dst_id=COURT
        )
        self.assertAlmostEqual(rent_paid, rent_ledger, places=6)
        self.assertAlmostEqual(
            world.stats.get("rent_collected", 0.0), rent_ledger, places=6,
            msg="Счётчик оброка разошёлся с реестром",
        )
        self.assertGreater(rent_ledger, 0.0)
        self.assertGreater(
            sum(obligation.arrears for obligation in rent_obligations), 0.0,
            "Недоимки нет ни у одного двора: база оброка облагает архив, а не "
            "прошлый месяц (ADR 0192 п. 1)",
        )
        self.assertLess(rent_ledger, sum(harvested.values()))
        self.assertGreater(world.get_stock(COURT).amounts.get("grain", 0.0), 0.0)
        self.assertAlmostEqual(
            world.get_stock(COURT).amounts["straw"], harvest["straw"], places=6
        )

    def test_personal_holding_feeds_the_household(self) -> None:
        """ЗАКОН, часть первая: надел кормит двор, который на нём живёт.

        Отделено от счёта домена (`test_demesne_feeds_the_court_only`), чтобы
        падение одного реестра не маскировало живой закон надела: раньше оба
        утверждения жили в одном тесте, и красный домен гасил зелёный надел.
        """
        world = _run_scripted(_world())
        for household_id in _tenant_ids(world):
            self.assertGreater(
                _harvest(world, f"household:{household_id}", "grain"),
                0.0,
                f"{household_id} не собирает урожай с личного надела",
            )
        for holding_tile in HOLDING_TILES:
            self.assertGreaterEqual(
                world.tiles[holding_tile].regime_id in {"villein_tenement", "cotter_plot"},
                True,
            )

    def test_demesne_feeds_the_court_only(self) -> None:
        """ЗАКОН, часть вторая: домен лорда кормит усадьбу, и только её.

        Тест держал число 1152.9703871 зерна — реестр, прибитый к
        `grow_grain.amount = 40.0`, который ADR 0200 вернул в 50.0 по закону
        `ADR 0141:45-46`. Число заменено на четыре утверждения, каждое из
        которых проверяет закон и не сдвинется ни при какой пересборке мира.

        **1. Домен — источник.** С клетки домена снято больше пашни, чем с любой
        прочей клетки стенда. Мутация «выкинуть `t_01_00` из индекса роста»
        (ровно тот баг, что чинил `engine/growth.py::index_growth_tiles`) роняет
        пункт.

        **2. У домена ЕСТЬ свой множитель выхода, и это доказано сравнением, а не
        числом.** Тот же мир, тот же сид, но `demesne_base_yield`, сведённый к
        1.0, обязан дать урожай зала меньше. Мутация «снять множитель домена»
        роняет пункт, и при этом ни одна правка `grow_grain.amount` его не
        погасит: оба числа растут от множителя, но сравниваются между собой.
        Дополнительно — зал кормится сильнее любого отдельного двора, потому что
        домен не надел, а пашня усадьбы со своей нормой (ADR 0141, ADR 0199 §4).

        **3. Домен кормит ТОЛЬКО зал.** Ни одно поселение, кроме усадьбы, не
        получает ни зерна, ни соломы жатвы: на стенде есть ещё три поселения
        (`farm`, `ash_village`, `salt_village`), и ни одно из них не кормится с
        пашни лорда. Мутация «отдать часть доменного урожая в `settlement:farm`»
        роняет пункт.

        **4. Счётчик и реестр говорят одно.** `stats["demesne_grain"]` обязан
        совпасть с суммой жатвы в амбар барона: расхождение означает, что
        счётчик и проводки считают разные вещи, и никакое число их не спасёт.

        **5. Домен — это ровно пашня усадьбы И лес под расчистку** (ADR 0182
        п. 5), и никаких прочих клеток. Прежний закон «демесн ровно один» был
        верен до ADR 0182: расчистка леса выдаётся **только доменом**, иначе
        право `clear_forest` неоткуда взять и рецепт расчистки становится
        призраком.
        """
        world = _run_scripted(_world())
        harvest = _harvest_to(world, COURT)
        self.assertTrue(harvest, "Зал ничего не собрал: урожая нет вовсе")
        self.assertGreater(harvest.get("grain", 0.0), 0.0)
        self.assertGreater(harvest.get("straw", 0.0), 0.0)

        # 1. Домен — источник.
        taken = _harvest_from_tiles(world)
        by_year = {
            tile_id: sum(months.values()) for tile_id, months in taken.items()
        }
        self.assertEqual(
            max(by_year, key=lambda tile_id: (by_year[tile_id], tile_id)),
            DEMESNE_TILE,
            f"Больше всего зерна снято не с домена: {by_year}",
        )
        self.assertIn(DEMESNE_TILE, world.growth_tile_ids)
        for other_tile, amount in sorted(by_year.items()):
            if other_tile == DEMESNE_TILE:
                continue
            with self.subTest(tile=other_tile):
                self.assertLess(
                    amount, by_year[DEMESNE_TILE],
                    f"{other_tile} ({amount:.1f}) собрал не меньше домена "
                    f"({by_year[DEMESNE_TILE]:.1f}): множитель домена не действует",
                )

        # 2. Зал кормится сильнее любого отдельного двора, и у домена ЕСТЬ свой
        # множитель выхода. Множитель доказывается сравнением, а не числом:
        # тот же мир с `demesne_base_yield`, сведённым к 1.0, обязан дать
        # урожай зала МЕНЬШЕ. Мутация «снять множитель домена» роняет пункт,
        # и никакая правка `grow_grain.amount` его не погасит — оба числа
        # меняются от множителя, но сравниваются между собой.
        without_multiplier = _run_scripted(
            _world_without_demesne_multiplier(), MONTHS
        )
        self.assertGreater(
            harvest["grain"],
            _harvest_to(without_multiplier, COURT)["grain"],
            "У домена нет своего множителя выхода: снятие `demesne_base_yield` "
            "ничего не изменило (ADR 0141, ADR 0199 §4)",
        )
        tenants = {
            household_id: amount
            for household_id, amount in (
                (household_id, _harvest(world, f"household:{household_id}", "grain"))
                for household_id in _tenant_ids(world)
            )
            if amount > 0.0
        }
        self.assertTrue(tenants, "Ни один двор не собрал урожай с надела")
        for household_id, amount in sorted(tenants.items()):
            with self.subTest(household=household_id):
                self.assertLess(
                    amount, harvest["grain"],
                    f"{household_id} ({amount:.1f}) собрал не меньше усадьбы "
                    f"({harvest['grain']:.1f}): у домену свой множитель выхода",
                )

        # 3. Домен кормит только зал.
        fed_settlements = {
            entry.dst_id
            for entry in world.ledger.entries
            if entry.reason == "harvest_grain"
            and entry.kind == "process"
            and entry.dst_id
            and entry.dst_id.startswith("settlement:")
        }
        self.assertEqual(
            fed_settlements, {COURT},
            f"Урожай с пашни попал не только в усадьбу: {sorted(fed_settlements)}",
        )
        self.assertEqual(
            sorted(world.settlements), sorted({"ash_village", "farm", "hill_court", "salt_village"}),
            "На стенде появилось или пропало поселение — список поселений "
            "меняет и список кормящихся",
        )

        # 4. Счётчик и реестр говорят одно.
        self.assertAlmostEqual(
            world.stats.get("demesne_grain", 0.0), harvest["grain"], places=6,
            msg="Счётчик доменного зерна разошёлся с реестром",
        )

        # 5. Домен — пашня усадьбы и лес под расчистку, и ничего кроме.
        demesne_tiles = {
            tile_id
            for tile_id, tile in world.tiles.items()
            if tile.regime_id == "demesne"
        }
        self.assertEqual(demesne_tiles, {DEMESNE_TILE, FOREST_TILE})
        self.assertIn(
            "clear_forest", world.catalogs.land_regimes["demesne"].allowed_actions,
            "Домен без права расчистки: рецепт uproot_stumps остался бы призраком",
        )

    def test_corvee_days_do_not_depend_on_baron_payment_stock(self) -> None:
        baseline = _run_scripted(_world(), 6)
        empty = _world()
        empty.get_stock(COURT).amounts["grain"] = 0.0
        empty.get_stock(COURT).amounts["silver"] = 0.0
        _run_scripted(empty, 6)
        self.assertEqual(_corvee_days(baseline), _corvee_days(empty))
        self.assertGreater(_corvee_days(baseline), 0.0)

    def test_livestock_income_is_measured_over_two_years(self) -> None:
        world = _run_scripted(_world())
        products = {"milk": 0.0, "wool": 0.0, "meat": 0.0}
        reasons = {
            "milk_goat",
            "milk_sheep",
            "shear_sheep",
            "butcher_hen",
            "butcher_duck",
            "butcher_goose",
        }
        for household_id in TENANTS:
            stock_id = world.households[household_id].stock_id
            for good in products:
                products[good] += sum(
                    entry.amount
                    for entry in world.ledger.entries
                    if entry.dst_id == stock_id
                    and entry.good == good
                    and entry.reason in reasons
                )
        # ЗАКОН, а не реестр: за два года не должно быть ни одной проводки
        # `starved` и поголовье должно уцелеть. Именно этот закон ловит побег
        # дойки (`_dairy_batch_cap` брал потолок пашни VIRGATE_BATCHES = 64):
        # двор с одной козой и одной овцой доил 128 партий в месяц, дойка съедала
        # 512.0 сена из 960, норма стада переставала закрываться, и поголовье
        # вымирало — 18 проводок `starved`, на финише 0 голов из 20.
        starved = [e for e in world.ledger.entries if e.reason == "starved"]
        self.assertEqual(
            [(e.good, e.src_id) for e in starved], [],
            "Скот умер от голода: сена не хватает",
        )
        heads = {
            good: sum(
                world.get_stock(world.households[hid].stock_id).amounts.get(good, 0.0)
                for hid in TENANTS
            )
            for good in ("hen", "duck", "goat", "sheep")
        }
        self.assertGreater(
            sum(heads.values()), 0.0, f"Поголовье вымерло за два года: {heads}",
        )
        # РЕЕСТР, по трём причинам, и потому поимённо (замер обоими индексами,
        # 24 мес, сид 1729, семь дворов TENANTS):
        #
        # 1. `meat` 7.2 — НЕ трогали: было 7.199999999999998 и осталось 7.2.
        #    Сдвига нет, и переписывать «новое» число здесь было бы враньём.
        # 2. `milk` 192.0 → 12.8 → 17.6. Первое движение (192.0 → 12.8) было у
        #    предыдущего агента: дворы перестали все время просить подачу, и
        #    свободные руки ушли из дойки в пашню. Второе (12.8 → 17.6) — моё, и
        #    причина измерена, а не угадана: это ОКНО оброка. Оброк с архива
        #    брал 10 % всего выращенного за всю жизнь двора, то есть забирал
        #    зерно, которое двору нужно было на зиму, и САМ ГОЛОДАЛ ДВОР:
        #    `relief_records` 43 → 33 и `relief_given` 78.769 → 52.950 за тот же
        #    прогон. Освободившиеся от нищеты руки вернулись в дойку. Проверка
        #    закона выше (ноль `starved`, поголовье живо) зелёная в обоих случаях;
        #    `wool` не сдвинулся вовсе (те же 5 коз и 5 овец стригутся 24
        #    месяца), `meat` — тоже (7.2).
        self.assertAlmostEqual(products["milk"], 17.6, places=6)
        self.assertAlmostEqual(products["meat"], 7.2, places=6)
        self.assertAlmostEqual(products["wool"], 6.507936507936508, places=10)
        for household_id in INITIAL_FAMILIES:
            self.assertGreater(_harvest(world, f"household:{household_id}", "grain"), 0.0)

    def test_wood_is_harvested_without_faking_housing(self) -> None:
        """ЗАКОН (ADR 0161 п. 1, п. 3): дрова доступны, лестница — не приговор.

        Прежний тест требовал от стенда трёх вещей разом: чтоб двор РУБИЛ, чтоб
        колол дрова и чтоб дошёл до планки. В v0 это недостижимо, и ADR 0161
        прямо разрешает: «двор без планки — просто беден». Проверка заменена на
        две части, и обе — по закону, а не по константе:

        1. **Дрова и брёвна достижимы** — двор, живущий у леса, при выборе
           топливного дела реально получает брёвна в СВОЙ амбар (принудительный
           выбор в тесте: стенд беден и сам до лесозаготовки не доходит, а
           закон про доступность дров от бедности не зависит). Это и есть
           непроверяемое иначе утверждение «дерево можно срубить».
        2. **Лестница не обязательна** — ни доска, ни планка не требуются, и
           тест говорит об этом прямо, вместо того чтобы требовать недостижимого.

        Призрачные проверки (действие не разрешает несуществующий рецепт, клетка
        не получила жильё) остались: они про закон, а не про баланс.
        """
        world = _world()
        # Двор у леса: `t_03_01` (выпас) соседствует с `t_03_00` (лес).
        fell = world.households["hh_family_01"]
        self.assertEqual(world.tiles[fell.current_tile_id].terrain, "pasture")
        fell.main_action = "cut_wood_if_allowed"
        fell.minor_action = "cut_wood_if_allowed"
        _apply_script(world, 1)
        run_month(world)

        cut = [
            entry
            for entry in world.ledger.entries
            if entry.reason == "cut_wood" and entry.good == "log"
        ]
        logs = [entry for entry in cut if entry.dst_id == fell.stock_id]
        self.assertTrue(
            logs, "Двор у леса не срубил ни бревна: дрова недостижимы"
        )
        fellers = {entry.dst_id for entry in logs}
        self.assertEqual(
            fellers, {fell.stock_id},
            f"Брёвна пришли не в амбар рубящего: {sorted(fellers)}",
        )
        self.assertGreater(sum(entry.amount for entry in logs), 0.0)
        # Второе: лестница в v0 не обязательна (ADR 0161 п. 1). Проверяем, что
        # двор не ПЫТАЛСЯ пилить, а просто не дотянул: ни доски, ни планки в его
        # амбаре нет — и это закон, а не поломка рецепта.
        barn = world.get_stock(fell.stock_id).amounts
        self.assertEqual(barn.get("board", 0.0), 0.0)
        self.assertEqual(barn.get("plank", 0.0), 0.0)
        # Материя не появилась ни рубкой, ни распилом.
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), 0.0, places=6,
            msg="Рубка создала материю",
        )
        # Призрак постройки (ADR 0150 п. 1) проверяем на живых признаках.
        recipe_ids = {recipe.id for recipe in world.catalogs.recipes.values()}
        for action_id, action in sorted(world.household_actions.items()):
            for rid in action.recipes:
                with self.subTest(action=action_id, recipe=rid):
                    self.assertIn(rid, recipe_ids, "Действие разрешает несуществующий рецепт")
                    self.assertNotIn(
                        rid, LAND_ACTIONS,
                        "Имя действия земли стало рецептом двора — это призрак",
                    )
        self.assertEqual(
            {tile.id for tile in world.tiles.values() if hasattr(tile, "dwelling")},
            {PASTURE_TILE, COURT_TILE},
            "Клетка получила жильё, которого в v0 нет",
        )
        self.assertEqual(world.tiles[PASTURE_TILE].dwelling, "tent_earth")
        self.assertEqual(world.tiles[COURT_TILE].dwelling, "tent_earth")

    def test_twenty_four_months_without_hunger_and_slaves_are_fed(self) -> None:
        world = _world()
        slaves = [
            person
            for person in world.persons.values()
            if person.personal_status == "slave"
        ]
        self.assertEqual(len(slaves), 10)
        hunger_by_month: list[tuple[int, str, int]] = []
        for month in range(1, MONTHS + 1):
            _apply_script(world, month)
            need = monthly_food_need(world, world.households[PLAYER])
            before = len(world.ledger.entries)
            run_month(world)
            board = sum(
                entry.amount
                for entry in world.ledger.entries[before:]
                if entry.reason == "board" and entry.dst_id == world.households[PLAYER].stock_id
            )
            self.assertGreaterEqual(board, need - 1e-6)
            for household_id in _tenant_ids(world):
                if world.households[household_id].hunger_days:
                    hunger_by_month.append(
                        (month, household_id, world.households[household_id].hunger_days)
                    )
            self.assertTrue(all(person.health > 0.0 for person in slaves))
        self.assertEqual(hunger_by_month, [])
        self.assertEqual(world.stats.get("hunger_months", 0.0), 0.0)
        self.assertEqual(len(_tenant_ids(world)), len(TENANTS))

    def test_wave_arrives_with_a_holding_and_food(self) -> None:
        world = _run_scripted(_world(), 6)
        self.assertNotIn(WAVE, world.households)
        self.assertEqual(_rights_for(world, WAVE), [])
        _apply_script(world, 7)
        wave = world.households[WAVE]
        self.assertEqual(len(wave.member_ids), 5)
        self.assertEqual(
            sum(
                person.household_id == WAVE and person.age_class == "adult"
                for person in world.persons.values()
            ),
            3,
        )
        rights = _rights_for(world, WAVE)
        self.assertEqual(len(rights), 1)
        self.assertEqual(rights[0].kind, "grazing")
        self.assertEqual(rights[0].tile_id, HOLDING_BY_TENANT[WAVE])
        self.assertGreater(wave.stock_id in world.stocks, 0)
        self.assertGreater(world.get_stock(wave.stock_id).amounts.get("grain", 0.0), 0.0)
        self.assertGreater(world.get_stock(wave.stock_id).amounts.get("hay", 0.0), 0.0)
        run_month(world)
        self.assertEqual(wave.holding_scale, VILLEIN_HOLDING_SCALE)
        self.assertEqual(wave.hunger_days, 0)
        self.assertTrue(
            any(
                report.facts.get("event") == "arrival"
                and report.facts.get("household_id") == WAVE
                for report in world.reports
            )
        )

    def test_hunt_moves_matter_in_the_tick_and_knowledge_only_by_report(self) -> None:
        world = _run_scripted(_world(), 2)
        hunt = [
            entry
            for entry in world.ledger.entries
            if entry.reason == "hunt" and entry.good == "meat"
        ]
        self.assertEqual(len(hunt), 1)
        self.assertAlmostEqual(hunt[0].amount, 8.0, places=6)
        self.assertEqual(hunt[0].src_id, f"tile:{FOREST_TILE}")
        self.assertEqual(hunt[0].dst_id, COURT)
        news = [
            report
            for report in world.reports
            if report.facts.get("event") == "hunt"
        ]
        self.assertEqual(len(news), 1)
        report = news[0]
        self.assertEqual(report.source, "adjacent_daily")
        self.assertEqual(report.confidence, 0.7)
        self.assertAlmostEqual(report.noise, 0.3, places=9)
        self.assertTrue(report.distorted)
        self.assertGreater(report.delivery_date, report.event_date)
        self.assertNotEqual(report.facts["amount_approx"], hunt[0].amount)
        self.assertEqual(report.facts["to"], "baron")
        self.assertNotIn("destination_stock_id", report.facts)

    def test_caravan_stays_on_the_stand(self) -> None:
        world = _run_scripted(_world(), 2)
        pack = world.packs["start_caravan"]
        self.assertEqual(pack.status, "arrived")
        self.assertEqual(pack.route[-1], PASTURE_TILE)
        self.assertTrue(
            any(
                entry.reason == "caravan_unload"
                and entry.dst_id == "settlement:farm"
                for entry in world.ledger.entries
            )
        )
        news = [
            report
            for report in world.reports
            if report.facts.get("event") == "caravan_arrival"
        ]
        self.assertEqual(len(news), 1)
        self.assertAlmostEqual(news[0].noise, 0.4, places=9)
        self.assertGreater(news[0].delivery_date, news[0].event_date)

    def test_matter_and_determinism(self) -> None:
        """ЗАКОН: мир не создаёт материю и читает свой seed.

        Тест держал константу `a8266a30…` — 24 месяца, сид 1729, после
        `engine/growth.py::index_growth_tiles`, после `log: 6.0` в амбаре лорда,
        после ОКНА оброка (ADR 0192 п. 1) и после `grow_grain.amount`
        50.0 → 40.0. Последнее движение отменено ADR 0200: 40.0 было молчаливой
        правкой без закона, и 50.0 вернули по `ADR 0141:45-46`. Константа
        сдвинулась законно, и её перенос был бы переносом реестра, а не
        проверкой.

        Взамен четыре утверждения, и ни одно не знает числа урожая:

        **1. Материя не появилась** (И-1, `docs/00_constitution.md:10-17`) — на
        стенде с приказами игрока и на обоих прогонах `runner.run`.

        **2. Тот же seed — тот же хеш.** Мир собран целиком из одного seed, и
        повтор обязан совпасть до знака.

        **3. ДРУГОЙ seed — ДРУГОЙ хеш.** Это ровно то, ради чего в старом тесте
        была константа: пара «два одинаковых прогона» не ловит мир, который
        seed игнорирует. Мутация «забить seed константой в `load_scenario`»
        даёт одинаковые хеши при 1729 и 1730 — и пункт краснеет.

        **4. Хеш — не пустая строка и не заглушка.** Мир материален и
        действительно менялся: `total_matter > 0`, месяцев ровно `MONTHS`, дата
        ровно `SimDate(3, 1, 1)`. Мутация «вернуть из `state_hash` только дату»
        ломает пункт 2, а не этот, — и это честно: пункт 4 стерел бы закон,
        который держит пункт 2.
        """
        world = _run_scripted(_world())
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

        first = run(SCENARIO, MONTHS, seed=SEED)
        second = run(SCENARIO, MONTHS, seed=SEED)
        other_seed = run(SCENARIO, MONTHS, seed=SEED + 1)

        # 1. Материя не появилась.
        for label, result in (
            ("стенд с приказами", None),
            ("runner.run, первый", first),
            ("runner.run, второй", second),
            ("runner.run, другой seed", other_seed),
        ):
            with self.subTest(run=label):
                if result is None:
                    self.assertAlmostEqual(
                        world.ledger.delta(world.total_matter()), 0.0, places=6,
                    )
                else:
                    self.assertAlmostEqual(result.matter_delta, 0.0, places=6)

        # 2. Тот же seed — тот же хеш.
        self.assertEqual(first.state_hash, second.state_hash)

        # 3. Другой seed — другой хеш.
        self.assertNotEqual(
            first.state_hash, other_seed.state_hash,
            "Мир не читает seed: пара одинаковых прогонов этого не видит, "
            "а зашитая константа видела",
        )

        # 4. Хеш не заглушка, прогон полон.
        for result in (first, second, other_seed):
            self.assertTrue(result.state_hash)
            self.assertGreater(result.total_matter, 0.0)
            self.assertEqual(len(result.monthly), MONTHS)
            self.assertEqual(result.final_date, SimDate(3, 1, 1))


if __name__ == "__main__":
    unittest.main()
