# 07. Право

> Вводная и карта срезов по праву и земле — `docs/09_law_and_land.md`. Деталь по манору —
> `docs/08_manor.md`.

## Назначение

Формальная модель права v0 по источнику «ранние англосаксы + Domesday как гибрид»: три оси
(личный статус, отношение к земле, бандл повинностей), пресеты-ярлыки, наделы манора.
Право — то, что игрок нарезает (`Right`/`Obligation`/`Pack`), а не приказ человеку (И-2).
Модуль-владелец — `sim/src/hillcourt/legal/` (роль Legal). Экономику зерна и yield не трогаем.

Свободность **юридическая**, а не «никому не должен»: свободный держит землю за службу/ренту.
Раб — не виллан; виллан — не раб. Зарплаты крестьянам нет.

## Правила

### Три оси
`Household`/`Person` несут:
- `personal_status ∈ {free, tied, slave}`;
- `land_relation ∈ {secure_holding, tenement, landless}`;
- `obligation_bundle` — id бандла из `obligations.yml`.

Пресет — ярлык из `legal_statuses.yml`, а не отдельный класс в коде. Ровно 8 пресетов:

| preset | personal_status | land_relation | obligation_bundle |
|---|---|---|---|
| `holder` | free | secure_holding | holder_none |
| `thegn` | free | secure_holding | thegn_service |
| `sokeman` | free | secure_holding | sokeman_rent |
| `geneat` | free | tenement | geneat_service |
| `villein` | tied | tenement | villein_full |
| `cotter` | tied | tenement | cotter_monday |
| `free_landless` | free | landless | free_landless_none |
| `slave` | slave | landless | slave_ration |

Права пресета: `can_leave`, `marriage_needs_permission`, `court ∈ {manor,public}`, `wergeld`,
`inheritance`, `can_sell_land`, `can_be_taken_on_expedition`, `ploughs`, `land_kind`.
`slave` отличается от `villein` отсутствием вергельда (`wergeld: false`); `thegn`/`holder`
не пашут сами (`ploughs: false`).

### Земля манора (`design/catalogs/land_regimes.yml`)
Режим/надел клетки (`Tile.regime_id`) отвечает, что двор МОЖЕТ (`allowed_actions`: `plough`,
`gather_brushwood`, `take_game`, `build_hut`, `leave`) и как земля участвует в хозяйстве:
`requires_labor_days` (домен требует трудодней), `feeds_household` (надел кормит двор).

| `id` | Домен? | Кормит? | Где |
|---|---|---|---|
| `demesne` | да | нет | своя клетка лорда |
| `villein_tenement` | нет | да | вилланский надел |
| `cotter_plot` | нет | да | коттерский клочок |
| `free_holding` | нет | да | свободное держание |
| `tenement` | нет | да | общий синоним надела |
| `waste` | нет | нет | пустошь, топь |
| `reserved_wood` | нет | нет | заповедный лес |
| `foreign` | — | — | выводится для чужого двора |

Домен и надел **не смешивают стоки**. `foreign` не хранится на клетке: выводится
(`legal/regimes.py`). `allowed_actions` урезает набор по пресету (уход, пахота).

`Right.kind = common` означает **общий доступ**, а не наделение конкретному двору: такие клетки
не входят в индивидуальные `own_tiles`/`_held_tile_ids` и не создают первого дворянина-владельца.
`Right.kind = grazing`, наоборот, остаётся **личным наделом** с индивидуальным режимом и
ограничениями. Земли племени-союзника задаются общим доступом `common` плюс
`Settlement.works_tiles`; `grazing` выдаётся отдельно только как личный надел (ADR 0060/0062/0064).

### Бандлы повинностей (`design/catalogs/obligations.yml`)
Три валюты: **трудодни** (`labor-day`), **гэфоль** (`penny`/`in-kind`), **паёк**.
У каждого числового условия есть `unit`; несведённое к месячному тику помечено `note`
(числа — СТАРТ из Rectitudines, не баланс).

| bundle | что несёт |
|---|---|
| `villein_full` | барщина по календарю, harvest_extra, boon_days_cap, geld_michaelmas, in_kind_martinmas, sow_demesne_acres |
| `cotter_monday` | барщина по календарю (понедельник), harvest_extra, almost_no_cash |
| `geneat_service` | callout по календарю, cartage, messenger, harvest_help, food_for_lord |
| `sokeman_rent` | fixed_rent_grain_or_pence, rare_labor, may_leave |
| `slave_ration` | no_holding, eats_lord_board, full_control |
| `thegn_service` | no_farm_labor, musters_men, self_armed, bridge_burh_fyrd |
| `holder_none`, `free_landless_none` | без повинности |

Сведены к тику (`legal/bundles.py`): сезонная барщина по календарю → `Obligation.duty_days`,
`fixed_rent_grain_or_pence` → натуральная рента. Остальное — данные.

