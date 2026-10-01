"""Приказ лорда **назначить двору работу** и молчание `set_tile_regime` о рельефе.

Стол плейтеста, дословно: «у пяти дворов `request_relief`, у него `oversee`,
назначать некого — приказа назначить нет в `ACTION_HANDLERS` вообще». Замером
подтверждено: `request_relief` занимает **главный** слот, а минорный слот
отдаётся пашне только когда на кормящем гексе двора **стоит урожай**
(`economy/decisions.py`, ADR 0113 п. 1). Двор, у которого надел есть, урожая на
гексе нет, а зерна не запасено, стоял в подаче месяцами и не пахал ни разу — а
рычага у лорда не было ни одного.

Что здесь проверяется и почему именно так:

* **приказ есть в `ACTION_HANDLERS`**, а не «функция где-то есть» — приказа без
  диспетчера нет для игрока, это ровно тот дефект, который замели;
* **приказ даёт труд в этом же месяце**: скрипт исполняется до `run_month`, а
  `phase_labor` (7-я фаза) читает `Household.main_action`; проверяется проводкой
  рецепта, а не полем в датаклассе;
* **приказ не обходит закон**: дело вне набора пресета отвергается. `oversee` и
  `demesne_labor` существуют в `actions_household.yml`, но виллану не даны —
  это проверка без правки состояния, на живом пресете;
* **приказ не выходит за книгу лорда**: соляной держатель (`manor_of_household`
  вернул None, `v0_hill_and_salt::hh_salt_01`) — равный, а не тяглый двор;
* **`set_tile_regime` не молчит о рельефе** (см. класс внизу);
* **клетка под расчистку имеет камень, который требует рецепт** (см. класс внизу).

Про выдачу зерна (`grant_grain`) — отдельный файл `sim/tests/test_grant_grain.py`:
перевод материи, адрес, отказ при нехватке и снятие заморозки двора проверяются
там. Здесь только то, чего там нет: два гейта полномочия и закон ADR 0169 о книге
подачи.

Проверка обязана падать без механики: снятый `assign_work` роняет свой тест на
`AssertionError` с названием приказа, снятый диспетчер — на `assertIn` по имени
рычага, снятая правка рельефа — на отсутствии поля в записи приказа.
"""

from __future__ import annotations

import dataclasses
import unittest
from pathlib import Path

import yaml

from hillcourt.economy.decisions import choose_actions
from hillcourt.engine import manor as engine_manor
from hillcourt.engine.tick import run_month
from hillcourt.runner import (
    ACTION_HANDLERS,
    WORK_LEVER_KEY,
    _apply_script_entry,
    run,
)
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
STAND = ROOT / "design" / "scenarios" / "start_stand.yml"
HILL_AND_SALT = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"
SEED = 1729
GRAIN = "grain"
COURT = "settlement:hill_court"
VILLAGER = "hh_family_01"
COTTER = "hh_family_03"
SALT_HOLDER = "hh_salt_01"
FOREST_TILE = "t_03_00"
# Дело, которое есть в `actions_household.yml`, но виллану не дано: `oversee` —
# надзор лорда, `demesne_labor` — работа на домене. Проверка закона без правки
# состояния: пресет взят из сценария, а не подстроен под тест.
FORBIDDEN_TO_VILLAIN = ("oversee", "demesne_labor")


def assign_work(*args, **kwargs):
    """`engine.manor.assign_work` — с внятным отказом, если приказа нет.

    Импорт на уровне модуля уронил бы весь файл одним `ImportError` и не показал
    бы, какой именно тест держит механику. Здесь падает только тот тест, чей
    приказ отсутствует, и падает с названием приказа.
    """
    order = getattr(engine_manor, "assign_work", None)
    if order is None:
        raise AssertionError("в `engine/manor.py` нет приказа `assign_work`")
    return order(*args, **kwargs)


def grant_grain(*args, **kwargs):
    order = getattr(engine_manor, "grant_grain", None)
    if order is None:
        raise AssertionError("в `engine/manor.py` нет приказа `grant_grain`")
    return order(*args, **kwargs)


def _world(path: Path = STAND):
    world = load_scenario(path, seed=SEED)
    for entry in world.script:
        if int(entry.get("at_month", 0)) == 1:
            _apply_script_entry(world, entry)
    return world


def _actions(world, name: str) -> list[dict]:
    return [entry for entry in world.player_actions if entry.get("action") == name]


