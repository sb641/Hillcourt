"""ЗАКОН ПРАВА (ADR 0206): соседство гексовое, ставка оброка — ставка, сумма
оброка — не приказ.

Три закона, которые этот файл запирает. По каждому указана мутация, которая
обязана ронять тест; зелёный тест без такой мутации — закон, который не в силе.

1. **Смежность спрашивает `hexgrid`, а не манхэттен** (ADR 0203 §4, долг закрыт).
   Замер Стола плейтеста: `legal._adjacent` отвергала 26 % настоящих соседств на
   `start_stand` (12 из 46) и 31 % на `v0_shire` (160 из 516). Мутация: вернуть
   `abs(ox-dx) + abs(oy-dy) == 1` в тело `_adjacent`.

2. **`rent_share` — доля урожая, а `0.0` — «оброк не заводится».** Мутация: в
   `rent_rate_for` всегда брать `template.default_share` (то есть ADR 0204-временный
   «переключатель»). Тогда счёт 90 %-ной ставки сравняется со счётом 10 %-ной.

3. **Вид «рента-доля» не принимает `due_amount` от приказа** — сумма выводится из
   ставки права и урожая прошлого месяца. Мутация: убрать `raise ValueError` в
   `add_obligation` (число снова принимается молча и выбрасывается).

Числа сняты с дерева после правки: `start_stand`, сид 1729, 12 месяцев, надел
`hh_family_01` на `t_02_00`, выдан на 1-м месяце:

| `rent_share` | счёт за 12 месяцев | отношение к 0.1 |
|---|---|---|
| 0.0 | 0.0 (оброк не заведён) | — |
| 0.1 | 6.615 | 1 |
| 0.5 | 33.075 | 5 |
| 0.9 | 59.535 | **9** |
| 1.0 | 66.150 | 10 |

До правки (ADR 0204) 0.1 / 0.5 / 0.9 / 1.0 давали одно и то же число, потому что
размер оброка брался из каталога. Ровно это и проверяет закон ниже.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.hexgrid import axial_is_neighbor, tile_ids_are_neighbor
from hillcourt.engine.tick import run_month
from hillcourt.legal import actions as legal_actions
from hillcourt.legal import obligations as rent_law
from hillcourt.legal.actions import _adjacent, add_obligation, grant_tenure, send_party
from hillcourt.legal.obligations import rent_rate_for
from hillcourt.legal.regimes import can_be_sent
from hillcourt.runner import _apply_script_entry, run
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
STAND = ROOT / "design" / "scenarios" / "start_stand.yml"
SHIRE = ROOT / "design" / "scenarios" / "v0_shire.yml"
SEED = 1729
MONTHS = 12
HARVEST_REASON = "harvest_grain"
#: Двор и клетка стенда, на которых снимались числа выше.
HOUSEHOLD = "hh_family_01"
TILE = "t_02_00"
#: Второй двор того же стенда: ему выдаётся другая ставка, поэтому сравнение
#: идёт внутри одного мира, где урожай дворов заведомо разный.
OTHER_HOUSEHOLD = "hh_family_05"
OTHER_TILE = "t_00_02"


def _manhattan(origin: str, destination: str) -> bool:
    """Старая, сломанная смежность по координатам карты — эталон для мутации."""
    ox, oy = int(origin.split("_")[1]), int(origin.split("_")[2])
    dx, dy = int(destination.split("_")[1]), int(destination.split("_")[2])
    return abs(ox - dx) + abs(oy - dy) == 1


def _harvest_of(world, stock_id: str, year: int, month: int) -> float:
    """Урожай стока за ОДИН месяц, посчитанный прямо из проводок (ADR 0206).

    Чтение проводок, а не вызов `household_rent_due_base`: иначе проверка
    сравнивала бы функцию с самой собой и прошла бы на любой сломанной базе.
    """
    return sum(
        entry.amount
        for entry in world.ledger.entries
        if entry.reason == HARVEST_REASON
        and entry.kind == "process"
        and entry.good == "grain"
        and entry.dst_id == stock_id
        and entry.date.year == year
        and entry.date.month == month
    )


def _scripted_months(world, months: int) -> None:
    for month in range(1, months + 1):
        for entry in world.script:
            if int(entry.get("at_month", 0)) == month:
                _apply_script_entry(world, entry)
        run_month(world)


def _bill(share: float, months: int = MONTHS) -> float:
    """Счёт за `months` месяцев при ставке `share`, как его оставил тик.

    Суммируется `due_amount`, **прочитанный после тика**: пересчитывать его вручную
    значило бы проверять не тот счёт, который лорд выставил (ADR 0192).
    """
    world = load_scenario(STAND, seed=SEED)
    total = 0.0
    for month in range(1, months + 1):
        for entry in world.script:
            if int(entry.get("at_month", 0)) == month:
                _apply_script_entry(world, entry)
        if month == 1:
            grant_tenure(world, HOUSEHOLD, TILE, kind="grazing", rent_share=share)
        run_month(world)
        obligation = world.obligations.get(f"obl_{HOUSEHOLD}_rent")
        if obligation is not None:
            total += obligation.due_amount
    return round(total, 3)


class TestNeighbourIsOneTruthInLegal(unittest.TestCase):
    """Смежность клеток — одна функция, и она гексовая (ADR 0203 §4)."""

    def _world_tile_ids(self, path: Path) -> list[str]:
        return sorted(load_scenario(path, seed=SEED).tiles)

    def test_legal_agrees_with_hexgrid_on_every_pair(self) -> None:
        """Ни одно настоящее соседство не отвергается, и ни одно лишнее не принято.

        Сравнение идёт по всем упорядоченным парам и по обоим знакам ошибки, а не
        по выборочным примерам: примеры проходят и на сломанной функции, если
        попали в четыре «карточных» направления.

        Мутация, которая обязана ронять тест: вернуть в `_adjacent` манхэттен по
        координатам карты. Тогда отвергнутых настоящих соседств 12 (`start_stand`)
        и 160 (`v0_shire`), а принятых лишних — тоже не ноль: `t_00_01` и `t_01_01`
        соседями по гексу не являются, а по манхэттену являются.
        """
        for path, before_rejected in ((STAND, 12), (SHIRE, 160)):
            with self.subTest(scenario=path.name):
                ids = self._world_tile_ids(path)
                rejected: list[tuple[str, str]] = []
                accepted_wrongly: list[tuple[str, str]] = []
                for origin in ids:
                    for destination in ids:
                        if origin == destination:
                            continue
                        truth = tile_ids_are_neighbor(origin, destination)
                        said = _adjacent(origin, destination)
                        if truth and not said:
                            rejected.append((origin, destination))
                        if said and not truth:
                            accepted_wrongly.append((origin, destination))
                self.assertEqual(
                    rejected, [],
                    f"{path.name}: отвергнуто {len(rejected)} настоящих соседств "
                    f"(было {before_rejected}); первые: {rejected[:4]}",
                )
                self.assertEqual(
                    accepted_wrongly, [],
                    f"{path.name}: принято {len(accepted_wrongly)} несоседних пар "
                    f"(манхэттен по карте — это 4 из 6 направлений); "
                    f"первые: {accepted_wrongly[:4]}",
                )

    def test_a_tile_is_not_its_own_neighbour(self) -> None:
        """Сторож на одностороннюю «починку»: клетка не сосед сама с собой."""
        for tile_id in ("t_00_00", "t_01_01", "t_05_08"):
            with self.subTest(tile=tile_id):
                self.assertFalse(_adjacent(tile_id, tile_id))

    def test_a_hex_neighbour_the_old_code_rejected_now_takes_the_party(self) -> None:
        """Закон, который видит игрок: `send_party` идёт на гекс, который отвергал манхэттен.

        Пара `av_08`: `t_05_08` → `t_04_07`. По координатам карты это
        `|5−4| + |8−7| = 2`, то есть манхэттен говорит «не соседняя», а по гексу
        это настоящий сосед (`axial_is_neighbor` → `True`).

        Мутация, которая обязана ронять тест: вернуть манхэттен — приказ упадёт с
        `ValueError` вместо `Pack`.
        """
        origin, destination = "t_05_08", "t_04_07"
        self.assertFalse(
            _manhattan(origin, destination),
            "подставная пара перестала быть примером сломанной смежности: правка "
            "сместилась, а тест продолжает считать, что манхэттен её отвергает",
        )
        world = load_scenario(SHIRE, seed=SEED)
        household = world.households["av_08"]
        self.assertTrue(can_be_sent(world, household), "подготовка не удалась: двор не посылается")
        adult = next(
            pid for pid in household.member_ids
            if world.persons.get(pid) is not None and world.persons[pid].age_class == "adult"
        )
        pack = send_party(world, "av_08", [adult], destination)
        self.assertEqual(pack.route, [origin, destination])
        self.assertEqual(pack.destination_tile_id, destination)
        self.assertEqual(world.persons[adult].location_tile_id, destination)


class TestRentShareIsTheRateTheLordNamed(unittest.TestCase):
    """`rent_share` — доля урожая (ADR 0206). `docs/01:12` «сколько платить» исполнена."""

    def test_the_bill_is_exactly_nine_times_the_bill_at_one_tenth(self) -> None:
        """Главное из замера: 0.9 и 0.1 больше не дают одно число.

        Мутация, которая обязана ронять тест: в `rent_rate_for` всегда брать
        `template.default_share` — тогда обе ставки дадут 6.615, и отношение
        перестанет быть девятью.

        Один двор, один сид, отличается только ставка приказа: иначе разницу могли
        бы дать урожаи, а не закон.
        """
        tenth = _bill(0.1)
        ninetieth = _bill(0.9)
        self.assertGreater(tenth, 0.0, "подготовка не удалась: при 0.1 оброк не начислен")
        self.assertAlmostEqual(
            ninetieth, 9.0 * tenth, places=2,
            msg=(
                f"счёт ставки 0.9 ({ninetieth}) не в 9 раз больше счёта ставки 0.1 "
                f"({tenth}): ставка приказа не влияет на оброк (ADR 0206)"
            ),
        )
        self.assertAlmostEqual(_bill(0.5), 5.0 * tenth, places=2, msg="ставка 0.5 поехала")
        self.assertAlmostEqual(_bill(1.0), 10.0 * tenth, places=2, msg="ставка 1.0 поехала")

    def test_each_bill_equals_its_own_share_of_its_own_harvest(self) -> None:
        """Закон по существу, а не по итогу: счёт = ставка права × урожай прошлого месяца.

        В одном мире живут два двора с РАЗНЫМИ ставками (0.1 и 0.9) и разным
        урожаем. У каждого счёт обязан равняться его ставке от ЕГО урожая, где
        урожай посчитан прямо из проводок `harvest_grain`. Проверка не проходит на
        мутации «размер всегда из каталога»: у 0.9-ного двора счёт был бы 0.1 от
        его урожая.

        Мутация: в `rent_rate_for` вернуть `float(template.default_share)`.
        """
        world = load_scenario(STAND, seed=SEED)
        rates = {HOUSEHOLD: 0.1, OTHER_HOUSEHOLD: 0.9}
        tiles = {HOUSEHOLD: TILE, OTHER_HOUSEHOLD: OTHER_TILE}
        checked_total = 0
        for month in range(1, 13):
            for entry in world.script:
                if int(entry.get("at_month", 0)) == month:
                    _apply_script_entry(world, entry)
            if month == 1:
                for household_id, share in rates.items():
                    grant_tenure(
                        world, household_id, tiles[household_id],
                        kind="grazing", rent_share=share,
                    )
            this_year, this_month = world.clock.date.year, world.clock.date.month
            run_month(world)
            previous = (
                (this_year, this_month - 1) if this_month > 1
                else (this_year - 1, world.clock.months_per_year)
            )
            for household_id, share in rates.items():
                household = world.households[household_id]
                obligation = world.obligations.get(f"obl_{household_id}_rent")
                if obligation is None or household.left_at is not None:
                    continue
                base = _harvest_of(world, household.stock_id, *previous)
                if base <= 0.0:
                    continue
                checked_total += 1
                self.assertAlmostEqual(
                    obligation.due_amount, round(share * base, 3), places=3,
                    msg=(
                        f"{household_id} в {previous[0]}-{previous[1]:02d}: счёт "
                        f"{obligation.due_amount} не равен ставке {share} от урожая "
                        f"{base}. Ставка приказа не применена (ADR 0206)"
                    ),
                )
        self.assertGreater(
            checked_total, 0,
            "за 12 месяцев не нашлось ни одного обложенного месяца: проверка "
            "счёта вхолостую",
        )

    def test_the_rate_is_read_from_the_right_and_not_from_the_catalog(self) -> None:
        """Ставка — поле права; каталог держит умолчание, а не потолок ставки."""
        world = load_scenario(STAND, seed=SEED)
        for entry in world.script:
            if int(entry.get("at_month", 0)) == 1:
                _apply_script_entry(world, entry)
        right = grant_tenure(
            world, HOUSEHOLD, TILE, kind="grazing", rent_share=0.9
        )
        obligation = world.obligations[f"obl_{HOUSEHOLD}_rent"]
        self.assertEqual(obligation.right_id, right.id)
        self.assertAlmostEqual(
            rent_rate_for(world, obligation), 0.9, places=9,
            msg="ставка оброка взята не из права, а из каталога (ADR 0206)",
        )
        self.assertNotAlmostEqual(
            rent_rate_for(world, obligation),
            float(rent_law.rent_template(world).default_share),
            places=9,
            msg="право с ставкой 0.9 обязано отличаться от каталожной доли",
        )

    def test_a_promotion_without_a_right_falls_back_to_the_catalog_share(self) -> None:
        """Правом не заведено — действует умолчание каталога, а не ноль.

        Мутация, которая обязана ронять тест: при `right_id=None` вернуть `0.0`
        вместо `default_share` — оброк бы тихо исчез у повинности без права.
        """
        world = load_scenario(STAND, seed=SEED)
        right = grant_tenure(world, HOUSEHOLD, TILE, kind="grazing", rent_share=0.9)
        obligation = world.obligations[f"obl_{HOUSEHOLD}_rent"]
        world.rights.pop(right.id)
        template = rent_law.rent_template(world)
        self.assertAlmostEqual(
            rent_rate_for(world, obligation), float(template.default_share), places=9,
            msg="без права ставка должна быть каталожной (ADR 0206)",
        )

    def test_a_zero_share_opens_no_rent_at_all(self) -> None:
        """`0.0` — это «оброк не заводится», а не «нулевой счёт»."""
        self.assertEqual(_bill(0.0), 0.0)
        world = load_scenario(STAND, seed=SEED)
        for entry in world.script:
            if int(entry.get("at_month", 0)) == 1:
                _apply_script_entry(world, entry)
        grant_tenure(world, HOUSEHOLD, TILE, kind="grazing", rent_share=0.0)
        self.assertNotIn(
            f"obl_{HOUSEHOLD}_rent", world.obligations,
            "при ставке 0.0 заведена оброчная повинность: 0.0 значит «не платит», "
            "а не «платит ноль» (ADR 0206)",
        )

    def test_a_rate_outside_the_share_is_refused(self) -> None:
        """Ставка — доля, то есть число из [0, 1]; за его пределами счёт недостижим.

        Мутация, которая обязана ронять тест: убрать проверку диапазона в
        `grant_tenure`/`rent_rate_for` — `1.5` прошла бы как обычная ставка.
        """
        for share in (1.5, -0.1):
            with self.subTest(share=share):
                world = load_scenario(STAND, seed=SEED)
                with self.assertRaises(ValueError):
                    grant_tenure(world, HOUSEHOLD, TILE, kind="grazing", rent_share=share)


class TestTheRentShareOrderTakesNoNumber(unittest.TestCase):
    """Сумма оброка — производная, поэтому приказ её не задаёт (ADR 0206)."""

    def _harvested_world(self, months: int = 6) -> object:
        """Мир, в котором двор уже что-то собрал: счёт оброка ненулевой."""
        world = load_scenario(STAND, seed=SEED)
        _scripted_months(world, months)
        return world

    def test_the_rent_order_refuses_a_number(self) -> None:
        """Главное: `due_amount=100` — отказ, а не принятое и выброшенное число.

        Замер Стола плейтеста: 0 / 1 / 5 / 20 / 100 давали одинаковый итог, потому
        что `refresh_rent_due` перезаписывает сумму (ADR 0185). Приказ, который
        принимает число и через месяц платит другое, лжёт ровно так же, как лгал
        `rent_share` в ADR 0204.

        Мутация, которая обязана ронять тест: убрать `raise ValueError` в
        `add_obligation` для вида «рента-доля».
        """
        world = self._harvested_world()
        before = set(world.obligations)
        with self.assertRaises(ValueError) as caught:
            add_obligation(world, HOUSEHOLD, "rent", "grain", 100.0, right_id=None)
        self.assertIn("grant_tenure", str(caught.exception))
        self.assertIn("100", str(caught.exception))
        self.assertEqual(
            set(world.obligations) - before, set(),
            "отказ приказа оставил повинность в книге: закон отверг, а мир — нет",
        )
        self.assertEqual(
            [oid for oid in world.households[HOUSEHOLD].obligation_ids
             if oid not in before],
            [],
            "отказ приказа записал повинность в книгу двора",
        )

    def test_the_rent_order_without_a_number_opens_the_derived_obligation(self) -> None:
        """Сумма выводится сама: ставка права × урожай прошлого месяца."""
        world = self._harvested_world()
        for entry in world.script:
            if int(entry.get("at_month", 0)) == 7:
                _apply_script_entry(world, entry)
        right = grant_tenure(world, HOUSEHOLD, TILE, kind="grazing", rent_share=0.9)
        household = world.households[HOUSEHOLD]
        obligation = add_obligation(
            world, HOUSEHOLD, "rent", "grain", None, right_id=right.id
        )
        this_year, this_month = world.clock.date.year, world.clock.date.month
        previous = (
            (this_year, this_month - 1) if this_month > 1
            else (this_year - 1, world.clock.months_per_year)
        )
        base = _harvest_of(world, household.stock_id, *previous)
        self.assertGreater(base, 0.0, "подготовка не удалась: урожай прошлого месяца пуст")
        self.assertAlmostEqual(
            obligation.due_amount, round(0.9 * base, 3), places=3,
            msg="приказ без числа не вывел счёт из ставки права и урожая",
        )
        self.assertGreater(obligation.due_amount, 0.0, "оброк заведён, но счёт нулевой")

    def test_an_order_that_takes_no_number_still_takes_its_own(self) -> None:
        """Сторож: отказ относится к оброку, а не ко всем видам подряд.

        Мутация, которая обязана ронять тест: отвергать `due_amount` у ЛЮБОГО вида
        (например, проверкой без `kind == RENT_KIND`) — фиксированный мешок и
        барщина перестали бы принимать собственную сумму.
        """
        world = self._harvested_world()
        fixed = add_obligation(world, HOUSEHOLD, "fixed_rent", "grain", 0.5)
        self.assertAlmostEqual(fixed.due_amount, 0.5, places=9)
        duty = add_obligation(world, HOUSEHOLD, "labor_duty", None, 8.0, basis="duty")
        self.assertAlmostEqual(duty.due_amount, 8.0, places=9)

    def test_a_non_rent_order_without_a_number_is_refused(self) -> None:
        """Вид, у которого сумма и есть долг, без неё не обходится.

        Мутация, которая обязана ронять тест: подставить `due_amount = 0.0` вместо
        отказа — появилась бы повинность, за которую платить нечем (ADR 0131).
        """
        world = self._harvested_world()
        with self.assertRaises(ValueError):
            add_obligation(world, HOUSEHOLD, "fixed_rent", "grain", None)
        self.assertNotIn(f"obl_{HOUSEHOLD}_fixed_rent", world.obligations)

    def test_the_tenure_order_still_opens_the_obligation_and_logs_it(self) -> None:
        """Сторож: внутренний путь не исчез вместе с публичным приказом.

        Выдача надела обязана по-прежнему заводить оброчную повинность И писать
        запись в `player_actions` — иначе игрок не увидит повинность, которая
        возникла из его приказа (ADR 0196, ADR 0202).

        Мутация, которая обязана ронять тест: убрать вызов `_open_rent_obligation`
        из `grant_tenure` либо писать в лог другим именем приказа.
        """
        world = load_scenario(STAND, seed=SEED)
        for entry in world.script:
            if int(entry.get("at_month", 0)) == 1:
                _apply_script_entry(world, entry)
        before = len(world.player_actions)
        grant_tenure(world, HOUSEHOLD, TILE, kind="grazing", rent_share=0.9)
        self.assertIn(f"obl_{HOUSEHOLD}_rent", world.obligations)
        logged = [
            record for record in world.player_actions[before:]
            if record.get("action") == "add_obligation"
        ]
        self.assertEqual(len(logged), 1, "выдача надела не записала оброк в лог приказов")
        self.assertEqual(logged[0]["obligation"], f"obl_{HOUSEHOLD}_rent")
        self.assertEqual(logged[0]["household"], HOUSEHOLD)


class TestTheLeversStayDeterministic(unittest.TestCase):
    """И-6: одна ставка и один сид — один мир; ставка — тоже часть детерминизма."""

    def _hash(self, seed: int, share: float) -> str:
        result = run(
            STAND,
            MONTHS,
            seed,
            actions=[
                {
                    "at_month": 1, "action": "grant_tenure",
                    "household": HOUSEHOLD, "tile": TILE,
                    "kind": "grazing", "rent_share": share,
                }
            ],
        )
        return result.state_hash

    def test_the_same_seed_and_share_give_the_same_world(self) -> None:
        self.assertEqual(self._hash(SEED, 0.9), self._hash(SEED, 0.9))

    def test_another_seed_gives_another_world(self) -> None:
        self.assertNotEqual(self._hash(SEED, 0.9), self._hash(7, 0.9))

    def test_another_share_gives_another_world(self) -> None:
        self.assertNotEqual(
            self._hash(SEED, 0.9), self._hash(SEED, 0.1),
            "ставка 0.9 и 0.1 дали один `state_hash`: рычаг не в силе (ADR 0206)",
        )


class TestTheDebtRegistryIsClosed(unittest.TestCase):
    """Сторож реестра долга ADR 0203: запись о `legal/actions.py` снята."""

    def test_no_module_outside_hexgrid_parses_a_tile_id_itself(self) -> None:
        """Формат id клетки разбирает один модуль; иначе вернётся вторая правда.

        Мутация, которая обязана ронять тест: вернуть локальный `id.split("_")` в
        любой другой модуль `sim/src` — тест упадёт на новом файле.
        """
        import re

        pattern = re.compile(r"\.split\(\s*[\"']_[\"']\s*\)")
        offenders: list[str] = []
        for path in sorted((ROOT / "sim" / "src").rglob("*.py")):
            if path.name == "hexgrid.py":
                continue
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(ROOT)}:{number}")
        self.assertEqual(
            offenders, [],
            "id клетки разбирается вне hexgrid: " + "; ".join(offenders) + " (ADR 0203)",
        )

    def test_legal_does_not_keep_its_own_neighbour_question(self) -> None:
        """`legal.actions` спрашивает `hexgrid`, а не держит свою копию вопроса."""
        source = legal_actions.__file__
        self.assertIn("hexgrid", (ROOT / "sim" / "src" / Path(source).relative_to(
            ROOT / "sim" / "src")).read_text(encoding="utf-8"),
            "`legal/actions.py` не импортирует hexgrid: правка ADR 0203 §4 откатилась")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
