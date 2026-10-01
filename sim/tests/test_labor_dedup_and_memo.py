"""Дедуп земель двора и мемо «кормит ли рельеф»: структурные свойства, не время.

Задача этих проверок — не «быстро», а «правильно устроено». Замер по секундам
был бы счётчиком, который мигает на загруженной машине и потому зеленеет всегда;
здесь вместо секунд стоят СЧЁТЧИКИ ВЫЗОВОВ, и каждый из них детерминирован.

Две правки, которые охраняет модуль, обе в `economy/labor.py`:

1. `own_tiles` дедуплицирует клетки по `tile.id`, а не через `Tile.__eq__`
   (датакласс на 13 полей). Замер инженера: 2.48 млн вызовов `Tile.__eq__` на
   месяц при 150 дворах, потому что `own_tiles` зовётся 59 раз на двор-месяц, а
   список надела — до 32 клеток. Проверка `test_own_tiles_never_compares_tiles_by_value`
   требует НОЛЬ сравнений `Tile` с `Tile`; откат к `tile not in tiles` роняет её
   немедленно.
   Равенство ответов проверяется отдельно и независимо от механизма
   (`test_own_tiles_result_matches_value_equality_reference`): эталонная копия
   старого правила считает по `__eq__`, и списки должны совпасть пядь в пядь.

2. `tile_feeds_household` спрашивает у каталога один раз на РЕЛЬЕФ и дальше берёт
   ответ из мемо. Каталог неизменяем в тике, а рельефов единицы, поэтому 376
   вызовов на двор-месяц (56 тыс. полных обходов 61 рецепта на месяц) сведены к
   числу уникальных рельефов. Проверяет это счётчик обходов каталога
   (`test_tile_feeds_scans_catalog_once_per_terrain`), а не время.

Про кэш — закон AGENTS.md §6: запись ушла в `weakref.ref`, значит в дереве обязан
быть обход, который её чистит. Здесь их два, и оба проверяются:
`test_feeds_memo_entry_dies_with_its_catalogs` (колбэк выбрасывает ключ сам) и
`test_feeds_memo_rejects_entry_of_another_object` (на чтении сверяется
`ref() is catalogs`, поэтому запись с переиспользованным `id` не читается).

Ни одно число симуляции здесь не проверяется и не меняется: обе правки обязаны
менять только скорость ответа на вопрос, а не сам ответ. Сквозной обвинитель
изменения числа живёт в `test_start_stand` и `test_matter_conservation`, этот
модуль их не дублирует и время не мерит вообще.
"""

from __future__ import annotations

import gc
import unittest
import weakref
from pathlib import Path

from hillcourt.economy import labor
from hillcourt.economy.labor import own_tiles, tile_feeds_household
from hillcourt.ontology import Tile
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_barony_100.yml"
SEED = 4242


def _own_tiles_by_value_equality(world, household) -> list[Tile]:
    """Эталон: прежнее правило, где «уже есть» значит `tile not in out` (__eq__).

    Живёт здесь, а не в `labor.py`, специально: как только правка в `labor.py`
    откатится, эта копия останется эталоном, и расхождение видно сразу.
    """
    out: list[Tile] = []
    if household.settlement_id and household.settlement_id in world.settlements:
        settlement = world.settlements[household.settlement_id]
        first = world.tiles.get(labor._tile_id(*settlement.coord))
        if first is not None:
            out.append(first)
        for tid in settlement.works_tiles:
            tile = world.tiles.get(tid)
            if tile is not None and tile not in out:
                out.append(tile)
    else:
        tile = world.tiles.get(household.current_tile_id)
        if tile is not None:
            out.append(tile)
    right_tiles = [
        world.tiles[right.tile_id]
        for right in world.rights.values()
        if right.holder_household_id == household.id
        and right.kind != "common"
        and right.tile_id in world.tiles
    ]
    for tile in sorted(right_tiles, key=lambda t: t.id):
        if tile not in out:
            out.append(tile)
    return out


class _TileEqCounter:
    """Считает сравнения `Tile` с `Tile` (сравнение с не-Tile не считаем)."""

    def __init__(self) -> None:
        self.count = 0
        self._raw = Tile.__eq__

    def __enter__(self):
        raw = self._raw
        # Имя счётчика НЕ `self`: первым аргументом `__eq__` приходит сам
        # `Tile`, и перекрытие уронило бы проверку AttributeError вместо
        # внятного «сравнил по значению».
        counter = self

        def counted(tile_self, other):
            if isinstance(other, Tile):
                counter.count += 1
            return raw(tile_self, other)

        Tile.__eq__ = counted
        return self

    def __exit__(self, *exc):
        Tile.__eq__ = self._raw
        return False


