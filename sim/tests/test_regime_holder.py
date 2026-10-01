"""ADR 0198: приказ смены режима назначает клетке держателя.

`land_regimes.yml` различает `requires_labor_days` (домен) и `feeds_household`
(надел). Надел — это обещание клетки **держателю**, а держателем клетки является
двор с индивидуальным правом `Right`: `economy/labor.py::own_holding_tiles` берёт
клетки «своя усадьба + клетки по `Right`», и `own_land_feeds` (ADR 0124) — единый
факт «этот двор землевладелец» — читает ровно этот набор.

`set_tile_regime` менял только `Tile.regime_id` и `Manor.tile_regimes`, то есть
клетка объявлялась наделом, и **никто** её не держал. Последствия, оба измерены
на плейтесте:

  * клетка выходила из домена (`requires_labor_days: false`) и теряла
    `grow_grain.params.demesne_base_yield` = 1.27;
  * «кормит» не сбывалось: `_feeding_tiles` и `own_land_feeds` идут по `Right`.

Поэтому все восемь режимов на одной клетке давали одинаковый результат: приказ
одинаково выкидывал клетку из производства, и разнице в режиме было некуда
проявляться.

Назначение — функция режима (`engine/manor.py::appoint_regime_holder`):

  * режим не надел (`demesne`/`waste`/`reserved_wood`/`foreign`) — `not_a_holding`,
    назначать некого;
  * надел, индивидуальное право уже есть — `kept`, приказ не отбирает
    держателя молча;
  * надел, права нет, на клетке стоит двор — `granted`, приказ выдаёт `Right`;
  * надел, права нет, двор не занимает — **`awaited`**: клетка ждёт двора.

Третий исход (`awaited`) — сознательный выбор, а не пробел. Доменом подменять
нельзя: `land_regimes.yml` запрещает смешивать домен и надел, а оба читателя
(`growth.py` и `yield_law.demesne_field_factor`) опознают домен по
`requires_labor_days` — надел под доменом противоречил бы самому себе. Общиной
(`Right.kind = "common"`) тоже нельзя: ADR 0060 и `own_tiles`/`_held_tile_ids`/
`worked_land_tile_ids` исключают `common` из наделов **для всех**, то есть
общиной клетка стала бы в тот самый момент, когда перестала бы кормить. Остаётся
третье: режим записан, держатель не назначен, назначит его `grant_tenure`.

Проверки:
  * надел без двора — режим и книга записаны, права нет, исход `awaited`;
  * надел под двором — `Right` выдан, в мире есть, исход `granted`;
  * существующего держателя приказ не отбирает и второго права не плодит;
  * два двора на одной клетке — достаётся младший по id (детерминизм, И-6);
  * `demesne`/`waste` держателя не назначают;
  * назначение не двигает материю (И-1) и детерминировано (И-6).
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hillcourt.economy.labor import own_holding_tiles, own_land_feeds
from hillcourt.engine.manor import grant_tenure, root_manor, set_tile_regime
from hillcourt.ontology import Right
from hillcourt.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "design" / "scenarios" / "v0_hill_and_salt.yml"

# Пашня, на которой стоит двор: `fs_03` в [5, 1], двор `hh_03` (пресет `cotter`).
RESIDENT_TILE = "t_05_01"
RESIDENT = "hh_03"
# `works_tiles` холма: пашня под родовым двором, но на ней никто не живёт.
EMPTY_TILE = "t_05_02"
# Соляная деревня: на клетке [8, 5] стоят сразу два двора, оба `holder`.
TWO_RESIDENTS_TILE = "t_08_05"
TWO_RESIDENTS = ("hh_salt_01", "hh_salt_02")
HOLDING_REGIMES = ("free_holding", "cotter_plot", "villein_tenement", "tenement")
NON_HOLDING_REGIMES = ("demesne", "waste", "reserved_wood", "foreign")


def _order(world, tile_id: str) -> dict:
    """Последняя запись приказа `set_tile_regime` по клетке."""
    records = [
        record
        for record in world.player_actions
        if record.get("action") == "set_tile_regime" and record.get("tile") == tile_id
    ]
    assert records, f"приказ смены режима по '{tile_id}' не записан"
    return records[-1]


def _individual_rights(world, tile_id: str) -> list:
    """Индивидуальные права на клетку, по id (`common` — доступ, а не держание)."""
    return sorted(
        (
            right
            for right in world.rights.values()
            if right.tile_id == tile_id and right.kind != "common"
        ),
        key=lambda right: right.id,
    )


class TestRegimeAppointsHolder(unittest.TestCase):
    """Назначение держателя — часть приказа, а не отдельная операция."""

    def setUp(self) -> None:
        self.world = load_scenario(SCENARIO)

    def test_nadel_bez_dvora_zhdet_i_ne_poluchaet_prava(self) -> None:
        """Вариант 2: наделный режим без двора — клетка ждёт, права не получает."""
        world = self.world
        self.assertEqual(_individual_rights(world, EMPTY_TILE), [])

        set_tile_regime(world, EMPTY_TILE, "free_holding")

        self.assertEqual(world.tiles[EMPTY_TILE].regime_id, "free_holding")
        self.assertEqual(
            root_manor(world).tile_regimes[EMPTY_TILE],
            "free_holding",
            "книга режимов разошлась с клеткой",
        )
        self.assertEqual(
            _individual_rights(world, EMPTY_TILE),
            [],
            "клетка без двора получила держателя из ниоткуда",
        )
        self.assertEqual(_order(world, EMPTY_TILE)["appointment"], "awaited")
        self.assertIsNone(_order(world, EMPTY_TILE)["holder"])

    def test_ozhidayushchaya_kletka_ne_kormit_nikogo_i_ne_vozvrashchaetsya_v_domen(self) -> None:
        """Ожидание — это третье состояние, а не домен и не община.

        Доменом подменять нельзя: `demesne_field_factor` опознаёт домен по
        `requires_labor_days`, и надел под доменом противоречил бы себе. Ни у
        одного двора клетка не должна стать наделом.
        """
        world = self.world
        set_tile_regime(world, EMPTY_TILE, "free_holding")

        self.assertFalse(
            world.catalogs.land_regimes[
                world.tiles[EMPTY_TILE].regime_id
            ].requires_labor_days,
            "надельный режим не должен работать за трудодни",
        )
        for household in world.households.values():
            self.assertNotIn(
                EMPTY_TILE,
                [tile.id for tile in own_holding_tiles(world, household)],
                f"двор '{household.id}' стал землевладельцем клетки без права",
            )

    def test_nadel_pod_dvorm_vydast_right(self) -> None:
        """На клетке стоит двор — приказ выдаёт ему право."""
        world = self.world
        self.assertEqual(world.households[RESIDENT].current_tile_id, RESIDENT_TILE)
        self.assertEqual(_individual_rights(world, RESIDENT_TILE), [])

        set_tile_regime(world, RESIDENT_TILE, "free_holding")

        rights = _individual_rights(world, RESIDENT_TILE)
        self.assertEqual(len(rights), 1, f"прав на клетке {len(rights)}, а не одно")
        right = rights[0]
        self.assertEqual(right.holder_household_id, RESIDENT)
        self.assertEqual(right.kind, "tenure")
        self.assertIn(right.id, world.rights)
        record = _order(world, RESIDENT_TILE)
        self.assertEqual(record["appointment"], "granted")
        self.assertEqual(record["holder"], RESIDENT)
        self.assertEqual(record["right"], right.id)

    def test_pravo_na_kletku_zhitelya_eto_zapis_a_ne_pribastka_pashni(self) -> None:
        """Граница закона, измеренная, а не предположенная (ADR 0198 «Последствия»).

        Усадьба двора уже входит в `own_holding_tiles` по поселенческой координате
        (`labor.py::own_holding_tiles` берёт `settlement.coord`, а не клетку с
        правом), поэтому `Right`, выданный двору **на его собственной** клетке,
        ничего не добавляет к `own_land_feeds`. Назначение становится приростом
        там, где клетку дали двору не по месту жительства, — то есть через
        `grant_tenure`. Закон честен и на это не претендует; тест держит границу,
        чтобы её не объявили потом поломкой.
        """
        world = self.world
        holder = world.households[RESIDENT]
        settlement = world.settlements[holder.settlement_id]
        self.assertEqual(
            settlement.coord,
            (int(RESIDENT_TILE.split("_")[1]), int(RESIDENT_TILE.split("_")[2])),
            "фикстура сменилась: двор больше не живёт на своей клетке",
        )
        self.assertTrue(
            own_land_feeds(world, holder),
            "усадьба сама по себе не кормит двор — значит фикстура не та",
        )

        set_tile_regime(world, RESIDENT_TILE, "free_holding")

        self.assertTrue(own_land_feeds(world, holder))
        self.assertEqual(len(_individual_rights(world, RESIDENT_TILE)), 1)

    def test_sushchestvuyushchego_derzhatelya_ne_otbirayut(self) -> None:
        """Приказ не отбирает назначенное ранее и не плодит второе право."""
        world = self.world
        granted = grant_tenure(world, RESIDENT, RESIDENT_TILE, kind="grazing")
        self.assertIsInstance(granted, Right)
        before = granted.id

        set_tile_regime(world, RESIDENT_TILE, "free_holding")

        rights = _individual_rights(world, RESIDENT_TILE)
        self.assertEqual(
            [right.id for right in rights],
            [before],
            "приказ смены режима отобрал или продублировал право",
        )
        record = _order(world, RESIDENT_TILE)
        self.assertEqual(record["appointment"], "kept")
        self.assertEqual(record["holder"], RESIDENT)

    def test_dva_dvora_odna_kletka_beret_nizshee_po_id(self) -> None:
        """Выбор детерминирован: порядок не должен зависеть от словаря (И-6)."""
        world = self.world
        residents = sorted(
            household.id
            for household in world.households.values()
            if household.current_tile_id == TWO_RESIDENTS_TILE
        )
        self.assertEqual(residents, list(TWO_RESIDENTS))

        set_tile_regime(world, TWO_RESIDENTS_TILE, "free_holding")

        rights = _individual_rights(world, TWO_RESIDENTS_TILE)
        self.assertEqual(len(rights), 1)
        self.assertEqual(rights[0].holder_household_id, TWO_RESIDENTS[0])
        self.assertEqual(_order(world, TWO_RESIDENTS_TILE)["holder"], TWO_RESIDENTS[0])

    def test_rezhim_ne_nadel_derzhatelya_ne_naznachaet(self) -> None:
        """`demesne` и `waste` — не наделы, назначать в них некого."""
        world = self.world
        for regime_id in NON_HOLDING_REGIMES:
            with self.subTest(regime=regime_id):
                set_tile_regime(world, RESIDENT_TILE, regime_id)
                self.assertEqual(
                    _individual_rights(world, RESIDENT_TILE),
                    [],
                    f"режим '{regime_id}' выдал держателя, хотя не надел",
                )
                self.assertEqual(
                    _order(world, RESIDENT_TILE)["appointment"], "not_a_holding"
                )

    def test_rezhim_ne_otzyvaet_uzhe_vydannoe_pravo(self) -> None:
        """Граница закона намеренная: приказ назначает, но не отзывает.

        Отзыв — это `revoke_tenure`, и он же снимает повинности. Здесь же право
        остаётся инертным для труда: `_feeding_tiles` отсеивает клетку по
        `feeds_household`, то есть `demesne` право не кормит и не портит.
        """
        world = self.world
        granted = grant_tenure(world, RESIDENT, RESIDENT_TILE, kind="grazing")
        self.assertIsInstance(granted, Right)
        before = granted.id

        set_tile_regime(world, RESIDENT_TILE, "demesne")

        self.assertEqual([right.id for right in _individual_rights(world, RESIDENT_TILE)], [before])

    def test_every_catalog_regime_gives_one_of_four_outcomes(self) -> None:
        """Все режимы каталога дают один из четырёх исходов, а не исключение."""
        world = self.world
        allowed = {"not_a_holding", "kept", "granted", "awaited"}
        for regime_id in sorted(world.catalogs.land_regimes):
            with self.subTest(regime=regime_id):
                set_tile_regime(world, EMPTY_TILE, regime_id)
                self.assertIn(_order(world, EMPTY_TILE)["appointment"], allowed)
                self.assertEqual(
                    _individual_rights(world, EMPTY_TILE),
                    [],
                    f"режим '{regime_id}' выдал право на пустую клетку",
                )

    def test_naznachenie_ne_dvigaet_materiyu(self) -> None:
        """И-1: приказ двигает право, а не материю."""
        world = self.world
        before = world.total_matter()
        for regime_id in (*HOLDING_REGIMES, *NON_HOLDING_REGIMES):
            set_tile_regime(world, RESIDENT_TILE, regime_id)
        self.assertAlmostEqual(
            world.total_matter(),
            before,
            places=9,
            msg="приказ смены режима создал или сжёг материю",
        )

    def test_odinakovy_mir_daet_odinakovoe_naznachenie(self) -> None:
        """И-6: тот же мир и тот же приказ — тот же `Right` и тот же хеш."""
        first = load_scenario(SCENARIO)
        second = load_scenario(SCENARIO)
        for world in (first, second):
            for tile_id in (RESIDENT_TILE, EMPTY_TILE, TWO_RESIDENTS_TILE):
                set_tile_regime(world, tile_id, "free_holding")
        self.assertEqual(sorted(first.rights), sorted(second.rights))
        self.assertEqual(first.state_hash(), second.state_hash())


if __name__ == "__main__":
    unittest.main()