### Календарь работ (`design/catalogs/calendar_v0.yml`)
Год = 12 месячных тиков. Барщина **сезонная**: бандл ссылается на календарь
(`calendar: calendar_v0`), а не дублирует 12 копий чисел. Дневного календаря святых нет.
Перевод недель в тик один: `month_days = week_days * 4` (`legal.calendar.WEEKS_PER_MONTH`).
- `villein`/`cotter` — `base_week_days` + `extra_week_days` (extra — помога).
- `geneat`/`sokeman` — только `callout` (недельных дней нет); geneat вызывается в сено/жатву.
- `slave` — `always`, сезонного нуля нет.
- `free_landless` — `hire_demand low|high` (наём, не долг).
- `boon_allowed` — лорд берёт помогу (bene) до `boon_days_cap`; счётчик `boon_used_this_year`
  (tension_flag), счастье не моделируется.
- `sow_demesne_acres` — гэфоль-пахота своим зерном в домен (`legal.calendar.sow_demesne`),
  зерно идёт двор → сток домена через `Ledger`; урожайность не считаем.

### Кто что может
- `tied` (villein, cotter) **не уходит** без приказа; бегство — отдельный риск
  (`legal/flight.py`), пойманного на земле лорда возвращают. В тик бегство не подключено.
- **Держатель ЛЮБОГО манора не уходит, пока манор жив**: `can_leave` ложен для двора, чей
  человек держит манор (`legal/regimes.py:can_leave`), — корневой `holder` и вложенный тэн
  одинаково (зеркало I3/J1). Освобождает только отзыв пожалования (`revoke_thegn`); `tied` и
  свободные вне книги — как раньше, `sokeman` уходит свободно.
- **Доступ к общинным клеткам сбора.** Режимы `waste`/`reserved_wood` — не держание, а право
  доступа: сбор стоячей материи (рецепты без входов с `draws_standing`: хворост, сено, торф,
  железо, шерсть) доступен двору, если клетка входит в `Settlement.works_tiles` его поселения
  или на неё выдан `Right.kind = common` поселению либо двору
  (`legal/regimes.py:has_access_to_communal_tile`, `is_communal_collection_tile`). Соседство
  по гекс-сетке даёт только встречу и путь, не доступ к сбору; `own_tiles` и `_held_tile_ids`
  от этого не меняются. Выход капится **ёмкостью клетки** (общий кап 4 партии), а не «каждому
  полная виргата» (`economy/labor.py:_communal_collection_shared`); пашня делится только
  держимой клеткой (G3, `economy/labor.py:_held_tile_ids`) (ADR 0077).
- **Число пожалований тэнов** задаёт сценарий: `player.thegn_grant_limit.grants` (по умолчанию
  1; в `v0_shire` — 2), depth ≤ 1. Третье при исчерпанном лимите отклоняется
  (`engine/manor.py:grant_thegn`, лог `grant_thegn_rejected reason=grants_limit`). Двор самого
  держателя всегда входит в книгу тэна (сверх лимита тяглых дворов).
- **Служба тэна — комплект, не титул (ADR 0021).** `grant_thegn` пишет в книгу норму:
  `service_kits_required = 1 (сам как тяжёлый) + число тяглых дворов`,
  `service_men_required = взрослые двора держателя`. `mustered` требует `war_kit ≥ 1` **на
  дворе держателя**; `service_met` сверяет норму с `fief_kits` (амбар + двор держателя), явку
  `men_held` и сытость (`engine/manor.py:596-630`). Сытый без комплекта — плохой тэн. Комплект —
  объект стока (`smith_kit`: `iron_bloom 1.0 → war_kit 1.0`, труд 10), не `+ATK` и не бафф
  урожая. Кольцо `guard_ring` = клетки книги + непосредственные соседи (**вычисляется**,
  не хранится) — это радиус вылазки тэна, а не глаз холма. Пустая служба подряд
  `REVOKE_BAD_MONTHS = 6` плюс развал держания — сигнал `revoke_due`, но отзыв исполняет
  действие корня, не автособытие. `revoke_thegn` возвращает землю, дворы и амбар в корень;
  бывший держатель остаётся живым **свободным без земли** (`free_landless`), не крепостным
  и не столом лорда.
- **Конь — тягло, не служба.** Рабочий конь нормы комплекта не закрывает (`war_kit` — только
  железо). Боевого применения коня в v0 нет; боевой конь не заводится без отдельного ADR.
  Правило `can_hold_horse` (только `free`) — **механизм с ADR 0030**: конь даёт тягло/воз
  лишь свободному двору (`economy/livestock.py::draft_goods_for`, проводка в `decisions`);
  `tied`-двор с конём бонуса не получает, в старте сценариев коня у `tied` нет.
- `slave` **не имеет своего двора-агента**: это рот и руки при стоке лорда (в сценарии
  `hh_court` держит двоих рабов; своего стока у них нет).
