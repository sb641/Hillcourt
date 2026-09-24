# 03. Онтология

## Назначение

Единственный источник сущностей и полей. Всё, что упомянуто в документах или коде, обязано
трассироваться сюда. Новая сущность или поле — только через ADR.

## Правила

Идентификаторы — english `snake_case`. Числовые поля носят единицу в имени (`grain_kg`, `labor_days`).
Владелец — роль, которая пишет модуль/каталог этой сущности.

### Ядро (8 + 3)

| Сущность | Поля (ключевые) | Владелец |
|---|---|---|
| `Person` | `id`, `name`, `household_id`, `age_class ∈ {child,adult,elder}`, `curiosity 0..1`, `fear 0..1`, `health 0..1`, `location_tile_id`, `personal_status ∈ {free,tied,slave}`, `land_relation ∈ {secure_holding,tenement,landless}`, `obligation_bundle?`, `age_months` | Implementer |
| `Household` | `id`, `name`, `settlement_id`, `member_ids`, `stores: Stock`, `labor_days`, `obligation_ids`, `hunger_days`, `arrears_days`, `mood 0..1`, `intent ∈ {stay,work,leave}`, `current_tile_id`, `left_at: SimDate?`, `legal_status_id` (пресет), `personal_status ∈ {free,tied,slave}`, `land_relation ∈ {secure_holding,tenement,landless}`, `obligation_bundle?`, `manor_id?`, `holding_scale`, `main_action`, `minor_action`, `adventurism 0..1`, `rumor_fear 0..1`, `tool_wear`, `traveling`, `food_streak`, `birth_count` | Economist |
| `Tile` | `id`, `coord (x,y)`, `terrain ∈ {hill,field,pasture,forest,marsh,heath,salt_flat,ruin,water}`, `standing_stock_id` (сток стоячей материи), `hazard_ids`, `settlement_id?`, `ruin_id?`, `regime_id`, `trail_wear`, `road`, `ford`, `bridge` (теги пути: в игре ставятся только стройкой `start_work`; начальная карта сценария задаёт их секциями `fords`/`bridges`/`roads` — стартовое условие, не игровая краска) | Implementer |
| `Stock` | `id`, `owner_kind ∈ {household,settlement,tile,pack,sink}`, `owner_id`, `amounts: dict[good_id,float]` | Economist |
| `Obligation` | `id`, `household_id`, `kind ∈ {rent,labor_duty,levy,muster}`, `basis ∈ {share,fixed,duty}`, `due_amount`, `due_good`, `period_months`, `paid_total`, `arrears`, `corvee_days`, `duty_days`, `right_id`, `call_status` (muster: `in_service|returned|overdue|unable|refused`) | Legal |
| `Report` | `id`, `source ∈ {eye_from_hill,adjacent_daily,messenger,caravan,silence}`, `subject_kind`, `subject_id`, `content`, `facts: dict`, `event_date: SimDate`, `delivery_date: SimDate`, `confidence 0..1`, `noise 0..1`, `distorted: bool`, `observer_id` | Info |
| `Pack` | `id`, `kind ∈ {pack,party,caravan,household_move}` (`party` — отряд/вылазка (`send_party`, default), `caravan` — обоз, в т.ч. речной, `household_move` — переселение двора, `pack` — заявленная ручная посылка, код-продюсер пока — будущий `send_pack`), `origin_tile_id`, `destination_tile_id`, `route: list[tile_id]` (маршрут `find_path`; сухопутный воз и приказы хранят с origin, речной воз `send_river` — без origin), `member_ids`, `cargo: Stock`, `departed_date`, `eta_date`, `status ∈ {in_transit,arrived,lost}`, `owner_household_id?`, `obligation_id?`, `lost_date?` | Implementer |
| `Hazard` | `id`, `kind ∈ {wolves,bog,band}`, `tile_id`, `intensity`, `population`, `satiety 0..1`, `active`, `spawn_rule_id` | Implementer |
| `Settlement` | `id`, `name`, `kind ∈ {hill_court,farmstead,salt_village,village,native_village}`, `coord`, `household_ids`, `stores: Stock`, `works_tiles: list[tile_id]` | Implementer |
| `Right` (`Tenure`) | `id`, `holder_household_id`, `tile_id`, `kind ∈ {tenure,common,grazing}`, `granted_date`, `rent_share 0..1` | Legal |
| `Manor` | `id`, `holder_person_id`, `stock_id`, `tile_ids`, `tile_regimes`, `household_ids`, `parent_manor_id?`, `upward_bundle`, `demesne_labor_demand_this_month`, `demesne_labor_filled`, `grant_ids`, `mustered`, `prior_preset`, `service_kits_required`, `service_men_required`, `service_kits_held`, `service_men_held`, `service_met`, `service_gap`, `bad_service_months`, `iron_requested`, `seat_tile_id` (клетка зала, только корень), `seat_tiles`, `eye_range_tiles`, `peace_range_tiles` | Implementer |
| `Tribe` | ровно 6 полей: `id`, `name`, `stance ∈ {independent,allied,vassal}`, `settlement_id`, `tribute_grain`, `muster_kits` | Implementer |

