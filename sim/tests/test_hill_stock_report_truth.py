"""Весть «Холм» называет то же, что читает (ADR 0205).

Находка плейтеста: единственный канал, которым игра говорит игроку о ЕГО
СОБСТВЕННОМ запасе, врал с абсолютной достоверностью. `info/briefing.py` брал
зерно из стока `world.player.household_id` — личный ларёк лорда — а текст
обещал «Холм: зерна во дворе». Замер `start_stand`, 24 мес, сид 1729: весть
говорила «примерно 47 / 17 / 8 / 0», тогда как книга корня стояла на
565.1 / 577.0 / 824.1 / 849.2, а личный ларёк лорда действительно 47.2 / 17.5
/ 8.4 / 0.0. То есть весть честно сообщала о ларьке, и игрок читал это как
«мой холм умирает».

Три величины, которые нельзя склеивать под одним именем:
  * холм   — зерно живых дворов клетки (`docs/05:100-105`, `engine/seat.py:242`);
  * книга  — `Manor.stock_id`, то, что берут `grant_grain` и `relief`;
  * ларёк  — сток `world.player.household_id`, из которого ест двор лорда.

Проверки:
  * весть о холме равна сумме живых дворов клетки и НЕ равна ларьку (на
    `start_stand` они различаются всегда — рядом стоит `hh_retinue`);
  * весть о книге равна `Manor.stock_id` и НЕ равна ни холму, ни ларьку;
  * три числа различимы, поэтому весть ни одну из них не путает с другой;
  * достоверность 1.0 / шум 0.0 / задержка 0 законны (свой запас, ADR 0205);
  * весть не даёт пути к истинному `Tile`/`Household`: `build_player_view`
    равна множеству доставленных `Report`, полей истины в ней нет;
  * `PlayerKnowledge.latest` различает места: холм и книга — разные `about`.

Мутации, которые обязаны ронять этот тест (см. ADR 0205, раздел «Проверка»):
  M1 — вернуть чтение личного ларька в весть о холме;
  M2 — убрать весть о книге;
  M3 — назвать весть о книге «Холм» (склейка двух величин).

Вторая половина файла — `TestHillNameIsNotWiderThanItsNumber` (ADR 0212): на
целевой карте `v0_barony_100` поселение (90 дворов на восьми кварталах) в
7.5 раза шире гекса зала (12 дворов), и подпись «Холм» над гексом — ложь,
которую числами не поймать. Там обвинитель сверяет не «два числа», а ОБЛАСТЬ:
текст вести не вправе называть место шире, чем `subject_id` вести, а число —
не вправе быть правдой о предмете, который весть не называет.
"""

from __future__ import annotations

import unittest
from dataclasses import fields
from pathlib import Path

import yaml

from hillcourt import ontology
from hillcourt.engine.manor import manor_stock, root_manor
from hillcourt.engine.seat import court_tile_id
from hillcourt.engine.tick import run_month
from hillcourt.info.knowledge import build_player_knowledge
from hillcourt.news.views import PlayerView, ReportView, build_player_view
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
STAND = ROOT / "design" / "scenarios" / "start_stand.yml"
HILL_SALT = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
BARONY = ROOT / "design" / "scenarios" / "v0_barony_100.yml"
SEED = 1729
MONTHS = 24
GRAIN = "grain"
EPSILON = 1e-9


def _world(path: Path = STAND, months: int = MONTHS):
    world = load_scenario(path, seed=SEED)
    for _ in range(months):
        run_month(world)
    return world


def _observed_per_month(path: Path = STAND, months: int = MONTHS) -> list[dict]:
    """Помесячная истина, снятая СРАЗУ после `run_month`.

    `phase_inform` — предпоследняя фаза (`engine/tick.py:1070-1091`), а
    `phase_record` только гасит тропы и двигает календарь, так что стоки после
    `run_month` — те же, что видела весть. Снимать надо по месяцам: весть
    `Y1-M01` сравнивается с миром `Y1-M01`, а не с миром после 24 месяцев.
    """
    world = load_scenario(path, seed=SEED)
    court = court_tile_id(world)
    manor = root_manor(world)
    lord = world.households[world.player.household_id]
    barn = manor_stock(world, manor)
    rows = []
    for _ in range(months):
        run_month(world)
        hill = _reports(world, "tile", court, "grain_approx")
        book = _reports(world, "manor", manor.id, "barn_grain")
        rows.append(
            {
                "date": world.clock.date,
                "hill_reported": hill[-1].facts["grain_approx"] if hill else None,
                "barn_reported": book[-1].facts["barn_grain"] if book else None,
                "hill_truth": sum(
                    world.get_stock(world.households[hid].stock_id)
                    .amounts.get(GRAIN, 0.0)
                    for hid in _live_households_on_tile(world, court)
                ),
                "barn_truth": barn.amounts.get(GRAIN, 0.0) if barn else None,
                "larder_truth": world.get_stock(lord.stock_id).amounts.get(GRAIN, 0.0),
            }
        )
    return rows