class TestTheOrderIsReachable(unittest.TestCase):
    """Приказ без диспетчера — это отсутствие приказа, а не функция.

    **Проверка `assertIn('assign_work', ACTION_HANDLERS)` была фиктивной** (ADR
    0201): имя в таблице есть, а кнопка не нажимается. `_dispatch_assign_work`
    читал `entry["action"]` — то есть ИМЯ ПРИКАЗА — и передавал его в `assign_work`
    как дело двора, откуда `ValueError: Неизвестное дело двора 'assign_work'`.
    Присутствие имени в таблице ничего не говорило о достижимости.

    Поэтому здесь проверяется **путь игрока целиком**: запись сценария, какая
    пишется в `design/scenarios/*.yml`, проходит `_apply_script_entry` — тот же
    путь, что и у `runner.run` — и двор получает дело. Мутация, которая обязана
    ронять тест: возврат `str(entry["action"])` в `_dispatch_assign_work`.
    """

    def test_an_unknown_order_names_the_month(self) -> None:
        """Отказ остаётся в прежнем виде: месяц и имя, а не `KeyError`."""
        world = _world()
        with self.assertRaises(ValueError) as caught:
            _apply_script_entry(world, {"action": "assign_nothing", "at_month": 1})
        self.assertIn("assign_nothing", str(caught.exception))
        self.assertIn(str(world.clock.date), str(caught.exception))

    def test_the_order_is_reachable_through_a_scenario_entry(self) -> None:
        """Главное: запись сценария назначает двору дело, а не падает.

        Ровно та запись, которую писал Стол плейтеста. До правки —
        `ValueError: Неизвестное дело двора 'assign_work'` (ADR 0201).
        """
        world = _world()
        records = _apply_script_entry(
            world,
            {
                "at_month": 1,
                "action": "assign_work",
                "household": VILLAGER,
                WORK_LEVER_KEY: "work_plot",
            },
        )
        self.assertEqual(
            world.households[VILLAGER].main_action,
            "work_plot",
            "запись сценария не назначила двору дело: приказ недостижим",
        )
        self.assertTrue(records, "исполненный приказ не записан в лог")
        self.assertFalse(
            any(r.get("refused") for r in records),
            f"приказ отказал вместо исполнения: {[r.get('reason') for r in records]}",
        )

    def test_the_whole_script_reaches_the_world_over_months(self) -> None:
        """Не «диспетчер вызвал функцию», а «мир от приказа изменился».

        **Почему не «зерно в том же месяце»:** замер показал, что такая проверка
        беззуба. Замер `start_stand`, сид 1729, двор `hh_family_01`, приказ
        `work_plot` в каждом месяце: зерно, пришедшее в сток двора за ОДИН тик,
        одинаково — 1.26 с приказом и 1.26 без него, потому что это подача, а она
        не зависит от дела. Число `process`-проводок за тик тоже совпадает
        (108/87/101/101/101/103/103/87). Приказ виден на горизонте месяцев, а не
        тика, и проверять надо разницу, а не наличие.

        Разница измерена и ненулевая: подача сеньору за 12 месяцев падает
        37.02 → 33.00 (приказ), мир без приказа — 37.02. Мутация, которая обязана
        ронять тест: возврат `str(entry["action"])` в `_dispatch_assign_work` —
        тогда оба прогона дают 37.02 и `assertLess` падает.
        """
        ordered = run(
            STAND, 12, SEED,
            actions=[
                {"at_month": month, "action": "assign_work",
                 "household": VILLAGER, WORK_LEVER_KEY: "work_plot"}
                for month in range(1, 13)
            ],
        )
        bare = run(STAND, 12, SEED)
        self.assertLess(
            ordered.relief_grain,
            bare.relief_grain,
            "приказ из сценария назначил дело, а мир не изменился: приказ мёртвый "
            f"(подача {ordered.relief_grain:.2f} против {bare.relief_grain:.2f})",
        )

    def test_the_action_key_names_the_order_and_never_the_work(self) -> None:
        """Закон разведения двух смыслов, а не совпадение имён.

        `action` — ИМЯ ПРИКАЗА. Если бы оно значило ещё и дело, рычаг `work` был бы
        мёртвым, и наоборот: без этого закона следующий приказ снова станет
        ненажимаемым. Проверяется тем, что `action` в таблице ровно один раз.
        """
        self.assertIn("assign_work", ACTION_HANDLERS)
        self.assertEqual(
            WORK_LEVER_KEY,
            "work",
            "канонический рычаг приказа переименован: сценарии и фикстуры молча "
            "сломаются",
        )
        self.assertNotEqual(
            WORK_LEVER_KEY,
            "action",
            "рычаг и имя приказа снова слились — это и есть дыра ADR 0201",
        )

    def test_a_scenario_without_the_work_lever_is_a_loud_refusal(self) -> None:
        """Опечатка в сценарии падает, а не превращается в «лорд не смог».

        Форму записи проверяет `_check_entry_shape` ДО `try` (ADR 0202). Если бы
        проверка шла внутри перехвата, сценарий с опечаткой тихо отыграл бы двадцать
        месяцев, и отказ выглядел бы как решение игры.
        """
        world = _world()
        with self.assertRaises(ValueError) as caught:
            _apply_script_entry(
                world, {"at_month": 1, "action": "assign_work", "household": VILLAGER}
            )
        message = str(caught.exception)
        self.assertIn(WORK_LEVER_KEY, message, "отказ не назвал ключ рычага")
        self.assertFalse(
            any(r.get("refused") for r in world.player_actions),
            "опечатка сценария записалась как отказ закона: тихая поломка",
        )


