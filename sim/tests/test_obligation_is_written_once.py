"""Обвинитель дубля повинности (ADR 0210).

Дубль — это не «лишняя запись», а двойное взыскание: `engine/tick.py`
обходит **список** `household.obligation_ids`, поэтому одна и та же запись,
значащаяся дважды, применяется дважды в месяц. На `v0_barony_100` сид 4242
замер давал 20 дворов / 22 дубля, `corvee_days` ровно 2.000× и
`arrears = 1.200` при `due = 0.6`.

Починка — не «убрать второй вызов» (вызов в `scenario.py` — зона Implementer), а
**идемпотентность**: `legal.obligations.register_obligation` — единственное
место в `legal/`, где id попадает в список, и оно не может добавить его дважды.
Тест сторожит не вызов, а **инвариант мира**: ни один двор в любой момент
жизни не имеет одного id дважды.

Каждая проверка ниже — обвинение, а не описание. Мутации, которые обязаны
ронять тест, названы у каждой проверки; они проверены прогоном, а не
утверждены на словах (см. ADR 0210 §Проверка).
"""

from __future__ import annotations

import inspect
import unittest
from collections import Counter
from pathlib import Path

from hillcourt.engine.tick import phase_obligations, run_month
from hillcourt.legal.actions import add_obligation, grant_tenure
from hillcourt.legal.bundles import materialize_obligations
from hillcourt.legal.obligations import register_obligation
from hillcourt.legal.service import create_muster_obligation
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
BARONY = ROOT / "design" / "scenarios" / "v0_barony_100.yml"
SEED = 4242


def _dupes(world) -> dict[str, list[str]]:
    """Дворы, у которых один id повинности значится в списке больше раза."""
    out: dict[str, list[str]] = {}
    for hid in sorted(world.households):
        ids = world.households[hid].obligation_ids
        repeated = sorted(oid for oid, n in Counter(ids).items() if n > 1)
        if repeated:
            out[hid] = repeated
    return out


def _load():
    return load_scenario(BARONY, seed=SEED)


class TestTheBookOfObligationsHoldsOneRowPerDuty(unittest.TestCase):
    """Инвариант: в книге двора одна строка на одну повинность."""

    def test_a_loaded_barony_has_no_duplicated_duty(self) -> None:
        """Замер, который нашёл дубль: 20 дворов / 22 дубля → 0 / 0.

        Мутация, которая обязана ронять тест: вернуть безусловный
        `household.obligation_ids.append(...)` в `bundles.py::_duty`/`_rent`.
        Тогда `scenario.py` (второй вызов `materialize_obligations`) снова
        продублирует список, и краснет именно этот тест.
        """
        world = _load()
        self.assertEqual(
            _dupes(world), {},
            "в загруженной баронсии двор знает одну повинность дважды",
        )
        self.assertGreater(
            sum(len(hh.obligation_ids) for hh in world.households.values()), 0,
            "подготовка не удалась: у дворов вообще нет повинностей",
        )

    def test_materializing_twice_changes_nothing(self) -> None:
        """Повторная материализация — идемпотентна по закону, а не по удаче.

        Мутация, которая обязана ронять тест: снять проверку
        `if obligation.id not in ids` в `register_obligation`.
        """
        world = _load()
        before = {hid: list(hh.obligation_ids) for hid, hh in world.households.items()}
        for hid in sorted(world.households):
            materialize_obligations(world, world.households[hid])
        after = {hid: list(hh.obligation_ids) for hid, hh in world.households.items()}
        self.assertEqual(before, after, "повторный materialize изменил книгу двора")
        self.assertEqual(_dupes(world), {})

    def test_the_registrar_is_the_only_writer_of_the_list(self) -> None:
        """Сторож на сам закон, а не на его сегодняшние экземпляры.

        Мутация, которая обязана ронять тест: добавить новый
        `obligation_ids.append` в любом модуле `legal/` мимо
        `register_obligation` — дубль вернётся, а тест выше может и не заметить
        (если новый путь ещё не вызывается при загрузке). Этот тест смотрит на
        **исходники зоны** и требует, чтобы прямая запись в список жила ровно в
        одном месте.
        """
        legal = ROOT / "sim" / "src" / "hillcourt" / "legal"
        offenders: list[str] = []
        for path in sorted(legal.glob("*.py")):
            if path.name == "obligations.py":
                continue  # сам регистратор
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if "obligation_ids.append" in line:
                    offenders.append(f"{path.name}:{number}")
        self.assertEqual(
            offenders, [],
            "прямая запись в obligation_ids вне register_obligation "
            "(обязана быть только в legal/obligations.py): " + ", ".join(offenders),
        )

    def test_the_registrar_carries_no_second_append(self) -> None:
        """Сам регистратор не чинит дубль, а не даёт ему возникнуть.

        Мутация, которая обязана ронять тест: заменить тело
        `register_obligation` на безусловный `ids.append(...)` — тест прочитает
        исходник и увидит, что проверки единственности нет.
        """
        source = inspect.getsource(register_obligation)
        self.assertIn(
            "not in ids", source,
            "регистратор добавляет id без проверки единственности",
        )
        self.assertIn(
            "dict.fromkeys", source,
            "регистратор не вылечивает дубль, уже висящий в списке",
        )


