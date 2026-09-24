# 09. Право и земля: сводная модель v0

## Назначение

Единый описательный вход в юридическую и земельную сторону игры: все **внедрённые** принципы и
правила в одном месте, с указателями на детали. Если этот документ расходится с кодом — прав
код и тесты, документ правится (И-9).

Начни здесь, дальше — срезы по надобности.

Уровни (кто за что отвечает):
| уровень | документ | что там |
|---|---|---|
| сводка (вводная) | **09 (этот)** | принципы, таблицы, карта правил и вызовов |
| право | `docs/07_legal.md` | оси, пресеты, бандлы, календарь, действия |
| манор | `docs/08_manor.md` | книга земли, вложенный тэн, контракт амбара |
| онтология | `docs/03_ontology.md` | поля сущностей (единственный источник) |
| данные | `design/catalogs/{legal_statuses,obligations,land_regimes,calendar_v0,offices,rights}.yml` | пресеты, бандлы, наделы, календарь |
| решения | `docs/decisions/0003,0006,0007,0008,0010` | почему так, и что запрещено |
| смежное | `docs/04_tick.md` (фазы), `docs/05_information.md` (известия) | куда право встроено |
| код | `sim/src/hillcourt/legal/`, `engine/manor.py`, `economy/manor.py` | реализация |

## Принципы

1. **Право, а не микроменеджмент (И-2).** Игрок меняет `Right`/`Obligation`/`Pack`/`Manor`,
   а не приказывает человеку рубить дерево. Все действия — `PlayerAction` (см. карту ниже).
2. **Свободность юридическая, не «никому не должен».** Свободный держит землю за службу/ренту.
3. **Раб — не виллан; виллан — не раб.** Раб без вергельда и без своего двора-агента; виллан
   прикреплён к наделу, но с вергельдом и наследством надела.
4. **Зарплаты крестьянам нет.** Три валюты повинности: трудодни, гэфоль (натура/пенс), паёк.
5. **Домен и надел не смешивают стоки.** Домен требует трудодней, надел кормит двор.
6. **Материя не берётся из ничего (И-1).** Пожалование двигает право, не стоки; гэфоль/посев —
   только `Ledger.transfer`.
7. **Нет лестницы вассалов, эрлов, титулов, войны за фьеф.** Сосед-держатель — равный;
   вложенность маноров depth ≤ 1.

## Три оси и 8 пресетов (`legal_statuses.yml`)

`Household`/`Person`: `personal_status ∈ {free,tied,slave}`, `land_relation ∈
{secure_holding,tenement,landless}`, `obligation_bundle`. Пресет — ярлык данных, не класс.

| preset | status | land | bundle | уйти | рента | в поход | пашет |
|---|---|---|---|---|---|---|---|
| `holder` | free | secure_holding | holder_none | да | нет | нет | нет |
| `thegn` | free | secure_holding | thegn_service | да | нет | да | нет |
| `sokeman` | free | secure_holding | sokeman_rent | да | да | нет | да |
| `geneat` | free | tenement | geneat_service | да | служба | да | да |
| `villein` | tied | tenement | villein_full | нет | да | нет | да |
| `cotter` | tied | tenement | cotter_monday | нет | да | нет | да |
| `free_landless` | free | landless | free_landless_none | да | нет | нет | нет |
| `slave` | slave | landless | slave_ration | нет | паёк | нет | нет |

Права: `marriage_needs_permission`, `court ∈ {manor,public}`, `wergeld`, `inheritance`,
`can_sell_land`, `land_kind`. `slave`: `wergeld: false`; `thegn`/`holder`: `ploughs: false`.
Колонка «пашет» = может держать и пахать **свой** надел: раб и безземельный работают руками
на домене и наймом, своего надела у них нет.

## Земля (`land_regimes.yml`)

Режим клетки (`Tile.regime_id`) = что МОЖЕТ (`plough`, `gather_brushwood`, `take_game`,
`build_hut`, `leave`) + как участвует в хозяйстве.

| `id` | домен? | кормит? | где |
|---|---|---|---|
| `demesne` | да | нет | своя клетка лорда |
| `villein_tenement` / `cotter_plot` / `free_holding` / `tenement` | нет | да | наделы дворов |
| `waste` | нет | нет | пустошь, топь |
| `reserved_wood` | нет | нет | заповедный лес |
| `foreign` | — | — | выводится для чужого двора, не хранится |

`allowed_actions` урезает набор по пресету (уход, пахота). Соляная деревня — клетки того же
манора, не второй фьеф.

## Повинности (`obligations.yml` + `calendar_v0.yml`)

Бандл ссылается на календарь, а не дублирует 12 чисел. У числовых условий есть `unit`
(`labor-day`/`in-kind`/`penny`/`acre`) и `note`, если к тику не сведено.

