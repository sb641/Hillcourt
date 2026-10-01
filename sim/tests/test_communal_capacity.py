"""Ёмкость клетки растёт от числа работников. Капа партий на клетке НЕТ (ADR 0137).

Режимов три, и их смешивать нельзя. Этому модулю принадлежат первые два:

1. **Пашня** (уборка `harvest_grain`) и **общинный сбор** (`forage_wild` по
   `works_tiles`/`Right.common`). Капа партий на клетке нет и делёжа партий между
   дворами на одной клетке нет: `economy/labor.py:530` и `:815` — «на гексе нет
   (ADR 0137): ни общих счётчиков партий по клетке, ни `_fair_share`». Работу
   ограничивают труд двора и стоячая материя клетки.
2. **Стойло скота, сено тяглом** (`gather_draft_hay`, `economy/livestock.py:594`) —
   этого режима здесь НЕТ, и он не трогается. Кап общий на пастбище месяца —
   `labor._tile_batch_cap` (ADR 0050/0055), и он ЖИВ: это кап СТОЙЛА, другое число,
   случайно равное капу пашни. Функция `_tile_batch_cap` вызывается ровно из
   одного места — `livestock.py:615`. В `work_month` (`labor.py:807`) она не
   вызывается ни разу, поэтому ни одна проверка этого модуля её не режет.
   Охраняет этот кап `test_bloom_and_hay.TestHayTileCap.test_shared_pasture_cap_not_bypassed`.

Закон режима 1 проверяется сравнениями, а не константой:
  * (a) общинное поле `works_tiles`: выход 1 < 2 < 5 дворов при равном труде —
    ёмкость РАСТЁТ от работников, потолка 4 (или любого фиксированного) нет;
  * (b) общинный сбор `Right.kind = common`: то же, и второй двор УВЕЛИЧИВАЕТ
    общий выход, а не делит его поровну (отменённая норма ADR 0173 п. 2);
  * (c) личный надел `tenure`: то же — держание по праву не вводит капа;
  * (d) `grazing` на выпасе, `gather_hay` в `work_month`: то же. Это НЕ стойло
    скота (режим 2 выше): сено тяглом собирает отдельная фаза `phase_hay`;
  * (e) стоячая материя клетки — ОДИН запас на всех: N дворов не умножают её и
    никто не берёт «полную виргату» из запаса, рассчитанного на одного;
  * (f) месяц двора ограничен ЕГО ТРУДОМ, а не ёмкостью клетки: две общинные
    клетки не удваивают месяц одного двора;
  * (g) общинный доступ — не держание: `works_tiles` в `_held_tile_ids` не входят;
  * (h) переименование дворов не меняет их доли (детерминизм, а не регламент);
  * (i) дельта материи ≈ 0 во всех прогонах.
"""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

from hillcourt.economy.labor import (
    _held_tile_ids,
    _tile_batch_cap,
    own_tiles,
    work_month,
)
from hillcourt.engine.manor import grant_tenement
from hillcourt.ontology import Right, Stock
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"

SALT_VILLAGE = "salt_village"
COMMUNAL_FIELD = "t_06_05"  # поле соляной деревни, режим tenement, по праву не держится
COMMUNAL_FIELD_ALT = "t_06_04"  # второе поле той же деревни, режим tenement
COMMUNAL_WOOD = "t_07_05"  # общинный лес, режим reserved_wood
NEIGHBOUR = "t_06_05"  # клетка, откуда виден общинный лес (соседство — не право)
HOLDING = "t_06_02"  # личный надел под жатву
PASTURE = "t_00_02"  # выпас под сено
HOUSEHOLDS = ("hh_01", "hh_02", "hh_04", "hh_05", "hh_09")
COUNTS = (1, 2, 5)
HARVEST = "harvest_grain"
HAY = "gather_hay"
FORAGE = "forage_wild"
LABOR = 400.0
HOLDER_LABOR = 200.0
SEED = 1729
SEED_MATTER = 4000.0


def _batches(world, tile_id: str, reason: str, good: str) -> int:
    """Партии рецепта, снятые с клетки: одна партия = одна проводка этого товара."""
    stock_id = world.tiles[tile_id].standing_stock_id
    return sum(
        1
        for entry in world.ledger.entries
        if entry.kind == "transfer"
        and entry.src_id == stock_id
        and entry.reason == reason
        and entry.good == good
    )