Корень — `settlement:hill_court`, тэн — `manor:<id>`. `guard_ring` (кольцо тэна:
клетки книги + непосредственные соседи) — **вычисляется**, в сущности не хранится.

`Tribe ↔ Settlement.kind = native_village` — биекция: каждая нативная деревня связана
ровно с одним `Tribe.settlement_id`, а каждое `Tribe` — с одной такой деревней. В коде у
`Tribe` ровно 6 полей; ADR 0064 перечисляет тот же набор, хотя в его тексте встречается
слово «7 полей». Состав племени не хранится в `Tribe`: он вычисляется из
`Settlement.household_ids`; общая земля — клетки `Right.kind = common` и
`Settlement.works_tiles`. `Right.kind = common` даёт доступ, но не индивидуальный надел;
`grazing` остаётся личным держанием. `tribute_grain` — поле данных, оброк создаётся при
`allied|vassal` и заданном значении 0.6; `muster_kits` — только данные потенциала вызова
(ADR 0060, 0062, 0064, 0068).

### Каталожные сущности (данные, не код)

| Сущность | Поля | Владелец |
|---|---|---|
| `Good` | `id`, `name`, `category ∈ {food,fuel,material,lux,livestock}`, `storage ∈ {granary,cellar,barn,pack,open}`, `spoil_per_month`, `edible`, `nutrition` | Economist |
| `Recipe` | `id`, `name`, `place ∈ {tile,settlement}`, `requires_terrain: list[terrain]`, `inputs: dict[good_id,float]`, `draws_standing: dict[good_id,float]`, `outputs: dict[good_id,float]`, `loss: dict[good_id,float]`, `labor_days`, `transform: bool` | Economist |
| `HouseholdAction` | `id`, `name`, `kind ∈ {main,minor,both}`, `labor_share 0..1`, `purpose`, `recipes: list[recipe_id]`, `requires_terrain`, `requires_tool`, `risk 0..1` | Economist |
| `NeedConfig` | `adult_food_per_month`, `child_food_per_month`, `elder_food_per_month`, `edible_order`, `winter_months`, `firewood_per_adult_winter_month`, `feed_good`, `feed_per_month`, `axe_wear_per_batch`, `axe_break_below`, `wear_recipes`, `relief_min_court_grain`, `relief_amount`, `birth_food_months`, `birth_streak_months`, `birth_max_household`, `birth_adults_required`, `birth_maturity_months` | Economist |
| `SpawnRule` | `id`, `name`, `target ∈ {hazard,pack,good,ruin}`, `kind ∈ {probabilistic,calendric,conditional}`, `params: dict` (в т.ч. `cap_per_tile`) | Economist |
| `HazardRule` | `id`, `name`, `kind`, `params: dict` | Economist |
| `LandRegime` | `id`, `name`, `allowed_actions ⊆ {plough,gather_brushwood,take_game,build_hut,leave}`, `requires_labor_days`, `feeds_household` | Legal |
| `LegalStatus` (пресет) | `id`, `name`, `personal_status`, `land_relation`, `obligation_bundle`, `can_leave`, `marriage_needs_permission`, `court ∈ {manor,public}`, `wergeld`, `inheritance`, `can_sell_land`, `can_be_taken_on_expedition`, `ploughs`, `land_kind` | Legal |
| `ObligationBundle` | `id`, `name`, `currency_mix ⊆ {labor-day,in-kind,penny,acre}`, `terms: dict[term,{value,unit,note}]`, `calendar_id?` | Legal |
| `CalendarMonth` | `month 1..12`, `season ∈ {winter,plough,hay,harvest,sow_winter}`, `demesne_work`, `labor_mod: dict[preset,dict]`, `boon_allowed`, `sow_demesne_acres` | Legal |
| `ManorConfig` | `holding_tiles_by_land_kind`, `plot_batch_cap_per_tile`, `demand_days_per_tile: dict[month,float]`, `harvest_recipe`, `board_grain_per_adult`, `hire_wage_grain_per_day`, `hire_wage_silver_per_day`, `hire_min_castle_grain`, `sow_grain_per_acre` | Economist |
| `SeasonYield` | `month 1..12`, `season`, `plot_yield`, `demesne_yield` | Economist |
| `Office` | `id`, `name`, `action`, `travel` | Legal |