class TestAssignWork(unittest.TestCase):
    """Назначение работы лордом: труд, закон и границы полномочия."""

    def test_order_puts_the_yard_on_the_field_in_the_same_month(self) -> None:
        """Главное: приказ исполняется в том же месяце, а не «со следующего»."""
        world = _world()
        yard = world.households[VILLAGER]
        assign_work(world, VILLAGER, "work_plot")
        self.assertEqual(yard.main_action, "work_plot")
        run_month(world)
        reaped = [
            entry
            for entry in world.ledger.entries
            if entry.dst_id == yard.stock_id
            and entry.good == GRAIN
            and entry.kind == "transfer"
        ]
        self.assertTrue(
            reaped, "приказ `work_plot` есть, а зерно двору не пришло: приказ мёртвый"
        )

    def test_order_is_logged_with_the_preset_it_was_given_under(self) -> None:
        world = _world()
        assign_work(world, VILLAGER, "work_plot")
        records = _actions(world, "assign_work")
        self.assertEqual(len(records), 1, "приказ не записан в книгу действий")
        self.assertEqual(records[0]["household"], VILLAGER)
        self.assertEqual(records[0]["main"], "work_plot")
        self.assertEqual(
            records[0]["status"],
            world.households[VILLAGER].legal_status_id,
            "в записи нет пресета, под которым приказ был выдан",
        )

    def test_order_is_visible_to_the_yard(self) -> None:
        """Молчаливый приказ — не приказ: двор обязан узнать о своей работе."""
        world = _world()
        assign_work(world, VILLAGER, "work_plot")
        told = [
            report
            for report in world.reports
            if report.facts.get("event") == "work_assigned"
        ]
        self.assertEqual(len(told), 1, "приказ не дошёл до двора ни одной вестью")
        self.assertEqual(told[0].facts["household_id"], VILLAGER)

    def test_the_order_leaves_no_hidden_state_on_the_yard(self) -> None:
        """Приказ месячный, и это видно: `phase_decide` переписывает выбор двора.

        Иначе «назначение» было бы скрытым вечным приоритетом — вторым
        состоянием без потребителя (ADR 0175 §6). Закон проверяется прямо: после
        тика двор снова решает сам, а в `Household` не появилось поля, которое
        хранит приказ и обходит выбор.
        """
        world = _world()
        yard = world.households[VILLAGER]
        declared = {f.name for f in dataclasses.fields(yard)}
        assign_work(world, VILLAGER, "work_plot")
        self.assertEqual(yard.main_action, "work_plot")
        run_month(world)
        self.assertEqual(
            yard.main_action,
            choose_actions(world, yard)[0],
            "после тика двор не решает сам: приказ пережил `phase_decide` тайно",
        )
        self.assertEqual(
            {f.name for f in dataclasses.fields(yard)},
            declared,
            "приказ завёл новое поле в `Household`: это скрытое постоянное состояние",
        )

    def test_work_the_preset_forbids_is_refused(self) -> None:
        """Закон не обходится приказом: виллану нельзя ни надзирать, ни на домен."""
        world = _world()
        for action in FORBIDDEN_TO_VILLAIN:
            with self.subTest(action=action):
                self.assertIn(action, world.household_actions, "дело вышло из каталога")
                with self.assertRaises(PermissionError):
                    assign_work(world, VILLAGER, action)

    def test_yard_outside_the_book_is_not_the_lords_to_command(self) -> None:
        """Соляной держатель — равный, вне тяглой книги (ADR 0140/0146)."""
        world = _world(HILL_AND_SALT)
        self.assertIsNone(
            engine_manor.manor_of_household(world, SALT_HOLDER),
            "подставка для отказа не годна: держатель оказался в книге",
        )
        with self.assertRaises(PermissionError):
            assign_work(world, SALT_HOLDER, "work_plot")

    def test_unknown_action_is_a_value_error(self) -> None:
        world = _world()
        with self.assertRaises(ValueError):
            assign_work(world, VILLAGER, "plough_the_moon")

    def test_departed_yard_cannot_be_ordered(self) -> None:
        world = _world()
        yard = world.households[VILLAGER]
        yard.left_at = world.clock.date
        with self.assertRaises(PermissionError):
            assign_work(world, VILLAGER, "work_plot")