- `free_landless` может уйти и наняться, земли не продаёт.
- `thegn`/`holder` не пашут сами.
- `sokeman` уходит свободно и платит фиксированную ренту.
- `grant_tool(household, good, amount)` — выдача железа: перевод `iron_share`/`iron`/
  `iron_bloom` (лемех, сырое железо, крица; ADR 0021) из амбара
  корневого манора двору (`engine/manor.py:grant_tool`) с записью в `World.player_actions`;
  при нехватке — отказ ДО перевода, частичной выдачи нет.
- Сосед-держатель — равный, не вассал (`vassalage_allowed() == False`); соляная деревня
  (`holder`) не платит ренту игроку.

### Недоимка и уход (цепочка исполняется; таблица Legal, цикл 24)
- **Накопление.** Неоплаченная натура/рента (`phase_obligations`): `Obligation.arrears += due`,
  `Household.arrears_days += 1`. Ушедшие (`left_at`) пропускаются.
- **Прощение при уплате.** Уплата срезает недоимку на половину уплаченного:
  `arrears = max(0, arrears − due × 0.5)` (`engine/tick.py`).
- **Триггер ухода — только от ренты.** `arrears ≥ 3 × due_amount` (при `due_amount > 0`)
  в `phase_migrate` поднимает уход (`engine/tick.py`, см. `docs/04`); дальше решают
  `can_leave` и кап соседа. Барщина (`basis=duty` / `kind=labor_duty`) недоимки не даёт:
  там только запись `duty_days` по календарю, а руки с надела снимает фаза манора.
- **Наём — безземельные и половина коттера.** Домен, не закрытый барщиной, при зерне/серебре
  в амбаре своей книги нанимает: `landless` — все руки, `cotter_plot` — половину
  (`labor_days × 0.5`, лишние руки); остальных не нанимает (`economy/manor.py::_hire`).
- **Призрак в книге (по эффекту).** Ушедший, но ещё не дошедший двор числится в
  `household_ids` книги, но ни в одном эффекте не участвует: труд, паёк, наём, повинности
  и `mustered` его пропускают. По прибытии снимается со всех книг
  (`hazards/travel.py::_detach_from_manor_books`).
  Проверка связки: `test_departure_route.py` (уход при недоимке, `tied` не уходит).

### Должности (`design/catalogs/offices.yml`)
Должность — дорогой рот: не пашет, ездит (`sheriff`, `huntsman` с `travel`); вести
рождает не должность, а обоз, молчание и события (поле `produces_report` упразднено,
ADR 0034); может погибнуть в пути (`hazards/travel.py`).

### Действия и карта вызовов
`legal.actions.send_party` посылает людей на соседнюю клетку (без pathfinding) только двору
с осью `can_be_taken_on_expedition`. `legal.manor.render_labor` кладёт трудодни двора в пул
домена (`world.stats["demesne_labor_days"]`), не из воздуха. `scenario.load_scenario` сводит
бандлы в `Obligation` (`legal.bundles.materialize_obligations`). `phase_migrate` читает
`can_leave`.

### Отложено в v0
- **Применение режима к производству:** `economy/labor.py` выбирает рецепты без вызова
  `allowed_actions` (экономическая зона).
- **Домен как контур:** `render_labor` есть и проверен, но в тик не подключён.
- **Бегство в тике:** `attempt_flight` есть и проверен, в `phase_migrate` не вызывается.
- **Несведённые условия бандлов:** пенсы/натура на праздники/подвода — данные с `note`.
- **Сезонность в тике:** `phase_obligations` берёт `duty_days` из `legal.calendar`; руки с
  надела снимает фаза манора (`economy/manor.py`), чтобы не было двойного счёта.
- **Держание коня:** `can_hold_horse` (только `free`) подключён к тяглу и решениям с
  ADR 0030 — запрет «конь не у `tied`» держится механизмом, а не только стартовыми стоками.
- **Задержка приказа:** `order_delay_months` (0/1) подключена только к `send_sally`;
  `send_march`/`send_river` идут со своим сроком из `find_path`, `send_party` — шагом без
  pathfinding (`legal/actions.py`).

## Проверка

```bash
bash sim/run_tests.sh
PYTHONPATH=sim/src python3 -m unittest discover -s sim/tests -p 'test_manor_axes.py' -v
PYTHONPATH=sim/src python3 -m unittest discover -s sim/tests -p 'test_legal_regimes.py' -v
PYTHONPATH=sim/src python3 -m unittest sim.tests.test_common_right_access sim.tests.test_communal_access -v
```

Критерий: `test_manor_axes.py` зелёный — виллан не уходит легально, сокмен уходит, трудодни
идут в пул домена и не берутся из воздуха, раб ест из стока лорда, пресеты читаются из YAML,
в коде нет классов под имена пресетов. `test_legal_regimes.py` зелёный — наделы/режимы,
уход по пресету, сосед-держатель без ренты, должности грузятся с `travel` (депеш не рождают).
`test_common_right_access.py` и `test_communal_access.py` зелёные — доступ дают только
`works_tiles`/`common`, а не соседство; `tenure`/`grazing` не меняют communal-доступ.