class TestOwnTilesDedupById(unittest.TestCase):
    """`own_tiles`: структура «сравниваем по id», ответ — тот же самый."""

    @classmethod
    def setUpClass(cls):
        cls.world = load_scenario(SCENARIO, seed=SEED)

    def test_own_tiles_never_compares_tiles_by_value(self):
        """Ни одного сравнения `Tile.__eq__` за надел, ни для одного двора.

        ДЕРЖИТСЯ НА: сравнении по `tile.id` вместо `tile not in tiles`.
        РОНЯЕТСЯ МУТАЦИЕЙ: вернуть `tile not in tiles` в `labor.own_tiles`
        (и/или убрать `seen: set[str]`). Счётчик сразу ненулевой: надел — до
        32 клетки, на каждый `not in` приходится линейный обход.
        """
        with _TileEqCounter() as counter:
            for hid in sorted(self.world.households):
                own_tiles(self.world, self.world.households[hid])
        self.assertEqual(
            0, counter.count,
            "own_tiles сравнил клетки по значению: дедуп обязан идти по tile.id",
        )

    def test_own_tiles_result_matches_value_equality_reference(self):
        """Список клеток и его порядок — как у прежнего правила, пядь в пядь.

        Независим от механизма: если правка когда-нибудь начнёт отбрасывать
        клетку или менять порядок, тест красный, даже если сравнение по id
        останется на месте.
        """
        checked = 0
        for hid in sorted(self.world.households):
            household = self.world.households[hid]
            got = own_tiles(self.world, household)
            want = _own_tiles_by_value_equality(self.world, household)
            self.assertEqual(
                [t.id for t in want], [t.id for t in got],
                f"надел двора {hid} разошёлся с эталоном __eq__",
            )
            self.assertEqual(len(want), len(set(t.id for t in want)))
            checked += 1
        self.assertGreater(checked, 0, "сценарий не дал ни одного двора")

    def test_dedup_actually_removes_a_duplicate(self):
        """Ветка дедупа не мёртвая: хотя бы раз клетка приходит дважды.

        Если сценарий перестанет давать пересечение `works_tiles` и `Right`,
        проверка ослабнет — это её сигнал, а не повод её удалять.
        """
        shared = 0
        for hid in sorted(self.world.households):
            household = self.world.households[hid]
            settlement = self.world.settlements.get(household.settlement_id or "")
            if settlement is None:
                continue
            by_right = {
                right.tile_id
                for right in self.world.rights.values()
                if right.holder_household_id == hid and right.kind != "common"
            }
            shared += len(by_right & set(settlement.works_tiles))
        self.assertGreater(
            shared, 0,
            "ни один двор не имеет клетки и в works_tiles, и по праву: "
            "дедуп не проверяется",
        )