| bundle | несёт |
|---|---|
| `villein_full` | барщина по календарю, harvest_extra, boon_days_cap, geld_michaelmas, in_kind_martinmas, sow_demesne_acres |
| `cotter_monday` | барщина (понедельник), harvest_extra, almost_no_cash |
| `geneat_service` | callout, cartage, messenger, harvest_help, food_for_lord |
| `sokeman_rent` | fixed_rent_grain_or_pence, rare_labor, may_leave |
| `slave_ration` | no_holding, eats_lord_board, full_control |
| `thegn_service` | no_farm_labor, musters_men, self_armed, bridge_burh_fyrd |
| `holder_none` / `free_landless_none` | без повинности |

**Календарь:** год = 12 месячных тиков, сезоны `winter/plough/hay/harvest/sow_winter`.
Перевод один: `month_days = week_days × 4` (`WEEKS_PER_MONTH`). `geneat`/`sokeman` — только
`callout`; `slave` — `always` (сезонного нуля нет); `free_landless` — `hire_demand low|high`.
Помочи (bene) — только в месяц `boon_allowed`, до `boon_days_cap`; счётчик
`boon_used_this_year` (годовой, сбрасывается). Гэфоль-пахота (`sow_demesne_acres`) — зерно
двора в домен, только `Ledger.transfer`.

Сведено к тику (`legal/bundles.py`): сезонная барщина → `Obligation.duty_days`;
`fixed_rent_grain_or_pence` → натуральная рента. Остальное — данные.

## Манор — книга земли (`engine/manor.py`, `ontology.Manor`)

Поля: `id`, `holder_person_id`, `stock_id` (корень `settlement:hill_court`, тэн `manor:<id>`),
`tile_ids` (+`tile_regimes`), `household_ids`, `parent_manor_id`, `upward_bundle`,
`demesne_labor_demand_this_month`, `demesne_labor_filled`, `grant_ids`, `mustered`,
`prior_preset`, служба — `service_kits_required`/`service_men_required`/`service_kits_held`/
`service_men_held`/`service_met`/`service_gap`/`bad_service_months`/`iron_requested`, место —
`seat_tile_id`/`seat_tiles`/`eye_range_tiles`/`peace_range_tiles`. Двор кормится из одной
книги — `Household.manor_id`. Реестр — `World.manors`/`player_manor_id`, лог —
`player_actions`. `guard_ring` тэна (книга + соседи) вычисляется, не хранится.

- **Корень** `manor_hill` — один у игрока; клетки — вся земля; тяглые дворы без соляных
  держателей. **Тэн** — вложенный манор (`thegn_service`), depth ≤ 1, не жалует дальше.
- **Пожалование** `grant_thegn`: не больше `player.thegn_grant_limit.grants` раз (по умолчанию
  1; в `v0_shire` — 2), ≤3 клетки/≤3 двора (жёсткий потолок `THEGN_MAX_*`), depth ≤ 1,
  атомарно, создаёт амбар `manor:<id>` и требует ≥1 доменной клетки; соляная
  клетка/двор/держатель отвергаются; при исчерпанном лимите — `grant_thegn_rejected`
  `reason=grants_limit`; `revoke_thegn` возвращает землю, дворы и амбар один раз и откатывает
  пресет. Двор держателя входит в книгу тэна всегда.
- Трудодни двора идут в пул **своего** манора; после пожалования исчезают из корня.
- Домен тэна: контракт амбара (ADR 0010) — `Manor.stock_id`/`Household.manor_id`, обязательный
  домен, слияние амбара на revoke. Паёк уже по амбару манора; сбор/урожай тэна Economist
  переводит (в работе). Помочи и найм в v0 — только корень.
- `mustered` = держатель жив, не голоден **по своему стоку** и имеет `war_kit ≥ 1` на своём
  дворе. `service_met` дополнительно сверяет норму (`service_kits_required = 1 + тяглые
  дворы`) с `fief_kits` (**амбар + двор** держателя) и явку; разрыв — `service_gap`
  (ADR 0021). Боевого движка нет: комплект — объекты стока, не `+ATK`.
- **Стыки (одно правило, один победитель).** (1) Конь — тягло/езда у `free`, норму службы
  закрывает только `war_kit`; боевого коня в v0 нет. (2) Глаз холма (`eye_range_tiles`=1,
  `Report`) и кольцо тэна (`guard_ring`, вылазка) — разные вещи, не один «радиус манора».
  (3) Три потолка не сводятся: кап жилья `TILE_MAX_HOUSEHOLDS`=5, кап пашни
  `plot_batch_cap_per_tile`=4 (партии), лимит книги `grant_thegn` (≤3 клетки/≤3 двора).
  (4) Вода без `ford`/`bridge` суше непроходима; переправа — проект `work_ford`/`work_bridge`,
  речной профиль `water_*` идёт водой без брода.   (5) Дорога/брод/мост дешевят **любой** ход `Pack`: приказы (`send_march`/`send_river`/
  `send_sally`) и живой обоз (маршрут `find_path` профилем `caravan`, ADR 0029) — но не
  телепортируют соль: груз едет физически, дни/фураж/риск считаются, `eta_months =
  max(1, ceil(days/30))`, поэтому экономия дней в v0 может округлиться в тот же месяц
  (квантование — честно, см. `docs/04`).