class TestADuplicatedDutyIsDoubleCollection(unittest.TestCase):
    """Дубль не косметика: он удваивает и труд, и недоимку."""

    def test_a_duplicated_duty_is_applied_twice_by_the_phase(self) -> None:
        """Фаза повинностей обходит список, поэтому дубль платит дважды.

        Тест строит дубль **вручную** и меряет эффект. Если бы `phase_obligations`
        перестала обходить список или начала дедуплицировать (то есть
        `engine/` починил дубль у себя), тест бы упал не по существу, а по
        причине «ф��за перестала считать» — поэтому проверка dual-эффекта
        делается сравнением **с и без** дубля на одном и том же мире.
        """
        world = _load()
        household = world.households["hh_hill_sokeman_001"]
        rent_id = next(
            oid for oid in household.obligation_ids
            if world.obligations[oid].kind == "rent"
        )
        base = world.ledger.delta(world.total_matter())

        phase_obligations(world)
        single = world.obligations[rent_id].arrears

        household.obligation_ids.append(rent_id)  # дубль руками
        phase_obligations(world)
        doubled = world.obligations[rent_id].arrears

        self.assertAlmostEqual(
            doubled - single, single, places=6,
            msg="дубль не удвоил недоимку: фаза применяет повинность один раз",
        )
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()) - base, 0.0, places=6,
            msg="применение повинности двинуло материю (И-1)",
        )

    def test_a_duplicated_labor_duty_is_worked_twice(self) -> None:
        """Кратность барщины обязана быть ровно 1.0, а не 2.0.

        `corvee_days` — **накопительный** счётчик (ADR 0149: оплата и отработка
        пишут в одно поле), поэтому кратность измеряется не «итог против
        месяца», а приростом за месяц: прирост обязан равняться `duty_days`
        этого месяца ровно один раз. Иначе сравнение итога с месячным долгом
        ловило бы не дубль, а саму накопительность счётчика.

        Мутация, которая обязана ронять тест: вернуть безусловный `append` —
        прирост станет 2 × `duty_days`.
        """
        world = _load()
        seen: dict[str, float] = {}
        months_with_duty = 0
        for _ in range(6):  # месяц 6 — callout для geneat (см. ADR 0154)
            run_month(world)
            self.assertEqual(_dupes(world), {}, "в шестой месяц дубль вернулся")
            for hid in sorted(world.households):
                for oid in world.households[hid].obligation_ids:
                    obligation = world.obligations.get(oid)
                    if obligation is None or obligation.kind != "labor_duty":
                        continue
                    if obligation.duty_days <= 0.0:
                        continue
                    months_with_duty += 1
                    growth = obligation.corvee_days - seen.get(oid, 0.0)
                    self.assertAlmostEqual(
                        growth, obligation.duty_days, places=6,
                        msg=(
                            f"{hid}/{oid}: за месяц начислено {growth} при "
                            f"долге {obligation.duty_days} — дубль или двойной учёт"
                        ),
                    )
                    seen[oid] = obligation.corvee_days
        self.assertGreater(months_with_duty, 0, "подготовка не удалась: барщины не было")