### Служебные типы

- `SimDate = {year:int, month:int, day:int}`; `months_per_year=12`.
- `World.roadworks` — реестр строек пути, ключ `tile_id`:
  `{kind ∈ {road,ford,bridge}, required_days, done_days, material_good, material_amount,
  status ∈ {active,done}, started, manor_id}`. Не `dataclass`, а словарь мира; материал —
  только перевод `log` → `sink:waste` (`engine/roadworks.py`).
- `TILE_MAX_HOUSEHOLDS = 5` — кап жилых дворов на клетке (`engine/tile_view.py`), эквивалент
  будущего `Tile.max_households`. Не путать с `plot_batch_cap_per_tile` (партии жатвы, G3).
  Вид клетки (`engine/tile_view.py:FORMS`) — чистый рендер, не сущность мира (И-5).
- `Tile.trail_wear` — износ сухопутного пути. `trail_level_for()` возвращает уровень 0/1/2
  по порогам `TRAIL_WEAR_TRAIL = 3.0` и `TRAIL_WEAR_DIRT = 12.0`; входные множители для
  целины, тропы и грунтовки — 1.0/0.9/0.8. Затухание `TRAIL_DECAY_PER_MONTH = 0.5`;
  вода и `Tile.road` не принимают тропообразование. Ходьба никогда не ставит `Tile.road`
  (ADR 0061).
- Веса топтания задаёт вид движения: `PACK_TREAD_WEIGHTS` — `caravan` 4.0, `party` 2.0,
  `household_move` 2.0; ходок `Household.traveling` весит 1.0; речной маршрут и неизвестный
  вид движения — 0.
- Профили движения — строки `MOVEMENT_PROFILES`, не классы. Среди восьми профилей:
  `arms` умножает все сухопутные цены на 1.25; `rider` задаёт для `field` 0.6,
  `forest` 4.0, `marsh` 6.0, `ford` 4.0, `hill` 2.0, `heath` 1.0, `salt_flat` 1.2,
  `ruin` 1.5, `road` 0.5 (ADR 0061).
- Сценарные маркеры `marks` хранятся как динамические атрибуты `Tile`, а не его поля.
  Для одного живого двора `dwelling ∈ {tent_earth,house,multi_storey}` задаёт только форму
  `tent_earth_homestead`/`timber_house`/`multi_storey_house`; `tavern` задаёт форму
  `tavern_site` (участок). Тик эти маркеры не читает, бонусов и переходов жилья нет;
  `housing_level` в v0 не существует (ADR 0065). Клетка без живых дворов получает вид
  пустой местности: для `hill`/`field`/`pasture` это `open_field`, не `tribal_village`;
  такая пустота не создаёт знание (ADR 0068).
- Сценарий большой карты хранит 100×100 `Tile` как данные, 6-связную реку, поселения,
  дворы, `works_tiles`, леса/луга/wilds и редкие маркеры; новые мировые сущности для карты не
  введены. Подробный контур ограничен поселениями и `works_tiles`; подключение карты к тику —
  «в работе» (ADR 0073/0080/0083).
- `Obligation(kind="muster")` хранит срок 2/3 месяца и `call_status`; связанные outbound/return
  `Pack` используют `Pack.obligation_id`. Потерянный Pack или тишина не означают `refused`;
  `unable` означает невозможность, а подавление тэна — существующий `revoke_thegn`. Регент,
  `annexed` и `chief_person_id` в v1 отсутствуют (ADR 0082).
- `state_hash` пока **не покрывает** `Obligation.call_status` и `Pack.obligation_id`; это
  обязательный долг, а не заявленная синхронизация.

  `transfer` (сток → сток), `process` (выдача из пула обработки рецепта), `external_in`
  (материя извне по `rule_id`), `external_out` (наружу; в v0 не используется). `process`
   разрешён только для товаров, объявленных на выходе рецепта (`allowed_goods`), иначе
   `Ledger.emit` падает. Счёт `sink:waste` и `sink:eaten` — обычные `Stock` с `owner_kind=sink`.