def _live_households_on_tile(world, tile_id: str) -> list[str]:
    return [
        hid
        for hid in sorted(world.households)
        if world.households[hid].left_at is None
        and world.households[hid].current_tile_id == tile_id
    ]


def _reports(world, subject_kind: str, subject_id: str, fact_key: str):
    return sorted(
        (
            report
            for report in world.reports
            if report.subject_kind == subject_kind
            and report.subject_id == subject_id
            and fact_key in report.facts
        ),
        key=lambda report: report.event_date,
    )


class TestHillReportNamesWhatItReads(unittest.TestCase):
    """Весть о холме — о холме; весть о книге — о книге."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = _world()
        cls.court = court_tile_id(cls.world)
        cls.manor = root_manor(cls.world)
        cls.hill = _reports(cls.world, "tile", cls.court, "grain_approx")
        cls.barn = _reports(cls.world, "manor", cls.manor.id, "barn_grain")
        cls.rows = _observed_per_month()

    def test_hill_report_exists_every_month(self) -> None:
        self.assertTrue(self.hill, "Нет ни одной вести о холме")
        self.assertEqual(len(self.hill), MONTHS)

    def test_barn_report_exists_every_month(self) -> None:
        """M2: без отдельной вести о книге у игрока нет числа «что можно раздать»."""
        self.assertTrue(self.barn, "Нет вести о книге корня")
        self.assertEqual(len(self.barn), MONTHS)

    def test_hill_report_is_grain_of_live_households_of_the_tile(self) -> None:
        """Весть о холме == сумма зерна живых дворов клетки, месяц к месяцу."""
        self.assertGreaterEqual(
            len(_live_households_on_tile(self.world, self.court)),
            2,
            "Стенд не годится: нужен соседний двор на клетке",
        )
        for row in self.rows:
            self.assertIsNotNone(row["hill_reported"], f"{row['date']}: нет вести о холме")
            self.assertAlmostEqual(
                row["hill_reported"],
                row["hill_truth"],
                delta=0.05,
                msg=(
                    f"{row['date']}: весть {row['hill_reported']} != зерно живых "
                    f"дворов клетки {row['hill_truth']}"
                ),
            )

    def test_barn_report_is_the_manor_stock(self) -> None:
        """Весть о книге == `Manor.stock_id`, месяц к месяцу."""
        for row in self.rows:
            self.assertIsNotNone(row["barn_reported"], f"{row['date']}: нет вести о книге")
            self.assertAlmostEqual(
                row["barn_reported"],
                row["barn_truth"],
                delta=0.05,
                msg=f"{row['date']}: книга {row['barn_reported']} != {row['barn_truth']}",
            )

    def test_hill_report_is_not_the_lords_personal_larder(self) -> None:
        """M1: возврат чтения `world.player.household_id` роняет эту проверку.

        На `start_stand` рядом с лордом на клетке стоит `hh_retinue`, поэтому
        сумма дворов и ларёк различаются в каждом месяце. Совпадение означало бы
        возврат бага, а не закон.
        """
        mismatched = [
            row
            for row in self.rows
            if abs(row["hill_reported"] - row["larder_truth"]) < 0.05
        ]
        self.assertEqual(
            mismatched,
            [],
            "Весть о холме снова равна личному ларьку лорда (47.2 в m05)",
        )
        # Последний месяц: ларёк пуст, холм — нет. Именно этот случай врал.
        last = self.rows[-1]
        self.assertAlmostEqual(last["larder_truth"], 0.0, places=6)
        self.assertGreater(
            last["hill_truth"],
            0.0,
            "Стенд не годится: холм тоже пуст, различие нечего проверять",
        )
        self.assertGreater(last["barn_truth"], 0.0)

    def test_three_quantities_are_distinguishable(self) -> None:
        """M3: склейка величин роняет эту проверку — числа обязаны различаться."""
        for row in self.rows:
            self.assertNotAlmostEqual(
                row["hill_reported"],
                row["barn_reported"],
                delta=1.0,
                msg=f"{row['date']}: холм и книга — одно число; величину потеряли",
            )
        self.assertEqual(self.hill[-1].subject_kind, "tile")
        self.assertEqual(self.barn[-1].subject_kind, "manor")
        self.assertNotEqual(self.hill[-1].subject_id, self.barn[-1].subject_id)

    def test_texts_name_their_own_subject(self) -> None:
        """Текст обязан называть свою величину: «дворы» против «книги»."""
        for report in self.hill:
            self.assertIn("дворах", report.content.lower())
            self.assertNotIn("книг", report.content.lower())
        for report in self.barn:
            self.assertIn("книг", report.content.lower())
            self.assertNotIn("дворах", report.content.lower())

    def test_confidence_is_exact_and_lawful_for_own_stock(self) -> None:
        """ADR 0205: 1.0 / 0.0 / задержка 0 законны — свидетель сам держатель.

        Условие, при котором они законны, — предмет вести равен своему
        запасу; оно проверяется `test_hill_report_is_grain_of_live_households_of_the_tile`.
        """
        for report in (*self.hill, *self.barn):
            self.assertEqual(report.confidence, 1.0)
            self.assertEqual(report.noise, 0.0)
            self.assertFalse(report.distorted)
            self.assertEqual(report.delivery_date, report.event_date)
            self.assertEqual(report.source, "eye_from_hill")

    def test_facts_carry_no_world_objects(self) -> None:
        """И-3: в `facts` — плоские числа, а не ссылки на контейнеры мира."""
        for report in (*self.hill, *self.barn):
            for key, value in report.facts.items():
                self.assertIsInstance(key, str)
                self.assertNotIn(key, ("amounts", "stock", "world", "tile_state"))
                self.assertIsInstance(value, float, f"{key} — не число")
            self.assertNotIn("amounts", report.facts)


class TestHillReportOpensNoOmniscience(unittest.TestCase):
    """И-3 на проверку: пути от `build_player_view` к истине мира не появилось."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = _world()
        cls.court = court_tile_id(cls.world)
        cls.manor = root_manor(cls.world)

    def test_player_view_is_exactly_the_delivered_reports(self) -> None:
        final = self.world.clock.date
        view = build_player_view(self.world, final)
        self.assertIsInstance(view, PlayerView)
        delivered = {
            r.id for r in self.world.reports if r.delivery_date <= final
        }
        self.assertEqual({entry.id for entry in view.entries}, delivered)

    def test_report_view_has_no_world_truth_fields(self) -> None:
        names = {field.name for field in fields(ReportView)}
        for forbidden in ("tile", "household", "stock", "world", "amounts"):
            self.assertNotIn(forbidden, names)

    def test_view_reads_no_world_truth(self) -> None:
        """Весть приходит к игроку числами, а не объектами стоков и дворов.

        Копия фактов в представлении игрока — плоские числа и описания. Если бы
        правка открыла путь к истинному `Stock`/`Household`, в `facts` лежал бы
        САМ объект мира, а не его имя.

        **Уточнение 2026-09 (ADR 0212).** Проверка в исходном виде запрещала
        ЛЮБОЙ словарь в `facts` и обвиняла `left_by_settlement` отчёта шерифа
        (ADR 0209) — а это законная таблица «поселение → число ушедших», то
        есть плоские `id` и целые; следом обвиняла и список клеток маршрута
        разведки (`docs/05:...`, канал `scout`). Обвинения были ложными и
        красными ещё до ADR 0212 — проверено на копии `briefing.py` без правки
        0210. Запрещено теперь не «словарь», а сам объект истины: значение
        `facts` не вправе быть (или содержать) `Stock`/`Household`/`Tile`/
        `Settlement`/`Manor`/`World`, на любой глубине.
        """
        view = build_player_view(self.world, self.world.clock.date)
        for entry in view.entries:
            for key, value in entry.facts.items():
                self.assertNotIn(key, ("amounts", "stock", "household"))
                _assert_no_world_object(self, value, f"{entry.id}.{key}")
                _assert_no_world_object(self, key, f"{entry.id}:ключ")


    def test_knowledge_separates_place_from_book(self) -> None:
        """`PlayerKnowledge.latest` различает `about`: холм и книга — разные места."""
        knowledge = build_player_knowledge(self.world, self.world.clock.date)
        hill = knowledge.latest(self.court)
        book = knowledge.latest(self.manor.id)
        self.assertIsNotNone(hill)
        self.assertIsNotNone(book)
        self.assertEqual(hill.source, "eye_from_hill")
        self.assertEqual(book.source, "eye_from_hill")
        self.assertIn("grain_approx", hill.facts)
        self.assertIn("barn_grain", book.facts)
        self.assertNotEqual(hill.about, book.about)