class TestTheDebtSurvivesTheBookReopening(unittest.TestCase):
    """Повторная регистрация не прощает долг и не воскрешает отменённый счёт."""

    def test_reregistering_keeps_the_debt_and_takes_the_new_bill(self) -> None:
        """Закон: счёт следует закону, история — факт.

        Мутация, которая обязана ронять тест: заменить тело `register_obligation`
        на «создать заново» (`world.obligations[id] = obligation` без проверки) —
        `paid_total`/`arrears` обнулятся, то есть правка YAML простит долг.
        """
        from hillcourt.ontology import Obligation

        world = _load()
        household = world.households["hh_hill_sokeman_001"]
        rent_id = next(
            oid for oid in household.obligation_ids
            if world.obligations[oid].kind == "rent"
        )
        living = world.obligations[rent_id]
        living.paid_total = 7.5
        living.arrears = 3.25

        fresh = Obligation(
            id=rent_id,
            household_id=household.id,
            kind="rent",
            due_good="grain",
            due_amount=99.0,  # новый закон: другой счёт
            period_months=1,
            paid_total=0.0,
            arrears=0.0,
            basis="fixed",
        )
        again = register_obligation(world, household, fresh)

        self.assertIs(again, living, "регистрация пересоздала повинность вместо неё")
        self.assertAlmostEqual(again.paid_total, 7.5, places=6, msg="правка закона простила выплаты")
        self.assertAlmostEqual(again.arrears, 3.25, places=6, msg="правка закона простила недоимку")
        self.assertAlmostEqual(
            again.due_amount, 99.0, places=6,
            msg="счёт не последовал за новым законом (отменённый закон не висит)",
        )
        self.assertEqual(household.obligation_ids.count(rent_id), 1)

    def test_a_healed_list_keeps_the_first_occurrence_order(self) -> None:
        """Выздоровление списка не переставляет повинности (И-6).

        Проверка идёт через `materialize_obligations`, а не через прямую
        регистрацию: дубль появляется в списке **в обход** регистратора (из
        чужой зоны или из старого мира на диске), и лечить его обязана обычная
        материализация, а не только явный вызов.
        """
        world = _load()
        household = world.households["hh_hill_sokeman_001"]
        ids = list(household.obligation_ids)
        household.obligation_ids = ids + [ids[0]]  # дубль руками
        materialize_obligations(world, household)
        self.assertEqual(
            household.obligation_ids, ids,
            "выздоровление списка изменило порядок повинностей двора (И-6)",
        )
        self.assertEqual(_dupes(world), {})