- Служебная память рядом с `Ledger` (не сущности мира, ADR 0058):
  `World.barter_memory: dict[str, dict]` — память сделок вместо биржи (ключ
  `origin->destination:good`, поля `carried`/`delivered`/`ratio`/`lost`; цен нет);
  `remembered_road_tiles(world)` — вычисляемые клетки дорог из маршрутов дошедших
  `Pack` (не хранятся). Игрок их напрямую не читает (И-3).

### Знание игрока (`news/`)

Игрок не читает мир напрямую. `build_player_view(world, as_of)` собирает `PlayerView` только из
доставленных `Report` (`delivery_date ≤ as_of`); другого доступа к истине у игрока нет.

| Сущность | Поля | Владелец |
|---|---|---|
| `ReportView` | `id`, `source`, `subject_kind`, `subject_id`, `content`, `facts: dict`, `event_date`, `delivery_date`, `confidence`, `distorted`, `stale` | Info |
| `PlayerView` | `as_of: SimDate`, `entries: list[ReportView]`, `sources()` | Info |
| `KnowledgeEntry` | `about`, `source`, `observed_month`, `arrived_month`, `noise`, `confidence`, `distorted`, `content`, `facts: dict`, `stale` | Info |
| `PlayerKnowledge` | `as_of: SimDate`, `entries: list[KnowledgeEntry]`, `by_about: dict`, `latest(about)`, `silences()` | Info |

`stale = (age > STALE_AFTER_MONTHS)`, где `STALE_AFTER_MONTHS = 2` (месяца от `event_date`).
У `ReportView` и `KnowledgeEntry` нет полей `Tile`/`Household`/`Stock`: только рассказ о них.
Знание игрока по месту — это `PlayerKnowledge.latest(about)` (последний доехавший `Report` о
месте, а не `Tile.state`); собирается `build_player_knowledge(world, as_of)` только из
доставленных `Report` (`info/knowledge.py`). Не-клеточные сводки адресуются ключами мест:
`neighbors` (молчание о соседях), `barony` (гонец/шериф).

### Действия игрока (`PlayerAction`)

В v0 реализованы, по домам:

- **Право/посылка** (`legal/actions.py`): `grant_tenure`, `add_obligation`, `send_party`.
- **Книга манора** (`engine/manor.py`): `set_tile_regime`, `grant_tenement`, `grant_tool`,
  `call_boon`, `ease_week_work`, `grant_thegn`, `revoke_thegn`.
- **Путь** (`engine/march.py`, `engine/seat.py`, `engine/river.py`): `send_march` (отряд по
  `find_path`, в логе `route`/`months`/`profile`), `send_sally` (высылка на соседнюю клетку,
  `order_delay_months` = 0 на клетке глаза/мира корня, иначе 1), `send_river` (речной воз:
  нужен `raft`/`boat` ≥ 1.0 для груза > 1.0, в маршруте обязана быть вода).
- **Стройка пути** (`engine/roadworks.py`): `work_road`, `work_ford`, `work_bridge`.
- **Место** — не приказ, а правило места (`engine/tile_view.py:settle_household`): посадка
  двора сверх капа 5 отклоняется; вызывается миграцией и тестами, в `ACTION_HANDLERS`
  runner не заведена (отдельного приказа `settle_household` нет).

Каждое действие меняет `Right`/`Obligation`/`Pack`/`Manor`/`Tile` и пишется в
`World.player_actions`; `Report` порождают не все (например `work_*`, `grant_tool`,
`call_boon`, `ease_week_work` — только лог). Прямого приказа `Person` нет (И-2).
`revoke_tenure`, `set_rent_share`, `send_pack`, `send_messenger`, `patrol` заявлены, но ещё
не реализованы (отложено). `World.rights` хранит выданные `Right`,
`World.manors` + `World.player_manor_id` — книгу земли.

## Проверка

Критерий: каждая сущность ядра существует как `dataclass` в `sim/src/hillcourt/`; любое имя
собственное в `docs/` и `design/` трассируется в эту таблицу или в каталог; поле с числом имеет
единицу в имени; `ReportView` не содержит полей истины мира.
Проверка: ревизия Critic, `sim/tests/test_catalog_rules.py` (каталог),
`sim/tests/test_news_no_omniscience.py` и `sim/tests/test_info_knowledge.py` (знание игрока),
`sim/tests/test_legal_regimes.py` (режимы/статусы), `sim/tests/test_hazards_encounter.py`
(опасность как популяция), `sim/tests/test_matter_conservation.py` (виды проводок `Ledger`).