def _standing(world, tile_id: str, good: str) -> float:
    return world.get_stock(world.tiles[tile_id].standing_stock_id).amounts.get(good, 0.0)


def _seed_standing(world, tile_id: str, good: str, amount: float = SEED_MATTER) -> None:
    """Засеять стоячую материю клетки внешним приходом (дельта остаётся 0)."""
    stock = world.get_stock(world.tiles[tile_id].standing_stock_id)
    world.ledger.external_in(
        stock, good, amount, "test_seed_standing", None, world.clock.date
    )


def _arms(world, household_ids: tuple[str, ...], action: str) -> None:
    """Руки вдоволь, а сила ровно та, что даёт месяц труда."""
    for household in world.households.values():
        household.labor_days = 0.0
    for household_id in household_ids:
        household = world.households[household_id]
        household.main_action = action
        household.minor_action = "idle_repair"
        household.labor_days = LABOR


def _communal_field_world(count: int, seed: float = SEED_MATTER):
    """`count` дворов с доступом к ОДНОМУ общинному полю поселения."""
    world = load_scenario(SCENARIO)
    ids = HOUSEHOLDS[:count]
    for household_id in ids:
        world.households[household_id].settlement_id = SALT_VILLAGE
    world.settlements[SALT_VILLAGE].works_tiles = [COMMUNAL_FIELD]
    _arms(world, ids, "work_plot")
    _seed_standing(world, COMMUNAL_FIELD, "grain", seed)
    return world, ids


def _communal_wood_world(count: int):
    """`count` дворов с `Right.kind = common` на одном общинном лесу."""
    world = load_scenario(SCENARIO)
    ids = HOUSEHOLDS[:count]
    for index, household_id in enumerate(ids):
        household = world.households[household_id]
        household.current_tile_id = NEIGHBOUR
        right_id = f"right_test_common_{index}"
        world.rights[right_id] = Right(
            id=right_id,
            holder_household_id=household_id,
            tile_id=COMMUNAL_WOOD,
            kind="common",
            granted_date=world.clock.date,
            rent_share=0.0,
        )
    _arms(world, ids, "forage_adjacent")
    _seed_standing(world, COMMUNAL_WOOD, "firewood")
    _seed_standing(world, COMMUNAL_WOOD, "grain")
    return world, ids


def _holding_world(count: int, kind: str, tile_id: str, good: str):
    """`count` дворов с личным держанием `kind` на одной клетке."""
    world = load_scenario(SCENARIO)
    ids = HOUSEHOLDS[:count]
    for household_id in ids:
        grant_tenement(world, household_id, [tile_id], kind=kind, rent_share=0.0)
    _arms(world, ids, "work_plot")
    _seed_standing(world, tile_id, good)
    return world, ids


def _one_month(world) -> None:
    work_month(world, world.clock.date)


def _shares(world, tile_id: str, reason: str, good: str, ids: tuple[str, ...]) -> list[int]:
    """Партии по дворам: одна партия — одна проводка товара В сток двора.

    Выход рецепта идёт через `sink:processing`, поэтому партия двора видна как
    запись `process` с `dst_id` его стока, а снятие с клетки — как `transfer` из
    стоячего стока клетки.
    """
    return [
        sum(
            1
            for entry in world.ledger.entries
            if entry.kind == "process"
            and entry.reason == reason
            and entry.good == good
            and entry.dst_id == world.households[hid].stock_id
        )
        for hid in ids
    ]


def _consumed(world, tile_id: str, good: str) -> float:
    """Сколько стоячей материи клетки сняли за месяц (из посеянного запаса)."""
    return SEED_MATTER - _standing(world, tile_id, good)