class TestHillReportOnSecondScenario(unittest.TestCase):
    """Правка не привязана к одному стенду: второй мир ведёт себя так же."""

    def test_hill_and_barn_agree_on_hill_and_salt(self) -> None:
        """Глаз с холма и книга амбара — разные величины, и РАЗНЫЕ КАЖДЫЙ МЕСЯЦ.

        **Дефект проверки, найден 2026-09.** Тест сравнивал КАЖДЫЙ отчёт амбара с
        ПОСЛЕДНИМ отчётом холма (`hill[-1]`), то есть месяц с месяцем не
        сопоставлял: 23 отчёта амбара сверялись с одним и тем же числом. Так
        проверка ничего не говорила о согласии — она ловила бы совпадение,
        случайно выпавшее на паре «месяц N против месяца 24», и молчала бы о
        расхождении внутри одного месяца.

        Соседний хелпер этого же файла (`_observed_per_month`) снимает обе
        величины ПОМЕСЯЧНО и правильно; здесь та же логика, только по дате
        отчёта, а не по порядку в списке.

        Побочный эффект правки, названный честно: продажа хлеба королевскому
        двору (ADR 0219) опустила амбар к величине, случайно совпавшей с оценкой
        холма (разница 0.3 при пороге 1.0). При сравнении «каждый с последним»
        эта случайность ПРОХОДИЛА, и тест был зелёным; при попарном сравнении
        расходятся все пары — то есть тест был зелёным по счастливому совпадению,
        а не по закону.
        """
        world = _world(HILL_SALT)
        court = court_tile_id(world)
        manor = root_manor(world)
        hill = _reports(world, "tile", court, "grain_approx")
        barn = _reports(world, "manor", manor.id, "barn_grain")
        self.assertTrue(hill)
        self.assertTrue(barn)
        self.assertEqual(len(hill), len(barn))
        # Попарно по ДАТЕ: месяц N амбара сверяется с месяцем N холма.
        # `SimDate` не хешируется (`@dataclass` без `frozen`), поэтому ключ —
        # его строковое представление, а не сам объект.
        hill_by_date = {str(report.event_date): report for report in hill}
        checked = 0
        for report in barn:
            twin = hill_by_date.get(str(report.event_date))
            self.assertIsNotNone(
                twin, f"{report.event_date}: нет вести о холме на этот месяц"
            )
            with self.subTest(month=str(report.event_date)):
                self.assertNotAlmostEqual(
                    report.facts["barn_grain"],
                    twin.facts["grain_approx"],
                    delta=1.0,
                    msg=(
                        f"{report.event_date}: книга {report.facts['barn_grain']} и "
                        f"холм {twin.facts['grain_approx']} — одно число, "
                        "величины склеились"
                    ),
                )
            checked += 1
        self.assertGreaterEqual(checked, MONTHS)