## Действия игрока и карта вызовов

| действие | где | эффект |
|---|---|---|
| `grant_tenure` / `add_obligation` | `legal/actions.py` | право/повинность |
| `send_party` | `legal/actions.py` | `Pack` на соседнюю клетку (без pathfinding) |
| `send_march` / `send_sally` / `send_river` | `engine/march.py`, `engine/seat.py`, `engine/river.py` | `Pack` по `find_path` / на соседнюю с задержкой 0–1 мес / речной воз (нужно судно ≥1.0) |
| `work_road` / `work_ford` / `work_bridge` | `engine/roadworks.py` | проект пути: `log` + руки → tag клетки |
| — (не приказ runner) | `engine/tile_view.py:settle_household` | правило посадки двора (кап 5); вызывается миграцией/тестами, в `ACTION_HANDLERS` не заведено |
| `set_tile_regime` | `engine/manor.py` | режим/надел клетки |
| `grant_tenement` | `engine/manor.py` | надел + `Right` + книга |
| `call_boon` / `ease_week_work` | `engine/manor.py` | помога / урезать барщину пика |
| `grant_tool` | `engine/manor.py` | перевод железа (`iron_share`/`iron`/`iron_bloom`, ADR 0021) от корня двору |
| `grant_thegn` / `revoke_thegn` | `engine/manor.py` | вложенный манор (лимит `grants` из сценария) / возврат земли |

Вызовы из тика: `scenario → materialize_obligations`; `phase_migrate → can_leave`
(держатель ЛЮБОГО манора не уходит, пока манор жив: `legal/regimes.py:can_leave`);
`phase_obligations → seasonal_labor_days`; `phase_manor → economy/manor_month`, у тэна —
`thegn_month`; `phase_roadworks → advance_roadworks`; `phase_inform →
make_seat_eye_reports`; `phase_record → update_musters`. Прямого приказа `Person` нет (И-2).

## Должности и известия

`offices.yml`: `sheriff`, `huntsman` — не пашут, ездят, рождают `Report`, могут погибнуть в
пути (`hazards/travel.py`). Право не читает истину мира: игрок знает `Report` с датой
(`docs/05_information.md`).

## Отложено в v0 (честно)

- Применение режима к производству (`economy/labor.py` не зовёт `allowed_actions`).
- `render_labor` и `attempt_flight` есть и проверены, в тик не подключены.
- Несведённые условия бандлов (пенсы/натура/подвода) — данные с `note`.
- Пожалований не больше `grants` из сценария (по умолчанию 1; `v0_shire` — 2), отзыв слот не
  освобождает; вложенность depth ≤ 1.
- Хозяйство тэна: контракт амбара зафиксирован (ADR 0010) и **закрыт** (ADR 0011) —
  `Manor.stock_id`, `Household.manor_id`, обязательный домен среди пожалованных клеток,
  слияние амбара на `revoke`; сбор/урожай/гэфоль/паёк/посев тэна идут через амбар своего
  манора (`_gafol`/`_sow_demesne`/`_work_demesne`/`_board`). Трудодни не пересчитываются —
  только принадлежность амбара. Добавлена **служба** (ADR 0021): норма комплектов, `guard_ring`,
  отзыв → `free_landless` (см. `docs/08`).
- `geld_michaelmas` не собирается: у вилланов нет серебра (ждёт денежной экономики).

## Проверка

```bash
bash sim/run_tests.sh
PYTHONPATH=sim/src python3 -m unittest discover -s sim/tests -p 'test_manor_axes.py' -v
PYTHONPATH=sim/src python3 -m unittest discover -s sim/tests -p 'test_legal_regimes.py' -v
PYTHONPATH=sim/src python3 -m unittest discover -s sim/tests -p 'test_manor_entity.py' -v
PYTHONPATH=sim/src python3 -m unittest discover -s sim/tests -p 'test_manor_economy.py' -v
```

Критерий: тесты зелёные (на ревизии приёмки — 416); сводка верна, если:
- `test_manor_axes.py` — пресеты из YAML (не классы), виллан не уходит, сокмен уходит, трудодни
  в пул домена, раб ест из стока лорда, сезонные зубцы;
- `test_legal_regimes.py` — наделы/режимы, уход по пресету, сосед без ренты, должности;
- `test_manor_entity.py` — книга земли, тэн, соль не фьеф, revoke, `mustered`, приёмка;
- `test_manor_economy.py` — домен/барщина/гэфоль/паёк/найм, дельта материи 0.
Прогон 36 месяцев печатает датированные `Report`, дельта материи `0.000000`, хеш стабилен
между процессами.