def _renamed_holders_world(names: tuple[str, ...], tile_id: str = HOLDING):
    """`names` — дворы-держатели одной клетки, клонированные под новыми именами.

    Остальные дворы из мира убраны, чтобы клетку пахали только эти двое: сравнение
    идёт по их долям, а не по сумме со сценарием.
    """
    world = load_scenario(SCENARIO, seed=SEED)
    for household in world.households.values():
        household.labor_days = 0.0
    template = world.households[HOUSEHOLDS[1]]
    holders = []
    for name in names:
        household = copy.deepcopy(template)
        household.id = name
        household.name = name
        household.member_ids = [f"p_{name}"]
        household.stock_id = f"household:{name}"
        world.add_stock(
            Stock(id=household.stock_id, owner_kind="household", owner_id=name, amounts={})
        )
        world.households[name] = household
        grant_tenement(world, name, [tile_id], kind="tenure", rent_share=0.0)
        household.main_action = "work_plot"
        household.minor_action = "idle_repair"
        household.holding_scale = 1.0
        household.labor_days = HOLDER_LABOR
        holders.append(household)
    world.households = {household.id: household for household in holders}
    _seed_standing(world, tile_id, "grain")
    return world, names


class _CapacityLaw(unittest.TestCase):
    """Общий каркас режима 1: ёмкость растёт от работников, потолка нет."""

    def setUp(self) -> None:
        self.last_world = None

    def assert_grows_with_workers(
        self,
        build,
        tile_id: str,
        reason: str,
        good: str,
        what: str,
        equal_shares: bool = True,
    ) -> dict[int, int]:
        """Выпуск клетки строго растёт с числом дворов при равном труде.

        Проверяется СРАВНЕНИЕМ, а не константой: если кап партий на клетке
        вернётся (отменённый ADR 0132 п. 1, отменён ADR 0173 п. 2 / 0137), уже
        второй двор не сможет превысить выход первого, и строгий рост провалится.

        `equal_shares` — проверка «равный труд, равная доля». Она законна там, где
        труд двора уходит только в жатву/сбор. Для выпаса (d) не проверяется:
        тот же труд делится с навозом и сменой полей (`labor.py:860-864`), и доли
        дворов обязаны совпадать не могут — там закон только про ИТОГ клетки.
        """
        totals: dict[int, int] = {}
        shares: dict[int, list[int]] = {}
        for count in COUNTS:
            world, ids = build(count)
            self.last_world = world
            _one_month(world)
            totals[count] = _batches(world, tile_id, reason, good)
            shares[count] = _shares(world, tile_id, reason, good, ids)
            self.assertGreater(
                totals[count],
                0,
                f"{what}: {count} двора не собрали ни одной партии",
            )
            self.assertEqual(
                sum(shares[count]),
                totals[count],
                f"{what}: выход клетки {totals[count]} не разошёлся по дворам"
                f" ({shares[count]})",
            )
            self.assertAlmostEqual(
                world.ledger.delta(world.total_matter()), 0.0, places=6
            )
        for smaller, bigger in zip(COUNTS, COUNTS[1:]):
            self.assertGreater(
                totals[bigger],
                totals[smaller],
                f"{what}: {bigger} двора не дали больше {smaller}"
                f" ({totals[bigger]} против {totals[smaller]}) — ёмкость не растёт"
                f" от работников (ADR 0137)",
            )
        if equal_shares:
            # Равный труд — равная доля: выход делится по числу РАБОТНИКОВ,
            # а не поровну между соседями по клетке (отменённый делёж ADR 0173 п. 2).
            for count in COUNTS:
                self.assertEqual(
                    len(set(shares[count])),
                    1,
                    f"{what}: {count} двора с равным трудом взяли не поровну:"
                    f" {shares[count]}",
                )
        return totals

    def assert_no_stall_cap_applies(self, total: int, what: str) -> None:
        """Кап СТОЙЛА (`_tile_batch_cap`) к пашне и сбору не применяется.

        Число берётся из кода, а не из теста: если кто-то вернёт отменённый
        общий кап партий в `work_month`, выход перестанет его превышать. Закон
        стойла (ADR 0050/0055) при этом остаётся: он живёт в
        `economy/livestock.py:615` и охраняется тестом
        `test_bloom_and_hay.TestHayTileCap.test_shared_pasture_cap_not_bypassed`.
        """
        self.assertIsNotNone(self.last_world)
        stall_cap = _tile_batch_cap(self.last_world)
        self.assertGreater(
            total,
            stall_cap,
            f"{what}: {total} партий не превышают кап стойла {stall_cap} —"
            f" кап стойла не кап пашни (ADR 0143 п. 2)",
        )