class TestHillNameIsNotWiderThanItsNumber(unittest.TestCase):
    """ADR 0212: подпись не вправе быть шире числа.

    Дефект, который остался после ADR 0205: подпись «Холм» стояла над зерном
    **гекса зала**, а слово «холм» в карте означает **поселение**. На стенде
    `start_stand` поселение == гекс, поэтому расхождения не было видно; на
    целевой карте `v0_barony_100` поселение `hill_court` — 90 дворов на
    восьми кварталах, гекс зала `t_45_50` — 12 дворов, то есть подпись описывала
    13 % холма (замер в ADR 0212: покрытие гуляло 16 %…63 %).

    Проверка не «сравнить два числа», а сверка ОБЛАСТИ, потому что подделка
    именно в подписи и числами не ловится:
      * `test_stand_is_a_map_where_the_name_is_wider_than_the_hex` — стенд
        годится только если поселение действительно шире гекса (иначе тест
        пуст и ничего не доказывает; это проверка ДО обвинения, а не оно);
      * `test_each_number_is_the_truth_of_its_own_declared_subject` — истина
        для каждой вести считается ТЕСТОМ по `subject_kind`/`subject_id` из
        онтологии, без вызова `info.briefing`: подмена предмета (число от
        другой области) роняется здесь;
      * `test_eye_number_is_a_proper_part_of_the_hill` — число гекса обязано быть
        СОБСТВЕННОЙ частью холма, а не всем холмом: равенство «холм = гекс»,
        то есть возврат к подписи «Холм» над гексом, ловится и числами;
      * `test_text_may_not_name_a_place_wider_than_its_subject` — слово «холм»
        (оно же имя поселения) не вправе стоять в тексте вести, `subject_id`
        которой — гекс. ЭТО ловит возврат старой подписи, которую не поймал бы
        ни один числовой тест;
      * `test_settlement_report_is_a_rumor_not_the_eye` — «холм» как поселение
        приходит отдельной вестью шерифа: со своим `subject_kind`, задержкой в
        месяц, `confidence` 0.5 и шумом канала, а не точной глазной цифрой;
      * `test_settlement_report_names_its_own_place` — и наоборот: весть о
        поселении обязана называть поселение, иначе игрок не отличит её от
        глазной;
      * `test_no_new_path_to_the_world` — И-3 на новую весть.

    Мутации (ADR 0212, раздел «Проверка»): M5 вернуть подпись «Холм»;
    M6 склеить гекс с поселением в глазной вести; M7 убрать весть о поселении;
    M8 выдать весть о поселении за глазную (точной и без задержки);
    M9 назвать весть о поселении «У зала».
    """

    MONTHS_ON_BIG_MAP = 4

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = load_scenario(BARONY, seed=SEED)
        cls.court = court_tile_id(cls.world)
        cls.settlement = cls.world.settlements[cls.world.player.court_settlement_id]
        cls.manor = root_manor(cls.world)
        cls.settlement_name = cls.settlement.name
        # Имена поселения берём из ФАЛА сценария, а не из `Settlement.name`
        # загруженного мира: обвинитель не должен опираться на код, который
        # проверяет.
        cls.declared = yaml.safe_load(BARONY.read_text(encoding="utf-8"))
        cls.rows = []
        for _ in range(cls.MONTHS_ON_BIG_MAP):
            run_month(cls.world)
            eye = _reports(cls.world, "tile", cls.court, "grain_approx")
            book = _reports(cls.world, "manor", cls.manor.id, "barn_grain")
            whole = _reports(
                cls.world, "settlement", cls.settlement.id, "settlement_grain_approx"
            )
            larder = cls.world.households[cls.world.player.household_id]
            # Истина снимается ТУТ ЖЕ, сразу после `run_month`: `phase_inform`
            # предпоследняя фаза (`engine/tick.py:1077-1099`), `phase_record`
            # только гасит тропы и двигает кали��дарь, поэтому стоки после
            # `run_month` — те же, что видела весть этого месяца. Истина
            # месяца, посчитанная позже, была бы истиной другого месяца.
            truth = {
                ("tile", cls.court): _witness_grain(cls.world, "tile", cls.court),
                ("settlement", cls.settlement.id): _witness_grain(
                    cls.world, "settlement", cls.settlement.id
                ),
                ("manor", cls.manor.id): _witness_grain(
                    cls.world, "manor", cls.manor.id
                ),
            }
            cls.rows.append(
                {
                    "date": cls.world.clock.date,
                    "eye": eye[-1] if eye else None,
                    "book": book[-1] if book else None,
                    "whole": whole[-1] if whole else None,
                    "truth": truth,
                    "cell_truth": truth[("tile", cls.court)],
                    "settlement_truth": truth[("settlement", cls.settlement.id)],
                    "barn_truth": truth[("manor", cls.manor.id)],
                    "larder_truth": cls.world.get_stock(larder.stock_id)
                    .amounts.get(GRAIN, 0.0),
                }
            )

    def _subject_of_every_report(self):
        """Все вести месяца, у которых тест умеет назвать истинную величину."""
        for row in self.rows:
            for report in (row["eye"], row["book"], row["whole"]):
                if report is not None:
                    yield row, report

    # --- стенд -----------------------------------------------------------

    def test_stand_is_a_map_where_the_name_is_wider_than_the_hex(self) -> None:
        """Стенд годится только если «холм» (поселение) шире гекса зала.

        Без этого класс был бы тавтологией: на карте, где поселение == гекс,
        подпись «Холм» над гексом истинна и проверять нечего. Факт берётся из
        самого файла сценария, а не из кода.
        """
        declared = next(
            s for s in self.declared["settlements"] if s["id"] == self.settlement.id
        )
        quarters = declared.get("quarter_tiles") or declared.get("household_tiles")
        self.assertGreaterEqual(
            len(quarters),
            4,
            "Стенд выродился: поселение снова на одной клетке",
        )
        live_settlement = _live_settlement_households(self.world, self.settlement.id)
        live_cell = _live_households_on_tile(self.world, self.court)
        self.assertGreater(
            len(live_settlement),
            len(live_cell),
            "Стенд выродился: в поселении не больше дворов, чем в гексе зала",
        )
        self.assertGreaterEqual(
            len(live_settlement) // max(1, len(live_cell)),
            2,
            "Стенд выродился: различие слишком мало, чтобы подделку отличить",
        )

    # --- обвинение -------------------------------------------------------

    def test_each_number_is_the_truth_of_its_own_declared_subject(self) -> None:
        """M6/M8: число вести — правда о предмете, который весть объявила.

        Истина считается тестом из онтологии: `tile` — живые дворы этой клетки,
        `settlement` — живые дворы поселения, `manor` — сток манора. Точная
        весть (`confidence == 1.0`) обязана совпасть в десятых; слух
        (`confidence < 1.0`) обязан укладываться в свой `noise` и носить
        `distorted`, когда не совпал. Подмена предмета (число гекса под именем
        поселения и наоборот) не укладывается ни в одну из этих двух рамок.
        """
        checked = 0
        for row, report in self._subject_of_every_report():
            subject = (report.subject_kind, report.subject_id)
            truth = row["truth"].get(subject)
            self.assertIsNotNone(
                truth,
                f"{report.event_date}: тест не знает истинной величины для "
                f"{subject[0]}:{subject[1]}",
            )
            fact = _grain_fact(report)
            self.assertIsNotNone(
                fact, f"{report.event_date}: весть {report.id} не несёт числа о зерне"
            )
            where = f"{report.event_date} {subject[0]}:{subject[1]}"
            if report.confidence >= 1.0:
                self.assertAlmostEqual(
                    fact,
                    truth,
                    delta=0.05,
                    msg=(
                        f"{where}: весть говорит {fact} с достоверностью 1.0, "
                        f"а предмет вести — {truth}"
                    ),
                )
                self.assertFalse(report.distorted)
            else:
                self.assertLessEqual(
                    abs(fact - truth) / max(truth, EPSILON),
                    report.noise + 1e-9,
                    f"{where}: весть говорит {fact} при истине {truth} "
                    f"и шуме {report.noise}",
                )
                self.assertEqual(
                    report.distorted,
                    abs(fact - truth) > 0.05,
                    f"{where}: искажение не помечено: {fact} против {truth}",
                )
            checked += 1
        self.assertGreaterEqual(checked, 3 * self.MONTHS_ON_BIG_MAP - 2)

    def test_eye_number_is_a_proper_part_of_the_hill(self) -> None:
        """M6: число гекса — часть холма, а не весь холм.

        Если бы «холм» снова означал гекс (или весть стала бы называть
        поселением число гекса), равенство «покрытие = 100 %» было бы законом, а
        подпись «Холм» — правдой. На большой карте это не так, и тест обязан
        это видеть каждый месяц.
        """
        for row in self.rows:
            self.assertIsNotNone(row["eye"], f"{row['date']}: нет глазной вести")
            self.assertGreater(row["cell_truth"], 0.0, f"{row['date']}: гекс пуст")
            self.assertGreater(
                row["settlement_truth"],
                row["cell_truth"],
                f"{row['date']}: поселение не шире гекса — обвинение нечего предъявить",
            )
            self.assertLess(
                _grain_fact(row["eye"]) / row["settlement_truth"],
                1.0,
                f"{row['date']}: глазная весть покрыла весь холм — "
                "это и есть возврат подписи «Холм» над гексом",
            )

    def test_text_may_not_name_a_place_wider_than_its_subject(self) -> None:
        """M5: «холм» — имя поселения, а весть гекса не вправе его носить.

        Единственная проверка в файле, которая ловит подделку БЕЗ смены
        числа: возврат текста «Холм: во дворах зерна около N» оставляет числа
        верными и делает подпись ложью. Словарь имени берётся из файла
        сценария, поэтому проверка не зашивает «холм» константой: она требует,
        чтобы весть гекса не называла ни одного слова имени поселения.
        """
        settlement_id = self.settlement.id
        named_words = _name_stems(settlement_id, self.declared)
        self.assertTrue(
            named_words,
            "Имя поселения пусто — проверять нечего",
        )
        for row in self.rows:
            for report in (row["eye"], row["book"]):
                if report is None:
                    continue
                self.assertEqual(
                    report.subject_kind in ("tile", "manor"),
                    True,
                    "Ожидались глазные вести, а пришла иная",
                )
                claims = _claims_name(report.content, named_words)
                self.assertEqual(
                    claims,
                    [],
                    f"{row['date']}: {report.subject_kind}:{report.subject_id} "
                    f"называет место шире своего предмета — {claims} в тексте "
                    f"«{report.content}»",
                )

    def test_settlement_report_names_its_own_place(self) -> None:
        """M9: весть о поселении обязана называть поселение.

        Обратная сторона закона: переименовать «холм» в «У зала» мало — игрок
        должен получить слово, за которым стоит всё поселение, иначе число
        «весь холм» повиснет без подписи.
        """
        for row in self.rows:
            self.assertIsNotNone(
                row["whole"], f"{row['date']}: нет вести о поселении «{self.settlement_name}»"
            )
            content = row["whole"].content.lower()
            for word in _name_stems(self.settlement.id, self.declared):
                self.assertIn(
                    word,
                    content,
                    f"{row['date']}: весть о поселении не называет поселения: "
                    f"«{row['whole'].content}»",
                )

    def test_settlement_report_is_a_rumor_not_the_eye(self) -> None:
        """M7/M8: «холм» как поселение приходит слухом, а не глазом.

        Глаз по закону видит радиус 1 от зала (`docs/05:101-103`), а поселение
        на большой карте выходит за него на три гекса. Точное число всего
        поселения в канале `eye_from_hill` было бы нарушением закона о радиусе,
        поэтому весть о поселении обязана идти каналом `messenger`: задержка
        месяц, `confidence` 0.5, шум 0.15, ключ факта не `grain_approx`
        (это имя закреплено за точной глазной величиной, `docs/05:104-105`).
        """
        for row in self.rows:
            report = row["whole"]
            self.assertIsNotNone(report, f"{row['date']}: нет вести о поселении")
            self.assertEqual(report.source, "messenger")
            self.assertEqual(report.subject_kind, "settlement")
            self.assertEqual(report.subject_id, self.settlement.id)
            self.assertNotEqual(
                report.subject_id,
                self.court,
                "Предмет вести — и гекс, и поселение: это склейка (ADR 0205)",
            )
            self.assertLess(report.confidence, 1.0)
            self.assertGreater(report.noise, 0.0)
            self.assertEqual(
                report.delivery_date,
                report.event_date.advance(self.world.clock.months_per_year),
                f"{row['date']}: весть шерифа доставлена без задержки месяца",
            )
            self.assertIn("settlement_grain_approx", report.facts)
            self.assertNotIn(
                "grain_approx",
                report.facts,
                "Слух о поселении занял имя точной глазной величины",
            )
            self.assertNotIn("barn_grain", report.facts)
        for row in self.rows:
            self.assertIsNotNone(
                row["book"],
                f"{row['date']}: книга корня пропала — ADR 0205 не отменялся",
            )
            self.assertAlmostEqual(
                _grain_fact(row["book"]), row["barn_truth"], delta=0.05
            )

    def test_eye_stays_exact_about_its_own_cell(self) -> None:
        """Точность 1.0 осталась законной — потому что предмет теперь назван."""
        for row in self.rows:
            report = row["eye"]
            self.assertIsNotNone(report, f"{row['date']}: нет глазной вести")
            self.assertEqual(report.source, "eye_from_hill")
            self.assertEqual(report.subject_kind, "tile")
            self.assertEqual(report.subject_id, self.court)
            self.assertEqual(report.confidence, 1.0)
            self.assertEqual(report.noise, 0.0)
            self.assertEqual(report.delivery_date, report.event_date)

    def test_no_new_path_to_the_world(self) -> None:
        """И-3: новая весть — плоское число, а не новый путь к истине места.

        Проверка на ТОЛЬКО новую весть: чужие каналы по праву ведут и строки
        (например, отчёт об уходе двора несёт его `id`, `docs/05:20-24`), и
        ловить их здесь — не моё дело. А вот весть о поселении обязана быть
        плоским числом и ни одного `id` двора в тексте или `facts` не содержать:
        иначе «покажем всё поселение» превратилось бы в показ дворов поимённо.
        """
        final = self.world.clock.date
        view = build_player_view(self.world, final)
        self.assertEqual(
            {entry.id for entry in view.entries},
            {r.id for r in self.world.reports if r.delivery_date <= final},
            "Представление игрока перестало быть ровно множеством вестей",
        )
        names = {field.name for field in fields(ReportView)}
        for forbidden in (
            "tile",
            "household",
            "stock",
            "world",
            "amounts",
            "settlement",
            "households",
        ):
            self.assertNotIn(forbidden, names, f"ReportView светит истиной: {forbidden}")
        households = set(self.world.households)
        for entry in view.entries:
            if entry.subject_kind != "settlement":
                continue
            for key, value in entry.facts.items():
                self.assertIsInstance(
                    value, (float, int), f"{entry.id}.{key} — не число: {value!r}"
                )
                self.assertNotIn(key, ("amounts", "stock", "household", "households"))
            for hid in households:
                self.assertNotIn(
                    hid,
                    entry.content,
                    f"{entry.id}: весть называет двор поимённо — И-3 нарушен",
                )
            self.assertIsInstance(entry.facts, dict)
            for value in entry.facts.values():
                self.assertNotIsInstance(value, (dict, list, tuple))
        knowledge = build_player_knowledge(self.world, final)
        cell = knowledge.latest(self.court)
        place = knowledge.latest(self.settlement.id)
        self.assertIsNotNone(cell)
        self.assertIsNotNone(place)
        self.assertNotEqual(cell.about, place.about)
        self.assertIn("grain_approx", cell.facts)
        self.assertIn("settlement_grain_approx", place.facts)
        # Поселение не должно выглядеть клеткой, иначе игрок спутает «холм»
        # с гексом: `engine/path.py` открывает клетки только по `subject_kind`.
        self.assertNotIn(self.settlement.id, self.world.tiles)
        self.assertNotIn(self.court, self.world.settlements)