class TestTileFeedsMemo(unittest.TestCase):
    """`tile_feeds_household`: обход каталога один на рельеф, а не на клетку."""

    @classmethod
    def setUpClass(cls):
        cls.world = load_scenario(SCENARIO, seed=SEED)

    def test_tile_feeds_scans_catalog_once_per_terrain(self):
        """Число полных обходов каталога равно числу УНИКАЛЬНЫХ рельефов.

        ДЕРЖИТСЯ НА: мемо `_feeds_by_terrain` в `labor.tile_feeds_household`.
        РОНЯЕТСЯ МУТАЦИЕЙ: убрать мемо и звать `_scan_feeds_household` каждый
        раз (то есть вернуть исходное тело функции). Счётчик вырастает с
        единиц на рельеф до числа вызовов — 376 на двор-месяц, то есть обратно
        в десятки тысяч обходов каталога на месяц.
        """
        tiles = list(self.world.tiles.values())
        scans: list[str] = []
        raw = labor._scan_feeds_household

        def counted(catalogs, terrain):
            scans.append(terrain)
            return raw(catalogs, terrain)

        # Холодный старт: мир общий у всего класса, и соседние проверки уже
        # прогрели мемо. Без сброса счётчик видел бы 0 обходов и проверял бы
        # не мемо, а порядок тестов.
        labor._FEEDS_BY_TERRAIN.pop(id(self.world.catalogs), None)
        labor._scan_feeds_household = counted
        try:
            for tile in tiles:
                for _ in range(3):        # три двора на одну и ту же клетку
                    tile_feeds_household(self.world, tile)
        finally:
            labor._scan_feeds_household = raw

        self.assertEqual(
            len(set(t.terrain for t in tiles)), len(scans),
            f"каталог обойдён {len(scans)} раз на {len(set(t.terrain for t in tiles))} "
            f"рельефов: мемо не работает",
        )
        self.assertEqual(sorted(set(scans)), sorted(set(t.terrain for t in tiles)))

    def test_memo_answer_equals_direct_scan_for_every_terrain(self):
        """Ответ из мемо равен ответу честного обхода — по каждому рельефу.

        Мемо меняет КАК спрашивают, а не ЧТО. Если каталог когда-нибудь изменят
        так, что правило «какой рецепт кормит» поедет, эта проверка покажет
        расхождение первым.
        """
        terrains = sorted({t.terrain for t in self.world.tiles.values()})
        self.assertGreater(len(terrains), 1, "один рельеф — сравнивать нечего")
        for terrain in terrains:
            tile = next(t for t in self.world.tiles.values() if t.terrain == terrain)
            self.assertEqual(
                labor._scan_feeds_household(self.world.catalogs, terrain),
                tile_feeds_household(self.world, tile),
                f"рельеф {terrain}: ответ из мемо разошёлся с обходом каталога",
            )

    def test_salt_flat_still_does_not_feed(self):
        """Закон ADR 0108 жив: солончак — производственная клетка, не пашня.

        Это единственная пара «рельеф кормит / не кормит» в каталоге, и оба
        ответа обязаны остаться прежними после введения мемо.
        """
        by_terrain = {
            t.terrain: t for t in self.world.tiles.values()
        }
        self.assertIn("salt_flat", by_terrain)
        self.assertIn("field", by_terrain)
        self.assertFalse(tile_feeds_household(self.world, by_terrain["salt_flat"]))
        self.assertTrue(tile_feeds_household(self.world, by_terrain["field"]))

    def test_feeds_memo_entry_dies_with_its_catalogs(self):
        """Закон AGENTS.md §6: запись в кэше обязана убираться сама.

        Ключ кэша — `id(catalogs)`, поэтому без обхода, который чистит запись,
        новый набор каталогов получил бы чужой ответ (и тик создал бы материю
        из ничего). Проверяем, что колбэк `weakref.ref` действительно выбрасывает
        ключ, когда набор каталогов умер.
        """
        world = load_scenario(SCENARIO, seed=SEED)
        tile = next(iter(world.tiles.values()))
        tile_feeds_household(world, tile)
        key = id(world.catalogs)
        self.assertIn(key, labor._FEEDS_BY_TERRAIN, "мемо не завелось")
        self.assertIsNotNone(labor._FEEDS_BY_TERRAIN[key][1]())

        del world
        gc.collect()
        self.assertNotIn(
            key, labor._FEEDS_BY_TERRAIN,
            "запись пережила свой набор каталогов: нужен обход, который её чистит",
        )

    def test_feeds_memo_rejects_entry_of_another_object(self):
        """Вторая страховка: на чтении сверяется `ref() is catalogs`.

        Подсаживаем запись с ЧУЖОЙ (но живой) ссылкой под тем же ключом — это
        ровно то, что увидел бы мир, попавший на переиспользованный `id`.
        Мемо обязано такую запись отбросить и посчитать заново, а не поверить ей.
        """
        world = load_scenario(SCENARIO, seed=SEED)
        catalogs = world.catalogs
        key = id(catalogs)
        other = load_scenario(SCENARIO, seed=SEED).catalogs
        labor._FEEDS_BY_TERRAIN[key] = ({}, weakref.ref(other))
        self.assertIsNot(labor._FEEDS_BY_TERRAIN[key][1](), catalogs)

        tile = next(t for t in world.tiles.values() if t.terrain == "field")
        self.assertTrue(tile_feeds_household(world, tile))
        self.assertIs(
            labor._FEEDS_BY_TERRAIN[key][1](), catalogs,
            "мемо поверило записи с чужим ref: сверка `ref() is catalogs` убрана",
        )
        del other
        gc.collect()

    def test_feeds_memo_is_per_catalogs_not_global(self):
        """Два мира с разными наборами каталогов не делят одну запись.

        Ключ — `id(catalogs)`, а `load_catalogs` отдаёт копию на каждый мир,
        поэтому ответы мира не могут утечь в другой. Проверяем прямо: после
        опроса первого мира второй мир с НОВЫМ каталогом обязан получить
        собственную запись и тот же ответ.
        """
        first = load_scenario(SCENARIO, seed=SEED)
        second = load_scenario(SCENARIO, seed=SEED)
        self.assertIsNot(first.catalogs, second.catalogs)
        field = next(t for t in first.tiles.values() if t.terrain == "field")
        self.assertTrue(tile_feeds_household(first, field))
        self.assertNotIn(
            id(second.catalogs), labor._FEEDS_BY_TERRAIN,
            "мир, который ещё ни разу не спрашивал, уже имеет запись в мемо",
        )
        field2 = next(t for t in second.tiles.values() if t.terrain == "field")
        self.assertTrue(tile_feeds_household(second, field2))
        self.assertIsNot(
            labor._FEEDS_BY_TERRAIN[id(first.catalogs)][0],
            labor._FEEDS_BY_TERRAIN[id(second.catalogs)][0],
        )
        del first, second, field, field2
        gc.collect()


if __name__ == "__main__":
    unittest.main()