class TestCapacityGrowsWithWorkers(_CapacityLaw):
    """Ёмкость клетки — функция числа работников, а не регламент (ADR 0137)."""

    def test_communal_field_capacity_grows_with_workers(self) -> None:
        """(a) Пашня: общинное поле `works_tiles`, 1 < 2 < 5 дворов при равном труде."""
        totals = self.assert_grows_with_workers(
            _communal_field_world,
            COMMUNAL_FIELD,
            HARVEST,
            "grain",
            "общинное поле",
        )
        self.assert_no_stall_cap_applies(totals[1], "общинное поле")

    def test_communal_wood_collection_grows_with_workers(self) -> None:
        """(b) Общинный сбор `Right.common`: второй двор УВЕЛИЧИВАЕТ общий выход.

        Отменённая норма ADR 0132 п. 2 («кап 4 на клетку, делёж поровну») не
        возвращается: выход не делится поровну и в 4 не упирается. Символа
        `_communal_collection_shared` в коде нет — документация была stale.
        """
        totals = self.assert_grows_with_workers(
            _communal_wood_world,
            COMMUNAL_WOOD,
            FORAGE,
            "firewood",
            "общинный лес",
        )
        self.assert_no_stall_cap_applies(totals[1], "общинный лес")

    def test_tenure_holding_capacity_grows_with_workers(self) -> None:
        """(c) Личный надел `tenure`: держание по праву капа не вводит."""
        totals = self.assert_grows_with_workers(
            lambda count: _holding_world(count, "tenure", HOLDING, "grain"),
            HOLDING,
            HARVEST,
            "grain",
            "личный надел",
        )
        self.assert_no_stall_cap_applies(totals[1], "личный надел")

    def test_grazing_hay_grows_with_workers(self) -> None:
        """(d) `grazing` на выпасе, `gather_hay` в `work_month`: капа клетки нет.

        НЕ стойло скота: сено тяглом собирает `gather_draft_hay`
        (`livestock.py:594`) из фазы `phase_hay` (`engine/tick.py:203`), и там
        кап пастбища общий и жив (ADR 0050/0055). Здесь двор с `work_plot`
        косит СВОЙ надел в `work_month`, где `_tile_batch_cap` не вызывается
        вовсе, и выход ограничен только трудом.
        """
        totals = self.assert_grows_with_workers(
            lambda count: _holding_world(count, "grazing", PASTURE, "hay"),
            PASTURE,
            HAY,
            "hay",
            "выпас `grazing`",
            equal_shares=False,
        )
        self.assert_no_stall_cap_applies(totals[1], "выпас `grazing`")