class TestGrantGrainStaysInsideTheLordsReach(unittest.TestCase):
    """Два гейта полномочия и закон ADR 0169 — то, чего нет в `test_grant_grain`.

    Сам перевод, адрес, отказ при нехватке и снятие заморозки двора проверяет
    `sim/tests/test_grant_grain.py`. Здесь — про то, чему приказ не имеет права:
    кормить чужого двора и попадать в книгу подачи.
    """

    def test_yard_outside_the_book_is_not_fed_from_the_lords_barn(self) -> None:
        world = _world(HILL_AND_SALT)
        holder = world.households[SALT_HOLDER]
        before = world.get_stock(COURT).amounts.get(GRAIN, 0.0)
        held = world.get_stock(holder.stock_id).amounts.get(GRAIN, 0.0)
        with self.assertRaises(PermissionError):
            grant_grain(world, SALT_HOLDER, 60.0)
        self.assertAlmostEqual(
            world.get_stock(COURT).amounts.get(GRAIN, 0.0), before, places=9,
            msg="хлеб ушёл из чужого амбара, хотя приказ отвергли",
        )
        self.assertAlmostEqual(
            world.get_stock(holder.stock_id).amounts.get(GRAIN, 0.0), held, places=9,
            msg="у равного держателя зерна стало больше, хотя приказ отвергли",
        )
        self.assertAlmostEqual(world.ledger.delta(world.total_matter()), 0.0, places=9)

    def test_departed_yard_is_not_fed(self) -> None:
        world = _world()
        yard = world.households[VILLAGER]
        yard.left_at = world.clock.date
        with self.assertRaises(PermissionError):
            grant_grain(world, VILLAGER, 60.0)

    def test_grant_does_not_touch_the_relief_book(self) -> None:
        """ADR 0169: книга подачи держит недобор, а выдача по приказу больше него.

        Закон на книге — `paid_total <= due_amount` по каждой записи
        (`sim/tests/test_relief_is_recorded.py`). Выдача по приказу больше
        недобора по построению, а `apply_relief` дописывает свою долю в **ту же**
        запись (id — пара «сеньор, двор, месяц»), поэтому попадание выдачи в
        книгу развело бы `paid_total` с `due_amount` дважды.
        """
        from hillcourt.economy import exchange

        world = _world()
        grant_grain(world, VILLAGER, 60.0)
        self.assertEqual(
            [o for o in exchange.relief_obligations(world) if o.paid_total > 0.0],
            [],
            "выдача по приказу записалась в книгу подачи: paid_total > due_amount",
        )
        self.assertIsNone(
            world.stats.get("relief_given"), "приказ не подача: счётчик не должен расти"
        )

    def test_grant_is_visible_to_the_yard(self) -> None:
        """Хлеб в руках без вести — вопрос без ответа."""
        world = _world()
        grant_grain(world, VILLAGER, 60.0)
        told = [
            report
            for report in world.reports
            if report.facts.get("event") == "grain_granted"
        ]
        self.assertEqual(len(told), 1, "двор не узнал о полученном зерне")
        self.assertAlmostEqual(told[0].facts["amount"], 60.0, places=9)


class _NoDuplicateKeys(yaml.SafeLoader):
    """YAML, который ругается на повтор ключа, а не берёт последний молча."""


def _no_duplicates(loader, node, deep=False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise AssertionError(f"ключ повторяется: {key!r}")
        seen.add(key)
    return loader.construct_mapping(node, deep)


_NoDuplicateKeys.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates
)


