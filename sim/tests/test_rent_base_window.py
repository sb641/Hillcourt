"""ЗАКОН (ADR 0192 п. 1): оброк — доля урожая ПРОШЕГО месяца, а не архива.

Дефект, который этот тест запирает. `_harvest_grain` имел только ВЕРХНЮЮ границу
(`before_current_month`) и не имел нижней, поэтому база начисления была суммой
всех проводок `harvest_grain` за всю жизнь двора. `refresh_rent_due` перевыводит
`due_amount` из этой базы каждый месяц, то есть лорд ежемесячно выставлял 10 % от
всего, что двор вырастил когда-либо. Замер `start_stand`, 24 месяца, сид 1729,
доля 0.1: выращено 861.500, выставлено счетов 760.966 — **88.3 % урожая** вместо
объявленных 10 %; уплачено 291.465, недоимка росла монотонно 0.000 → 385.218,
и 78.5 % всего зерна ушло в ренту. Ширина окна 1 даёт 9.9 %, и это единственное
чтение, при котором оброк остаётся долей: счёт пропорционален ширине окна, а не
доле урожая.

Закон проверяется тремя числами, и все три падающие:
1. счёт месяца = `default_share × урожай ПРОШЕГО месяца` — ровно, до копейки;
2. счёт месяца ≠ `default_share × урожай за всю жизнь` (иначе это аннуитет);
3. месяц, в котором прошлый месяц ничего не собрали, даёт счёт 0.000 при полном
   амбаре — старый урожай не облагается дважды.

Проверка обязана падать без механики: `test_removing_the_window_makes_the_law_red`
снимает окно (подменяет `RENT_BASE_WINDOW_MONTHS` на «без нижней границы») и
требует, чтобы те же проверки покраснели. Зелёный тест без этого — закон, который
не в силе.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.engine.tick import run_month
from hillcourt.legal import obligations as rent_law
from hillcourt.legal.obligations import (
    RENT_BASE_WINDOW_MONTHS,
    household_harvest_total,
    household_rent_due_base,
    refresh_rent_due,
    rent_amount_for,
)
from hillcourt.runner import _apply_script_entry
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "start_stand.yml"
SEED = 1729
MONTHS = 24
RENT_REASON = "harvest_grain"


def _run_scripted(world, months: int):
    for month in range(1, months + 1):
        for entry in world.script:
            if int(entry.get("at_month", 0)) == month:
                _apply_script_entry(world, entry)
        run_month(world)
    return world


def _world():
    return load_scenario(SCENARIO, seed=SEED)


def _harvest_of(world, stock_id: str, year: int, month: int) -> float:
    """Урожай стока за ОДИН месяц: проводки `harvest_grain` в его амбар."""
    return sum(
        entry.amount
        for entry in world.ledger.entries
        if entry.reason == RENT_REASON
        and entry.kind == "process"
        and entry.good == "grain"
        and entry.dst_id == stock_id
        and entry.date.year == year
        and entry.date.month == month
    )


def _previous_month(world):
    """Прошлый месяц относительно часов мира: `(год, месяц)`.

    Во время тика часы стоят на обрабатываемом месяце, а `advance_month` делает
    последняя фаза, поэтому «прошлый» — это `date - 1`. Проверка читает мир ПОСЛЕ
    тика, то есть часы уже сдвинуты на месяц вперёд, и прошлым оказывается месяц,
    за который выставлялся счёт.
    """
    months_per_year = world.clock.months_per_year
    this_month = world.clock.date.year * months_per_year + (world.clock.date.month - 1)
    year, month_index = divmod(this_month - 1, months_per_year)
    return year, month_index + 1


def _shift_month(year: int, month: int, delta: int, months_per_year: int) -> tuple[int, int]:
    """Сдвиг календарного месяца на `delta`: `(1, 12) - 1 → (0, 12)`."""
    year, month_index = divmod(year * months_per_year + (month - 1) + delta, months_per_year)
    return year, month_index + 1


def _rentable(world):
    """Дворы с живой оброчной повинностью: они и есть налогоплательщики."""
    out = []
    for hid in sorted(world.households):
        household = world.households[hid]
        if household.left_at is not None:
            continue
        for oid in household.obligation_ids:
            obligation = world.obligations.get(oid)
            if obligation is not None and rent_law.is_rent_share(obligation):
                out.append((household, obligation))
                break
    return out


class TestRentBaseWindow(unittest.TestCase):
    """Оброк бьётся по урожаю прошлого месяца, а не по архиву урожая."""

    def _assert_window_is_the_law(self, world) -> tuple[float, float]:
        """Проверить закон на текущем месяце мира; вернуть (окно, архив)."""
        template = rent_law.rent_template(world)
        share = template.default_share
        self.assertIsNotNone(share, "У оброка не объявлена доля (ADR 0183)")
        year, month = _previous_month(world)
        window_total = archive_total = 0.0
        checked = 0
        for household, obligation in _rentable(world):
            window = _harvest_of(world, household.stock_id, year, month)
            archive = household_harvest_total(world, household)
            due = refresh_rent_due(world, household, obligation)
            self.assertAlmostEqual(
                window, household_rent_due_base(world, household), places=6,
                msg=(
                    f"{household.id}: база {household_rent_due_base(world, household)} "
                    f"не равна урожаю {year}-{month:02d} ({window}). Окно поехало"
                ),
            )
            self.assertAlmostEqual(
                due, rent_amount_for(template, window), places=6,
                msg=(
                    f"{household.id}: счёт {due} не равен доле {share} от урожая "
                    f"прошлого месяца {window}"
                ),
            )
            if archive > window * 1.05 + 0.01:
                # Проверка различает долю и аннуитет: счёт не имеет права расти
                # вместе с архивом. Если бы база была безоконной, счёт стоял бы
                # на `share × archive` и это равенство было бы провалено.
                # Сравнение имеет смысл, только когда архив заведомо шире окна:
                # при равных урожаях `share × window` и `share × archive` совпадут
                # и проверка сказала бы «оброк сломан» там, где он честен.
                self.assertNotAlmostEqual(
                    due, rent_amount_for(template, archive), places=6,
                    msg=(
                        f"{household.id}: счёт {due} равен доле от архива "
                        f"({archive}). Оброк считается от урожая ПРОШЛОГО месяца, "
                        f"а не от всего, что двор вырастил когда-либо (ADR 0192)"
                    ),
                )
            window_total += window
            archive_total += archive
            checked += 1
        self.assertGreater(checked, 0, "В стенде не осталось оброчных дворов")
        return window_total, archive_total

    def test_bill_equals_share_of_previous_month_harvest(self) -> None:
        """ЗАКОН: счёт месяца = доля от урожая ровно прошлого месяца."""
        world = _run_scripted(_world(), MONTHS)
        self._assert_window_is_the_law(world)

    def test_bill_ignores_every_month_before_the_window(self) -> None:
        """ЗАКОН: урожай месяца вне окна не облагается снова.

        Находится двор и месяц, в котором этот двор ничего не собрал, но у него
        есть архив урожая. Счёт обязан быть 0.000: старый урожай уже обложен один
        раз, и месяц без жатвы не имеет права перекладывать его заново. Проверка
        идёт по каждому двору отдельно — иначе месяц, где хоть кто-то собрал,
        скрыл бы двор, который не собрал ничего.
        """
        world = _world()
        template = rent_law.rent_template(world)
        share = float(template.default_share)
        caught = False
        for month in range(1, MONTHS + 1):
            for entry in world.script:
                if int(entry.get("at_month", 0)) == month:
                    _apply_script_entry(world, entry)
            this_year, this_month = world.clock.date.year, world.clock.date.month
            run_month(world)
            previous = _shift_month(
                this_year, this_month, -1, world.clock.months_per_year
            )
            for household, obligation in _rentable(world):
                archive = household_harvest_total(world, household)
                window = _harvest_of(world, household.stock_id, *previous)
                if window > 1e-6 or archive < 1e-6:
                    continue
                caught = True
                self.assertAlmostEqual(
                    obligation.due_amount, 0.0, places=6,
                    msg=(
                        f"{household.id} в {this_year}-{this_month:02d}: прошлый "
                        f"месяц {previous[0]}-{previous[1]:02d} пуст, а счёт "
                        f"{obligation.due_amount} облагает архив {archive}. Старый "
                        f"урожай не обкладывается дважды (ADR 0192)"
                    ),
                )
                self.assertNotAlmostEqual(
                    obligation.due_amount, rent_amount_for(template, archive), places=6,
                    msg=(
                        f"{household.id} в {this_year}-{this_month:02d}: счёт равен "
                        f"доле от архива {archive} — база вышла за окно"
                    ),
                )
        self.assertTrue(
            caught,
            "За 24 месяца не нашлось двора, который в месяц без жатвы имел архив — "
            "проверка пустого счёта не в силе",
        )

    def test_bill_never_charges_the_month_being_processed(self) -> None:
        """ЗАКОН, отдельной строкой: нельзя брать оброк с недомолотого.

        ADR 0192 п. 1 запрещает оброк с зерна текущего месяца. Проверка идёт по
        тику: в месяце, где двор собрал урожай и жатва пришла в амбар, счёт
        обязан равняться доле от урожая ПРЕДЫДУЩЕГО месяца и НЕ равняться доле от
        урожая этого месяца. Счёт читается **как тик его оставил**
        (`obligation.due_amount`), а не пересчитывается вручную: пересчёт после
        тика сдвинул бы окно на месяц вперёд и проверил бы не тот месяц.

        Эта проверка ловит сползание окна на текущий месяц — ровно тот off-by-one,
        который даёт «оброк с недомолотого» и снаружи выглядит как «просто
        сдвинули на месяц».
        """
        world = _world()
        template = rent_law.rent_template(world)
        share = float(template.default_share)
        caught = False
        for month in range(1, MONTHS + 1):
            for entry in world.script:
                if int(entry.get("at_month", 0)) == month:
                    _apply_script_entry(world, entry)
            this_year, this_month = world.clock.date.year, world.clock.date.month
            run_month(world)
            previous = _shift_month(
                this_year, this_month, -1, world.clock.months_per_year
            )
            for household, obligation in _rentable(world):
                harvested_now = _harvest_of(
                    world, household.stock_id, this_year, this_month
                )
                if harvested_now <= 1e-6:
                    continue
                caught = True
                harvested_before = _harvest_of(world, household.stock_id, *previous)
                due = obligation.due_amount
                self.assertAlmostEqual(
                    due, rent_amount_for(template, harvested_before), places=6,
                    msg=(
                        f"{household.id} в {this_year}-{this_month:02d}: счёт {due} "
                        f"не равен доле {share} от урожая прошлого месяца "
                        f"{harvested_before}"
                    ),
                )
                if harvested_now * 1.05 + 0.01 < harvested_before:
                    # Окна различаются заметно, значит «не равно» здесь — закон, а
                    # не совпадение двух похожих чисел. При равных урожаях доля от
                    # текущего месяца совпала бы с долей от прошлого, и запрет
                    # на оброк с недомолотого этой проверкой не читается вовсе.
                    caught = True
                    self.assertNotAlmostEqual(
                        due, rent_amount_for(template, harvested_now), places=6,
                        msg=(
                            f"{household.id} в {this_year}-{this_month:02d}: счёт "
                            f"{due} обложил урожай ТЕКУЩЕГО месяца "
                            f"({harvested_now}). Оброк с недомолотого брать нельзя "
                            f"(ADR 0192)"
                        ),
                    )
        self.assertTrue(
            caught,
            "За 24 месяца ни один двор не собрал урожай в обрабатываемом месяце — "
            "проверка лага вхолостую",
        )

    def test_whole_run_bills_the_share_once_and_not_more(self) -> None:
        """ЗАКОН, выращенный суммой: выставлено ровно 10 % урожая, попавшего в окно.

        Это проверка, которую игрок видит как «барон забрал десятую часть», а не
        как форму. Считается по проводкам, как их видит бухгалтерия: счёт месяца
        есть доля от окна, значит сумма счетов равна доле от всего урожая, который
        успел войти в окно. Урожай последнего месяца прогона в окно ещё не входил
        (счёт за него будет в следующем месяце) и из ожидания исключён — иначе
        проверка была бы неточной на величину одного месяца, а не законом.
        """
        world = _run_scripted(_world(), MONTHS)
        template = rent_law.rent_template(world)
        share = float(template.default_share)
        final_year, final_month = _previous_month(world)
        stock_ids = {household.stock_id for household, _ in _rentable(world)}
        harvest = sum(
            entry.amount
            for entry in world.ledger.entries
            if entry.reason == RENT_REASON
            and entry.kind == "process"
            and entry.good == "grain"
            and entry.dst_id in stock_ids
            and (entry.date.year, entry.date.month) != (final_year, final_month)
        )
        world2 = _world()
        billed = 0.0
        for month in range(1, MONTHS + 1):
            for entry in world2.script:
                if int(entry.get("at_month", 0)) == month:
                    _apply_script_entry(world2, entry)
            run_month(world2)
            billed += sum(
                obligation.due_amount for _, obligation in _rentable(world2)
            )
        self.assertGreater(harvest, 0.0, "Стенд не собрал урожая — проверка вхолостую")
        self.assertAlmostEqual(
            billed, round(share * harvest, 3), delta=0.5,
            msg=(
                f"выставлено {billed} против доли {share * harvest} от урожая "
                f"{harvest}. Оброк облагает урожай не один раз"
            ),
        )

    def test_removing_the_window_makes_the_law_red(self) -> None:
        """ПРОВЕРКА В СИЛЕ: снял окно — те же проверки краснеют.

        Мутация ставит окно «без нижней границы» на время прогона и требует, чтобы
        проверки закона упали. Если после снятия окна проверки остались зелёными,
        закон держится на чужой функции и в силе не находится.
        """
        world = _run_scripted(_world(), MONTHS)
        with self.subTest("до снятия окна закон зелёный"):
            self._assert_window_is_the_law(world)
        saved = rent_law.RENT_BASE_WINDOW_MONTHS
        try:
            rent_law.RENT_BASE_WINDOW_MONTHS = None
            with self.subTest("окно снято — проверка обязана краснеть"):
                with self.assertRaises(AssertionError):
                    self._assert_window_is_the_law(world)
        finally:
            rent_law.RENT_BASE_WINDOW_MONTHS = saved
        with self.subTest("окно вернулось — закон снова зелёный"):
            self._assert_window_is_the_law(world)

    def test_window_constant_is_a_month_not_the_archive(self) -> None:
        """ЗАКОН, записанный числом: окно — месяц, а не «всё до текущего месяца»."""
        self.assertEqual(
            RENT_BASE_WINDOW_MONTHS, 1,
            msg=(
                "Окно базы оброка перестало быть прошлым месяцем. При окне шире "
                "каждый старый урожай облагается снова, и оброк перестаёт быть долей"
            ),
        )


if __name__ == "__main__":
    unittest.main()