class TestStandingPoolIsShared(_CapacityLaw):
    """Стоячая материя клетки — один запас: её нельзя размножить числом дворов."""

    def test_one_pool_is_not_multiplied_by_households(self) -> None:
        """(e) 5 дворов на запасе одного не дают 5 × выхода одного.

        Размер запаса берётся ИЗМЕРЕНИЕМ (сколько снял один двор), а не выдуманным
        числом: «ёмкость общая» сегодня означает ровно это — клетка не создаёт
        материю и не раздаёт каждому «полную виргату». Когда запаса хватает всем
        (тесты (a)-(d)), выход растёт; когда не хватает — делится, но не
        умножается.
        """
        one, _ = _communal_field_world(1)
        _one_month(one)
        single_total = _batches(one, COMMUNAL_FIELD, HARVEST, "grain")
        self.assertGreater(single_total, 0)
        pool = _consumed(one, COMMUNAL_FIELD, "grain")

        count = 5
        world, ids = _communal_field_world(count, seed=pool)
        _one_month(world)

        total = _batches(world, COMMUNAL_FIELD, HARVEST, "grain")
        shares = _shares(world, COMMUNAL_FIELD, HARVEST, "grain", ids)
        self.assertEqual(sum(shares), total, "Выход клетки не разошёлся по дворам")
        self.assertGreater(total, 0, "Общий запас не достался ни одному двору")
        self.assertLess(
            total,
            count * single_total,
            f"{count} дворов размножили запас клетки: {total} партий против"
            f" {count} × {single_total} — материя не создаётся",
        )
        self.assertLessEqual(
            total,
            single_total,
            "С общего запаса сняли больше, чем снимает один двор: запас один",
        )
        self.assertLess(
            min(shares),
            single_total,
            f"Каждый двор взял полную долю одного ({shares}) — клетка не раздаёт"
            f" «полную виргату» каждому (ADR 0068/J3 в жившей части)",
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)

    def test_two_communal_tiles_do_not_double_a_household_month(self) -> None:
        """(f) Месяц двора ограничен его трудом, а не ёмкостью клетки.

        Две общинные клетки под одним двором не удваивают его месяц: труд уходит
        на первую клетку до конца бюджета, вторая ждёт следующего месяца. Это и
        есть «ёмкость растёт от работников»: работник один — месяц один.
        """
        one_tile, _ = _communal_field_world(1)
        _one_month(one_tile)
        single = _batches(one_tile, COMMUNAL_FIELD, HARVEST, "grain")

        world = load_scenario(SCENARIO)
        self.last_world = world
        ids = (HOUSEHOLDS[0],)
        world.households[ids[0]].settlement_id = SALT_VILLAGE
        world.settlements[SALT_VILLAGE].works_tiles = [
            COMMUNAL_FIELD,
            COMMUNAL_FIELD_ALT,
        ]
        _arms(world, ids, "work_plot")
        _seed_standing(world, COMMUNAL_FIELD, "grain")
        _seed_standing(world, COMMUNAL_FIELD_ALT, "grain")
        _one_month(world)

        total = _batches(world, COMMUNAL_FIELD, HARVEST, "grain") + _batches(
            world, COMMUNAL_FIELD_ALT, HARVEST, "grain"
        )
        self.assertGreater(total, 0, "Двор на двух клетках не собрал ничего")
        self.assertEqual(
            total,
            single,
            "Вторая общинная клетка удвоила месяц одного двора: труд не резиновый",
        )
        self.assert_no_stall_cap_applies(total, "две общинные клетки")
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


class TestCommunalAccessIsNotHolding(unittest.TestCase):
    """Общинный доступ (`works_tiles`, `Right.common`) — не держание (ADR 0060)."""

    def test_works_tile_is_not_a_held_tile(self) -> None:
        """(g) Общинное поле в `_held_tile_ids` не входит, а в `own_tiles` входит.

        Двор пашет общинное поле по праву доступа, но клетка не становится его
        наделом: `_held_tile_ids` отдаёт только усадьбу и клетки по `Right` без
        `common` (`labor.py:221`).
        """
        world, ids = _communal_field_world(2)
        household = world.households[ids[0]]
        self.assertEqual(household.settlement_id, SALT_VILLAGE)
        self.assertIn(
            COMMUNAL_FIELD,
            {tile.id for tile in own_tiles(world, household)},
            "Общинное поле не попало в надел двора — доступ не работает",
        )
        self.assertNotIn(
            COMMUNAL_FIELD,
            _held_tile_ids(world, household),
            "Общинное поле вдруг стало держанием по праву",
        )


class TestHolderNamesDoNotMatter(unittest.TestCase):
    """Имя двора не имеет права менять, кто сколько собрал."""

    def test_renaming_holders_changes_no_share(self) -> None:
        """(h) Двое держателей одной клетки под разными именами берут одно и то же.

        Порядок обхода дворов — по id (`labor.py:818`), поэтому проверка ловит
        утечку результата в имя или в порядок объявления, а не регламент делёжа.
        """
        first, _ = _renamed_holders_world(("aaa_1", "aaa_2"))
        _one_month(first)
        second, _ = _renamed_holders_world(("zzz_2", "zzz_1"))
        _one_month(second)

        first_shares = _shares(first, HOLDING, HARVEST, "grain", ("aaa_1", "aaa_2"))
        second_shares = _shares(second, HOLDING, HARVEST, "grain", ("zzz_2", "zzz_1"))
        self.assertGreater(sum(first_shares), 0, "Держатели ничего не собрали")
        self.assertEqual(
            first_shares, second_shares, "Переименование дворов изменило их доли"
        )
        self.assertEqual(
            _batches(first, HOLDING, HARVEST, "grain"),
            _batches(second, HOLDING, HARVEST, "grain"),
            "Переименование дворов изменило выход клетки",
        )
        for world in (first, second):
            self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