_WORLD_TYPES = (
    ontology.Stock,
    ontology.Household,
    ontology.Tile,
    ontology.Settlement,
    ontology.Manor,
    ontology.Person,
)


def _assert_no_world_object(case, value, where: str) -> None:
    """И-3: в `facts` не вправе лежать САМ объект мира, на любой глубине.

    Словарь из `id` и чисел — это описание («поселение → ушло дворов»), и
    законно: игрок знает имя места, а не ссылку на контейнер. Объект мира —
    не описание, а обход И-3: по нему можно дочитать то, чего весть не сказала.
    """
    if isinstance(value, _WORLD_TYPES):
        case.fail(f"{where}: в известии лежит объект мира {type(value).__name__}")
    if isinstance(value, dict):
        for key, inner in value.items():
            _assert_no_world_object(case, key, f"{where}:ключ")
            _assert_no_world_object(case, inner, f"{where}[{key!r}]")
    elif isinstance(value, (list, tuple, set)):
        for index, inner in enumerate(value):
            _assert_no_world_object(case, inner, f"{where}[{index}]")


def _live_settlement_households(world, settlement_id: str) -> list[str]:
    """Живые дворы поселения — по `Settlement.household_ids`, без чтения клетки."""
    settlement = world.settlements[settlement_id]
    return [
        hid
        for hid in sorted(settlement.household_ids)
        if hid in world.households and world.households[hid].left_at is None
    ]