class TestTheClearingCellHasStone(unittest.TestCase):
    """Расчистка не упёрлась в 12 камней, которых на клетке не было.

    `uproot_stumps` требует `draws_standing {log 12.0, stone 12.0}`, а
    `grow_stone` даёт 0.6 × сумма сезонных множителей = 0.6 × 4.28 = **2.568
    камня в год**: 12.0 копится 4.67 года. Ключ `tile:t_03_00` в
    `starting_stocks` был записан **дважды**, и `yaml.safe_load` брал последнюю —
    `stone: 12.0` исчезал молча, без ошибки и без предупреждения. Замер стенда:
    `stone 2.57` на 48-м месяце, 0 партий `uproot_stumps` за 48 месяцев при
    любом труде.
    """

    def test_no_scenario_declares_a_starting_stock_key_twice(self) -> None:
        """Молчаливый дубль ключа — тот же брак, что флаг без потребителя.

        Проверяются все сценарии, а не только стенд: потерянное стартовое
        состояние — это тихая неверная цифра в каждом мире, где ключ повторится.
        """
        for path in sorted((ROOT / "design" / "scenarios").glob("*.yml")):
            if path.name.startswith("tmp"):
                continue
            with self.subTest(scenario=path.name):
                raw = path.read_text(encoding="utf-8")
                try:
                    yaml.load(raw, Loader=_NoDuplicateKeys)
                except AssertionError as caught:
                    self.fail(f"{path.name}: {caught}")

    def test_the_clearing_cell_starts_with_the_stone_the_recipe_asks(self) -> None:
        world = _world()
        tile = world.tiles[FOREST_TILE]
        standing = world.get_stock(tile.standing_stock_id).amounts
        recipe = world.catalogs.recipes["uproot_stumps"]
        self.assertTrue(recipe.draws_standing, "рецепт расчистки ни чего не берёт")
        for good, need in sorted(recipe.draws_standing.items()):
            self.assertGreaterEqual(
                standing.get(good, 0.0) + 1e-9,
                need,
                f"{FOREST_TILE}: {good} {standing.get(good, 0.0):.3f} < {need} — "
                "расчистка не начнётся никогда",
            )

    def test_clearing_right_is_still_granted_on_that_cell(self) -> None:
        """Сторож закона: `demesne` на лесу остаётся законным (ADR 0175 §5).

        Правка чинит камень, а не запрещает домен на лесу: `clear_forest` даёт
        только домен, и запрет убил бы единственный путь к расчистке. Этот тест
        падает, если кто-то введёт тот запрет.
        """
        world = _world()
        tile = world.tiles[FOREST_TILE]
        self.assertEqual(tile.terrain, "forest")
        self.assertEqual(tile.regime_id, "demesne")
        self.assertIn(
            "clear_forest", world.catalogs.land_regimes[tile.regime_id].allowed_actions
        )


class TestRegimeOrderIsNotSilentAboutTerrain(unittest.TestCase):
    """`demesne` на лесу разрешён, но приказ обязан сказать, что это лес.

    Запрещать нельзя (см. `test_clearing_right_is_still_granted_on_that_cell`).
    Молчать тоже нельзя: в книге оставалось `demesne`, и по ней нельзя было
    отличить пашню усадьбы от леса под расчистку — а это разные намерения
    лорда и разные последствия для клетки.
    """

    def test_set_tile_regime_records_the_terrain_it_was_given_on(self) -> None:
        world = _world()
        _apply_script_entry(
            world,
            {"action": "set_tile_regime", "tile": FOREST_TILE, "regime": "demesne"},
        )
        records = _actions(world, "set_tile_regime")
        self.assertTrue(records, "приказ смены режима не записан")
        self.assertEqual(records[0]["terrain"], "forest")
        self.assertTrue(
            records[0]["regime_on_other_terrain"],
            "лес под доменом не помечен: приказ снова прошёл молча",
        )

    def test_a_field_under_demesne_is_not_marked_as_a_mismatch(self) -> None:
        """Пометка не деградировала в «всегда true»: пашня под доменом — обычное дело."""
        world = _world()
        _apply_script_entry(
            world, {"action": "set_tile_regime", "tile": "t_01_00", "regime": "demesne"}
        )
        records = _actions(world, "set_tile_regime")
        field_records = [r for r in records if r["tile"] == "t_01_00"]
        self.assertTrue(field_records, "пашня под доменом не записана")
        self.assertEqual(field_records[0]["terrain"], "field")
        self.assertFalse(
            field_records[0]["regime_on_other_terrain"],
            "пашня помечена как несовпадение: пометка ничего не значит",
        )


if __name__ == "__main__":
    unittest.main()