class TestEveryLivePathIsIdempotent(unittest.TestCase):
    """Каждый путь, который заводит повинность, обязан быть идемпотентным."""

    def test_a_repeated_tenure_order_does_not_double_the_duty(self) -> None:
        """Повторный `grant_tenure` в живом мире (ADR 0210 §Закон 1).

        Мутация, которая обязана ронять тест: снять вызов `register_obligation`
        в `actions.py::_register_obligation` и вернуть безусловный `append`.
        """
        world = _load()
        household_id = "hh_hill_sokeman_001"
        tile_id = next(
            tid for rid, right in sorted(world.rights.items())
            if right.holder_household_id == household_id
            for tid in [right.tile_id]
        )
        before = len(world.households[household_id].obligation_ids)
        grant_tenure(world, household_id, tile_id, kind="tenure", rent_share=0.1)
        grant_tenure(world, household_id, tile_id, kind="tenure", rent_share=0.1)
        after = list(world.households[household_id].obligation_ids)
        self.assertEqual(
            len(after), len(set(after)),
            "повторный приказ grant_tenure продублировал повинность",
        )
        self.assertEqual(_dupes(world), {})

    def test_a_repeated_order_does_not_double_the_duty(self) -> None:
        """Второй путь к тому же дублю: два приказа `add_obligation` одного вида.

        Этот путь не проходил через `scenario.py` и был найден отдельно: id
        `obl_{двор}_{вид}` детерминирован, поэтому второй приказ того же вида
        перетирал запись в `world.obligations` и **дописывал** её в список.

        Мутация, которая обязана ронять тест: вернуть безусловный `append` в
        `actions.py::_register_obligation`.
        """
        world = _load()
        household_id = "hh_hill_sokeman_001"
        first = add_obligation(world, household_id, "fixed_rent", "grain", 0.5)
        second = add_obligation(world, household_id, "fixed_rent", "grain", 0.5)
        self.assertIs(
            first, second,
            "второй приказ создал вторую запись повинности",
        )
        ids = world.households[household_id].obligation_ids
        self.assertEqual(ids.count(first.id), 1, "приказ продублировал повинность в списке")
        self.assertEqual(_dupes(world), {})

    def test_a_repeated_muster_order_does_not_double_the_duty(self) -> None:
        """Третий путь: `create_muster_obligation` тоже звал `append` безусловно.

        У вызова **своя** нумерация (`muster_{n}` считается по книге), поэтому
        два вызова — это законные две разные повинности, и обвинять их в дубле
        нельзя. Обвинение здесь одно: сколько бы вызовов ни прошло, ни один id
        не должен значиться в списке дважды.

        **Честное ограничение этого обвинения.** Безусловный `append` в
        `service.py` сам по себе дубля не родит: идентификатор вызова
        считается по книге и потому уникален. Убивает его
        `test_the_registrar_is_the_only_writer_of_the_list` — сторож на
        **единственность места записи**, а не на сегодняшнее поведение. Если
        нумерация когда-нибудь станет неуникальной, этот путь не защищён
        числовым обвинением, и это видно по таблице мутаций в ADR 0210, а не
        спрятано.
        """
        world = _load()
        household_id = "hh_hill_sokeman_001"
        opened = [
            create_muster_obligation(world, household_id, 2 + n) for n in range(4)
        ]
        ids = world.households[household_id].obligation_ids
        self.assertEqual(len(ids), len(set(ids)), "вызовы вызова продублировали запись")
        self.assertEqual(_dupes(world), {})
        self.assertTrue(
            all(obligation.id in ids for obligation in opened),
            "заведённый вызов не попал в книгу двора",
        )

    def test_matter_does_not_move_when_the_book_is_opened_twice(self) -> None:
        """И-1: регистрация повинности не двигает материю.

        Проверка на загрузке баронсии (150 дворов, 133 повинности): второй
        проход материализации не меняет ни сумму, ни `state_hash`.
        """
        world = _load()
        before_matter = world.total_matter()
        before_delta = world.ledger.delta(before_matter)
        before_hash = world.state_hash()
        for hid in sorted(world.households):
            materialize_obligations(world, world.households[hid])
        self.assertAlmostEqual(world.total_matter(), before_matter, places=6)
        self.assertAlmostEqual(
            world.ledger.delta(world.total_matter()), before_delta, places=6
        )
        self.assertEqual(
            world.state_hash(), before_hash,
            "повторная материализация изменила состояние мира (И-6)",
        )

    def test_one_seed_gives_one_hash_and_another_seed_another(self) -> None:
        """И-6 после починки."""
        first = _load()
        second = _load()
        other = load_scenario(BARONY, seed=1729)
        self.assertEqual(first.state_hash(), second.state_hash())
        self.assertNotEqual(first.state_hash(), other.state_hash())


if __name__ == "__main__":
    unittest.main()