def _witness_grain(world, subject_kind: str, subject_id: str) -> float | None:
    """Истинная величина зерна ДЛЯ ПРЕДМЕТА, который весть объявила.

    Обвинитель считает сам, из онтологии, и не вызывает `info.briefing`:
    иначе проверка повторяла бы код, который проверяет. `None` — предмета такого
    вида тест не знает и обвинения не предъявляет.
    """
    if subject_kind == "tile":
        ids = _live_households_on_tile(world, subject_id)
    elif subject_kind == "settlement":
        ids = _live_settlement_households(world, subject_id)
    elif subject_kind == "manor":
        manor = world.manors.get(subject_id)
        if manor is None:
            return None
        return world.get_stock(manor.stock_id).amounts.get(GRAIN, 0.0)
    else:
        return None
    return sum(
        world.get_stock(world.households[hid].stock_id).amounts.get(GRAIN, 0.0)
        for hid in ids
    )


def _grain_fact(report) -> float | None:
    """Число вести о зерне — какое бы имя оно ни носило."""
    for key in ("grain_approx", "settlement_grain_approx", "barn_grain"):
        if key in report.facts:
            return float(report.facts[key])
    return None


def _name_stems(settlement_id: str, declared: dict) -> list[str]:
    """Основы значимых слов ИМЕНИ поселения, взятого из файла сценария.

    Сценарий — источник истины о том, как место называется. Основы (первые
    четыре буквы) вместо слов, потому что имя в родительном падеже в тексте
    меняет окончание: «Город на холме» → «в городе на холме».
    """
    entry = next(
        s for s in declared["settlements"] if s["id"] == settlement_id
    )
    stems = []
    for word in str(entry["name"]).lower().replace("«", " ").replace("»", " ").split():
        if len(word) < 4:
            continue
        stem = word[:4]
        if stem not in stems:
            stems.append(stem)
    return stems


def _claims_name(content: str, stems: list[str]) -> list[str]:
    """Слова текста, звучащие как имя поселения (основа в основе)."""
    found = []
    for word in content.lower().replace("«", " ").replace("»", " ").replace(":", " ").split():
        for stem in stems:
            head = word[:4]
            if head == stem or word.startswith(stem) or stem.startswith(word[:4]) and len(word) >= 4:
                if word not in found:
                    found.append(word)
                break
    return found


if __name__ == "__main__":
    unittest.main()
